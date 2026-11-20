import { useEffect, useRef } from "react";
import { AlertCircle, CheckCircle2, Cpu, Loader2, X } from "lucide-react";
import { useLanguage } from "../i18n/LanguageContext";
import type { PipelineProgressSnapshot } from "../types/protocol";

interface PipelineProgressWidgetProps {
  task: PipelineProgressSnapshot | null;
  elapsedSec: number;
  visible: boolean;
  onDismiss: () => void;
}

export function PipelineProgressWidget({
  task,
  elapsedSec,
  visible,
  onDismiss,
}: PipelineProgressWidgetProps) {
  const { t } = useLanguage();
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [task?.steps.length, visible]);

  if (!visible || !task) return null;

  const isRunning = task.status === "running";
  const isError = task.status === "error";
  const isDone = task.status === "done";

  const shownElapsed = Math.max(elapsedSec, task.elapsed_sec || 0).toFixed(1);

  return (
    <div
      data-testid="pipeline-progress-widget"
      role="status"
      aria-live="polite"
      className="fixed bottom-4 right-4 z-50 w-[345px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-slate-200 bg-white/95 shadow-2xl ring-1 ring-slate-900/5 backdrop-blur-md"
    >
      <div
        className={`h-1 w-full ${
          isError
            ? "bg-rose-500"
            : isDone
            ? "bg-emerald-500"
            : "bg-gradient-to-r from-[#004A8F] via-[#0284C7] to-[#1FA84F]"
        }`}
      />

      <div className="p-3.5">
        {/* Header */}
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
                {task.title}
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

          <button
            type="button"
            onClick={onDismiss}
            aria-label={t("pipe.dismiss")}
            className="rounded-lg p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Smart Research Brand badge & percentage */}
        <div className="mt-2.5 flex items-center justify-between gap-2">
          <span
            data-testid="pipeline-brand-badge"
            className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2.5 py-0.5 text-[10px] font-bold text-[#004A8F] ring-1 ring-[#004A8F]/15"
          >
            <Cpu className="h-3 w-3 shrink-0" />
            {t("pipe.brand_badge")}
          </span>
          <span className="text-[11px] font-bold tabular-nums text-slate-700">
            {Math.min(100, Math.max(0, task.progress_pct))}%
          </span>
        </div>

        {/* Progress Bar */}
        <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
          <div
            className={`h-full rounded-full transition-all duration-300 ${
              isError
                ? "bg-rose-500"
                : isDone
                ? "bg-emerald-500"
                : "bg-gradient-to-r from-[#004A8F] to-[#0284C7]"
            }`}
            style={{ width: `${Math.min(100, Math.max(4, task.progress_pct))}%` }}
          />
        </div>

        {/* Live Step List */}
        <div className="mt-2.5">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            {t("pipe.steps_title")}
          </div>
          <div
            ref={listRef}
            className="max-h-[168px] space-y-1.5 overflow-y-auto pr-1"
          >
            {task.steps.map((step) => (
              <div
                key={`${step.key}-${step.timestamp}`}
                className="flex items-start gap-2 rounded-lg bg-slate-50/80 px-2 py-1.5 text-[11px]"
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
                  <div
                    className={`leading-snug ${
                      step.status === "running"
                        ? "font-semibold text-slate-900"
                        : step.status === "error"
                        ? "font-semibold text-rose-800"
                        : "font-medium text-slate-700"
                    }`}
                  >
                    {step.label}
                  </div>
                  {step.detail && (
                    <div className="truncate text-[10px] text-slate-500">
                      {step.detail}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {isError && task.error_message && (
          <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-[10px] text-rose-800">
            {task.error_message}
          </div>
        )}
      </div>
    </div>
  );
}
