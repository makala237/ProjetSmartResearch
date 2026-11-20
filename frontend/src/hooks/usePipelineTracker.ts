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

export interface TrackedPipelineTask {
  taskId: string;
  snapshot: PipelineProgressSnapshot;
  startedMs: number;
  elapsedSec: number;
  titleFr: string;
  titleEn: string;
}

export type RunWithProgressFn = <T>(
  opts: PipelineTaskOptions,
  runner: (taskId: string) => Promise<T>
) => Promise<T>;

export function usePipelineTracker(lang: AppLang, defaultModelHint?: string) {
  const [tasks, setTasks] = useState<TrackedPipelineTask[]>([]);
  const tasksRef = useRef<TrackedPipelineTask[]>([]);
  tasksRef.current = tasks;

  const langRef = useRef<AppLang>(lang);
  langRef.current = lang;

  const tickTimerRef = useRef<number | null>(null);
  const pollInFlightRef = useRef<Set<string>>(new Set());
  const removeTimersRef = useRef<Map<string, number>>(new Map());

  const clearRemoveTimer = useCallback((taskId: string) => {
    const existing = removeTimersRef.current.get(taskId);
    if (existing !== undefined) {
      window.clearTimeout(existing);
      removeTimersRef.current.delete(taskId);
    }
  }, []);

  const clearAllTimers = useCallback(() => {
    if (tickTimerRef.current !== null) {
      window.clearInterval(tickTimerRef.current);
      tickTimerRef.current = null;
    }
    removeTimersRef.current.forEach((timerId) => {
      window.clearTimeout(timerId);
    });
    removeTimersRef.current.clear();
  }, []);

  useEffect(() => clearAllTimers, [clearAllTimers]);

  const dismissTask = useCallback(
    (taskId: string) => {
      clearRemoveTimer(taskId);
      pollInFlightRef.current.delete(taskId);
      setTasks((prev) => prev.filter((t) => t.taskId !== taskId));
    },
    [clearRemoveTimer]
  );

  const dismiss = useCallback(() => {
    clearAllTimers();
    pollInFlightRef.current.clear();
    setTasks([]);
  }, [clearAllTimers]);

  const scheduleAutoRemove = useCallback(
    (taskId: string, delayMs: number) => {
      clearRemoveTimer(taskId);
      const timerId = window.setTimeout(() => {
        removeTimersRef.current.delete(taskId);
        pollInFlightRef.current.delete(taskId);
        setTasks((prev) => prev.filter((t) => t.taskId !== taskId));
      }, delayMs);
      removeTimersRef.current.set(taskId, timerId);
    },
    [clearRemoveTimer]
  );

  // Shared ticker for live elapsed time & concurrent polling of all running tasks
  useEffect(() => {
    const hasRunning = tasks.some((t) => t.snapshot.status === "running");
    if (!hasRunning) {
      if (tickTimerRef.current !== null) {
        window.clearInterval(tickTimerRef.current);
        tickTimerRef.current = null;
      }
      return;
    }

    if (tickTimerRef.current !== null) return;

    tickTimerRef.current = window.setInterval(() => {
      const now = Date.now();
      const currentLang = langRef.current;

      setTasks((prev) =>
        prev.map((item) => {
          if (item.snapshot.status !== "running") return item;
          const elapsed = Number(((now - item.startedMs) / 1000).toFixed(1));
          return {
            ...item,
            elapsedSec: elapsed,
            snapshot: {
              ...item.snapshot,
              title: currentLang === "en" ? item.titleEn : item.titleFr,
            },
          };
        })
      );

      for (const item of tasksRef.current) {
        if (item.snapshot.status !== "running") continue;
        if (pollInFlightRef.current.has(item.taskId)) continue;
        pollInFlightRef.current.add(item.taskId);

        api
          .getPipelineProgress(item.taskId)
          .then((snap) => {
            setTasks((prev) =>
              prev.map((existing) => {
                if (existing.taskId !== item.taskId) return existing;
                // Keep caller's specific title & terminal state if already finished locally
                const customTitle =
                  langRef.current === "en" ? existing.titleEn : existing.titleFr;
                if (existing.snapshot.status !== "running") {
                  return existing;
                }
                return {
                  ...existing,
                  snapshot: {
                    ...snap,
                    title: customTitle || snap.title,
                  },
                };
              })
            );
          })
          .catch(() => {
            // Task might not be registered yet during initial milliseconds
          })
          .finally(() => {
            pollInFlightRef.current.delete(item.taskId);
          });
      }
    }, 550);
  }, [tasks]);

  const runWithProgress = useCallback<RunWithProgressFn>(
    async (opts, runner) => {
      const taskId = `pl_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      const startedMs = Date.now();
      const currentLang = langRef.current;
      const title = currentLang === "en" ? opts.titleEn : opts.titleFr;

      const initialSnapshot: PipelineProgressSnapshot = {
        task_id: taskId,
        project_id: opts.projectId,
        operation: opts.operation,
        lang: currentLang,
        title,
        status: "running",
        progress_pct: 6,
        active_model: defaultModelHint || "smart-research-center",
        fallback_triggered: false,
        error_message: "",
        elapsed_sec: 0,
        steps: [
          {
            key: "init",
            label:
              currentLang === "en"
                ? "Preparing your assistant..."
                : "Préparation de votre assistant...",
            detail: "",
            status: "running",
            timestamp: new Date().toISOString(),
          },
        ],
      };

      setTasks((prev) => [
        ...prev,
        {
          taskId,
          snapshot: initialSnapshot,
          startedMs,
          elapsedSec: 0,
          titleFr: opts.titleFr,
          titleEn: opts.titleEn,
        },
      ]);

      try {
        const result = await runner(taskId);
        const finalElapsed = Number(((Date.now() - startedMs) / 1000).toFixed(1));

        let finalSnap: PipelineProgressSnapshot | null = null;
        try {
          finalSnap = await api.getPipelineProgress(taskId);
        } catch {
          finalSnap = null;
        }

        setTasks((prev) =>
          prev.map((item) => {
            if (item.taskId !== taskId) return item;
            const customTitle =
              langRef.current === "en" ? item.titleEn : item.titleFr;
            const mergedSnap: PipelineProgressSnapshot = finalSnap
              ? {
                  ...finalSnap,
                  title: customTitle || finalSnap.title,
                  status: "done",
                  progress_pct: 100,
                  elapsed_sec: finalElapsed,
                }
              : {
                  ...item.snapshot,
                  title: customTitle || item.snapshot.title,
                  status: "done",
                  progress_pct: 100,
                  elapsed_sec: finalElapsed,
                  steps: item.snapshot.steps.map((s) =>
                    s.status === "running" ? { ...s, status: "done" } : s
                  ),
                };
            return {
              ...item,
              elapsedSec: finalElapsed,
              snapshot: mergedSnap,
            };
          })
        );

        // Automatically disappear shortly after task completion
        scheduleAutoRemove(taskId, 1300);
        return result;
      } catch (err) {
        const finalElapsed = Number(((Date.now() - startedMs) / 1000).toFixed(1));
        const msg = err instanceof Error ? err.message : String(err);

        let errSnap: PipelineProgressSnapshot | null = null;
        try {
          errSnap = await api.getPipelineProgress(taskId);
        } catch {
          errSnap = null;
        }

        setTasks((prev) =>
          prev.map((item) => {
            if (item.taskId !== taskId) return item;
            const customTitle =
              langRef.current === "en" ? item.titleEn : item.titleFr;
            const mergedSnap: PipelineProgressSnapshot = errSnap
              ? {
                  ...errSnap,
                  title: customTitle || errSnap.title,
                  status: "error",
                  error_message: errSnap.error_message || msg,
                }
              : {
                  ...item.snapshot,
                  title: customTitle || item.snapshot.title,
                  status: "error",
                  error_message: msg,
                  steps: item.snapshot.steps.map((s) =>
                    s.status === "running"
                      ? { ...s, status: "error", detail: msg }
                      : s
                  ),
                };
            return {
              ...item,
              elapsedSec: finalElapsed,
              snapshot: mergedSnap,
            };
          })
        );

        scheduleAutoRemove(taskId, 5000);
        throw err;
      }
    },
    [defaultModelHint, scheduleAutoRemove]
  );

  const latestTask = tasks.length > 0 ? tasks[tasks.length - 1] : null;

  return {
    tasks,
    activeTask: latestTask?.snapshot ?? null,
    localElapsedSec: latestTask?.elapsedSec ?? 0,
    visible: tasks.length > 0,
    runWithProgress,
    dismiss,
    dismissTask,
  };
}
