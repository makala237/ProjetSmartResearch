import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  Circle,
  Clock,
  FileText,
  Globe,
  Loader2,
  MessageSquarePlus,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  Wand2,
} from "lucide-react";
import { api } from "../api/client";
import type {
  AuditResult,
  CanvasSectionSpec,
  CoverMetadata,
  DraftResult,
  ProtocolProject,
  SectionOrchestrationTrace,
  StepStatus,
  SupervisorInfo,
} from "../types/protocol";
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

function formatSupervisorSummary(sup: SupervisorInfo): string {
  return [sup.name, sup.rank, sup.specialty]
    .map((x) => x.trim())
    .filter(Boolean)
    .join(", ");
}

function emptySupervisor(): SupervisorInfo {
  return { name: "", rank: "", specialty: "" };
}

function withStructuredSupervisors(cov: CoverMetadata): CoverMetadata {
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
    director_info: dirInfo,
    co_directors_info: codirsInfo,
  };
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
}: Module1Props) {
  const activeSpec =
    canvas.find((s) => s.id === selectedSectionId) ?? canvas[0];
  const activeState = project.sections[activeSpec.id];

  const [draft, setDraft] = useState(activeState?.content_markdown ?? "");
  const [draftHtml, setDraftHtml] = useState(activeState?.content_html ?? "");
  const [notes, setNotes] = useState(activeState?.user_notes ?? "");
  const [aiInstruction, setAiInstruction] = useState("");
  const [coverForm, setCoverForm] = useState<CoverMetadata>(() =>
    withStructuredSupervisors(project.cover)
  );
  const [coDirDraft, setCoDirDraft] = useState<SupervisorInfo>(emptySupervisor);
  const [translatingDeptDir, setTranslatingDeptDir] = useState<"fr" | "en" | null>(null);
  const [translateModelBadge, setTranslateModelBadge] = useState<string | null>(null);
  const deptTimerRef = useRef<number | null>(null);
  const deptReqSeqRef = useRef(0);

  const scheduleDepartmentTranslation = (value: string, sourceLang: "fr" | "en") => {
    if (deptTimerRef.current !== null) {
      window.clearTimeout(deptTimerRef.current);
    }
    const trimmed = value.trim();
    if (!trimmed) {
      setTranslatingDeptDir(null);
      return;
    }
    const seq = ++deptReqSeqRef.current;
    const targetLang = sourceLang === "fr" ? "en" : "fr";
    deptTimerRef.current = window.setTimeout(async () => {
      setTranslatingDeptDir(sourceLang);
      try {
        const res = await api.translateDepartment({
          text: trimmed,
          source_lang: sourceLang,
          target_lang: targetLang,
        });
        if (seq !== deptReqSeqRef.current) return;
        setTranslateModelBadge(res.model_used);
        if (res.translated_text) {
          setCoverForm((prev) => ({
            ...prev,
            ...(targetLang === "en"
              ? { department_en: res.translated_text }
              : { department_fr: res.translated_text }),
          }));
        }
      } catch {
        // Keep user input untouched if live translation fails transiently
      } finally {
        if (seq === deptReqSeqRef.current) {
          setTranslatingDeptDir(null);
        }
      }
    }, 420);
  };

  const [busyDraft, setBusyDraft] = useState(false);
  const [busyAudit, setBusyAudit] = useState(false);
  const [busyFix, setBusyFix] = useState(false);
  const [busyToc, setBusyToc] = useState(false);
  const [busyRefs, setBusyRefs] = useState(false);
  const [busyAntigravity, setBusyAntigravity] = useState(false);
  const [lastRefDecisionNote, setLastRefDecisionNote] = useState<string | null>(null);
  const [lastDraftResult, setLastDraftResult] = useState<DraftResult | null>(null);
  const [lastAuditResult, setLastAuditResult] = useState<AuditResult | null>(null);
  const [lastOrchestration, setLastOrchestration] =
    useState<SectionOrchestrationTrace | null>(null);
  const [showMemoryPanel, setShowMemoryPanel] = useState(false);
  const [workingMemoryMd, setWorkingMemoryMd] = useState<string>("");
  const [busyMemory, setBusyMemory] = useState(false);

  async function handleToggleWorkingMemory() {
    if (showMemoryPanel) {
      setShowMemoryPanel(false);
      return;
    }
    setBusyMemory(true);
    try {
      const res = await api.getWorkingMemory(project.project_id);
      setWorkingMemoryMd(res.markdown);
      setShowMemoryPanel(true);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyMemory(false);
    }
  }

  async function handleRefreshWorkingMemory() {
    setBusyMemory(true);
    onError("");
    try {
      const res = await api.refreshWorkingMemory(project.project_id);
      setWorkingMemoryMd(res.markdown);
      setShowMemoryPanel(true);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyMemory(false);
    }
  }

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
        `${res.references_count} références numérotées (${res.corrected_count} normalisées) · ${res.decision_model} — ${res.decision_summary}`
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
    setLastDraftResult(null);
    setLastAuditResult(null);
    setLastRefDecisionNote(null);
    setLastOrchestration(null);
  }, [activeSpec.id, activeState?.content_markdown, activeState?.content_html, activeState?.user_notes]);

  useEffect(() => {
    setCoverForm(withStructuredSupervisors(project.cover));
  }, [project.cover]);

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
      setLastOrchestration(res.result.orchestration_trace ?? null);
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
      setLastOrchestration(res.result.orchestration_trace ?? null);
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
      setLastOrchestration(res.orchestration_trace);
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
      setLastOrchestration(res.result.orchestration_trace ?? null);
      setLastAuditResult(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyFix(false);
    }
  }

  return (
    <div className="space-y-5">
      {/* Top Research / Import / References Panel */}
      <ResearchPanel
        project={project}
        onProjectUpdated={onProjectUpdated}
        onError={onError}
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Sidebar: Step-by-step FMSB Canvas */}
        <aside className="lg:col-span-4 xl:col-span-3">
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

            <div className="max-h-[68vh] space-y-4 overflow-y-auto pr-1">
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
                      const isSelected = item.id === activeSpec.id;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => onSelectSection(item.id)}
                          className={`flex w-full items-start justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition ${
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
                            <span className="line-clamp-2 leading-snug">
                              {item.title}
                            </span>
                          </span>
                          {segCount > 0 && (
                            <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">
                              {segCount}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
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
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="md:col-span-2">
                  <label className="block text-xs font-medium text-slate-600">
                    Titre complet du protocole (encadré 18 à 24 pt)
                  </label>
                  <input
                    type="text"
                    value={coverForm.title}
                    onChange={(e) =>
                      setCoverForm({ ...coverForm, title: e.target.value })
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-600">
                    Diplôme visé
                  </label>
                  <input
                    type="text"
                    value={coverForm.degree_label}
                    onChange={(e) =>
                      setCoverForm({ ...coverForm, degree_label: e.target.value })
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-600">
                    Année académique
                  </label>
                  <input
                    type="text"
                    value={coverForm.academic_year}
                    onChange={(e) =>
                      setCoverForm({
                        ...coverForm,
                        academic_year: e.target.value,
                      })
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div className="sm:col-span-2 grid grid-cols-1 gap-3 sm:grid-cols-2 rounded-lg border border-indigo-100 bg-indigo-50/40 p-3">
                  <div className="sm:col-span-2 flex items-center justify-between text-[11px]">
                    <span className="font-semibold text-indigo-950">
                      Département académique (bilingue FR ↔ EN)
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-0.5 font-medium text-indigo-800">
                      <Sparkles className="h-3 w-3" />
                      {translatingDeptDir
                        ? "Traduction Gemini 3.8 Live..."
                        : translateModelBadge
                        ? `Auto FR ↔ EN (${translateModelBadge})`
                        : "Gemini 3.8 Live · Traduction auto FR ↔ EN"}
                    </span>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-600">
                      Département (FR)
                    </label>
                    <input
                      type="text"
                      value={coverForm.department_fr}
                      onChange={(e) => {
                        const val = e.target.value;
                        setCoverForm((prev) => ({ ...prev, department_fr: val }));
                        scheduleDepartmentTranslation(val, "fr");
                      }}
                      className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-600">
                      Department (EN)
                    </label>
                    <input
                      type="text"
                      value={coverForm.department_en}
                      onChange={(e) => {
                        const val = e.target.value;
                        setCoverForm((prev) => ({ ...prev, department_en: val }));
                        scheduleDepartmentTranslation(val, "en");
                      }}
                      className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-600">
                    Noms & Prénoms de l'étudiant(e)
                  </label>
                  <input
                    type="text"
                    value={coverForm.author_name}
                    onChange={(e) =>
                      setCoverForm({ ...coverForm, author_name: e.target.value })
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-600">
                    Matricule
                  </label>
                  <input
                    type="text"
                    value={coverForm.matricule}
                    onChange={(e) =>
                      setCoverForm({ ...coverForm, matricule: e.target.value })
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div className="sm:col-span-2 rounded-lg border border-slate-200 bg-slate-50/70 p-3.5">
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
                        placeholder="Ex : Pr ESSI Marie-José"
                        value={coverForm.director_info?.name ?? ""}
                        onChange={(e) => {
                          const nextDir = {
                            ...(coverForm.director_info ?? emptySupervisor()),
                            name: e.target.value,
                          };
                          setCoverForm({
                            ...coverForm,
                            director_info: nextDir,
                            director: formatSupervisorSummary(nextDir),
                          });
                        }}
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-semibold focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-[11px] font-medium text-slate-600">
                        Grade (Times New Roman 12)
                      </label>
                      <input
                        type="text"
                        placeholder="Ex : Professeure Titulaire"
                        value={coverForm.director_info?.rank ?? ""}
                        onChange={(e) => {
                          const nextDir = {
                            ...(coverForm.director_info ?? emptySupervisor()),
                            rank: e.target.value,
                          };
                          setCoverForm({
                            ...coverForm,
                            director_info: nextDir,
                            director: formatSupervisorSummary(nextDir),
                          });
                        }}
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-[11px] font-medium text-slate-600">
                        Spécialité (Times New Roman 12)
                      </label>
                      <input
                        type="text"
                        placeholder="Ex : Anthropologie médicale"
                        value={coverForm.director_info?.specialty ?? ""}
                        onChange={(e) => {
                          const nextDir = {
                            ...(coverForm.director_info ?? emptySupervisor()),
                            specialty: e.target.value,
                          };
                          setCoverForm({
                            ...coverForm,
                            director_info: nextDir,
                            director: formatSupervisorSummary(nextDir),
                          });
                        }}
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                  </div>
                </div>

                <div className="sm:col-span-2 rounded-lg border border-slate-200 bg-slate-50/70 p-3.5">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-bold uppercase tracking-wide text-slate-700">
                      Co-directeur(s) ({(coverForm.co_directors_info ?? []).length})
                    </span>
                    <span className="text-[11px] text-slate-500">
                      Nom en 14 pt gras · Grade et Spécialité en 12 pt
                    </span>
                  </div>

                  {(coverForm.co_directors_info ?? []).length > 0 && (
                    <div className="mb-3 space-y-2">
                      {(coverForm.co_directors_info ?? []).map((cd, idx) => (
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
                          <button
                            type="button"
                            onClick={() => {
                              const nextList = (
                                coverForm.co_directors_info ?? []
                              ).filter((_, i) => i !== idx);
                              setCoverForm({
                                ...coverForm,
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
                        </div>
                      ))}
                    </div>
                  )}

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
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-semibold focus:border-indigo-500 focus:outline-none"
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
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
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
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
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
                          ...(coverForm.co_directors_info ?? []),
                          entry,
                        ];
                        setCoverForm({
                          ...coverForm,
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
                </div>
              </div>
              <div className="mt-4 flex flex-wrap justify-end gap-3">
                <button
                  type="button"
                  disabled={saving}
                  onClick={async () => {
                    let finalCodirs = [...(coverForm.co_directors_info ?? [])];
                    if (
                      coDirDraft.name.trim() ||
                      coDirDraft.rank.trim() ||
                      coDirDraft.specialty.trim()
                    ) {
                      finalCodirs.push({
                        name: coDirDraft.name.trim(),
                        rank: coDirDraft.rank.trim(),
                        specialty: coDirDraft.specialty.trim(),
                      });
                      setCoDirDraft(emptySupervisor());
                    }
                    const dirInfo = coverForm.director_info ?? emptySupervisor();
                    const payload: CoverMetadata = {
                      ...coverForm,
                      director_info: dirInfo,
                      director: formatSupervisorSummary(dirInfo),
                      co_directors_info: finalCodirs,
                      co_directors: finalCodirs
                        .map(formatSupervisorSummary)
                        .filter(Boolean),
                    };
                    setCoverForm(payload);
                    await onSaveCover(payload);
                    await onSaveSection("cover_page", payload.title, "validated", notes);
                  }}
                  className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  <CheckCircle2 className="h-4 w-4" />
                  Enregistrer &amp; Valider la page de garde
                </button>
              </div>
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

              {/* Segments assigned to this section by 3.1-flash-lite */}
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
                        : "Valider & Formater en Liste Vancouver (Gemini Decision 1)"}
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
                  ].includes(activeSpec.id) && (
                    <button
                      type="button"
                      disabled={busyAntigravity}
                      onClick={() => handleMobilizeAntigravity()}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-rose-300 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-[#6C2826] hover:bg-rose-100 disabled:opacity-50"
                      title="Charger Antigravity de rechercher des références Vancouver et figures pour cette section"
                    >
                      {busyAntigravity ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Globe className="h-3.5 w-3.5" />
                      )}
                      {busyAntigravity
                        ? "Antigravity en cours..."
                        : "Antigravity (Réf. Vancouver & Figures)"}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busyMemory}
                    onClick={handleToggleWorkingMemory}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50/70 px-3 py-1.5 text-xs font-semibold text-indigo-800 hover:bg-indigo-100 disabled:opacity-50"
                    title="Consulter le fichier de Mémoire de Travail (résumés Gemini 3.8 Live)"
                  >
                    <BookOpen className="h-3.5 w-3.5" />
                    {showMemoryPanel ? "Masquer Mémoire" : "Mémoire de Travail"}
                  </button>
                </div>
              </div>

              {showMemoryPanel && (
                <div className="rounded-xl border border-indigo-200 bg-indigo-50/40 p-3.5 text-xs text-slate-800">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <strong className="text-indigo-950">
                        Mémoire de Travail Persistante (Gemini Decision 1 → Gemini 3.8 Live)
                      </strong>
                      <p className="text-[11px] text-slate-600">
                        À chaque modification, le résumé de la section est régénéré et écrase le précédent ; les résumés en amont guident toutes les sections suivantes.
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={busyMemory}
                      onClick={handleRefreshWorkingMemory}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                    >
                      {busyMemory && <Loader2 className="h-3 w-3 animate-spin" />}
                      Actualiser toute la mémoire (Gemini 3.8 Live)
                    </button>
                  </div>
                  <pre className="mt-2.5 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg border border-indigo-100 bg-white p-3 font-mono text-[11px] text-slate-700">
                    {workingMemoryMd || "(Aucun résumé enregistré pour le moment.)"}
                  </pre>
                </div>
              )}

              {lastOrchestration && (
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-800">
                  <div className="flex flex-wrap items-center gap-2 font-semibold text-[#6C2826]">
                    <span>Orchestration Tri-Moteur :</span>
                    <span className="rounded bg-white px-2 py-0.5 text-[11px] text-slate-700 ring-1 ring-slate-200">
                      {lastOrchestration.initial_text_present
                        ? "Texte initial présent"
                        : "Section vide"}
                    </span>
                    <span className="rounded bg-white px-2 py-0.5 text-[11px] text-indigo-700 ring-1 ring-indigo-200">
                      Mémoire : {lastOrchestration.memory_sections_count} section(s)
                    </span>
                    <span className="rounded bg-white px-2 py-0.5 text-[11px] text-emerald-700 ring-1 ring-emerald-200">
                      {lastOrchestration.decision_model}
                    </span>
                    {lastOrchestration.antigravity_called && (
                      <span className="rounded bg-[#6C2826] px-2 py-0.5 text-[11px] text-white">
                        Antigravity (+{lastOrchestration.added_references_count} réf. Vancouver, +{lastOrchestration.added_images_count} fig.)
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-[11px] text-slate-600">
                    {lastOrchestration.routing_summary}
                  </p>
                </div>
              )}

              {activeSpec.id === "references" && lastRefDecisionNote && (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50/70 p-3 text-xs text-emerald-950">
                  <strong>Validation Vancouver :</strong> {lastRefDecisionNote}
                </div>
              )}

              {lastDraftResult && (
                <div className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-3 text-xs text-indigo-950">
                  <strong>Modèle ({lastDraftResult.model_used}) :</strong>{" "}
                  {lastDraftResult.assistant_commentary}
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
                        <li key={i}>{n}</li>
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
