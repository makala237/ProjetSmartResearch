import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AppLang } from "../i18n/translations";
import type { PipelineProgressSnapshot } from "../types/protocol";

export interface PipelineTaskOptions {
  operation: string;
  titleFr: string;
  titleEn: string;
  projectId: string;
}

export type RunWithProgressFn = <T>(
  opts: PipelineTaskOptions,
  runner: (taskId: string) => Promise<T>
) => Promise<T>;

export function usePipelineTracker(lang: AppLang, defaultModelHint?: string) {
  const [activeTask, setActiveTask] = useState<PipelineProgressSnapshot | null>(null);
  const [localElapsedSec, setLocalElapsedSec] = useState(0);
  const [visible, setVisible] = useState(false);

  const pollTimerRef = useRef<number | null>(null);
  const elapsedTimerRef = useRef<number | null>(null);
  const autoHideTimerRef = useRef<number | null>(null);

  const clearTimers = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (elapsedTimerRef.current !== null) {
      window.clearInterval(elapsedTimerRef.current);
      elapsedTimerRef.current = null;
    }
    if (autoHideTimerRef.current !== null) {
      window.clearTimeout(autoHideTimerRef.current);
      autoHideTimerRef.current = null;
    }
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  const dismiss = useCallback(() => {
    clearTimers();
    setVisible(false);
  }, [clearTimers]);

  const runWithProgress = useCallback<RunWithProgressFn>(
    async (opts, runner) => {
      clearTimers();
      const taskId = `pl_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      const startedMs = Date.now();
      const title = lang === "en" ? opts.titleEn : opts.titleFr;

      setLocalElapsedSec(0);
      setVisible(true);
      setActiveTask({
        task_id: taskId,
        project_id: opts.projectId,
        operation: opts.operation,
        lang,
        title,
        status: "running",
        progress_pct: 5,
        active_model: defaultModelHint || "smart-research-center",
        fallback_triggered: false,
        error_message: "",
        elapsed_sec: 0,
        steps: [
          {
            key: "init",
            label:
              lang === "en"
                ? "Initializing AI pipeline..."
                : "Initialisation du pipeline IA...",
            detail: "",
            status: "running",
            timestamp: new Date().toISOString(),
          },
        ],
      });

      elapsedTimerRef.current = window.setInterval(() => {
        setLocalElapsedSec(Number(((Date.now() - startedMs) / 1000).toFixed(1)));
      }, 500);

      pollTimerRef.current = window.setInterval(() => {
        api
          .getPipelineProgress(taskId)
          .then((snap) => {
            setActiveTask(snap);
          })
          .catch(() => {
            // Task might not be registered yet during initial milliseconds
          });
      }, 650);

      try {
        const result = await runner(taskId);
        if (pollTimerRef.current !== null) {
          window.clearInterval(pollTimerRef.current);
          pollTimerRef.current = null;
        }
        if (elapsedTimerRef.current !== null) {
          window.clearInterval(elapsedTimerRef.current);
          elapsedTimerRef.current = null;
        }
        const finalElapsed = Number(((Date.now() - startedMs) / 1000).toFixed(1));
        setLocalElapsedSec(finalElapsed);

        try {
          const finalSnap = await api.getPipelineProgress(taskId);
          setActiveTask(finalSnap);
        } catch {
          setActiveTask((prev) =>
            prev
              ? {
                  ...prev,
                  status: "done",
                  progress_pct: 100,
                  elapsed_sec: finalElapsed,
                  steps: prev.steps.map((s) =>
                    s.status === "running" ? { ...s, status: "done" } : s
                  ),
                }
              : prev
          );
        }

        autoHideTimerRef.current = window.setTimeout(() => {
          setVisible(false);
        }, 4500);

        return result;
      } catch (err) {
        if (pollTimerRef.current !== null) {
          window.clearInterval(pollTimerRef.current);
          pollTimerRef.current = null;
        }
        if (elapsedTimerRef.current !== null) {
          window.clearInterval(elapsedTimerRef.current);
          elapsedTimerRef.current = null;
        }
        const msg = err instanceof Error ? err.message : String(err);
        try {
          const errSnap = await api.getPipelineProgress(taskId);
          setActiveTask({
            ...errSnap,
            status: "error",
            error_message: errSnap.error_message || msg,
          });
        } catch {
          setActiveTask((prev) =>
            prev
              ? {
                  ...prev,
                  status: "error",
                  error_message: msg,
                  steps: prev.steps.map((s) =>
                    s.status === "running" ? { ...s, status: "error", detail: msg } : s
                  ),
                }
              : prev
          );
        }
        throw err;
      }
    },
    [clearTimers, defaultModelHint, lang]
  );

  return {
    activeTask,
    localElapsedSec,
    visible,
    runWithProgress,
    dismiss,
  };
}
