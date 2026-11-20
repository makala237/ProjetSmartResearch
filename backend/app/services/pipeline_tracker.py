from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

_LOCK = threading.Lock()
_TASKS: Dict[str, Dict[str, Any]] = {}
_TTL_SEC = 600.0

_current_task_id: ContextVar[Optional[str]] = ContextVar("pipeline_task_id", default=None)
_current_task_lang: ContextVar[str] = ContextVar("pipeline_task_lang", default="fr")


def _norm_lang(lang: Optional[str]) -> str:
    return "en" if str(lang or "").strip().lower().startswith("en") else "fr"


def _prune_expired_locked(now: float) -> None:
    expired = [
        tid
        for tid, task in _TASKS.items()
        if task.get("finished_at") is not None and (now - float(task["finished_at"])) > _TTL_SEC
    ]
    for tid in expired:
        _TASKS.pop(tid, None)


def get_current_pipeline_task_id() -> Optional[str]:
    return _current_task_id.get()


def get_current_pipeline_lang() -> str:
    return _current_task_lang.get()


def reset_pipeline_context() -> None:
    _current_task_id.set(None)
    _current_task_lang.set("fr")


def start_pipeline_task(
    task_id: str,
    user_id: str,
    project_id: str,
    operation: str,
    title_fr: str,
    title_en: str,
    lang: str = "fr",
    initial_model: str = "",
) -> Dict[str, Any]:
    """Initialize a tracked pipeline task and bind its ContextVar for the active request."""
    clean_id = (task_id or "").strip()[:128]
    norm_lang = _norm_lang(lang)
    now = time.time()
    iso_now = datetime.now(timezone.utc).isoformat()

    record: Dict[str, Any] = {
        "task_id": clean_id,
        "user_id": user_id,
        "project_id": project_id,
        "operation": operation,
        "lang": norm_lang,
        "title": title_en if norm_lang == "en" else title_fr,
        "title_fr": title_fr,
        "title_en": title_en,
        "status": "running",
        "progress_pct": 5,
        "active_model": initial_model,
        "fallback_triggered": False,
        "error_message": "",
        "started_at": now,
        "updated_at": now,
        "finished_at": None,
        "steps": [
            {
                "key": "init",
                "label": "Initializing pipeline..." if norm_lang == "en" else "Initialisation du pipeline...",
                "label_fr": "Initialisation du pipeline...",
                "label_en": "Initializing pipeline...",
                "detail": "",
                "status": "running",
                "timestamp": iso_now,
            }
        ],
    }

    if clean_id:
        with _LOCK:
            _prune_expired_locked(now)
            _TASKS[clean_id] = record
        _current_task_id.set(clean_id)
    _current_task_lang.set(norm_lang)
    return record


def emit_pipeline_step(
    step_key: str,
    label_fr: str,
    label_en: str,
    detail: str = "",
    status: str = "running",
    progress_pct: Optional[int] = None,
    active_model: Optional[str] = None,
    fallback_triggered: Optional[bool] = None,
) -> None:
    """Record a pipeline step transition for the active tracked task (thread-safe)."""
    task_id = _current_task_id.get()
    if not task_id:
        return

    now = time.time()
    iso_now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        lang = task.get("lang", "fr")
        steps: List[Dict[str, Any]] = task["steps"]

        for st in steps:
            if st["key"] != step_key and st.get("status") == "running":
                st["status"] = "done"

        localized_label = label_en if lang == "en" else label_fr
        existing = next((st for st in steps if st["key"] == step_key), None)
        if existing is not None:
            existing["label"] = localized_label
            existing["label_fr"] = label_fr
            existing["label_en"] = label_en
            if detail:
                existing["detail"] = detail[:160]
            existing["status"] = status
            existing["timestamp"] = iso_now
        else:
            steps.append(
                {
                    "key": step_key,
                    "label": localized_label,
                    "label_fr": label_fr,
                    "label_en": label_en,
                    "detail": (detail or "")[:160],
                    "status": status,
                    "timestamp": iso_now,
                }
            )

        if progress_pct is not None:
            clamped = max(0, min(99 if task["status"] == "running" else 100, int(progress_pct)))
            task["progress_pct"] = max(int(task.get("progress_pct", 0)), clamped)

        if active_model:
            task["active_model"] = active_model
        if fallback_triggered is not None:
            task["fallback_triggered"] = bool(task.get("fallback_triggered")) or bool(fallback_triggered)
        task["updated_at"] = now


def update_pipeline_model(
    model_used: str,
    fallback_triggered: bool = False,
    note: str = "",
) -> None:
    """Update active Gemini model metadata (and optionally running step detail) on the active task."""
    task_id = _current_task_id.get()
    if not task_id:
        return

    now = time.time()
    with _LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        if model_used:
            task["active_model"] = model_used
        if fallback_triggered:
            task["fallback_triggered"] = True
        if note:
            for st in reversed(task["steps"]):
                if st.get("status") == "running":
                    st["detail"] = note[:160]
                    break
        task["updated_at"] = now


def finish_pipeline_task(
    status: str = "done",
    final_label_fr: str = "Terminé",
    final_label_en: str = "Completed",
    error_message: str = "",
) -> None:
    """Mark all pending steps completed and close the tracked pipeline task."""
    task_id = _current_task_id.get()
    if not task_id:
        return

    now = time.time()
    iso_now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        lang = task.get("lang", "fr")
        steps: List[Dict[str, Any]] = task["steps"]

        if status == "error":
            last_running = next((st for st in reversed(steps) if st.get("status") == "running"), None)
            if last_running is not None:
                last_running["status"] = "error"
                if error_message:
                    last_running["detail"] = error_message[:160]
            task["error_message"] = error_message[:300]
            task["status"] = "error"
        else:
            for st in steps:
                if st.get("status") == "running":
                    st["status"] = "done"
            done_label = final_label_en if lang == "en" else final_label_fr
            if not any(st["key"] == "completed" for st in steps):
                steps.append(
                    {
                        "key": "completed",
                        "label": done_label,
                        "label_fr": final_label_fr,
                        "label_en": final_label_en,
                        "detail": "",
                        "status": "done",
                        "timestamp": iso_now,
                    }
                )
            task["progress_pct"] = 100
            task["status"] = "done"

        task["finished_at"] = now
        task["updated_at"] = now


def get_pipeline_task_snapshot(
    task_id: str,
    requesting_user_id: str,
    is_admin: bool,
) -> Optional[Dict[str, Any]]:
    """Return a serialized progress snapshot if `task_id` exists and caller is authorized."""
    clean_id = (task_id or "").strip()
    if not clean_id:
        return None
    now = time.time()
    with _LOCK:
        _prune_expired_locked(now)
        task = _TASKS.get(clean_id)
        if task is None:
            return None
        if task["user_id"] != requesting_user_id and not is_admin:
            raise HTTPException(status_code=403, detail="Accès interdit à cette tâche.")

        end_ts = float(task["finished_at"]) if task.get("finished_at") is not None else now
        elapsed_sec = round(max(0.0, end_ts - float(task["started_at"])), 1)
        lang = task.get("lang", "fr")
        steps_out = [
            {
                "key": st["key"],
                "label": st["label_en"] if lang == "en" else st["label_fr"],
                "detail": st.get("detail", ""),
                "status": st["status"],
                "timestamp": st["timestamp"],
            }
            for st in task["steps"]
        ]
        return {
            "task_id": task["task_id"],
            "project_id": task["project_id"],
            "operation": task["operation"],
            "lang": lang,
            "title": task["title_en"] if lang == "en" else task["title_fr"],
            "status": task["status"],
            "progress_pct": int(task["progress_pct"]),
            "active_model": task.get("active_model", "") if is_admin else "smart-research-center",
            "fallback_triggered": bool(task.get("fallback_triggered", False)) if is_admin else False,
            "error_message": task.get("error_message", ""),
            "elapsed_sec": elapsed_sec,
            "steps": steps_out,
        }
