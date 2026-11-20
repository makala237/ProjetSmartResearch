import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ClipboardCheck,
  Database,
  Download,
  FileSpreadsheet,
  FileText,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  Settings2,
  Trash2,
  UserPlus,
} from "lucide-react";
import { api } from "../api/client";
import { useLanguage } from "../i18n/LanguageContext";
import type {
  CRFFieldSpec,
  CRFFieldType,
  PatientRecord,
  ProtocolProject,
} from "../types/protocol";

interface Module3Props {
  project: ProtocolProject;
  onProjectUpdated: (p: ProtocolProject) => void;
  onOpenModule2: () => void;
  onError: (msg: string) => void;
}

type SubTab = "entry" | "designer" | "paper_preview";

const FIELD_TYPE_LABELS: Record<CRFFieldType, string> = {
  dichotomous: "Cases à cocher Dichotomique (Oui / Non)",
  single_choice: "Cases à cocher — Choix unique",
  multi_choice: "Cases à cocher — Choix multiple",
  number: "Cadre numérique à remplir (+ unité)",
  date: "Cadre Date segmenté (JJ / MM / AAAA)",
  text: "Cadre texte libre à remplir",
};

function computeNextPatientCode(records: PatientRecord[]): string {
  const existing = new Set(records.map((r) => r.patient_code));
  let idx = records.length + 1;
  while (existing.has(`PAT-${String(idx).padStart(3, "0")}`)) {
    idx += 1;
  }
  return `PAT-${String(idx).padStart(3, "0")}`;
}

function computeCompletionPct(
  rec: PatientRecord,
  fields: CRFFieldSpec[]
): number {
  if (!fields.length) return 0;
  let filled = 0;
  for (const f of fields) {
    const val = rec.values?.[f.field_id];
    if (Array.isArray(val) ? val.length > 0 : val !== undefined && val !== null && String(val).trim() !== "") {
      filled += 1;
    }
  }
  return Math.round((filled / fields.length) * 100);
}

export function Module3DataEntry({
  project,
  onProjectUpdated,
  onOpenModule2,
  onError,
}: Module3Props) {
  const { lang, t } = useLanguage();
  const [subTab, setSubTab] = useState<SubTab>("entry");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Paper preview HTML from backend crf_builder
  const [annexe1Html, setAnnexe1Html] = useState<string>("");

  // Designer editable copy of CRF fields
  const [draftFields, setDraftFields] = useState<CRFFieldSpec[]>(
    project.crf_fields ?? []
  );
  const [syncAnnexe1OnSave, setSyncAnnexe1OnSave] = useState(true);

  // Patient Data Entry form state
  const records = project.patient_records ?? [];
  const fields = project.crf_fields ?? [];
  const [searchQuery, setSearchQuery] = useState("");
  const [editingRecordId, setEditingRecordId] = useState<string | null>(null);
  const [patientCode, setPatientCode] = useState<string>(() =>
    computeNextPatientCode(records)
  );
  const [inclusionDate, setInclusionDate] = useState<string>(() =>
    new Date().toISOString().slice(0, 10)
  );
  const [hospitalSite, setHospitalSite] = useState<string>(
    project.cover?.host_institutions?.[0] || "HGOPY"
  );
  const [investigatorName, setInvestigatorName] = useState<string>(
    project.cover?.author_name || ""
  );
  const [values, setValues] = useState<
    Record<string, string | string[] | number | boolean | null>
  >({});
  const [notes, setNotes] = useState<string>("");

  const hospitalChoices = useMemo(() => {
    const base =
      project.cover?.host_institutions?.filter((s) => s.trim()) ?? [];
    const merged = Array.from(new Set([...base, "HGOPY", "HCBY", "HGY"]));
    return merged.slice(0, 6);
  }, [project.cover?.host_institutions]);

  // Synchronize draft schema & load Annexe 1 HTML when project changes
  useEffect(() => {
    setDraftFields(project.crf_fields ?? []);
    api
      .getCrfPreviewHtml(project.project_id)
      .then((res) => {
        setAnnexe1Html(res.annexe1_html);
        if (
          (!project.crf_fields || project.crf_fields.length === 0) &&
          res.crf_fields?.length > 0
        ) {
          onProjectUpdated({ ...project, crf_fields: res.crf_fields });
          setDraftFields(res.crf_fields);
        }
      })
      .catch(() => {
        /* fallback silently if offline */
      });
    // Reset new patient default investigator when switching projects
    if (!editingRecordId) {
      setPatientCode(computeNextPatientCode(project.patient_records ?? []));
      setInvestigatorName(project.cover?.author_name || "");
    }
  }, [project.project_id, project.crf_fields?.length]);

  const sectionsGrouped = useMemo(() => {
    const groups: Array<{ title: string; items: CRFFieldSpec[] }> = [];
    const idxMap = new Map<string, number>();
    for (const f of fields) {
      const st =
        f.section_title?.trim() ||
        "SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES";
      if (!idxMap.has(st)) {
        idxMap.set(st, groups.length);
        groups.push({ title: st, items: [] });
      }
      groups[idxMap.get(st)!].items.push(f);
    }
    return groups;
  }, [fields]);

  const avgCompletion = useMemo(() => {
    if (!records.length || !fields.length) return 0;
    const total = records.reduce(
      (acc, r) => acc + computeCompletionPct(r, fields),
      0
    );
    return Math.round(total / records.length);
  }, [records, fields]);

  const filteredRecords = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return records;
    return records.filter(
      (r) =>
        r.patient_code.toLowerCase().includes(q) ||
        r.hospital_site.toLowerCase().includes(q) ||
        r.investigator_name.toLowerCase().includes(q)
    );
  }, [records, searchQuery]);

  function startNewPatientForm() {
    setEditingRecordId(null);
    setPatientCode(computeNextPatientCode(records));
    setInclusionDate(new Date().toISOString().slice(0, 10));
    setHospitalSite(hospitalChoices[0] || "HGOPY");
    setInvestigatorName(project.cover?.author_name || "");
    setValues({});
    setNotes("");
    setNotice(null);
  }

  function loadPatientIntoForm(rec: PatientRecord) {
    setEditingRecordId(rec.record_id);
    setPatientCode(rec.patient_code);
    setInclusionDate(rec.inclusion_date || "");
    setHospitalSite(rec.hospital_site || "");
    setInvestigatorName(rec.investigator_name || "");
    setValues({ ...(rec.values || {}) });
    setNotes(rec.notes || "");
    setNotice(`Édition de la fiche ${rec.patient_code}`);
    setSubTab("entry");
  }

  async function handleSavePatient(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNotice(null);
    onError("");
    try {
      const res = await api.upsertPatientRecord(project.project_id, {
        record_id: editingRecordId,
        patient_code: patientCode.trim(),
        inclusion_date: inclusionDate,
        hospital_site: hospitalSite.trim(),
        investigator_name: investigatorName.trim(),
        values,
        notes,
      });
      onProjectUpdated(res.project);
      setEditingRecordId(res.record.record_id);
      setPatientCode(res.record.patient_code);
      setNotice(
        `Patient ${res.record.patient_code} enregistré avec succès (${res.project.patient_records?.length ?? 0} fiches au total).`
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeletePatient(rec: PatientRecord) {
    if (
      !window.confirm(
        `Supprimer définitivement la fiche du patient ${rec.patient_code} ?`
      )
    ) {
      return;
    }
    setBusy(true);
    onError("");
    try {
      const updated = await api.deletePatientRecord(
        project.project_id,
        rec.record_id
      );
      onProjectUpdated(updated);
      if (editingRecordId === rec.record_id) {
        startNewPatientForm();
      }
      setNotice(`Fiche ${rec.patient_code} supprimée.`);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSyncFromAnnexe1(forceExtract: boolean) {
    setBusy(true);
    onError("");
    try {
      const res = await api.syncProjectCrf(project.project_id, forceExtract);
      onProjectUpdated(res.project);
      setDraftFields(res.crf_fields);
      setAnnexe1Html(res.annexe1_html);
      setNotice(
        `Masque de saisie synchronisé (${res.crf_fields.length} questions) avec dessins d'Annexe 1 mis à jour.`
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveDesignerSchema() {
    setBusy(true);
    onError("");
    try {
      const res = await api.updateProjectCrfSchema(
        project.project_id,
        draftFields,
        syncAnnexe1OnSave
      );
      onProjectUpdated(res.project);
      setDraftFields(res.crf_fields);
      setAnnexe1Html(res.annexe1_html);
      setNotice(
        syncAnnexe1OnSave
          ? `Masque de saisie (${res.crf_fields.length} champs) et Annexe 1 mis à jour avec succès.`
          : `Masque de saisie (${res.crf_fields.length} champs) enregistré.`
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function updateDraftField(idx: number, patch: Partial<CRFFieldSpec>) {
    setDraftFields((prev) =>
      prev.map((item, i) => {
        if (i !== idx) return item;
        const merged = { ...item, ...patch };
        if (patch.field_type === "dichotomous" && (!merged.options || merged.options.length === 0)) {
          merged.options = ["Oui", "Non"];
        }
        return merged;
      })
    );
  }

  function moveDraftField(idx: number, dir: -1 | 1) {
    const target = idx + dir;
    if (target < 0 || target >= draftFields.length) return;
    setDraftFields((prev) => {
      const copy = [...prev];
      const [moved] = copy.splice(idx, 1);
      copy.splice(target, 0, moved);
      return copy;
    });
  }

  function removeDraftField(idx: number) {
    setDraftFields((prev) => prev.filter((_, i) => i !== idx));
  }

  function addNewDraftField() {
    const lastSec =
      draftFields[draftFields.length - 1]?.section_title ||
      "SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES";
    const num = draftFields.length + 1;
    const newField: CRFFieldSpec = {
      field_id: `q_${Date.now().toString(36)}`,
      code: `Q${num}`,
      section_title: lastSec,
      label: "Nouvelle variable clinique",
      field_type: "dichotomous",
      options: ["Oui", "Non"],
      unit: "",
      placeholder: "",
      sub_field_label: "",
      required: false,
    };
    setDraftFields((prev) => [...prev, newField]);
  }

  function toggleMultiChoice(fieldId: string, option: string) {
    const current = values[fieldId];
    const list = Array.isArray(current) ? [...current] : [];
    const exists = list.includes(option);
    const updated = exists ? list.filter((x) => x !== option) : [...list, option];
    setValues((prev) => ({ ...prev, [fieldId]: updated }));
  }

  return (
    <div className="space-y-6">
      {/* Top Banner Card */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full bg-rose-50 px-3 py-1 text-xs font-semibold text-[#6C2826]">
              <ClipboardCheck className="h-3.5 w-3.5" />
              {lang === "en"
                ? "Appendix 1 (Case Report Form) & Clinical Data Capture"
                : "Annexe 1 (Fiche de Collecte) & Masque de Saisie Clinique"}
            </div>
            <h2 className="mt-2 text-lg font-bold text-slate-900">
              {t("m3.title")}
            </h2>
            <p className="mt-0.5 text-xs text-slate-600">
              {t("m3.subtitle")}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => handleSyncFromAnnexe1(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              title={
                lang === "en"
                  ? "Re-parse Appendix 1 and update drawn checkboxes and fields"
                  : "Ré-analyser le texte d'Annexe 1 et mettre à jour les dessins de cases et cadres"
              }
            >
              <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
              {t("m3.regenerate_crf")}
            </button>

            <a
              href={api.exportPatientsCsvUrl(project.project_id)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-700"
            >
              <Download className="h-3.5 w-3.5" />
              {t("m3.export_csv")}
            </a>

            <button
              type="button"
              onClick={onOpenModule2}
              className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-3 py-2 text-xs font-semibold text-white hover:bg-[#561f1d]"
            >
              <FileText className="h-3.5 w-3.5" />
              {lang === "en" ? "Open Word Studio" : "Voir dans Studio Word"}
            </button>
          </div>
        </div>

        {/* Summary KPI strip */}
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-xl border border-slate-100 bg-slate-50 px-3.5 py-2.5">
            <div className="text-[11px] font-medium text-slate-500">
              {lang === "en" ? "CRF Questions (Appendix 1)" : "Questions du masque (Annexe 1)"}
            </div>
            <div className="mt-0.5 text-lg font-bold text-[#6C2826]">
              {fields.length}
            </div>
          </div>
          <div className="rounded-xl border border-slate-100 bg-slate-50 px-3.5 py-2.5">
            <div className="text-[11px] font-medium text-slate-500">
              {lang === "en" ? "Clinical sections" : "Sections cliniques"}
            </div>
            <div className="mt-0.5 text-lg font-bold text-slate-900">
              {sectionsGrouped.length}
            </div>
          </div>
          <div className="rounded-xl border border-emerald-100 bg-emerald-50/70 px-3.5 py-2.5">
            <div className="text-[11px] font-medium text-emerald-700">
              {lang === "en" ? "Enrolled patients" : "Patients enregistrés"}
            </div>
            <div className="mt-0.5 text-lg font-bold text-emerald-800">
              {records.length}
            </div>
          </div>
          <div className="rounded-xl border border-indigo-100 bg-indigo-50/70 px-3.5 py-2.5">
            <div className="text-[11px] font-medium text-indigo-700">
              {lang === "en" ? "Average completion" : "Complétude moyenne"}
            </div>
            <div className="mt-0.5 text-lg font-bold text-indigo-800">
              {avgCompletion}%
            </div>
          </div>
        </div>

        {notice && (
          <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-800">
            <CheckCircle2 className="h-4 w-4 shrink-0" />
            <span>{notice}</span>
          </div>
        )}

        {/* Sub-navigation Tabs */}
        <div className="mt-4 flex flex-nowrap gap-2 overflow-x-auto no-scrollbar border-t border-slate-100 pt-3 pb-1">
          <button
            type="button"
            onClick={() => setSubTab("entry")}
            className={`inline-flex min-h-[42px] shrink-0 items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-semibold transition ${
              subTab === "entry"
                ? "bg-[#6C2826] text-white shadow-sm"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}
          >
            <UserPlus className="h-3.5 w-3.5" />
            {t("m3.patients_tab")} ({records.length})
          </button>
          <button
            type="button"
            onClick={() => setSubTab("designer")}
            className={`inline-flex min-h-[42px] shrink-0 items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-semibold transition ${
              subTab === "designer"
                ? "bg-[#6C2826] text-white shadow-sm"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}
          >
            <Settings2 className="h-3.5 w-3.5" />
            {t("m3.crf_tab")} ({fields.length})
          </button>
          <button
            type="button"
            onClick={() => setSubTab("paper_preview")}
            className={`inline-flex min-h-[42px] shrink-0 items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-semibold transition ${
              subTab === "paper_preview"
                ? "bg-[#6C2826] text-white shadow-sm"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}
          >
            <Database className="h-3.5 w-3.5" />
            {lang === "en"
              ? "A4 Paper Form Preview & Database"
              : "Aperçu Fiche Papier A4 & Base de Données"}
          </button>
        </div>
      </div>

      {/* SUB-TAB 1: PATIENT DATA ENTRY */}
      {subTab === "entry" && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          {/* Sidebar: Patient Registry (below form on mobile, left on desktop) */}
          <aside className="order-2 space-y-3 lg:order-1 lg:col-span-4">
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-bold text-slate-900">
                  {t("m3.patients_tab")} ({records.length})
                </h3>
                <button
                  type="button"
                  onClick={startNewPatientForm}
                  className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
                >
                  <Plus className="h-3.5 w-3.5" />
                  {t("m3.new_patient")}
                </button>
              </div>

              <div className="relative mt-3">
                <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Rechercher code, hôpital..."
                  className="w-full rounded-lg border border-slate-200 py-1.5 pl-8 pr-3 text-xs focus:border-[#6C2826] focus:outline-none"
                />
              </div>

              <div className="mt-3 max-h-[580px] space-y-2 overflow-y-auto pr-1">
                {filteredRecords.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-4 text-center text-xs text-slate-500">
                    Aucun patient enregistré pour le moment. Remplissez le masque ci-contre puis cliquez sur « Enregistrer le patient ».
                  </div>
                ) : (
                  filteredRecords.map((rec) => {
                    const pct = computeCompletionPct(rec, fields);
                    const isSelected = editingRecordId === rec.record_id;
                    return (
                      <div
                        key={rec.record_id}
                        className={`rounded-xl border p-3 transition ${
                          isSelected
                            ? "border-[#6C2826] bg-rose-50/50 ring-1 ring-[#6C2826]"
                            : "border-slate-200 bg-white hover:border-slate-300"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <button
                            type="button"
                            onClick={() => loadPatientIntoForm(rec)}
                            className="flex-1 text-left"
                          >
                            <div className="flex items-center gap-2">
                              <span className="font-mono text-xs font-bold text-[#6C2826]">
                                {rec.patient_code}
                              </span>
                              {rec.hospital_site && (
                                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
                                  {rec.hospital_site}
                                </span>
                              )}
                            </div>
                            <div className="mt-1 text-[11px] text-slate-500">
                              Inclusion : {rec.inclusion_date || "Non datée"} •{" "}
                              {pct}% rempli
                            </div>
                            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                              <div
                                className="h-full rounded-full bg-emerald-500"
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                          </button>

                          <div className="flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() => loadPatientIntoForm(rec)}
                              className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-indigo-600"
                              title="Modifier cette fiche"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDeletePatient(rec)}
                              className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                              title="Supprimer ce patient"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </aside>

          {/* Main Area: Interactive Masque de Saisie (top priority on mobile) */}
          <form
            onSubmit={handleSavePatient}
            className="order-1 space-y-5 lg:order-2 lg:col-span-8"
          >
            {/* Header Identification Block */}
            <div className="rounded-2xl border-2 border-[#6C2826]/30 bg-white p-4 shadow-sm sm:p-5">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
                <div>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-[#6C2826]">
                    {editingRecordId
                      ? lang === "en"
                        ? `Edit Record: ${patientCode}`
                        : `Modification Fiche : ${patientCode}`
                      : lang === "en"
                        ? "New Patient Record (CRF Data Entry)"
                        : "Nouvelle Fiche Patient (Masque de Saisie)"}
                  </span>
                  <h3 className="text-base font-bold text-slate-900">
                    {lang === "en"
                      ? "Anonymous Identification Header"
                      : "En-tête d'Identification Anonyme"}
                  </h3>
                </div>
                <button
                  type="submit"
                  disabled={busy}
                  className="inline-flex min-h-[44px] w-full items-center justify-center gap-1.5 rounded-lg bg-[#6C2826] px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-[#561f1d] disabled:opacity-50 sm:w-auto sm:text-xs"
                >
                  <Save className="h-4 w-4 sm:h-3.5 sm:w-3.5" />
                  {busy
                    ? lang === "en"
                      ? "Saving..."
                      : "Enregistrement..."
                    : editingRecordId
                      ? lang === "en"
                        ? "Update Record"
                        : "Mettre à jour la fiche"
                      : t("m3.save_patient")}
                </button>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label className="block text-xs font-semibold text-slate-700">
                    Code participant (anonyme) *
                  </label>
                  <input
                    type="text"
                    required
                    value={patientCode}
                    onChange={(e) => setPatientCode(e.target.value)}
                    placeholder="PAT-001"
                    className="mt-1 min-h-[44px] w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-base font-semibold focus:border-[#6C2826] focus:outline-none sm:text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-700">
                    Date de collecte / d'inclusion
                  </label>
                  <input
                    type="date"
                    value={inclusionDate}
                    onChange={(e) => setInclusionDate(e.target.value)}
                    className="mt-1 min-h-[44px] w-full rounded-lg border border-slate-300 px-3 py-2 text-base focus:border-[#6C2826] focus:outline-none sm:text-sm"
                  />
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-xs font-semibold text-slate-700">
                    Hôpital / Site d'inclusion
                  </label>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    {hospitalChoices.map((site) => {
                      const active = hospitalSite === site;
                      return (
                        <button
                          key={site}
                          type="button"
                          onClick={() => setHospitalSite(site)}
                          className={`inline-flex min-h-[44px] items-center gap-2 rounded-lg border px-3.5 py-2 text-sm font-medium transition sm:text-xs ${
                            active
                              ? "border-[#6C2826] bg-[#6C2826]/10 text-[#6C2826]"
                              : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                          }`}
                        >
                          <span
                            className={`flex h-4 w-4 items-center justify-center rounded-[2px] border text-[10px] font-bold ${
                              active
                                ? "border-[#6C2826] bg-[#6C2826] text-white"
                                : "border-slate-400 bg-white"
                            }`}
                          >
                            {active ? "✓" : ""}
                          </span>
                          {site}
                        </button>
                      );
                    })}
                    <input
                      type="text"
                      value={hospitalSite}
                      onChange={(e) => setHospitalSite(e.target.value)}
                      placeholder="Ou autre hôpital..."
                      className="min-h-[44px] min-w-[160px] flex-1 rounded-lg border border-slate-300 px-3 py-2 text-base focus:border-[#6C2826] focus:outline-none sm:text-xs"
                    />
                  </div>
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-xs font-semibold text-slate-700">
                    Nom de l'enquêteur
                  </label>
                  <input
                    type="text"
                    value={investigatorName}
                    onChange={(e) => setInvestigatorName(e.target.value)}
                    placeholder="Nom et matricule de l'enquêteur"
                    className="mt-1 min-h-[44px] w-full rounded-lg border border-slate-300 px-3 py-2 text-base focus:border-[#6C2826] focus:outline-none sm:text-xs"
                  />
                </div>
              </div>
            </div>

            {/* CRF Section Cards */}
            {sectionsGrouped.map((sec) => (
              <div
                key={sec.title}
                className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
              >
                <div className="bg-[#6C2826] px-4 py-2.5 text-xs font-bold tracking-wide text-white">
                  {sec.title}
                </div>
                <div className="divide-y divide-slate-100 p-4">
                  {sec.items.map((field) => {
                    const currentVal = values[field.field_id];
                    const subVal = String(
                      values[`${field.field_id}__sub`] ?? ""
                    );
                    const opts =
                      field.options && field.options.length > 0
                        ? field.options
                        : field.field_type === "dichotomous"
                          ? ["Oui", "Non"]
                          : [];

                    return (
                      <div
                        key={field.field_id}
                        className="py-3 first:pt-1 last:pb-1"
                      >
                        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                          <div className="sm:w-5/12">
                            <span className="text-xs font-semibold text-slate-900">
                              {field.code && (
                                <span className="mr-1.5 font-bold text-[#6C2826]">
                                  {field.code}.
                                </span>
                              )}
                              {field.label}
                            </span>
                            {field.required && (
                              <span className="ml-1 text-xs font-bold text-rose-600">
                                *
                              </span>
                            )}
                          </div>

                          <div className="space-y-2 sm:w-7/12">
                            {/* DICHOTOMOUS OR SINGLE CHOICE CHECKBOXES */}
                            {(field.field_type === "dichotomous" ||
                              field.field_type === "single_choice") && (
                              <div
                                className={
                                  field.field_type === "dichotomous" || opts.length === 2
                                    ? "grid grid-cols-2 gap-2 sm:flex sm:flex-wrap"
                                    : "flex flex-wrap gap-2"
                                }
                              >
                                {opts.map((opt) => {
                                  const checked = currentVal === opt;
                                  return (
                                    <button
                                      key={opt}
                                      type="button"
                                      onClick={() =>
                                        setValues((prev) => ({
                                          ...prev,
                                          [field.field_id]: checked ? "" : opt,
                                        }))
                                      }
                                      className={`inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg border px-3.5 py-2.5 text-left text-sm font-medium transition sm:justify-start sm:text-xs ${
                                        checked
                                          ? "border-[#6C2826] bg-[#6C2826]/10 text-[#6C2826] ring-1 ring-[#6C2826]/30"
                                          : "border-slate-300 bg-slate-50/50 text-slate-700 hover:bg-slate-100"
                                      }`}
                                    >
                                      <span
                                        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-[2px] border text-[11px] font-bold ${
                                          checked
                                            ? "border-[#6C2826] bg-[#6C2826] text-white"
                                            : "border-slate-500 bg-white"
                                        }`}
                                      >
                                        {checked ? "✓" : ""}
                                      </span>
                                      <span>{opt}</span>
                                    </button>
                                  );
                                })}
                              </div>
                            )}

                            {/* MULTI CHOICE CHECKBOXES */}
                            {field.field_type === "multi_choice" && (
                              <div className="flex flex-wrap gap-2">
                                {opts.map((opt) => {
                                  const selected =
                                    Array.isArray(currentVal) &&
                                    currentVal.includes(opt);
                                  return (
                                    <button
                                      key={opt}
                                      type="button"
                                      onClick={() =>
                                        toggleMultiChoice(field.field_id, opt)
                                      }
                                      className={`inline-flex min-h-[44px] items-center gap-2 rounded-lg border px-3.5 py-2.5 text-left text-sm font-medium transition sm:text-xs ${
                                        selected
                                          ? "border-indigo-600 bg-indigo-50 text-indigo-800 ring-1 ring-indigo-300"
                                          : "border-slate-300 bg-slate-50/50 text-slate-700 hover:bg-slate-100"
                                      }`}
                                    >
                                      <span
                                        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-[2px] border text-[11px] font-bold ${
                                          selected
                                            ? "border-indigo-600 bg-indigo-600 text-white"
                                            : "border-slate-500 bg-white"
                                        }`}
                                      >
                                        {selected ? "✓" : ""}
                                      </span>
                                      <span>{opt}</span>
                                    </button>
                                  );
                                })}
                              </div>
                            )}

                            {/* NUMBER INPUT WITH UNIT */}
                            {field.field_type === "number" && (
                              <div className="flex items-center gap-2">
                                <input
                                  type="number"
                                  step="any"
                                  value={
                                    currentVal === undefined ||
                                    currentVal === null
                                      ? ""
                                      : String(currentVal)
                                  }
                                  onChange={(e) =>
                                    setValues((prev) => ({
                                      ...prev,
                                      [field.field_id]: e.target.value,
                                    }))
                                  }
                                  placeholder="Saisir valeur..."
                                  className="min-h-[44px] w-full max-w-[180px] rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-base font-medium focus:border-[#6C2826] focus:bg-white focus:outline-none sm:text-sm"
                                />
                                {field.unit && (
                                  <span className="min-h-[44px] inline-flex items-center rounded-md bg-slate-100 px-3 text-xs font-semibold text-slate-600">
                                    {field.unit}
                                  </span>
                                )}
                              </div>
                            )}

                            {/* DATE INPUT */}
                            {field.field_type === "date" && (
                              <input
                                type="date"
                                value={String(currentVal ?? "")}
                                onChange={(e) =>
                                  setValues((prev) => ({
                                    ...prev,
                                    [field.field_id]: e.target.value,
                                  }))
                                }
                                className="min-h-[44px] w-full max-w-[220px] rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-base focus:border-[#6C2826] focus:bg-white focus:outline-none sm:text-sm"
                              />
                            )}

                            {/* TEXT INPUT */}
                            {field.field_type === "text" && (
                              <input
                                type="text"
                                value={String(currentVal ?? "")}
                                onChange={(e) =>
                                  setValues((prev) => ({
                                    ...prev,
                                    [field.field_id]: e.target.value,
                                  }))
                                }
                                placeholder={
                                  field.placeholder || "Saisir la réponse..."
                                }
                                className="min-h-[44px] w-full rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-base focus:border-[#6C2826] focus:bg-white focus:outline-none sm:text-xs"
                              />
                            )}

                            {/* CONDITIONAL SUB-FIELD */}
                            {field.sub_field_label && (
                              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-slate-200 bg-slate-50/70 px-2.5 py-2">
                                <span className="shrink-0 text-[11px] font-medium italic text-slate-600">
                                  ▸ {field.sub_field_label} :
                                </span>
                                <input
                                  type="text"
                                  value={subVal}
                                  onChange={(e) =>
                                    setValues((prev) => ({
                                      ...prev,
                                      [`${field.field_id}__sub`]: e.target.value,
                                    }))
                                  }
                                  placeholder="Préciser..."
                                  className="min-h-[40px] min-w-[140px] flex-1 rounded border border-slate-200 bg-white px-2.5 py-1.5 text-base focus:border-[#6C2826] focus:outline-none sm:text-xs"
                                />
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}

            {/* Clinical Notes & Footer Save */}
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <label className="block text-xs font-semibold text-slate-700">
                Observations cliniques / Remarques complémentaires de l'enquêteur
              </label>
              <textarea
                rows={2}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Observations particulières lors de la collecte de cette fiche..."
                className="mt-1.5 w-full rounded-lg border border-slate-300 p-3 text-base focus:border-[#6C2826] focus:outline-none sm:p-2.5 sm:text-xs"
              />
              <div className="mt-3 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
                <button
                  type="button"
                  onClick={startNewPatientForm}
                  className="min-h-[44px] w-full rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 sm:w-auto"
                >
                  Réinitialiser pour un nouveau patient
                </button>
                <button
                  type="submit"
                  disabled={busy}
                  className="inline-flex min-h-[46px] w-full items-center justify-center gap-1.5 rounded-lg bg-[#6C2826] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[#561f1d] disabled:opacity-50 sm:w-auto sm:text-xs"
                >
                  <Save className="h-4 w-4 sm:h-3.5 sm:w-3.5" />
                  {busy
                    ? "Enregistrement..."
                    : editingRecordId
                      ? "Mettre à jour ce patient"
                      : "Enregistrer ce patient dans la base"}
                </button>
              </div>
            </div>
          </form>
        </div>
      )}

      {/* SUB-TAB 2: MASQUE DE SAISIE & ANNEXE 1 DESIGNER */}
      {subTab === "designer" && (
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
            <div>
              <h3 className="text-base font-bold text-slate-900">
                Concepteur du Masque de Saisie & Fiche de Collecte (Annexe 1)
              </h3>
              <p className="mt-0.5 text-xs text-slate-600">
                Ajoutez, modifiez ou changez le type de dessin pour chaque question :
                les cases à cocher dichotomiques (Oui/Non), choix multiples et cadres à
                remplir sont générés automatiquement dans l'Annexe 1 (Word & HTML) et
                dans le Masque de Saisie Patients.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={addNewDraftField}
                className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-300 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-100"
              >
                <Plus className="h-3.5 w-3.5" />
                Ajouter une question
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={handleSaveDesignerSchema}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-4 py-2 text-xs font-semibold text-white hover:bg-[#561f1d] disabled:opacity-50"
              >
                <Save className="h-3.5 w-3.5" />
                {busy ? "Enregistrement..." : "Enregistrer & Synchroniser Annexe 1"}
              </button>
            </div>
          </div>

          <label className="mt-3 inline-flex items-center gap-2 text-xs font-medium text-slate-700">
            <input
              type="checkbox"
              checked={syncAnnexe1OnSave}
              onChange={(e) => setSyncAnnexe1OnSave(e.target.checked)}
              className="rounded border-slate-300 text-[#6C2826] focus:ring-[#6C2826]"
            />
            Mettre à jour automatiquement le texte structuré d'Annexe 1 et le tableau des variables lors de l'enregistrement
          </label>

          <div className="mt-4 space-y-3">
            {draftFields.map((item, idx) => (
              <div
                key={`${item.field_id}-${idx}`}
                className="rounded-xl border border-slate-200 bg-slate-50/60 p-3.5"
              >
                <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
                  <div className="md:col-span-2">
                    <label className="block text-[10px] font-bold uppercase text-slate-500">
                      Code
                    </label>
                    <input
                      type="text"
                      value={item.code}
                      onChange={(e) =>
                        updateDraftField(idx, { code: e.target.value })
                      }
                      className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-bold text-[#6C2826]"
                    />
                  </div>

                  <div className="md:col-span-5">
                    <label className="block text-[10px] font-bold uppercase text-slate-500">
                      Section de la fiche
                    </label>
                    <input
                      type="text"
                      value={item.section_title}
                      onChange={(e) =>
                        updateDraftField(idx, { section_title: e.target.value })
                      }
                      className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                    />
                  </div>

                  <div className="md:col-span-5">
                    <label className="block text-[10px] font-bold uppercase text-slate-500">
                      Libellé de la question
                    </label>
                    <input
                      type="text"
                      value={item.label}
                      onChange={(e) =>
                        updateDraftField(idx, { label: e.target.value })
                      }
                      className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium"
                    />
                  </div>

                  <div className="md:col-span-4">
                    <label className="block text-[10px] font-bold uppercase text-slate-500">
                      Type de contrôle / Dessin Annexe 1
                    </label>
                    <select
                      value={item.field_type}
                      onChange={(e) =>
                        updateDraftField(idx, {
                          field_type: e.target.value as CRFFieldType,
                        })
                      }
                      className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                    >
                      {(Object.keys(FIELD_TYPE_LABELS) as CRFFieldType[]).map(
                        (t) => (
                          <option key={t} value={t}>
                            {FIELD_TYPE_LABELS[t]}
                          </option>
                        )
                      )}
                    </select>
                  </div>

                  {["dichotomous", "single_choice", "multi_choice"].includes(
                    item.field_type
                  ) ? (
                    <div className="md:col-span-4">
                      <label className="block text-[10px] font-bold uppercase text-slate-500">
                        Modalités / Cases à cocher (séparées par ;)
                      </label>
                      <input
                        type="text"
                        value={(item.options || []).join(" ; ")}
                        onChange={(e) =>
                          updateDraftField(idx, {
                            options: e.target.value
                              .split(";")
                              .map((x) => x.trim())
                              .filter(Boolean),
                          })
                        }
                        placeholder="Oui ; Non"
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                      />
                    </div>
                  ) : (
                    <div className="md:col-span-4">
                      <label className="block text-[10px] font-bold uppercase text-slate-500">
                        Unité (pour champ numérique)
                      </label>
                      <input
                        type="text"
                        value={item.unit}
                        onChange={(e) =>
                          updateDraftField(idx, { unit: e.target.value })
                        }
                        placeholder="mois / ans, grammes, minutes..."
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                      />
                    </div>
                  )}

                  <div className="md:col-span-3">
                    <label className="block text-[10px] font-bold uppercase text-slate-500">
                      Sous-champ conditionnel (optionnel)
                    </label>
                    <input
                      type="text"
                      value={item.sub_field_label}
                      onChange={(e) =>
                        updateDraftField(idx, {
                          sub_field_label: e.target.value,
                        })
                      }
                      placeholder="Ex. Si oui, préciser"
                      className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                    />
                  </div>

                  <div className="flex items-end justify-end gap-1 md:col-span-1">
                    <button
                      type="button"
                      onClick={() => moveDraftField(idx, -1)}
                      disabled={idx === 0}
                      className="rounded border border-slate-200 bg-white p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-30"
                      title="Monter"
                    >
                      <ArrowUp className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => moveDraftField(idx, 1)}
                      disabled={idx === draftFields.length - 1}
                      className="rounded border border-slate-200 bg-white p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-30"
                      title="Descendre"
                    >
                      <ArrowDown className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removeDraftField(idx)}
                      className="rounded border border-rose-200 bg-white p-1.5 text-rose-600 hover:bg-rose-50"
                      title="Supprimer cette question"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SUB-TAB 3: PAPER PREVIEW (ANNEXE 1) & DATABASE TABLE */}
      {subTab === "paper_preview" && (
        <div className="space-y-6">
          {/* Paper Preview */}
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-base font-bold text-slate-900">
                  Aperçu de la Fiche de Collecte des Données (Annexe 1 — Dessins Word/A4)
                </h3>
                <p className="text-xs text-slate-500">
                  Chaque question dichotomique ou à choix affiche ses cases à cocher dessinées, et chaque variable numérique/texte affiche son cadre rectangulaire à remplir.
                </p>
              </div>
              <a
                href={api.exportDocxUrl(project.project_id)}
                onClick={(e) => {
                  e.preventDefault();
                  onOpenModule2();
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[#6C2826] px-3 py-1.5 text-xs font-semibold text-[#6C2826] hover:bg-rose-50"
              >
                <FileText className="h-3.5 w-3.5" />
                Exporter en Word (.docx)
              </a>
            </div>

            {annexe1Html ? (
              <div
                className="max-w-4xl rounded-xl border border-slate-200 bg-slate-50 p-3"
                dangerouslySetInnerHTML={{ __html: annexe1Html }}
              />
            ) : (
              <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-xs text-slate-500">
                Cliquez sur « Synchroniser depuis Annexe 1 » pour générer l'aperçu visuel.
              </div>
            )}
          </div>

          {/* Patient Dataset Table */}
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <FileSpreadsheet className="h-4 w-4 text-emerald-600" />
                <h3 className="text-base font-bold text-slate-900">
                  Base de Données des Patients Enregistrés ({records.length})
                </h3>
              </div>
              <a
                href={api.exportPatientsCsvUrl(project.project_id)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700"
              >
                <Download className="h-3.5 w-3.5" />
                Télécharger CSV (Excel / SPSS / R)
              </a>
            </div>

            {records.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-6 text-center text-xs text-slate-500">
                Aucune fiche patient enregistrée pour le moment. Utilisez l'onglet « Saisie des Patients » pour saisir votre premier patient.
              </div>
            ) : (
              <>
                {/* Mobile Patient Cards (< md) */}
                <div className="space-y-2.5 md:hidden">
                  {records.map((rec) => (
                    <div
                      key={rec.record_id}
                      className="rounded-xl border border-slate-200 bg-slate-50/50 p-3.5 text-xs"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="rounded bg-[#6C2826]/10 px-2 py-0.5 font-mono font-bold text-[#6C2826]">
                          {rec.patient_code}
                        </span>
                        <span className="text-[11px] text-slate-500">
                          {rec.inclusion_date || "Sans date"}
                        </span>
                      </div>
                      <div className="mt-1.5 text-[11px] font-medium text-slate-700">
                        Site : {rec.hospital_site || "—"}
                      </div>
                      <div className="mt-2 space-y-1 text-[11px] text-slate-600">
                        {fields.slice(0, 3).map((f) => {
                          const v = rec.values?.[f.field_id];
                          const txt = Array.isArray(v)
                            ? v.join(", ")
                            : v !== undefined && v !== null && String(v).trim() !== ""
                              ? String(v)
                              : "—";
                          return (
                            <div key={f.field_id} className="flex justify-between gap-2">
                              <span className="truncate text-slate-500">
                                {f.code ? `${f.code}. ` : ""}
                                {f.label}
                              </span>
                              <span className="shrink-0 font-semibold text-slate-800">
                                {txt}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                      <button
                        type="button"
                        onClick={() => loadPatientIntoForm(rec)}
                        className="mt-3 min-h-[44px] w-full rounded-lg border border-indigo-200 bg-white text-xs font-semibold text-indigo-700 hover:bg-indigo-50"
                      >
                        Ouvrir / Modifier la fiche
                      </button>
                    </div>
                  ))}
                </div>

                {/* Desktop Table (md+) */}
                <div className="hidden overflow-x-auto rounded-xl border border-slate-200 md:block">
                  <table className="min-w-full divide-y divide-slate-200 text-xs">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="px-3 py-2 text-left font-bold text-slate-700">
                          Code
                        </th>
                        <th className="px-3 py-2 text-left font-bold text-slate-700">
                          Site
                        </th>
                        <th className="px-3 py-2 text-left font-bold text-slate-700">
                          Date
                        </th>
                        {fields.slice(0, 8).map((f) => (
                          <th
                            key={f.field_id}
                            className="max-w-[160px] truncate px-3 py-2 text-left font-bold text-slate-700"
                            title={`${f.code}. ${f.label}`}
                          >
                            {f.code ? `${f.code} - ` : ""}
                            {f.label}
                          </th>
                        ))}
                        <th className="px-3 py-2 text-right font-bold text-slate-700">
                          Action
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 bg-white">
                      {records.map((rec) => (
                        <tr key={rec.record_id} className="hover:bg-slate-50">
                          <td className="whitespace-nowrap px-3 py-2 font-mono font-bold text-[#6C2826]">
                            {rec.patient_code}
                          </td>
                          <td className="whitespace-nowrap px-3 py-2 text-slate-700">
                            {rec.hospital_site || "—"}
                          </td>
                          <td className="whitespace-nowrap px-3 py-2 text-slate-600">
                            {rec.inclusion_date || "—"}
                          </td>
                          {fields.slice(0, 8).map((f) => {
                            const v = rec.values?.[f.field_id];
                            const txt = Array.isArray(v)
                              ? v.join(", ")
                              : v !== undefined && v !== null && String(v).trim() !== ""
                                ? String(v)
                                : "—";
                            return (
                              <td
                                key={f.field_id}
                                className="max-w-[160px] truncate px-3 py-2 text-slate-700"
                                title={txt}
                              >
                                {txt}
                              </td>
                            );
                          })}
                          <td className="whitespace-nowrap px-3 py-2 text-right">
                            <button
                              type="button"
                              onClick={() => loadPatientIntoForm(rec)}
                              className="font-semibold text-indigo-600 hover:underline"
                            >
                              Ouvrir
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
