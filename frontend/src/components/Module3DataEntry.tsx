import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  BookOpen,
  CheckCircle2,
  ClipboardCheck,
  Database,
  Download,
  FileSpreadsheet,
  FileText,
  Plus,
  RefreshCw,
  Save,
  Search,
  Settings2,
  Sparkles,
  Trash2,
  UserPlus,
  Wand2,
  X,
} from "lucide-react";
import { api } from "../api/client";
import { useLanguage } from "../i18n/LanguageContext";
import type {
  CRFBoundaryDetection,
  CRFFieldSpec,
  CRFFieldType,
  CRFSyncReport,
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
  const [aiBusy, setAiBusy] = useState(false);
  const [extractBusy, setExtractBusy] = useState(false);
  const [selectedInternalSourceId, setSelectedInternalSourceId] = useState<string>(
    () => (project.internal_sources ?? [])[0]?.source_id ?? ""
  );
  const [extractReport, setExtractReport] = useState<{
    detection: CRFBoundaryDetection;
    sync_report: CRFSyncReport;
    model_used: string;
  } | null>(null);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiReport, setAiReport] = useState<{
    model_used: string;
    mode_applied: string;
    assistant_commentary: string;
    cames_compliance_checklist: string[];
  } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastSavedRecord, setLastSavedRecord] = useState<PatientRecord | null>(null);
  const formTopRef = useRef<HTMLFormElement | null>(null);

  // Paper preview HTML from backend crf_builder
  const [annexe1Html, setAnnexe1Html] = useState<string>("");

  // Designer editable copy of CRF fields
  const [draftFields, setDraftFields] = useState<CRFFieldSpec[]>(
    project.crf_fields ?? []
  );
  const [designerDirty, setDesignerDirty] = useState(false);
  const [syncAnnexe1OnSave, setSyncAnnexe1OnSave] = useState(true);
  const [backendHospitalSites, setBackendHospitalSites] = useState<string[]>([]);

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
    project.cover?.host_institutions?.find((s) => s.trim()) || "HGOPY"
  );
  const [investigatorName, setInvestigatorName] = useState<string>(
    project.cover?.author_name || ""
  );
  const [values, setValues] = useState<
    Record<string, string | string[] | number | boolean | null>
  >({});
  const [notes, setNotes] = useState<string>("");

  const hospitalChoices = useMemo(() => {
    const coverSites =
      project.cover?.host_institutions?.map((s) => s.trim()).filter(Boolean) ?? [];
    if (coverSites.length > 0) {
      return Array.from(new Set(coverSites)).slice(0, 8);
    }
    const syncedSites = backendHospitalSites.map((s) => s.trim()).filter(Boolean);
    if (syncedSites.length > 0) {
      return Array.from(new Set(syncedSites)).slice(0, 8);
    }
    return ["HGOPY", "HCBY", "HGY"];
  }, [project.cover?.host_institutions, backendHospitalSites]);

  // Content fingerprint across CRF upstream sources (omits updated_at so patient saves do not re-fetch preview HTML)
  const crfSyncSignature = useMemo(
    () =>
      JSON.stringify({
        id: project.project_id,
        crfFields: project.crf_fields ?? [],
        variables: project.variables ?? [],
        chap1Md: project.sections?.chap1_variables?.content_markdown ?? "",
        chap1Html: project.sections?.chap1_variables?.content_html ?? "",
        chap1Tables: project.sections?.chap1_variables?.custom_tables ?? [],
        annexesMd: project.sections?.annexes?.content_markdown ?? "",
        coverTitle: project.cover?.title ?? "",
        coverAuthor: project.cover?.author_name ?? "",
        coverMatricule: project.cover?.matricule ?? "",
        coverSites: project.cover?.host_institutions ?? [],
      }),
    [
      project.project_id,
      project.crf_fields,
      project.variables,
      project.sections?.chap1_variables?.content_markdown,
      project.sections?.chap1_variables?.content_html,
      project.sections?.chap1_variables?.custom_tables,
      project.sections?.annexes?.content_markdown,
      project.cover?.title,
      project.cover?.author_name,
      project.cover?.matricule,
      project.cover?.host_institutions,
    ]
  );

  // Reset dirty designer flag & transient notice when switching projects
  useEffect(() => {
    setDesignerDirty(false);
    setLastSavedRecord(null);
  }, [project.project_id]);

  // Real-time synchronize draft schema, hospital sites & Annexe 1 HTML when any source changes
  useEffect(() => {
    let cancelled = false;
    if (!designerDirty) {
      setDraftFields(project.crf_fields ?? []);
    }
    api
      .getCrfPreviewHtml(project.project_id)
      .then((res) => {
        if (cancelled) return;
        setAnnexe1Html(res.annexe1_html);
        if (res.hospital_sites?.length) {
          setBackendHospitalSites(res.hospital_sites);
        }
        const incomingJson = JSON.stringify(res.crf_fields ?? []);
        const currentJson = JSON.stringify(project.crf_fields ?? []);
        if (res.crf_fields && incomingJson !== currentJson) {
          onProjectUpdated({ ...project, crf_fields: res.crf_fields });
          if (!designerDirty) {
            setDraftFields(res.crf_fields);
          }
        }
      })
      .catch(() => {
        /* fallback silently if offline */
      });

    if (!editingRecordId) {
      setPatientCode(computeNextPatientCode(project.patient_records ?? []));
      setInvestigatorName(project.cover?.author_name || "");
      const preferredSite =
        project.cover?.host_institutions?.find((s) => s.trim()) ||
        hospitalChoices[0] ||
        "HGOPY";
      if (!hospitalSite || !hospitalChoices.includes(hospitalSite)) {
        setHospitalSite(preferredSite);
      }
    }
    return () => {
      cancelled = true;
    };
  }, [crfSyncSignature]);

  // Refresh project silently on window focus so multi-tab edits sync in real time
  useEffect(() => {
    function handleWindowFocus() {
      if (busy || designerDirty) return;
      api
        .getProject(project.project_id)
        .then((fresh) => {
          if (
            fresh.updated_at !== project.updated_at ||
            JSON.stringify(fresh.crf_fields ?? []) !==
              JSON.stringify(project.crf_fields ?? [])
          ) {
            onProjectUpdated(fresh);
          }
        })
        .catch(() => {
          /* ignore background refresh errors */
        });
    }
    window.addEventListener("focus", handleWindowFocus);
    return () => window.removeEventListener("focus", handleWindowFocus);
  }, [project.project_id, project.updated_at, project.crf_fields, busy, designerDirty]);

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

  const currentAnsweredCount = useMemo(() => {
    let filled = 0;
    for (const f of fields) {
      const val = values[f.field_id];
      if (
        Array.isArray(val)
          ? val.length > 0
          : val !== undefined && val !== null && String(val).trim() !== ""
      ) {
        filled += 1;
      }
    }
    return filled;
  }, [fields, values]);

  function resetForNextPatient(
    nextRecords: PatientRecord[],
    keepSiteAndInvestigator = true
  ) {
    setEditingRecordId(null);
    setPatientCode(computeNextPatientCode(nextRecords));
    setValues({});
    setNotes("");
    if (!keepSiteAndInvestigator) {
      setInclusionDate(new Date().toISOString().slice(0, 10));
      setHospitalSite(hospitalChoices[0] || "HGOPY");
      setInvestigatorName(project.cover?.author_name || "");
    }
  }

  function startNewPatientForm(clearNotice = true) {
    resetForNextPatient(records, false);
    setSubTab("entry");
    if (clearNotice) {
      setNotice(null);
      setLastSavedRecord(null);
    }
    formTopRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function loadPatientIntoForm(rec: PatientRecord) {
    setEditingRecordId(rec.record_id);
    setPatientCode(rec.patient_code);
    setInclusionDate(rec.inclusion_date || "");
    setHospitalSite(rec.hospital_site || "");
    setInvestigatorName(rec.investigator_name || "");
    setValues({ ...(rec.values || {}) });
    setNotes(rec.notes || "");
    setLastSavedRecord(null);
    setNotice(
      lang === "en"
        ? `Editing record ${rec.patient_code}`
        : `Modification de la fiche ${rec.patient_code}`
    );
    setSubTab("entry");
    formTopRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function handleSavePatient(e: React.FormEvent) {
    e.preventDefault();
    const wasEditing = Boolean(editingRecordId);
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
      const updatedRecords = res.project.patient_records ?? [];
      const nextCode = computeNextPatientCode(updatedRecords);
      setLastSavedRecord(res.record);
      resetForNextPatient(updatedRecords, true);
      setNotice(
        lang === "en"
          ? wasEditing
            ? `Record ${res.record.patient_code} updated! Form reset and ready for ${nextCode}.`
            : `Patient ${res.record.patient_code} saved (${updatedRecords.length} total)! Form reset and ready for ${nextCode}.`
          : wasEditing
            ? `Fiche ${res.record.patient_code} mise à jour ! Formulaire réinitialisé et prêt pour ${nextCode}.`
            : `Patient ${res.record.patient_code} enregistré (${updatedRecords.length} au total) ! Formulaire prêt pour ${nextCode}.`
      );
      formTopRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
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
      if (lastSavedRecord?.record_id === rec.record_id) {
        setLastSavedRecord(null);
      }
      if (editingRecordId === rec.record_id) {
        resetForNextPatient(updated.patient_records ?? [], false);
      }
      setNotice(`Fiche ${rec.patient_code} supprimée.`);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleAiGenerateOrImproveCrf(mode: "generate" | "improve" | "auto") {
    setAiBusy(true);
    setNotice(null);
    onError("");
    try {
      const res = await api.aiGenerateCrf(project.project_id, {
        mode,
        user_instruction: aiInstruction,
      });
      onProjectUpdated(res.project);
      setDraftFields(res.crf_fields);
      setAnnexe1Html(res.annexe1_html);
      setAiReport({
        model_used: res.model_used,
        mode_applied: res.mode_applied,
        assistant_commentary: res.assistant_commentary,
        cames_compliance_checklist: res.cames_compliance_checklist ?? [],
      });
      setNotice(
        lang === "en"
          ? `CAMES CRF ${res.mode_applied === "improve" ? "improved" : "generated"} (${res.crf_fields.length} items) using ${res.model_used} and rebuilt into the data mask.`
          : `Fiche de collecte CAMES ${res.mode_applied === "improve" ? "améliorée" : "générée"} (${res.crf_fields.length} champs) via ${res.model_used} et reconstruite dans le masque de saisie.`
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setAiBusy(false);
    }
  }

  async function handleExtractAndSyncCrf(
    sourceMode: "auto" | "section" | "internal_source" = "auto",
    sourceId?: string
  ) {
    setExtractBusy(true);
    setNotice(null);
    onError("");
    try {
      const res = await api.extractAndSyncCrf(project.project_id, {
        source_mode: sourceMode,
        source_id: sourceId || null,
        use_ai: true,
      });
      onProjectUpdated(res.project);
      setDraftFields(res.crf_fields);
      setAnnexe1Html(res.annexe1_html);
      setExtractReport({
        detection: res.detection,
        sync_report: res.sync_report,
        model_used: res.model_used,
      });
      const r = res.sync_report;
      setNotice(
        lang === "en"
          ? `Dedicated CRF extraction complete: ${r.total_fields} fields compiled (${r.preserved_count} preserved, ${r.added_count} added, ${r.modified_count} updated).`
          : `Extraction dédiée terminée : ${r.total_fields} champs compilés (${r.preserved_count} conservé(s), ${r.added_count} ajouté(s), ${r.modified_count} mis à jour — données patients préservées).`
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setExtractBusy(false);
    }
  }

  async function handleSyncFromAnnexe1(forceExtract: boolean) {
    setBusy(true);
    onError("");
    try {
      const res = await api.syncProjectCrf(project.project_id, forceExtract);
      onProjectUpdated(res.project);
      setDraftFields(res.crf_fields);
      setDesignerDirty(false);
      setAnnexe1Html(res.annexe1_html);
      if (res.detection && res.sync_report) {
        setExtractReport({
          detection: res.detection,
          sync_report: res.sync_report,
          model_used: res.model_used || "hybrid-crf-compiler",
        });
      }
      setNotice(
        lang === "en"
          ? `Mask synchronized (${res.crf_fields.length} fields) from dedicated Data Collection sub-section (Appendix 1).`
          : `Masque de saisie reconstruit (${res.crf_fields.length} questions) depuis la sous-partie Fiche de Collecte (Annexe 1).`
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
      setDesignerDirty(false);
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
    setDesignerDirty(true);
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
    setDesignerDirty(true);
    setDraftFields((prev) => {
      const copy = [...prev];
      const [moved] = copy.splice(idx, 1);
      copy.splice(target, 0, moved);
      return copy;
    });
  }

  function removeDraftField(idx: number) {
    setDesignerDirty(true);
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
    setDesignerDirty(true);
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
            <div className="flex flex-wrap items-center gap-2">
              <div className="inline-flex items-center gap-2 rounded-full bg-rose-50 px-3 py-1 text-xs font-semibold text-[#6C2826]">
                <ClipboardCheck className="h-3.5 w-3.5" />
                {lang === "en"
                  ? "Appendix 1 (Case Report Form) & Clinical Data Capture"
                  : "Annexe 1 (Fiche de Collecte) & Masque de Saisie Clinique"}
              </div>
              <div className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                {lang === "en"
                  ? "Real-time sync active (Ch. I.5 • M2 Tables • Appendix 1 • Cover)"
                  : "Synchronisation temps réel active (Chap. I.5 • Tableaux M2 • Annexe 1 • Page de garde)"}
              </div>
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
              onClick={() => startNewPatientForm(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700"
              title={
                lang === "en"
                  ? "Open a blank form for the next patient"
                  : "Ouvrir une fiche vierge pour enregistrer un nouveau patient"
              }
            >
              <Plus className="h-3.5 w-3.5" />
              {t("m3.new_patient")}
            </button>

            <button
              type="button"
              disabled={busy || aiBusy || extractBusy}
              onClick={() => handleSyncFromAnnexe1(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              title={
                lang === "en"
                  ? "Rebuild the data mask directly from the dedicated Data Collection Form sub-section"
                  : "Reconstruire le masque de saisie depuis la sous-partie dédiée Fiche de Collecte (Annexes)"
              }
            >
              <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
              {lang === "en" ? "Rebuild Mask from CRF" : "Construire Masque depuis la Fiche"}
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
              disabled={busy}
              onClick={() => handleSyncFromAnnexe1(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              title={
                lang === "en"
                  ? "Force re-parsing Appendix 1 and Chapter I.5 variables"
                  : "Forcer la ré-analyse d'Annexe 1 et du Chapitre I.5"
              }
            >
              <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
              {lang === "en" ? "Resync" : "Resynchroniser"}
            </button>

            <button
              type="button"
              onClick={onOpenModule2}
              className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-3 py-2 text-xs font-semibold text-white hover:bg-[#561f1d]"
              title={
                lang === "en"
                  ? "Open Module 2 (Word Studio) to review Appendix 1 inside the full protocol and export Word / PDF"
                  : "Ouvrir le Module 2 (Studio Word) pour vérifier l'Annexe 1 dans le protocole complet et exporter en Word / PDF"
              }
            >
              <FileText className="h-3.5 w-3.5" />
              {lang === "en" ? "Word Studio (M2)" : "Studio Word (M2)"}
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
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-800">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              <span>{notice}</span>
            </div>
            {lastSavedRecord && (
              <button
                type="button"
                onClick={() => loadPatientIntoForm(lastSavedRecord)}
                className="rounded-md border border-emerald-300 bg-white px-2.5 py-1 text-[11px] font-semibold text-emerald-800 hover:bg-emerald-100"
              >
                {lang === "en"
                  ? `Reopen ${lastSavedRecord.patient_code}`
                  : `Rouvrir ${lastSavedRecord.patient_code}`}
              </button>
            )}
          </div>
        )}

        {/* Phase 2: CAMES AI CRF Generator & Improver */}
        <div className="mt-4 rounded-xl border border-indigo-200/80 bg-gradient-to-br from-indigo-50/70 via-white to-rose-50/40 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-2xl">
              <div className="flex items-center gap-2 text-xs font-bold text-indigo-950">
                <Sparkles className="h-4 w-4 text-indigo-600" />
                {lang === "en"
                  ? "AI Assistant — CAMES Data Collection Form & Mask Builder"
                  : "Assistant IA — Création & Amélioration de la Fiche de Collecte (Normes CAMES)"}
              </div>
              <p className="mt-0.5 text-[11px] text-slate-600">
                {lang === "en"
                  ? "Generates or refines your dedicated Data Collection Form (Appendix 1) using your objectives, operational variables, literature scores, user needs, and strict CAMES/GCP anonymization rules, then rebuilds this mask."
                  : "Génère une nouvelle fiche ou améliore la fiche existante en croisant vos besoins, les scores issus de la revue de la littérature, vos objectifs/variables et les 7 normes CAMES/BPC, puis construit automatiquement le masque."}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={busy || aiBusy}
                onClick={() => handleAiGenerateOrImproveCrf("generate")}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50"
              >
                {aiBusy ? (
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                {lang === "en" ? "Generate Form (CAMES AI)" : "Générer Fiche (IA CAMES)"}
              </button>
              <button
                type="button"
                disabled={busy || aiBusy}
                onClick={() => handleAiGenerateOrImproveCrf("improve")}
                className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-300 bg-white px-3 py-2 text-xs font-semibold text-indigo-800 hover:bg-indigo-50 disabled:opacity-50"
              >
                {aiBusy ? (
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Wand2 className="h-3.5 w-3.5" />
                )}
                {lang === "en" ? "Improve Existing Form" : "Améliorer Fiche Existante"}
              </button>
            </div>
          </div>

          <div className="mt-3">
            <input
              type="text"
              value={aiInstruction}
              onChange={(e) => setAiInstruction(e.target.value)}
              placeholder={
                lang === "en"
                  ? "Specific needs (e.g., include Apgar score at 1/5 min, stratify blood pressure categories, maternal comorbidities...)"
                  : "Vos besoins précis (ex. inclure le score d'Apgar à 1 et 5 min, détailler les comorbidités maternelles, ajouter le coût en FCFA...)"
              }
              className="w-full rounded-lg border border-indigo-200 bg-white px-3 py-2 text-xs text-slate-800 placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none"
            />
          </div>

          {aiReport && (
            <div className="mt-3 rounded-lg border border-indigo-100 bg-white/90 p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2 font-semibold text-indigo-900">
                <span>
                  {lang === "en" ? "Methodological Report" : "Rapport méthodologique IA"} (
                  {aiReport.model_used})
                </span>
                <span className="rounded bg-indigo-50 px-2 py-0.5 text-[10px] font-bold uppercase text-indigo-700">
                  {aiReport.mode_applied}
                </span>
              </div>
              {aiReport.assistant_commentary && (
                <p className="mt-1 text-[11px] leading-relaxed text-slate-700">
                  {aiReport.assistant_commentary}
                </p>
              )}
              {aiReport.cames_compliance_checklist.length > 0 && (
                <ul className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
                  {aiReport.cames_compliance_checklist.map((chk, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-1.5 text-[11px] text-emerald-800"
                    >
                      <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-600" />
                      <span>{chk}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        {/* Dedicated CRF Extractor & Non-Destructive Compiler Control Strip */}
        <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50/40 p-3.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-xs font-bold text-emerald-950">
                <Search className="h-4 w-4 text-emerald-700" />
                {lang === "en"
                  ? "Dedicated CRF Boundary Extractor & Non-Destructive Mask Sync"
                  : "Extracteur & Compilateur dédié de Fiche de Collecte (Début → Fin)"}
              </div>
              <p className="mt-0.5 text-[11px] text-slate-600">
                {lang === "en"
                  ? "Isolates the Data Collection Form cleanly (even from Word tables or dotted lines), compiles fields into the mask, and preserves existing patient record IDs."
                  : "Isole précisément les bornes de votre fiche (tableaux Word, pointillés, choix /), recompile le masque sans altérer les données patients déjà saisies, et contrôle l'anonymat CAMES."}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={busy || aiBusy || extractBusy}
                onClick={() => handleExtractAndSyncCrf("auto")}
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-700 px-3 py-2 text-xs font-semibold text-white shadow-xs hover:bg-emerald-800 disabled:opacity-50"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${extractBusy ? "animate-spin" : ""}`} />
                {extractBusy
                  ? lang === "en"
                    ? "Extracting & Compiling..."
                    : "Extraction & Compilation..."
                  : lang === "en"
                    ? "Analyze & Extract Form (AI)"
                    : "Analyser & extraire la fiche (IA)"}
              </button>
            </div>
          </div>

          {(project.internal_sources ?? []).length > 0 && (
            <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-emerald-200/70 pt-2.5 text-xs">
              <BookOpen className="h-3.5 w-3.5 shrink-0 text-emerald-700" />
              <span className="text-[11px] font-semibold text-emerald-950">
                {lang === "en" ? "From internal library:" : "Depuis une source interne :"}
              </span>
              <select
                value={selectedInternalSourceId}
                onChange={(e) => setSelectedInternalSourceId(e.target.value)}
                disabled={extractBusy}
                className="min-w-[180px] flex-1 rounded-lg border border-emerald-200 bg-white px-2.5 py-1.5 text-xs text-slate-800"
              >
                {(project.internal_sources ?? []).map((src) => (
                  <option key={src.source_id} value={src.source_id}>
                    {src.filename} ({src.mapped_sections.length} section(s))
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={busy || aiBusy || extractBusy || !selectedInternalSourceId}
                onClick={() => handleExtractAndSyncCrf("internal_source", selectedInternalSourceId)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-600 bg-white px-3 py-1.5 text-xs font-semibold text-emerald-800 hover:bg-emerald-50 disabled:opacity-50"
              >
                {lang === "en" ? "Extract from Source" : "Extraire depuis source interne"}
              </button>
            </div>
          )}

          {extractReport && (
            <div className="mt-3 rounded-lg border border-emerald-200 bg-white p-3 text-xs text-slate-800">
              <div className="flex flex-wrap items-center justify-between gap-2 font-semibold text-emerald-950">
                <span>
                  {extractReport.detection.detected_title ||
                    (lang === "en" ? "CRF Boundary Control Report" : "Carte de contrôle d'extraction CRF")}
                </span>
                <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 font-bold text-emerald-800">
                    {lang === "en" ? "Confidence" : "Confiance"} :{" "}
                    {Math.round((extractReport.detection.confidence || 0) * 100)}%
                  </span>
                  <span className="rounded bg-slate-100 px-2 py-0.5 font-semibold text-slate-700">
                    {extractReport.model_used}
                  </span>
                </div>
              </div>

              {(extractReport.detection.start_excerpt || extractReport.detection.end_excerpt) && (
                <div className="mt-2 grid grid-cols-1 gap-2 rounded bg-slate-50 p-2 text-[11px] sm:grid-cols-2">
                  <div>
                    <span className="font-bold text-slate-700">
                      {lang === "en" ? "Detected start:" : "Début repéré :"}
                    </span>{" "}
                    <span className="text-slate-600">{extractReport.detection.start_excerpt}</span>
                  </div>
                  <div>
                    <span className="font-bold text-slate-700">
                      {lang === "en" ? "Detected end:" : "Fin repérée :"}
                    </span>{" "}
                    <span className="text-slate-600">{extractReport.detection.end_excerpt}</span>
                  </div>
                </div>
              )}

              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                <span className="rounded bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-800">
                  {extractReport.sync_report.preserved_count} {lang === "en" ? "preserved" : "conservé(s)"}
                </span>
                <span className="rounded bg-indigo-50 px-2 py-0.5 font-semibold text-indigo-800">
                  +{extractReport.sync_report.added_count} {lang === "en" ? "added" : "ajouté(s)"}
                </span>
                <span className="rounded bg-amber-50 px-2 py-0.5 font-semibold text-amber-800">
                  {extractReport.sync_report.modified_count} {lang === "en" ? "updated" : "modifié(s)"}
                </span>
                <span className="text-slate-600">
                  • {extractReport.sync_report.sections_count}{" "}
                  {lang === "en" ? "clinical sections" : "sections cliniques"}
                </span>
              </div>

              {extractReport.detection.excluded_neighbor_annexes.length > 0 && (
                <div className="mt-1.5 text-[11px] text-slate-600">
                  <strong>
                    {lang === "en" ? "Excluded neighbor annexes:" : "Annexes voisines écartées :"}
                  </strong>{" "}
                  {extractReport.detection.excluded_neighbor_annexes.join(" ; ")}
                </div>
              )}

              {extractReport.sync_report.cames_warnings.length > 0 && (
                <div className="mt-2 space-y-1 rounded border border-amber-200 bg-amber-50/70 p-2 text-[11px] text-amber-950">
                  {extractReport.sync_report.cames_warnings.map((w, i) => (
                    <div key={i} className="flex items-start gap-1.5">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700" />
                      <span>{w}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

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
                <span className="text-[11px] text-slate-400">
                  {lang === "en" ? "Click a card to edit" : "Cliquer pour éditer"}
                </span>
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
                            title={
                              lang === "en"
                                ? "Click to open and edit this record"
                                : "Cliquer pour ouvrir et modifier cette fiche"
                            }
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
                    );
                  })
                )}
              </div>
            </div>
          </aside>

          {/* Main Area: Interactive Masque de Saisie (top priority on mobile) */}
          <form
            ref={formTopRef}
            onSubmit={handleSavePatient}
            className="order-1 space-y-5 lg:order-2 lg:col-span-8"
          >
            {/* Header Identification Block */}
            <div className="rounded-2xl border-2 border-[#6C2826]/30 bg-white p-4 shadow-sm sm:p-5">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded-full px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wider ${
                      editingRecordId
                        ? "bg-amber-100 text-amber-800"
                        : "bg-emerald-100 text-emerald-800"
                    }`}
                  >
                    {editingRecordId
                      ? lang === "en"
                        ? `Editing: ${patientCode}`
                        : `Modification : ${patientCode}`
                      : lang === "en"
                        ? `New Record (${patientCode})`
                        : `Nouvelle fiche (${patientCode})`}
                  </span>
                  <h3 className="text-base font-bold text-slate-900">
                    {lang === "en"
                      ? "Anonymous Identification Header"
                      : "En-tête d'Identification Anonyme"}
                  </h3>
                </div>

                <div className="flex items-center gap-2">
                  <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-700">
                    {currentAnsweredCount} / {fields.length}{" "}
                    {lang === "en" ? "answered" : "renseignés"}
                  </span>
                  {editingRecordId && (
                    <button
                      type="button"
                      onClick={() => startNewPatientForm(true)}
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                    >
                      <X className="h-3.5 w-3.5" />
                      {lang === "en" ? "Cancel edit" : "Annuler l'édition"}
                    </button>
                  )}
                </div>
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
                <div>
                  <label className="block text-xs font-semibold text-slate-700">
                    Nom de l'enquêteur
                  </label>
                  <input
                    type="text"
                    value={investigatorName}
                    onChange={(e) => setInvestigatorName(e.target.value)}
                    placeholder={
                      lang === "en"
                        ? "Investigator name and ID"
                        : "Nom et matricule de l'enquêteur"
                    }
                    className="mt-1 min-h-[44px] w-full rounded-lg border border-slate-300 px-3 py-2 text-base focus:border-[#6C2826] focus:outline-none sm:text-xs"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-700">
                    Hôpital / Site d'inclusion
                  </label>
                  <input
                    type="text"
                    value={hospitalSite}
                    onChange={(e) => setHospitalSite(e.target.value)}
                    placeholder="Ou autre hôpital..."
                    className="mt-1 min-h-[44px] w-full rounded-lg border border-slate-300 px-3 py-2 text-base focus:border-[#6C2826] focus:outline-none sm:text-xs"
                  />
                </div>
                <div className="sm:col-span-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {hospitalChoices.map((site) => {
                      const active = hospitalSite === site;
                      return (
                        <button
                          key={site}
                          type="button"
                          onClick={() => setHospitalSite(site)}
                          className={`inline-flex min-h-[38px] items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
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
                  </div>
                </div>
              </div>
            </div>

            {/* CRF Section Cards */}
            {sectionsGrouped.map((sec) => {
              const secAnswered = sec.items.reduce((acc, f) => {
                const v = values[f.field_id];
                const ok = Array.isArray(v)
                  ? v.length > 0
                  : v !== undefined && v !== null && String(v).trim() !== "";
                return acc + (ok ? 1 : 0);
              }, 0);
              return (
              <div
                key={sec.title}
                className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
              >
                <div className="flex items-center justify-between gap-2 bg-[#6C2826] px-4 py-2.5 text-xs font-bold tracking-wide text-white">
                  <span>{sec.title}</span>
                  <span className="rounded bg-white/15 px-2 py-0.5 text-[10px] font-semibold">
                    {secAnswered} / {sec.items.length}
                  </span>
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
                                  placeholder={lang === "en" ? "Specify..." : "Préciser..."}
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
              );
            })}

            {/* Clinical Notes & Footer Save */}
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <label className="block text-xs font-semibold text-slate-700">
                Observations cliniques / Remarques complémentaires de l'enquêteur
              </label>
              <textarea
                rows={2}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder={
                  lang === "en"
                    ? "Special observations during data collection for this record..."
                    : "Observations particulières lors de la collecte de cette fiche..."
                }
                className="mt-1.5 w-full rounded-lg border border-slate-300 p-3 text-base focus:border-[#6C2826] focus:outline-none sm:p-2.5 sm:text-xs"
              />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  {editingRecordId ? (
                    <button
                      type="button"
                      onClick={() => startNewPatientForm(true)}
                      className="inline-flex min-h-[40px] items-center gap-1 rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
                    >
                      <X className="h-3.5 w-3.5" />
                      {lang === "en" ? "Cancel edit" : "Annuler l'édition"}
                    </button>
                  ) : (
                    <span className="text-[11px] text-slate-500">
                      {lang === "en"
                        ? "Auto-resets after saving so you can enter the next patient right away."
                        : "Réinitialisation automatique après enregistrement pour enchaîner les fiches."}
                    </span>
                  )}
                </div>
                <button
                  type="submit"
                  disabled={busy}
                  className="inline-flex min-h-[46px] w-full items-center justify-center gap-1.5 rounded-lg bg-[#6C2826] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[#561f1d] disabled:opacity-50 sm:w-auto sm:text-xs"
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
                      placeholder={lang === "en" ? "e.g. If yes, specify" : "Ex. Si oui, préciser"}
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
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <FileSpreadsheet className="h-4 w-4 text-emerald-600" />
              <h3 className="text-base font-bold text-slate-900">
                Base de Données des Patients Enregistrés ({records.length})
              </h3>
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
