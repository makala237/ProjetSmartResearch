import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Feather,
  FileCheck2,
  FileText,
  RefreshCw,
  ShieldCheck,
  Sliders,
  TableProperties,
  Trash2,
  XCircle,
} from "lucide-react";
import { api, authHeaders, withAuthToken } from "../api/client";
import { useLanguage } from "../i18n/LanguageContext";
import type {
  CanvasSectionSpec,
  FormattingRules,
  GlobalAuditReport,
  HumanizeProtocolReport,
  ProtocolProject,
  TimelineGrid,
} from "../types/protocol";
import { StructuredTablesEditor } from "./StructuredTablesEditor";
import { TimelineGridEditor } from "./TimelineGridEditor";
import { getFormattedCoverHeader } from "../utils/coverHeader";

interface Module2Props {
  project: ProtocolProject;
  canvas: CanvasSectionSpec[];
  onProjectUpdated: (updated: ProtocolProject) => void;
  onSaveFormatting: (formatting: FormattingRules) => Promise<void>;
  onError: (msg: string) => void;
  saving: boolean;
}

export function Module2Editor({
  project,
  canvas,
  onProjectUpdated,
  onSaveFormatting,
  onError,
  saving,
}: Module2Props) {
  const { lang, t } = useLanguage();
  const [fmt, setFmt] = useState<FormattingRules>(project.formatting);
  const [viewMode, setViewMode] = useState<"preview" | "tables">("preview");
  const [exportingDocx, setExportingDocx] = useState(false);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [generatingToc, setGeneratingToc] = useState(false);
  const [syncingStructured, setSyncingStructured] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [paginationSummary, setPaginationSummary] = useState<{
    model_used: string;
    decision_notes: string;
    total_roman_pages: number;
    total_arabic_pages: number;
    usable_width_cm: number;
    usable_height_cm: number;
    figures_checked: number;
  } | null>(null);
  const [auditingGlobal, setAuditingGlobal] = useState(false);
  const [globalAudit, setGlobalAudit] = useState<GlobalAuditReport | null>(null);
  const [humanizingAll, setHumanizingAll] = useState(false);
  const [humanizeReport, setHumanizeReport] =
    useState<HumanizeProtocolReport | null>(null);
  const [timelineDraft, setTimelineDraft] = useState<TimelineGrid>(
    project.timeline_grid
  );
  const [savingTimeline, setSavingTimeline] = useState(false);

  const syncedTransportProjectRef = useRef<string | null>(null);
  const fmtDirtyRef = useRef(false);
  const prevFmtProjectIdRef = useRef(project.project_id);

  const accentColor = fmt.border_color || "#6C2826";
  const hfRuleColor = (fmt.header_footer_rule_color || "#CBD5E1").trim();
  const hfRuleWidthPt = fmt.header_footer_rule_width_pt ?? 0.5;
  const isFormattingDirty =
    JSON.stringify(fmt) !== JSON.stringify(project.formatting);
  fmtDirtyRef.current = isFormattingDirty;

  useEffect(() => {
    if (
      prevFmtProjectIdRef.current !== project.project_id ||
      !fmtDirtyRef.current
    ) {
      prevFmtProjectIdRef.current = project.project_id;
      setFmt(project.formatting);
      fmtDirtyRef.current = false;
    }
  }, [project.project_id, project.formatting]);

  useEffect(() => {
    setTimelineDraft(project.timeline_grid);
  }, [project.timeline_grid]);

  useEffect(() => {
    if (syncedTransportProjectRef.current === project.project_id) {
      return;
    }
    syncedTransportProjectRef.current = project.project_id;
    api
      .syncMediaTransport(project.project_id)
      .then((res) => {
        onProjectUpdated(res.project);
      })
      .catch(() => {
        // Non-blocking background transport sync on Module 2 open
      });
  }, [project.project_id]);

  const handleGenerateToc = async () => {
    setGeneratingToc(true);
    try {
      const res = await api.generateToc(project.project_id);
      onProjectUpdated(res.project);
      if (res.pagination_summary) {
        setPaginationSummary(res.pagination_summary);
      }
      const pagesTxt = res.pagination_summary
        ? ` — ${res.pagination_summary.total_roman_pages} p. romaines + ${res.pagination_summary.total_arabic_pages} p. A4`
        : "";
      const listCounts = [
        res.abbreviations_count != null ? `${res.abbreviations_count} abréviations` : "",
        res.tables_count != null ? `${res.tables_count} tableaux` : "",
        res.figures_count != null ? `${res.figures_count} figures` : "",
      ]
        .filter(Boolean)
        .join(", ");
      setSyncMessage(
        `TDM & Listes FMSB unifiées sans doublon (${res.entries_count} rubriques TDM${listCounts ? ` ; ${listCounts}` : ""}${pagesTxt}).`
      );
    } catch (e: unknown) {
      onError(
        e instanceof Error
          ? e.message
          : "Échec de la génération automatique de la table des matières."
      );
    } finally {
      setGeneratingToc(false);
    }
  };

  const persistPendingFormattingBeforeExport = async () => {
    if (!isFormattingDirty) return;
    try {
      await onSaveFormatting(fmt);
      fmtDirtyRef.current = false;
    } catch {
      // Non-blocking fallback when Module 2 is read-only; formatting is still sent in export body
    }
  };

  const handleApplyFormatting = async () => {
    await onSaveFormatting(fmt);
    fmtDirtyRef.current = false;
  };

  const handleDownloadDocx = async () => {
    setExportingDocx(true);
    try {
      await persistPendingFormattingBeforeExport();
      const res = await fetch(api.exportDocxUrl(project.project_id), {
        method: "POST",
        headers: {
          ...authHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ formatting: fmt }),
      });
      if (!res.ok) {
        const errTxt = await res.text();
        throw new Error(errTxt || `Erreur HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Protocole_${project.project_id}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : "Échec de l'export DOCX");
    } finally {
      setExportingDocx(false);
    }
  };

  const handleDownloadPdf = async () => {
    setExportingPdf(true);
    try {
      await persistPendingFormattingBeforeExport();
      const res = await fetch(api.exportPdfUrl(project.project_id), {
        method: "POST",
        headers: {
          ...authHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ formatting: fmt }),
      });
      if (!res.ok) {
        const errTxt = await res.text();
        throw new Error(errTxt || `Erreur HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Protocole_${project.project_id}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : "Échec de l'export PDF");
    } finally {
      setExportingPdf(false);
    }
  };

  const handleSyncStructured = async () => {
    setSyncingStructured(true);
    setSyncMessage(null);
    try {
      const res = await api.syncStructured(project.project_id);
      onProjectUpdated(res.project);
      const r = res.report;
      setSyncMessage(
        `Synchronisation terminée : +${r.added_abbreviations} abréviation(s), +${r.added_variables} variable(s), +${r.added_literature_rows} étude(s), +${r.added_budget_items} ligne(s) budgétaire(s).`
      );
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : "Échec de synchronisation");
    } finally {
      setSyncingStructured(false);
    }
  };

  const handleGlobalAudit = async () => {
    setAuditingGlobal(true);
    try {
      const report = await api.runGlobalAudit(project.project_id);
      setGlobalAudit(report);
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : "Échec de l'audit global");
    } finally {
      setAuditingGlobal(false);
    }
  };

  const handleHumanizeAll = async () => {
    setHumanizingAll(true);
    onError("");
    try {
      const res = await api.humanizeProtocol(project.project_id, true);
      onProjectUpdated(res.project);
      setHumanizeReport(res.report);
    } catch (e: unknown) {
      onError(
        e instanceof Error
          ? e.message
          : "Échec de l'humanisation académique du protocole."
      );
    } finally {
      setHumanizingAll(false);
    }
  };

  const handleSaveTimelineGrid = async (nextGrid: TimelineGrid) => {
    setSavingTimeline(true);
    onError("");
    try {
      const updated = await api.updateStructuredData(project.project_id, {
        timeline_grid: nextGrid,
      });
      onProjectUpdated(updated);
    } catch (err: unknown) {
      onError(
        err instanceof Error
          ? err.message
          : "Échec de la mise à jour du chronogramme des activités."
      );
    } finally {
      setSavingTimeline(false);
    }
  };

  const validatedSections = canvas.filter(
    (s) =>
      s.id !== "cover_page" &&
      project.sections[s.id]?.status === "validated"
  );

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      {/* Deterministic Formatting Controls */}
      <aside className="space-y-4 lg:col-span-4">
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-3 flex items-center gap-2">
            <Sliders className="h-4 w-4 text-indigo-600" />
            <h2 className="text-sm font-bold uppercase tracking-wide text-slate-800">
              {t("m2.formatting_tab")} · {t("m2.title")}
            </h2>
          </div>
          <p className="mb-4 text-xs text-slate-500">
            {lang === "en"
              ? "These deterministic parameters directly drive Word/PDF compilation. The AI never alters layout rules."
              : "Ces paramètres pilotent directement l'algorithme de compilation Word/PDF. Le LLM ne modifie jamais le formatage directement."}
          </p>

          <div className="space-y-3 text-sm">
            <div>
              <label className="block text-xs font-medium text-slate-600">
                {lang === "en" ? "Primary font" : "Police principale"}
              </label>
              <select
                value={fmt.font_family}
                onChange={(e) => setFmt({ ...fmt, font_family: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="Times New Roman">Times New Roman (FMSB)</option>
                <option value="Calibri">Calibri</option>
                <option value="Arial">Arial</option>
                <option value="Cambria">Cambria</option>
              </select>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600">
                  {lang === "en" ? "Body size (pt)" : "Taille corps (pt)"}
                </label>
                <input
                  type="number"
                  min={10}
                  max={14}
                  value={fmt.body_font_size_pt}
                  onChange={(e) =>
                    setFmt({ ...fmt, body_font_size_pt: Number(e.target.value) })
                  }
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600">
                  {lang === "en" ? "Line spacing" : "Interligne"}
                </label>
                <select
                  value={fmt.line_spacing}
                  onChange={(e) =>
                    setFmt({ ...fmt, line_spacing: Number(e.target.value) })
                  }
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                >
                  <option value={1.15}>1.15</option>
                  <option value={1.5}>1.5 (Standard)</option>
                  <option value={2}>2.0</option>
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600">
                  {lang === "en" ? "Alignment" : "Alignement"}
                </label>
                <select
                  value={fmt.text_align}
                  onChange={(e) =>
                    setFmt({
                      ...fmt,
                      text_align: e.target.value as FormattingRules["text_align"],
                    })
                  }
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                >
                  <option value="justify">{lang === "en" ? "Justified" : "Justifié"}</option>
                  <option value="left">{lang === "en" ? "Align left" : "Aligné à gauche"}</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600">
                  {lang === "en" ? "A4 margins (cm)" : "Marges A4 (cm)"}
                </label>
                <input
                  type="number"
                  step={0.5}
                  min={1.5}
                  max={3.5}
                  value={fmt.margin_cm}
                  onChange={(e) =>
                    setFmt({ ...fmt, margin_cm: Number(e.target.value) })
                  }
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div className="space-y-2 pt-2 text-xs text-slate-700">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={fmt.auto_toc}
                    onChange={(e) => setFmt({ ...fmt, auto_toc: e.target.checked })}
                  />
                  {lang === "en" ? "Automatic Table of Contents" : "Table des matières automatique"}
                </label>
                <button
                  type="button"
                  disabled={generatingToc}
                  onClick={handleGenerateToc}
                  className="inline-flex items-center gap-1 rounded-md border border-[#6C2826]/40 bg-[#6C2826]/5 px-2 py-1 text-[11px] font-semibold text-[#6C2826] hover:bg-[#6C2826]/10 disabled:opacity-50"
                >
                  <RefreshCw className={`h-3 w-3 ${generatingToc ? "animate-spin" : ""}`} />
                  {generatingToc
                    ? lang === "en"
                      ? "Updating..."
                      : "Actualisation..."
                    : lang === "en"
                    ? "Refresh TOC & Lists"
                    : "Actualiser TDM & Listes FMSB"}
                </button>
              </div>

              {fmt.auto_toc && (
                <div className="ml-5 flex flex-wrap items-center gap-3 rounded bg-slate-50 p-1.5 text-[11px] text-slate-700 border border-slate-200">
                  <span className="font-semibold text-slate-800">
                    {lang === "en" ? "TOC Depth:" : "Format TDM :"}
                  </span>
                  <label className="inline-flex items-center gap-1 cursor-pointer">
                    <input
                      type="radio"
                      name="toc_depth"
                      checked={(fmt.toc_depth ?? 2) === 2}
                      onChange={() => setFmt({ ...fmt, toc_depth: 2 })}
                    />
                    <span className="font-medium text-[#6C2826]">
                      {lang === "en"
                        ? "Executive CAMES (1 page — Levels 1 & 2)"
                        : "Sommaire CAMES (1 page — Niveaux 1 & 2)"}
                    </span>
                  </label>
                  <label className="inline-flex items-center gap-1 cursor-pointer">
                    <input
                      type="radio"
                      name="toc_depth"
                      checked={(fmt.toc_depth ?? 2) === 3}
                      onChange={() => setFmt({ ...fmt, toc_depth: 3 })}
                    />
                    <span>{lang === "en" ? "Detailed (Level 3)" : "Analytique (Niveau 3)"}</span>
                  </label>
                </div>
              )}
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={fmt.auto_abbreviations_table}
                  onChange={(e) =>
                    setFmt({
                      ...fmt,
                      auto_abbreviations_table: e.target.checked,
                    })
                  }
                />
                {lang === "en" ? "Abbreviations & acronyms list" : "Liste des abréviations & sigles"}
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={fmt.auto_list_of_tables}
                  onChange={(e) =>
                    setFmt({ ...fmt, auto_list_of_tables: e.target.checked })
                  }
                />
                {lang === "en" ? "Automatic List of Tables" : "Liste des tableaux automatique"}
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={fmt.auto_list_of_figures}
                  onChange={(e) =>
                    setFmt({ ...fmt, auto_list_of_figures: e.target.checked })
                  }
                />
                {lang === "en" ? "Automatic List of Figures" : "Liste des figures automatique"}
              </label>

              <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50/90 p-2.5 text-[11px] text-slate-700">
                <div className="flex flex-wrap items-center justify-between gap-1 font-semibold text-[#6C2826]">
                  <span>
                    {lang === "en"
                      ? "A4 Pagination & Figure Control (>= 12 pt)"
                      : "Contrôle de Pagination A4 & Figures (≥ 12 pt)"}
                  </span>
                  <span className="rounded bg-[#6C2826]/10 px-1.5 py-0.5 text-[10px]">
                    {lang === "en" ? "A4 Ready (21 × 29.7 cm)" : "A4 Prêt (21 × 29,7 cm)"}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] text-slate-600">
                  <span>
                    {lang === "en" ? "Printable A4 area:" : "Zone imprimable A4 :"}{" "}
                    <strong>
                      {paginationSummary?.usable_width_cm ?? (21.0 - 2 * (fmt.margin_cm || 2.5)).toFixed(1)} ×{" "}
                      {paginationSummary?.usable_height_cm ?? (29.7 - 2 * (fmt.margin_cm || 2.5)).toFixed(1)} cm
                    </strong>
                  </span>
                  {paginationSummary && (
                    <span>
                      {lang === "en" ? "Estimated pages:" : "Pages estimées :"}{" "}
                      <strong>
                        {paginationSummary.total_roman_pages} rom. + {paginationSummary.total_arabic_pages} body
                      </strong>{" "}
                      · Figures ≥ 12 pt : <strong>{paginationSummary.figures_checked}</strong>
                    </span>
                  )}
                </div>
                {paginationSummary?.decision_notes && (
                  <p className="mt-1 text-[10px] italic text-slate-500">
                    {paginationSummary.decision_notes.replace(/gemini[^,.;)]*/gi, "Smart Research")}
                  </p>
                )}
              </div>
            </div>

            <div className="space-y-2 border-t border-slate-200 pt-3">
              <div className="text-xs font-semibold text-slate-700">
                {lang === "en" ? "Template Frames & Border Styles" : "Style des cadres & bordures du template"}
              </div>
              <div className="grid grid-cols-2 gap-2.5">
                <div>
                  <label className="block text-[11px] font-medium text-slate-600">
                    {lang === "en" ? "Chapter boxes" : "Encadrés chapitres"}
                  </label>
                  <select
                    value={fmt.chapter_box_style ?? "rounded"}
                    onChange={(e) => {
                      const style = e.target.value as FormattingRules["chapter_box_style"];
                      setFmt({
                        ...fmt,
                        chapter_box_style: style,
                        box_corner_radius_pct:
                          style === "square"
                            ? 0
                            : fmt.box_corner_radius_pct && fmt.box_corner_radius_pct > 0
                            ? fmt.box_corner_radius_pct
                            : 18,
                      });
                    }}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                  >
                    <option value="rounded">{lang === "en" ? "Rounded corners" : "Coins arrondis (PROT K1)"}</option>
                    <option value="square">{lang === "en" ? "Square corners" : "Coins carrés"}</option>
                    <option value="minimal">{lang === "en" ? "Minimal thin border" : "Bordure fine minimale"}</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-slate-600">
                    {lang === "en" ? "Cover border" : "Bordure page de garde"}
                  </label>
                  <select
                    value={fmt.cover_border_style ?? "double"}
                    onChange={(e) =>
                      setFmt({
                        ...fmt,
                        cover_border_style: e.target.value as FormattingRules["cover_border_style"],
                      })
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                  >
                    <option value="double">{lang === "en" ? "Double line (FMSB)" : "Double filet FMSB"}</option>
                    <option value="single">{lang === "en" ? "Single line" : "Filet simple"}</option>
                    <option value="none">{lang === "en" ? "No border" : "Sans bordure"}</option>
                  </select>
                </div>
              </div>
              <div className="flex items-center justify-between gap-2">
                <label className="text-[11px] font-medium text-slate-600">
                  {lang === "en" ? "Border color" : "Couleur des bordures"}
                </label>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[11px] font-semibold text-slate-700">
                    {accentColor.toUpperCase()}
                  </span>
                  <input
                    type="color"
                    aria-label={lang === "en" ? "Border color" : "Couleur des bordures"}
                    value={accentColor}
                    onChange={(e) =>
                      setFmt({ ...fmt, border_color: e.target.value })
                    }
                    className="h-7 w-14 cursor-pointer rounded border border-slate-300"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2.5 pt-1">
                <div>
                  <label className="flex items-center justify-between text-[11px] font-medium text-slate-600">
                    <span>{lang === "en" ? "Border width" : "Épaisseur cadres"}</span>
                    <span className="font-semibold text-slate-800">
                      {(fmt.box_border_width_pt ?? 2.5).toFixed(1)} pt
                    </span>
                  </label>
                  <input
                    type="range"
                    min={1}
                    max={5}
                    step={0.5}
                    value={fmt.box_border_width_pt ?? 2.5}
                    onChange={(e) =>
                      setFmt({
                        ...fmt,
                        box_border_width_pt: Number(e.target.value),
                      })
                    }
                    className="mt-1 w-full accent-[#6C2826]"
                  />
                </div>
                <div>
                  <label className="flex items-center justify-between text-[11px] font-medium text-slate-600">
                    <span>{lang === "en" ? "Corner radius" : "Arrondi angles"}</span>
                    <span className="font-semibold text-slate-800">
                      {fmt.box_corner_radius_pct ?? 18} %
                    </span>
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={50}
                    step={1}
                    value={fmt.box_corner_radius_pct ?? 18}
                    onChange={(e) => {
                      const val = Number(e.target.value);
                      setFmt({
                        ...fmt,
                        box_corner_radius_pct: val,
                        chapter_box_style:
                          val > 0
                            ? fmt.chapter_box_style === "minimal"
                              ? "minimal"
                              : "rounded"
                            : "square",
                      });
                    }}
                    className="mt-1 w-full accent-[#6C2826]"
                  />
                </div>
              </div>

              {/* Header & Footer Separator Line Customization (Pages 2+) */}
              <div className="mt-2 space-y-2 rounded-lg border border-slate-200 bg-slate-50/70 p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-semibold text-slate-700">
                    {lang === "en"
                      ? "Header & footer separators (pages 2+)"
                      : "Séparateurs en-tête & pied de page (p. 2+)"}
                  </span>
                  <button
                    type="button"
                    data-testid="hf-rule-match-border-btn"
                    onClick={() =>
                      setFmt({ ...fmt, header_footer_rule_color: accentColor })
                    }
                    className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[10px] font-semibold text-slate-700 hover:bg-slate-100"
                    title={
                      lang === "en"
                        ? "Match document border color"
                        : "Aligner sur la couleur des bordures"
                    }
                  >
                    {lang === "en" ? "Match borders" : "Idem bordures"}
                  </button>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <label className="text-[11px] font-medium text-slate-600">
                    {lang === "en" ? "Separator color" : "Couleur des filets"}
                  </label>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold text-slate-700">
                      {hfRuleColor.toUpperCase()}
                    </span>
                    <input
                      type="color"
                      data-testid="hf-rule-color-input"
                      aria-label={
                        lang === "en"
                          ? "Header and footer separator color"
                          : "Couleur des séparateurs en-tête et pied de page"
                      }
                      value={hfRuleColor}
                      onChange={(e) =>
                        setFmt({ ...fmt, header_footer_rule_color: e.target.value })
                      }
                      className="h-7 w-14 cursor-pointer rounded border border-slate-300"
                    />
                  </div>
                </div>
                <div>
                  <label className="flex items-center justify-between text-[11px] font-medium text-slate-600">
                    <span>
                      {lang === "en" ? "Separator thickness" : "Épaisseur des filets"}
                    </span>
                    <span className="font-semibold text-slate-800">
                      {hfRuleWidthPt.toFixed(2).replace(/\.00$/, ".0")} pt
                    </span>
                  </label>
                  <input
                    type="range"
                    data-testid="hf-rule-width-input"
                    aria-label={
                      lang === "en"
                        ? "Header and footer separator thickness"
                        : "Épaisseur des séparateurs en-tête et pied de page"
                    }
                    min={0.5}
                    max={4}
                    step={0.25}
                    value={hfRuleWidthPt}
                    onChange={(e) =>
                      setFmt({
                        ...fmt,
                        header_footer_rule_width_pt: Number(e.target.value),
                      })
                    }
                    className="mt-1 w-full accent-[#6C2826]"
                  />
                </div>
              </div>
            </div>

            <button
              type="button"
              disabled={saving}
              onClick={handleApplyFormatting}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
            >
              <span>
                {lang === "en" ? "Apply Formatting Rules" : "Appliquer les règles de mise en page"}
              </span>
              {isFormattingDirty && (
                <span className="rounded-full bg-amber-400 px-2 py-0.5 text-[10px] font-bold text-slate-950">
                  {lang === "en" ? "Modified" : "Modifié"}
                </span>
              )}
            </button>
          </div>
        </div>

        {/* Tab switch button for Tables & Annexes */}
        <div className="rounded-xl border border-indigo-200 bg-indigo-50/60 p-4">
          <div className="flex items-center gap-2 font-bold text-xs uppercase text-indigo-900 mb-2">
            <TableProperties className="h-4 w-4" />
            {t("m2.structured_tab")}
          </div>
          <p className="text-xs text-indigo-950/80 mb-3">
            {lang === "en"
              ? "Study variables, CRF collection form, prior studies synthesis, Gantt timeline, budget, and administrative letters."
              : "Variables, Fiche de collecte, Synthèse littérature, Gantt, Budget et lettres administratives."}
          </p>
          <button
            type="button"
            onClick={() => setViewMode(viewMode === "preview" ? "tables" : "preview")}
            className="w-full rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700"
          >
            {viewMode === "preview"
              ? lang === "en"
                ? "Edit Tables & Appendices"
                : "Éditer les Tableaux & Annexes FMSB"
              : lang === "en"
              ? "Back to Document Preview"
              : "Retour à l'Aperçu du document"}
          </button>
        </div>

        {/* Humanisation Académique & Anti-Détection IA */}
        <div className="rounded-xl border border-amber-300 bg-gradient-to-br from-amber-50/90 via-white to-emerald-50/40 p-5 shadow-xs">
          <div className="flex items-center gap-2">
            <Feather className="h-4 w-4 text-amber-700" />
            <h3 className="text-sm font-bold text-slate-900">
              {t("m2.audit_tab")}
            </h3>
          </div>
          <p className="mt-1 text-xs text-slate-600">
            {lang === "en"
              ? "Natural academic prose polishing prior to Word/PDF compilation, preserving 100% of citations and author metadata."
              : "Réécriture en plume clinique FMSB authentique avant compilation Word/PDF, avec conservation stricte des données et empreinte auteur réelle."}
          </p>
          <ul className="mt-2.5 space-y-1 text-[11px] text-slate-700">
            <li className="flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
              <span>
                {lang === "en"
                  ? "Natural clinical rhythm & phrasing"
                  : "Réécriture naturelle FMSB (Burstiness & Perplexité)"}
              </span>
            </li>
            <li className="flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
              <span>
                {lang === "en"
                  ? "100% preservation of Vancouver citations [n] and numeric data"
                  : "Conservation 100 % des citations Vancouver [n] et chiffres"}
              </span>
            </li>
            <li className="flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
              <span>
                {lang === "en" ? "Signed .docx/.pdf author:" : "Signature .docx/.pdf au nom de :"}{" "}
                <strong>{project.cover.author_name || "Impétrant FMSB"}</strong>
              </span>
            </li>
          </ul>
          <button
            type="button"
            disabled={humanizingAll}
            onClick={handleHumanizeAll}
            className="mt-3 flex min-h-[40px] w-full items-center justify-center gap-2 rounded-lg bg-amber-700 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-800 disabled:opacity-50"
          >
            <Feather className={`h-4 w-4 ${humanizingAll ? "animate-pulse" : ""}`} />
            {humanizingAll
              ? lang === "en"
                ? "Humanizing protocol..."
                : "Humanisation du protocole en cours..."
              : t("m2.humanize_all")}
          </button>

          {humanizeReport && (
            <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50/90 p-3 text-xs text-emerald-950">
              <div className="flex flex-wrap items-center justify-between gap-1 font-bold">
                <span>
                  {humanizeReport.sections_humanized}{" "}
                  {lang === "en" ? "section(s) humanized" : "section(s) humanisée(s)"}
                </span>
                <span className="rounded-full bg-emerald-700 px-2 py-0.5 text-[11px] text-white">
                  {lang === "en" ? "Natural style:" : "Style humain :"} {humanizeReport.average_human_score} %
                </span>
              </div>
              <div className="mt-1.5 space-y-0.5 text-[11px] text-emerald-900">
                <div>
                  {lang === "en" ? "AI clichés removed:" : "Tournures IA purgées :"}{" "}
                  <strong>{humanizeReport.total_cliches_removed}</strong> ·{" "}
                  {humanizeReport.average_before_score} % →{" "}
                  <strong>{humanizeReport.average_human_score} %</strong>
                </div>
                <div>
                  {lang === "en" ? "Citations [n] preserved:" : "Citations Vancouver [n] préservées :"}{" "}
                  <strong>
                    {humanizeReport.citations_preserved
                      ? lang === "en" ? "100% intact" : "100 % conformes"
                      : lang === "en" ? "Check required" : "Vérification requise"}
                  </strong>
                </div>
                <div>
                  {lang === "en" ? "Signed author:" : "Auteur Word/PDF signé :"}{" "}
                  <strong>{humanizeReport.author_signature}</strong>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Export Box */}
        <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-5">
          <h3 className="flex items-center gap-2 text-sm font-bold text-emerald-950">
            <FileCheck2 className="h-4 w-4 text-emerald-700" />
            {lang === "en" ? "Protocol Export" : "Export du protocole"}
          </h3>
          <p className="mt-1 text-xs text-emerald-900/80">
            {lang === "en"
              ? `Compiled from the official master template (${fmt.margin_cm} cm margins).`
              : `Généré depuis le gabarit maître officiel 2025_Canevas_protocole.docx (marges ${fmt.margin_cm} cm).`}
          </p>
          <div className="mt-4 space-y-2">
            <button
              type="button"
              disabled={exportingDocx}
              onClick={handleDownloadDocx}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-800 disabled:opacity-60"
            >
              <Download className="h-4 w-4" />
              {exportingDocx
                ? lang === "en"
                  ? "Generating Word (.docx)..."
                  : "Génération Word (.docx)..."
                : t("m2.export_docx")}
            </button>
            <button
              type="button"
              disabled={exportingPdf}
              onClick={handleDownloadPdf}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-[#6C2826] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#571f1d] disabled:opacity-60"
            >
              <FileText className="h-4 w-4" />
              {exportingPdf
                ? lang === "en"
                  ? "Compiling PDF..."
                  : "Compilation PDF fidèle en cours..."
                : t("m2.export_pdf")}
            </button>
          </div>
        </div>

        {/* Phase 4: Cross-Section Audit & Bidirectional Sync */}
        <div className="rounded-xl border border-amber-200 bg-amber-50/50 p-5">
          <h3 className="flex items-center gap-2 text-sm font-bold text-amber-950">
            <ShieldCheck className="h-4 w-4 text-amber-700" />
            {lang === "en" ? "Final QC & Sync" : "Contrôle Final & Synchronisation FMSB"}
          </h3>
          <p className="mt-1 text-xs text-amber-900/80">
            {lang === "en"
              ? "Synchronize abbreviations, variables, prior studies, and budget from drafted sections, then run cross-section audit."
              : "Synchronisez les sigles, variables, études et budget depuis les sections rédigées puis vérifiez la cohérence inter-sections avant impression."}
          </p>
          <div className="mt-3 space-y-2">
            <button
              type="button"
              disabled={syncingStructured}
              onClick={handleSyncStructured}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-semibold text-amber-950 hover:bg-amber-100 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${syncingStructured ? "animate-spin" : ""}`} />
              {syncingStructured
                ? lang === "en"
                  ? "Syncing..."
                  : "Synchronisation en cours..."
                : lang === "en"
                ? "Sync Text -> Structured Tables"
                : "Synchroniser Texte → Tableaux structurés"}
            </button>
            <button
              type="button"
              disabled={auditingGlobal}
              onClick={handleGlobalAudit}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-amber-700 px-3 py-2 text-xs font-semibold text-white hover:bg-amber-800 disabled:opacity-50"
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              {auditingGlobal
                ? lang === "en"
                  ? "Running global audit..."
                  : "Analyse transversale..."
                : lang === "en"
                ? "Run Global Protocol Audit"
                : "Lancer l'Audit Global (Phase 4)"}
            </button>
          </div>

          {syncMessage && (
            <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-2.5 text-xs text-emerald-900">
              {syncMessage}
            </div>
          )}

          {globalAudit && (
            <div className="mt-4 space-y-2.5 border-t border-amber-200 pt-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-slate-800">
                  Score de conformité : {globalAudit.score_percent}%
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                    globalAudit.ready_for_print
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-amber-100 text-amber-900"
                  }`}
                >
                  {globalAudit.ready_for_print ? "Prêt pour impression" : "Révisions conseillées"}
                </span>
              </div>
              <div className="space-y-2">
                {globalAudit.checks.map((chk) => (
                  <div
                    key={chk.id}
                    className="rounded-lg border border-slate-200 bg-white p-2.5 text-xs"
                  >
                    <div className="flex items-start gap-1.5 font-semibold text-slate-800">
                      {chk.status === "ok" && (
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                      )}
                      {chk.status === "warning" && (
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                      )}
                      {chk.status === "error" && (
                        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600" />
                      )}
                      <span>{chk.label}</span>
                    </div>
                    <p className="mt-0.5 text-[11px] text-slate-600">{chk.summary}</p>
                    {chk.status !== "ok" && chk.details.length > 0 && (
                      <ul className="mt-1 list-disc pl-4 text-[11px] text-slate-500">
                        {chk.details.map((d, idx) => (
                          <li key={idx}>{d}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* Right Column: Live Document Preview OR Structured Tables Editor */}
      <section className="lg:col-span-8">
        {viewMode === "tables" ? (
          <StructuredTablesEditor
            project={project}
            onProjectUpdated={onProjectUpdated}
            onError={onError}
          />
        ) : (
          <div className="rounded-xl border border-slate-200 bg-slate-200/70 p-6">
            <div
              className="mx-auto max-w-[794px] rounded bg-white p-10 shadow-md"
              style={{
                fontFamily: fmt.font_family,
                fontSize: `${fmt.body_font_size_pt}pt`,
                lineHeight: fmt.line_spacing,
                textAlign: fmt.text_align,
              }}
            >
              {/* Cover Preview */}
              <div
                className="mb-10 flex min-h-[920px] flex-col justify-between p-6 text-center"
                style={{
                  borderWidth: fmt.cover_border_style === "none" ? 0 : fmt.cover_border_style === "single" ? 2 : 4,
                  borderStyle: fmt.cover_border_style === "none" ? "none" : fmt.cover_border_style === "single" ? "solid" : "double",
                  borderColor: fmt.border_color ?? "#6C2826",
                  borderRadius: fmt.chapter_box_style === "rounded" ? "12px" : "0px",
                }}
              >
                <div>
                  {(() => {
                    const ch = getFormattedCoverHeader(project.cover);
                    const uniLogoSrc = project.cover.university_logo_url
                      ? withAuthToken(project.cover.university_logo_url)
                      : "/tamplates/fmsb_uy1_logo.png";
                    const facLogoSrc = project.cover.faculty_logo_url
                      ? withAuthToken(project.cover.faculty_logo_url)
                      : null;
                    return (
                      <div
                        className={`grid grid-cols-12 items-center gap-2 font-bold leading-tight text-slate-800 ${
                          ch.hasOptionalTiers ? "text-[9px]" : "text-[10px]"
                        }`}
                      >
                        <div className="col-span-5 text-center">
                          <div>RÉPUBLIQUE DU CAMEROUN</div>
                          <div className="font-normal italic">Paix – Travail – Patrie</div>
                          <div className="text-[8px]">************</div>
                          <div>MINISTÈRE DE L&apos;ENSEIGNEMENT SUPÉRIEUR</div>
                          <div className="text-[8px]">**************</div>
                          <div>{ch.universityFr}</div>
                          {ch.universityContactsFr.map((ln, idx) => (
                            <div key={idx} className="text-[8.5px] font-normal text-slate-700">
                              {ln}
                            </div>
                          ))}
                          <div className="text-[8px]">**************</div>
                          <div>{ch.facultyFr}</div>
                          <div className="text-[8px]">**************</div>
                          <div className="mt-0.5">DÉPARTEMENT DE {ch.departmentFr}</div>
                          {ch.facultyContactsFr.length > 0 && (
                            <>
                              <div className="text-[8px]">**************</div>
                              {ch.facultyContactsFr.map((ln, idx) => (
                                <div key={idx} className="text-[8.5px] font-normal text-slate-700">
                                  {ln}
                                </div>
                              ))}
                            </>
                          )}
                        </div>
                        <div className="col-span-2 flex flex-col items-center justify-center gap-1.5">
                          <img
                            src={uniLogoSrc}
                            alt="Logo Université"
                            className={`w-auto object-contain ${facLogoSrc ? "max-h-14" : "max-h-24"}`}
                          />
                          {facLogoSrc && (
                            <img
                              src={facLogoSrc}
                              alt="Logo Faculté"
                              className="max-h-14 w-auto object-contain"
                            />
                          )}
                        </div>
                        <div className="col-span-5 text-center">
                          <div>REPUBLIC OF CAMEROON</div>
                          <div className="font-normal italic">Peace – Work – Fatherland</div>
                          <div className="text-[8px]">************</div>
                          <div>MINISTRY OF HIGHER EDUCATION</div>
                          <div className="text-[8px]">************</div>
                          <div>{ch.universityEn}</div>
                          {ch.universityContactsEn.map((ln, idx) => (
                            <div key={idx} className="text-[8.5px] font-normal text-slate-700">
                              {ln}
                            </div>
                          ))}
                          <div className="text-[8px]">************</div>
                          <div>{ch.facultyEn}</div>
                          <div className="text-[8px]">************</div>
                          <div className="mt-0.5">DEPARTMENT OF {ch.departmentEn}</div>
                          {ch.facultyContactsEn.length > 0 && (
                            <>
                              <div className="text-[8px]">************</div>
                              {ch.facultyContactsEn.map((ln, idx) => (
                                <div key={idx} className="text-[8.5px] font-normal text-slate-700">
                                  {ln}
                                </div>
                              ))}
                            </>
                          )}
                        </div>
                      </div>
                    );
                  })()}
                  <div
                    className="my-6 px-4 py-5 font-bold text-slate-900"
                    style={{
                      fontSize: "18pt",
                      lineHeight: 1.3,
                      borderWidth: `${fmt.box_border_width_pt ?? 2.5}px`,
                      borderStyle: "solid",
                      borderColor: fmt.border_color ?? "#6C2826",
                      borderRadius: `${fmt.box_corner_radius_pct ?? 18}px`,
                    }}
                  >
                    {project.cover.title || project.name}
                  </div>
                  <div className="text-xs">
                    {t("m2.cover_protocol_of", {
                      degree:
                        project.cover.degree_label ||
                        (lang === "en" ? "Doctor of Medicine (MD)" : "Doctorat en Médecine Générale"),
                    })}
                  </div>
                  <div className="mt-2 font-bold">
                    {project.cover.author_name || (lang === "en" ? "Full Name" : "Noms & prénoms")}
                  </div>
                  <div className="text-xs">
                    {lang === "en" ? "Registration No.:" : "Matricule :"} {project.cover.matricule || "—"}
                  </div>
                </div>
                <div
                  className="mt-6 grid grid-cols-2 gap-6 text-slate-800"
                  style={{ fontFamily: "'Times New Roman', serif" }}
                >
                  <div>
                    <div className="mb-1.5 text-xs font-bold">{t("m2.cover_supervisor")}</div>
                    {project.cover.director_info?.name ||
                    project.cover.director_info?.rank ||
                    project.cover.director_info?.specialty ? (
                      <div>
                        {project.cover.director_info.name && (
                          <div style={{ fontSize: "14pt", fontWeight: "bold" }}>
                            {project.cover.director_info.name}
                          </div>
                        )}
                        {project.cover.director_info.rank && (
                          <div style={{ fontSize: "12pt", fontWeight: "normal" }}>
                            {project.cover.director_info.rank}
                          </div>
                        )}
                        {project.cover.director_info.specialty && (
                          <div style={{ fontSize: "12pt", fontWeight: "normal" }}>
                            {project.cover.director_info.specialty}
                          </div>
                        )}
                      </div>
                    ) : (
                      <div style={{ fontSize: "14pt", fontWeight: "bold" }}>
                        {project.cover.director || (lang === "en" ? "To be filled" : "À renseigner")}
                      </div>
                    )}
                  </div>
                  <div>
                    <div className="mb-1.5 text-xs font-bold">{t("m2.cover_cosupervisors")}</div>
                    {project.cover.co_directors_info &&
                    project.cover.co_directors_info.length > 0 ? (
                      <div className="space-y-3">
                        {project.cover.co_directors_info.map((cd, idx) => (
                          <div key={idx}>
                            {cd.name && (
                              <div style={{ fontSize: "14pt", fontWeight: "bold" }}>
                                {cd.name}
                              </div>
                            )}
                            {cd.rank && (
                              <div style={{ fontSize: "12pt", fontWeight: "normal" }}>
                                {cd.rank}
                              </div>
                            )}
                            {cd.specialty && (
                              <div style={{ fontSize: "12pt", fontWeight: "normal" }}>
                                {cd.specialty}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : project.cover.co_directors.length > 0 ? (
                      <div className="space-y-3">
                        {project.cover.co_directors.map((cd, idx) => (
                          <div key={idx} style={{ fontSize: "14pt", fontWeight: "bold" }}>
                            {cd}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div style={{ fontSize: "12pt" }}>
                        {lang === "en" ? "To be filled" : "À renseigner"}
                      </div>
                    )}
                  </div>
                </div>
                <div className="mt-10 flex justify-center">
                  <div
                    className="px-6 py-2 text-xs font-bold text-slate-900"
                    style={{
                      borderWidth: `${fmt.box_border_width_pt ?? 2.5}px`,
                      borderStyle: "solid",
                      borderColor: fmt.border_color ?? "#6C2826",
                      borderRadius: `${fmt.box_corner_radius_pct ?? 18}px`,
                    }}
                  >
                    {t("m2.cover_academic_year")} {project.cover.academic_year}
                  </div>
                </div>
              </div>

              {/* Validated & Drafted Sections */}
              <div className="mb-4 flex items-center justify-between border-b border-slate-200 pb-2 text-xs font-sans text-slate-500">
                <span>
                  {lang === "en"
                    ? `Structured preview (${validatedSections.length} validated sections)`
                    : `Aperçu structuré (${validatedSections.length} sections validées)`}
                </span>
                <span>
                  {fmt.font_family} {fmt.body_font_size_pt}pt ·{" "}
                  {lang === "en" ? "Line spacing" : "Interligne"} {fmt.line_spacing}
                </span>
              </div>

              {/* Preview of Running Header starting Page 2 */}
              <div
                data-testid="preview-running-header"
                className="mb-4 flex items-center justify-between gap-4 pb-1.5 text-[11px] italic text-slate-500"
                style={{
                  borderBottomWidth: `${Math.max(1, Number((hfRuleWidthPt * 1.35).toFixed(2)))}px`,
                  borderBottomStyle: "solid",
                  borderBottomColor: hfRuleColor,
                }}
              >
                <span className="font-sans text-[10px] font-semibold not-italic uppercase tracking-wide text-slate-400">
                  {lang === "en" ? "Header (pages 2+)" : "En-tête (pages 2+)"}
                </span>
                <span className="truncate text-right">
                  {project.cover.title || project.name}
                </span>
              </div>

              <div className="space-y-6">
                {canvas
                  .filter((s) => s.id !== "cover_page")
                  .map((spec, idx, arr) => {
                    const prevSpec = idx > 0 ? arr[idx - 1] : null;
                    const isNewMajorChapter =
                      spec.chapter_id !== "preliminaires" &&
                      spec.chapter_id !== prevSpec?.chapter_id;
                    const st = project.sections[spec.id];
                    const hasHtml = Boolean(st?.content_html?.trim());
                    const hasText = Boolean(st?.content_markdown?.trim());
                    const isRefs =
                      spec.id === "references" &&
                      project.references.length > 0 &&
                      !(st?.status === "validated" && hasText);
                    return (
                      <div key={spec.id}>
                        {isNewMajorChapter && (
                          <div
                            className="my-6 px-4 py-3 text-center text-base font-bold uppercase tracking-wide text-slate-900"
                            style={{
                              borderWidth: `${fmt.box_border_width_pt ?? 2.5}px`,
                              borderStyle: "solid",
                              borderColor: fmt.border_color ?? "#6C2826",
                              borderRadius: `${fmt.box_corner_radius_pct ?? 18}px`,
                            }}
                          >
                            {spec.chapter_title}
                          </div>
                        )}
                        <div className="flex items-center gap-2 text-left">
                          <h4 className="text-base font-bold text-slate-900">
                            {spec.title}
                          </h4>
                          {st?.status === "validated" && (
                            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                          )}
                        </div>

                        {/* Rich HTML or Plain Markdown */}
                        {isRefs ? (
                          <ol className="mt-2 list-decimal space-y-1.5 pl-6 text-left text-sm text-slate-800">
                            {project.references.map((r) => (
                              <li key={r.ref_id}>{r.vancouver_citation}</li>
                            ))}
                          </ol>
                        ) : hasHtml ? (
                          <div
                            className={`mt-2 prose prose-slate max-w-none text-sm text-slate-800 ${
                              spec.id === "toc" ? "text-left" : ""
                            }`}
                            dangerouslySetInnerHTML={{ __html: st.content_html }}
                          />
                        ) : hasText ? (
                          <p
                            className={`mt-1 whitespace-pre-wrap text-slate-800 ${
                              spec.id === "toc" ? "text-left" : ""
                            }`}
                          >
                            {st.content_markdown}
                          </p>
                        ) : (
                          <p className="mt-1 font-sans text-xs italic text-slate-400">
                            {lang === "en"
                              ? "[Pending drafting / validation in Module 1]"
                              : "[En attente de rédaction / validation dans le Module 1]"}
                          </p>
                        )}

                        {/* Unified Effective Tables Preview (identical to Word/PDF export & Module 1) */}
                        {(() => {
                          const hasCustomTables =
                            (st?.custom_tables?.filter(
                              (ct) => (ct.headers?.length ?? 0) > 0 || (ct.rows?.length ?? 0) > 0
                            ).length ?? 0) > 0;
                          const suppressStructured = Boolean(st?.suppress_structured_table);
                          if (hasCustomTables || suppressStructured) {
                            return null;
                          }
                          if (spec.id === "chap1_variables" && project.variables?.length > 0) {
                            return (
                              <div className="mt-3">
                                <div className="mb-1 flex items-center justify-between text-left text-xs font-bold text-slate-800">
                                  <span>
                                    {lang === "en"
                                      ? "Table: Operational Study Variables"
                                      : "Tableau : Liste opérationnelle des variables d'étude"}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={async () => {
                                      try {
                                        const updated = await api.deleteSectionTable(
                                          project.project_id,
                                          spec.id,
                                          "__structured_variables__"
                                        );
                                        onProjectUpdated(updated);
                                      } catch (err) {
                                        onError(err instanceof Error ? err.message : String(err));
                                      }
                                    }}
                                    className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-sans text-[11px] font-medium text-rose-600 hover:bg-rose-50"
                                    title={lang === "en" ? "Delete this table" : "Supprimer ce tableau"}
                                  >
                                    <Trash2 className="h-3 w-3" />
                                    {lang === "en" ? "Remove" : "Retirer"}
                                  </button>
                                </div>
                                <div className="overflow-x-auto rounded border border-slate-200">
                                  <table className="w-full text-left text-xs">
                                    <thead className="text-white" style={{ backgroundColor: accentColor }}>
                                      <tr>
                                        <th className="p-1.5">Variable</th>
                                        <th className="p-1.5">{lang === "en" ? "Role" : "Rôle"}</th>
                                        <th className="p-1.5">{lang === "en" ? "Scale" : "Échelle"}</th>
                                        <th className="p-1.5">{lang === "en" ? "Categories" : "Modalités"}</th>
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                      {project.variables.map((v, idx) => (
                                        <tr key={idx}>
                                          <td className="p-1.5 font-medium">{v.name}</td>
                                          <td className="p-1.5">{v.role}</td>
                                          <td className="p-1.5">{v.measurement_scale}</td>
                                          <td className="p-1.5">{v.modalities || "—"}</td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              </div>
                            );
                          }
                          if (
                            spec.id === "chap2_etat_connaissances" &&
                            project.literature_synthesis?.length > 0
                          ) {
                            return (
                              <div className="mt-3">
                                <div className="mb-1 flex items-center justify-between text-left text-xs font-bold text-slate-800">
                                  <span>
                                    {lang === "en"
                                      ? "Table: Synthesis of Prior Studies"
                                      : "Tableau : Synthèse des études antérieures"}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={async () => {
                                      try {
                                        const updated = await api.deleteSectionTable(
                                          project.project_id,
                                          spec.id,
                                          "__structured_synthesis__"
                                        );
                                        onProjectUpdated(updated);
                                      } catch (err) {
                                        onError(err instanceof Error ? err.message : String(err));
                                      }
                                    }}
                                    className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-sans text-[11px] font-medium text-rose-600 hover:bg-rose-50"
                                    title={lang === "en" ? "Delete this table" : "Supprimer ce tableau"}
                                  >
                                    <Trash2 className="h-3 w-3" />
                                    {lang === "en" ? "Remove" : "Retirer"}
                                  </button>
                                </div>
                                <div className="overflow-x-auto rounded border border-slate-200">
                                  <table className="w-full text-left text-xs">
                                    <thead className="text-white" style={{ backgroundColor: accentColor }}>
                                      <tr>
                                        <th className="p-1.5">{lang === "en" ? "Author(s), Country" : "Auteur(s), Pays"}</th>
                                        <th className="p-1.5">{lang === "en" ? "Design" : "Devis"}</th>
                                        <th className="p-1.5">Population</th>
                                        <th className="p-1.5">{lang === "en" ? "Key Findings" : "Résultats principaux"}</th>
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                      {project.literature_synthesis.map((s, idx) => (
                                        <tr key={idx}>
                                          <td className="p-1.5 font-medium">{s.authors_country_year}</td>
                                          <td className="p-1.5">{s.study_design}</td>
                                          <td className="p-1.5">{s.population}</td>
                                          <td className="p-1.5">{s.main_results}</td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              </div>
                            );
                          }
                          if (
                            spec.id === "chap3_chronogramme" &&
                            (timelineDraft?.rows?.length ?? 0) > 0
                          ) {
                            return (
                              <div className="mt-3">
                                <div className="mb-1.5 flex items-center justify-between text-left text-xs font-bold text-slate-800">
                                  <span>
                                    {lang === "en"
                                      ? "Table: Activity Timeline"
                                      : "Tableau : Chronogramme des activités"}{" "}
                                    <span className="font-sans text-[11px] font-normal text-slate-500">
                                      {lang === "en"
                                        ? "(editable dates, activities & milestones)"
                                        : "(dates, activités et jalons éditables)"}
                                    </span>
                                  </span>
                                  <button
                                    type="button"
                                    onClick={async () => {
                                      try {
                                        const updated = await api.deleteSectionTable(
                                          project.project_id,
                                          spec.id,
                                          "__structured_timeline__"
                                        );
                                        onProjectUpdated(updated);
                                      } catch (err) {
                                        onError(err instanceof Error ? err.message : String(err));
                                      }
                                    }}
                                    className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-sans text-[11px] font-medium text-rose-600 hover:bg-rose-50"
                                    title={lang === "en" ? "Delete this table" : "Supprimer ce tableau"}
                                  >
                                    <Trash2 className="h-3 w-3" />
                                    {lang === "en" ? "Remove" : "Retirer"}
                                  </button>
                                </div>
                                <TimelineGridEditor
                                  timelineGrid={timelineDraft}
                                  onChange={setTimelineDraft}
                                  onCommit={handleSaveTimelineGrid}
                                  variant="preview"
                                  saving={savingTimeline}
                                />
                              </div>
                            );
                          }
                          if (spec.id === "chap3_budget" && project.budget?.length > 0) {
                            return (
                              <div className="mt-3">
                                <div className="mb-1 flex items-center justify-between text-left text-xs font-bold text-slate-800">
                                  <span>
                                    {lang === "en"
                                      ? "Table: Estimated Study Budget"
                                      : "Tableau : Budget prévisionnel de l'étude"}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={async () => {
                                      try {
                                        const updated = await api.deleteSectionTable(
                                          project.project_id,
                                          spec.id,
                                          "__structured_budget__"
                                        );
                                        onProjectUpdated(updated);
                                      } catch (err) {
                                        onError(err instanceof Error ? err.message : String(err));
                                      }
                                    }}
                                    className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-sans text-[11px] font-medium text-rose-600 hover:bg-rose-50"
                                    title={lang === "en" ? "Delete this table" : "Supprimer ce tableau"}
                                  >
                                    <Trash2 className="h-3 w-3" />
                                    {lang === "en" ? "Remove" : "Retirer"}
                                  </button>
                                </div>
                                <div className="overflow-x-auto rounded border border-slate-200">
                                  <table className="w-full text-left text-xs">
                                    <thead className="text-white" style={{ backgroundColor: accentColor }}>
                                      <tr>
                                        <th className="p-1.5">{lang === "en" ? "Item" : "Désignation"}</th>
                                        <th className="p-1.5">{lang === "en" ? "Unit Cost (FCFA)" : "PU (FCFA)"}</th>
                                        <th className="p-1.5">{lang === "en" ? "Qty" : "Qté"}</th>
                                        <th className="p-1.5">Total (FCFA)</th>
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                      {project.budget.map((b, idx) => (
                                        <tr key={idx}>
                                          <td className="p-1.5">{b.designation}</td>
                                          <td className="p-1.5">{b.unit_cost_fcfa.toLocaleString(lang === "en" ? "en-US" : "fr-FR")}</td>
                                          <td className="p-1.5">{b.quantity}</td>
                                          <td className="p-1.5 font-semibold">
                                            {(b.quantity * b.unit_cost_fcfa).toLocaleString(lang === "en" ? "en-US" : "fr-FR")}
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              </div>
                            );
                          }
                          return null;
                        })()}

                        {/* Custom Tables Preview */}
                        {st?.custom_tables?.map((ct) => (
                          <div key={ct.table_id} className="mt-3">
                            <div className="mb-1 flex items-center justify-between text-left text-xs font-bold text-slate-800">
                              <span>
                                {lang === "en" ? "Table" : "Tableau"} : {ct.title}
                              </span>
                              <button
                                type="button"
                                onClick={async () => {
                                  try {
                                    const updated = await api.deleteSectionTable(
                                      project.project_id,
                                      spec.id,
                                      ct.table_id
                                    );
                                    onProjectUpdated(updated);
                                  } catch (err) {
                                    onError(err instanceof Error ? err.message : String(err));
                                  }
                                }}
                                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-sans text-[11px] font-medium text-rose-600 hover:bg-rose-50"
                                title={lang === "en" ? "Delete this table" : "Supprimer ce tableau"}
                              >
                                <Trash2 className="h-3 w-3" />
                                {lang === "en" ? "Remove" : "Retirer"}
                              </button>
                            </div>
                            <div className="overflow-x-auto rounded border border-slate-200">
                              <table className="w-full text-left text-xs">
                                <thead className="text-white" style={{ backgroundColor: accentColor }}>
                                  <tr>
                                    {ct.headers.map((h, i) => (
                                      <th key={i} className="p-1.5">
                                        {h}
                                      </th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100">
                                  {ct.rows.map((r, ri) => (
                                    <tr key={ri}>
                                      {r.map((cell, ci) => (
                                        <td key={ci} className="p-1.5">
                                          {cell}
                                        </td>
                                      ))}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        ))}

                        {/* Pipeline & Approved Figures Preview */}
                        {project.images
                          .filter(
                            (img) =>
                              img.target_section_id === spec.id && img.approved
                          )
                          .map((img) => {
                            const imgSrc = img.local_path
                              ? withAuthToken(`/api/projects/${project.project_id}/images/${img.asset_id}/file`)
                              : img.source_url || "";
                            return (
                              <div key={img.asset_id} className="mt-4 rounded-lg border border-slate-200 bg-slate-50/70 p-3 text-center">
                                {imgSrc && (
                                  <img
                                    src={imgSrc}
                                    alt={img.title}
                                    className="mx-auto max-h-72 rounded border border-slate-200 bg-white object-contain p-1 shadow-xs"
                                  />
                                )}
                                <div className="mt-2 text-xs font-bold text-slate-800">
                                  Figure. {img.title}
                                </div>
                                {img.caption && (
                                  <div className="mt-0.5 text-[11px] italic text-slate-600">
                                    {img.caption}
                                  </div>
                                )}
                                <div className="mt-1.5 flex items-center justify-center gap-3 text-[11px]">
                                  {img.license_info && (
                                    <span className="text-slate-500">{img.license_info}</span>
                                  )}
                                  <button
                                    type="button"
                                    onClick={async () => {
                                      try {
                                        const updated = await api.toggleImage(
                                          project.project_id,
                                          img.asset_id,
                                          { approved: false }
                                        );
                                        onProjectUpdated(updated);
                                      } catch (err) {
                                        onError(err instanceof Error ? err.message : String(err));
                                      }
                                    }}
                                    className="text-rose-600 hover:underline font-sans"
                                  >
                                    {lang === "en" ? "Exclude from export" : "Exclure du rendu"}
                                  </button>
                                </div>
                              </div>
                            );
                          })}

                        {/* Vector Diagrams Preview */}
                        {st?.custom_diagrams?.map((dg) => (
                          <div key={dg.diagram_id} className="mt-3 text-center">
                            <img
                              src={api.diagramSvgUrl(project.project_id, spec.id, dg.diagram_id)}
                              alt={dg.title}
                              className="mx-auto max-h-52 max-w-md rounded border border-slate-200 bg-white p-1 object-contain"
                            />
                            <div className="mt-1 text-[11px] italic text-slate-600">
                              Figure. {dg.title}
                              {dg.caption ? ` — ${dg.caption}` : ""}
                            </div>
                          </div>
                        ))}
                      </div>
                    );
                  })}
              </div>

              {/* Preview of Running Footer starting Page 2 */}
              <div
                data-testid="preview-running-footer"
                className="mt-8 flex items-center justify-between gap-4 pt-2 text-[11px] text-slate-500"
                style={{
                  borderTopWidth: `${Math.max(1, Number((hfRuleWidthPt * 1.35).toFixed(2)))}px`,
                  borderTopStyle: "solid",
                  borderTopColor: hfRuleColor,
                }}
              >
                <span className="truncate italic">
                  {project.cover.author_name || (lang === "en" ? "Candidate Name" : "Nom du candidat")}
                </span>
                <span className="shrink-0 font-bold text-slate-800">
                  Page i / 1…
                </span>
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
