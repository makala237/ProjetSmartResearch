from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.api.admin_routes import router as admin_router
from app.api.deps import (
    assert_cover_admin_only,
    enforce_topic_lock_on_cover_update,
    get_active_user,
    get_active_user_allow_query_token,
    get_authorized_project,
    get_client_ip,
    get_current_user,
    require_admin,
    require_any_active_license,
    require_module_write,
)
from app.core.config import WORKSPACE_DIR, get_settings
from app.data.fmsb_canvas import FMSB_CANVAS_VERSION, FMSB_SECTIONS
from app.data.i18n import get_localized_canvas, normalize_lang
from app.db.database import get_database_dialect, get_db, init_db
from app.db.datastore import UserRecord
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    OnboardingStateUpdateRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserMeOut,
)
from app.services.auth_service import (
    authenticate_and_login,
    build_user_me_out,
    change_user_password,
    logout_user,
    normalize_onboarding_state,
    refresh_session_tokens,
)
from app.services.licensing_service import (
    assert_ai_quota_available,
    assert_module_write_allowed,
    is_module_active,
    record_usage_and_debit,
    resolve_user_model_chain,
)
from app.services.copilot_actions import (
    ACTION_MODULE_MAP,
    AI_ACTION_CREDIT_COSTS,
    VALID_COPILOT_ACTION_TYPES,
    execute_copilot_app_action,
)
from app.services.copilot_live import handle_copilot_live_websocket
from app.services.copilot_service import (
    append_scoped_copilot_messages,
    clear_user_copilot_messages,
    format_supervisor_directives_block,
    get_user_copilot_messages,
    restore_section_version,
    run_copilot_chat,
    scope_project_copilot_for_user,
    snapshot_section_version,
)
from app.services.copilot_tools import (
    apply_surgical_text_patches,
    tool_draft_compare_and_merge,
)
from app.services.selective_reformulation import (
    smart_reformulate_section,
    transform_selected_text,
)
from app.services.supervisor_media import extract_directives_from_media
from app.services.gemini_client import gemini_router
from app.schemas.protocol import (
    CoverMetadata,
    CreateProjectRequest,
    CRFFieldSpec,
    DiagramConnection,
    DiagramNode,
    ExtractCRFRequest,
    GenerateCRFRequest,
    GenerateSubAnnexRequest,
    ImageAsset,
    PatientRecord,
    ProtocolProject,
    SectionDiagram,
    SectionTable,
    StepStatus,
    SupervisorDirectiveItem,
    UpdateCRFSchemaRequest,
    UpdateFormattingRequest,
    UpdateLanguageRequest,
    UpdateSectionRequest,
    UpdateStructuredDataRequest,
    UpsertPatientRecordRequest,
)
from app.services.annexe_engine import generate_sub_annex_content
from app.services.annexes_generator import build_annexes_pack
from app.services.coherence_sentinel import (
    evaluate_project_coherence,
    harmonize_section_with_working_memory,
)
from app.services.crf_builder import (
    ensure_project_crf_fields,
    export_patient_records_csv,
    extract_crf_fields_from_project,
    generate_or_improve_crf_with_ai,
    render_crf_annexe1_html,
    sync_crf_to_project,
)
from app.services.crf_extractor import extract_and_sync_crf_for_project
from app.services.diagram_renderer import render_diagram_svg
from app.services.docx_template import compile_protocol_docx, ensure_master_template
from app.services.drafting import (
    audit_section_with_ai,
    draft_section_with_ai,
    fix_section_issues_with_ai,
    suggest_section_diagram_with_ai,
    suggest_section_table_with_ai,
)
from app.services.gemini_client import (
    gemini_router,
    get_ai_routing_policy,
    reset_ai_routing_policy,
)
from app.services.global_audit import (
    run_global_protocol_audit,
    sync_structured_from_sections,
)
from app.services.humanizer import (
    humanize_entire_protocol,
    humanize_section_with_ai,
)
from app.services.importer import analyze_and_import_protocol, remove_internal_source_from_project
from app.services.list_builder import generate_all_lists_and_toc
from app.services.literature import run_research_pipeline
from app.services.media_transport import (
    build_structured_table_for_section,
    clear_structured_table_for_section,
    materialize_image_asset,
    synchronize_module1_to_module2_tables,
    transport_and_repair_project_images,
)
from app.services.page_layout_engine import plan_document_pagination
from app.services.pipeline_tracker import (
    emit_pipeline_step,
    finish_pipeline_task,
    get_pipeline_task_snapshot,
    reset_pipeline_context,
    start_pipeline_task,
)
from app.services.section_orchestrator import orchestrate_section_action
from app.services.working_memory import (
    get_working_memory_path,
    load_working_memory_map,
    refresh_all_working_memory,
    update_section_working_memory,
)
from app.data.pptx_canvas import SLIDE_TEMPLATE_CATALOG
from app.services.blob_storage import build_object_key, get_blob_storage
from app.services.pdf_exporter import (
    compile_protocol_pdf,
    resolve_render_engine,
    update_docx_fields_via_word_com,
)
from app.services.presentation_orchestrator import PresentationOrchestrator
from app.services.pptx_exporter import compile_presentation_pptx
from app.services.pptx_renderer import export_pptx_to_pdf, render_pptx_previews
from app.services.slide_layout_engine import compute_deck_layout
from app.schemas.presentation import (
    PresentationAuditReport,
    PresentationDeck,
    Slide,
    SlideElement,
)
from app.services.rich_formatter import markdown_to_clean_html
from app.services.segmentation import segment_project_articles
from app.services.storage import project_storage, sync_special_sections


def _sync_module2_queue(project: ProtocolProject) -> None:
    """Keep Module 2 queue (downloaded figures, effective tables, List of Tables/Figures) synced with Module 1."""
    transport_and_repair_project_images(project)
    synchronize_module1_to_module2_tables(project)
    sync_special_sections(project, force_section_id="list_of_tables")
    sync_special_sections(project, force_section_id="list_of_figures")


def _strip_table_from_section_text(sec, deleted_title: str = "") -> None:
    """Remove any orphaned Markdown/HTML table matching a deleted table from section text."""
    md = sec.content_markdown or ""
    html = sec.content_html or ""

    clean_t = re.sub(
        r"^\s*(?:Tableaux?|Tables?)\s*(?:n°\s*)?\d*\s*[:.\-–—]*\s*",
        "",
        (deleted_title or "").strip(),
        flags=re.IGNORECASE,
    ).strip()
    if clean_t:
        esc = re.escape(clean_t)
        # Remove matching Markdown table preceded by its caption
        md = re.sub(
            rf"(?im)^\s*(?:\*\*)?(?:Tableau|Table)[^\n]*{esc}[^\n]*\n+(?:\s*\|[^\n]+\n)+",
            "",
            md,
        )
        # Remove standalone matching title line
        md = re.sub(rf"(?im)^\s*(?:\*\*)?(?:Tableau|Table)[^\n]*{esc}[^\n]*\n*", "", md)
        # Remove matching HTML table preceded by caption
        html = re.sub(
            rf"(?is)<(?:p|h[1-6]|div)[^>]*>[^<]*{esc}[^<]*</(?:p|h[1-6]|div)>\s*<table[^>]*>.*?</table>",
            "",
            html,
        )

    # If no custom_tables remain and text has HTML/MD mismatch, keep both synchronized
    sec.content_markdown = md.strip()
    if sec.content_markdown and ("<table" not in html.lower() or clean_t):
        sec.content_html = markdown_to_clean_html(sec.content_markdown) if not html.strip() else html.strip()
from app.services.toc_builder import (
    generate_clean_toc_section_content,
    sync_toc_from_exported_docx,
)
from app.services.vancouver import (
    build_and_validate_vancouver_references,
    renumber_and_sync_vancouver_references,
)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _cleanup_request_contextvars(request: Request, call_next):
    from app.core.request_lang import reset_request_lang, set_request_lang

    set_request_lang(request.headers.get("accept-language"))
    try:
        return await call_next(request)
    finally:
        reset_request_lang()
        reset_ai_routing_policy()
        reset_pipeline_context()


class ResearchRequest(BaseModel):
    custom_query: Optional[str] = None
    max_per_source: int = 4
    include_web_grounding: bool = True
    search_scope: str = "hybrid_africa"


class SegmentRequest(BaseModel):
    limit: int = 8
    only_unsegmented: bool = True


class ToggleSegmentRequest(BaseModel):
    approved: Optional[bool] = None
    target_section_ids: Optional[List[str]] = None


class ToggleImageRequest(BaseModel):
    approved: Optional[bool] = None
    target_section_id: Optional[str] = None
    caption: Optional[str] = None
    title: Optional[str] = None
    anchor_hint: Optional[str] = None


class DraftSectionRequest(BaseModel):
    instruction: Optional[str] = None
    force_external_research: bool = False


class SuggestTableRequest(BaseModel):
    title: str
    goal: str = ""
    anchor_hint: str = ""


class CreateSectionTableRequest(BaseModel):
    title: str
    anchor_hint: str = ""
    headers: List[str]
    rows: List[List[str]]


class TranslateDepartmentRequest(BaseModel):
    text: str
    source_lang: str = "fr"
    target_lang: str = "en"
    field_type: str = "department"


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    try:
        ensure_master_template()
    except FileNotFoundError:
        pass


@app.get("/api/health")
def health_check() -> Dict[str, object]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "canvas_version": FMSB_CANVAS_VERSION,
        "gemini_key_configured": bool(settings.gemini_api_key),
        "auth_enabled": True,
        "database_dialect": get_database_dialect(),
        "db_backend": (get_settings().db_backend or "sql").strip().lower(),
        "render_engine": resolve_render_engine(),
        "storage_backend": get_blob_storage().backend_name,
        "soffice_available": bool(shutil.which("soffice") or shutil.which("libreoffice")),
        "pdftoppm_available": bool(shutil.which("pdftoppm")),
        "model_chains": settings.model_chains(),
    }


class ClientErrorReportRequest(BaseModel):
    message: str
    context: str = ""
    module: str = ""
    status_code: Optional[int] = None


@app.post("/api/client-errors")
def report_client_error(
    payload: ClientErrorReportRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    """Record a runtime error event so administrators receive the real error details in real time."""
    raw_msg = (payload.message or "").strip()
    if not raw_msg:
        return {"recorded": False}
    try:
        from app.db.datastore import as_store
        from app.db.models import utcnow

        store = as_store(db)
        store.create_audit_log(
            admin_id=None,
            target_user_id=current_user.id,
            action="runtime_error_alert",
            details_json={
                "error_message": raw_msg[:1500],
                "context": (payload.context or "")[:200],
                "module": (payload.module or "")[:64],
                "status_code": payload.status_code,
                "user_name": current_user.full_name,
                "user_email": current_user.email,
                "user_role": current_user.role,
                "ip_address": get_client_ip(request),
                "source": "client",
            },
            created_at=utcnow(),
        )
        return {"recorded": True}
    except Exception:
        return {"recorded": False}


@app.get("/api/notifications/resolved-errors")
def list_user_resolved_error_notifications(
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Return undismissed 'issue fixed' notifications targeted at the current user's account."""
    try:
        from app.core.security import ensure_utc
        from app.db.datastore import as_store
        from app.db.models import utcnow

        store = as_store(db)
        logs = store.list_audit_logs(limit=250)
        dismissed_ids = {
            str((l.details_json or {}).get("notification_id") or "").strip()
            for l in logs
            if l.action == "runtime_error_resolution_dismissed"
            and l.target_user_id == current_user.id
        }
        now = utcnow()
        out: List[Dict[str, Any]] = []
        seen_alert_ids: set[str] = set()
        for l in logs:
            if l.action != "runtime_error_resolved" or l.target_user_id != current_user.id:
                continue
            if l.id in dismissed_ids:
                continue
            d = l.details_json or {}
            aid = str(d.get("alert_id") or l.id)
            if aid in seen_alert_ids:
                continue
            seen_alert_ids.add(aid)
            note = str(d.get("note") or "").strip()
            out.append(
                {
                    "id": l.id,
                    "alert_id": aid,
                    "module": str(d.get("module") or ""),
                    "resolved_at": (ensure_utc(l.created_at) or now).isoformat(),
                    "message_fr": (
                        "Bonne nouvelle ! L'incident technique que vous avez rencontré a été corrigé "
                        "par notre équipe. Vous pouvez maintenant réessayer votre action sereinement."
                    ),
                    "message_en": (
                        "Good news! The technical issue you encountered has been fixed by our team. "
                        "You can now safely retry your action."
                    ),
                    "note": note,
                }
            )
            if len(out) >= 5:
                break
        return out
    except Exception:
        return []


@app.post("/api/notifications/resolved-errors/{notification_id}/dismiss")
def dismiss_user_resolved_error_notification(
    notification_id: str,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    """Dismiss a resolved-error notification for the current user's account."""
    try:
        from app.db.datastore import as_store
        from app.db.models import utcnow

        store = as_store(db)
        store.create_audit_log(
            admin_id=None,
            target_user_id=current_user.id,
            action="runtime_error_resolution_dismissed",
            details_json={"notification_id": notification_id},
            created_at=utcnow(),
        )
        return {"dismissed": True, "notification_id": notification_id}
    except Exception:
        return {"dismissed": False, "notification_id": notification_id}


@app.post("/api/auth/login", response_model=TokenResponse)
def auth_login(
    payload: LoginRequest,
    request: Request,
    db: Any = Depends(get_db),
) -> TokenResponse:
    client_ip = request.client.host if request.client else ""
    user, access_tok, refresh_tok = authenticate_and_login(
        db=db,
        email=payload.resolved_identifier(),
        password=payload.password,
        ip_address=client_ip,
    )
    return TokenResponse(
        access_token=access_tok,
        refresh_token=refresh_tok,
        must_change_password=user.must_change_password,
        user=build_user_me_out(db, user),
    )


@app.post("/api/auth/refresh", response_model=TokenResponse)
def auth_refresh(
    payload: RefreshTokenRequest,
    db: Any = Depends(get_db),
) -> TokenResponse:
    user, access_tok, refresh_tok = refresh_session_tokens(db, payload.refresh_token)
    return TokenResponse(
        access_token=access_tok,
        refresh_token=refresh_tok,
        must_change_password=user.must_change_password,
        user=build_user_me_out(db, user),
    )


@app.get("/api/auth/me", response_model=UserMeOut)
def auth_me(
    current_user: UserRecord = Depends(get_current_user),
    db: Any = Depends(get_db),
) -> UserMeOut:
    return build_user_me_out(db, current_user)


@app.patch("/api/auth/me/onboarding", response_model=UserMeOut)
def update_my_onboarding_state(
    payload: OnboardingStateUpdateRequest,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> UserMeOut:
    from app.db.datastore import as_store

    store = as_store(db)
    if payload.reset:
        next_state = normalize_onboarding_state({})
    else:
        cur = normalize_onboarding_state(getattr(current_user, "onboarding_state", None))
        if payload.welcome_tour_completed is not None:
            cur["welcome_tour_completed"] = bool(payload.welcome_tour_completed)
        if payload.welcome_tour_dismissed is not None:
            cur["welcome_tour_dismissed"] = bool(payload.welcome_tour_dismissed)
        if payload.tips_enabled is not None:
            cur["tips_enabled"] = bool(payload.tips_enabled)
        if payload.mark_feature_seen and payload.mark_feature_seen.strip():
            fid = payload.mark_feature_seen.strip()[:64]
            if fid.lower() not in {x.lower() for x in cur["seen_features"]}:
                cur["seen_features"].append(fid)
        next_state = normalize_onboarding_state(cur)

    updated = store.update_user(current_user.id, onboarding_state=next_state) or current_user
    return build_user_me_out(db, updated)


@app.post("/api/auth/change-password", response_model=TokenResponse)
def auth_change_password(
    payload: ChangePasswordRequest,
    current_user: UserRecord = Depends(get_current_user),
    db: Any = Depends(get_db),
) -> TokenResponse:
    access_tok, refresh_tok = change_user_password(
        db=db,
        user=current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return TokenResponse(
        access_token=access_tok,
        refresh_token=refresh_tok,
        must_change_password=current_user.must_change_password,
        user=build_user_me_out(db, current_user),
    )


@app.post("/api/auth/logout")
def auth_logout(
    current_user: UserRecord = Depends(get_current_user),
    db: Any = Depends(get_db),
) -> Dict[str, str]:
    logout_user(db, current_user)
    return {"status": "ok"}


@app.get("/api/models")
def list_models(_current_user: UserRecord = Depends(get_active_user)) -> Dict[str, object]:
    try:
        available = gemini_router.list_available_models()
        return {
            "connected": True,
            "configured_chains": settings.model_chains(),
            "available_models": available,
        }
    except Exception as exc:
        return {
            "connected": False,
            "error": str(exc),
            "configured_chains": settings.model_chains(),
            "available_models": [],
        }


@app.get("/api/canvas")
def get_canvas(lang: str = "fr") -> Dict[str, object]:
    norm_lang = normalize_lang(lang)
    sections = get_localized_canvas(norm_lang)
    return {
        "version": FMSB_CANVAS_VERSION,
        "language": norm_lang,
        "total_steps": len(sections),
        "sections": [s.model_dump() for s in sections],
    }


@app.get("/api/pipeline/progress/{task_id}")
async def get_pipeline_progress(
    task_id: str,
    current_user: UserRecord = Depends(get_active_user),
) -> Dict[str, object]:
    snapshot = get_pipeline_task_snapshot(
        task_id=task_id,
        requesting_user_id=current_user.id,
        is_admin=(current_user.role == "admin"),
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable ou expirée.")
    return snapshot


def _begin_request_pipeline_task(
    request: Request,
    current_user: UserRecord,
    project: ProtocolProject,
    operation: str,
    title_fr: str,
    title_en: str,
) -> bool:
    task_id = (request.headers.get("X-Pipeline-Task-Id") or "").strip()
    if not task_id:
        return False
    policy = get_ai_routing_policy()
    initial_model = (
        settings.gemini_fallback_reasoning_model
        if policy.get("force_fallback_35")
        else settings.gemini_primary_reasoning_model
    )
    start_pipeline_task(
        task_id=task_id,
        user_id=current_user.id,
        project_id=project.project_id,
        operation=operation,
        title_fr=title_fr,
        title_en=title_en,
        lang=getattr(project, "language", "fr"),
        initial_model=initial_model,
    )
    return True


@app.post("/api/translate-department")
def translate_department(
    payload: TranslateDepartmentRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, str]:
    cost = assert_ai_quota_available(db, current_user, module_key="general", action="translate_department")
    res = gemini_router.translate_department_live(
        text=payload.text,
        source_lang=payload.source_lang,
        target_lang=payload.target_lang,
        field_type=payload.field_type,
    )
    model_used = res.get("model_used", "")
    is_real_ai = model_used not in ("none", "passthrough", "fmsb-glossary-fallback", "")
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="general",
        action="translate_department",
        credits_cost=cost if is_real_ai else 0,
        model_used=model_used,
        ip_address=get_client_ip(request),
    )
    return res


@app.get("/api/projects", response_model=List[ProtocolProject])
def list_projects(
    include_all: bool = False,
    current_user: UserRecord = Depends(get_active_user),
) -> List[ProtocolProject]:
    """Return strictly the caller's own projects by default (admins must opt in with include_all=true)."""
    effective_role = "admin" if (include_all and current_user.role == "admin") else "student"
    return [
        scope_project_copilot_for_user(p, current_user.id)
        for p in project_storage.list_for_user(current_user.id, effective_role)
    ]


@app.post("/api/projects", response_model=ProtocolProject)
def create_project(
    payload: CreateProjectRequest,
    current_user: UserRecord = Depends(require_any_active_license()),
) -> ProtocolProject:
    try:
        return project_storage.create(
            payload,
            owner_user_id=current_user.id,
            owner_role=current_user.role,
            owner_full_name=current_user.full_name,
            owner_matricule=current_user.matricule,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ONE_PROTOCOL_LIMIT",
                "message": "Chaque compte étudiant est strictement limité à 1 seul protocole.",
            },
        ) from exc


@app.get("/api/projects/{project_id}", response_model=ProtocolProject)
def get_project(
    project_id: str,
    current_user: UserRecord = Depends(get_active_user),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if ensure_project_crf_fields(project):
        project = project_storage.save(project)
    return scope_project_copilot_for_user(project, current_user.id)


@app.patch("/api/projects/{project_id}/language", response_model=ProtocolProject)
def update_project_language(
    project_id: str,
    payload: UpdateLanguageRequest,
    current_user: UserRecord = Depends(require_any_active_license()),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    project.language = normalize_lang(payload.language)
    sync_special_sections(project)
    return project_storage.save(project)


@app.patch("/api/projects/{project_id}/cover", response_model=ProtocolProject)
def update_cover(
    project_id: str,
    cover: CoverMetadata,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    assert_cover_admin_only(current_user)
    project = get_authorized_project(project_id, current_user)
    enforce_topic_lock_on_cover_update(project, cover, current_user)
    project.cover = cover
    if (cover.title or "").strip():
        project.name = cover.title.strip()
    if "cover_page" in project.sections:
        cov_sec = project.sections["cover_page"]
        cov_sec.status = StepStatus.VALIDATED
        if (cover.title or "").strip():
            cov_sec.content_markdown = cover.title.strip()
    saved = project_storage.save(project)
    update_section_working_memory(saved, "cover_page")
    return saved


def _mirror_upload_to_blob(project_id: str, local_path: Path) -> None:
    blob = get_blob_storage()
    if blob.is_remote and local_path.exists():
        blob.upload_file(build_object_key("uploads", project_id, local_path.name), local_path)


def _ensure_local_or_blob(project_id: str, path_str: Optional[str]) -> Optional[Path]:
    if not path_str:
        return None
    p = Path(path_str)
    if not p.is_absolute():
        p = WORKSPACE_DIR / p
    if p.exists() and p.is_file():
        return p
    blob = get_blob_storage()
    if blob.is_remote:
        p.parent.mkdir(parents=True, exist_ok=True)
        if blob.download_to_path(build_object_key("uploads", project_id, p.name), p):
            return p
    return None


def _delete_local_and_blob(project_id: str, local_path: Optional[Path]) -> None:
    if not local_path:
        return
    try:
        local_path.unlink(missing_ok=True)
    except Exception:
        pass
    blob = get_blob_storage()
    if blob.is_remote:
        blob.delete(build_object_key("uploads", project_id, local_path.name))


@app.post("/api/projects/{project_id}/cover/logo/{slot}", response_model=ProtocolProject)
async def upload_cover_logo(
    project_id: str,
    slot: str,
    file: UploadFile = File(...),
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    assert_cover_admin_only(current_user)
    if slot not in ("university", "faculty"):
        raise HTTPException(status_code=400, detail="Emplacement de logo invalide ('university' ou 'faculty').")
    project = get_authorized_project(project_id, current_user)
    suffix = Path(file.filename or "logo.png").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(
            status_code=400,
            detail="Format de logo non supporté (PNG, JPG, JPEG, WEBP uniquement).",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Fichier logo vide.")

    upload_dir = settings.storage_dir / "uploads" / project_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    for old_f in upload_dir.glob(f"cover_logo_{slot}.*"):
        _delete_local_and_blob(project_id, old_f)
    dest_path = upload_dir / f"cover_logo_{slot}{suffix}"
    dest_path.write_bytes(content)
    _mirror_upload_to_blob(project_id, dest_path)

    logo_url = f"/api/projects/{project_id}/cover/logo/{slot}?v={int(time.time())}"
    if slot == "university":
        project.cover.university_logo_path = str(dest_path)
        project.cover.university_logo_url = logo_url
    else:
        project.cover.faculty_logo_path = str(dest_path)
        project.cover.faculty_logo_url = logo_url

    saved = project_storage.save(project)
    return saved


@app.delete("/api/projects/{project_id}/cover/logo/{slot}", response_model=ProtocolProject)
def delete_cover_logo(
    project_id: str,
    slot: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    assert_cover_admin_only(current_user)
    if slot not in ("university", "faculty"):
        raise HTTPException(status_code=400, detail="Emplacement de logo invalide ('university' ou 'faculty').")
    project = get_authorized_project(project_id, current_user)
    upload_dir = (settings.storage_dir / "uploads" / project_id).resolve()

    existing_path_str = (
        project.cover.university_logo_path if slot == "university" else project.cover.faculty_logo_path
    )
    if existing_path_str:
        p = Path(existing_path_str).resolve()
        if upload_dir in p.parents:
            _delete_local_and_blob(project_id, p)

    if slot == "university":
        project.cover.university_logo_path = ""
        project.cover.university_logo_url = ""
    else:
        project.cover.faculty_logo_path = ""
        project.cover.faculty_logo_url = ""

    return project_storage.save(project)


@app.get("/api/projects/{project_id}/cover/logo/{slot}")
def get_cover_logo_file(
    project_id: str,
    slot: str,
    current_user: UserRecord = Depends(get_active_user_allow_query_token),
) -> FileResponse:
    if slot not in ("university", "faculty"):
        raise HTTPException(status_code=400, detail="Emplacement de logo invalide ('university' ou 'faculty').")
    project = get_authorized_project(project_id, current_user)
    path_str = (
        project.cover.university_logo_path if slot == "university" else project.cover.faculty_logo_path
    )
    resolved = _ensure_local_or_blob(project_id, path_str)
    if resolved is not None:
        return FileResponse(path=str(resolved), headers={"Cache-Control": "no-store"})

    if slot == "university":
        default_logo = WORKSPACE_DIR / "tamplates" / "fmsb_uy1_logo.png"
        if default_logo.exists():
            return FileResponse(path=str(default_logo), headers={"Cache-Control": "no-store"})

    raise HTTPException(status_code=404, detail="Logo introuvable pour cet emplacement.")


@app.patch("/api/projects/{project_id}/sections/{section_id}", response_model=ProtocolProject)
def update_section(
    project_id: str,
    section_id: str,
    payload: UpdateSectionRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    if section_id == "cover_page":
        assert_cover_admin_only(current_user)
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue dans le canevas FMSB.")

    sec = project.sections[section_id]
    if payload.content_markdown is not None:
        if (payload.content_markdown or "").strip() != (sec.content_markdown or "").strip():
            snapshot_section_version(
                sec,
                source="manual",
                note="Sauvegarde manuelle",
            )
        sec.content_markdown = payload.content_markdown
        # If HTML wasn't explicitly sent, rebuild clean HTML so Module 2 never displays stale HTML tables
        if payload.content_html is None:
            sec.content_html = markdown_to_clean_html(payload.content_markdown)
    if payload.content_html is not None:
        is_auto_table_sec = section_id in ("toc", "abbreviations", "list_of_tables", "list_of_figures")
        had_html_table = "<table" in (sec.content_html or "").lower()
        incoming_has_table = "<table" in (payload.content_html or "").lower()
        if is_auto_table_sec and had_html_table and not incoming_has_table:
            # Protect generated TOC/list HTML tables from being flattened by rich-text editors without table support
            pass
        else:
            sec.content_html = payload.content_html
            # If user removed a table in RichTextEditor HTML, strip orphaned pipe tables from markdown too
            if not incoming_has_table and re.search(r"(?m)^\s*\|[\s:\-|]+\|\s*$", sec.content_markdown or ""):
                sec.content_markdown = re.sub(r"(?m)(?:\n|^)(?:\s*\|[^\n]+\n?)+", "\n", sec.content_markdown).strip()
    if payload.status is not None:
        sec.status = payload.status
    if payload.cited_ref_ids is not None:
        sec.cited_ref_ids = payload.cited_ref_ids
    if payload.user_notes is not None:
        sec.user_notes = payload.user_notes

    if section_id == "chap3_budget" and (payload.content_markdown is not None or payload.content_html is not None):
        from app.services.budget_engine import sync_budget_from_section
        from app.services.presentation_orchestrator import refresh_budget_slide_if_present

        sync_budget_from_section(project, overwrite=True)
        refresh_budget_slide_if_present(project)

    if section_id != "references" and (
        payload.content_markdown is not None or payload.content_html is not None
    ):
        renumber_and_sync_vancouver_references(project)

    _sync_module2_queue(project)
    evaluate_project_coherence(project, changed_section_id=section_id)
    saved = project_storage.save(project)
    if payload.content_markdown is not None or payload.content_html is not None:
        update_section_working_memory(saved, section_id)
    return scope_project_copilot_for_user(saved, current_user.id)


@app.patch("/api/projects/{project_id}/formatting", response_model=ProtocolProject)
def update_formatting(
    project_id: str,
    payload: UpdateFormattingRequest,
    current_user: UserRecord = Depends(require_module_write("module2")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    project.formatting = payload.formatting
    return project_storage.save(project)


@app.patch("/api/projects/{project_id}/structured", response_model=ProtocolProject)
def update_structured_data(
    project_id: str,
    payload: UpdateStructuredDataRequest,
    current_user: UserRecord = Depends(require_module_write("module2")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if payload.abbreviations is not None:
        project.abbreviations = payload.abbreviations
    if payload.variables is not None:
        project.variables = payload.variables
        if payload.variables and "chap1_variables" in project.sections:
            project.sections["chap1_variables"].suppress_structured_table = False
    if payload.literature_synthesis is not None:
        project.literature_synthesis = payload.literature_synthesis
        if payload.literature_synthesis and "chap2_etat_connaissances" in project.sections:
            project.sections["chap2_etat_connaissances"].suppress_structured_table = False
    if payload.timeline_grid is not None:
        project.timeline_grid = payload.timeline_grid
        if payload.timeline_grid.rows and "chap3_chronogramme" in project.sections:
            project.sections["chap3_chronogramme"].suppress_structured_table = False
    if payload.budget is not None:
        from app.services.budget_engine import normalize_budget_items, sync_section_from_budget
        from app.services.presentation_orchestrator import refresh_budget_slide_if_present

        project.budget = normalize_budget_items(payload.budget)
        if project.budget and "chap3_budget" in project.sections:
            project.sections["chap3_budget"].suppress_structured_table = False
            sync_section_from_budget(project)
        refresh_budget_slide_if_present(project)
    if payload.annexes_config is not None:
        project.annexes_config = payload.annexes_config
    _sync_module2_queue(project)
    return project_storage.save(project)


@app.get("/api/projects/{project_id}/annexes/preview")
def preview_annexes(
    project_id: str,
    current_user: UserRecord = Depends(get_active_user),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    return {"annexes": build_annexes_pack(project)}


@app.delete("/api/projects/{project_id}")
def delete_project(
    project_id: str,
    _admin: UserRecord = Depends(require_admin),
) -> Dict[str, bool]:
    deleted = project_storage.delete(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    return {"deleted": True}


# --- Phase 2 AI & Bibliography Endpoints ---


@app.post("/api/projects/{project_id}/research")
def run_project_research(
    project_id: str,
    payload: ResearchRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="research")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="research",
        title_fr="Recherche bibliographique",
        title_en="Literature Search",
    )
    try:
        report = run_research_pipeline(
            project,
            custom_query=payload.custom_query,
            max_per_source=payload.max_per_source,
            include_web_grounding=payload.include_web_grounding,
            search_scope=payload.search_scope,
        )
        project_storage.save(project)
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="research",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(report.get("planner_model", "")),
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {"project": project.model_dump(mode="json"), "report": report}
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/segment")
def segment_project(
    project_id: str,
    payload: SegmentRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="segment")
    if not project.references:
        raise HTTPException(
            status_code=400,
            detail="Aucune référence trouvée dans le projet. Lancez d'abord la recherche documentaire.",
        )
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="segment",
        title_fr="Segmentation des articles",
        title_en="Article Segmentation",
    )
    try:
        report = segment_project_articles(
            project,
            limit=payload.limit,
            only_unsegmented=payload.only_unsegmented,
        )
        project_storage.save(project)
        models = report.get("models") or []
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="segment",
            credits_cost=cost,
            project_id=project_id,
            model_used=",".join(str(m) for m in models[:2]),
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {"project": project.model_dump(mode="json"), "report": report}
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.patch("/api/projects/{project_id}/segments/{segment_id}", response_model=ProtocolProject)
def update_segment(
    project_id: str,
    segment_id: str,
    payload: ToggleSegmentRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    for seg in project.article_segments:
        if seg.segment_id == segment_id:
            if payload.approved is not None:
                seg.approved = payload.approved
            if payload.target_section_ids is not None:
                seg.target_section_ids = payload.target_section_ids
            return project_storage.save(project)
    raise HTTPException(status_code=404, detail="Segment introuvable.")


@app.patch("/api/projects/{project_id}/images/{asset_id}", response_model=ProtocolProject)
def update_image_asset(
    project_id: str,
    asset_id: str,
    payload: ToggleImageRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    for img in project.images:
        if img.asset_id == asset_id:
            if payload.approved is not None:
                img.approved = payload.approved
            if payload.target_section_id is not None:
                img.target_section_id = payload.target_section_id
            if payload.caption is not None:
                img.caption = payload.caption
            if payload.title is not None:
                img.title = payload.title
            if payload.anchor_hint is not None:
                img.anchor_hint = payload.anchor_hint
            _sync_module2_queue(project)
            return project_storage.save(project)
    raise HTTPException(status_code=404, detail="Image introuvable.")


@app.post("/api/projects/{project_id}/images/upload", response_model=ProtocolProject)
async def upload_project_image(
    project_id: str,
    file: UploadFile = File(...),
    target_section_id: str = Form("chap1_justificatif"),
    title: str = Form("Figure personnalisée"),
    caption: str = Form(""),
    anchor_hint: str = Form(""),
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    suffix = Path(file.filename or "image.png").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(status_code=400, detail="Format d'image non supporté (PNG, JPG, JPEG, WEBP uniquement).")

    upload_dir = settings.storage_dir / "uploads" / project_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    asset_id = f"img_{uuid.uuid4().hex[:10]}"
    dest_path = upload_dir / f"{asset_id}{suffix}"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Fichier image vide.")
    dest_path.write_bytes(content)
    _mirror_upload_to_blob(project_id, dest_path)

    asset = ImageAsset(
        asset_id=asset_id,
        title=title.strip() or (file.filename or "Figure"),
        source_url=f"/api/projects/{project_id}/images/{asset_id}/file",
        local_path=str(dest_path),
        caption=caption.strip(),
        target_section_id=target_section_id,
        anchor_hint=anchor_hint.strip(),
        approved=True,
    )
    project.images.append(asset)
    _sync_module2_queue(project)
    return project_storage.save(project)


@app.get("/api/projects/{project_id}/images/{asset_id}/file")
def get_uploaded_image_file(
    project_id: str,
    asset_id: str,
    current_user: UserRecord = Depends(get_active_user_allow_query_token),
) -> FileResponse:
    project = get_authorized_project(project_id, current_user)
    for img in project.images:
        if img.asset_id == asset_id:
            resolved = _ensure_local_or_blob(project_id, img.local_path)
            if resolved is None:
                if materialize_image_asset(project, img):
                    project_storage.save(project)
                    if img.local_path:
                        _mirror_upload_to_blob(project_id, Path(img.local_path))
                resolved = _ensure_local_or_blob(project_id, img.local_path)
            if resolved is not None:
                return FileResponse(path=str(resolved))
    raise HTTPException(status_code=404, detail="Fichier image introuvable.")


@app.delete("/api/projects/{project_id}/images/{asset_id}", response_model=ProtocolProject)
def delete_image_asset(
    project_id: str,
    asset_id: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    remaining = []
    found = False
    for img in project.images:
        if img.asset_id == asset_id:
            found = True
            if img.local_path:
                _delete_local_and_blob(project_id, Path(img.local_path))
        else:
            remaining.append(img)
    if not found:
        raise HTTPException(status_code=404, detail="Image introuvable.")
    project.images = remaining
    _sync_module2_queue(project)
    return project_storage.save(project)


@app.post("/api/projects/{project_id}/sections/{section_id}/draft")
def draft_section(
    project_id: str,
    section_id: str,
    payload: DraftSectionRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="draft_section")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="draft",
        title_fr="Rédaction de la section",
        title_en="Section Drafting",
    )
    try:
        if section_id in ("toc", "abbreviations", "list_of_tables", "list_of_figures"):
            emit_pipeline_step(
                "lists_sync",
                "Génération des listes & sommaire...",
                "Generating lists & table of contents...",
                progress_pct=50,
            )
            res_lists = generate_all_lists_and_toc(project, use_gemini_decision=True)
            saved = project_storage.save(project)
            sec_out = saved.sections[section_id]
            plan = res_lists["plan"]
            note_summary = (
                f"Liste automatique unifiée sans doublon ({res_lists['abbreviations_count']} abréviations, "
                f"{res_lists['tables_count']} tableaux, {res_lists['figures_count']} figures, "
                f"{plan.total_roman_pages} p. romaines + {plan.total_arabic_pages} p. arabes)."
            )
            record_usage_and_debit(
                db=db,
                user=current_user,
                module_key="module1",
                action="draft_section",
                credits_cost=cost,
                project_id=project_id,
                model_used=str(plan.model_used or ""),
                ip_address=get_client_ip(request),
            )
            if tracked:
                finish_pipeline_task("done")
            return {
                "project": saved.model_dump(mode="json"),
                "result": {
                    "section_id": section_id,
                    "content_markdown": sec_out.content_markdown,
                    "content_html": sec_out.content_html,
                    "cited_ref_ids": [],
                    "audit_notes": [note_summary],
                    "model_used": plan.model_used,
                },
            }

        result = draft_section_with_ai(
            project,
            section_id=section_id,
            user_instruction=payload.instruction,
            force_external_research=bool(getattr(payload, "force_external_research", False)),
        )
        if section_id == "references":
            emit_pipeline_step(
                "vancouver_check",
                "Validation Vancouver ICMJE...",
                "Validating Vancouver ICMJE citations...",
                progress_pct=82,
            )
            ref_report = build_and_validate_vancouver_references(
                project, use_gemini_decision=True, mark_validated=False
            )
            sec_ref = project.sections.get("references")
            if sec_ref and ref_report.get("references_count", 0) > 0:
                result["content_markdown"] = sec_ref.content_markdown
                result["content_html"] = sec_ref.content_html
                result["model_used"] = ref_report.get("decision_model", result.get("model_used"))
                result["audit_notes"] = list(result.get("audit_notes") or []) + [
                    f"Validation Vancouver ICMJE : {ref_report['references_count']} références."
                ]
        emit_pipeline_step(
            "coherence_save",
            "Vérification de cohérence & sauvegarde...",
            "Checking coherence & saving...",
            progress_pct=92,
        )
        _sync_module2_queue(project)
        evaluate_project_coherence(project, changed_section_id=section_id)
        project_storage.save(project)
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="draft_section",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(result.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {"project": project.model_dump(mode="json"), "result": result}
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/sections/{section_id}/audit")
def audit_section(
    project_id: str,
    section_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="audit_section")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="audit",
        title_fr="Audit de conformité FMSB",
        title_en="FMSB Compliance Audit",
    )
    try:
        result = audit_section_with_ai(project, section_id=section_id)
        project_storage.save(project)
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="audit_section",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(result.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {"project": project.model_dump(mode="json"), "result": result}
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/sections/{section_id}/fix")
def fix_section(
    project_id: str,
    section_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="fix_section")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="fix",
        title_fr="Correction guidée par l'audit",
        title_en="Audit-Guided Fix",
    )
    try:
        result = fix_section_issues_with_ai(project, section_id=section_id)
        emit_pipeline_step(
            "coherence_save",
            "Synchronisation & sauvegarde...",
            "Syncing & saving...",
            progress_pct=92,
        )
        _sync_module2_queue(project)
        evaluate_project_coherence(project, changed_section_id=section_id)
        project_storage.save(project)
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="fix_section",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(result.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {"project": project.model_dump(mode="json"), "result": result}
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


class SmartReformulateRequest(BaseModel):
    instruction: str = ""
    current_markdown: Optional[str] = None
    mode: str = "auto"  # "auto" | "audit_fix" | "complement"
    audit_issues: List[str] = Field(default_factory=list)


class TransformSelectionRequest(BaseModel):
    selected_text: str = Field(..., min_length=1)
    action: str = "reformulate"  # "reformulate" | "modify" | "complement" | "cite" | "shorten" | "expand"
    instruction: str = ""
    current_markdown: Optional[str] = None


class ApplyReformulationRequest(BaseModel):
    content_markdown: str
    content_html: Optional[str] = None
    source: str = "smart_reformulate"
    note: str = ""


@app.post("/api/projects/{project_id}/sections/{section_id}/smart-reformulate")
def smart_reformulate_endpoint(
    project_id: str,
    section_id: str,
    payload: SmartReformulateRequest,
    request: Request,
    current_user: UserRecord = Depends(require_module_write("module1")),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    """Non-destructive selective AI reformulation (returns colored diff preview before applying)."""
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="draft_section")
    try:
        preview = smart_reformulate_section(
            project=project,
            section_id=section_id,
            instruction=payload.instruction,
            current_markdown=payload.current_markdown,
            mode=payload.mode,
            audit_issues=payload.audit_issues,
        )
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="smart_reformulate",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(preview.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
        return preview
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/sections/{section_id}/transform-selection")
def transform_selection_endpoint(
    project_id: str,
    section_id: str,
    payload: TransformSelectionRequest,
    request: Request,
    current_user: UserRecord = Depends(require_module_write("module1")),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    """Non-destructive transformation of ONLY the user's highlighted passage."""
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="draft_section")
    try:
        preview = transform_selected_text(
            project=project,
            section_id=section_id,
            selected_text=payload.selected_text,
            action=payload.action,
            instruction=payload.instruction,
            current_markdown=payload.current_markdown,
        )
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action=f"transform_selection:{payload.action}",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(preview.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
        return preview
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/sections/{section_id}/apply-reformulation", response_model=ProtocolProject)
def apply_reformulation_endpoint(
    project_id: str,
    section_id: str,
    payload: ApplyReformulationRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    """Commit a validated selective reformulation / selection transform after snapshotting the previous version."""
    if section_id == "cover_page":
        assert_cover_admin_only(current_user)
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    sec = project.sections[section_id]
    snapshot_section_version(
        sec,
        source=payload.source or "smart_reformulate",
        note=payload.note or "Avant reformulation sélective",
    )
    sec.content_markdown = payload.content_markdown
    sec.content_html = (
        payload.content_html
        if payload.content_html is not None and payload.content_html.strip()
        else markdown_to_clean_html(payload.content_markdown)
    )
    if sec.status == StepStatus.NOT_STARTED and (sec.content_markdown or "").strip():
        sec.status = StepStatus.IN_PROGRESS
    if section_id == "chap3_budget":
        from app.services.budget_engine import sync_budget_from_section
        from app.services.presentation_orchestrator import refresh_budget_slide_if_present

        sync_budget_from_section(project, overwrite=True)
        refresh_budget_slide_if_present(project)
    if section_id != "references":
        renumber_and_sync_vancouver_references(project)
    _sync_module2_queue(project)
    evaluate_project_coherence(project, changed_section_id=section_id)
    saved = project_storage.save(project)
    update_section_working_memory(saved, section_id)
    return scope_project_copilot_for_user(saved, current_user.id)


class HumanizeRequest(BaseModel):
    use_ai: bool = True


class HarmonizeSectionRequest(BaseModel):
    alert_id: Optional[str] = None
    use_ai: bool = True


@app.post("/api/projects/{project_id}/sections/{section_id}/humanize")
def humanize_section(
    project_id: str,
    section_id: str,
    request: Request,
    payload: Optional[HumanizeRequest] = None,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="humanize_section")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="humanize",
        title_fr="Humanisation académique",
        title_en="Academic Humanization",
    )
    try:
        use_ai = True if payload is None else bool(payload.use_ai)
        result = humanize_section_with_ai(project, section_id=section_id, use_ai=use_ai)
        emit_pipeline_step(
            "coherence_save",
            "Mise à jour & sauvegarde...",
            "Updating & saving...",
            progress_pct=92,
        )
        _sync_module2_queue(project)
        evaluate_project_coherence(project, changed_section_id=section_id)
        project_storage.save(project)
        model_used = str(result.get("model_used", ""))
        is_real_ai = model_used not in ("none", "humanizer-deterministe-fmsb", "")
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="humanize_section",
            credits_cost=cost if (is_real_ai and not result.get("skipped")) else 0,
            project_id=project_id,
            model_used=model_used,
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {"project": project.model_dump(mode="json"), "result": result}
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/sections/{section_id}/harmonize")
def harmonize_section(
    project_id: str,
    section_id: str,
    request: Request,
    payload: Optional[HarmonizeSectionRequest] = None,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="harmonize_section")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="harmonize",
        title_fr="Harmonisation de cohérence",
        title_en="Coherence Harmonization",
    )
    try:
        alert_id = payload.alert_id if payload is not None else None
        use_ai = payload.use_ai if payload is not None else True
        result = harmonize_section_with_working_memory(
            project=project,
            section_id=section_id,
            alert_id=alert_id,
            use_ai=use_ai,
        )
        _sync_module2_queue(project)
        saved = project_storage.save(project)
        model_used = str(result.get("model_used", ""))
        is_real_ai = model_used not in ("none", "coherence-harmonizer-deterministe", "")
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="harmonize_section",
            credits_cost=cost if (is_real_ai and result.get("harmonized")) else 0,
            project_id=project_id,
            model_used=model_used,
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {"project": saved.model_dump(mode="json"), "result": result}
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


class SectionAntigravityRequest(BaseModel):
    instruction: Optional[str] = None
    capture_images: bool = True


@app.post("/api/projects/{project_id}/sections/{section_id}/antigravity")
def run_section_antigravity(
    project_id: str,
    section_id: str,
    request: Request,
    payload: Optional[SectionAntigravityRequest] = None,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="antigravity_section")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="antigravity",
        title_fr="Enrichissement scientifique web",
        title_en="Web Scientific Enrichment",
    )
    try:
        orch = orchestrate_section_action(
            project,
            section_id=section_id,
            action="antigravity",
            user_instruction=payload.instruction if payload else None,
            force_antigravity=True,
        )
        _sync_module2_queue(project)
        project_storage.save(project)
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="antigravity_section",
            credits_cost=cost,
            project_id=project_id,
            model_used=str((orch.get("antigravity_report") or {}).get("agent_used", "")),
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {
            "project": project.model_dump(mode="json"),
            "orchestration_trace": orch["orchestration_trace"],
            "antigravity_report": orch["antigravity_report"],
        }
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/sections/{section_id}/suggest-table")
def suggest_section_table(
    project_id: str,
    section_id: str,
    payload: SuggestTableRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="suggest_table")
    try:
        result = suggest_section_table_with_ai(
            project,
            section_id=section_id,
            table_title=payload.title,
            table_goal=payload.goal,
        )
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="suggest_table",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(result.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
        return {"suggestion": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/sections/{section_id}/tables", response_model=ProtocolProject)
def add_section_table(
    project_id: str,
    section_id: str,
    payload: CreateSectionTableRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    tbl = SectionTable(
        table_id=f"tbl_{uuid.uuid4().hex[:8]}",
        title=payload.title.strip() or "Tableau personnalisé",
        anchor_hint=payload.anchor_hint.strip(),
        headers=[h.strip() for h in payload.headers if h.strip()] or ["Colonne 1", "Colonne 2"],
        rows=payload.rows,
    )
    project.sections[section_id].custom_tables.append(tbl)
    _sync_module2_queue(project)
    return project_storage.save(project)


class UpdateSectionTableRequest(BaseModel):
    title: Optional[str] = None
    anchor_hint: Optional[str] = None
    headers: Optional[List[str]] = None
    rows: Optional[List[List[str]]] = None


@app.patch("/api/projects/{project_id}/sections/{section_id}/tables/{table_id}", response_model=ProtocolProject)
def update_section_table(
    project_id: str,
    section_id: str,
    table_id: str,
    payload: UpdateSectionTableRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    sec = project.sections[section_id]

    target = next((t for t in sec.custom_tables if t.table_id == table_id), None)
    if target is None and table_id.startswith("__structured"):
        structured = build_structured_table_for_section(project, section_id)
        if structured is None:
            raise HTTPException(status_code=404, detail="Tableau structuré introuvable.")
        sec.suppress_structured_table = False
        target = SectionTable(
            table_id=f"tbl_{uuid.uuid4().hex[:8]}",
            title=structured.title,
            anchor_hint=structured.anchor_hint,
            headers=structured.headers,
            rows=structured.rows,
        )
        sec.custom_tables.append(target)

    if target is None:
        raise HTTPException(status_code=404, detail="Tableau introuvable.")

    if payload.title is not None:
        cleaned_title = payload.title.strip()
        if cleaned_title:
            target.title = cleaned_title
    if payload.anchor_hint is not None:
        target.anchor_hint = payload.anchor_hint.strip()
    if payload.headers is not None:
        cleaned_headers = [h.strip() for h in payload.headers if h.strip()]
        if cleaned_headers:
            target.headers = cleaned_headers
    if payload.rows is not None:
        target.rows = payload.rows

    _sync_module2_queue(project)
    return project_storage.save(project)


@app.delete("/api/projects/{project_id}/sections/{section_id}/tables/{table_id}", response_model=ProtocolProject)
def delete_section_table(
    project_id: str,
    section_id: str,
    table_id: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    sec = project.sections[section_id]

    if table_id.startswith("__structured"):
        cleared = clear_structured_table_for_section(project, section_id)
        if not cleared:
            raise HTTPException(status_code=404, detail="Tableau structuré déjà absent.")
        _strip_table_from_section_text(sec, "")
        _sync_module2_queue(project)
        return project_storage.save(project)

    removed = next((t for t in sec.custom_tables if t.table_id == table_id), None)
    if removed is None:
        raise HTTPException(status_code=404, detail="Tableau introuvable.")
    sec.custom_tables = [t for t in sec.custom_tables if t.table_id != table_id]
    # Also suppress any backing structured clone when deleting custom tables in structured sections
    # so deleted tables NEVER reappear in Module 2 preview, List of Tables, or Word/PDF export.
    if section_id in ("chap1_variables", "chap2_etat_connaissances", "chap3_chronogramme", "chap3_budget") and not sec.custom_tables:
        clear_structured_table_for_section(project, section_id)
    _strip_table_from_section_text(sec, removed.title)
    _sync_module2_queue(project)
    return project_storage.save(project)


@app.post("/api/projects/{project_id}/media/transport-sync")
def sync_media_transport(
    project_id: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> Dict[str, object]:
    """Transport downloaded/captured images locally and strictly synchronize Module 1 -> Module 2 tables."""
    project = get_authorized_project(project_id, current_user)
    image_report = transport_and_repair_project_images(project)
    table_report = synchronize_module1_to_module2_tables(project)
    _sync_module2_queue(project)
    saved = project_storage.save(project)
    return {
        "project": saved.model_dump(mode="json"),
        "image_report": image_report,
        "table_report": table_report,
    }


class CreateSectionDiagramRequest(BaseModel):
    title: str
    caption: str = ""
    anchor_hint: str = ""
    layout: str = "vertical"
    nodes: List[DiagramNode]
    connections: List[DiagramConnection] = []


@app.post("/api/projects/{project_id}/sections/{section_id}/suggest-diagram")
def suggest_section_diagram(
    project_id: str,
    section_id: str,
    payload: SuggestTableRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="suggest_diagram")
    try:
        result = suggest_section_diagram_with_ai(
            project,
            section_id=section_id,
            diagram_title=payload.title,
            diagram_goal=payload.goal,
        )
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="suggest_diagram",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(result.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
        return {"suggestion": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/sections/{section_id}/diagrams", response_model=ProtocolProject)
def add_section_diagram(
    project_id: str,
    section_id: str,
    payload: CreateSectionDiagramRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    dg = SectionDiagram(
        diagram_id=f"diag_{uuid.uuid4().hex[:8]}",
        title=payload.title.strip() or "Schéma conceptuel",
        caption=payload.caption.strip(),
        anchor_hint=payload.anchor_hint.strip(),
        layout=payload.layout if payload.layout in ("vertical", "horizontal") else "vertical",
        nodes=payload.nodes,
        connections=payload.connections,
    )
    project.sections[section_id].custom_diagrams.append(dg)
    _sync_module2_queue(project)
    return project_storage.save(project)


class UpdateSectionDiagramRequest(BaseModel):
    title: Optional[str] = None
    caption: Optional[str] = None
    anchor_hint: Optional[str] = None
    layout: Optional[str] = None
    nodes: Optional[List[DiagramNode]] = None
    connections: Optional[List[DiagramConnection]] = None


@app.patch("/api/projects/{project_id}/sections/{section_id}/diagrams/{diagram_id}", response_model=ProtocolProject)
def update_section_diagram(
    project_id: str,
    section_id: str,
    diagram_id: str,
    payload: UpdateSectionDiagramRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    sec = project.sections[section_id]
    target = next((d for d in sec.custom_diagrams if d.diagram_id == diagram_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Schéma introuvable.")

    if payload.title is not None:
        cleaned_title = payload.title.strip()
        if cleaned_title:
            target.title = cleaned_title
    if payload.caption is not None:
        target.caption = payload.caption.strip()
    if payload.anchor_hint is not None:
        target.anchor_hint = payload.anchor_hint.strip()
    if payload.layout is not None and payload.layout in ("vertical", "horizontal"):
        target.layout = payload.layout
    if payload.nodes is not None:
        target.nodes = payload.nodes
    if payload.connections is not None:
        target.connections = payload.connections

    _sync_module2_queue(project)
    return project_storage.save(project)


@app.get("/api/projects/{project_id}/sections/{section_id}/diagrams/{diagram_id}/svg")
def get_section_diagram_svg(
    project_id: str,
    section_id: str,
    diagram_id: str,
    current_user: UserRecord = Depends(get_active_user_allow_query_token),
) -> Response:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section introuvable.")
    for dg in project.sections[section_id].custom_diagrams:
        if dg.diagram_id == diagram_id:
            svg = render_diagram_svg(dg)
            return Response(content=svg, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Schéma introuvable.")


@app.delete("/api/projects/{project_id}/sections/{section_id}/diagrams/{diagram_id}", response_model=ProtocolProject)
def delete_section_diagram(
    project_id: str,
    section_id: str,
    diagram_id: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    sec = project.sections[section_id]
    before = len(sec.custom_diagrams)
    sec.custom_diagrams = [d for d in sec.custom_diagrams if d.diagram_id != diagram_id]
    if len(sec.custom_diagrams) == before:
        raise HTTPException(status_code=404, detail="Schéma introuvable.")
    _sync_module2_queue(project)
    return project_storage.save(project)


@app.post("/api/projects/{project_id}/import")
async def import_existing_protocol(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    import_mode: str = Form("skeleton"),
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if project.owner_user_id and project.owner_user_id != current_user.id:
        from app.core.request_lang import bi

        raise HTTPException(
            status_code=403,
            detail={
                "code": "CROSS_USER_IMPORT_BLOCKED",
                "message": bi(
                    "Par sécurité, l'importation sur le protocole d'un autre utilisateur est bloquée "
                    "afin d'éviter d'écraser son travail. Créez votre propre protocole dans le Studio.",
                    "For safety, importing a document onto another user's protocol is blocked "
                    "to prevent overwriting their work. Create your own protocol in the Studio.",
                ),
            },
        )
    normalized_mode = "internal_source" if (import_mode or "").strip().lower() == "internal_source" else "skeleton"
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="import_protocol")
    cover_snapshot = project.cover.model_copy(deep=True)
    name_snapshot = project.name
    cover_sec_snapshot = (
        project.sections["cover_page"].model_copy(deep=True)
        if "cover_page" in project.sections
        else None
    )
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="import",
        title_fr="Import source interne (Bibliothèque)" if normalized_mode == "internal_source" else "Import & analyse du document",
        title_en="Internal Source Import (Library)" if normalized_mode == "internal_source" else "Document Import & Analysis",
    )
    content = await file.read()
    try:
        report = analyze_and_import_protocol(
            project,
            filename=file.filename or "protocole.docx",
            content_bytes=content,
            import_mode=normalized_mode,
        )
        if normalized_mode == "internal_source" or current_user.role != "admin":
            project.name = name_snapshot
            project.cover = cover_snapshot
            if cover_sec_snapshot is not None:
                project.sections["cover_page"] = cover_sec_snapshot
        project_storage.save(project)
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module1",
            action="import_protocol",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(report.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {"project": project.model_dump(mode="json"), "report": report}
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/projects/{project_id}/internal-sources/{source_id}")
def delete_internal_source(
    project_id: str,
    source_id: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> Dict[str, object]:
    """Delete an internal library source document and all its mapped section entries."""
    project = get_authorized_project(project_id, current_user)
    removed = remove_internal_source_from_project(project, source_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Source interne introuvable.")
    saved = project_storage.save(project)
    return {"project": saved.model_dump(mode="json"), "removed_source_id": source_id}


@app.post("/api/projects/{project_id}/global-audit")
def global_audit_protocol(
    project_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module2", action="global_audit")
    res = run_global_protocol_audit(project)
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module2",
        action="global_audit",
        credits_cost=cost,
        project_id=project_id,
        model_used=str(res.get("model_used", "")),
        ip_address=get_client_ip(request),
    )
    return res


@app.post("/api/projects/{project_id}/humanize-all")
def humanize_protocol(
    project_id: str,
    request: Request,
    payload: Optional[HumanizeRequest] = None,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module2", action="humanize_protocol")
    use_ai = True if payload is None else bool(payload.use_ai)
    report = humanize_entire_protocol(project, use_ai=use_ai)
    _sync_module2_queue(project)
    evaluate_project_coherence(project)
    saved = project_storage.save(project)
    model_used = str(report.get("model_used", ""))
    is_real_ai = model_used not in ("none", "humanizer-deterministe-fmsb", "")
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module2",
        action="humanize_protocol",
        credits_cost=cost if (is_real_ai and report.get("sections_humanized", 0) > 0) else 0,
        project_id=project_id,
        model_used=model_used,
        ip_address=get_client_ip(request),
    )
    return {
        "project": saved.model_dump(mode="json"),
        "report": report,
    }


@app.post("/api/projects/{project_id}/sync-structured")
def sync_structured_data(
    project_id: str,
    current_user: UserRecord = Depends(require_module_write("module2")),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    report = sync_structured_from_sections(project)
    from app.services.presentation_orchestrator import refresh_budget_slide_if_present

    refresh_budget_slide_if_present(project)
    saved = project_storage.save(project)
    return {"project": saved.model_dump(mode="json"), "report": report}


@app.post("/api/projects/{project_id}/generate-toc")
@app.post("/api/projects/{project_id}/generate-lists")
def generate_project_toc(
    project_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module2", action="generate_toc")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="generate_toc",
        title_fr="Génération TDM & listes",
        title_en="TOC & Lists Generation",
    )
    try:
        emit_pipeline_step(
            "collect_items",
            "Collecte des abréviations, tableaux et figures...",
            "Collecting abbreviations, tables, and figures...",
            progress_pct=35,
        )
        res = generate_all_lists_and_toc(project, use_gemini_decision=True)
        emit_pipeline_step(
            "build_toc",
            "Pagination A4 & mise à jour du sommaire...",
            "Computing A4 pagination & updating TOC...",
            progress_pct=85,
        )
        plan = res["plan"]
        entries = res["toc_entries"]
        saved = project_storage.save(project)
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module2",
            action="generate_toc",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(plan.model_used or ""),
            ip_address=get_client_ip(request),
        )
        pagination_summary = {
            "model_used": plan.model_used,
            "decision_notes": plan.decision_notes,
            "total_roman_pages": plan.total_roman_pages,
            "total_arabic_pages": plan.total_arabic_pages,
            "usable_width_cm": plan.usable_width_cm,
            "usable_height_cm": plan.usable_height_cm,
            "figures_checked": len(plan.figure_dimensions_cm),
            "figure_dimensions_cm": plan.figure_dimensions_cm,
        }
        if tracked:
            finish_pipeline_task("done")
        return {
            "project": saved.model_dump(mode="json"),
            "entries_count": res["entries_count"],
            "abbreviations_count": res["abbreviations_count"],
            "tables_count": res["tables_count"],
            "figures_count": res["figures_count"],
            "entries": [e.model_dump(mode="json") for e in entries],
            "pagination_summary": pagination_summary,
        }
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/validate-references")
def validate_project_references(
    project_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module2", action="validate_references")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="validate_refs",
        title_fr="Validation Vancouver ICMJE",
        title_en="Vancouver ICMJE Validation",
    )
    try:
        emit_pipeline_step(
            "vancouver_audit",
            "Normalisation des citations Vancouver...",
            "Normalizing Vancouver citations...",
            progress_pct=45,
        )
        ref_report = build_and_validate_vancouver_references(
            project, use_gemini_decision=True, mark_validated=True
        )
        emit_pipeline_step(
            "sync_lists",
            "Synchronisation du sommaire & sauvegarde...",
            "Synchronizing TOC & saving...",
            progress_pct=85,
        )
        generate_all_lists_and_toc(project, use_gemini_decision=False)
        saved = project_storage.save(project)
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module2",
            action="validate_references",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(ref_report.get("decision_model", "")),
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {
            "project": saved.model_dump(mode="json"),
            **ref_report,
        }
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/export/docx")
def export_docx(
    project_id: str,
    request: Request,
    payload: Optional[UpdateFormattingRequest] = Body(default=None),
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> FileResponse:
    project = get_authorized_project(project_id, current_user)
    saved_formatting = project.formatting.model_copy(deep=True)
    can_write_m2 = current_user.role == "admin" or is_module_active(db, current_user, "module2")[0]
    if payload is not None and payload.formatting is not None:
        project.formatting = payload.formatting

    out_path = settings.storage_dir / "exports" / f"{project.project_id}_protocole.docx"
    compile_protocol_docx(project, out_path)
    toc_depth = int(getattr(project.formatting, "toc_depth", 2) or 2)
    if update_docx_fields_via_word_com(out_path, toc_depth=toc_depth):
        sync_toc_from_exported_docx(project, out_path)
    if not can_write_m2:
        project.formatting = saved_formatting
    project_storage.save(project)
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module2",
        action="export_docx",
        credits_cost=0,
        project_id=project_id,
        ip_address=get_client_ip(request),
    )
    return FileResponse(
        path=str(out_path),
        filename=f"Protocole_{project.project_id}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.post("/api/projects/{project_id}/export/pdf")
def export_pdf(
    project_id: str,
    request: Request,
    payload: Optional[UpdateFormattingRequest] = Body(default=None),
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> FileResponse:
    project = get_authorized_project(project_id, current_user)
    saved_formatting = project.formatting.model_copy(deep=True)
    can_write_m2 = current_user.role == "admin" or is_module_active(db, current_user, "module2")[0]
    if payload is not None and payload.formatting is not None:
        project.formatting = payload.formatting

    out_path = settings.storage_dir / "exports" / f"{project.project_id}_protocole.pdf"
    try:
        compile_protocol_pdf(project, out_path)
        sync_toc_from_exported_docx(project, out_path.with_suffix(".docx"))
        if not can_write_m2:
            project.formatting = saved_formatting
        project_storage.save(project)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module2",
        action="export_pdf",
        credits_cost=0,
        project_id=project_id,
        ip_address=get_client_ip(request),
    )
    return FileResponse(
        path=str(out_path),
        filename=f"Protocole_{project.project_id}.pdf",
        media_type="application/pdf",
    )


# --- Phase 5: CRF / Fiche de Collecte (Annexe 1) & Module 3 Patient Data Entry ---


@app.post("/api/projects/{project_id}/annexes/{section_id}/generate")
def generate_project_sub_annex(
    project_id: str,
    section_id: str,
    payload: GenerateSubAnnexRequest,
    request: Request,
    current_user: UserRecord = Depends(require_module_write("module1")),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    """Generate or refine an individualized Chapter V sub-appendix adapted to the user's Faculty and cover info."""
    project = get_authorized_project(project_id, current_user)
    cost = 0
    if payload.use_ai:
        cost = assert_ai_quota_available(db, current_user, module_key="module1", action="generate_sub_annex")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="generate_sub_annex",
        title_fr=f"Rédaction de sous-annexe ({section_id})",
        title_en=f"Generating Sub-Appendix ({section_id})",
    )
    try:
        res = generate_sub_annex_content(
            project,
            section_id=section_id,
            user_instruction=payload.user_instruction,
            bilingual=payload.bilingual,
            use_ai=payload.use_ai,
        )
        saved = project_storage.save(project)
        if payload.use_ai:
            record_usage_and_debit(
                db=db,
                user=current_user,
                module_key="module1",
                action="generate_sub_annex",
                credits_cost=cost,
                project_id=project_id,
                model_used=str(res.get("model_used", "")),
                ip_address=get_client_ip(request),
            )
        if tracked:
            finish_pipeline_task("done")
        return {
            "project": saved.model_dump(mode="json"),
            "section_id": res["section_id"],
            "content_markdown": res["content_markdown"],
            "content_html": res["content_html"],
            "model_used": res["model_used"],
            "assistant_commentary": res["assistant_commentary"],
            "faculty_context": res["faculty_context"],
        }
    except ValueError as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        reset_ai_routing_policy()


@app.post("/api/projects/{project_id}/crf/ai-generate")
def ai_generate_project_crf(
    project_id: str,
    payload: GenerateCRFRequest,
    request: Request,
    current_user: UserRecord = Depends(require_module_write("module3")),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    """Generate or improve the CAMES/BPC data collection form (Annexe 1) and rebuild the CRF mask."""
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module3", action="generate_crf")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="generate_crf",
        title_fr="Création / Amélioration IA de la Fiche de Collecte (CAMES)",
        title_en="AI Generation / Improvement of Data Collection Form (CAMES)",
    )
    try:
        res = generate_or_improve_crf_with_ai(
            project,
            user_instruction=payload.user_instruction,
            mode=payload.mode,
        )
        evaluate_project_coherence(project, changed_section_id="annexe_fiche_collecte")
        saved = project_storage.save(project)
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module3",
            action="generate_crf",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(res.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
        if tracked:
            finish_pipeline_task("done")
        return {
            "project": saved.model_dump(mode="json"),
            "crf_fields": [f.model_dump(mode="json") for f in saved.crf_fields],
            "annexe1_html": res["annexe1_html"],
            "model_used": res["model_used"],
            "mode_applied": res["mode_applied"],
            "assistant_commentary": res["assistant_commentary"],
            "cames_compliance_checklist": res["cames_compliance_checklist"],
        }
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        reset_ai_routing_policy()


@app.post("/api/projects/{project_id}/crf/extract-and-sync")
def extract_and_sync_project_crf(
    project_id: str,
    payload: ExtractCRFRequest,
    request: Request,
    current_user: UserRecord = Depends(require_module_write("module3")),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    """
    Dedicated CRF extractor & compiler pipeline:
    1. Isolates exact start/end boundaries of the CRF from Annexes or Internal Library.
    2. Compiles questions/tables/choices into structured CRFFieldSpec[] via Hybrid AI/Deterministic engine.
    3. Reconciles non-destructively (preserving patient_records field_ids) and syncs Mask & Annexe 1.
    """
    project = get_authorized_project(project_id, current_user)
    cost = 0
    if payload.use_ai:
        cost = assert_ai_quota_available(db, current_user, module_key="module3", action="extract_crf")
    tracked = _begin_request_pipeline_task(
        request,
        current_user,
        project,
        operation="extract_crf",
        title_fr="Identification & Compilation dédiée de la Fiche de Collecte",
        title_en="Dedicated Data Collection Form Extraction & Compilation",
    )
    try:
        res = extract_and_sync_crf_for_project(
            project,
            source_mode=payload.source_mode,
            source_id=payload.source_id,
            use_ai=payload.use_ai,
        )
        evaluate_project_coherence(project, changed_section_id="annexe_fiche_collecte")
        saved = project_storage.save(project)
        if payload.use_ai:
            record_usage_and_debit(
                db=db,
                user=current_user,
                module_key="module3",
                action="extract_crf",
                credits_cost=cost,
                project_id=project_id,
                model_used=str(res.get("model_used", "")),
                ip_address=get_client_ip(request),
            )
        if tracked:
            finish_pipeline_task("done")
        return {
            "project": saved.model_dump(mode="json"),
            "crf_fields": [f.model_dump(mode="json") for f in saved.crf_fields],
            "annexe1_html": res["annexe1_html"],
            "detection": res["detection"],
            "sync_report": res["sync_report"],
            "model_used": res["model_used"],
        }
    except ValueError as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        if tracked:
            finish_pipeline_task("error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        reset_ai_routing_policy()


@app.post("/api/projects/{project_id}/crf/sync")
def sync_project_crf(
    project_id: str,
    force_extract: bool = False,
    current_user: UserRecord = Depends(require_module_write("module3")),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    detection_out: Optional[Dict[str, object]] = None
    sync_report_out: Optional[Dict[str, object]] = None
    model_used_out = "deterministic-crf-compiler"

    if force_extract or not project.crf_fields:
        try:
            ext_res = extract_and_sync_crf_for_project(
                project,
                source_mode="auto",
                use_ai=bool(force_extract),
            )
            detection_out = ext_res.get("detection")
            sync_report_out = ext_res.get("sync_report")
            model_used_out = str(ext_res.get("model_used", model_used_out))
        except Exception:
            if force_extract or not project.crf_fields:
                project.crf_fields = extract_crf_fields_from_project(project)
            else:
                ensure_project_crf_fields(project)
            sync_crf_to_project(project, sync_annexe1_markdown=True)
    else:
        ensure_project_crf_fields(project)
        sync_crf_to_project(project, sync_annexe1_markdown=True)

    saved = project_storage.save(project)
    return {
        "project": saved.model_dump(mode="json"),
        "crf_fields": [f.model_dump(mode="json") for f in saved.crf_fields],
        "annexe1_html": render_crf_annexe1_html(saved),
        "detection": detection_out,
        "sync_report": sync_report_out,
        "model_used": model_used_out,
    }


@app.put("/api/projects/{project_id}/crf/schema")
def update_project_crf_schema(
    project_id: str,
    payload: UpdateCRFSchemaRequest,
    current_user: UserRecord = Depends(require_module_write("module3")),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    project.crf_fields = payload.crf_fields
    sync_crf_to_project(project, sync_annexe1_markdown=payload.sync_annexe1)
    saved = project_storage.save(project)
    return {
        "project": saved.model_dump(mode="json"),
        "crf_fields": [f.model_dump(mode="json") for f in saved.crf_fields],
        "annexe1_html": render_crf_annexe1_html(saved),
    }


@app.get("/api/projects/{project_id}/crf/preview-html")
def get_crf_preview_html(
    project_id: str,
    current_user: UserRecord = Depends(get_active_user),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if ensure_project_crf_fields(project):
        project = project_storage.save(project)
    return {
        "annexe1_html": render_crf_annexe1_html(project),
        "crf_fields": [f.model_dump(mode="json") for f in project.crf_fields],
    }


@app.post("/api/projects/{project_id}/patients")
def upsert_patient_record(
    project_id: str,
    payload: UpsertPatientRecordRequest,
    request: Request,
    current_user: UserRecord = Depends(require_module_write("module3")),
    db: Any = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    ensure_project_crf_fields(project)

    now = datetime.now(timezone.utc)
    existing_idx: Optional[int] = None
    if payload.record_id:
        for idx, rec in enumerate(project.patient_records):
            if rec.record_id == payload.record_id:
                existing_idx = idx
                break

    code = (payload.patient_code or "").strip()
    if not code:
        next_num = len(project.patient_records) + 1
        existing_codes = {r.patient_code for r in project.patient_records}
        while f"PAT-{next_num:03d}" in existing_codes:
            next_num += 1
        code = f"PAT-{next_num:03d}"

    if existing_idx is not None:
        rec = project.patient_records[existing_idx]
        rec.patient_code = code
        rec.inclusion_date = payload.inclusion_date
        rec.hospital_site = payload.hospital_site
        rec.investigator_name = payload.investigator_name
        rec.values = payload.values
        rec.notes = payload.notes
        rec.updated_at = now
    else:
        rec = PatientRecord(
            patient_code=code,
            inclusion_date=payload.inclusion_date,
            hospital_site=payload.hospital_site,
            investigator_name=payload.investigator_name,
            values=payload.values,
            notes=payload.notes,
            created_at=now,
            updated_at=now,
        )
        project.patient_records.append(rec)

    saved = project_storage.save(project)
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module3",
        action="patient_upsert",
        credits_cost=0,
        project_id=project_id,
        ip_address=get_client_ip(request),
    )
    return {
        "project": saved.model_dump(mode="json"),
        "record": rec.model_dump(mode="json"),
    }


@app.delete("/api/projects/{project_id}/patients/{record_id}", response_model=ProtocolProject)
def delete_patient_record(
    project_id: str,
    record_id: str,
    current_user: UserRecord = Depends(require_module_write("module3")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    before = len(project.patient_records)
    project.patient_records = [r for r in project.patient_records if r.record_id != record_id]
    if len(project.patient_records) == before:
        raise HTTPException(status_code=404, detail="Fiche patient introuvable.")
    return project_storage.save(project)


@app.get("/api/projects/{project_id}/patients/export.csv")
def export_patients_csv(
    project_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user_allow_query_token),
    db: Any = Depends(get_db),
) -> Response:
    project = get_authorized_project(project_id, current_user)
    csv_text = export_patient_records_csv(project)
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module3",
        action="export_csv",
        credits_cost=0,
        project_id=project_id,
        ip_address=get_client_ip(request),
    )
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="base_patients_{project.project_id}.csv"'
        },
    )


@app.get("/api/projects/{project_id}/working-memory")
def get_working_memory(
    project_id: str,
    current_user: UserRecord = Depends(get_active_user),
) -> Dict[str, Any]:
    project = get_authorized_project(project_id, current_user)
    memory_map = load_working_memory_map(project)
    fpath = get_working_memory_path(project_id)
    return {
        "project_id": project_id,
        "file_path": str(fpath),
        "sections_count": len(memory_map),
        "memory_map": memory_map,
        "markdown": fpath.read_text(encoding="utf-8") if fpath.exists() else "",
    }


@app.post("/api/projects/{project_id}/working-memory/refresh")
def refresh_working_memory(
    project_id: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> Dict[str, Any]:
    project = get_authorized_project(project_id, current_user)
    return refresh_all_working_memory(project)


class AntigravityAgentRequest(BaseModel):
    prompt: str = "Confirme ton statut opérationnel pour SmartResearch FMSB."
    agent_model: str = "antigravity-preview-latest"


@app.post("/api/antigravity/test")
def test_antigravity_agent(
    payload: Optional[AntigravityAgentRequest] = None,
    _admin: UserRecord = Depends(require_admin),
) -> Dict[str, Any]:
    prompt = payload.prompt if payload else "Confirme ton statut opérationnel pour SmartResearch FMSB."
    model = payload.agent_model if payload else "antigravity-preview-latest"
    return gemini_router.run_antigravity_agent(prompt=prompt, agent_model=model)


# =========================================================================
# MODULE 4: PRESENTATION POWERPOINT (CAMES / FMSB)
# =========================================================================
presentation_orchestrator = PresentationOrchestrator()


class GenerateSlideRequest(BaseModel):
    instruction: str = ""
    layout_mode: Optional[str] = None


def _load_valid_deck(
    project_id: str,
    current_user: UserRecord,
) -> Tuple[ProtocolProject, PresentationDeck]:
    project = get_authorized_project(project_id, current_user)
    cached = project_storage.get_presentation(project_id)
    deck, changed = presentation_orchestrator.ensure_fresh_valid_deck(project, cached)
    if changed:
        project_storage.save_presentation(deck)
    return project, deck


@app.get("/api/projects/{project_id}/presentation")
def get_presentation(
    project_id: str,
    current_user: UserRecord = Depends(get_active_user),
) -> PresentationDeck:
    """Retrieve, auto-migrate if needed, and return the presentation deck with Welcome first."""
    _, deck = _load_valid_deck(project_id, current_user)
    return deck


@app.put("/api/projects/{project_id}/presentation")
def update_presentation(
    project_id: str,
    payload: PresentationDeck,
    current_user: UserRecord = Depends(require_module_write("module4")),
) -> PresentationDeck:
    """Save modified slides, speaker notes, layout modes, enforce opening order and recompute geometry."""
    project = get_authorized_project(project_id, current_user)
    payload.project_id = project_id
    presentation_orchestrator.enforce_opening_order(payload)
    compute_deck_layout(payload, project=project)
    project_storage.save_presentation(payload)
    return payload


@app.post("/api/projects/{project_id}/presentation/reset-from-protocol")
def reset_presentation_from_protocol(
    project_id: str,
    current_user: UserRecord = Depends(require_module_write("module4")),
) -> PresentationDeck:
    """Rebuild the entire presentation deck directly from real protocol data."""
    project = get_authorized_project(project_id, current_user)
    deck = presentation_orchestrator.build_default_deck_from_protocol(project)
    project_storage.save_presentation(deck)
    return deck


@app.post("/api/projects/{project_id}/presentation/generate-all")
def generate_full_presentation_ai(
    project_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    """Orchestrate 3 Gemini roles (3.5 Flash Lite drafting, 3.1 Flash Lite audit, Decision 1 layout)."""
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module4", action="pptx_generate_all")
    deck = presentation_orchestrator.generate_full_deck_with_ai(project)
    project_storage.save_presentation(deck)
    models_str = ",".join(str(v) for v in (deck.models_used or {}).values() if v)[:120]
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module4",
        action="pptx_generate_all",
        credits_cost=cost,
        project_id=project_id,
        model_used=models_str,
        ip_address=get_client_ip(request),
    )
    return {
        "deck": deck,
        "models_used": deck.models_used,
        "decision_notes": deck.decision_notes,
    }


@app.post("/api/projects/{project_id}/presentation/generate-slide/{slide_index}")
def ai_generate_slide(
    project_id: str,
    slide_index: int,
    request: Request,
    payload: Optional[GenerateSlideRequest] = None,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    """Regenerate one slide via Gemini 3.5 Flash Lite + Gemini 3.1 Flash Lite with optional instruction."""
    project, deck = _load_valid_deck(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module4", action="pptx_generate_slide")

    instruction = payload.instruction if payload else ""
    preferred_layout = payload.layout_mode if payload else None
    try:
        result = presentation_orchestrator.generate_slide_ai_summary(
            project=project,
            deck=deck,
            slide_index=slide_index,
            user_instruction=instruction,
            preferred_layout=preferred_layout,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    project_storage.save_presentation(result["deck"])
    if result.get("status") == "ok":
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key="module4",
            action="pptx_generate_slide",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(result.get("model_used", "")),
            ip_address=get_client_ip(request),
        )
    return result


@app.post("/api/projects/{project_id}/presentation/audit", response_model=PresentationAuditReport)
def audit_presentation(
    project_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> PresentationAuditReport:
    """Audit slide readability (>=24pt, <=6 lines), oral duration (10-15 min), and protocol fidelity."""
    project, deck = _load_valid_deck(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module4", action="pptx_audit")
    report = presentation_orchestrator.audit_presentation_deck(project, deck)
    project_storage.save_presentation(deck)
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module4",
        action="pptx_audit",
        credits_cost=cost,
        project_id=project_id,
        model_used=str(report.model_used or ""),
        ip_address=get_client_ip(request),
    )
    return report


@app.get("/api/projects/{project_id}/presentation/export/pptx")
@app.post("/api/projects/{project_id}/presentation/export/pptx")
def export_presentation_pptx(
    project_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user_allow_query_token),
    db: Any = Depends(get_db),
) -> FileResponse:
    """Compile and download the native Microsoft PowerPoint (.pptx) presentation."""
    project, deck = _load_valid_deck(project_id, current_user)

    out_pptx = settings.storage_dir / "exports" / f"{project.project_id}_soutenance.pptx"
    compile_presentation_pptx(deck, out_pptx, project=project)
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module4",
        action="export_pptx",
        credits_cost=0,
        project_id=project_id,
        ip_address=get_client_ip(request),
    )

    return FileResponse(
        path=str(out_pptx),
        filename=f"Soutenance_Protocole_{project.project_id}.pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@app.get("/api/projects/{project_id}/presentation/export/pdf")
@app.post("/api/projects/{project_id}/presentation/export/pdf")
def export_presentation_pdf(
    project_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_active_user_allow_query_token),
    db: Any = Depends(get_db),
) -> FileResponse:
    """Compile the presentation to .pptx and render PDF via Microsoft PowerPoint COM."""
    project, deck = _load_valid_deck(project_id, current_user)

    out_pptx = settings.storage_dir / "exports" / f"{project.project_id}_soutenance.pptx"
    out_pdf = settings.storage_dir / "exports" / f"{project.project_id}_soutenance_slides.pdf"
    compile_presentation_pptx(deck, out_pptx, project=project)
    try:
        export_pptx_to_pdf(out_pptx, out_pdf)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module4",
        action="export_pptx_pdf",
        credits_cost=0,
        project_id=project_id,
        ip_address=get_client_ip(request),
    )

    return FileResponse(
        path=str(out_pdf),
        filename=f"Soutenance_Protocole_{project.project_id}.pdf",
        media_type="application/pdf",
    )


@app.post("/api/projects/{project_id}/presentation/render-previews")
def render_presentation_previews(
    project_id: str,
    current_user: UserRecord = Depends(get_active_user),
) -> Dict[str, Any]:
    """Compile .pptx and export genuine Microsoft PowerPoint PNG slide previews (1280x720)."""
    project, deck = _load_valid_deck(project_id, current_user)

    out_pptx = settings.storage_dir / "exports" / f"{project.project_id}_soutenance.pptx"
    previews_dir = settings.storage_dir / "exports" / f"{project.project_id}_previews"
    compile_presentation_pptx(deck, out_pptx, project=project)
    try:
        pngs = render_pptx_previews(out_pptx, previews_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    ts = int(datetime.now(timezone.utc).timestamp())
    return {
        "count": len(pngs),
        "preview_urls": [
            f"/api/projects/{project_id}/presentation/previews/{i}?v={ts}"
            for i in range(1, len(pngs) + 1)
        ],
    }


@app.get("/api/projects/{project_id}/presentation/previews/{slide_num}")
def get_presentation_preview_png(
    project_id: str,
    slide_num: int,
    current_user: UserRecord = Depends(get_active_user_allow_query_token),
) -> FileResponse:
    """Serve a rendered PowerPoint PNG preview image for a specific slide index."""
    get_authorized_project(project_id, current_user)
    png_path = settings.storage_dir / "exports" / f"{project_id}_previews" / f"slide_{slide_num:02d}.png"
    if not png_path.exists():
        raise HTTPException(status_code=404, detail="Aperçu PNG non encore généré.")
    return FileResponse(
        path=str(png_path),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/presentation/templates")
def list_presentation_templates() -> Dict[str, Any]:
    """Return available FMSB/CAMES slide graphic templates."""
    return {"templates": SLIDE_TEMPLATE_CATALOG}


class DirectiveCreateRequest(BaseModel):
    text: str
    source: str = "manual"


class DirectiveUpdateRequest(BaseModel):
    text: Optional[str] = None
    active: Optional[bool] = None


class CopilotChatRequest(BaseModel):
    message: str
    active_module: str = "module1"
    active_section_id: Optional[str] = None
    mode: str = "auto"


class CopilotApplyProposalRequest(BaseModel):
    section_id: str
    proposed_markdown: str
    message_id: Optional[str] = None
    patches: Optional[List[Dict[str, Any]]] = None


@app.get("/api/projects/{project_id}/sections/{section_id}/versions")
def list_section_versions(
    project_id: str,
    section_id: str,
    current_user: UserRecord = Depends(get_active_user),
) -> Dict[str, Any]:
    project = get_authorized_project(project_id, current_user)
    sec = project.sections.get(section_id)
    if sec is None:
        raise HTTPException(status_code=404, detail="Section inconnue dans le canevas.")
    return {
        "section_id": section_id,
        "versions": [v.model_dump(mode="json") for v in reversed(sec.versions or [])],
    }


@app.post(
    "/api/projects/{project_id}/sections/{section_id}/versions/{version_id}/restore",
    response_model=ProtocolProject,
)
def restore_section_version_endpoint(
    project_id: str,
    section_id: str,
    version_id: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    try:
        restore_section_version(project, section_id, version_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if section_id != "references":
        renumber_and_sync_vancouver_references(project)
    from app.services.working_memory import update_section_working_memory

    update_section_working_memory(project, section_id)
    saved = project_storage.save(project)
    return scope_project_copilot_for_user(saved, current_user.id)


@app.get("/api/projects/{project_id}/directives")
def list_project_directives(
    project_id: str,
    current_user: UserRecord = Depends(get_active_user),
) -> Dict[str, Any]:
    project = get_authorized_project(project_id, current_user)
    return {
        "directives": [d.model_dump(mode="json") for d in (project.supervisor_directives or [])],
    }


@app.post("/api/projects/{project_id}/directives", response_model=ProtocolProject)
def add_project_directive(
    project_id: str,
    payload: DirectiveCreateRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    clean_text = (payload.text or "").strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="Le texte de la directive ne peut pas être vide.")
    project = get_authorized_project(project_id, current_user)
    project.supervisor_directives.append(
        SupervisorDirectiveItem(
            text=clean_text,
            source=(payload.source or "manual").strip() or "manual",
            active=True,
        )
    )
    project.updated_at = datetime.now(timezone.utc)
    saved = project_storage.save(project)
    return scope_project_copilot_for_user(saved, current_user.id)


@app.post("/api/projects/{project_id}/directives/upload-media")
async def upload_supervisor_media_directives(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    section_id: Optional[str] = Form(default=None),
    user_note: str = Form(default=""),
    current_user: UserRecord = Depends(require_module_write("module1")),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    """Analyze a supervisor's video or audio clip (via `supervisor_media` chain, default gemini-3.5-flash-lite)
    and save extracted directives into `project.supervisor_directives`."""
    project = get_authorized_project(project_id, current_user)
    raw_bytes = await file.read()
    cost = assert_ai_quota_available(
        db, current_user, module_key="module1", action="supervisor_media_directives"
    )
    try:
        result = extract_directives_from_media(
            project=project,
            file_bytes=raw_bytes,
            filename=file.filename or "directive_media.mp4",
            content_type=file.content_type,
            section_id=(section_id or "").strip() or None,
            user_note=user_note or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    project.updated_at = datetime.now(timezone.utc)
    saved = project_storage.save(project)
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module1",
        action="supervisor_media_directives",
        credits_cost=cost,
        project_id=project_id,
        model_used=str(result.get("model_used") or ""),
        ip_address=get_client_ip(request),
    )
    return {
        "project": scope_project_copilot_for_user(saved, current_user.id).model_dump(mode="json"),
        "extraction": result,
    }


@app.patch("/api/projects/{project_id}/directives/{directive_id}", response_model=ProtocolProject)
def update_project_directive(
    project_id: str,
    directive_id: str,
    payload: DirectiveUpdateRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    target = next(
        (d for d in project.supervisor_directives if d.directive_id == directive_id),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Directive introuvable.")
    if payload.text is not None:
        clean_text = payload.text.strip()
        if not clean_text:
            raise HTTPException(status_code=400, detail="Le texte de la directive ne peut pas être vide.")
        target.text = clean_text
    if payload.active is not None:
        target.active = bool(payload.active)
    project.updated_at = datetime.now(timezone.utc)
    saved = project_storage.save(project)
    return scope_project_copilot_for_user(saved, current_user.id)


@app.delete("/api/projects/{project_id}/directives/{directive_id}", response_model=ProtocolProject)
def delete_project_directive(
    project_id: str,
    directive_id: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    before = len(project.supervisor_directives)
    project.supervisor_directives = [
        d for d in project.supervisor_directives if d.directive_id != directive_id
    ]
    if len(project.supervisor_directives) == before:
        raise HTTPException(status_code=404, detail="Directive introuvable.")
    project.updated_at = datetime.now(timezone.utc)
    saved = project_storage.save(project)
    return scope_project_copilot_for_user(saved, current_user.id)


@app.get("/api/projects/{project_id}/copilot/status")
def get_copilot_status(
    project_id: str,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    from app.db.datastore import as_store

    project = get_authorized_project(project_id, current_user)
    chain = resolve_user_model_chain(as_store(db), current_user)
    if chain.get("has_copilot_text_chain"):
        text_models = list(chain.get("copilot_text_models") or [])
    elif chain.get("has_main_chain"):
        text_models = list(chain.get("eligible_models") or [])
    else:
        text_models = [
            "gemini-3.8-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ]
    voice_models = (
        list(chain.get("copilot_voice_models") or [])
        if chain.get("copilot_voice_models")
        else ["gemini-3.8-live", "gemini-2.5-flash-native-audio-latest"]
    )
    return {
        "text_models": text_models,
        "voice_models": voice_models,
        "directives_count": sum(1 for d in project.supervisor_directives if d.active),
        "messages": [
            m.model_dump(mode="json")
            for m in get_user_copilot_messages(project, current_user.id)
        ],
    }


@app.post("/api/projects/{project_id}/copilot/chat")
def copilot_chat_endpoint(
    project_id: str,
    payload: CopilotChatRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    project = get_authorized_project(project_id, current_user)
    if not (payload.message or "").strip():
        raise HTTPException(status_code=400, detail="Le message ne peut pas être vide.")

    cost = assert_ai_quota_available(
        db,
        current_user,
        module_key="module1",
        action="copilot_chat",
        credits_cost=1,
    )
    res = run_copilot_chat(
        project,
        message=payload.message,
        active_module=payload.active_module,
        active_section_id=payload.active_section_id,
        mode=payload.mode,
        current_user_id=current_user.id,
    )
    saved = project_storage.save(project)
    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module1",
        action="copilot_chat",
        credits_cost=cost,
        project_id=project_id,
        model_used=str(res.get("model_used") or ""),
        ip_address=get_client_ip(request),
    )
    return {
        "assistant_message": res["assistant_message"].model_dump(mode="json"),
        "project": scope_project_copilot_for_user(saved, current_user.id).model_dump(
            mode="json"
        ),
        "model_used": res["model_used"],
        "active_text_models": res["active_text_models"],
        "active_voice_models": res["active_voice_models"],
    }


@app.post("/api/projects/{project_id}/copilot/apply-proposal", response_model=ProtocolProject)
def apply_copilot_proposal_endpoint(
    project_id: str,
    payload: CopilotApplyProposalRequest,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    if payload.section_id == "cover_page":
        assert_cover_admin_only(current_user)
    project = get_authorized_project(project_id, current_user)
    sec = project.sections.get(payload.section_id)
    if sec is None:
        raise HTTPException(status_code=404, detail="Section introuvable.")
    clean_md = (payload.proposed_markdown or "").strip()
    patches_list = list(payload.patches or [])
    if not clean_md and not patches_list:
        raise HTTPException(status_code=400, detail="La proposition ne peut pas être vide.")

    snapshot_section_version(
        sec,
        source="copilot",
        note="Avant application proposition Assistant Smart Research",
    )
    final_md = clean_md
    if patches_list:
        patch_res = apply_surgical_text_patches(sec.content_markdown or "", patches_list)
        if patch_res["applied_count"] > 0:
            final_md = patch_res["patched_markdown"]

    sec.content_markdown = final_md
    sec.content_html = markdown_to_clean_html(final_md)
    now = datetime.now(timezone.utc)
    sec.updated_at = now
    if payload.message_id:
        allowed_msg_ids = {
            m.message_id for m in get_user_copilot_messages(project, current_user.id)
        }
        for msg in project.copilot_messages:
            if (
                msg.message_id in allowed_msg_ids
                and msg.message_id == payload.message_id
                and msg.proposal is not None
            ):
                msg.proposal.applied = True
    project.updated_at = now
    if payload.section_id != "references":
        renumber_and_sync_vancouver_references(project)
    from app.services.working_memory import update_section_working_memory

    update_section_working_memory(project, payload.section_id)
    saved = project_storage.save(project)
    return scope_project_copilot_for_user(saved, current_user.id)


class CopilotExecuteActionRequest(BaseModel):
    action_type: str
    params: Dict[str, Any] = {}
    message_id: Optional[str] = None


@app.post("/api/projects/{project_id}/copilot/execute-action", response_model=ProtocolProject)
def execute_copilot_action_endpoint(
    project_id: str,
    payload: CopilotExecuteActionRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> ProtocolProject:
    action_type = (payload.action_type or "").strip()
    if action_type not in VALID_COPILOT_ACTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Action applicative inconnue ou non prise en charge : {action_type!r}",
        )

    target_module = ACTION_MODULE_MAP[action_type]
    assert_module_write_allowed(db, current_user, target_module)
    project = get_authorized_project(project_id, current_user)

    clean_params = dict(payload.params or {})
    if action_type == "update_cover" or (
        str(clean_params.get("section_id") or "").strip() == "cover_page"
        and action_type
        in (
            "waive_audit_and_validate",
            "fix_section_issues",
            "humanize_section",
            "draft_section",
            "generate_section_table",
            "upsert_section_table",
            "generate_section_diagram",
        )
    ):
        assert_cover_admin_only(current_user)

    cost = 0
    if action_type in AI_ACTION_CREDIT_COSTS:
        cost = assert_ai_quota_available(
            db,
            current_user,
            module_key=target_module,
            action=f"copilot_{action_type}",
            credits_cost=AI_ACTION_CREDIT_COSTS[action_type],
        )

    try:
        res = execute_copilot_app_action(
            project,
            action_type=action_type,
            params=clean_params,
            current_user=current_user,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "COVER_ADMIN_ONLY", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.message_id:
        allowed_msg_ids = {
            m.message_id for m in get_user_copilot_messages(project, current_user.id)
        }
        for msg in project.copilot_messages:
            if (
                msg.message_id in allowed_msg_ids
                and msg.message_id == payload.message_id
                and msg.app_action is not None
            ):
                msg.app_action.applied = True
                msg.app_action.result_summary = str(res.get("summary") or "")

    saved = project_storage.save(project)
    if action_type in AI_ACTION_CREDIT_COSTS:
        record_usage_and_debit(
            db=db,
            user=current_user,
            module_key=target_module,
            action=f"copilot_{action_type}",
            credits_cost=cost,
            project_id=project_id,
            model_used=str(res.get("model_used") or ""),
            ip_address=get_client_ip(request),
        )
    return scope_project_copilot_for_user(saved, current_user.id)


@app.delete("/api/projects/{project_id}/copilot/messages", response_model=ProtocolProject)
def clear_copilot_messages_endpoint(
    project_id: str,
    current_user: UserRecord = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    clear_user_copilot_messages(project, current_user.id)
    project.updated_at = datetime.now(timezone.utc)
    saved = project_storage.save(project)
    return scope_project_copilot_for_user(saved, current_user.id)


class CopilotCompareMergeRequest(BaseModel):
    section_id: str
    instruction: str = ""


@app.post("/api/projects/{project_id}/copilot/compare-merge")
def copilot_compare_merge_endpoint(
    project_id: str,
    payload: CopilotCompareMergeRequest,
    request: Request,
    current_user: UserRecord = Depends(get_active_user),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    project = get_authorized_project(project_id, current_user)
    if payload.section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section introuvable.")

    cost = assert_ai_quota_available(
        db,
        current_user,
        module_key="module1",
        action="copilot_chat",
        credits_cost=2,
    )
    t_res = tool_draft_compare_and_merge(
        project,
        section_id=payload.section_id,
        instruction=payload.instruction,
    )
    from app.schemas.protocol import CopilotMessage, CopilotProposal

    lang = normalize_lang(getattr(project, "language", "fr"))
    reply_txt = (
        f"Background comparison completed ({t_res['original_word_count']} -> {t_res['merged_word_count']} words). Review and apply if you approve.\n\n{t_res['summary']}"
        if lang == "en"
        else f"Comparaison & fusion effectuée en arrière-plan ({t_res['original_word_count']} → {t_res['merged_word_count']} mots) sans toucher votre texte actuel.\n\n{t_res['summary']}"
    )
    user_msg = CopilotMessage(
        role="user",
        content=(
            payload.instruction.strip()
            or (
                "Compare background generation with my current text and keep the best"
                if lang == "en"
                else "Comparer une nouvelle génération en coulisses avec mon texte actuel et garder le meilleur"
            )
        ),
        intent="edit",
        active_module="module1",
        active_section_id=payload.section_id,
    )
    assistant_msg = CopilotMessage(
        role="assistant",
        content=reply_txt,
        intent="propose_edit",
        model_used=str(t_res.get("model_used") or ""),
        active_module="module1",
        active_section_id=payload.section_id,
        proposal=CopilotProposal(
            section_id=payload.section_id,
            proposed_markdown=t_res["proposed_markdown"],
            summary=t_res["summary"],
            applied=False,
        ),
        tools_used=["draft_compare_and_merge"],
    )
    append_scoped_copilot_messages(
        project,
        [user_msg, assistant_msg],
        current_user.id,
        max_per_user=40,
    )
    project.updated_at = datetime.now(timezone.utc)
    saved = project_storage.save(project)

    record_usage_and_debit(
        db=db,
        user=current_user,
        module_key="module1",
        action="copilot_chat",
        credits_cost=cost,
        project_id=project_id,
        model_used=str(t_res.get("model_used") or ""),
        ip_address=get_client_ip(request),
    )
    chain = resolve_user_model_chain(as_store(db), current_user) if False else None
    _ = chain
    text_models, _ = gemini_router._resolve_candidates("copilot_text")
    voice_models = gemini_router.resolve_copilot_voice_models()
    return {
        "assistant_message": assistant_msg.model_dump(mode="json"),
        "project": scope_project_copilot_for_user(saved, current_user.id).model_dump(
            mode="json"
        ),
        "model_used": str(t_res.get("model_used") or ""),
        "active_text_models": text_models,
        "active_voice_models": voice_models,
    }


@app.websocket("/api/projects/{project_id}/copilot/live")
async def copilot_live_websocket_endpoint(
    websocket: WebSocket,
    project_id: str,
    access_token: str = "",
    active_module: str = "module1",
    active_section_id: Optional[str] = None,
    db: Any = Depends(get_db),
) -> None:
    await websocket.accept()
    token = (access_token or "").strip()
    if not token:
        await websocket.send_json({"type": "error", "message": "Authentification requise."})
        await websocket.close(code=1008)
        return

    try:
        from app.services.auth_service import verify_session_token

        user = verify_session_token(db, token, expected_type="access")
        if user.must_change_password:
            await websocket.send_json({"type": "error", "message": "Changement de mot de passe requis."})
            await websocket.close(code=1008)
            return
        project = get_authorized_project(project_id, user)
        cost = assert_ai_quota_available(
            db,
            user,
            module_key="module1",
            action="copilot_chat",
            credits_cost=1,
        )
        record_usage_and_debit(
            db=db,
            user=user,
            module_key="module1",
            action="copilot_chat",
            credits_cost=cost,
            project_id=project_id,
            model_used="gemini-3.8-live",
            ip_address="",
        )
    except HTTPException as exc:
        detail_msg = (
            exc.detail.get("message")
            if isinstance(exc.detail, dict)
            else str(exc.detail or "Accès refusé.")
        )
        await websocket.send_json({"type": "error", "message": detail_msg})
        await websocket.close(code=1008)
        return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011)
        return

    await handle_copilot_live_websocket(
        websocket=websocket,
        project=project,
        user=user,
        db=db,
        active_module=active_module,
        active_section_id=active_section_id,
    )


app.include_router(admin_router)


# Serve user presentation image templates
TAMPLATES_DIR = WORKSPACE_DIR / "tamplates"
if TAMPLATES_DIR.exists():
    app.mount("/tamplates", StaticFiles(directory=str(TAMPLATES_DIR)), name="tamplates")

# Serve built frontend assets when present (single-server preview mode)
FRONTEND_DIST = WORKSPACE_DIR / "frontend" / "dist"
if FRONTEND_DIST.exists():
    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/{full_path:path}", include_in_schema=False)
    def serve_admin_spa(full_path: str = "") -> FileResponse:
        return FileResponse(str(FRONTEND_DIST / "index.html"))

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
