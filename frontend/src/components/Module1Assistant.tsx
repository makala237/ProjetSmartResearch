import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Circle,
  Clock,
  Feather,
  FileText,
  FolderPlus,
  Globe,
  History,
  Loader2,
  Menu,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
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
  SectionVersionEntry,
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
import {
  ReformulationReviewBanner,
  type PendingReformulationPreview,
} from "./ReformulationReviewBanner";
import { ResearchPanel } from "./ResearchPanel";
import { RichTextEditor, type SelectionTransformAction } from "./RichTextEditor";
import { SectionMediaPanel } from "./SectionMediaPanel";
import {
  extractInvestigatorPlaceholders,
  splitTextWithPlaceholders,
} from "../utils/investigatorPlaceholders";

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
  ) => Promise<boolean | void>;
  onSaveCover: (cover: CoverMetadata) => Promise<void>;
  onOpenModule2?: () => void;
  onError: (msg: string) => void;
  saving: boolean;
  isAdmin?: boolean;
  isVisible?: boolean;
  onFeatureUsed?: (featureId: string) => void;
}

const ALGORITHMIC_SECTION_IDS = new Set([
  "toc",
  "abbreviations",
  "list_of_tables",
  "list_of_figures",
  "annexes",
  "references",
]);
const AUTO_SYNC_SECTION_IDS = new Set([
  "toc",
  "abbreviations",
  "list_of_tables",
  "list_of_figures",
  "annexes",
]);
const SUB_ANNEX_SECTION_IDS = new Set([
  "annexe_notice_information",
  "annexe_consentement",
  "annexe_autorisation_recherche",
  "annexe_clairance_ethique",
  "annexe_confidentialite",
  "annexe_engagement",
]);
const NON_MEDIA_SECTION_IDS = new Set([
  "toc",
  "abbreviations",
  "list_of_tables",
  "list_of_figures",
  "references",
  "annexe_notice_information",
  "annexe_consentement",
  "annexe_fiche_collecte",
  "annexe_autorisation_recherche",
  "annexe_clairance_ethique",
  "annexe_confidentialite",
  "annexe_engagement",
  "annexes",
]);

function renderHighlightedSegmentText(text: string) {
  const parts = splitTextWithPlaceholders(text);
  return parts.map((pt, i) =>
    pt.isPlaceholder ? (
      <mark key={i} className="sr-placeholder-highlight">
        {pt.text}
      </mark>
    ) : (
      <span key={i}>{pt.text}</span>
    )
  );
}

export function Module1Assistant({
  project,
  canvas,
  selectedSectionId,
  onSelectSection,
  onProjectUpdated,
  onSaveSection,
  onSaveCover,
  onOpenModule2,
  onError,
  saving,
  isAdmin = false,
  isVisible = true,
  onFeatureUsed,
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
  const draftRef = useRef(draft);
  const draftHtmlRef = useRef(draftHtml);
  const notesRef = useRef(notes);
  draftRef.current = draft;
  draftHtmlRef.current = draftHtml;
  notesRef.current = notes;

  const [autoSaveState, setAutoSaveState] = useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const [lastSavedAt, setLastSavedAt] = useState<string>("");
  const autoSaveTimerRef = useRef<number | null>(null);
  const autoSaveInFlightRef = useRef(false);

  const [pendingReformulation, setPendingReformulation] = useState<PendingReformulationPreview | null>(null);
  const [busyReformulate, setBusyReformulate] = useState(false);
  const [assistantActionMode, setAssistantActionMode] = useState<
    "auto" | "delegate_reformulate" | "create_table" | "create_diagram" | "search_image"
  >("auto");
  const [assistantActionFeedback, setAssistantActionFeedback] = useState<{
    actionType: string;
    message: string;
  } | null>(null);
  const [busyApplyReformulation, setBusyApplyReformulation] = useState(false);

  const [showVersionHistory, setShowVersionHistory] = useState(false);
  const [sectionVersions, setSectionVersions] = useState<SectionVersionEntry[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [restoringVersionId, setRestoringVersionId] = useState<string | null>(null);
  const [mobileStepsOpen, setMobileStepsOpen] = useState(false);
  const [showMethodGuide, setShowMethodGuide] = useState(false);
  const [showCamesRequirements, setShowCamesRequirements] = useState(false);
  const [showAssignedSegments, setShowAssignedSegments] = useState(false);

  // Interactive Chapter & Section Structure Manager state
  const [structureEditMode, setStructureEditMode] = useState(false);
  const [busyStructure, setBusyStructure] = useState(false);
  const [addingChapter, setAddingChapter] = useState(false);
  const [newChapterTitle, setNewChapterTitle] = useState("");
  const [newChapterFirstSection, setNewChapterFirstSection] = useState("");
  const [editingChapterId, setEditingChapterId] = useState<string | null>(null);
  const [editingChapterTitle, setEditingChapterTitle] = useState("");
  const [addingSectionChapterId, setAddingSectionChapterId] = useState<string | null>(null);
  const [newSectionTitle, setNewSectionTitle] = useState("");
  const [editingSectionId, setEditingSectionId] = useState<string | null>(null);
  const [editingSectionTitle, setEditingSectionTitle] = useState("");
  const [editingSectionChapterId, setEditingSectionChapterId] = useState("");
  const [showInternalLibrary, setShowInternalLibrary] = useState(false);
  const [showPersonalNotes, setShowPersonalNotes] = useState(() =>
    Boolean(activeState?.user_notes?.trim())
  );
  const internalLibraryEntries = activeState?.internal_library ?? [];
  const [aiInstruction, setAiInstruction] = useState("");
  const [coverForm, setCoverForm] = useState<CoverMetadata>(() =>
    withStructuredSupervisors(project.cover)
  );
  const [coDirDraft, setCoDirDraft] = useState<SupervisorInfo>(emptySupervisor);

  const [busyDraftBySec, setBusyDraftBySec] = useState<Record<string, boolean>>({});
  const [busyAuditBySec, setBusyAuditBySec] = useState<Record<string, boolean>>({});
  const [busyFixBySec, setBusyFixBySec] = useState<Record<string, boolean>>({});
  const [busyHumanizeBySec, setBusyHumanizeBySec] = useState<Record<string, boolean>>({});
  const [busyHarmonize, setBusyHarmonize] = useState(false);
  const [busyToc, setBusyToc] = useState(false);
  const [busyRefs, setBusyRefs] = useState(false);
  const [busyExtractCrf, setBusyExtractCrf] = useState(false);
  const [crfExtractNote, setCrfExtractNote] = useState<string | null>(null);
  const [busySubAnnex, setBusySubAnnex] = useState(false);
  const [subAnnexBilingual, setSubAnnexBilingual] = useState(false);
  const [subAnnexFacultyNote, setSubAnnexFacultyNote] = useState<string | null>(null);
  const [busyAntigravityBySec, setBusyAntigravityBySec] = useState<Record<string, boolean>>({});

  const busyDraft = Boolean(busyDraftBySec[activeSpec.id]);
  const busyAudit = Boolean(busyAuditBySec[activeSpec.id]);
  const busyFix = Boolean(busyFixBySec[activeSpec.id]);
  const busyHumanize = Boolean(busyHumanizeBySec[activeSpec.id]);
  const busyAntigravity = Boolean(busyAntigravityBySec[activeSpec.id]);

  const latestProjectRef = useRef(project);
  latestProjectRef.current = project;

  function mergeSectionProjectUpdate(
    serverProject: ProtocolProject,
    targetSectionId: string
  ): ProtocolProject {
    const current = latestProjectRef.current;
    if (!current || current.project_id !== serverProject.project_id) {
      return serverProject;
    }
    const mergedSections = {
      ...serverProject.sections,
      ...current.sections,
    };
    if (serverProject.sections[targetSectionId]) {
      mergedSections[targetSectionId] = serverProject.sections[targetSectionId];
    }
    const merged: ProtocolProject = {
      ...serverProject,
      sections: mergedSections,
    };
    latestProjectRef.current = merged;
    return merged;
  }

  function isSectionBusy(secId: string): boolean {
    return Boolean(
      busyDraftBySec[secId] ||
        busyAuditBySec[secId] ||
        busyFixBySec[secId] ||
        busyHumanizeBySec[secId] ||
        busyAntigravityBySec[secId]
    );
  }

  const [actionToast, setActionToast] = useState<{
    kind: "validated" | "saved";
    message: string;
    isFinalStep?: boolean;
  } | null>(null);
  const toastTimerRef = useRef<number | null>(null);

  function showToast(
    kind: "validated" | "saved",
    message: string,
    isFinalStep = false
  ) {
    if (toastTimerRef.current !== null) {
      window.clearTimeout(toastTimerRef.current);
    }
    setActionToast({ kind, message, isFinalStep });
    toastTimerRef.current = window.setTimeout(() => {
      setActionToast(null);
      toastTimerRef.current = null;
    }, 5000);
  }

  useEffect(() => {
    return () => {
      if (toastTimerRef.current !== null) {
        window.clearTimeout(toastTimerRef.current);
      }
    };
  }, []);

  async function handleSectionSaveOrValidate(
    sectionId: string,
    sectionTitle: string,
    contentMarkdown: string,
    status: StepStatus,
    userNotes: string,
    contentHtml?: string
  ) {
    const res = await onSaveSection(
      sectionId,
      contentMarkdown,
      status,
      userNotes,
      contentHtml
    );
    if (res === false) return;

    if (status === "validated") {
      const idx = canvas.findIndex((s) => s.id === sectionId);
      if (idx >= 0 && idx < canvas.length - 1) {
        const nextSpec = canvas[idx + 1];
        onSelectSection(nextSpec.id);
        if (typeof window !== "undefined") {
          window.scrollTo({ top: 0, behavior: "smooth" });
        }
        showToast(
          "validated",
          lang === "en"
            ? `Step "${sectionTitle}" validated! Moved to "${nextSpec.title}".`
            : `Étape « ${sectionTitle} » validée ! Passage automatique à « ${nextSpec.title} ».`
        );
      } else {
        showToast(
          "validated",
          lang === "en"
            ? `Step "${sectionTitle}" validated! All steps completed — ready for Module 2.`
            : `Étape « ${sectionTitle} » validée ! Protocole prêt pour la mise en forme dans le Module 2.`,
          true
        );
      }
    } else {
      showToast(
        "saved",
        lang === "en"
          ? `Draft saved for "${sectionTitle}".`
          : `Brouillon enregistré pour « ${sectionTitle} ».`
      );
    }
  }
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

  const isAutoTableSection =
    activeSpec.id === "toc" ||
    activeSpec.id === "abbreviations" ||
    activeSpec.id === "list_of_tables" ||
    activeSpec.id === "list_of_figures";

  const isDirty =
    activeSpec.id !== "cover_page" &&
    !isAutoTableSection &&
    (draft !== (activeState?.content_markdown ?? "") ||
      draftHtml !== (activeState?.content_html ?? "") ||
      notes !== (activeState?.user_notes ?? ""));

  async function flushAutoSave(sectionIdToSave?: string): Promise<boolean> {
    const sid = sectionIdToSave ?? selectedSectionIdRef.current;
    if (
      !sid ||
      sid === "cover_page" ||
      sid === "toc" ||
      sid === "abbreviations" ||
      sid === "list_of_tables" ||
      sid === "list_of_figures" ||
      autoSaveInFlightRef.current
    ) {
      return true;
    }
    const secState = latestProjectRef.current.sections[sid];
    const curDraft = draftRef.current;
    const curHtml = draftHtmlRef.current;
    const curNotes = notesRef.current;
    // Never silently overwrite a structured HTML table if TipTap stripped <table> tags
    if (
      (secState?.content_html ?? "").toLowerCase().includes("<table") &&
      !curHtml.toLowerCase().includes("<table")
    ) {
      return true;
    }
    const changed =
      curDraft !== (secState?.content_markdown ?? "") ||
      curHtml !== (secState?.content_html ?? "") ||
      curNotes !== (secState?.user_notes ?? "");
    if (!changed) return true;

    autoSaveInFlightRef.current = true;
    setAutoSaveState("saving");
    try {
      const updated = await api.updateSection(latestProjectRef.current.project_id, sid, {
        content_markdown: curDraft,
        content_html: curHtml,
        status: secState?.status && secState.status !== "pending" ? secState.status : "in_progress",
        user_notes: curNotes,
      });
      const merged = mergeSectionProjectUpdate(updated, sid);
      onProjectUpdated(merged);
      setAutoSaveState("saved");
      setLastSavedAt(
        new Date().toLocaleTimeString(lang === "en" ? "en-US" : "fr-FR", {
          hour: "2-digit",
          minute: "2-digit",
        })
      );
      return true;
    } catch {
      setAutoSaveState("error");
      return false;
    } finally {
      autoSaveInFlightRef.current = false;
    }
  }

  async function handleSelectSectionSafe(targetSectionId: string) {
    if (targetSectionId === activeSpec.id) return;
    if (autoSaveTimerRef.current !== null) {
      window.clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
    if (isDirty) {
      await flushAutoSave(activeSpec.id);
    }
    onSelectSection(targetSectionId);
  }

  // Silent debounced auto-save (1.8s after user pauses typing in editor or personal notes)
  useEffect(() => {
    if (activeSpec.id === "cover_page") return;
    if (!isDirty) return;
    setAutoSaveState("dirty");
    if (autoSaveTimerRef.current !== null) {
      window.clearTimeout(autoSaveTimerRef.current);
    }
    const sid = activeSpec.id;
    autoSaveTimerRef.current = window.setTimeout(() => {
      autoSaveTimerRef.current = null;
      void flushAutoSave(sid);
    }, 1800);
    return () => {
      if (autoSaveTimerRef.current !== null) {
        window.clearTimeout(autoSaveTimerRef.current);
        autoSaveTimerRef.current = null;
      }
    };
  }, [draft, draftHtml, notes, activeSpec.id, isDirty]);

  // Ctrl+S / Cmd+S manual save shortcut
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        if (selectedSectionIdRef.current !== "cover_page") {
          e.preventDefault();
          void handleSectionSaveOrValidate(
            selectedSectionIdRef.current,
            activeSpec.title,
            draftRef.current,
            activeState?.status && activeState.status !== "pending"
              ? activeState.status
              : "in_progress",
            notesRef.current,
            draftHtmlRef.current
          );
        }
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeSpec.title, activeState?.status]);

  async function handleToggleVersionHistory() {
    if (showVersionHistory) {
      setShowVersionHistory(false);
      return;
    }
    setShowVersionHistory(true);
    setLoadingVersions(true);
    try {
      const res = await api.listSectionVersions(project.project_id, activeSpec.id);
      setSectionVersions(res.versions || []);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingVersions(false);
    }
  }

  async function handleRestoreVersion(versionId: string) {
    setRestoringVersionId(versionId);
    onError("");
    try {
      const updated = await api.restoreSectionVersion(
        project.project_id,
        activeSpec.id,
        versionId
      );
      const merged = mergeSectionProjectUpdate(updated, activeSpec.id);
      onProjectUpdated(merged);
      const sec = merged.sections[activeSpec.id];
      if (sec) {
        setDraft(sec.content_markdown ?? "");
        setDraftHtml(sec.content_html ?? "");
      }
      setShowVersionHistory(false);
      showToast(
        "saved",
        lang === "en"
          ? "Previous version restored."
          : "Version précédente restaurée avec succès."
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setRestoringVersionId(null);
    }
  }

  async function handleAssistantCommandOrReformulate(hasContentNow: boolean) {
    const instr = aiInstruction.trim();
    if (assistantActionMode === "delegate_reformulate") {
      if (hasContentNow) {
        await handleSmartReformulate("auto", instr);
      } else {
        await handleDraftWithAI(instr || undefined);
      }
      return;
    }
    if (!instr && assistantActionMode === "auto") {
      if (hasContentNow) {
        await handleSmartReformulate("auto");
      } else {
        await handleDraftWithAI(undefined);
      }
      return;
    }

    const targetSectionId = activeSpec.id;
    setBusyReformulate(true);
    setAssistantActionFeedback(null);
    onError("");
    try {
      const cmdRes = await api.runSectionAssistantCommand(
        project.project_id,
        targetSectionId,
        {
          instruction: instr || activeSpec.title,
          current_markdown: draftRef.current,
          action: assistantActionMode,
        }
      );
      if (cmdRes.action_type === "delegate_reformulate") {
        setBusyReformulate(false);
        if (hasContentNow) {
          await handleSmartReformulate("auto", instr);
        } else {
          await handleDraftWithAI(instr);
        }
        return;
      }
      if (cmdRes.project) {
        onProjectUpdated(cmdRes.project);
      }
      setAssistantActionFeedback({
        actionType: cmdRes.action_type,
        message: cmdRes.message,
      });
      setAiInstruction("");
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyReformulate(false);
    }
  }

  async function handleSmartReformulate(
    mode: "auto" | "audit_fix" | "complement" = "auto",
    customInstruction?: string,
    auditIssues?: string[]
  ) {
    const targetSectionId = activeSpec.id;
    const instr = (customInstruction ?? aiInstruction).trim();
    const issuesList =
      auditIssues ??
      (mode === "audit_fix"
        ? lastAuditResult?.issues ?? activeState?.audit_notes ?? []
        : []);

    setBusyReformulate(true);
    if (mode === "audit_fix") {
      setBusyFixBySec((prev) => ({ ...prev, [targetSectionId]: true }));
    }
    onError("");
    try {
      const res = await api.smartReformulateSection(project.project_id, targetSectionId, {
        instruction: instr,
        current_markdown: draftRef.current,
        mode,
        audit_issues: issuesList,
      });
      if (selectedSectionIdRef.current === targetSectionId) {
        setPendingReformulation({
          kind: mode === "audit_fix" ? "audit_fix" : "smart_reformulate",
          strategy: res.strategy,
          summary: res.summary,
          original_markdown: res.original_markdown,
          proposed_markdown: res.proposed_markdown,
          proposed_html: res.proposed_html,
          patches: res.patches || [],
          model_used: res.model_used,
          retryPayload: {
            instruction: instr,
            mode,
            audit_issues: issuesList,
          },
        });
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyReformulate(false);
      if (mode === "audit_fix") {
        setBusyFixBySec((prev) => ({ ...prev, [targetSectionId]: false }));
      }
    }
  }

  async function handleSelectionTransform(
    selectedText: string,
    action: SelectionTransformAction,
    instruction?: string
  ) {
    const targetSectionId = activeSpec.id;
    setBusyReformulate(true);
    onError("");
    try {
      const res = await api.transformSectionSelection(project.project_id, targetSectionId, {
        selected_text: selectedText,
        action,
        instruction: instruction ?? "",
        current_markdown: draftRef.current,
      });
      if (selectedSectionIdRef.current === targetSectionId) {
        setPendingReformulation({
          kind: "selection_transform",
          strategy: action === "complement" ? "selective_patch" : "selective_patch",
          summary: res.summary,
          original_markdown: res.original_markdown,
          proposed_markdown: res.proposed_markdown,
          proposed_html: res.proposed_html,
          patches: res.patches || [],
          model_used: res.model_used,
          retryPayload: {
            instruction: instruction ?? "",
            selected_text: selectedText,
            action,
          },
        });
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyReformulate(false);
    }
  }

  async function handleApplyPendingReformulation() {
    if (!pendingReformulation) return;
    const targetSectionId = activeSpec.id;
    setBusyApplyReformulation(true);
    onError("");
    try {
      const updated = await api.applySectionReformulation(project.project_id, targetSectionId, {
        content_markdown: pendingReformulation.proposed_markdown,
        content_html: pendingReformulation.proposed_html,
        source: pendingReformulation.kind,
        note: pendingReformulation.summary,
      });
      const merged = mergeSectionProjectUpdate(updated, targetSectionId);
      onProjectUpdated(merged);
      const sec = merged.sections[targetSectionId];
      if (sec) {
        setDraft(sec.content_markdown ?? "");
        setDraftHtml(sec.content_html ?? "");
      }
      if (pendingReformulation.kind === "audit_fix") {
        setLastAuditResult(null);
      }
      setPendingReformulation(null);
      setAutoSaveState("saved");
      setLastSavedAt(
        new Date().toLocaleTimeString(lang === "en" ? "en-US" : "fr-FR", {
          hour: "2-digit",
          minute: "2-digit",
        })
      );
      showToast(
        "saved",
        lang === "en"
          ? "Reformulation validated and saved (previous version kept in History)."
          : "Reformulation validée et enregistrée (version précédente conservée dans l'Historique)."
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyApplyReformulation(false);
    }
  }

  async function handleRetryPendingReformulation(refinedInstruction?: string) {
    if (!pendingReformulation) return;
    const rp = pendingReformulation.retryPayload;
    const nextInstr = refinedInstruction !== undefined ? refinedInstruction : rp.instruction;
    if (pendingReformulation.kind === "selection_transform" && rp.selected_text && rp.action) {
      await handleSelectionTransform(rp.selected_text, rp.action, nextInstr);
    } else {
      await handleSmartReformulate(rp.mode ?? "auto", nextInstr, rp.audit_issues);
    }
  }

  useEffect(() => {
    setDraft(activeState?.content_markdown ?? "");
    setDraftHtml(activeState?.content_html ?? "");
    setNotes(activeState?.user_notes ?? "");
    setAutoSaveState("idle");
  }, [activeSpec.id, activeState?.content_markdown, activeState?.content_html, activeState?.user_notes]);

  useEffect(() => {
    setLastDraftResult(null);
    setLastAuditResult(null);
    setLastHumanizeResult(null);
    setLastRefDecisionNote(null);
    setLastHarmonizeNote(null);
    setPendingReformulation(null);
    setShowVersionHistory(false);
    setAiInstruction("");
    setShowPersonalNotes(Boolean(project.sections[activeSpec.id]?.user_notes?.trim()));
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

  const isAlgorithmicSection = ALGORITHMIC_SECTION_IDS.has(activeSpec.id);
  const isAutoSyncSection = AUTO_SYNC_SECTION_IDS.has(activeSpec.id);
  const isReferencesSection = activeSpec.id === "references";
  const hasContent = draft.trim().length > 0;
  const hasInstruction = aiInstruction.trim().length > 0;
  const showMediaPanel = !NON_MEDIA_SECTION_IDS.has(activeSpec.id);
  const wordCount = hasContent ? draft.trim().split(/\s+/).length : 0;
  const detectedPlaceholders = extractInvestigatorPlaceholders(draft);

  const syncButtonLabel =
    activeSpec.id === "toc"
      ? t("m1.sync_toc")
      : activeSpec.id === "list_of_tables"
      ? t("m1.sync_tables")
      : activeSpec.id === "list_of_figures"
      ? t("m1.sync_figures")
      : activeSpec.id === "annexes"
      ? t("m1.sync_annexes")
      : t("m1.sync_abbreviations");

  const aiPlaceholder = !hasContent
    ? t("m1.instruction_placeholder_generate")
    : t("m1.instruction_placeholder_refine");

  async function handleDraftWithAI(
    instruction?: string,
    forceExternalResearch = false
  ) {
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusyDraftBySec((prev) => ({ ...prev, [targetSectionId]: true }));
    onError("");
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "draft",
          titleFr: forceExternalResearch
            ? `Enrichissement bibliographique : ${targetTitle}`
            : `Rédaction : ${targetTitle}`,
          titleEn: forceExternalResearch
            ? `Bibliographic Enrichment: ${targetTitle}`
            : `Drafting: ${targetTitle}`,
          projectId: project.project_id,
        },
        (taskId) =>
          api.draftSection(
            project.project_id,
            targetSectionId,
            instruction,
            taskId,
            forceExternalResearch
          )
      );
      const merged = mergeSectionProjectUpdate(res.project, targetSectionId);
      onProjectUpdated(merged);
      if (selectedSectionIdRef.current === targetSectionId) {
        setDraft(res.result.content_markdown);
        setDraftHtml(
          res.result.content_html ??
            merged.sections[targetSectionId]?.content_html ??
            ""
        );
        setLastDraftResult(res.result);
        setAiInstruction("");
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyDraftBySec((prev) => ({ ...prev, [targetSectionId]: false }));
    }
  }

  async function handleAuditWithAI() {
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusyAuditBySec((prev) => ({ ...prev, [targetSectionId]: true }));
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
      const merged = mergeSectionProjectUpdate(res.project, targetSectionId);
      onProjectUpdated(merged);
      if (selectedSectionIdRef.current === targetSectionId) {
        setLastAuditResult(res.result);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAuditBySec((prev) => ({ ...prev, [targetSectionId]: false }));
    }
  }

  async function handleMobilizeAntigravity(instruction?: string) {
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusyAntigravityBySec((prev) => ({ ...prev, [targetSectionId]: true }));
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
      const merged = mergeSectionProjectUpdate(res.project, targetSectionId);
      onProjectUpdated(merged);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAntigravityBySec((prev) => ({ ...prev, [targetSectionId]: false }));
    }
  }

  async function handleFixWithAI() {
    // Use selective surgical audit reformulation so existing manual edits are preserved
    // and the user reviews the color-highlighted diff before validating!
    await handleSmartReformulate(
      "audit_fix",
      lang === "en"
        ? "Fix only the passages flagged by the FMSB audit while preserving all other text."
        : "Corriger uniquement les passages signalés par l'audit FMSB en conservant le reste du texte intact.",
      lastAuditResult?.issues ?? activeState?.audit_notes ?? []
    );
  }

  async function handleExtractAndSyncToMask() {
    setBusyExtractCrf(true);
    setCrfExtractNote(null);
    onError("");
    try {
      if (draft.trim() && draft.trim() !== (activeState?.content_markdown ?? "").trim()) {
        await onSaveSection(
          activeSpec.id,
          draft,
          activeState?.status ?? "in_progress",
          notes,
          draftHtml
        );
      }
      const res = await tracker.runWithProgress(
        {
          operation: "extract_crf",
          titleFr: "Extraction & synchronisation de la fiche vers le Masque de saisie",
          titleEn: "Extracting & syncing Data Collection Form into Mask",
          projectId: project.project_id,
        },
        (taskId) =>
          api.extractAndSyncCrf(
            project.project_id,
            { source_mode: "auto", use_ai: true },
            taskId
          )
      );
      onProjectUpdated(res.project);
      const updatedSec = res.project.sections["annexe_fiche_collecte"];
      if (selectedSectionIdRef.current === "annexe_fiche_collecte" && updatedSec) {
        setDraft(updatedSec.content_markdown);
        setDraftHtml(updatedSec.content_html ?? "");
      }
      const r = res.sync_report;
      setCrfExtractNote(
        lang === "en"
          ? `${r.total_fields} field(s) synced into the Data Mask (${r.preserved_count} preserved, ${r.added_count} added, ${r.modified_count} updated).`
          : `${r.total_fields} champ(s) compilé(s) et synchronisé(s) vers le Masque de saisie (${r.preserved_count} conservé(s), ${r.added_count} ajouté(s), ${r.modified_count} modifié(s)).`
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyExtractCrf(false);
    }
  }

  async function handleGenerateFacultySubAnnex() {
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusySubAnnex(true);
    setSubAnnexFacultyNote(null);
    onError("");
    try {
      const res = await tracker.runWithProgress(
        {
          operation: "generate_sub_annex",
          titleFr: `Rédaction adaptée : ${targetTitle}`,
          titleEn: `Generating Sub-Appendix: ${targetTitle}`,
          projectId: project.project_id,
        },
        (taskId) =>
          api.generateSubAnnex(
            project.project_id,
            targetSectionId,
            {
              user_instruction: aiInstruction.trim(),
              bilingual: subAnnexBilingual,
              use_ai: true,
            },
            taskId
          )
      );
      const merged = mergeSectionProjectUpdate(res.project, targetSectionId);
      onProjectUpdated(merged);
      if (selectedSectionIdRef.current === targetSectionId) {
        setDraft(res.content_markdown);
        setDraftHtml(res.content_html ?? "");
        const fc = res.faculty_context;
        setSubAnnexFacultyNote(
          lang === "en"
            ? `Tailored to ${fc.faculty_name} (${fc.university_name}) — Recipient: ${fc.faculty_head_recipient}.`
            : `Adapté à : ${fc.faculty_name} (${fc.university_name}) — Destinataire : ${fc.faculty_head_recipient}.`
        );
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusySubAnnex(false);
    }
  }

  async function handleHumanizeWithAI() {
    onFeatureUsed?.("humanizer");
    const targetSectionId = activeSpec.id;
    const targetTitle = activeSpec.title;
    setBusyHumanizeBySec((prev) => ({ ...prev, [targetSectionId]: true }));
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
      const merged = mergeSectionProjectUpdate(res.project, targetSectionId);
      onProjectUpdated(merged);
      if (selectedSectionIdRef.current === targetSectionId) {
        setDraft(res.result.content_markdown);
        setDraftHtml(
          res.result.content_html ??
            merged.sections[targetSectionId]?.content_html ??
            ""
        );
        setLastHumanizeResult(res.result);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyHumanizeBySec((prev) => ({ ...prev, [targetSectionId]: false }));
    }
  }

  async function handleHarmonizeSection(targetSectionId: string, alertId?: string) {
    onFeatureUsed?.("coherence_sentinel");
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

  const PROTECTED_STRUCTURE_IDS = new Set(["cover_page", "references"]);

  async function runStructureAction(
    payload: Parameters<typeof api.manageProjectStructure>[1],
    selectSectionAfter?: string
  ) {
    setBusyStructure(true);
    onError("");
    try {
      const updated = await api.manageProjectStructure(project.project_id, payload);
      onProjectUpdated(updated);
      const nextCanvas =
        updated.custom_canvas && updated.custom_canvas.length > 0
          ? updated.custom_canvas
          : canvas;
      if (selectSectionAfter && nextCanvas.some((s) => s.id === selectSectionAfter)) {
        onSelectSection(selectSectionAfter);
      } else if (!nextCanvas.some((s) => s.id === activeSpec.id) && nextCanvas.length > 0) {
        onSelectSection(nextCanvas[0].id);
      }
      return updated;
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      setBusyStructure(false);
    }
  }

  const renderStepsList = (closeDrawer?: () => void) => (
    <div className="space-y-4">
      {structureEditMode && (
        <div className="space-y-2 rounded-lg border border-indigo-200 bg-indigo-50/70 p-2.5">
          <div className="flex flex-wrap items-center justify-between gap-1.5">
            <button
              type="button"
              disabled={busyStructure}
              onClick={() => setAddingChapter((v) => !v)}
              className="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              <FolderPlus className="h-3.5 w-3.5" />
              {lang === "en" ? "+ New Chapter" : "+ Nouveau chapitre"}
            </button>
            {project.custom_canvas && project.custom_canvas.length > 0 && (
              <button
                type="button"
                disabled={busyStructure}
                onClick={() => {
                  if (
                    window.confirm(
                      lang === "en"
                        ? "Reset chapters and sections back to the default FMSB 5-chapter template? (Custom section content will be hidden.)"
                        : "Réinitialiser les chapitres et sections vers le canevas FMSB par défaut ?"
                    )
                  ) {
                    void runStructureAction({ action: "reset_structure" });
                  }
                }}
                className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-[10px] font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              >
                <RotateCcw className="h-3 w-3" />
                {lang === "en" ? "Reset FMSB" : "Réinitialiser"}
              </button>
            )}
          </div>
          {addingChapter && (
            <div className="space-y-1.5 rounded-md border border-indigo-200 bg-white p-2">
              <input
                type="text"
                value={newChapterTitle}
                onChange={(e) => setNewChapterTitle(e.target.value)}
                placeholder={
                  lang === "en"
                    ? "Chapter title (e.g. CHAPTER VI: DISCUSSION)"
                    : "Titre du chapitre (ex. CHAPITRE VI : DISCUSSION)"
                }
                className="w-full rounded border border-slate-300 px-2 py-1 text-xs focus:border-indigo-500 focus:outline-none"
              />
              <input
                type="text"
                value={newChapterFirstSection}
                onChange={(e) => setNewChapterFirstSection(e.target.value)}
                placeholder={
                  lang === "en"
                    ? "First section title (optional)"
                    : "Titre de la 1re section (optionnel)"
                }
                className="w-full rounded border border-slate-300 px-2 py-1 text-xs focus:border-indigo-500 focus:outline-none"
              />
              <div className="flex justify-end gap-1.5">
                <button
                  type="button"
                  onClick={() => {
                    setAddingChapter(false);
                    setNewChapterTitle("");
                    setNewChapterFirstSection("");
                  }}
                  className="rounded px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100"
                >
                  {lang === "en" ? "Cancel" : "Annuler"}
                </button>
                <button
                  type="button"
                  disabled={busyStructure || !newChapterTitle.trim()}
                  onClick={async () => {
                    const updated = await runStructureAction({
                      action: "add_chapter",
                      chapter_title: newChapterTitle.trim(),
                      title: newChapterFirstSection.trim() || undefined,
                    });
                    if (updated) {
                      setAddingChapter(false);
                      setNewChapterTitle("");
                      setNewChapterFirstSection("");
                    }
                  }}
                  className="rounded bg-indigo-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  {lang === "en" ? "Create" : "Créer"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {chapters.map((chap) => {
        const chapHasOnlyProtected = chap.items.every((it) =>
          PROTECTED_STRUCTURE_IDS.has(it.id)
        );
        return (
          <div key={chap.chapterId}>
            <div className="mb-1.5 flex items-center justify-between gap-1">
              {editingChapterId === chap.chapterId ? (
                <div className="flex w-full items-center gap-1">
                  <input
                    type="text"
                    value={editingChapterTitle}
                    onChange={(e) => setEditingChapterTitle(e.target.value)}
                    className="min-w-0 flex-1 rounded border border-indigo-400 px-1.5 py-0.5 text-xs font-semibold text-slate-800 focus:outline-none"
                  />
                  <button
                    type="button"
                    disabled={busyStructure || !editingChapterTitle.trim()}
                    onClick={async () => {
                      const updated = await runStructureAction({
                        action: "rename_chapter",
                        chapter_id: chap.chapterId,
                        chapter_title: editingChapterTitle.trim(),
                      });
                      if (updated) setEditingChapterId(null);
                    }}
                    className="rounded bg-indigo-600 px-1.5 py-0.5 text-[10px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    OK
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditingChapterId(null)}
                    className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ) : (
                <>
                  <div className="min-w-0 flex-1 truncate text-xs font-bold uppercase tracking-wider text-slate-500">
                    {chap.chapterTitle}
                  </div>
                  {structureEditMode && (
                    <div className="flex shrink-0 items-center gap-0.5">
                      <button
                        type="button"
                        disabled={busyStructure}
                        onClick={() => {
                          setAddingSectionChapterId(
                            addingSectionChapterId === chap.chapterId ? null : chap.chapterId
                          );
                          setNewSectionTitle("");
                        }}
                        title={lang === "en" ? "Add section to this chapter" : "Ajouter une section à ce chapitre"}
                        className="rounded p-1 text-emerald-700 hover:bg-emerald-50 disabled:opacity-40"
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        disabled={busyStructure}
                        onClick={() => {
                          setEditingChapterId(chap.chapterId);
                          setEditingChapterTitle(chap.chapterTitle);
                        }}
                        title={lang === "en" ? "Rename chapter" : "Renommer le chapitre"}
                        className="rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-indigo-700 disabled:opacity-40"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      {!chapHasOnlyProtected && (
                        <button
                          type="button"
                          disabled={busyStructure}
                          onClick={() => {
                            if (
                              window.confirm(
                                lang === "en"
                                  ? `Delete chapter "${chap.chapterTitle}" and its sections?`
                                  : `Supprimer le chapitre « ${chap.chapterTitle} » et ses sections ?`
                              )
                            ) {
                              void runStructureAction({
                                action: "delete_chapter",
                                chapter_id: chap.chapterId,
                              });
                            }
                          }}
                          title={lang === "en" ? "Delete chapter" : "Supprimer le chapitre"}
                          className="rounded p-1 text-rose-500 hover:bg-rose-50 disabled:opacity-40"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>

            {structureEditMode && addingSectionChapterId === chap.chapterId && (
              <div className="mb-2 flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50/70 p-1.5">
                <input
                  type="text"
                  value={newSectionTitle}
                  onChange={(e) => setNewSectionTitle(e.target.value)}
                  placeholder={lang === "en" ? "New section title..." : "Titre de la nouvelle section..."}
                  className="min-w-0 flex-1 rounded border border-slate-300 bg-white px-2 py-1 text-xs focus:border-emerald-500 focus:outline-none"
                />
                <button
                  type="button"
                  disabled={busyStructure || !newSectionTitle.trim()}
                  onClick={async () => {
                    const updated = await runStructureAction({
                      action: "add_section",
                      chapter_id: chap.chapterId,
                      title: newSectionTitle.trim(),
                    });
                    if (updated) {
                      setAddingSectionChapterId(null);
                      setNewSectionTitle("");
                    }
                  }}
                  className="rounded bg-emerald-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  {lang === "en" ? "Add" : "Ajouter"}
                </button>
                <button
                  type="button"
                  onClick={() => setAddingSectionChapterId(null)}
                  className="rounded p-1 text-slate-400 hover:bg-white hover:text-slate-700"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}

            <div className="space-y-1">
              {chap.items.map((item, itemIdx) => {
                const st = project.sections[item.id]?.status ?? "pending";
                const segCount = project.article_segments.filter(
                  (s) => s.approved && s.target_section_ids.includes(item.id)
                ).length;
                const hasCoherenceAlert = allCoherenceAlerts.some(
                  (a) => a.target_section_id === item.id
                );
                const isSelected = item.id === activeSpec.id;
                const isProtected = PROTECTED_STRUCTURE_IDS.has(item.id);
                const isEditingThis = structureEditMode && editingSectionId === item.id;
                return (
                  <div key={item.id} className="space-y-1">
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          void handleSelectSectionSafe(item.id);
                          closeDrawer?.();
                        }}
                        className={`flex min-h-[42px] min-w-0 flex-1 items-start justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition ${
                          isSelected
                            ? "bg-indigo-50 font-medium text-indigo-900 ring-1 ring-indigo-200"
                            : "text-slate-700 hover:bg-slate-50"
                        }`}
                      >
                        <span className="flex min-w-0 items-start gap-2">
                          <span className="mt-0.5 shrink-0">
                            {isSectionBusy(item.id) ? (
                              <Loader2 className="h-4 w-4 animate-spin text-indigo-600" />
                            ) : st === "validated" ? (
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
                        {!structureEditMode && (
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
                        )}
                      </button>

                      {structureEditMode && (
                        <div className="flex shrink-0 items-center gap-0.5">
                          <button
                            type="button"
                            disabled={busyStructure || itemIdx === 0}
                            onClick={() =>
                              void runStructureAction({
                                action: "update_section",
                                section_id: item.id,
                                direction: "up",
                              })
                            }
                            title={lang === "en" ? "Move up" : "Monter"}
                            className="rounded p-1 text-slate-500 hover:bg-slate-100 disabled:opacity-30"
                          >
                            <ArrowUp className="h-3 w-3" />
                          </button>
                          <button
                            type="button"
                            disabled={busyStructure || itemIdx === chap.items.length - 1}
                            onClick={() =>
                              void runStructureAction({
                                action: "update_section",
                                section_id: item.id,
                                direction: "down",
                              })
                            }
                            title={lang === "en" ? "Move down" : "Descendre"}
                            className="rounded p-1 text-slate-500 hover:bg-slate-100 disabled:opacity-30"
                          >
                            <ArrowDown className="h-3 w-3" />
                          </button>
                          <button
                            type="button"
                            disabled={busyStructure}
                            onClick={() => {
                              if (isEditingThis) {
                                setEditingSectionId(null);
                              } else {
                                setEditingSectionId(item.id);
                                setEditingSectionTitle(item.title);
                                setEditingSectionChapterId(item.chapter_id);
                              }
                            }}
                            title={
                              lang === "en"
                                ? "Edit title or switch chapter"
                                : "Modifier le titre ou déplacer vers un autre chapitre"
                            }
                            className={`rounded p-1 ${
                              isEditingThis
                                ? "bg-indigo-100 text-indigo-700"
                                : "text-slate-500 hover:bg-slate-100 hover:text-indigo-700"
                            }`}
                          >
                            <Pencil className="h-3 w-3" />
                          </button>
                          {!isProtected && (
                            <button
                              type="button"
                              disabled={busyStructure}
                              onClick={() => {
                                if (
                                  window.confirm(
                                    lang === "en"
                                      ? `Delete section "${item.title}"?`
                                      : `Supprimer la section « ${item.title} » ?`
                                  )
                                ) {
                                  void runStructureAction({
                                    action: "delete_section",
                                    section_id: item.id,
                                  });
                                }
                              }}
                              title={lang === "en" ? "Delete section" : "Supprimer la section"}
                              className="rounded p-1 text-rose-500 hover:bg-rose-50"
                            >
                              <Trash2 className="h-3 w-3" />
                            </button>
                          )}
                        </div>
                      )}
                    </div>

                    {isEditingThis && (
                      <div className="space-y-1.5 rounded-lg border border-indigo-200 bg-indigo-50/60 p-2 text-xs">
                        <div>
                          <label className="mb-0.5 block text-[10px] font-semibold text-slate-600">
                            {lang === "en" ? "Section title" : "Titre de la section"}
                          </label>
                          <input
                            type="text"
                            value={editingSectionTitle}
                            onChange={(e) => setEditingSectionTitle(e.target.value)}
                            className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs focus:border-indigo-500 focus:outline-none"
                          />
                        </div>
                        {!isProtected && chapters.length > 1 && (
                          <div>
                            <label className="mb-0.5 block text-[10px] font-semibold text-slate-600">
                              {lang === "en" ? "Chapter (switch chapter)" : "Chapitre (déplacer vers)"}
                            </label>
                            <select
                              value={editingSectionChapterId}
                              onChange={(e) => setEditingSectionChapterId(e.target.value)}
                              className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs focus:border-indigo-500 focus:outline-none"
                            >
                              {chapters.map((c) => (
                                <option key={c.chapterId} value={c.chapterId}>
                                  {c.chapterTitle}
                                </option>
                              ))}
                            </select>
                          </div>
                        )}
                        <div className="flex justify-end gap-1.5 pt-0.5">
                          <button
                            type="button"
                            onClick={() => setEditingSectionId(null)}
                            className="rounded px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-white"
                          >
                            {lang === "en" ? "Cancel" : "Annuler"}
                          </button>
                          <button
                            type="button"
                            disabled={busyStructure || !editingSectionTitle.trim()}
                            onClick={async () => {
                              const updated = await runStructureAction(
                                {
                                  action: "update_section",
                                  section_id: item.id,
                                  title: editingSectionTitle.trim(),
                                  target_chapter_id: !isProtected
                                    ? editingSectionChapterId
                                    : undefined,
                                },
                                item.id
                              );
                              if (updated) setEditingSectionId(null);
                            }}
                            className="rounded bg-indigo-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                          >
                            {lang === "en" ? "Save" : "Enregistrer"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
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
        onFeatureUsed={onFeatureUsed}
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
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => {
                    setStructureEditMode((v) => !v);
                    setEditingChapterId(null);
                    setEditingSectionId(null);
                    setAddingChapter(false);
                    setAddingSectionChapterId(null);
                  }}
                  className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold transition ${
                    structureEditMode
                      ? "border-indigo-600 bg-indigo-600 text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-700"
                  }`}
                >
                  <SlidersHorizontal className="h-3 w-3" />
                  {structureEditMode
                    ? lang === "en"
                      ? "Done"
                      : "Terminer"
                    : lang === "en"
                    ? "Edit plan"
                    : "Organiser"}
                </button>
                <button
                  type="button"
                  aria-label={t("nav.close")}
                  onClick={() => setMobileStepsOpen(false)}
                  className="flex min-h-[40px] min-w-[40px] items-center justify-center rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-100"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
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
        <aside className="hidden lg:block lg:col-span-4 xl:col-span-3 lg:sticky lg:top-20 lg:self-start">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">
                {t("m1.steps_title", { count: canvas.length })}
              </h2>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => {
                    setStructureEditMode((v) => !v);
                    setEditingChapterId(null);
                    setEditingSectionId(null);
                    setAddingChapter(false);
                    setAddingSectionChapterId(null);
                  }}
                  title={
                    lang === "en"
                      ? "Add, edit, delete chapters & switch sections between chapters"
                      : "Ajouter, modifier, supprimer des chapitres et déplacer des sections"
                  }
                  className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-semibold transition ${
                    structureEditMode
                      ? "border-indigo-600 bg-indigo-600 text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-700"
                  }`}
                >
                  <SlidersHorizontal className="h-3 w-3" />
                  {structureEditMode
                    ? lang === "en"
                      ? "Done"
                      : "Terminer"
                    : lang === "en"
                    ? "Edit plan"
                    : "Organiser"}
                </button>
                <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700">
                  {t("m1.validated_count", { done: validatedCount, total: canvas.length })}
                </span>
              </div>
            </div>
            <div className="mb-4 h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full bg-emerald-600 transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>

            <div className="max-h-[calc(100vh-11rem)] overflow-y-auto pr-1">
              {renderStepsList()}
            </div>
          </div>
        </aside>

        {/* Main Workspace for Active Step */}
        <section className="space-y-5 lg:col-span-8 xl:col-span-9">
          {actionToast && (
            <div
              role="status"
              aria-live="polite"
              className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 text-xs font-medium shadow-md transition ${
                actionToast.kind === "validated"
                  ? "border-emerald-300 bg-emerald-50 text-emerald-950"
                  : "border-indigo-200 bg-indigo-50 text-indigo-950"
              }`}
            >
              <div className="flex items-center gap-2">
                <CheckCircle2
                  className={`h-4 w-4 shrink-0 ${
                    actionToast.kind === "validated"
                      ? "text-emerald-600"
                      : "text-indigo-600"
                  }`}
                />
                <span>{actionToast.message}</span>
              </div>
              <div className="flex items-center gap-2">
                {actionToast.isFinalStep && onOpenModule2 && (
                  <button
                    type="button"
                    onClick={() => {
                      setActionToast(null);
                      onOpenModule2();
                    }}
                    className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700"
                  >
                    <span>
                      {lang === "en"
                        ? "Open Module 2 (Formatting & Export)"
                        : "Ouvrir le Module 2 (Mise en forme & Export)"}
                    </span>
                    <ArrowRight className="h-3.5 w-3.5" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setActionToast(null)}
                  className="rounded p-1 text-slate-400 hover:bg-white/60 hover:text-slate-700"
                  aria-label={t("nav.close")}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}

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
                      await handleSectionSaveOrValidate(
                        "cover_page",
                        activeSpec.title,
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
                              <span className="text-slate-800">
                                {renderHighlightedSegmentText(seg.summary)}
                              </span>
                              {seg.key_quote_or_data && (
                                <div className="mt-0.5 text-[11px] text-slate-500">
                                  {lang === "en" ? "Data:" : "Donnée :"}{" "}
                                  {renderHighlightedSegmentText(
                                    seg.key_quote_or_data
                                  )}
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

              {/* Algorithmic Synchronization Bar (No AI Buttons) vs Narrative Unified AI Command Bar */}
              {isAlgorithmicSection ? (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50/90 px-4 py-3">
                  <div className="flex items-center gap-2.5 text-xs text-slate-700">
                    <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[#6C2826]/10 text-[#6C2826]">
                      <RefreshCw className="h-3.5 w-3.5" />
                    </span>
                    <div>
                      <div className="font-bold text-slate-900">
                        {lang === "en"
                          ? "Automatic Protocol Synchronization"
                          : "Synchronisation algorithmique automatique"}
                      </div>
                      <div className="text-[11px] text-slate-600">
                        {isReferencesSection
                          ? lang === "en"
                            ? "Automatically compiled and formatted in Vancouver ICMJE order from your cited references."
                            : "Générée automatiquement selon la norme Vancouver ICMJE à partir des références du protocole."
                          : lang === "en"
                          ? "Automatically compiled from the sections, tables, figures, and structured forms of your protocol."
                          : "Extraite automatiquement à partir des sections, tableaux, figures et formulaires structurés du protocole."}
                      </div>
                    </div>
                  </div>

                  {isAutoSyncSection && (
                    <button
                      type="button"
                      disabled={busyToc}
                      onClick={handleGenerateAutoToc}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-[#571f1d] disabled:opacity-50"
                    >
                      {busyToc ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                      {busyToc
                        ? lang === "en"
                          ? "Syncing..."
                          : "Synchronisation..."
                        : syncButtonLabel}
                    </button>
                  )}

                  {isReferencesSection && (
                    <button
                      type="button"
                      disabled={busyRefs}
                      onClick={handleValidateVancouverReferences}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-[#571f1d] disabled:opacity-50"
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
                </div>
              ) : (
                <div className="space-y-2.5 rounded-xl border border-indigo-200/80 bg-gradient-to-b from-indigo-50/70 to-white p-3.5 shadow-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-xs text-indigo-950">
                      <span className="inline-flex h-6 w-6 items-center justify-center rounded-lg bg-indigo-600/10 text-indigo-700">
                        <Sparkles className="h-3.5 w-3.5" />
                      </span>
                      <div>
                        <span className="font-bold">
                          {lang === "en" ? "AI Assistant" : "Assistant IA"}
                        </span>
                        <span className="mx-1.5 text-slate-300">·</span>
                        <span className="text-slate-600">
                          {lang === "en"
                            ? "Evidence-grounded drafting, refinement & FMSB audit"
                            : "Rédaction sourcée, affinage ciblé & audit FMSB"}
                        </span>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        disabled={busyAudit || !hasContent}
                        onClick={handleAuditWithAI}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        {busyAudit ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
                        )}
                        {busyAudit ? t("m1.auditing") : t("m1.audit_ai")}
                      </button>

                      <button
                        type="button"
                        disabled={busyHumanize || !hasContent}
                        onClick={handleHumanizeWithAI}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-50"
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

                      <button
                        type="button"
                        disabled={busyAntigravity}
                        onClick={() =>
                          handleMobilizeAntigravity(aiInstruction.trim() || undefined)
                        }
                        className="inline-flex items-center gap-1.5 rounded-lg border border-rose-300 bg-rose-50 px-2.5 py-1.5 text-xs font-semibold text-[#6C2826] hover:bg-rose-100 disabled:opacity-50"
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
                    </div>
                  </div>

                  {/* Unified Prompt Input + Adaptive Selective Reformulation / Full Generation */}
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (busyDraft || busyReformulate) return;
                      if (hasInstruction || assistantActionMode !== "auto") {
                        void handleAssistantCommandOrReformulate(hasContent);
                      } else if (hasContent) {
                        void handleSmartReformulate("auto");
                      } else {
                        void handleDraftWithAI(undefined);
                      }
                    }}
                    className="flex flex-wrap items-center gap-2"
                  >
                    <select
                      value={assistantActionMode}
                      onChange={(e) =>
                        setAssistantActionMode(
                          e.target.value as
                            | "auto"
                            | "delegate_reformulate"
                            | "create_table"
                            | "create_diagram"
                            | "search_image"
                        )
                      }
                      aria-label={lang === "en" ? "AI Action" : "Action IA"}
                      className="shrink-0 rounded-lg border border-indigo-200 bg-indigo-50/70 px-2.5 py-2 text-xs font-semibold text-indigo-900 focus:border-indigo-500 focus:outline-none"
                    >
                      <option value="auto">
                        {lang === "en" ? "✨ Auto (Smart)" : "✨ Auto (Intelligent)"}
                      </option>
                      <option value="delegate_reformulate">
                        {lang === "en" ? "✏️ Edit / Reformulate text" : "✏️ Reformuler / Compléter"}
                      </option>
                      <option value="create_table">
                        {lang === "en" ? "📊 Create structured table" : "📊 Créer un tableau"}
                      </option>
                      <option value="create_diagram">
                        {lang === "en" ? "📐 Create diagram / flowchart" : "📐 Créer un schéma"}
                      </option>
                      <option value="search_image">
                        {lang === "en" ? "🖼️ Search & insert image" : "🖼️ Chercher une image"}
                      </option>
                    </select>
                    <input
                      type="text"
                      value={aiInstruction}
                      onChange={(e) => setAiInstruction(e.target.value)}
                      placeholder={
                        assistantActionMode === "create_table"
                          ? lang === "en"
                            ? "Describe table columns/rows (e.g. 8-month activity timeline, budget items, variables...)"
                            : "Précisez le tableau voulu (ex: chronogramme sur 8 mois, variables clés, budget détaillé...)"
                          : assistantActionMode === "create_diagram"
                          ? lang === "en"
                            ? "Describe the diagram (e.g. PRISMA exclusion flow, conceptual model, decision flowchart...)"
                            : "Précisez le schéma voulu (ex: flux PRISMA d'inclusion, modèle conceptuel, organigramme...)"
                          : assistantActionMode === "search_image"
                          ? lang === "en"
                            ? "Describe the medical figure to search & insert (e.g. skin layers anatomy, HIV cycle...)"
                            : "Précisez la figure médicale à chercher & insérer (ex: anatomie de la peau, cycle du VIH...)"
                          : lang === "en"
                          ? "Specify details for the action or ask AI freely (table, diagram, image, or text edit)..."
                          : "Précisez les détails de l'action (tableau, schéma, image ou passage à reformuler)..."
                      }
                      className="min-w-[200px] flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                    />

                    {!hasContent ? (
                      <button
                        type="submit"
                        disabled={busyDraft || busyReformulate}
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:opacity-50"
                      >
                        {busyDraft || busyReformulate ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Sparkles className="h-3.5 w-3.5" />
                        )}
                        {busyDraft || busyReformulate ? t("m1.drafting") : t("m1.generate_ai")}
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          disabled={busyReformulate || busyDraft}
                          onClick={() => void handleAssistantCommandOrReformulate(true)}
                          title={
                            lang === "en"
                              ? "Execute your instruction (create table, diagram, search & insert image, or selectively reformulate text)"
                              : "Exécuter votre demande (créer un tableau, un schéma, insérer une image, ou reformuler sélectivement le texte)"
                          }
                          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:opacity-50"
                        >
                          {busyReformulate ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Wand2 className="h-3.5 w-3.5" />
                          )}
                          {busyReformulate
                            ? lang === "en"
                              ? "Working..."
                              : "Exécution..."
                            : lang === "en"
                            ? "Smart Action / Reformulate"
                            : "Exécuter / Reformuler"}
                        </button>
                        <button
                          type="button"
                          disabled={busyDraft || busyReformulate}
                          onClick={() =>
                            void handleDraftWithAI(aiInstruction.trim() || undefined)
                          }
                          title={
                            lang === "en"
                              ? "Fully regenerate the entire section from scratch"
                              : "Régénérer intégralement toute la section"
                          }
                          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
                        >
                          {busyDraft ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <RefreshCw className="h-3.5 w-3.5" />
                          )}
                          {busyDraft
                            ? t("m1.drafting")
                            : lang === "en"
                            ? "Full regenerate"
                            : "Tout régénérer"}
                        </button>
                      </>
                    )}
                  </form>
                  {assistantActionFeedback && (
                    <div className="mt-2 flex items-center justify-between gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900">
                      <span className="font-medium">{assistantActionFeedback.message}</span>
                      <button
                        type="button"
                        onClick={() => setAssistantActionFeedback(null)}
                        className="shrink-0 text-[11px] font-semibold text-emerald-700 hover:underline"
                      >
                        OK
                      </button>
                    </div>
                  )}
                  {activeSpec.id === "chap3_chronogramme" &&
                    (project.timeline_grid?.rows?.length ?? 0) > 0 && (
                      <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-sky-200 bg-sky-50/80 px-3 py-2 text-xs text-sky-950">
                        <div className="min-w-0 flex-1 text-[11px]">
                          <strong>
                            {lang === "en"
                              ? "Existing Gantt Timeline (synced with Module 2):"
                              : "Diagramme de Gantt déjà présent (synchronisé avec Module 2) :"}
                          </strong>{" "}
                          {lang === "en"
                            ? `${project.timeline_grid.rows.length} activities × ${project.timeline_grid.periods.length} periods — visible below in the Tables section.`
                            : `${project.timeline_grid.rows.length} activités × ${project.timeline_grid.periods.length} périodes — visible ci-dessous dans la section Tableaux.`}
                        </div>
                        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => {
                              document
                                .getElementById("m1-section-media-panel")
                                ?.scrollIntoView({ behavior: "smooth", block: "start" });
                            }}
                            className="inline-flex items-center gap-1 rounded-md border border-sky-300 bg-white px-2.5 py-1 text-[11px] font-semibold text-sky-800 hover:bg-sky-100"
                          >
                            {lang === "en" ? "View Gantt below" : "Voir le Gantt ci-dessous"}
                          </button>
                          <button
                            type="button"
                            disabled={busyReformulate || busyDraft}
                            onClick={async () => {
                              setBusyReformulate(true);
                              setAssistantActionFeedback(null);
                              onError("");
                              try {
                                const res = await api.runSectionAssistantCommand(
                                  project.project_id,
                                  "chap3_chronogramme",
                                  {
                                    instruction:
                                      aiInstruction.trim() ||
                                      (lang === "en"
                                        ? "Regenerate and update the Gantt activity timeline aligned with section III.1 study duration"
                                        : "Régénérer et mettre à jour le chronogramme des activités (Gantt) cohérent avec la durée de l'étude en III.1"),
                                    current_markdown: draftRef.current,
                                    action: "create_table",
                                  }
                                );
                                if (res.project) {
                                  onProjectUpdated(res.project);
                                }
                                setAssistantActionFeedback({
                                  actionType: res.action_type,
                                  message: res.message,
                                });
                                setAiInstruction("");
                              } catch (err) {
                                onError(err instanceof Error ? err.message : String(err));
                              } finally {
                                setBusyReformulate(false);
                              }
                            }}
                            className="inline-flex items-center gap-1 rounded-md bg-sky-700 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-sky-800 disabled:opacity-50"
                          >
                            {busyReformulate ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <RefreshCw className="h-3 w-3" />
                            )}
                            {lang === "en"
                              ? "Regenerate / Update Gantt"
                              : "Régénérer / Mettre à jour le Gantt"}
                          </button>
                        </div>
                      </div>
                    )}
                  {activeSpec.id === "annexe_fiche_collecte" && (
                    <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-emerald-200 bg-emerald-50/70 px-3 py-2 text-xs text-emerald-950">
                      <div className="min-w-0 flex-1 text-[11px]">
                        <strong>
                          {lang === "en"
                            ? "Dedicated CRF Extractor → Data Mask:"
                            : "Extracteur dédié Fiche → Masque de saisie :"}
                        </strong>{" "}
                        {crfExtractNote ||
                          (lang === "en"
                            ? "Isolates form boundaries and compiles every question, table, and choice non-destructively into Module 3."
                            : "Isole le début et la fin exacts de la fiche (tableaux, pointillés, choix) et synchronise sans perte vers le Module 3.")}
                      </div>
                      <button
                        type="button"
                        disabled={busyExtractCrf || busyDraft}
                        onClick={handleExtractAndSyncToMask}
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-emerald-700 px-3 py-1.5 text-[11px] font-semibold text-white shadow-xs hover:bg-emerald-800 disabled:opacity-50"
                      >
                        {busyExtractCrf ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="h-3.5 w-3.5" />
                        )}
                        {busyExtractCrf
                          ? lang === "en"
                            ? "Compiling Mask..."
                            : "Synchronisation..."
                          : lang === "en"
                            ? "Extract & Sync to Mask"
                            : "Extraire & synchroniser vers Masque de saisie"}
                      </button>
                    </div>
                  )}
                  {SUB_ANNEX_SECTION_IDS.has(activeSpec.id) && (
                    <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-indigo-200 bg-indigo-50/70 px-3 py-2 text-xs text-indigo-950">
                      <div className="min-w-0 flex-1 space-y-1 text-[11px]">
                        <div>
                          <strong>
                            {lang === "en"
                              ? "Faculty-Aware Sub-Appendix Generator:"
                              : "Générateur d'Annexe adapté à votre Faculté :"}
                          </strong>{" "}
                          {subAnnexFacultyNote ||
                            (lang === "en"
                              ? `Uses your real cover identity (${project.cover.faculty_en || project.cover.faculty_fr || "Faculty"}), Dean/Director & Ethics Committee.`
                              : `S'adapte automatiquement à votre établissement (${project.cover.faculty_fr || "Faculté"}), Doyen/Directeur et Comité d'Éthique.`)}
                        </div>
                        {(activeSpec.id === "annexe_notice_information" ||
                          activeSpec.id === "annexe_consentement") && (
                          <label className="inline-flex cursor-pointer items-center gap-1.5 font-medium text-indigo-900">
                            <input
                              type="checkbox"
                              checked={subAnnexBilingual}
                              onChange={(e) => setSubAnnexBilingual(e.target.checked)}
                              className="rounded border-indigo-300 text-indigo-700"
                            />
                            {lang === "en"
                              ? "Generate Bilingual Version (FR + EN)"
                              : "Générer en version bilingue (FR + EN)"}
                          </label>
                        )}
                      </div>
                      <button
                        type="button"
                        disabled={busySubAnnex || busyDraft}
                        onClick={handleGenerateFacultySubAnnex}
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-[#6C2826] px-3 py-1.5 text-[11px] font-semibold text-white shadow-xs hover:bg-[#561f1d] disabled:opacity-50"
                      >
                        {busySubAnnex ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Sparkles className="h-3.5 w-3.5" />
                        )}
                        {busySubAnnex
                          ? lang === "en"
                            ? "Generating..."
                            : "Génération..."
                          : lang === "en"
                            ? "Generate for My Faculty"
                            : "Générer / Actualiser selon ma Faculté"}
                      </button>
                    </div>
                  )}
                </div>
              )}

              {internalLibraryEntries.length > 0 && (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50/40 p-3 text-xs text-emerald-950">
                  <button
                    type="button"
                    onClick={() => {
                      setShowInternalLibrary((v) => !v);
                      onFeatureUsed?.("internal_library");
                    }}
                    className="flex w-full items-center justify-between gap-2 font-semibold text-emerald-900"
                  >
                    <span className="inline-flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5 text-emerald-700" />
                      {lang === "en"
                        ? `Section Internal Library (${internalLibraryEntries.length} secondary source(s))`
                        : `Bibliothèque interne de cette section (${internalLibraryEntries.length} source(s) secondaire(s))`}
                    </span>
                    {showInternalLibrary ? (
                      <ChevronUp className="h-4 w-4 text-emerald-700" />
                    ) : (
                      <ChevronDown className="h-4 w-4 text-emerald-700" />
                    )}
                  </button>
                  {showInternalLibrary && (
                    <div className="mt-2.5 space-y-2 border-t border-emerald-200/70 pt-2.5">
                      {internalLibraryEntries.map((entry) => (
                        <div
                          key={entry.entry_id}
                          className="rounded-lg border border-emerald-200/80 bg-white p-2.5"
                        >
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="font-semibold text-slate-900">
                              {entry.source_filename}
                            </span>
                            <span
                              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                                entry.citable
                                  ? "bg-emerald-100 text-emerald-800"
                                  : "bg-slate-100 text-slate-700"
                              }`}
                            >
                              {entry.citable
                                ? lang === "en"
                                  ? `Citable (${entry.source_kind})`
                                  : `Citable — ${entry.source_kind}`
                                : lang === "en"
                                  ? "Unreferenced draft"
                                  : "Brouillon non référencé"}
                            </span>
                            {entry.tables_markdown.length > 0 && (
                              <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-800">
                                {entry.tables_markdown.length}{" "}
                                {lang === "en" ? "table(s)" : "tableau(x)"}
                              </span>
                            )}
                            {entry.image_asset_ids.length > 0 && (
                              <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
                                {entry.image_asset_ids.length}{" "}
                                {lang === "en" ? "figure(s)" : "figure(s)"}
                              </span>
                            )}
                          </div>
                          {entry.citable && entry.vancouver_citation && (
                            <p className="mt-1 text-[11px] italic text-emerald-800">
                              {entry.vancouver_citation}
                            </p>
                          )}
                          {entry.content_markdown && (
                            <p className="mt-1.5 line-clamp-4 whitespace-pre-line text-[11px] leading-relaxed text-slate-700">
                              {entry.content_markdown}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

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
                <div className="space-y-2">
                  {lastDraftResult.used_internal_library_only && (
                    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-emerald-300 bg-emerald-50/70 p-3 text-xs text-emerald-950">
                      <div className="min-w-0 flex-1">
                        <div className="font-bold text-emerald-900">
                          {lang === "en"
                            ? "Drafted primarily from your Internal Library (WHO/CAMES compliant)"
                            : "Rédigé en priorité depuis votre bibliothèque interne (conforme OMS/CAMES)"}
                        </div>
                        <p className="mt-0.5 text-[11px] text-emerald-800">
                          {lang === "en"
                            ? `Built using ${lastDraftResult.internal_library_entries_used ?? internalLibraryEntries.length} internal source segment(s) without external bibliographic queries.`
                            : `Basé sur ${lastDraftResult.internal_library_entries_used ?? internalLibraryEntries.length} segment(s) de votre bibliothèque interne sans requête externe superflue.`}
                        </p>
                      </div>
                      {lastDraftResult.can_enrich_externally && (
                        <button
                          type="button"
                          disabled={busyDraft}
                          onClick={() =>
                            handleDraftWithAI(aiInstruction.trim() || undefined, true)
                          }
                          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-emerald-700 px-3 py-1.5 text-[11px] font-semibold text-white shadow-xs transition hover:bg-emerald-800 disabled:opacity-50"
                        >
                          {busyDraft ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Globe className="h-3.5 w-3.5" />
                          )}
                          {lang === "en"
                            ? "Enrich via PubMed / AJOL / OpenAlex"
                            : "Enrichir via PubMed / AJOL / OpenAlex"}
                        </button>
                      )}
                    </div>
                  )}
                  <div className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-3 text-xs text-indigo-950">
                    <strong>
                      {lang === "en" ? "Methodological Assistant:" : "Assistant méthodologique FMSB :"}
                    </strong>{" "}
                    {lastDraftResult.assistant_commentary.replace(/gemini[^,.;)]*/gi, "Smart Research")}
                  </div>
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

              {detectedPlaceholders.length > 0 && (
                <div className="rounded-xl border-2 border-amber-400 bg-amber-50/95 p-3.5 text-xs text-rose-950 shadow-sm">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" />
                    <div className="min-w-0 flex-1">
                      <div className="font-bold text-rose-900">
                        {lang === "en"
                          ? `${detectedPlaceholders.length} investigator placeholder(s) to complete in this section:`
                          : `${detectedPlaceholders.length} champ(s) [À compléter par l'investigateur] à renseigner dans cette section :`}
                      </div>
                      <p className="mt-0.5 text-[11px] text-amber-900">
                        {lang === "en"
                          ? "Replace the highlighted yellow/red markers below directly in the editor with your verified local or institutional figures."
                          : "Remplacez directement les balises surlignées en jaune et rouge dans l'éditeur ci-dessous par vos données locales ou institutionnelles."}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {detectedPlaceholders.map((ph, idx) => (
                          <span
                            key={idx}
                            className="sr-placeholder-highlight inline-block text-[11px]"
                          >
                            {ph}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {pendingReformulation && (
                <ReformulationReviewBanner
                  preview={pendingReformulation}
                  busy={busyReformulate}
                  applying={busyApplyReformulation}
                  onAccept={() => void handleApplyPendingReformulation()}
                  onRetry={(refined) => void handleRetryPendingReformulation(refined)}
                  onCancel={() => setPendingReformulation(null)}
                />
              )}

              <div>
                <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="block text-sm font-semibold text-slate-800">
                      {lang === "en"
                        ? "Section Content (Rich Text Editor)"
                        : "Contenu de la section (Éditeur riche FMSB)"}
                    </label>
                    {autoSaveState === "saving" && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-0.5 text-[11px] font-medium text-indigo-700">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        {lang === "en" ? "Auto-saving..." : "Enregistrement auto..."}
                      </span>
                    )}
                    {autoSaveState === "dirty" && isDirty && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-[11px] font-semibold text-amber-800">
                        {lang === "en" ? "Unsaved edits" : "Modifications non enregistrées"}
                      </span>
                    )}
                    {autoSaveState === "saved" && !isDirty && lastSavedAt && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-[11px] font-medium text-emerald-700">
                        <CheckCircle2 className="h-3 w-3" />
                        {lang === "en"
                          ? `Saved at ${lastSavedAt}`
                          : `Enregistré à ${lastSavedAt}`}
                      </span>
                    )}
                    {autoSaveState === "error" && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2.5 py-0.5 text-[11px] font-semibold text-rose-700">
                        {lang === "en"
                          ? "Auto-save failed — click Save"
                          : "Échec auto-save — cliquez sur Enregistrer"}
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void handleToggleVersionHistory()}
                      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-semibold transition ${
                        showVersionHistory
                          ? "border-indigo-300 bg-indigo-50 text-indigo-800"
                          : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                      }`}
                      title={
                        lang === "en"
                          ? "Restore a previous version or generation"
                          : "Revenir sur une version ou génération précédente"
                      }
                    >
                      <History className="h-3.5 w-3.5" />
                      {lang === "en" ? "History / Undo" : "Historique / Retour"}
                    </button>
                    <button
                      type="button"
                      disabled={saving || (!isDirty && autoSaveState === "saved")}
                      onClick={() =>
                        void handleSectionSaveOrValidate(
                          activeSpec.id,
                          activeSpec.title,
                          draft,
                          activeState?.status && activeState.status !== "pending"
                            ? activeState.status
                            : "in_progress",
                          notes,
                          draftHtml
                        )
                      }
                      className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-semibold transition disabled:opacity-50 ${
                        isDirty
                          ? "bg-amber-500 text-white shadow-sm hover:bg-amber-600"
                          : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                      }`}
                      title={lang === "en" ? "Save changes (Ctrl+S)" : "Enregistrer (Ctrl+S)"}
                    >
                      <Save className="h-3.5 w-3.5" />
                      {lang === "en" ? "Save" : "Enregistrer"}
                    </button>
                  </div>
                </div>

                {showVersionHistory && (
                  <div className="mb-2.5 rounded-xl border border-indigo-200 bg-indigo-50/40 p-3 text-xs">
                    <div className="flex items-center justify-between gap-2 pb-2 border-b border-indigo-100">
                      <span className="font-bold text-indigo-950">
                        {lang === "en"
                          ? "Section Version History (1-click restore)"
                          : "Historique des versions de cette section (retour en 1 clic)"}
                      </span>
                      <button
                        type="button"
                        onClick={() => setShowVersionHistory(false)}
                        className="rounded p-0.5 text-slate-400 hover:text-slate-700"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    {loadingVersions ? (
                      <div className="py-3 text-center text-slate-500">
                        <Loader2 className="mx-auto h-4 w-4 animate-spin" />
                      </div>
                    ) : sectionVersions.length === 0 ? (
                      <p className="py-2 text-slate-500">
                        {lang === "en"
                          ? "No previous versions recorded yet for this section."
                          : "Aucune version précédente enregistrée pour cette section."}
                      </p>
                    ) : (
                      <div className="mt-2 max-h-48 space-y-1.5 overflow-y-auto">
                        {sectionVersions.map((v) => (
                          <div
                            key={v.version_id}
                            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2"
                          >
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-slate-700">
                                  {v.source}
                                </span>
                                <span className="text-[11px] font-medium text-slate-800 truncate">
                                  {v.note || (lang === "en" ? "Saved snapshot" : "Sauvegarde")}
                                </span>
                                <span className="text-[10px] text-slate-400">
                                  {new Date(v.created_at).toLocaleString(
                                    lang === "en" ? "en-US" : "fr-FR",
                                    { dateStyle: "short", timeStyle: "short" }
                                  )}
                                </span>
                              </div>
                              <p className="mt-0.5 truncate text-[11px] text-slate-500">
                                {(v.content_markdown || "").slice(0, 140)}
                              </p>
                            </div>
                            <button
                              type="button"
                              disabled={restoringVersionId !== null}
                              onClick={() => void handleRestoreVersion(v.version_id)}
                              className="inline-flex shrink-0 items-center gap-1 rounded-md border border-indigo-300 bg-indigo-50 px-2.5 py-1 text-[11px] font-semibold text-indigo-800 hover:bg-indigo-100 disabled:opacity-50"
                            >
                              {restoringVersionId === v.version_id ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                <RotateCcw className="h-3 w-3" />
                              )}
                              {lang === "en" ? "Restore" : "Restaurer"}
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {isAutoTableSection && draftHtml.toLowerCase().includes("<table") ? (
                  <div className="overflow-x-auto rounded-xl border border-slate-300 bg-white p-5 shadow-sm">
                    <div
                      className="prose prose-sm max-w-none text-slate-900"
                      dangerouslySetInnerHTML={{ __html: draftHtml }}
                    />
                  </div>
                ) : (
                  <RichTextEditor
                    contentHtml={draftHtml}
                    contentMarkdown={draft}
                    wordCount={wordCount}
                    selectionBusy={busyReformulate}
                    onSelectionAction={(selectedText, action, instr) =>
                      void handleSelectionTransform(selectedText, action, instr)
                    }
                    onChange={(html, text) => {
                      setDraftHtml(html);
                      setDraft(text);
                    }}
                  />
                )}
              </div>

              {showMediaPanel && (
                <div id="m1-section-media-panel">
                  <SectionMediaPanel
                    project={project}
                    sectionId={activeSpec.id}
                    sectionText={draft}
                    sectionHtml={draftHtml}
                    onProjectUpdated={onProjectUpdated}
                    onError={onError}
                  />
                </div>
              )}

              {/* Compact Collapsible Personal Notes + Save Bar */}
              <div className="border-t border-slate-100 pt-3">
                {showPersonalNotes ? (
                  <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <label className="text-xs font-semibold uppercase tracking-wide text-slate-600">
                        {lang === "en"
                          ? "Candidate Personal Notes"
                          : "Notes personnelles de l'étudiant"}
                      </label>
                      <button
                        type="button"
                        onClick={() => setShowPersonalNotes(false)}
                        className="text-[11px] font-medium text-slate-500 hover:text-slate-700"
                      >
                        {lang === "en" ? "Hide" : "Masquer"}
                      </button>
                    </div>
                    <textarea
                      rows={2}
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder={
                        lang === "en" ? "Internal notes..." : "Notes internes..."
                      }
                      className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white p-2.5 text-xs focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                ) : null}

                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3 text-xs text-slate-500">
                    <span>
                      {wordCount}{" "}
                      {lang === "en"
                        ? wordCount === 1
                          ? "word"
                          : "words"
                        : wordCount <= 1
                        ? "mot"
                        : "mots"}
                    </span>
                    {!showPersonalNotes && (
                      <button
                        type="button"
                        onClick={() => setShowPersonalNotes(true)}
                        className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
                      >
                        {notes.trim()
                          ? lang === "en"
                            ? "View personal notes"
                            : "Voir notes personnelles"
                          : lang === "en"
                          ? "+ Personal notes"
                          : "+ Notes personnelles"}
                      </button>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2.5">
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() =>
                        handleSectionSaveOrValidate(
                          activeSpec.id,
                          activeSpec.title,
                          draft,
                          "in_progress",
                          notes,
                          draftHtml
                        )
                      }
                      className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                    >
                      {t("m1.save_draft")}
                    </button>
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() =>
                        handleSectionSaveOrValidate(
                          activeSpec.id,
                          activeSpec.title,
                          draft,
                          "validated",
                          notes,
                          draftHtml
                        )
                      }
                      className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                    >
                      <CheckCircle2 className="h-4 w-4" />
                      {t("m1.validate_step")}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
      </div>

      {/* Real-Time Multi-Task Pipeline Steps Side Panel */}
      <PipelineProgressWidget
        tasks={tracker.tasks}
        task={tracker.activeTask}
        elapsedSec={tracker.localElapsedSec}
        visible={tracker.visible}
        onDismiss={tracker.dismiss}
        onDismissTask={tracker.dismissTask}
      />
    </>
  );
}
