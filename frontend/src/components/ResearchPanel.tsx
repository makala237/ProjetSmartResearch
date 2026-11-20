import { useState } from "react";
import {
  CheckCircle2,
  Database,
  ExternalLink,
  FileUp,
  Image as ImageIcon,
  Layers,
  Loader2,
  Search,
  Sparkles,
} from "lucide-react";
import { api, withAuthToken } from "../api/client";
import type { RunWithProgressFn } from "../hooks/usePipelineTracker";
import { useLanguage } from "../i18n/LanguageContext";
import type {
  ImportReport,
  ProtocolProject,
  ResearchReport,
  SegmentReport,
} from "../types/protocol";

interface ResearchPanelProps {
  project: ProtocolProject;
  onProjectUpdated: (updated: ProtocolProject) => void;
  onError: (msg: string) => void;
  runWithProgress?: RunWithProgressFn;
}

export function ResearchPanel({
  project,
  onProjectUpdated,
  onError,
  runWithProgress,
}: ResearchPanelProps) {
  const { lang, t } = useLanguage();
  const [tab, setTab] = useState<"search" | "import" | "refs">(
    project.entry_mode === "existing_protocol" ? "import" : "search"
  );
  const [customQuery, setCustomQuery] = useState("");
  const [busyResearch, setBusyResearch] = useState(false);
  const [busySegment, setBusySegment] = useState(false);
  const [busyImport, setBusyImport] = useState(false);

  const [researchReport, setResearchReport] = useState<ResearchReport | null>(null);
  const [segmentReport, setSegmentReport] = useState<SegmentReport | null>(null);
  const [importReport, setImportReport] = useState<ImportReport | null>(null);

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

  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusyImport(true);
    onError("");
    try {
      const res = await runTask(
        "import",
        "Import & analyse du document",
        "Document Import & Analysis",
        (taskId) => api.importProtocol(project.project_id, file, taskId)
      );
      onProjectUpdated(res.project);
      setImportReport(res.report);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyImport(false);
      e.target.value = "";
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
            onClick={() => setTab("import")}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              tab === "import"
                ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200"
                : "text-slate-600 hover:bg-slate-50"
            }`}
          >
            <FileUp className="h-3.5 w-3.5" />
            {t("m1.import_tab")}
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
                  Recherche documentaire :{" "}
                  {researchReport.added_references_count} nouvelles références ajoutées (total{" "}
                  {researchReport.total_references_count}) ·{" "}
                  {researchReport.added_images_count} figures Open Access
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
                ? "Automatically splits your document along the CAMES/FMSB canvas, extracts figures, and checks each section."
                : "L'outil découpe votre document selon le canevas FMSB et génère un diagnostic par section."}
            </span>
            <input
              type="file"
              accept=".docx,.pdf,.txt,.md"
              onChange={handleImportFile}
              disabled={busyImport}
              className="hidden"
            />
          </label>

          {importReport && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-950">
              <div className="font-semibold">
                {lang === "en" ? "Import completed" : "Import réussi"} ({importReport.filename}) :{" "}
                {importReport.mapped_sections_count}{" "}
                {lang === "en" ? "sections populated" : "sections FMSB renseignées"}
                {(importReport.extracted_images_count ?? 0) > 0
                  ? `, ${importReport.extracted_images_count} ${lang === "en" ? "extracted figure(s)" : "figure(s) extraite(s)"}`
                  : ""}
                .
              </div>
              <p className="mt-1 text-emerald-900">{importReport.global_summary}</p>
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
        </div>
      )}

      {tab === "refs" && (
        <div className="mt-4 max-h-64 space-y-3 overflow-y-auto pr-1 text-xs">
          {project.references.length === 0 ? (
            <p className="text-slate-500">
              {lang === "en"
                ? "No references collected yet. Run a literature search in Tab 1."
                : "Aucune référence collectée pour l'instant. Lancez une recherche dans l'onglet 1."}
            </p>
          ) : (
            project.references.map((r) => (
              <div
                key={r.ref_id}
                className="rounded-lg border border-slate-200 bg-slate-50 p-2.5"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="font-semibold text-slate-900">
                    [{r.order_index}] {r.vancouver_citation}
                  </span>
                  <span className="shrink-0 rounded bg-slate-200 px-1.5 py-0.5 text-[10px] uppercase text-slate-700">
                    {r.source_platform}
                  </span>
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
            ))
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
