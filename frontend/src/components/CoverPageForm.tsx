import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Image as ImageIcon,
  Lock,
  Plus,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { api, withAuthToken } from "../api/client";
import type {
  CoverMetadata,
  ProtocolProject,
  SupervisorInfo,
} from "../types/protocol";
import { formatBilingualContactTier } from "../utils/coverHeader";

export function emptySupervisor(): SupervisorInfo {
  return { name: "", rank: "", specialty: "" };
}

export function formatSupervisorSummary(sup: SupervisorInfo): string {
  return [sup.name, sup.rank, sup.specialty]
    .map((x) => x.trim())
    .filter(Boolean)
    .join(", ");
}

export function createDefaultCoverMetadata(
  overrides?: Partial<CoverMetadata>
): CoverMetadata {
  const base: CoverMetadata = {
    title: "",
    degree_label: "Doctorat en Médecine Générale",
    university_fr: "UNIVERSITÉ DE YAOUNDÉ I",
    university_en: "THE UNIVERSITY OF YAOUNDÉ I",
    university_po_box: "",
    university_email: "",
    faculty_fr: "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES",
    faculty_en: "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES",
    faculty_po_box: "",
    faculty_email: "",
    university_logo_path: "",
    university_logo_url: "",
    faculty_logo_path: "",
    faculty_logo_url: "",
    department_fr: "SANTE PUBLIQUE",
    department_en: "PUBLIC HEALTH",
    author_name: "",
    matricule: "",
    student_level: "Étudiant(e) en 6ème année de médecine générale",
    student_phone: "",
    student_email: "",
    director: "",
    director_info: emptySupervisor(),
    co_directors: [],
    co_directors_info: [],
    academic_year: "2025-2026",
    study_period_label: "",
    host_institutions: [],
    ethics_committees: [
      "Comité Institutionnel d'Éthique et de la Recherche de la FMSB (CIER-FMSB)",
    ],
    ...overrides,
  };
  return withStructuredSupervisors(base);
}

export function withStructuredSupervisors(cov: CoverMetadata): CoverMetadata {
  const dirInfo: SupervisorInfo = cov.director_info ?? {
    name: cov.director || "",
    rank: "",
    specialty: "",
  };
  const codirsInfo: SupervisorInfo[] =
    cov.co_directors_info && cov.co_directors_info.length > 0
      ? cov.co_directors_info
      : (cov.co_directors || [])
          .filter((x) => x.trim().length > 0)
          .map((x) => ({ name: x.trim(), rank: "", specialty: "" }));

  return {
    ...cov,
    university_fr: cov.university_fr ?? "UNIVERSITÉ DE YAOUNDÉ I",
    university_en: cov.university_en ?? "THE UNIVERSITY OF YAOUNDÉ I",
    university_po_box: cov.university_po_box ?? "",
    university_email: cov.university_email ?? "",
    faculty_fr:
      cov.faculty_fr ?? "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES",
    faculty_en:
      cov.faculty_en ?? "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES",
    faculty_po_box: cov.faculty_po_box ?? "",
    faculty_email: cov.faculty_email ?? "",
    university_logo_path: cov.university_logo_path ?? "",
    university_logo_url: cov.university_logo_url ?? "",
    faculty_logo_path: cov.faculty_logo_path ?? "",
    faculty_logo_url: cov.faculty_logo_url ?? "",
    student_level:
      cov.student_level ?? "Étudiant(e) en 6ème année de médecine générale",
    student_phone: cov.student_phone ?? "",
    student_email: cov.student_email ?? "",
    academic_year: cov.academic_year ?? "2025-2026",
    study_period_label: cov.study_period_label ?? "",
    host_institutions: cov.host_institutions ?? [],
    ethics_committees: cov.ethics_committees ?? [
      "Comité Institutionnel d'Éthique et de la Recherche de la FMSB (CIER-FMSB)",
    ],
    director_info: dirInfo,
    co_directors_info: codirsInfo,
  };
}

export function finalizeCoverMetadataPayload(
  cover: CoverMetadata,
  pendingCoDir?: SupervisorInfo
): CoverMetadata {
  const finalCodirs = [...(cover.co_directors_info ?? [])];
  if (
    pendingCoDir &&
    (pendingCoDir.name.trim() ||
      pendingCoDir.rank.trim() ||
      pendingCoDir.specialty.trim())
  ) {
    finalCodirs.push({
      name: pendingCoDir.name.trim(),
      rank: pendingCoDir.rank.trim(),
      specialty: pendingCoDir.specialty.trim(),
    });
  }
  const dirInfo = cover.director_info ?? emptySupervisor();
  return {
    ...cover,
    director_info: dirInfo,
    director: formatSupervisorSummary(dirInfo),
    co_directors_info: finalCodirs,
    co_directors: finalCodirs.map(formatSupervisorSummary).filter(Boolean),
  };
}

interface CoverPageFormProps {
  value: CoverMetadata;
  onChange: (next: CoverMetadata) => void;
  readOnly?: boolean;
  titleRequired?: boolean;
  projectId?: string | null;
  onProjectUpdated?: (updated: ProtocolProject) => void;
  stagedLogos?: { university: File | null; faculty: File | null };
  onStageLogo?: (slot: "university" | "faculty", file: File | null) => void;
  onError?: (msg: string) => void;
  coDirDraft?: SupervisorInfo;
  onCoDirDraftChange?: (draft: SupervisorInfo) => void;
}

export function CoverPageForm({
  value,
  onChange,
  readOnly = false,
  titleRequired = false,
  projectId = null,
  onProjectUpdated,
  stagedLogos,
  onStageLogo,
  onError,
  coDirDraft: controlledCoDirDraft,
  onCoDirDraftChange,
}: CoverPageFormProps) {
  const [internalCoDirDraft, setInternalCoDirDraft] =
    useState<SupervisorInfo>(emptySupervisor);
  const coDirDraft = controlledCoDirDraft ?? internalCoDirDraft;
  const setCoDirDraft = (next: SupervisorInfo) => {
    if (onCoDirDraftChange) {
      onCoDirDraftChange(next);
    } else {
      setInternalCoDirDraft(next);
    }
  };

  const [translatingField, setTranslatingField] = useState<string | null>(null);
  const [uploadingLogoSlot, setUploadingLogoSlot] = useState<
    "university" | "faculty" | null
  >(null);
  const [showAdvanced, setShowAdvanced] = useState<boolean>(() =>
    Boolean(
      (value.study_period_label || "").trim() ||
        (value.host_institutions || []).length > 0
    )
  );

  const [stagedUniUrl, setStagedUniUrl] = useState<string | null>(null);
  const [stagedFacUrl, setStagedFacUrl] = useState<string | null>(null);

  const translationTimersRef = useRef<Record<string, number>>({});
  const translationSeqRef = useRef<Record<string, number>>({});
  const latestValueRef = useRef<CoverMetadata>(value);
  latestValueRef.current = value;

  useEffect(() => {
    if (!stagedLogos?.university) {
      setStagedUniUrl(null);
      return;
    }
    const url = URL.createObjectURL(stagedLogos.university);
    setStagedUniUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [stagedLogos?.university]);

  useEffect(() => {
    if (!stagedLogos?.faculty) {
      setStagedFacUrl(null);
      return;
    }
    const url = URL.createObjectURL(stagedLogos.faculty);
    setStagedFacUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [stagedLogos?.faculty]);

  const scheduleBilingualCoverTranslation = (
    field: "university" | "faculty" | "department",
    rawText: string,
    sourceLang: "fr" | "en"
  ) => {
    if (readOnly) return;
    const prevTimer = translationTimersRef.current[field];
    if (prevTimer !== undefined) {
      window.clearTimeout(prevTimer);
    }
    const trimmed = rawText.trim();
    if (!trimmed) {
      setTranslatingField((curr) =>
        curr?.startsWith(`${field}:`) ? null : curr
      );
      return;
    }
    const seq = (translationSeqRef.current[field] ?? 0) + 1;
    translationSeqRef.current[field] = seq;
    const targetLang = sourceLang === "fr" ? "en" : "fr";

    translationTimersRef.current[field] = window.setTimeout(async () => {
      setTranslatingField(`${field}:${sourceLang}`);
      try {
        const res = await api.translateDepartment({
          text: trimmed,
          source_lang: sourceLang,
          target_lang: targetLang,
          field_type: field,
        });
        if (translationSeqRef.current[field] !== seq) return;
        if (res.translated_text) {
          const targetKey = `${field}_${targetLang}` as keyof CoverMetadata;
          onChange({
            ...latestValueRef.current,
            [targetKey]: res.translated_text,
          });
        }
      } catch {
        // Keep user input untouched if live translation fails transiently
      } finally {
        if (translationSeqRef.current[field] === seq) {
          setTranslatingField((curr) =>
            curr === `${field}:${sourceLang}` ? null : curr
          );
        }
      }
    }, 420);
  };

  const handleCoverLogoUpload = async (
    slot: "university" | "faculty",
    file: File | null
  ) => {
    if (!file || readOnly) return;
    if (!projectId) {
      onStageLogo?.(slot, file);
      return;
    }
    setUploadingLogoSlot(slot);
    onError?.("");
    try {
      const updated = await api.uploadCoverLogo(projectId, slot, file);
      const nextLogos = {
        university_logo_path: updated.cover.university_logo_path ?? "",
        university_logo_url: updated.cover.university_logo_url ?? "",
        faculty_logo_path: updated.cover.faculty_logo_path ?? "",
        faculty_logo_url: updated.cover.faculty_logo_url ?? "",
      };
      const nextCov = { ...latestValueRef.current, ...nextLogos };
      onChange(nextCov);
      onProjectUpdated?.({
        ...updated,
        cover: nextCov,
      });
    } catch (err: unknown) {
      onError?.(
        err instanceof Error ? err.message : "Échec de l'import du logo."
      );
    } finally {
      setUploadingLogoSlot(null);
    }
  };

  const handleCoverLogoDelete = async (slot: "university" | "faculty") => {
    if (readOnly) return;
    if (!projectId) {
      onStageLogo?.(slot, null);
      return;
    }
    setUploadingLogoSlot(slot);
    onError?.("");
    try {
      const updated = await api.deleteCoverLogo(projectId, slot);
      const nextLogos = {
        university_logo_path: updated.cover.university_logo_path ?? "",
        university_logo_url: updated.cover.university_logo_url ?? "",
        faculty_logo_path: updated.cover.faculty_logo_path ?? "",
        faculty_logo_url: updated.cover.faculty_logo_url ?? "",
      };
      const nextCov = { ...latestValueRef.current, ...nextLogos };
      onChange(nextCov);
      onProjectUpdated?.({
        ...updated,
        cover: nextCov,
      });
    } catch (err: unknown) {
      onError?.(
        err instanceof Error
          ? err.message
          : "Échec de la suppression du logo."
      );
    } finally {
      setUploadingLogoSlot(null);
    }
  };

  const inputBaseCls =
    "mt-1 w-full rounded-lg border px-3 py-2 text-sm focus:outline-none";
  const inputCls = readOnly
    ? `${inputBaseCls} cursor-not-allowed border-slate-200 bg-slate-100 text-slate-600`
    : `${inputBaseCls} border-slate-300 bg-white focus:border-indigo-500`;
  const compactInputBaseCls =
    "mt-1 w-full rounded-lg border px-3 py-1.5 text-sm focus:outline-none";
  const compactInputCls = readOnly
    ? `${compactInputBaseCls} cursor-not-allowed border-slate-200 bg-slate-100 text-slate-600`
    : `${compactInputBaseCls} border-slate-300 bg-white focus:border-indigo-500`;

  const uniPreviewSrc = stagedUniUrl
    ? stagedUniUrl
    : value.university_logo_url
    ? withAuthToken(value.university_logo_url)
    : "/tamplates/fmsb_uy1_logo.png";

  const facPreviewSrc = stagedFacUrl
    ? stagedFacUrl
    : value.faculty_logo_url
    ? withAuthToken(value.faculty_logo_url)
    : null;

  return (
    <div className="space-y-4">
      {readOnly && (
        <div className="flex items-start gap-2.5 rounded-xl border border-amber-300 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-950">
          <Lock className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
          <div>
            <span className="font-bold">
              Page de garde définie et verrouillée par l&apos;administrateur — Consultation uniquement.
            </span>{" "}
            Toute rectification (titre, institution, logos, impétrant ou jury) doit être effectuée depuis le Portail Administrateur.
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="md:col-span-2">
          <label className="flex flex-wrap items-center gap-2 text-xs font-medium text-slate-600">
            <span>
              Titre complet du protocole (encadré 18 à 24 pt)
              {titleRequired ? " *" : ""}
            </span>
            {readOnly && (
              <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">
                <Lock className="h-2.5 w-2.5" /> Verrouillé (Admin uniquement)
              </span>
            )}
          </label>
          <input
            type="text"
            required={titleRequired}
            disabled={readOnly}
            value={value.title}
            placeholder="Ex : Facteurs associés à l'hypertension artérielle à Yaoundé"
            onChange={(e) => onChange({ ...value, title: e.target.value })}
            className={inputCls}
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600">
            Diplôme visé
          </label>
          <input
            type="text"
            disabled={readOnly}
            value={value.degree_label}
            onChange={(e) =>
              onChange({ ...value, degree_label: e.target.value })
            }
            className={inputCls}
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600">
            Année académique
          </label>
          <input
            type="text"
            disabled={readOnly}
            value={value.academic_year}
            onChange={(e) =>
              onChange({ ...value, academic_year: e.target.value })
            }
            className={inputCls}
          />
        </div>

        {/* Institutional Header CAMES (Tiers 3, Optional Uni Contacts, Tier 4, Tier 5, Optional Fac Contacts, 1-2 Stacked Logos) */}
        <div className="md:col-span-2 space-y-3 rounded-xl border border-indigo-200 bg-indigo-50/40 p-3.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-bold text-indigo-950">
              En-tête institutionnel bilingue (CAMES — FR ↔ EN) &amp; Logos
            </span>
            {!readOnly && (
              <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2.5 py-0.5 text-[11px] font-medium text-indigo-800">
                <Sparkles className="h-3 w-3" />
                {translatingField
                  ? "Traduction automatique en cours..."
                  : "Traduction auto FR ↔ EN"}
              </span>
            )}
          </div>

          {/* Étage 3 : Université */}
          <div className="grid grid-cols-1 gap-2.5 rounded-lg border border-white/80 bg-white/80 p-2.5 sm:grid-cols-2">
            <div className="sm:col-span-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Étage 3 — Université
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Université (FR)
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : UNIVERSITÉ DE YAOUNDÉ I"
                value={value.university_fr ?? ""}
                onChange={(e) => {
                  const val = e.target.value;
                  onChange({ ...value, university_fr: val });
                  scheduleBilingualCoverTranslation("university", val, "fr");
                }}
                className={compactInputCls}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                University (EN)
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : THE UNIVERSITY OF YAOUNDÉ I"
                value={value.university_en ?? ""}
                onChange={(e) => {
                  const val = e.target.value;
                  onChange({ ...value, university_en: val });
                  scheduleBilingualCoverTranslation("university", val, "en");
                }}
                className={compactInputCls}
              />
            </div>
          </div>

          {/* Étage intermédiaire facultatif : Coordonnées Université */}
          <div className="grid grid-cols-1 gap-2.5 rounded-lg border border-dashed border-slate-300 bg-white/60 p-2.5 sm:grid-cols-2">
            <div className="sm:col-span-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                Étage intermédiaire (facultatif) — Coordonnées Université
              </span>
              <span className="text-[10px] text-slate-500">
                N&apos;apparaît entre l&apos;université et la faculté que s&apos;il est rempli
              </span>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Boîte postale Université (B.P.)
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : 337 Yaoundé (laisser vide pour masquer)"
                value={value.university_po_box ?? ""}
                onChange={(e) =>
                  onChange({ ...value, university_po_box: e.target.value })
                }
                className={compactInputCls}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Adresse e-mail Université
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : rectorat@uy1.uninet.cm (facultatif)"
                value={value.university_email ?? ""}
                onChange={(e) =>
                  onChange({ ...value, university_email: e.target.value })
                }
                className={compactInputCls}
              />
            </div>
            {(() => {
              const prevUni = formatBilingualContactTier(
                value.university_po_box,
                value.university_email
              );
              if (prevUni.fr.length === 0) return null;
              return (
                <div className="sm:col-span-2 flex flex-wrap justify-between gap-2 rounded bg-slate-100 px-2.5 py-1 text-[11px] text-slate-700">
                  <span>FR : {prevUni.fr.join(" • ")}</span>
                  <span>EN : {prevUni.en.join(" • ")}</span>
                </div>
              );
            })()}
          </div>

          {/* Étage 4 : Faculté / École */}
          <div className="grid grid-cols-1 gap-2.5 rounded-lg border border-white/80 bg-white/80 p-2.5 sm:grid-cols-2">
            <div className="sm:col-span-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Étage 4 — Faculté / École / Institut
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Faculté / École (FR)
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES"
                value={value.faculty_fr ?? ""}
                onChange={(e) => {
                  const val = e.target.value;
                  onChange({ ...value, faculty_fr: val });
                  scheduleBilingualCoverTranslation("faculty", val, "fr");
                }}
                className={compactInputCls}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Faculty / School (EN)
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES"
                value={value.faculty_en ?? ""}
                onChange={(e) => {
                  const val = e.target.value;
                  onChange({ ...value, faculty_en: val });
                  scheduleBilingualCoverTranslation("faculty", val, "en");
                }}
                className={compactInputCls}
              />
            </div>
          </div>

          {/* Étage 5 : Département */}
          <div className="grid grid-cols-1 gap-2.5 rounded-lg border border-white/80 bg-white/80 p-2.5 sm:grid-cols-2">
            <div className="sm:col-span-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Étage 5 — Département académique
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Département (FR)
              </label>
              <input
                type="text"
                disabled={readOnly}
                value={value.department_fr}
                onChange={(e) => {
                  const val = e.target.value;
                  onChange({ ...value, department_fr: val });
                  scheduleBilingualCoverTranslation("department", val, "fr");
                }}
                className={compactInputCls}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Department (EN)
              </label>
              <input
                type="text"
                disabled={readOnly}
                value={value.department_en}
                onChange={(e) => {
                  const val = e.target.value;
                  onChange({ ...value, department_en: val });
                  scheduleBilingualCoverTranslation("department", val, "en");
                }}
                className={compactInputCls}
              />
            </div>
          </div>

          {/* Étage final facultatif : Coordonnées Faculté */}
          <div className="grid grid-cols-1 gap-2.5 rounded-lg border border-dashed border-slate-300 bg-white/60 p-2.5 sm:grid-cols-2">
            <div className="sm:col-span-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                Étage après département (facultatif) — Coordonnées Faculté
              </span>
              <span className="text-[10px] text-slate-500">
                N&apos;apparaît après le département que s&apos;il est rempli
              </span>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Boîte postale Faculté (B.P.)
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : 1364 Yaoundé (laisser vide pour masquer)"
                value={value.faculty_po_box ?? ""}
                onChange={(e) =>
                  onChange({ ...value, faculty_po_box: e.target.value })
                }
                className={compactInputCls}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Adresse e-mail Faculté
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : decanat@fmsb-uy1.cm (facultatif)"
                value={value.faculty_email ?? ""}
                onChange={(e) =>
                  onChange({ ...value, faculty_email: e.target.value })
                }
                className={compactInputCls}
              />
            </div>
            {(() => {
              const prevFac = formatBilingualContactTier(
                value.faculty_po_box,
                value.faculty_email
              );
              if (prevFac.fr.length === 0) return null;
              return (
                <div className="sm:col-span-2 flex flex-wrap justify-between gap-2 rounded bg-slate-100 px-2.5 py-1 text-[11px] text-slate-700">
                  <span>FR : {prevFac.fr.join(" • ")}</span>
                  <span>EN : {prevFac.en.join(" • ")}</span>
                </div>
              );
            })()}
          </div>

          {/* Logos centraux : 1 ou 2 logos empilés */}
          <div className="grid grid-cols-1 gap-3 rounded-lg border border-white/80 bg-white/90 p-3 sm:grid-cols-2">
            <div className="sm:col-span-2 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
              Emblèmes centraux (1 logo au centre, ou Logo Université au-dessus + Logo Faculté en dessous)
            </div>

            {/* Logo 1 : Université mère */}
            <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50/70 p-2.5">
              <img
                src={uniPreviewSrc}
                alt="Logo Université"
                className="h-14 w-12 shrink-0 rounded border border-slate-200 bg-white object-contain p-0.5"
              />
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="text-xs font-semibold text-slate-800">
                  Logo 1 — Université mère (haut)
                </div>
                {!readOnly && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <label className="inline-flex cursor-pointer items-center gap-1 rounded bg-indigo-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-indigo-700">
                      <Upload className="h-3 w-3" />
                      {uploadingLogoSlot === "university" ? "Envoi…" : "Changer"}
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        className="hidden"
                        disabled={uploadingLogoSlot !== null}
                        onChange={(e) => {
                          const f = e.target.files?.[0] ?? null;
                          e.target.value = "";
                          void handleCoverLogoUpload("university", f);
                        }}
                      />
                    </label>
                    {(Boolean(value.university_logo_url) || Boolean(stagedUniUrl)) && (
                      <button
                        type="button"
                        disabled={uploadingLogoSlot !== null}
                        onClick={() => void handleCoverLogoDelete("university")}
                        className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-100"
                      >
                        Par défaut
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Logo 2 : Faculté (facultatif) */}
            <div className="flex items-center gap-3 rounded-lg border border-dashed border-slate-300 bg-slate-50/50 p-2.5">
              {facPreviewSrc ? (
                <img
                  src={facPreviewSrc}
                  alt="Logo Faculté"
                  className="h-14 w-12 shrink-0 rounded border border-slate-200 bg-white object-contain p-0.5"
                />
              ) : (
                <div className="flex h-14 w-12 shrink-0 items-center justify-center rounded border border-dashed border-slate-300 bg-white text-slate-400">
                  <ImageIcon className="h-5 w-5" />
                </div>
              )}
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="text-xs font-semibold text-slate-800">
                  Logo 2 — Faculté (facultatif, en dessous)
                </div>
                {!readOnly && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <label className="inline-flex cursor-pointer items-center gap-1 rounded bg-slate-800 px-2 py-1 text-[11px] font-semibold text-white hover:bg-slate-900">
                      <Upload className="h-3 w-3" />
                      {uploadingLogoSlot === "faculty"
                        ? "Envoi…"
                        : facPreviewSrc
                        ? "Remplacer"
                        : "Ajouter 2ᵉ logo"}
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        className="hidden"
                        disabled={uploadingLogoSlot !== null}
                        onChange={(e) => {
                          const f = e.target.files?.[0] ?? null;
                          e.target.value = "";
                          void handleCoverLogoUpload("faculty", f);
                        }}
                      />
                    </label>
                    {facPreviewSrc && (
                      <button
                        type="button"
                        disabled={uploadingLogoSlot !== null}
                        onClick={() => void handleCoverLogoDelete("faculty")}
                        className="inline-flex items-center gap-1 rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] font-medium text-rose-700 hover:bg-rose-100"
                      >
                        <Trash2 className="h-3 w-3" />
                        Retirer
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Identité étudiant sur la page de garde */}
        <div>
          <label className="block text-xs font-medium text-slate-600">
            Noms &amp; Prénoms de l&apos;étudiant(e)
          </label>
          <input
            type="text"
            disabled={readOnly}
            value={value.author_name}
            onChange={(e) => onChange({ ...value, author_name: e.target.value })}
            className={inputCls}
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600">
            Matricule
          </label>
          <input
            type="text"
            disabled={readOnly}
            value={value.matricule}
            onChange={(e) => onChange({ ...value, matricule: e.target.value })}
            className={inputCls}
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600">
            Niveau / Qualité académique
          </label>
          <input
            type="text"
            disabled={readOnly}
            value={value.student_level}
            placeholder="Ex : Étudiant(e) en 6ème année de médecine générale"
            onChange={(e) =>
              onChange({ ...value, student_level: e.target.value })
            }
            className={inputCls}
          />
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium text-slate-600">
              Téléphone étudiant
            </label>
            <input
              type="text"
              disabled={readOnly}
              value={value.student_phone}
              placeholder="691 38 46 52"
              onChange={(e) =>
                onChange({ ...value, student_phone: e.target.value })
              }
              className={inputCls}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600">
              E-mail étudiant
            </label>
            <input
              type="text"
              disabled={readOnly}
              value={value.student_email}
              placeholder="etudiant@fmsb.cm"
              onChange={(e) =>
                onChange({ ...value, student_email: e.target.value })
              }
              className={inputCls}
            />
          </div>
        </div>

        {/* Directeur de mémoire */}
        <div className="md:col-span-2 rounded-lg border border-slate-200 bg-slate-50/70 p-3.5">
          <div className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-700">
            Directeur de mémoire
          </div>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Nom (Times New Roman 14, gras)
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : Pr ESSI Marie-José"
                value={value.director_info?.name ?? ""}
                onChange={(e) => {
                  const nextDir = {
                    ...(value.director_info ?? emptySupervisor()),
                    name: e.target.value,
                  };
                  onChange({
                    ...value,
                    director_info: nextDir,
                    director: formatSupervisorSummary(nextDir),
                  });
                }}
                className={`${compactInputCls} font-semibold`}
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Grade (Times New Roman 12)
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : Professeure Titulaire"
                value={value.director_info?.rank ?? ""}
                onChange={(e) => {
                  const nextDir = {
                    ...(value.director_info ?? emptySupervisor()),
                    rank: e.target.value,
                  };
                  onChange({
                    ...value,
                    director_info: nextDir,
                    director: formatSupervisorSummary(nextDir),
                  });
                }}
                className={compactInputCls}
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Spécialité (Times New Roman 12)
              </label>
              <input
                type="text"
                disabled={readOnly}
                placeholder="Ex : Anthropologie médicale"
                value={value.director_info?.specialty ?? ""}
                onChange={(e) => {
                  const nextDir = {
                    ...(value.director_info ?? emptySupervisor()),
                    specialty: e.target.value,
                  };
                  onChange({
                    ...value,
                    director_info: nextDir,
                    director: formatSupervisorSummary(nextDir),
                  });
                }}
                className={compactInputCls}
              />
            </div>
          </div>
        </div>

        {/* Co-directeurs */}
        <div className="md:col-span-2 rounded-lg border border-slate-200 bg-slate-50/70 p-3.5">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-bold uppercase tracking-wide text-slate-700">
              Co-directeur(s) ({(value.co_directors_info ?? []).length})
            </span>
            <span className="text-[11px] text-slate-500">
              Nom en 14 pt gras · Grade et Spécialité en 12 pt
            </span>
          </div>

          {(value.co_directors_info ?? []).length > 0 ? (
            <div className="mb-3 space-y-2">
              {(value.co_directors_info ?? []).map((cd, idx) => (
                <div
                  key={idx}
                  className="flex items-start justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs"
                >
                  <div>
                    <div className="text-sm font-bold text-slate-900">
                      {cd.name || "—"}
                    </div>
                    {cd.rank && (
                      <div className="text-slate-700">{cd.rank}</div>
                    )}
                    {cd.specialty && (
                      <div className="text-slate-600">{cd.specialty}</div>
                    )}
                  </div>
                  {!readOnly && (
                    <button
                      type="button"
                      onClick={() => {
                        const nextList = (value.co_directors_info ?? []).filter(
                          (_, i) => i !== idx
                        );
                        onChange({
                          ...value,
                          co_directors_info: nextList,
                          co_directors: nextList
                            .map(formatSupervisorSummary)
                            .filter(Boolean),
                        });
                      }}
                      title="Retirer ce co-directeur"
                      className="rounded p-1 text-rose-600 hover:bg-rose-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          ) : (
            readOnly && (
              <div className="text-xs text-slate-500">
                Aucun co-directeur renseigné.
              </div>
            )
          )}

          {!readOnly && (
            <>
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
                <div>
                  <label className="block text-[11px] font-medium text-slate-600">
                    Nom du Co-directeur
                  </label>
                  <input
                    type="text"
                    placeholder="Ex : Dr FOTSING Claire"
                    value={coDirDraft.name}
                    onChange={(e) =>
                      setCoDirDraft({ ...coDirDraft, name: e.target.value })
                    }
                    className={`${compactInputCls} font-semibold`}
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-slate-600">
                    Grade
                  </label>
                  <input
                    type="text"
                    placeholder="Ex : Chargée de cours"
                    value={coDirDraft.rank}
                    onChange={(e) =>
                      setCoDirDraft({ ...coDirDraft, rank: e.target.value })
                    }
                    className={compactInputCls}
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-slate-600">
                    Spécialité
                  </label>
                  <input
                    type="text"
                    placeholder="Ex : Chirurgie Pédiatrique"
                    value={coDirDraft.specialty}
                    onChange={(e) =>
                      setCoDirDraft({
                        ...coDirDraft,
                        specialty: e.target.value,
                      })
                    }
                    className={compactInputCls}
                  />
                </div>
              </div>
              <div className="mt-2.5 flex justify-end">
                <button
                  type="button"
                  disabled={
                    !coDirDraft.name.trim() &&
                    !coDirDraft.rank.trim() &&
                    !coDirDraft.specialty.trim()
                  }
                  onClick={() => {
                    const entry: SupervisorInfo = {
                      name: coDirDraft.name.trim(),
                      rank: coDirDraft.rank.trim(),
                      specialty: coDirDraft.specialty.trim(),
                    };
                    if (!entry.name && !entry.rank && !entry.specialty) return;
                    const nextList = [
                      ...(value.co_directors_info ?? []),
                      entry,
                    ];
                    onChange({
                      ...value,
                      co_directors_info: nextList,
                      co_directors: nextList
                        .map(formatSupervisorSummary)
                        .filter(Boolean),
                    });
                    setCoDirDraft(emptySupervisor());
                  }}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-300 bg-white px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-40"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Ajouter un co-directeur
                </button>
              </div>
            </>
          )}
        </div>

        {/* Informations complémentaires repliables */}
        <div className="md:col-span-2 rounded-lg border border-slate-200 bg-slate-50/50">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex w-full items-center justify-between px-3.5 py-2.5 text-left text-xs font-semibold text-slate-700 hover:bg-slate-100/70"
          >
            <span>
              Informations complémentaires (période d&apos;étude, hôpitaux d&apos;accueil, comités d&apos;éthique)
            </span>
            {showAdvanced ? (
              <ChevronUp className="h-4 w-4 text-slate-500" />
            ) : (
              <ChevronDown className="h-4 w-4 text-slate-500" />
            )}
          </button>

          {showAdvanced && (
            <div className="grid grid-cols-1 gap-3 border-t border-slate-200 p-3.5 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className="block text-xs font-medium text-slate-600">
                  Période d&apos;étude prévue
                </label>
                <input
                  type="text"
                  disabled={readOnly}
                  placeholder="Ex : 1er Janvier 2026 au 31 Décembre 2026"
                  value={value.study_period_label ?? ""}
                  onChange={(e) =>
                    onChange({ ...value, study_period_label: e.target.value })
                  }
                  className={compactInputCls}
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-600">
                  Établissement(s) / Hôpital(aux) d&apos;accueil (1 par ligne)
                </label>
                <textarea
                  rows={3}
                  disabled={readOnly}
                  placeholder="Ex : Hôpital Central de Yaoundé"
                  value={(value.host_institutions ?? []).join("\n")}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      host_institutions: e.target.value
                        .split(/\r?\n/)
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                  className={compactInputCls}
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-600">
                  Comité(s) d&apos;éthique sollicité(s) (1 par ligne)
                </label>
                <textarea
                  rows={3}
                  disabled={readOnly}
                  placeholder="Comité Institutionnel d'Éthique et de la Recherche de la FMSB (CIER-FMSB)"
                  value={(value.ethics_committees ?? []).join("\n")}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      ethics_committees: e.target.value
                        .split(/\r?\n/)
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                  className={compactInputCls}
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
