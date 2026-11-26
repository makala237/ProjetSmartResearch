"""Local Per-User Workspace Sync CLI for Smart Research Document Support Requests.

Creates one local folder per user/project under `user_workspaces/<user_slug>_<project_id>/`
containing:
  - `request.json`  : Pending student adjustment request metadata
  - `project.json`  : Live Firestore ProtocolProject (editable locally)
  - `protocol.docx` : Compiled Word document for visual inspection

Usage (from repo root):
  backend\\.venv\\Scripts\\python.exe backend/scripts/user_support_sync.py list
  backend\\.venv\\Scripts\\python.exe backend/scripts/user_support_sync.py pull
  backend\\.venv\\Scripts\\python.exe backend/scripts/user_support_sync.py pull --project-id <pid>
  backend\\.venv\\Scripts\\python.exe backend/scripts/user_support_sync.py push <pid> --note "Tableau 3 ajusté"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
WORKSPACES_DIR = REPO_ROOT / "user_workspaces"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Force Firestore production backend when running this sync tool locally
os.environ.setdefault("DB_BACKEND", "firestore")
os.environ.setdefault("FIRESTORE_PROJECT_ID", "smart-research-fmsb")
os.environ.setdefault("FIRESTORE_DATABASE", "smartresearch-prod")
os.environ.setdefault("STORAGE_BACKEND", "gcs")
os.environ.setdefault("CLOUD_STORAGE_BUCKET", "smart-research-fmsb-smartresearch-uploads")

from app.core.security import ensure_utc  # noqa: E402
from app.db.datastore import FirestoreDataStore  # noqa: E402
from app.db.models import utcnow  # noqa: E402
from app.schemas.protocol import ProtocolProject  # noqa: E402
from app.services.docx_template import compile_protocol_docx  # noqa: E402
from app.services.storage import project_storage  # noqa: E402


def _slugify(value: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", (value or "user").strip()).strip("_")
    return (s[:36] or "user").lower()


def _get_gcp_credentials():
    """Return ADC credentials or fall back seamlessly to the active gcloud CLI access token."""
    import google.auth

    try:
        creds, _ = google.auth.default()
        return creds
    except Exception:
        from google.oauth2.credentials import Credentials

        gcloud_cmd = "gcloud.cmd" if os.name == "nt" else "gcloud"
        tok = subprocess.check_output([gcloud_cmd, "auth", "print-access-token"], text=True).strip()
        if not tok:
            raise RuntimeError("No gcloud access token available. Run `gcloud auth login`.")
        return Credentials(token=tok)


def _build_firestore_client():
    from google.cloud import firestore

    project_id = os.environ.get("FIRESTORE_PROJECT_ID", "smart-research-fmsb")
    database_id = os.environ.get("FIRESTORE_DATABASE", "smartresearch-prod")
    return firestore.Client(project=project_id, database=database_id, credentials=_get_gcp_credentials())


def _build_gcs_blob_storage():
    from google.cloud import storage as gcs_storage
    from app.services.blob_storage import GCSBlobStorage

    project_id = os.environ.get("FIRESTORE_PROJECT_ID", "smart-research-fmsb")
    bucket_name = os.environ.get("CLOUD_STORAGE_BUCKET", "smart-research-fmsb-smartresearch-uploads")
    blob = object.__new__(GCSBlobStorage)
    blob.bucket_name = bucket_name
    blob._client = gcs_storage.Client(project=project_id, credentials=_get_gcp_credentials())
    blob._bucket = blob._client.bucket(bucket_name)
    return blob


def _get_store() -> FirestoreDataStore:
    return FirestoreDataStore(
        client=_build_firestore_client(),
        blob_storage=_build_gcs_blob_storage(),
    )


def fetch_pending_requests(store: FirestoreDataStore, limit: int = 200) -> List[Dict[str, Any]]:
    logs = store.list_audit_logs(limit=limit)
    resolved_ids = {
        str((l.details_json or {}).get("request_id") or "").strip()
        for l in logs
        if l.action == "user_update_request_resolved"
    }
    now = utcnow()
    pending: List[Dict[str, Any]] = []
    for l in logs:
        if l.action != "user_update_request" or l.id in resolved_ids:
            continue
        d = l.details_json or {}
        pending.append(
            {
                "id": l.id,
                "created_at": (ensure_utc(l.created_at) or now).isoformat(),
                "user_id": l.target_user_id or "",
                "user_name": str(d.get("user_name") or "Unknown"),
                "user_email": str(d.get("user_email") or ""),
                "project_id": str(d.get("project_id") or ""),
                "project_name": str(d.get("project_name") or ""),
                "module": str(d.get("module") or ""),
                "section_id": str(d.get("section_id") or ""),
                "category": str(d.get("category") or "formatting"),
                "message": str(d.get("message") or ""),
            }
        )
    return pending


def _find_workspace_dir(project_id: str) -> Optional[Path]:
    if not WORKSPACES_DIR.exists():
        return None
    suffix = f"_{project_id}"
    for p in WORKSPACES_DIR.iterdir():
        if p.is_dir() and p.name.endswith(suffix):
            return p
    return None


def cmd_list() -> None:
    store = _get_store()
    pending = fetch_pending_requests(store)
    if not pending:
        print("No pending user update requests.")
        return
    print(f"Pending user update requests ({len(pending)}):")
    for r in pending:
        print(
            f"  - [{r['id']}] {r['user_name']} ({r['user_email']}) | project={r['project_id']} "
            f"| {r['module']}/{r['section_id']} | {r['category']}\n"
            f"    Message: {r['message']}"
        )


def cmd_pull(project_id: Optional[str] = None) -> None:
    store = _get_store()
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    targets: List[Dict[str, Any]] = []
    if project_id:
        pending = fetch_pending_requests(store)
        matched = next((r for r in pending if r["project_id"] == project_id), None)
        targets.append(
            matched
            or {
                "id": "",
                "user_name": "manual",
                "project_id": project_id,
                "message": "(Manual pull)",
            }
        )
    else:
        targets = fetch_pending_requests(store)

    if not targets:
        print("No pending requests to pull.")
        return

    for req in targets:
        pid = req.get("project_id", "").strip()
        if not pid:
            print(f"Skipping request {req.get('id')} (no project_id attached).")
            continue
        loaded = store.get_project_payload(pid)
        if loaded is None:
            print(f"Project {pid} not found in Firestore/GCS.")
            continue
        _meta, payload_dict = loaded
        proj = ProtocolProject.model_validate(payload_dict)
        folder_name = f"{_slugify(req.get('user_name') or proj.cover.author_name or 'user')}_{pid}"
        ws = WORKSPACES_DIR / folder_name
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "request.json").write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
        (ws / "project.json").write_text(
            json.dumps(proj.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        docx_path = ws / "protocol.docx"
        try:
            compile_protocol_docx(proj, docx_path)
            docx_status = f"compiled ({docx_path.stat().st_size:,} bytes)"
        except Exception as exc:
            docx_status = f"docx warning: {exc}"
        print(f"Pulled {pid} -> {ws} ({docx_status})")


def cmd_push(project_id: str, request_id: Optional[str] = None, note: str = "") -> None:
    ws = _find_workspace_dir(project_id)
    if ws is None or not (ws / "project.json").exists():
        raise SystemExit(f"No local workspace found for project_id={project_id} in {WORKSPACES_DIR}")

    raw_data = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    proj = ProtocolProject.model_validate(raw_data)

    # Verify docx compiles cleanly before pushing back to Firestore/GCS
    docx_path = ws / "protocol.docx"
    compile_protocol_docx(proj, docx_path)
    print(f"Verified local protocol.docx ({docx_path.stat().st_size:,} bytes).")

    store = _get_store()
    existing_meta = store.get_project_meta(project_id)
    if existing_meta is None:
        raise SystemExit(f"Project metadata for {project_id} not found in Firestore.")
    now = utcnow()
    proj.updated_at = now
    existing_meta.name = proj.name
    existing_meta.updated_at = now
    existing_meta.sections_validated_count = sum(
        1 for s in proj.sections.values() if str(getattr(s.status, "value", s.status)) == "validated"
    )
    existing_meta.patients_count = len(proj.patient_records or [])
    store.save_project(existing_meta, proj.model_dump(mode="json"))
    print(f"Pushed updated project {project_id} to Firestore + GCS.")

    # Resolve matching support request & notify the student
    req_meta: Dict[str, Any] = {}
    if (ws / "request.json").exists():
        try:
            req_meta = json.loads((ws / "request.json").read_text(encoding="utf-8"))
        except Exception:
            req_meta = {}
    rid = (request_id or req_meta.get("id") or "").strip()
    target_uid = str(req_meta.get("user_id") or proj.owner_user_id or "").strip()
    if rid:
        store.create_audit_log(
            admin_id=None,
            target_user_id=target_uid,
            action="user_update_request_resolved",
            details_json={
                "request_id": rid,
                "project_id": project_id,
                "module": str(req_meta.get("module") or "module2"),
                "admin_name": "Équipe Smart Research",
                "note": note.strip()[:300],
            },
            created_at=utcnow(),
        )
        print(f"Marked request {rid} as resolved and notified user {target_uid}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Research local per-user support workspace sync")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List pending user document update requests")
    p_pull = sub.add_parser("pull", help="Pull pending user projects into user_workspaces/")
    p_pull.add_argument("--project-id", default=None, help="Optional specific project_id to pull")
    p_push = sub.add_parser("push", help="Push locally edited project.json back to Firestore & notify user")
    p_push.add_argument("project_id", help="Project ID to push")
    p_push.add_argument("--request-id", default=None, help="Optional request ID to mark resolved")
    p_push.add_argument("--note", default="Ajustement appliqué sur votre document.", help="Resolution note shown to user")

    args = parser.parse_args()
    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "pull":
        cmd_pull(project_id=args.project_id)
    elif args.cmd == "push":
        cmd_push(project_id=args.project_id, request_id=args.request_id, note=args.note)


if __name__ == "__main__":
    main()
