import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
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
import { usePipelineTracker } from "../hooks/usePipelineTracker";
import { useLanguage } from "../i18n/LanguageContext";
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
import { PipelineProgressWidget } from "./PipelineProgressWidget";
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
  isVisible?: boolean;
}

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
  isVisible = true,
}: Module1Props) {
  const { lang, t } = useLanguage();
  const tracker = usePipelineTracker(lang);

  const statusBadges: Record<StepStatus, { label: string; badgeClass: string }> = {
    pending: {
      label: t("m1.status_pending"),
      badgeClass: "bg-slate-100 text-slate-600",
    },
    in_progress: {
      label: t("m1.status_in_progress"),
      badgeClass: "bg-amber-100 text-amber-800",
    },
    needs_revision: {
      label: t("m1.status_needs_revision"),
      badgeClass: "bg-rose-100 text-rose-800",
    },
    validated: {
      label: t("m1.status_validated"),
      badgeClass: "bg-emerald-100 text-emerald-800",
    },
  };

  const activeSpec =
    canvas.find((s) => s.id === selectedSectionId) ?? canvas[0];
  const activeState = project.sections[activeSpec.id];
  const selectedSectionIdRef = useRef(activeSpec.id);
  selectedSectionIdRef.current = activeSpec.id;

  const [draft, setDraft] = useState(activeState?.content_markdown ?? "");
  const [draftHtml, setDraftHtml] = useState(activeState?.content_html ?? "");
  const [notes, setNotes] = useState(activeState?.user_notes ?? "");
  const [mobileStepsOpen, setMobileStepsOpen] = useState(false);
  const [showMethodGuide, setShowMethodGuide] = useState(false);
  const [showCamesRequirements, setShowCamesRequirements] = useState(false);
  const [showAssignedSegments, setShowAssignedSegments] = useState(false);
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
    const targetSectionId = activeSpec.id;
    setBusyToc(true);
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "generate_toc",
          titleFr: "Génération TDM & listes",
          titleEn: "TOC & Lists Generation",
          projectId: project.project_id,
        },
        (taskId) => api.generateToc(project.project_id, taskId)
      );
      onProjectUpdated(res.project);
      if (selectedSectionIdRef.current === targetSectionId) {
        const updatedTarget = res.project.sections[targetSectionId] ?? res.project.sections.toc;
        if (updatedTarget) {
          setDraft(updatedTarget.content_markdown);
          setDraftHtml(updatedTarget.content_html);
        }
      }
    } catch (e: unknown) {
      onError(
        e instanceof Error
          ? e.message
          : lang === "en"
          ? "Unable to automatically generate the FMSB list or Table of Contents."
          : "Impossible de générer automatiquement la liste ou le sommaire FMSB."
      );
    } finally {
      setBusyToc(false);
    }
  };

  const handleValidateVancouverReferences = async () => {
    const targetSectionId = activeSpec.id;
    setBusyRefs(true);
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "validate_refs",
          titleFr: "Validation Vancouver ICMJE",
          titleEn: "Vancouver ICMJE Validation",
          projectId: project.project_id,
        },
        (taskId) => api.validateVancouverReferences(project.project_id, taskId)
      );
      onProjectUpdated(res.project);
      if (selectedSectionIdRef.current === targetSectionId) {
        const updatedRef = res.project.sections.references;
        if (updatedRef) {
          setDraft(updatedRef.content_markdown);
          setDraftHtml(updatedRef.content_html);
        }
        setLastRefDecisionNote(
          lang === "en"
            ? `${res.references_count} numbered references (${res.corrected_count} normalized) — ${res.decision_summary}`
            : `${res.references_count} références numérotées (${res.corrected_count} normalisées) — ${res.decision_summary}`
        );
      }
    } catch (e: unknown) {
      onError(
        e instanceof Error
          ? e.message
          : lang === "en"
          ? "Unable to validate references in Vancouver format."
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
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusyDraft(true);
    onError("");
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "draft",
          titleFr: `Rédaction : ${targetTitle}`,
          titleEn: `Drafting: ${targetTitle}`,
          projectId: project.project_id,
        },
        (taskId) =>
          api.draftSection(project.project_id, targetSectionId, instruction, taskId)
      );
      onProjectUpdated(res.project);
      if (selectedSectionIdRef.current === targetSectionId) {
        setDraft(res.result.content_markdown);
        setDraftHtml(
          res.result.content_html ??
            res.project.sections[targetSectionId]?.content_html ??
            ""
        );
        setLastDraftResult(res.result);
        setAiInstruction("");
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyDraft(false);
    }
  }

  async function handleAuditWithAI() {
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusyAudit(true);
    onError("");
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "audit",
          titleFr: `Audit FMSB : ${targetTitle}`,
          titleEn: `FMSB Audit: ${targetTitle}`,
          projectId: project.project_id,
        },
        (taskId) => api.auditSection(project.project_id, targetSectionId, taskId)
      );
      onProjectUpdated(res.project);
      if (selectedSectionIdRef.current === targetSectionId) {
        setLastAuditResult(res.result);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAudit(false);
    }
  }

  async function handleMobilizeAntigravity(instruction?: string) {
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusyAntigravity(true);
    onError("");
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "antigravity",
          titleFr: `Enrichissement web : ${targetTitle}`,
          titleEn: `Web Enrichment: ${targetTitle}`,
          projectId: project.project_id,
        },
        (taskId) =>
          api.runSectionAntigravity(
            project.project_id,
            targetSectionId,
            instruction ?? aiInstruction,
            true,
            taskId
          )
      );
      onProjectUpdated(res.project);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAntigravity(false);
    }
  }

  async function handleFixWithAI() {
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusyFix(true);
    onError("");
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "fix",
          titleFr: `Correction audit : ${targetTitle}`,
          titleEn: `Audit Fix: ${targetTitle}`,
          projectId: project.project_id,
        },
        (taskId) => api.fixSection(project.project_id, targetSectionId, taskId)
      );
      onProjectUpdated(res.project);
      if (selectedSectionIdRef.current === targetSectionId) {
        setDraft(res.result.content_markdown);
        setDraftHtml(
          res.result.content_html ??
            res.project.sections[targetSectionId]?.content_html ??
            ""
        );
        setLastDraftResult(res.result);
        setLastAuditResult(null);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyFix(false);
    }
  }

  async function handleHumanizeWithAI() {
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusyHumanize(true);
    onError("");
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "humanize",
          titleFr: `Humanisation : ${targetTitle}`,
          titleEn: `Humanization: ${targetTitle}`,
          projectId: project.project_id,
        },
        (taskId) =>
          api.humanizeSection(project.project_id, targetSectionId, true, taskId)
      );
      onProjectUpdated(res.project);
      if (selectedSectionIdRef.current === targetSectionId) {
        setDraft(res.result.content_markdown);
        setDraftHtml(
          res.result.content_html ??
            res.project.sections[targetSectionId]?.content_html ??
            ""
        );
        setLastHumanizeResult(res.result);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyHumanize(false);
    }
  }

  async function handleHarmonizeSection(targetSectionId: string, alertId?: string) {
    const targetTitle = canvas.find((c) => c.id === targetSectionId)?.title ?? activeSpec.title;
    setBusyHarmonize(true);
    onError("");
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "harmonize",
          titleFr: `Harmonisation : ${targetTitle}`,
          titleEn: `Harmonization: ${targetTitle}`,
          projectId: project.project_id,
        },
        (taskId) =>
          api.harmonizeSection(project.project_id, targetSectionId, alertId, taskId)
      );
      onProjectUpdated(res.project);
      if (selectedSectionIdRef.current === targetSectionId) {
        setDraft(res.result.content_markdown);
        setDraftHtml(
          res.result.content_html ??
            res.project.sections[targetSectionId]?.content_html ??
            ""
        );
        setLastHarmonizeNote(res.result.message);
      }
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
    <>
      <div
        data-testid="module1-workspace-root"
        className={isVisible ? "space-y-4 sm:space-y-5" : "hidden"}
      >
      {/* Top Research / Import / References Panel */}
      <ResearchPanel
        project={project}
        onProjectUpdated={onProjectUpdated}
        onError={onError}
        runWithProgress={tracker.runWithProgress}
      />

      {/* Mobile Sticky Step Selector Bar (< lg) */}
      <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 shadow-sm lg:hidden">
        <div className="min-w-0">
          <div className="truncate text-[10px] font-bold uppercase tracking-wider text-indigo-600">
            {activeSpec.chapter_title} ·{" "}
            {t("m1.validated_count", { done: validatedCount, total: canvas.length })}
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
          {t("m1.steps_title", { count: canvas.length })}
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
                  {t("m1.steps_title", { count: canvas.length })}
                </h2>
                <span className="text-xs font-semibold text-indigo-700">
                  {t("m1.validated_count", { done: validatedCount, total: canvas.length })}
                </span>
              </div>
              <button
                type="button"
                aria-label={t("nav.close")}
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
                {t("m1.steps_title", { count: canvas.length })}
              </h2>
              <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
                {t("m1.validated_count", { done: validatedCount, total: canvas.length })}
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
          {/* Section Header + Collapsible FMSB Constraints */}
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
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
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <button
                  type="button"
                  data-testid="toggle-method-guide"
                  aria-expanded={showMethodGuide}
                  onClick={() => setShowMethodGuide((v) => !v)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-700 transition hover:bg-slate-100"
                >
                  <BookOpen className="h-3.5 w-3.5 text-indigo-600" />
                  <span>
                    {showMethodGuide
                      ? lang === "en"
                        ? "Hide guide & rules"
                        : "Masquer guide & règles"
                      : lang === "en"
                      ? "Methodological guide & rules"
                      : "Guide méthodologique & règles"}
                  </span>
                  {showMethodGuide ? (
                    <ChevronUp className="h-3.5 w-3.5 text-slate-500" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5 text-slate-500" />
                  )}
                </button>
                <span
                  className={`rounded-full px-3 py-1 text-xs font-semibold ${
                    statusBadges[activeState?.status ?? "pending"].badgeClass
                  }`}
                >
                  {statusBadges[activeState?.status ?? "pending"].label}
                </span>
              </div>
            </div>

            {showMethodGuide && (
              <div className="mt-4 grid grid-cols-1 gap-3 rounded-lg bg-slate-50 p-3.5 md:grid-cols-2">
                <div>
                  <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase text-slate-700">
                    <BookOpen className="h-3.5 w-3.5 text-indigo-600" />
                    {t("m1.guidance_title")}
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
                    {lang === "en" ? "Constraints & Rules" : "Contraintes & Règles"}
                  </div>
                  <div className="space-y-1 text-xs text-slate-700">
                    {activeSpec.constraints.max_pages && (
                      <div>
                        • {lang === "en" ? "Length limit: max" : "Limite de volume : max"} {activeSpec.constraints.max_pages} {lang === "en" ? "page(s)" : "page(s)"}
                      </div>
                    )}
                    {activeSpec.constraints.max_references && (
                      <div>
                        • {lang === "en" ? "Reference cap: max" : "Limite bibliographique : max"}{" "}
                        {activeSpec.constraints.max_references} {lang === "en" ? "references" : "références"}
                      </div>
                    )}
                    {activeSpec.constraints.notes && (
                      <div>• {activeSpec.constraints.notes}</div>
                    )}
                    {!activeSpec.constraints.max_pages &&
                      !activeSpec.constraints.max_references &&
                      !activeSpec.constraints.notes && (
                        <div>
                          • {lang === "en" ? "Mandatory section compliant with the FMSB/CAMES canvas." : "Section obligatoire conforme au canevas FMSB 2025."}
                        </div>
                      )}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Cover Page Form OR Section AI + Manual Editor */}
          {activeSpec.id === "cover_page" ? (
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <h4 className="mb-4 text-base font-semibold text-slate-800">
                {lang === "en" ? "Official Cover Page Metadata" : "Métadonnées officielles de la page de garde"}
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
                    {lang === "en" ? "Save & Validate Cover Page" : "Enregistrer & Valider la page de garde"}
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              {/* Collapsible CAMES / FMSB Granular Section Specifications (collapsed by default) */}
              <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 px-3.5 py-2.5 text-xs">
                <button
                  type="button"
                  data-testid="toggle-cames-requirements"
                  aria-expanded={showCamesRequirements}
                  onClick={() => setShowCamesRequirements((v) => !v)}
                  className="flex w-full flex-wrap items-center justify-between gap-2 text-left"
                >
                  <span className="inline-flex items-center gap-1.5 font-bold text-indigo-950">
                    {showCamesRequirements ? (
                      <ChevronUp className="h-4 w-4 text-indigo-700" />
                    ) : (
                      <ChevronDown className="h-4 w-4 text-indigo-700" />
                    )}
                    {lang === "en"
                      ? "CAMES / FMSB Framework — Granular Requirements"
                      : "Référentiel CAMES / FMSB — Attendus granulaires"}
                  </span>
                  <div className="flex flex-wrap items-center gap-1.5">
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
                        {lang === "en" ? "Words" : "Mots"} : {activeSpec.constraints.min_words ?? "?"}–
                        {activeSpec.constraints.max_words ?? "?"}
                      </span>
                    )}
                    {(activeSpec.constraints.min_references != null ||
                      activeSpec.constraints.max_references != null) && (
                      <span className="rounded-md bg-white px-2 py-0.5 text-[11px] font-semibold text-emerald-800 ring-1 ring-emerald-200">
                        {lang === "en" ? "Refs" : "Réf."} : {activeSpec.constraints.min_references ?? 0}–
                        {activeSpec.constraints.max_references ?? (lang === "en" ? "open" : "libre")}
                      </span>
                    )}
                    <span className="rounded-md bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-700 ring-1 ring-slate-200">
                      {lang === "en" ? "Tables" : "Tableaux"} :{" "}
                      {activeSpec.constraints.table_policy === "required"
                        ? lang === "en" ? "Required" : "Obligatoire"
                        : activeSpec.constraints.table_policy === "optional_if_justified"
                          ? lang === "en" ? "If justified" : "Si justifié"
                          : lang === "en" ? "None" : "Aucun"}
                    </span>
                    <span className="rounded-md bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-700 ring-1 ring-slate-200">
                      Figures :{" "}
                      {activeSpec.constraints.figure_policy === "required"
                        ? lang === "en" ? "Required" : "Obligatoire"
                        : activeSpec.constraints.figure_policy === "recommended"
                          ? lang === "en" ? "Recommended" : "Recommandée"
                          : lang === "en" ? "Not required" : "Non requise"}
                    </span>
                    <span className="ml-1 text-[11px] font-semibold text-indigo-700 underline decoration-dotted">
                      {showCamesRequirements
                        ? lang === "en" ? "Collapse" : "Réduire"
                        : lang === "en" ? "Expand" : "Détails"}
                    </span>
                  </div>
                </button>

                {showCamesRequirements && (activeSpec.cames_concepts_required?.length ?? 0) > 0 && (
                  <div className="mt-2.5 grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <div className="rounded-lg bg-white/80 p-2.5 ring-1 ring-indigo-100">
                      <div className="text-[11px] font-bold text-indigo-900">
                        {lang === "en" ? "Required CAMES Concepts:" : "Concepts CAMES à développer :"}
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
                          {lang === "en" ? "Avoid here (no redundancy):" : "À éviter ici (zéro redondance) :"}
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

              {/* Segments assigned to this section (collapsible, collapsed by default) */}
              {sectionSegments.length > 0 && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/40 px-3 py-2">
                  <button
                    type="button"
                    data-testid="toggle-assigned-segments"
                    aria-expanded={showAssignedSegments}
                    onClick={() => setShowAssignedSegments((v) => !v)}
                    className="flex w-full items-center justify-between gap-2 text-left text-xs font-semibold text-amber-950"
                  >
                    <span className="inline-flex items-center gap-1.5">
                      {showAssignedSegments ? (
                        <ChevronUp className="h-3.5 w-3.5 text-amber-800" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5 text-amber-800" />
                      )}
                      {lang === "en"
                        ? `Article segments assigned to this section (${sectionSegments.length})`
                        : `Segments d'articles classés pour cette section (${sectionSegments.length})`}
                    </span>
                    <span className="text-[11px] font-medium text-amber-800 underline decoration-dotted">
                      {showAssignedSegments
                        ? lang === "en" ? "Hide" : "Masquer"
                        : lang === "en" ? "Show" : "Afficher"}
                    </span>
                  </button>
                  {showAssignedSegments && (
                    <div className="mt-2 max-h-40 space-y-2 overflow-y-auto pr-1 text-xs">
                      {sectionSegments.map((seg) => {
                        const ref = refById.get(seg.ref_id);
                        return (
                          <div
                            key={seg.segment_id}
                            className="flex items-start justify-between gap-2 rounded bg-white p-2 ring-1 ring-amber-100"
                          >
                            <div>
                              <span className="font-semibold text-indigo-700">
                                [Ref {ref?.order_index ?? "?"}]
                              </span>{" "}
                              {seg.epidemiology_scope && (
                                <span className="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase text-slate-600">
                                  {seg.epidemiology_scope}
                                </span>
                              )}
                              <span className="text-slate-800">{seg.summary}</span>
                              {seg.key_quote_or_data && (
                                <div className="mt-0.5 text-[11px] text-slate-500">
                                  {lang === "en" ? "Data:" : "Donnée :"} {seg.key_quote_or_data}
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
                              {seg.approved
                                ? lang === "en" ? "Included" : "Inclus"
                                : lang === "en" ? "Excluded" : "Exclu"}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* AI Actions Bar */}
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-indigo-100 bg-indigo-50/60 px-3.5 py-2.5">
                <span className="text-xs text-indigo-950">
                  <strong>{lang === "en" ? "AI Assistant:" : "Assistant IA :"}</strong>{" "}
                  {lang === "en"
                    ? "evidence-grounded drafting without hallucination & FMSB audit"
                    : "rédaction sourcée sans hallucination & audit FMSB"}
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
                        ? lang === "en"
                          ? "Generating..."
                          : "Génération..."
                        : t("m1.generate_toc")}
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
                        ? lang === "en"
                          ? "Validating Vancouver..."
                          : "Validation Vancouver..."
                        : t("m1.validate_refs")}
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
                    {busyDraft ? t("m1.drafting") : t("m1.draft_ai")}
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
                    {busyAudit ? t("m1.auditing") : t("m1.audit_ai")}
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
                      title={
                        lang === "en"
                          ? "Rewrite this section in natural academic medical prose while preserving 100% of citations [n] and figures"
                          : "Réécrire cette section en plume médicale naturelle FMSB tout en conservant 100 % des citations [n] et chiffres"
                      }
                    >
                      {busyHumanize ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Feather className="h-3.5 w-3.5 text-amber-700" />
                      )}
                      {busyHumanize ? t("m1.humanizing") : t("m1.humanize")}
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
                      title={
                        lang === "en"
                          ? "Search Vancouver references and relevant figures for this section"
                          : "Rechercher des références Vancouver et figures pertinentes pour cette section"
                      }
                    >
                      {busyAntigravity ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Globe className="h-3.5 w-3.5" />
                      )}
                      {busyAntigravity ? t("m1.enriching") : t("m1.enrich_web")}
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
                            {lang === "en" ? "Divergence source:" : "Source de divergence :"}{" "}
                            <strong>{alert.source_section_title}</strong>
                          </span>
                          {alert.suggested_study_type && (
                            <span className="rounded bg-white px-2 py-0.5 font-semibold text-amber-900 ring-1 ring-amber-200">
                              {lang === "en" ? "Suggested design:" : "Qualification proposée :"}{" "}
                              {alert.suggested_study_type}
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
                        ? lang === "en"
                          ? "Harmonizing..."
                          : "Harmonisation en cours..."
                        : t("m1.harmonize_btn")}
                    </button>
                  </div>
                </div>
              ))}

              {/* Cas B : Autres sections déjà rédigées impactées par une modification récente */}
              {otherSectionAlerts.length > 0 && (
                <div className="rounded-xl border border-indigo-200 bg-indigo-50/60 p-3 text-xs text-indigo-950">
                  <div className="font-semibold text-indigo-900">
                    {t("m1.coherence_title")}
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
                          <span>{lang === "en" ? "Review & harmonize" : "Examiner & harmoniser"}</span>
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
                  <strong>{lang === "en" ? "Coherence restored:" : "Cohérence rétablie :"}</strong>{" "}
                  {lastHarmonizeNote}
                </div>
              )}

              {activeSpec.id === "references" && lastRefDecisionNote && (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50/70 p-3 text-xs text-emerald-950">
                  <strong>{lang === "en" ? "Vancouver Validation:" : "Validation Vancouver :"}</strong>{" "}
                  {lastRefDecisionNote.replace(/gemini[^,.;)]*/gi, "Smart Research")}
                </div>
              )}

              {lastDraftResult && (
                <div className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-3 text-xs text-indigo-950">
                  <strong>
                    {lang === "en" ? "Methodological Assistant:" : "Assistant méthodologique FMSB :"}
                  </strong>{" "}
                  {lastDraftResult.assistant_commentary.replace(/gemini[^,.;)]*/gi, "Smart Research")}
                </div>
              )}

              {lastHumanizeResult && !lastHumanizeResult.skipped && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/70 p-3 text-xs text-amber-950">
                  <div className="flex flex-wrap items-center justify-between gap-2 font-semibold">
                    <span>
                      {lang === "en" ? "Academic Style Humanization" : "Humanisation académique FMSB"}
                    </span>
                    <span className="rounded-full bg-emerald-700 px-2 py-0.5 text-[11px] text-white">
                      {lang === "en" ? "Natural style:" : "Style naturel :"}{" "}
                      {lastHumanizeResult.before_metrics.human_style_score} % →{" "}
                      {lastHumanizeResult.after_metrics.human_style_score} %
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-amber-900">
                    {lastHumanizeResult.style_adjustments_summary} ·{" "}
                    {lang === "en" ? "AI clichés removed:" : "Tournures IA éliminées :"}{" "}
                    <strong>{lastHumanizeResult.cliches_removed}</strong> · Citations [n] :{" "}
                    <strong>
                      {lastHumanizeResult.citations_preserved
                        ? lang === "en" ? "100% preserved" : "100 % préservées"
                        : lang === "en" ? "Check required" : "À vérifier"}
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
                        ? lang === "en" ? "FMSB Audit Passed" : "Audit FMSB conforme"
                        : lang === "en" ? "Audit Findings:" : "Points de vigilance / Audit FMSB :"}
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
                        {busyFix ? t("m1.fixing") : t("m1.fix_ai")}
                      </button>
                    )}
                  </div>
                  <ul className="mt-1 list-inside list-disc space-y-0.5">
                    {(lastAuditResult?.issues ?? activeState.audit_notes).map(
                      (n, i) => (
                        <li key={i}>
                          {n
                            .replace(/\s+par\s+gemini[^()]*\([^)]*\)/gi, "")
                            .replace(/gemini[^,.;)]*/gi, "Smart Research")
                            .replace(/\bAntigravity\b/gi, lang === "en" ? "Research assistant" : "L'assistant documentaire")}
                        </li>
                      )
                    )}
                  </ul>
                  {lastAuditResult?.recommendations &&
                    lastAuditResult.recommendations.length > 0 && (
                      <div className="mt-1.5 text-slate-700">
                        <strong>{lang === "en" ? "Suggestions:" : "Suggestions :"}</strong>{" "}
                        {lastAuditResult.recommendations.join(" · ").replace(/gemini[^,.;)]*/gi, "Smart Research")}
                      </div>
                    )}
                </div>
              )}

              <div>
                <label className="block text-sm font-semibold text-slate-800 mb-1.5">
                  {lang === "en"
                    ? "Section Content (Rich Text Editor)"
                    : "Contenu de la section (Éditeur riche FMSB)"}
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
                  placeholder={t("m1.instruction_placeholder")}
                  className="min-w-[240px] flex-1 rounded-lg border border-slate-300 px-3 py-2 text-xs focus:border-indigo-500 focus:outline-none"
                />
                <button
                  type="button"
                  disabled={busyDraft || !aiInstruction.trim()}
                  onClick={() => handleDraftWithAI(aiInstruction)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-3.5 py-2 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
                >
                  <MessageSquarePlus className="h-3.5 w-3.5" />
                  {lang === "en" ? "Apply instruction" : "Appliquer l'amélioration"}
                </button>
              </div>

              <div>
                <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {lang === "en" ? "Candidate Personal Notes" : "Notes personnelles de l'étudiant"}
                </label>
                <textarea
                  rows={2}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder={lang === "en" ? "Internal notes..." : "Notes internes..."}
                  className="mt-1 w-full rounded-lg border border-slate-200 p-2.5 text-xs focus:border-indigo-500 focus:outline-none"
                />
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-xs text-slate-500">
                  {draft.trim()
                    ? `${draft.trim().split(/\s+/).length} ${lang === "en" ? "words" : "mots"}`
                    : lang === "en"
                    ? "0 words"
                    : "0 mot"}
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
                    {t("m1.save_draft")}
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
                    {t("m1.validate_step")}
                  </button>
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
      </div>

      {/* Real-Time Pipeline Steps Utility (kept visible even across module switches) */}
      <PipelineProgressWidget
        task={tracker.activeTask}
        elapsedSec={tracker.localElapsedSec}
        visible={tracker.visible}
        onDismiss={tracker.dismiss}
      />
    </>
  );
}
