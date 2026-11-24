import { useState } from "react";
import {
  BookOpen,
  CheckCircle2,
  Database,
  ExternalLink,
  FileText,
  FileUp,
  Image as ImageIcon,
  Layers,
  Loader2,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { api, withAuthToken } from "../api/client";
import type { RunWithProgressFn } from "../hooks/usePipelineTracker";
import { useLanguage } from "../i18n/LanguageContext";
import type {
  ImportMode,
  ImportReport,
  ProtocolProject,
  ResearchReport,
  ResearchScope,
  SegmentReport,
} from "../types/protocol";

interface ResearchPanelProps {
  project: ProtocolProject;
  onProjectUpdated: (updated: ProtocolProject) => void;
  onError: (msg: string) => void;
  runWithProgress?: RunWithProgressFn;
  onFeatureUsed?: (featureId: string) => void;
}

export function ResearchPanel({
  project,
  onProjectUpdated,
  onError,
  runWithProgress,
  onFeatureUsed,
}: ResearchPanelProps) {
  const { lang, t } = useLanguage();
  const [tab, setTab] = useState<"search" | "import" | "refs">(
    project.entry_mode === "existing_protocol" ? "import" : "search"
  );
  const [customQuery, setCustomQuery] = useState("");
  const [searchScope, setSearchScope] = useState<ResearchScope>("hybrid_africa");
  const [refFilter, setRefFilter] = useState<"all" | "africa">("all");
  const [busyResearch, setBusyResearch] = useState(false);
  const [busySegment, setBusySegment] = useState(false);
  const [busyImport, setBusyImport] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [importMode, setImportMode] = useState<ImportMode>("internal_source");
  const [busyDeleteSourceId, setBusyDeleteSourceId] = useState<string | null>(null);

  const [researchReport, setResearchReport] = useState<ResearchReport | null>(null);
  const [segmentReport, setSegmentReport] = useState<SegmentReport | null>(null);
  const [importReport, setImportReport] = useState<ImportReport | null>(null);

  const internalSources = project.internal_sources ?? [];
  const hasExistingDrafts = Object.values(project.sections).some(
    (sec) => Boolean(sec?.content_markdown?.trim())
  );

  const africanReferences = project.references.filter(
    (r) => r.regional_scope === "cameroun_fmsb" || r.regional_scope === "afrique_cames"
  );
  const displayedReferences =
    refFilter === "africa" ? africanReferences : project.references;

  async function runTask<T>(
    operation: string,
    titleFr: string,
    titleEn: string,
    fn: (taskId?: string) => Promise<T>
  ): Promise<T> {
    if (runWithProgress) {
      return runWithProgress(
        {
          operation,
          titleFr,
          titleEn,
          projectId: project.project_id,
        },
        (taskId) => fn(taskId)
      );
    }
    return fn(undefined);
  }

  async function handleRunResearch() {
    setBusyResearch(true);
    onError("");
    try {
      const res = await runTask(
        "research",
        "Recherche bibliographique",
        "Literature Search",
        (taskId) =>
          api.runResearch(
            project.project_id,
            {
              custom_query: customQuery.trim() || undefined,
              max_per_source: 4,
              include_web_grounding: true,
              search_scope: searchScope,
            },
            taskId
          )
      );
      onProjectUpdated(res.project);
      setResearchReport(res.report);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyResearch(false);
    }
  }

  async function handleSegment() {
    setBusySegment(true);
    onError("");
    try {
      const res = await runTask(
        "segment",
        "Segmentation des articles",
        "Article Segmentation",
        (taskId) => api.segmentArticles(project.project_id, 8, taskId)
      );
      onProjectUpdated(res.project);
      setSegmentReport(res.report);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusySegment(false);
    }
  }

  function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    onError("");
    setImportReport(null);
    setPendingFile(file);
    // Protect existing drafts by defaulting to internal_source when sections already contain text.
    setImportMode(hasExistingDrafts ? "internal_source" : "skeleton");
    e.target.value = "";
  }

  async function handleConfirmImport() {
    if (!pendingFile) return;
    const fileToImport = pendingFile;
    setBusyImport(true);
    onError("");
    try {
      const res = await runTask(
        "import",
        importMode === "internal_source"
          ? "Indexation en bibliothèque interne"
          : "Import & analyse du document",
        importMode === "internal_source"
          ? "Internal Library Indexing"
          : "Document Import & Analysis",
        (taskId) => api.importProtocol(project.project_id, fileToImport, importMode, taskId)
      );
      onProjectUpdated(res.project);
      setImportReport(res.report);
      setPendingFile(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyImport(false);
    }
  }

  async function handleDeleteInternalSource(sourceId: string) {
    setBusyDeleteSourceId(sourceId);
    onError("");
    try {
      const res = await api.deleteInternalSource(project.project_id, sourceId);
      onProjectUpdated(res.project);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyDeleteSourceId(null);
    }
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setTab("search")}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              tab === "search"
                ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
                : "text-slate-600 hover:bg-slate-50"
            }`}
          >
            <Search className="h-3.5 w-3.5" />
            {t("m1.research_tab")}
          </button>
          <button
            type="button"
            onClick={() => {
              setTab("import");
              onFeatureUsed?.("import_draft");
            }}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              tab === "import"
                ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200"
                : "text-slate-600 hover:bg-slate-50"
            }`}
          >
            <FileUp className="h-3.5 w-3.5" />
            {t("m1.import_tab")}
            {internalSources.length > 0 && (
              <span className="rounded-full bg-emerald-600 px-1.5 py-0.5 text-[10px] font-bold text-white">
                {internalSources.length}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => setTab("refs")}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              tab === "refs"
                ? "bg-slate-100 text-slate-900 ring-1 ring-slate-300"
                : "text-slate-600 hover:bg-slate-50"
            }`}
          >
            <Database className="h-3.5 w-3.5" />
            {t("m1.refs_tab")} ({project.references.length} / {project.article_segments.length})
          </button>
        </div>
      </div>

      {tab === "search" && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-col gap-1.5 rounded-lg border border-slate-200 bg-slate-50/70 p-2.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-[11px] font-semibold text-slate-600">
                {lang === "en" ? "Scope:" : "Portée :"}
              </span>
              {(
                [
                  {
                    value: "hybrid_africa" as ResearchScope,
                    labelFr: "Hybride Afrique & Monde (FMSB / CAMES)",
                    labelEn: "Hybrid Africa & Global (FMSB / CAMES)",
                  },
                  {
                    value: "africa_only" as ResearchScope,
                    labelFr: "Afrique & CAMES uniquement",
                    labelEn: "Africa & CAMES Only",
                  },
                  {
                    value: "global" as ResearchScope,
                    labelFr: "Mondiale",
                    labelEn: "Global",
                  },
                ] as const
              ).map((opt) => {
                const active = searchScope === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setSearchScope(opt.value)}
                    className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition ${
                      active
                        ? opt.value === "global"
                          ? "bg-indigo-600 text-white shadow-sm"
                          : "bg-emerald-600 text-white shadow-sm"
                        : "bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-100"
                    }`}
                  >
                    {lang === "en" ? opt.labelEn : opt.labelFr}
                  </button>
                );
              })}
            </div>
            {(searchScope === "hybrid_africa" || searchScope === "africa_only") && (
              <p className="text-[11px] text-emerald-800">
                {lang === "en"
                  ? "Targeted African sources: HSD (FMSB Yaoundé I), PAMJ, AJOL, WHO AFRO, Mali/Dakar Médical, OpenAlex Africa, CAMES"
                  : "Sources africaines ciblées : HSD (FMSB Yaoundé I), PAMJ, AJOL, OMS AFRO, Mali/Dakar Médical, OpenAlex Afrique, CAMES"}
              </p>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <input
              type="text"
              value={customQuery}
              onChange={(e) => setCustomQuery(e.target.value)}
              placeholder={
                lang === "en"
                  ? `Topic: "${project.research_idea || project.name}" (or enter custom query)...`
                  : `Sujet utilisé : "${project.research_idea || project.name}" (ou précisez une requête)...`
              }
              className="min-w-[260px] flex-1 rounded-lg border border-slate-300 px-3 py-2 text-xs focus:border-indigo-500 focus:outline-none"
            />
            <button
              type="button"
              disabled={busyResearch}
              onClick={handleRunResearch}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {busyResearch ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Search className="h-3.5 w-3.5" />
              )}
              {busyResearch
                ? lang === "en"
                  ? "Searching PubMed / Europe PMC / Web..."
                  : "Recherche PubMed / Europe PMC / Web..."
                : t("m1.run_research")}
            </button>
            <button
              type="button"
              disabled={busySegment || project.references.length === 0}
              onClick={handleSegment}
              className="inline-flex items-center gap-1.5 rounded-lg bg-amber-600 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
            >
              {busySegment ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Layers className="h-3.5 w-3.5" />
              )}
              {busySegment
                ? lang === "en"
                  ? "Segmenting articles..."
                  : "Segmentation des articles en cours..."
                : `${t("m1.run_segment")} (${project.references.length})`}
            </button>
          </div>

          {researchReport && (
            <div className="rounded-lg border border-indigo-100 bg-indigo-50/50 p-3 text-xs text-slate-800">
              <div className="flex flex-wrap items-center justify-between gap-2 font-semibold text-indigo-900">
                <span>
                  <Sparkles className="mr-1 inline h-3.5 w-3.5" />
                  {lang === "en" ? "Literature search:" : "Recherche documentaire :"}{" "}
                  {researchReport.added_references_count}{" "}
                  {lang === "en" ? "new reference(s) added" : "nouvelles références ajoutées"}{" "}
                  {(researchReport.african_references_count ?? 0) > 0 && (
                    <span className="ml-1 inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800">
                      {lang === "en"
                        ? `incl. ${researchReport.african_references_count} Africa / CAMES`
                        : `dont ${researchReport.african_references_count} Afrique / CAMES`}
                    </span>
                  )}{" "}
                  ({lang === "en" ? "total" : "total"} {researchReport.total_references_count}) ·{" "}
                  {researchReport.added_images_count} {lang === "en" ? "Open Access figures" : "figures Open Access"}
                </span>
              </div>
              <div className="mt-1.5 text-slate-600">
                <strong>Requêtes :</strong>{" "}
                {researchReport.plan.pubmed_queries.join(" | ")}
              </div>
              {researchReport.web_summary && (
                <p className="mt-2 line-clamp-3 rounded bg-white/80 p-2 text-slate-700">
                  <strong>Synthèse Web Grounding :</strong>{" "}
                  {researchReport.web_summary}
                </p>
              )}
              {researchReport.source_errors.length > 0 && (
                <div className="mt-1.5 text-amber-800">
                  Sources partielles : {researchReport.source_errors.join(" ; ")}
                </div>
              )}
            </div>
          )}

          {segmentReport && (
            <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-xs text-amber-950">
              <CheckCircle2 className="mr-1 inline h-3.5 w-3.5 text-emerald-700" />
              <strong>{segmentReport.processed_articles}</strong> articles analysés →{" "}
              <strong>{segmentReport.added_segments}</strong> segments classés dans les sections FMSB (total :{" "}
              {segmentReport.total_segments}).
            </div>
          )}
        </div>
      )}

          {tab === "import" && (
        <div className="mt-4 space-y-3">
          {!pendingFile ? (
            <label className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-center hover:border-emerald-500">
              {busyImport ? (
                <Loader2 className="h-6 w-6 animate-spin text-emerald-600" />
              ) : (
                <FileUp className="h-6 w-6 text-emerald-600" />
              )}
              <span className="mt-2 text-xs font-semibold text-slate-800">
                {busyImport
                  ? lang === "en"
                    ? "Analyzing and mapping sections..."
                    : "Analyse et mapping vers les étapes FMSB en cours..."
                  : t("m1.import_btn")}
              </span>
              <span className="mt-0.5 text-[11px] text-slate-500">
                {lang === "en"
                  ? "Select a .docx, .pdf, .txt or .md document, then choose whether it serves as the protocol skeleton or as an internal information source."
                  : "Sélectionnez un document (.docx, .pdf, .txt, .md), puis choisissez s'il sert de squelette du protocole ou de source d'information interne."}
              </span>
              <input
                type="file"
                accept=".docx,.pdf,.txt,.md"
                onChange={handleImportFile}
                disabled={busyImport}
                className="hidden"
              />
            </label>
          ) : (
            <div className="rounded-xl border border-indigo-200 bg-indigo-50/50 p-3.5 text-xs text-slate-900">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 shrink-0 text-indigo-700" />
                  <div>
                    <div className="font-bold text-slate-900">
                      {lang === "en"
                        ? "How should this document be integrated?"
                        : "Comment intégrer ce document ?"}
                    </div>
                    <div className="text-[11px] text-slate-600">
                      {pendingFile.name} ({Math.max(1, Math.round(pendingFile.size / 1024))} Ko)
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setPendingFile(null)}
                  disabled={busyImport}
                  className="rounded p-1 text-slate-500 hover:bg-white hover:text-slate-800"
                  title={lang === "en" ? "Cancel selection" : "Annuler la sélection"}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  disabled={busyImport}
                  onClick={() => setImportMode("internal_source")}
                  className={`flex flex-col items-start rounded-lg border p-3 text-left transition ${
                    importMode === "internal_source"
                      ? "border-emerald-600 bg-emerald-50/90 ring-2 ring-emerald-500/20"
                      : "border-slate-200 bg-white hover:border-slate-300"
                  }`}
                >
                  <div className="flex w-full items-center justify-between gap-2">
                    <span className="inline-flex items-center gap-1.5 font-bold text-emerald-900">
                      <BookOpen className="h-3.5 w-3.5 text-emerald-700" />
                      {lang === "en"
                        ? "Internal information source"
                        : "Source d'information interne"}
                    </span>
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800">
                      {lang === "en" ? "Safe / Secondary memory" : "Bibliothèque secondaire"}
                    </span>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-slate-600">
                    {lang === "en"
                      ? "Preserves existing drafted sections. Segments text, tables & figures into each section's internal library and checks cover/header for Vancouver citability."
                      : "Préserve vos rédactions actuelles. Segmente texte, tableaux et figures dans la bibliothèque interne de chaque section et analyse la page de garde pour une citation Vancouver si citable."}
                  </p>
                </button>

                <button
                  type="button"
                  disabled={busyImport}
                  onClick={() => setImportMode("skeleton")}
                  className={`flex flex-col items-start rounded-lg border p-3 text-left transition ${
                    importMode === "skeleton"
                      ? "border-indigo-600 bg-white ring-2 ring-indigo-500/20"
                      : "border-slate-200 bg-white hover:border-slate-300"
                  }`}
                >
                  <div className="flex w-full items-center justify-between gap-2">
                    <span className="inline-flex items-center gap-1.5 font-bold text-slate-900">
                      <Layers className="h-3.5 w-3.5 text-indigo-700" />
                      {lang === "en"
                        ? "Protocol skeleton"
                        : "Squelette du protocole"}
                    </span>
                    {hasExistingDrafts && (
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                        {lang === "en" ? "Overwrites sections" : "Remplace les sections"}
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-slate-600">
                    {lang === "en"
                      ? "Populates the FMSB canvas sections directly as the primary protocol draft skeleton."
                      : "Utilise ce document comme base principale et remplit directement les sections du canevas FMSB."}
                  </p>
                </button>
              </div>

              <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                <button
                  type="button"
                  disabled={busyImport}
                  onClick={() => setPendingFile(null)}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {lang === "en" ? "Cancel" : "Annuler"}
                </button>
                <button
                  type="button"
                  disabled={busyImport}
                  onClick={handleConfirmImport}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-xs font-semibold text-white shadow-sm disabled:opacity-50 ${
                    importMode === "internal_source"
                      ? "bg-emerald-600 hover:bg-emerald-700"
                      : "bg-indigo-600 hover:bg-indigo-700"
                  }`}
                >
                  {busyImport ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <CheckCircle2 className="h-3.5 w-3.5" />
                  )}
                  {busyImport
                    ? lang === "en"
                      ? "Analyzing..."
                      : "Analyse en cours..."
                    : importMode === "internal_source"
                      ? lang === "en"
                        ? "Add to internal library"
                        : "Ajouter à la bibliothèque interne"
                      : lang === "en"
                        ? "Import as skeleton"
                        : "Importer comme squelette"}
                </button>
              </div>
            </div>
          )}

          {importReport && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-950">
              <div className="font-semibold">
                {importReport.import_mode === "internal_source" ? (
                  <>
                    {lang === "en"
                      ? "Added to internal library"
                      : "Ajouté à la bibliothèque interne"}{" "}
                    ({importReport.filename}) :{" "}
                    {importReport.internal_entries_added ?? importReport.mapped_sections_count}{" "}
                    {lang === "en"
                      ? "section segment(s) indexed"
                      : "segment(s) indexé(s) par section"}
                    {(importReport.extracted_images_count ?? 0) > 0
                      ? `, ${importReport.extracted_images_count} ${lang === "en" ? "extracted figure(s)" : "figure(s) extraite(s)"}`
                      : ""}
                    .
                  </>
                ) : (
                  <>
                    {lang === "en" ? "Import completed" : "Import réussi"} ({importReport.filename}) :{" "}
                    {importReport.mapped_sections_count}{" "}
                    {lang === "en" ? "sections populated" : "sections FMSB renseignées"}
                    {(importReport.extracted_images_count ?? 0) > 0
                      ? `, ${importReport.extracted_images_count} ${lang === "en" ? "extracted figure(s)" : "figure(s) extraite(s)"}`
                      : ""}
                    .
                  </>
                )}
              </div>
              {importReport.import_mode === "internal_source" && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
                  <span
                    className={`rounded-full px-2 py-0.5 font-semibold ${
                      importReport.citable
                        ? "bg-emerald-200/80 text-emerald-950"
                        : "bg-amber-100 text-amber-900"
                    }`}
                  >
                    {importReport.citable
                      ? lang === "en"
                        ? "Citable source (Vancouver)"
                        : "Source citable (référence Vancouver créée)"
                      : lang === "en"
                        ? "Unreferenced internal draft"
                        : "Brouillon interne exploitable (non citable)"}
                  </span>
                  {importReport.citable && importReport.vancouver_citation && (
                    <span className="italic text-emerald-900">
                      {importReport.vancouver_citation}
                    </span>
                  )}
                </div>
              )}
              <p className="mt-1 text-emerald-900">{importReport.global_summary}</p>
              {importReport.crf_detected && (
                <div className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-emerald-300 bg-white/80 px-2.5 py-1.5 text-[11px] text-emerald-950">
                  <span className="rounded bg-emerald-100 px-1.5 py-0.5 font-bold text-emerald-900">
                    {lang === "en" ? "Data Collection Form isolated" : "Fiche de collecte isolée (début → fin)"}
                  </span>
                  {typeof importReport.crf_fields_count === "number" && importReport.crf_fields_count > 0 && (
                    <span className="font-semibold text-indigo-900">
                      • {importReport.crf_fields_count}{" "}
                      {lang === "en" ? "fields compiled into Mask" : "champ(s) compilé(s) dans le Masque de saisie"}
                    </span>
                  )}
                  {importReport.crf_detection?.detected_title && (
                    <span className="truncate text-slate-600" title={importReport.crf_detection.detected_title}>
                      ({importReport.crf_detection.detected_title})
                    </span>
                  )}
                </div>
              )}
              {importReport.detected_sub_annexes && importReport.detected_sub_annexes.length > 0 && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-emerald-900">
                  <span className="rounded bg-indigo-100 px-1.5 py-0.5 font-semibold text-indigo-900">
                    {lang === "en"
                      ? `${importReport.detected_sub_annexes.length} sub-appendix(es) isolated automatically`
                      : `${importReport.detected_sub_annexes.length} sous-annexe(s) isolée(s) automatiquement`}
                  </span>
                  <span className="text-slate-600">
                    ({importReport.detected_sub_annexes.join(", ")})
                  </span>
                </div>
              )}
              {importReport.extracted_images && importReport.extracted_images.length > 0 && (
                <div className="mt-2.5 border-t border-emerald-200/80 pt-2">
                  <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-emerald-900">
                    <ImageIcon className="h-3.5 w-3.5" />
                    {lang === "en"
                      ? `Automatically extracted figures (${importReport.extracted_images.length})`
                      : `Figures extraites automatiquement (${importReport.extracted_images.length})`}
                  </div>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {importReport.extracted_images.map((im) => (
                      <div
                        key={im.asset_id}
                        className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-white p-1.5"
                      >
                        <img
                          src={withAuthToken(im.source_url)}
                          alt={im.title}
                          className="h-12 w-12 shrink-0 rounded border border-slate-200 bg-slate-50 object-contain p-0.5"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate font-semibold text-slate-900" title={im.title}>
                            {im.title}
                          </div>
                          <div className="truncate text-[10px] text-emerald-700">
                            Section : {im.target_section_id}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {internalSources.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 text-xs">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="inline-flex items-center gap-1.5 font-bold text-slate-800">
                  <BookOpen className="h-3.5 w-3.5 text-emerald-700" />
                  {lang === "en"
                    ? `Internal Library — Secondary Memory (${internalSources.length})`
                    : `Bibliothèque interne — Mémoire secondaire (${internalSources.length})`}
                </span>
              </div>
              <div className="space-y-2">
                {internalSources.map((src) => (
                  <div
                    key={src.source_id}
                    className="flex items-start justify-between gap-2 rounded-lg border border-slate-200 bg-white p-2.5"
                  >
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="truncate font-semibold text-slate-900" title={src.filename}>
                          {src.filename}
                        </span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                            src.citable
                              ? "bg-emerald-100 text-emerald-800"
                              : "bg-slate-100 text-slate-700"
                          }`}
                        >
                          {src.citable
                            ? lang === "en"
                              ? `Citable (${src.source_kind})`
                              : `Citable — ${src.source_kind}`
                            : lang === "en"
                              ? "Unreferenced draft"
                              : "Brouillon interne non référencé"}
                        </span>
                        <span className="text-[10px] text-slate-500">
                          {src.mapped_sections.length}{" "}
                          {lang === "en" ? "section(s)" : "section(s)"}
                          {src.extracted_images_count > 0
                            ? ` • ${src.extracted_images_count} ${lang === "en" ? "figure(s)" : "figure(s)"}`
                            : ""}
                        </span>
                      </div>
                      {src.citable && src.vancouver_citation && (
                        <div className="text-[11px] italic text-slate-600">
                          {src.vancouver_citation}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      disabled={busyDeleteSourceId === src.source_id}
                      onClick={() => handleDeleteInternalSource(src.source_id)}
                      className="shrink-0 rounded p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-700 disabled:opacity-50"
                      title={
                        lang === "en"
                          ? "Remove from internal library"
                          : "Retirer de la bibliothèque interne"
                      }
                    >
                      {busyDeleteSourceId === src.source_id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "refs" && (
        <div className="mt-4 max-h-64 space-y-3 overflow-y-auto pr-1 text-xs">
          {project.references.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-100 pb-2">
              <button
                type="button"
                onClick={() => setRefFilter("all")}
                className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold transition ${
                  refFilter === "all"
                    ? "bg-slate-800 text-white"
                    : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                }`}
              >
                {lang === "en"
                  ? `All (${project.references.length})`
                  : `Toutes (${project.references.length})`}
              </button>
              <button
                type="button"
                onClick={() => setRefFilter("africa")}
                className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold transition ${
                  refFilter === "africa"
                    ? "bg-emerald-700 text-white"
                    : "bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200 hover:bg-emerald-100"
                }`}
              >
                {lang === "en"
                  ? `Africa & CAMES (${africanReferences.length})`
                  : `Afrique & CAMES (${africanReferences.length})`}
              </button>
            </div>
          )}

          {displayedReferences.length === 0 ? (
            <p className="text-slate-500">
              {project.references.length === 0
                ? lang === "en"
                  ? "No references collected yet. Run a literature search in Tab 1."
                  : "Aucune référence collectée pour l'instant. Lancez une recherche dans l'onglet 1."
                : lang === "en"
                  ? "No Africa / CAMES references found in current bibliography yet."
                  : "Aucune référence Afrique / CAMES identifiée dans la bibliographie actuelle."}
            </p>
          ) : (
            displayedReferences.map((r) => {
              const isAfrica =
                r.regional_scope === "cameroun_fmsb" ||
                r.regional_scope === "afrique_cames";
              const africaLabel =
                r.african_source_label ||
                (r.regional_scope === "cameroun_fmsb"
                  ? "Cameroun / FMSB"
                  : "Afrique / CAMES");
              return (
                <div
                  key={r.ref_id}
                  className="rounded-lg border border-slate-200 bg-slate-50 p-2.5"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-semibold text-slate-900">
                      [{r.order_index}] {r.vancouver_citation}
                    </span>
                    <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                      {isAfrica && (
                        <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-800 ring-1 ring-emerald-200">
                          {africaLabel}
                        </span>
                      )}
                      <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px] uppercase text-slate-700">
                        {r.source_platform}
                      </span>
                    </div>
                  </div>
                  {r.url && (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 inline-flex items-center gap-1 text-[11px] text-indigo-600 hover:underline"
                    >
                      {lang === "en" ? "Open source" : "Consulter la source"} <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
              );
            })
          )}

          {project.images.length > 0 && (
            <div className="pt-2">
              <div className="mb-1.5 flex items-center gap-1 font-semibold text-slate-700">
                <ImageIcon className="h-3.5 w-3.5" />
                {lang === "en"
                  ? `Extracted & Open Access Figures (${project.images.length})`
                  : `Figures Open Access & extraites (${project.images.length})`}
              </div>
              <div className="space-y-2">
                {project.images.map((img) => (
                  <div
                    key={img.asset_id}
                    className="flex items-start justify-between gap-2 rounded border border-slate-200 bg-white p-2"
                  >
                    <div>
                      <div className="font-semibold text-slate-800">{img.title}</div>
                      <div className="line-clamp-2 text-[11px] text-slate-600">
                        {img.caption}
                      </div>
                      <div className="text-[10px] text-slate-400">
                        {lang === "en" ? "Target section:" : "Section cible :"} {img.target_section_id} · {img.license_info}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={async () => {
                        const updated = await api.toggleImage(
                          project.project_id,
                          img.asset_id,
                          { approved: !img.approved }
                        );
                        onProjectUpdated(updated);
                      }}
                      className={`shrink-0 rounded px-2 py-1 text-[11px] font-semibold ${
                        img.approved
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {img.approved
                        ? lang === "en" ? "Approved" : "Approuvée"
                        : lang === "en" ? "Approve" : "Approuver"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
