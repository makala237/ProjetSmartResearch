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
from app.services.drafting import (  # noqa: E402
    _count_prose_words,
    deterministic_section_checks,
    refresh_section_audit_notes,
)
from app.services.list_builder import generate_all_lists_and_toc  # noqa: E402
from app.services.storage import project_storage  # noqa: E402
from app.services.vancouver import (  # noqa: E402
    format_vancouver_citation,
    get_cited_references_in_order,
    renumber_and_sync_vancouver_references,
)


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
    from app.services import blob_storage as bs_mod
    from app.services.blob_storage import GCSBlobStorage

    project_id = os.environ.get("FIRESTORE_PROJECT_ID", "smart-research-fmsb")
    bucket_name = os.environ.get("CLOUD_STORAGE_BUCKET", "smart-research-fmsb-smartresearch-uploads")
    blob = object.__new__(GCSBlobStorage)
    blob.bucket_name = bucket_name
    blob._client = gcs_storage.Client(project=project_id, credentials=_get_gcp_credentials())
    blob._bucket = blob._client.bucket(bucket_name)
    # Also wire the module-level cache so media_transport/docx_template use the same gcloud token
    bs_mod._build_cached_blob_storage.cache_clear()
    bs_mod.get_blob_storage = lambda: blob
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


def sync_and_validate_guardrails(proj: ProtocolProject) -> List[str]:
    """Sync Vancouver + TOC/Lists and verify 100% compliance with CAMES/FMSB & Vancouver guardrails."""
    violations: List[str] = []

    # 1. Sync Vancouver numbering/formatting & front-matter lists/TOC
    renumber_and_sync_vancouver_references(proj)
    generate_all_lists_and_toc(proj, use_gemini_decision=False)

    # 2. Refresh deterministic CAMES/FMSB audit notes on every section
    for sid, sec in proj.sections.items():
        notes = refresh_section_audit_notes(proj, sid, clear_ai_notes=True)
        for note in notes:
            wc = _count_prose_words(sec.content_markdown or "")
            violations.append(f"[CAMES:{sid} | {wc} mots] {note}")

    # 3. Verify Vancouver citation integrity across all body sections
    max_ref_idx = len(proj.references or [])
    for sid, sec in proj.sections.items():
        if sid in ("references", "toc", "abbreviations", "list_of_tables", "list_of_figures"):
            continue
        cited = [int(x) for x in re.findall(r"\[(\d+)\]", sec.content_markdown or "")]
        bad = sorted({n for n in cited if n < 1 or n > max_ref_idx})
        if bad:
            violations.append(
                f"[VANCOUVER:{sid}] Citations hors plage {bad} (pool actuel : 1..{max_ref_idx})."
            )

    # 4. Verify Vancouver reference metadata cleanliness on cited references
    cited_refs = get_cited_references_in_order(proj)
    seen_titles: Dict[str, int] = {}
    lang = getattr(proj, "language", "fr") or "fr"
    for ref in cited_refs:
        idx = ref.order_index
        rendered = format_vancouver_citation(ref, lang=lang)
        if any(ent in rendered for ent in ("&lt;", "&gt;", "&amp;", "<sup>", "</sup>")):
            violations.append(f"[VANCOUVER:ref#{idx}] Balises ou entités HTML détectées : {rendered[:100]}")
        authors_joined = " ".join(ref.authors or [])
        if "auteurs non précisés" in authors_joined.lower():
            violations.append(f"[VANCOUVER:ref#{idx}] Auteur générique interdit ('Auteurs non précisés').")
        if any("et al." in (a or "").lower() for a in (ref.authors or [])):
            violations.append(f"[VANCOUVER:ref#{idx}] 'et al.' fusionné dans un champ auteur : {ref.authors}")
        norm_title = re.sub(r"[^a-z0-9]+", "", (ref.title or "").lower())
        if norm_title and len(norm_title) >= 15:
            if norm_title in seen_titles:
                violations.append(
                    f"[VANCOUVER:ref#{idx}] Référence dupliquée avec réf. #{seen_titles[norm_title]} : {ref.title[:70]}"
                )
            else:
                seen_titles[norm_title] = idx

    return violations


def cmd_validate(project_id: str) -> None:
    _build_gcs_blob_storage()
    ws = _find_workspace_dir(project_id)
    if ws is None or not (ws / "project.json").exists():
        raise SystemExit(f"No local workspace found for project_id={project_id} in {WORKSPACES_DIR}")

    raw_data = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    proj = ProtocolProject.model_validate(raw_data)
    violations = sync_and_validate_guardrails(proj)
    if violations:
        print(f"GUARDRAIL CHECK FAILED ({len(violations)} violation(s)):")
        for v in violations:
            print(f"  - {v}")
        raise SystemExit(1)
    print(f"GUARDRAIL CHECK PASSED (0 violations across {len(proj.sections)} sections, {len(get_cited_references_in_order(proj))} cited Vancouver refs).")


def cmd_push(
    project_id: str,
    request_id: Optional[str] = None,
    note: str = "",
    force: bool = False,
) -> None:
    _build_gcs_blob_storage()
    ws = _find_workspace_dir(project_id)
    if ws is None or not (ws / "project.json").exists():
        raise SystemExit(f"No local workspace found for project_id={project_id} in {WORKSPACES_DIR}")

    raw_data = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    proj = ProtocolProject.model_validate(raw_data)

    # Strict CAMES/FMSB + Vancouver guardrail gate before any upload
    violations = sync_and_validate_guardrails(proj)
    if violations and not force:
        print(f"PUSH BLOCKED — {len(violations)} guardrail violation(s) detected:")
        for v in violations:
            print(f"  - {v}")
        raise SystemExit("Fix all CAMES/Vancouver guardrail violations before pushing (or pass --force only if explicitly authorized).")
    if violations:
        print(f"WARNING (--force): pushing despite {len(violations)} guardrail violation(s).")
    else:
        print(f"Guardrails passed: 0 CAMES/FMSB/Vancouver violations across {len(proj.sections)} sections.")

    # Persist synced project.json locally before compiling & uploading
    (ws / "project.json").write_text(
        json.dumps(proj.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

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
                "note": note.strip()[:6000],
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
    p_val = sub.add_parser("validate", help="Run all CAMES/FMSB & Vancouver guardrails on a local workspace")
    p_val.add_argument("project_id", help="Project ID to validate")
    p_push = sub.add_parser("push", help="Push locally edited project.json back to Firestore & notify user")
    p_push.add_argument("project_id", help="Project ID to push")
    p_push.add_argument("--request-id", default=None, help="Optional request ID to mark resolved")
    p_push.add_argument("--note", default="Ajustement appliqué sur votre document.", help="Resolution note shown to user")
    p_push.add_argument("--note-file", default=None, help="Optional UTF-8 text/markdown file containing the resolution note")
    p_push.add_argument("--force", action="store_true", help="Bypass guardrail block (emergency override only)")

    args = parser.parse_args()
    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "pull":
        cmd_pull(project_id=args.project_id)
    elif args.cmd == "validate":
        cmd_validate(project_id=args.project_id)
    elif args.cmd == "push":
        final_note = args.note
        if args.note_file:
            final_note = Path(args.note_file).read_text(encoding="utf-8")
        cmd_push(project_id=args.project_id, request_id=args.request_id, note=final_note, force=args.force)


if __name__ == "__main__":
    main()
