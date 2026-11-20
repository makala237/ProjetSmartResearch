import { useEffect, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Circle,
  Clock,
  Feather,
  FileText,
  Globe,
  Loader2,
  Menu,
  MessageSquarePlus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wand2,
  X,
} from "lucide-react";
import { api } from "../api/client";
import type {
  AuditResult,
  CanvasSectionSpec,
  CoherenceAlert,
  CoverMetadata,
  DraftResult,
  HumanizeSectionResult,
  ProtocolProject,
  StepStatus,
  SupervisorInfo,
} from "../types/protocol";
import {
  CoverPageForm,
  emptySupervisor,
  finalizeCoverMetadataPayload,
  withStructuredSupervisors,
} from "./CoverPageForm";
import { ResearchPanel } from "./ResearchPanel";
import { RichTextEditor } from "./RichTextEditor";
import { SectionMediaPanel } from "./SectionMediaPanel";

interface Module1Props {
  project: ProtocolProject;
  canvas: CanvasSectionSpec[];
  selectedSectionId: string;
  onSelectSection: (sectionId: string) => void;
  onProjectUpdated: (updated: ProtocolProject) => void;
  onSaveSection: (
    sectionId: string,
    content: string,
    status: StepStatus,
    userNotes: string,
    contentHtml?: string
  ) => Promise<void>;
  onSaveCover: (cover: CoverMetadata) => Promise<void>;
  onError: (msg: string) => void;
  saving: boolean;
  isAdmin?: boolean;
}

const STATUS_BADGES: Record<
  StepStatus,
  { label: string; badgeClass: string }
> = {
  pending: {
    label: "À faire",
    badgeClass: "bg-slate-100 text-slate-600",
  },
  in_progress: {
    label: "En cours",
    badgeClass: "bg-amber-100 text-amber-800",
  },
  needs_revision: {
    label: "À réviser",
    badgeClass: "bg-rose-100 text-rose-800",
  },
  validated: {
    label: "Validé",
    badgeClass: "bg-emerald-100 text-emerald-800",
  },
};

export function Module1Assistant({
  project,
  canvas,
  selectedSectionId,
  onSelectSection,
  onProjectUpdated,
  onSaveSection,
  onSaveCover,
  onError,
  saving,
  isAdmin = false,
}: Module1Props) {
  const activeSpec =
    canvas.find((s) => s.id === selectedSectionId) ?? canvas[0];
  const activeState = project.sections[activeSpec.id];

  const [draft, setDraft] = useState(activeState?.content_markdown ?? "");
  const [draftHtml, setDraftHtml] = useState(activeState?.content_html ?? "");
  const [notes, setNotes] = useState(activeState?.user_notes ?? "");
  const [mobileStepsOpen, setMobileStepsOpen] = useState(false);
  const [aiInstruction, setAiInstruction] = useState("");
  const [coverForm, setCoverForm] = useState<CoverMetadata>(() =>
    withStructuredSupervisors(project.cover)
  );
  const [coDirDraft, setCoDirDraft] = useState<SupervisorInfo>(emptySupervisor);

  const [busyDraft, setBusyDraft] = useState(false);
  const [busyAudit, setBusyAudit] = useState(false);
  const [busyFix, setBusyFix] = useState(false);
  const [busyHumanize, setBusyHumanize] = useState(false);
  const [busyHarmonize, setBusyHarmonize] = useState(false);
  const [busyToc, setBusyToc] = useState(false);
  const [busyRefs, setBusyRefs] = useState(false);
  const [busyAntigravity, setBusyAntigravity] = useState(false);
  const [lastRefDecisionNote, setLastRefDecisionNote] = useState<string | null>(null);
  const [lastDraftResult, setLastDraftResult] = useState<DraftResult | null>(null);
  const [lastAuditResult, setLastAuditResult] = useState<AuditResult | null>(null);
  const [lastHumanizeResult, setLastHumanizeResult] =
    useState<HumanizeSectionResult | null>(null);
  const [lastHarmonizeNote, setLastHarmonizeNote] = useState<string | null>(null);

  const handleGenerateAutoToc = async () => {
    setBusyToc(true);
    try {
      const res = await api.generateToc(project.project_id);
      onProjectUpdated(res.project);
      const updatedTarget = res.project.sections[activeSpec.id] ?? res.project.sections.toc;
      if (updatedTarget) {
        setDraft(updatedTarget.content_markdown);
        setDraftHtml(updatedTarget.content_html);
      }
    } catch (e: unknown) {
      onError(
        e instanceof Error
          ? e.message
          : "Impossible de générer automatiquement la liste ou le sommaire FMSB."
      );
    } finally {
      setBusyToc(false);
    }
  };

  const handleValidateVancouverReferences = async () => {
    setBusyRefs(true);
    try {
      const res = await api.validateVancouverReferences(project.project_id);
      onProjectUpdated(res.project);
      const updatedRef = res.project.sections.references;
      if (updatedRef) {
        setDraft(updatedRef.content_markdown);
        setDraftHtml(updatedRef.content_html);
      }
      setLastRefDecisionNote(
        `${res.references_count} références numérotées (${res.corrected_count} normalisées) — ${res.decision_summary}`
      );
    } catch (e: unknown) {
      onError(
        e instanceof Error
          ? e.message
          : "Impossible de valider les références selon le système Vancouver."
      );
    } finally {
      setBusyRefs(false);
    }
  };

  useEffect(() => {
    setDraft(activeState?.content_markdown ?? "");
    setDraftHtml(activeState?.content_html ?? "");
    setNotes(activeState?.user_notes ?? "");
  }, [activeSpec.id, activeState?.content_markdown, activeState?.content_html, activeState?.user_notes]);

  useEffect(() => {
    setLastDraftResult(null);
    setLastAuditResult(null);
    setLastHumanizeResult(null);
    setLastRefDecisionNote(null);
    setLastHarmonizeNote(null);
  }, [activeSpec.id]);

  useEffect(() => {
    setCoverForm(withStructuredSupervisors(project.cover));
    setCoDirDraft(emptySupervisor());
  }, [project.project_id, project.cover]);

  const chapters = canvas.reduce<
    Array<{ chapterId: string; chapterTitle: string; items: CanvasSectionSpec[] }>
  >((acc, sec) => {
    const existing = acc.find((c) => c.chapterId === sec.chapter_id);
    if (existing) {
      existing.items.push(sec);
    } else {
      acc.push({
        chapterId: sec.chapter_id,
        chapterTitle: sec.chapter_title,
        items: [sec],
      });
    }
    return acc;
  }, []);

  const validatedCount = Object.values(project.sections).filter(
    (s) => s.status === "validated"
  ).length;
  const progressPct = Math.round((validatedCount / Math.max(canvas.length, 1)) * 100);

  const refById = new Map(project.references.map((r) => [r.ref_id, r]));
  const sectionSegments = project.article_segments.filter((seg) =>
    seg.target_section_ids.includes(activeSpec.id)
  );
  const allCoherenceAlerts: CoherenceAlert[] = project.coherence_alerts ?? [];
  const activeSectionAlerts = allCoherenceAlerts.filter(
    (a) => a.target_section_id === activeSpec.id
  );
  const otherSectionAlerts = allCoherenceAlerts.filter(
    (a) => a.target_section_id !== activeSpec.id
  );

  async function handleDraftWithAI(instruction?: string) {
    setBusyDraft(true);
    onError("");
    try {
      const res = await api.draftSection(
        project.project_id,
        activeSpec.id,
        instruction
      );
      onProjectUpdated(res.project);
      setDraft(res.result.content_markdown);
      setDraftHtml(
        res.result.content_html ??
          res.project.sections[activeSpec.id]?.content_html ??
          ""
      );
      setLastDraftResult(res.result);
      setAiInstruction("");
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyDraft(false);
    }
  }

  async function handleAuditWithAI() {
    setBusyAudit(true);
    onError("");
    try {
      const res = await api.auditSection(project.project_id, activeSpec.id);
      onProjectUpdated(res.project);
      setLastAuditResult(res.result);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAudit(false);
    }
  }

  async function handleMobilizeAntigravity(instruction?: string) {
    setBusyAntigravity(true);
    onError("");
    try {
      const res = await api.runSectionAntigravity(
        project.project_id,
        activeSpec.id,
        instruction ?? aiInstruction,
        true
      );
      onProjectUpdated(res.project);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAntigravity(false);
    }
  }

  async function handleFixWithAI() {
    setBusyFix(true);
    onError("");
    try {
      const res = await api.fixSection(project.project_id, activeSpec.id);
      onProjectUpdated(res.project);
      setDraft(res.result.content_markdown);
      setDraftHtml(
        res.result.content_html ??
          res.project.sections[activeSpec.id]?.content_html ??
          ""
      );
      setLastDraftResult(res.result);
      setLastAuditResult(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyFix(false);
    }
  }

  async function handleHumanizeWithAI() {
    setBusyHumanize(true);
    onError("");
    try {
      const res = await api.humanizeSection(project.project_id, activeSpec.id, true);
      onProjectUpdated(res.project);
      setDraft(res.result.content_markdown);
      setDraftHtml(
        res.result.content_html ??
          res.project.sections[activeSpec.id]?.content_html ??
          ""
      );
      setLastHumanizeResult(res.result);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyHumanize(false);
    }
  }

  async function handleHarmonizeSection(targetSectionId: string, alertId?: string) {
    setBusyHarmonize(true);
    onError("");
    try {
      const res = await api.harmonizeSection(project.project_id, targetSectionId, alertId);
      onProjectUpdated(res.project);
      if (targetSectionId === activeSpec.id) {
        setDraft(res.result.content_markdown);
        setDraftHtml(
          res.result.content_html ??
            res.project.sections[targetSectionId]?.content_html ??
            ""
        );
      }
      setLastHarmonizeNote(res.result.message);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyHarmonize(false);
    }
  }

  const renderStepsList = (closeDrawer?: () => void) => (
    <div className="space-y-4">
      {chapters.map((chap) => (
        <div key={chap.chapterId}>
          <div className="mb-1.5 text-xs font-bold uppercase tracking-wider text-slate-500">
            {chap.chapterTitle}
          </div>
          <div className="space-y-1">
            {chap.items.map((item) => {
              const st = project.sections[item.id]?.status ?? "pending";
              const segCount = project.article_segments.filter(
                (s) => s.approved && s.target_section_ids.includes(item.id)
              ).length;
              const hasCoherenceAlert = allCoherenceAlerts.some(
                (a) => a.target_section_id === item.id
              );
              const isSelected = item.id === activeSpec.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    onSelectSection(item.id);
                    closeDrawer?.();
                  }}
                  className={`flex min-h-[42px] w-full items-start justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition ${
                    isSelected
                      ? "bg-indigo-50 font-medium text-indigo-900 ring-1 ring-indigo-200"
                      : "text-slate-700 hover:bg-slate-50"
                  }`}
                >
                  <span className="flex items-start gap-2">
                    <span className="mt-0.5 shrink-0">
                      {st === "validated" ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                      ) : st === "in_progress" ? (
                        <Clock className="h-4 w-4 text-amber-500" />
                      ) : st === "needs_revision" ? (
                        <AlertCircle className="h-4 w-4 text-rose-500" />
                      ) : (
                        <Circle className="h-4 w-4 text-slate-300" />
                      )}
                    </span>
                    <span className="line-clamp-2 leading-snug">{item.title}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    {hasCoherenceAlert && (
                      <span
                        className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800"
                        title="Des modifications dans une autre section impactent cette rubrique"
                      >
                        À harmoniser
                      </span>
                    )}
                    {segCount > 0 && (
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">
                        {segCount}
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );

  return (
    <div className="space-y-4 sm:space-y-5">
      {/* Top Research / Import / References Panel */}
      <ResearchPanel
        project={project}
        onProjectUpdated={onProjectUpdated}
        onError={onError}
      />

      {/* Mobile Sticky Step Selector Bar (< lg) */}
      <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 shadow-sm lg:hidden">
        <div className="min-w-0">
          <div className="truncate text-[10px] font-bold uppercase tracking-wider text-indigo-600">
            {activeSpec.chapter_title} · {validatedCount}/{canvas.length} validées
          </div>
          <div className="truncate text-sm font-bold text-slate-900">
            {activeSpec.title}
          </div>
        </div>
        <button
          type="button"
          onClick={() => setMobileStepsOpen(true)}
          className="inline-flex min-h-[44px] shrink-0 items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-700"
        >
          <Menu className="h-4 w-4" />
          Étapes ({canvas.length})
        </button>
      </div>

      {/* Mobile Slide-Over Drawer for 24 Steps */}
      {mobileStepsOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div
            className="fixed inset-0 bg-slate-900/50 backdrop-blur-xs"
            onClick={() => setMobileStepsOpen(false)}
          />
          <div className="relative z-50 flex h-full w-80 max-w-[85vw] flex-col bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <div>
                <h2 className="text-sm font-bold uppercase tracking-wide text-slate-800">
                  Canevas FMSB
                </h2>
                <span className="text-xs font-semibold text-indigo-700">
                  {validatedCount}/{canvas.length} étapes validées
                </span>
              </div>
              <button
                type="button"
                aria-label="Fermer le plan FMSB"
                onClick={() => setMobileStepsOpen(false)}
                className="flex min-h-[40px] min-w-[40px] items-center justify-center rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-100"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="h-1.5 w-full bg-slate-100">
              <div
                className="h-full bg-emerald-600 transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {renderStepsList(() => setMobileStepsOpen(false))}
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Desktop Sidebar: Step-by-step FMSB Canvas */}
        <aside className="hidden lg:block lg:col-span-4 xl:col-span-3">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">
                Canevas FMSB
              </h2>
              <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
                {validatedCount}/{canvas.length} validées
              </span>
            </div>
            <div className="mb-4 h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full bg-emerald-600 transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>

            <div className="max-h-[68vh] overflow-y-auto pr-1">
              {renderStepsList()}
            </div>
          </div>
        </aside>

        {/* Main Workspace for Active Step */}
        <section className="space-y-5 lg:col-span-8 xl:col-span-9">
          {/* Section Header + FMSB Constraints */}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider text-indigo-600">
                  {activeSpec.chapter_title}
                </div>
                <h3 className="mt-0.5 text-xl font-bold text-slate-900">
                  {activeSpec.title}
                </h3>
                <p className="mt-1 text-sm text-slate-600">
                  {activeSpec.description}
                </p>
              </div>
              <span
                className={`rounded-full px-3 py-1 text-xs font-semibold ${
                  STATUS_BADGES[activeState?.status ?? "pending"].badgeClass
                }`}
              >
                {STATUS_BADGES[activeState?.status ?? "pending"].label}
              </span>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 rounded-lg bg-slate-50 p-3.5 md:grid-cols-2">
              <div>
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase text-slate-700">
                  <BookOpen className="h-3.5 w-3.5 text-indigo-600" />
                  Attendus du guide FMSB
                </div>
                <ul className="list-inside list-disc space-y-1 text-xs text-slate-700">
                  {activeSpec.guidance_points.map((pt, idx) => (
                    <li key={idx}>{pt}</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase text-slate-700">
                  <FileText className="h-3.5 w-3.5 text-rose-600" />
                  Contraintes & Règles
                </div>
                <div className="space-y-1 text-xs text-slate-700">
                  {activeSpec.constraints.max_pages && (
                    <div>
                      • Limite de volume : max {activeSpec.constraints.max_pages} page(s)
                    </div>
                  )}
                  {activeSpec.constraints.max_references && (
                    <div>
                      • Limite bibliographique : max{" "}
                      {activeSpec.constraints.max_references} références
                    </div>
                  )}
                  {activeSpec.constraints.notes && (
                    <div>• {activeSpec.constraints.notes}</div>
                  )}
                  {!activeSpec.constraints.max_pages &&
                    !activeSpec.constraints.max_references &&
                    !activeSpec.constraints.notes && (
                      <div>
                        • Section obligatoire conforme au canevas FMSB 2025.
                      </div>
                    )}
                </div>
              </div>
            </div>
          </div>

          {/* Cover Page Form OR Section AI + Manual Editor */}
          {activeSpec.id === "cover_page" ? (
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <h4 className="mb-4 text-base font-semibold text-slate-800">
                Métadonnées officielles de la page de garde
              </h4>
              <CoverPageForm
                value={coverForm}
                onChange={setCoverForm}
                readOnly={!isAdmin}
                projectId={project.project_id}
                onProjectUpdated={onProjectUpdated}
                onError={onError}
                coDirDraft={coDirDraft}
                onCoDirDraftChange={setCoDirDraft}
              />
              {isAdmin && (
                <div className="mt-4 flex flex-wrap justify-end gap-3">
                  <button
                    type="button"
                    disabled={saving}
                    onClick={async () => {
                      const payload = finalizeCoverMetadataPayload(
                        coverForm,
                        coDirDraft
                      );
                      setCoDirDraft(emptySupervisor());
                      setCoverForm(payload);
                      await onSaveCover(payload);
                      await onSaveSection(
                        "cover_page",
                        payload.title,
                        "validated",
                        notes
                      );
                    }}
                    className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    <CheckCircle2 className="h-4 w-4" />
                    Enregistrer &amp; Valider la page de garde
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              {/* CAMES / FMSB Granular Section Specifications (all sections except cover_page) */}
              <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-3.5 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-bold text-indigo-950">
                    Référentiel CAMES / FMSB — Attendus granulaires
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {(activeSpec.constraints.min_pages != null ||
                      activeSpec.constraints.max_pages != null) && (
                      <span className="rounded-md bg-white px-2 py-0.5 text-[11px] font-semibold text-indigo-800 ring-1 ring-indigo-200">
                        Pages : {activeSpec.constraints.min_pages ?? "?"}–
                        {activeSpec.constraints.max_pages ?? "?"} p.
                      </span>
                    )}
                    {(activeSpec.constraints.min_words != null ||
                      activeSpec.constraints.max_words != null) && (
                      <span className="rounded-md bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-700 ring-1 ring-slate-200">
                        Mots : {activeSpec.constraints.min_words ?? "?"}–
                        {activeSpec.constraints.max_words ?? "?"}
                      </span>
                    )}
                    {(activeSpec.constraints.min_references != null ||
                      activeSpec.constraints.max_references != null) && (
                      <span className="rounded-md bg-white px-2 py-0.5 text-[11px] font-semibold text-emerald-800 ring-1 ring-emerald-200">
                        Réf. : {activeSpec.constraints.min_references ?? 0}–
                        {activeSpec.constraints.max_references ?? "libre"}
                      </span>
                    )}
                    <span className="rounded-md bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-700 ring-1 ring-slate-200">
                      Tableaux :{" "}
                      {activeSpec.constraints.table_policy === "required"
                        ? "Obligatoire"
                        : activeSpec.constraints.table_policy === "optional_if_justified"
                          ? "Si justifié"
                          : "Aucun"}
                    </span>
                    <span className="rounded-md bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-700 ring-1 ring-slate-200">
                      Figures :{" "}
                      {activeSpec.constraints.figure_policy === "required"
                        ? "Obligatoire"
                        : activeSpec.constraints.figure_policy === "recommended"
                          ? "Recommandée"
                          : "Non requise"}
                    </span>
                  </div>
                </div>

                {(activeSpec.cames_concepts_required?.length ?? 0) > 0 && (
                  <div className="mt-2.5 grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <div className="rounded-lg bg-white/80 p-2.5 ring-1 ring-indigo-100">
                      <div className="text-[11px] font-bold text-indigo-900">
                        Concepts CAMES à développer :
                      </div>
                      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-slate-700">
                        {activeSpec.cames_concepts_required!.map((c, idx) => (
                          <li key={idx}>{c}</li>
                        ))}
                      </ul>
                    </div>
                    {(activeSpec.concepts_to_avoid?.length ?? 0) > 0 && (
                      <div className="rounded-lg bg-white/80 p-2.5 ring-1 ring-amber-100">
                        <div className="text-[11px] font-bold text-amber-900">
                          À éviter ici (zéro redondance) :
                        </div>
                        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-slate-700">
                          {activeSpec.concepts_to_avoid!.map((c, idx) => (
                            <li key={idx}>{c}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Segments assigned to this section */}
              {sectionSegments.length > 0 && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/40 p-3">
                  <div className="mb-2 text-xs font-semibold text-amber-950">
                    Segments d'articles classés pour cette section ({sectionSegments.length}) :
                  </div>
                  <div className="max-h-40 space-y-2 overflow-y-auto pr-1 text-xs">
                    {sectionSegments.map((seg) => {
                      const ref = refById.get(seg.ref_id);
                      return (
                        <div
                          key={seg.segment_id}
                          className="flex items-start justify-between gap-2 rounded bg-white p-2 ring-1 ring-amber-100"
                        >
                          <div>
                            <span className="font-semibold text-indigo-700">
                              [Réf {ref?.order_index ?? "?"}]
                            </span>{" "}
                            {seg.epidemiology_scope && (
                              <span className="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase text-slate-600">
                                {seg.epidemiology_scope}
                              </span>
                            )}
                            <span className="text-slate-800">{seg.summary}</span>
                            {seg.key_quote_or_data && (
                              <div className="mt-0.5 text-[11px] text-slate-500">
                                Donnée : {seg.key_quote_or_data}
                              </div>
                            )}
                          </div>
                          <button
                            type="button"
                            onClick={async () => {
                              const updated = await api.toggleSegment(
                                project.project_id,
                                seg.segment_id,
                                !seg.approved
                              );
                              onProjectUpdated(updated);
                            }}
                            className={`shrink-0 rounded px-2 py-0.5 text-[10px] font-semibold ${
                              seg.approved
                                ? "bg-emerald-100 text-emerald-800"
                                : "bg-slate-200 text-slate-600"
                            }`}
                          >
                            {seg.approved ? "Inclus" : "Exclu"}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* AI Actions Bar */}
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-indigo-100 bg-indigo-50/60 px-3.5 py-2.5">
                <span className="text-xs text-indigo-950">
                  <strong>Assistant IA :</strong> rédaction sourcée sans hallucination & audit FMSB
                </span>
                <div className="flex flex-wrap gap-2">
                  {(activeSpec.id === "toc" ||
                    activeSpec.id === "abbreviations" ||
                    activeSpec.id === "list_of_tables" ||
                    activeSpec.id === "list_of_figures") && (
                    <button
                      type="button"
                      disabled={busyToc}
                      onClick={handleGenerateAutoToc}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#571f1d] disabled:opacity-50"
                    >
                      {busyToc ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <FileText className="h-3.5 w-3.5" />
                      )}
                      {busyToc
                        ? "Génération..."
                        : activeSpec.id === "abbreviations"
                          ? "Unifier les Abréviations (1 tableau A-Z sans doublons)"
                          : activeSpec.id === "list_of_tables"
                            ? "Générer la Liste des tableaux (sans doublons + pages A4)"
                            : activeSpec.id === "list_of_figures"
                              ? "Générer la Liste des figures (sans doublons + pages A4)"
                              : "Générer / Actualiser TDM & Listes automatiques (Word FMSB)"}
                    </button>
                  )}
                  {activeSpec.id === "references" && (
                    <button
                      type="button"
                      disabled={busyRefs}
                      onClick={handleValidateVancouverReferences}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#571f1d] disabled:opacity-50"
                    >
                      {busyRefs ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <ShieldCheck className="h-3.5 w-3.5" />
                      )}
                      {busyRefs
                        ? "Validation Vancouver..."
                        : "Valider & Formater en Liste Vancouver"}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busyDraft}
                    onClick={() => handleDraftWithAI()}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {busyDraft ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Sparkles className="h-3.5 w-3.5" />
                    )}
                    {busyDraft ? "Rédaction..." : "Rédiger cette étape"}
                  </button>
                  <button
                    type="button"
                    disabled={busyAudit}
                    onClick={handleAuditWithAI}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    {busyAudit ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
                    )}
                    Auditer conformité FMSB
                  </button>
                  {![
                    "toc",
                    "abbreviations",
                    "list_of_tables",
                    "list_of_figures",
                    "references",
                  ].includes(activeSpec.id) && (
                    <button
                      type="button"
                      disabled={busyHumanize || !draft.trim()}
                      onClick={handleHumanizeWithAI}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-50"
                      title="Réécrire cette section en plume médicale naturelle FMSB tout en conservant 100 % des citations [n] et chiffres"
                    >
                      {busyHumanize ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Feather className="h-3.5 w-3.5 text-amber-700" />
                      )}
                      {busyHumanize ? "Humanisation..." : "Humaniser le style"}
                    </button>
                  )}
                  {![
                    "toc",
                    "abbreviations",
                    "list_of_tables",
                    "list_of_figures",
                  ].includes(activeSpec.id) && (
                    <button
                      type="button"
                      disabled={busyAntigravity}
                      onClick={() => handleMobilizeAntigravity()}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-rose-300 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-[#6C2826] hover:bg-rose-100 disabled:opacity-50"
                      title="Rechercher des références Vancouver et figures pertinentes pour cette section"
                    >
                      {busyAntigravity ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Globe className="h-3.5 w-3.5" />
                      )}
                      {busyAntigravity
                        ? "Enrichissement en cours..."
                        : "Enrichir (Réf. Vancouver & Figures)"}
                    </button>
                  )}
                </div>
              </div>

              {/* Cas A : Section courante ciblée par une alerte de cohérence inter-sections */}
              {activeSectionAlerts.map((alert) => (
                <div
                  key={alert.alert_id}
                  className="rounded-xl border border-amber-300 bg-gradient-to-r from-amber-50 to-orange-50/60 p-3.5 text-xs text-amber-950 shadow-xs"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
                      <div>
                        <div className="font-bold text-amber-950">{alert.title}</div>
                        <p className="mt-1 leading-relaxed text-amber-900">
                          {alert.message}
                        </p>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-amber-800">
                          <span>
                            Source de divergence :{" "}
                            <strong>{alert.source_section_title}</strong>
                          </span>
                          {alert.suggested_study_type && (
                            <span className="rounded bg-white px-2 py-0.5 font-semibold text-amber-900 ring-1 ring-amber-200">
                              Qualification proposée : {alert.suggested_study_type}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={busyHarmonize}
                      onClick={() =>
                        handleHarmonizeSection(alert.target_section_id, alert.alert_id)
                      }
                      className="inline-flex min-h-[38px] shrink-0 items-center gap-1.5 rounded-lg bg-amber-700 px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-amber-800 disabled:opacity-50"
                    >
                      {busyHarmonize ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                      {busyHarmonize
                        ? "Harmonisation en cours..."
                        : "Reformuler automatiquement cette section pour l'harmoniser"}
                    </button>
                  </div>
                </div>
              ))}

              {/* Cas B : Autres sections déjà rédigées impactées par une modification récente */}
              {otherSectionAlerts.length > 0 && (
                <div className="rounded-xl border border-indigo-200 bg-indigo-50/60 p-3 text-xs text-indigo-950">
                  <div className="font-semibold text-indigo-900">
                    Impact détecté sur d'autres sections déjà rédigées :
                  </div>
                  <div className="mt-2 space-y-2">
                    {otherSectionAlerts.map((alert) => (
                      <div
                        key={alert.alert_id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-indigo-100 bg-white px-3 py-2"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="font-semibold text-slate-900">
                            {alert.target_section_title}
                          </div>
                          <div className="text-[11px] text-slate-600">{alert.title}</div>
                        </div>
                        <button
                          type="button"
                          onClick={() => onSelectSection(alert.target_section_id)}
                          className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-indigo-700"
                        >
                          <span>Examiner &amp; harmoniser</span>
                          <ArrowRight className="h-3 w-3" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {lastHarmonizeNote && (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-950">
                  <CheckCircle2 className="mr-1.5 inline h-4 w-4 text-emerald-700" />
                  <strong>Cohérence rétablie :</strong> {lastHarmonizeNote}
                </div>
              )}

              {activeSpec.id === "references" && lastRefDecisionNote && (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50/70 p-3 text-xs text-emerald-950">
                  <strong>Validation Vancouver :</strong> {lastRefDecisionNote}
                </div>
              )}

              {lastDraftResult && (
                <div className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-3 text-xs text-indigo-950">
                  <strong>Assistant méthodologique FMSB :</strong>{" "}
                  {lastDraftResult.assistant_commentary}
                </div>
              )}

              {lastHumanizeResult && !lastHumanizeResult.skipped && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/70 p-3 text-xs text-amber-950">
                  <div className="flex flex-wrap items-center justify-between gap-2 font-semibold">
                    <span>Humanisation académique FMSB</span>
                    <span className="rounded-full bg-emerald-700 px-2 py-0.5 text-[11px] text-white">
                      Style naturel : {lastHumanizeResult.before_metrics.human_style_score} % →{" "}
                      {lastHumanizeResult.after_metrics.human_style_score} %
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-amber-900">
                    {lastHumanizeResult.style_adjustments_summary} · Tournures IA éliminées :{" "}
                    <strong>{lastHumanizeResult.cliches_removed}</strong> · Citations [n] :{" "}
                    <strong>
                      {lastHumanizeResult.citations_preserved ? "100 % préservées" : "À vérifier"}
                    </strong>
                  </p>
                </div>
              )}

              {(activeState?.audit_notes?.length > 0 || lastAuditResult) && (
                <div
                  className={`rounded-lg border p-3 text-xs ${
                    lastAuditResult?.compliant
                      ? "border-emerald-200 bg-emerald-50 text-emerald-950"
                      : "border-rose-200 bg-rose-50 text-rose-950"
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-semibold">
                      {lastAuditResult?.compliant
                        ? "Audit FMSB conforme"
                        : "Points de vigilance / Audit FMSB :"}
                    </div>
                    {(!lastAuditResult?.compliant ||
                      activeState?.audit_notes?.length > 0) && (
                      <button
                        type="button"
                        disabled={busyFix}
                        onClick={handleFixWithAI}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-rose-700 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-rose-800 disabled:opacity-50"
                      >
                        {busyFix ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Wand2 className="h-3.5 w-3.5" />
                        )}
                        {busyFix
                          ? "Correction en cours..."
                          : "Autoriser l'IA à corriger ces manquements"}
                      </button>
                    )}
                  </div>
                  <ul className="mt-1 list-inside list-disc space-y-0.5">
                    {(lastAuditResult?.issues ?? activeState.audit_notes).map(
                      (n, i) => (
                        <li key={i}>
                          {n
                            .replace(/\s+par\s+Gemini Decision 1\s*\([^)]*\)/gi, "")
                            .replace(/\bAntigravity\b/gi, "L'assistant documentaire")}
                        </li>
                      )
                    )}
                  </ul>
                  {lastAuditResult?.recommendations &&
                    lastAuditResult.recommendations.length > 0 && (
                      <div className="mt-1.5 text-slate-700">
                        <strong>Suggestions :</strong>{" "}
                        {lastAuditResult.recommendations.join(" · ")}
                      </div>
                    )}
                </div>
              )}

              <div>
                <label className="block text-sm font-semibold text-slate-800 mb-1.5">
                  Contenu de la section (Éditeur riche FMSB)
                </label>
                <RichTextEditor
                  contentHtml={draftHtml}
                  contentMarkdown={draft}
                  onChange={(html, text) => {
                    setDraftHtml(html);
                    setDraft(text);
                  }}
                />
              </div>

              <SectionMediaPanel
                project={project}
                sectionId={activeSpec.id}
                sectionText={draft}
                sectionHtml={draftHtml}
                onProjectUpdated={onProjectUpdated}
                onError={onError}
              />

              {/* Dialogue / Iterative Revision Box */}
              <div className="flex flex-wrap gap-2">
                <input
                  type="text"
                  value={aiInstruction}
                  onChange={(e) => setAiInstruction(e.target.value)}
                  placeholder="Discuter avec l'assistant : ex. « Raccourcis l'introduction à 500 mots et garde 4 références »..."
                  className="min-w-[240px] flex-1 rounded-lg border border-slate-300 px-3 py-2 text-xs focus:border-indigo-500 focus:outline-none"
                />
                <button
                  type="button"
                  disabled={busyDraft || !aiInstruction.trim()}
                  onClick={() => handleDraftWithAI(aiInstruction)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-3.5 py-2 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
                >
                  <MessageSquarePlus className="h-3.5 w-3.5" />
                  Appliquer l'amélioration
                </button>
              </div>

              <div>
                <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Notes personnelles de l'étudiant
                </label>
                <textarea
                  rows={2}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Notes internes..."
                  className="mt-1 w-full rounded-lg border border-slate-200 p-2.5 text-xs focus:border-indigo-500 focus:outline-none"
                />
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-xs text-slate-500">
                  {draft.trim() ? `${draft.trim().split(/\s+/).length} mots` : "0 mot"}
                </span>
                <div className="flex flex-wrap gap-2.5">
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() =>
                      onSaveSection(activeSpec.id, draft, "in_progress", notes, draftHtml)
                    }
                    className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    Enregistrer le brouillon
                  </button>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() =>
                      onSaveSection(activeSpec.id, draft, "validated", notes, draftHtml)
                    }
                    className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    <CheckCircle2 className="h-4 w-4" />
                    Valider & Envoyer au Module 2
                  </button>
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
