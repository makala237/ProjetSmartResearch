import { AlertCircle, CheckCircle2, Cpu, Loader2, X } from "lucide-react";
import type { TrackedPipelineTask } from "../hooks/usePipelineTracker";
import { useLanguage } from "../i18n/LanguageContext";
import type { PipelineProgressSnapshot } from "../types/protocol";

interface PipelineProgressWidgetProps {
  tasks?: TrackedPipelineTask[];
  task?: PipelineProgressSnapshot | null;
  elapsedSec?: number;
  visible: boolean;
  onDismiss: () => void;
  onDismissTask?: (taskId: string) => void;
}

const FRIENDLY_STEP_LABELS_FR: Record<string, string> = {
  init: "Préparation de votre assistant...",
  memory: "Relecture de vos autres sections pour garder la cohérence...",
  gap_check: "Lecture de votre texte actuel et des consignes FMSB...",
  decision_route: "Sélection des références et données utiles...",
  postprocess: "Mise en forme finale et vérification qualité...",
  coherence_save: "Harmonisation avec le protocole et sauvegarde...",
  coherence_check: "Vérification de l'harmonie avec le reste du protocole...",
  llm_harmonize: "Ajustement du texte pour harmoniser la section...",
  prepare: "Lecture des articles scientifiques sélectionnés...",
  segmenting: "Repérage des passages clés pour vos sections...",
  lists_sync: "Mise à jour automatique du sommaire et des listes...",
  vancouver_check: "Mise aux normes Vancouver de la bibliographie...",
  completed: "Terminé !",
};

const FRIENDLY_STEP_LABELS_EN: Record<string, string> = {
  init: "Preparing your assistant...",
  memory: "Reviewing your other sections to keep everything consistent...",
  gap_check: "Reading your current text and FMSB guidelines...",
  decision_route: "Selecting relevant references and data...",
  postprocess: "Final formatting and quality check...",
  coherence_save: "Checking consistency and saving...",
  coherence_check: "Checking consistency with the rest of your protocol...",
  llm_harmonize: "Updating section text for consistency...",
  prepare: "Reading selected scientific articles...",
  segmenting: "Highlighting key passages for your sections...",
  lists_sync: "Automatically updating table of contents and lists...",
  vancouver_check: "Formatting bibliography in Vancouver style...",
  completed: "Done!",
};

function formatFriendlyStepLabel(
  stepKey: string,
  rawLabel: string,
  lang: "fr" | "en"
): string {
  const dict = lang === "en" ? FRIENDLY_STEP_LABELS_EN : FRIENDLY_STEP_LABELS_FR;
  if (dict[stepKey]) {
    return dict[stepKey];
  }
  const clean = (rawLabel || "")
    .replace(/pipeline\s+ia/gi, lang === "en" ? "assistant" : "assistant")
    .replace(/pipeline/gi, lang === "en" ? "process" : "assistant")
    .replace(
      /mémoire transversale/gi,
      lang === "en" ? "overall consistency" : "cohérence globale"
    )
    .replace(
      /mémoire de travail/gi,
      lang === "en" ? "project summary" : "synthèse du projet"
    )
    .replace(/working memory/gi, "project context")
    .replace(
      /orchestration documentaire/gi,
      lang === "en" ? "reference selection" : "sélection des références"
    )
    .replace(/rédaction scientifique de/gi, "Rédaction de")
    .replace(/déterministe/gi, "automatique")
    .replace(/\bLLM\b/gi, "IA")
    .trim();
  return clean || (lang === "en" ? "Working..." : "Traitement en cours...");
}

function formatFriendlyDetail(rawDetail: string): string {
  if (!rawDetail) return "";
  return rawDetail
    .replace(/gemini[^,.;)]*/gi, "Smart Research")
    .replace(/\bLLM\b/gi, "IA")
    .replace(/working memory/gi, "context")
    .replace(/mémoire transversale/gi, "cohérence")
    .trim();
}

export function PipelineProgressWidget({
  tasks,
  task,
  elapsedSec = 0,
  visible,
  onDismiss,
  onDismissTask,
}: PipelineProgressWidgetProps) {
  const { lang, t } = useLanguage();

  const resolvedTasks: TrackedPipelineTask[] =
    tasks && tasks.length > 0
      ? tasks
      : task
      ? [
          {
            taskId: task.task_id,
            snapshot: task,
            startedMs: Date.now(),
            elapsedSec,
            titleFr: task.title,
            titleEn: task.title,
          },
        ]
      : [];

  if (!visible || resolvedTasks.length === 0) return null;

  const runningCount = resolvedTasks.filter(
    (item) => item.snapshot.status === "running"
  ).length;
  const isMulti = resolvedTasks.length > 1;

  return (
    <div
      data-testid="pipeline-progress-widget"
      role="status"
      aria-live="polite"
      className="fixed bottom-4 right-4 z-50 flex max-h-[82vh] w-[365px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white/95 shadow-2xl ring-1 ring-slate-900/5 backdrop-blur-md"
    >
      {/* Top accent bar */}
      <div
        className={`h-1 w-full shrink-0 ${
          resolvedTasks.some((x) => x.snapshot.status === "error")
            ? "bg-rose-500"
            : runningCount === 0
            ? "bg-emerald-500"
            : "bg-gradient-to-r from-[#004A8F] via-[#0284C7] to-[#1FA84F]"
        }`}
      />

      {/* Panel Header */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-100 bg-slate-50/80 px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <span
            data-testid="pipeline-brand-badge"
            className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2.5 py-0.5 text-[10px] font-bold text-[#004A8F] ring-1 ring-[#004A8F]/15"
          >
            <Cpu className="h-3 w-3 shrink-0" />
            {t("pipe.brand_badge")}
          </span>
          {isMulti && (
            <span className="inline-flex items-center gap-1 rounded-full bg-indigo-600 px-2 py-0.5 text-[10px] font-bold text-white shadow-2xs">
              {runningCount > 0 ? (
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
              ) : null}
              {lang === "en"
                ? `${resolvedTasks.length} actions`
                : `${resolvedTasks.length} actions`}
            </span>
          )}
        </div>

        <button
          type="button"
          onClick={onDismiss}
          aria-label={t("pipe.dismiss")}
          className="rounded-lg p-1 text-slate-400 transition hover:bg-slate-200/60 hover:text-slate-700"
          title={
            isMulti
              ? lang === "en"
                ? "Hide all"
                : "Tout masquer"
              : t("pipe.dismiss")
          }
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Scrollable Stack of Active Task Cards */}
      <div className="space-y-2.5 overflow-y-auto p-3">
        {resolvedTasks.map((item) => {
          const snap = item.snapshot;
          const isRunning = snap.status === "running";
          const isError = snap.status === "error";
          const isDone = snap.status === "done";
          const shownElapsed = Math.max(
            item.elapsedSec,
            snap.elapsed_sec || 0
          ).toFixed(1);
          const pct = Math.min(100, Math.max(0, snap.progress_pct));

          return (
            <div
              key={item.taskId}
              className={`rounded-xl border p-3 transition ${
                isError
                  ? "border-rose-200 bg-rose-50/40"
                  : isDone
                  ? "border-emerald-200 bg-emerald-50/40"
                  : "border-slate-200/90 bg-white shadow-2xs"
              }`}
            >
              {/* Task Card Header */}
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  {isRunning && (
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[#004A8F]" />
                  )}
                  {isDone && (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                  )}
                  {isError && (
                    <AlertCircle className="h-4 w-4 shrink-0 text-rose-600" />
                  )}
                  <div className="min-w-0">
                    <div className="truncate text-xs font-bold text-slate-900">
                      {snap.title}
                    </div>
                    <div className="text-[10px] font-medium text-slate-500">
                      {isRunning
                        ? t("pipe.running")
                        : isError
                        ? t("pipe.error")
                        : t("pipe.done")}{" "}
                      · {shownElapsed} s
                    </div>
                  </div>
                </div>

                <div className="flex shrink-0 items-center gap-1.5">
                  <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold tabular-nums text-slate-700">
                    {pct}%
                  </span>
                  {isMulti && onDismissTask && (
                    <button
                      type="button"
                      onClick={() => onDismissTask(item.taskId)}
                      className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                      title={lang === "en" ? "Close task" : "Fermer cette tâche"}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
              </div>

              {/* Progress Bar */}
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                <div
                  className={`h-full rounded-full transition-all duration-300 ${
                    isError
                      ? "bg-rose-500"
                      : isDone
                      ? "bg-emerald-500"
                      : "bg-gradient-to-r from-[#004A8F] to-[#0284C7]"
                  }`}
                  style={{ width: `${Math.min(100, Math.max(5, pct))}%` }}
                />
              </div>

              {/* Friendly Step List */}
              <div className="mt-2 space-y-1">
                {snap.steps.map((step) => {
                  const friendlyLabel = formatFriendlyStepLabel(
                    step.key,
                    step.label,
                    lang
                  );
                  const friendlyDetail = formatFriendlyDetail(step.detail);
                  return (
                    <div
                      key={`${step.key}-${step.timestamp}`}
                      className={`flex items-start gap-2 rounded-lg px-2 py-1 text-[11px] ${
                        step.status === "running"
                          ? "bg-sky-50/70 font-semibold text-slate-900"
                          : step.status === "error"
                          ? "bg-rose-50 font-semibold text-rose-800"
                          : "text-slate-600"
                      }`}
                    >
                      {step.status === "running" && (
                        <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin text-[#004A8F]" />
                      )}
                      {step.status === "done" && (
                        <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-600" />
                      )}
                      {step.status === "error" && (
                        <AlertCircle className="mt-0.5 h-3 w-3 shrink-0 text-rose-600" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="leading-snug">{friendlyLabel}</div>
                        {friendlyDetail && (
                          <div className="truncate text-[10px] font-normal text-slate-500">
                            {friendlyDetail}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {isError && snap.error_message && (
                <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-[10px] text-rose-800">
                  {snap.error_message}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
