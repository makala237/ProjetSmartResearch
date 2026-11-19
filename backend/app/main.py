from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.admin_routes import router as admin_router
from app.api.deps import (
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
from app.db.database import engine, get_db, init_db
from app.db.models import UserORM
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserMeOut,
)
from app.services.auth_service import (
    authenticate_and_login,
    build_user_me_out,
    change_user_password,
    logout_user,
    refresh_session_tokens,
)
from app.services.licensing_service import (
    assert_ai_quota_available,
    record_usage_and_debit,
)
from app.schemas.protocol import (
    CoverMetadata,
    CreateProjectRequest,
    CRFFieldSpec,
    DiagramConnection,
    DiagramNode,
    ImageAsset,
    PatientRecord,
    ProtocolProject,
    SectionDiagram,
    SectionTable,
    StepStatus,
    UpdateCRFSchemaRequest,
    UpdateFormattingRequest,
    UpdateSectionRequest,
    UpdateStructuredDataRequest,
    UpsertPatientRecordRequest,
)
from app.services.annexes_generator import build_annexes_pack
from app.services.crf_builder import (
    ensure_project_crf_fields,
    export_patient_records_csv,
    extract_crf_fields_from_project,
    render_crf_annexe1_html,
    sync_crf_to_project,
)
from app.services.diagram_renderer import render_diagram_svg
from app.services.docx_template import compile_protocol_docx, ensure_master_template
from app.services.drafting import (
    audit_section_with_ai,
    draft_section_with_ai,
    fix_section_issues_with_ai,
    suggest_section_diagram_with_ai,
    suggest_section_table_with_ai,
)
from app.services.gemini_client import gemini_router
from app.services.global_audit import (
    run_global_protocol_audit,
    sync_structured_from_sections,
)
from app.services.humanizer import (
    humanize_entire_protocol,
    humanize_section_with_ai,
)
from app.services.importer import analyze_and_import_protocol
from app.services.list_builder import generate_all_lists_and_toc
from app.services.literature import run_research_pipeline
from app.services.media_transport import (
    clear_structured_table_for_section,
    materialize_image_asset,
    synchronize_module1_to_module2_tables,
    transport_and_repair_project_images,
)
from app.services.page_layout_engine import plan_document_pagination
from app.services.section_orchestrator import orchestrate_section_action
from app.services.working_memory import (
    get_working_memory_path,
    load_working_memory_map,
    refresh_all_working_memory,
    update_section_working_memory,
)
from app.data.pptx_canvas import SLIDE_TEMPLATE_CATALOG
from app.services.pdf_exporter import compile_protocol_pdf, update_docx_fields_via_word_com
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
from app.services.vancouver import build_and_validate_vancouver_references

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


class ResearchRequest(BaseModel):
    custom_query: Optional[str] = None
    max_per_source: int = 4
    include_web_grounding: bool = True


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
        "database_dialect": engine.dialect.name,
        "model_chains": settings.model_chains(),
    }


@app.post("/api/auth/login", response_model=TokenResponse)
def auth_login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    client_ip = request.client.host if request.client else ""
    user, access_tok, refresh_tok = authenticate_and_login(
        db=db,
        email=payload.email,
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
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserMeOut:
    return build_user_me_out(db, current_user)


@app.post("/api/auth/change-password", response_model=TokenResponse)
def auth_change_password(
    payload: ChangePasswordRequest,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    logout_user(db, current_user)
    return {"status": "ok"}


@app.get("/api/models")
def list_models(_current_user: UserORM = Depends(get_active_user)) -> Dict[str, object]:
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
def get_canvas() -> Dict[str, object]:
    return {
        "version": FMSB_CANVAS_VERSION,
        "total_steps": len(FMSB_SECTIONS),
        "sections": [s.model_dump() for s in FMSB_SECTIONS],
    }


@app.post("/api/translate-department")
def translate_department(
    payload: TranslateDepartmentRequest,
    request: Request,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    cost = assert_ai_quota_available(db, current_user, module_key="general", action="translate_department")
    res = gemini_router.translate_department_live(
        text=payload.text,
        source_lang=payload.source_lang,
        target_lang=payload.target_lang,
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
def list_projects(current_user: UserORM = Depends(get_active_user)) -> List[ProtocolProject]:
    return project_storage.list_for_user(current_user.id, current_user.role)


@app.post("/api/projects", response_model=ProtocolProject)
def create_project(
    payload: CreateProjectRequest,
    current_user: UserORM = Depends(require_any_active_license()),
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
    current_user: UserORM = Depends(get_active_user),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if ensure_project_crf_fields(project):
        project = project_storage.save(project)
    return project


@app.patch("/api/projects/{project_id}/cover", response_model=ProtocolProject)
def update_cover(
    project_id: str,
    cover: CoverMetadata,
    current_user: UserORM = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    enforce_topic_lock_on_cover_update(project, cover, current_user)
    project.cover = cover
    saved = project_storage.save(project)
    update_section_working_memory(saved, "cover_page")
    return saved


@app.patch("/api/projects/{project_id}/sections/{section_id}", response_model=ProtocolProject)
def update_section(
    project_id: str,
    section_id: str,
    payload: UpdateSectionRequest,
    current_user: UserORM = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue dans le canevas FMSB.")

    sec = project.sections[section_id]
    if payload.content_markdown is not None:
        sec.content_markdown = payload.content_markdown
        # If HTML wasn't explicitly sent, rebuild clean HTML so Module 2 never displays stale HTML tables
        if payload.content_html is None:
            sec.content_html = markdown_to_clean_html(payload.content_markdown)
    if payload.content_html is not None:
        sec.content_html = payload.content_html
        # If user removed a table in RichTextEditor HTML, strip orphaned pipe tables from markdown too
        if "<table" not in payload.content_html.lower() and re.search(r"(?m)^\s*\|[\s:\-|]+\|\s*$", sec.content_markdown or ""):
            sec.content_markdown = re.sub(r"(?m)(?:\n|^)(?:\s*\|[^\n]+\n?)+", "\n", sec.content_markdown).strip()
    if payload.status is not None:
        sec.status = payload.status
    if payload.cited_ref_ids is not None:
        sec.cited_ref_ids = payload.cited_ref_ids
    if payload.user_notes is not None:
        sec.user_notes = payload.user_notes

    _sync_module2_queue(project)
    saved = project_storage.save(project)
    if payload.content_markdown is not None or payload.content_html is not None:
        update_section_working_memory(saved, section_id)
    return saved


@app.patch("/api/projects/{project_id}/formatting", response_model=ProtocolProject)
def update_formatting(
    project_id: str,
    payload: UpdateFormattingRequest,
    current_user: UserORM = Depends(require_module_write("module2")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    project.formatting = payload.formatting
    return project_storage.save(project)


@app.patch("/api/projects/{project_id}/structured", response_model=ProtocolProject)
def update_structured_data(
    project_id: str,
    payload: UpdateStructuredDataRequest,
    current_user: UserORM = Depends(require_module_write("module2")),
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
        project.budget = payload.budget
        if payload.budget and "chap3_budget" in project.sections:
            project.sections["chap3_budget"].suppress_structured_table = False
    if payload.annexes_config is not None:
        project.annexes_config = payload.annexes_config
    _sync_module2_queue(project)
    return project_storage.save(project)


@app.get("/api/projects/{project_id}/annexes/preview")
def preview_annexes(
    project_id: str,
    current_user: UserORM = Depends(get_active_user),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    return {"annexes": build_annexes_pack(project)}


@app.delete("/api/projects/{project_id}")
def delete_project(
    project_id: str,
    _admin: UserORM = Depends(require_admin),
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="research")
    try:
        report = run_research_pipeline(
            project,
            custom_query=payload.custom_query,
            max_per_source=payload.max_per_source,
            include_web_grounding=payload.include_web_grounding,
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
        return {"project": project.model_dump(mode="json"), "report": report}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/segment")
def segment_project(
    project_id: str,
    payload: SegmentRequest,
    request: Request,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="segment")
    if not project.references:
        raise HTTPException(
            status_code=400,
            detail="Aucune référence trouvée dans le projet. Lancez d'abord la recherche documentaire.",
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
        return {"project": project.model_dump(mode="json"), "report": report}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.patch("/api/projects/{project_id}/segments/{segment_id}", response_model=ProtocolProject)
def update_segment(
    project_id: str,
    segment_id: str,
    payload: ToggleSegmentRequest,
    current_user: UserORM = Depends(require_module_write("module1")),
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
    current_user: UserORM = Depends(require_module_write("module1")),
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
    current_user: UserORM = Depends(require_module_write("module1")),
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
    current_user: UserORM = Depends(get_active_user_allow_query_token),
) -> FileResponse:
    project = get_authorized_project(project_id, current_user)
    for img in project.images:
        if img.asset_id == asset_id:
            if not img.local_path or not Path(img.local_path).exists():
                if materialize_image_asset(project, img):
                    project_storage.save(project)
            if img.local_path:
                p = Path(img.local_path)
                if p.exists():
                    return FileResponse(path=p)
    raise HTTPException(status_code=404, detail="Fichier image introuvable.")


@app.delete("/api/projects/{project_id}/images/{asset_id}", response_model=ProtocolProject)
def delete_image_asset(
    project_id: str,
    asset_id: str,
    current_user: UserORM = Depends(require_module_write("module1")),
) -> ProtocolProject:
    project = get_authorized_project(project_id, current_user)
    remaining = []
    found = False
    for img in project.images:
        if img.asset_id == asset_id:
            found = True
            if img.local_path:
                try:
                    Path(img.local_path).unlink(missing_ok=True)
                except Exception:
                    pass
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="draft_section")
    try:
        if section_id in ("toc", "abbreviations", "list_of_tables", "list_of_figures"):
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
        )
        if section_id == "references":
            ref_report = build_and_validate_vancouver_references(
                project, use_gemini_decision=True, mark_validated=False
            )
            sec_ref = project.sections.get("references")
            if sec_ref and ref_report.get("references_count", 0) > 0:
                result["content_markdown"] = sec_ref.content_markdown
                result["content_html"] = sec_ref.content_html
                result["model_used"] = ref_report.get("decision_model", result.get("model_used"))
                result["audit_notes"] = list(result.get("audit_notes") or []) + [
                    f"Validation Vancouver ICMJE : {ref_report['references_count']} références ({ref_report['decision_model']})."
                ]
        _sync_module2_queue(project)
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
        return {"project": project.model_dump(mode="json"), "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/sections/{section_id}/audit")
def audit_section(
    project_id: str,
    section_id: str,
    request: Request,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="audit_section")
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
        return {"project": project.model_dump(mode="json"), "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/sections/{section_id}/fix")
def fix_section(
    project_id: str,
    section_id: str,
    request: Request,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="fix_section")
    try:
        result = fix_section_issues_with_ai(project, section_id=section_id)
        _sync_module2_queue(project)
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
        return {"project": project.model_dump(mode="json"), "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class HumanizeRequest(BaseModel):
    use_ai: bool = True


@app.post("/api/projects/{project_id}/sections/{section_id}/humanize")
def humanize_section(
    project_id: str,
    section_id: str,
    request: Request,
    payload: Optional[HumanizeRequest] = None,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="humanize_section")
    try:
        use_ai = True if payload is None else bool(payload.use_ai)
        result = humanize_section_with_ai(project, section_id=section_id, use_ai=use_ai)
        _sync_module2_queue(project)
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
        return {"project": project.model_dump(mode="json"), "result": result}
    except Exception as exc:
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if section_id not in project.sections:
        raise HTTPException(status_code=404, detail="Section inconnue.")
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="antigravity_section")
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
        return {
            "project": project.model_dump(mode="json"),
            "orchestration_trace": orch["orchestration_trace"],
            "antigravity_report": orch["antigravity_report"],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/sections/{section_id}/suggest-table")
def suggest_section_table(
    project_id: str,
    section_id: str,
    payload: SuggestTableRequest,
    request: Request,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(require_module_write("module1")),
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


@app.delete("/api/projects/{project_id}/sections/{section_id}/tables/{table_id}", response_model=ProtocolProject)
def delete_section_table(
    project_id: str,
    section_id: str,
    table_id: str,
    current_user: UserORM = Depends(require_module_write("module1")),
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
    current_user: UserORM = Depends(require_module_write("module1")),
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(require_module_write("module1")),
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


@app.get("/api/projects/{project_id}/sections/{section_id}/diagrams/{diagram_id}/svg")
def get_section_diagram_svg(
    project_id: str,
    section_id: str,
    diagram_id: str,
    current_user: UserORM = Depends(get_active_user_allow_query_token),
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
    current_user: UserORM = Depends(require_module_write("module1")),
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module1", action="import_protocol")
    locked_snapshot = (
        project.name,
        project.cover.title,
        project.cover.author_name,
        project.cover.matricule,
    )
    content = await file.read()
    try:
        report = analyze_and_import_protocol(
            project,
            filename=file.filename or "protocole.docx",
            content_bytes=content,
        )
        if current_user.role != "admin" and project.topic_locked:
            project.name = locked_snapshot[0]
            if project.locked_title:
                project.cover.title = project.locked_title
            else:
                project.cover.title = locked_snapshot[1]
            if project.locked_author_name:
                project.cover.author_name = project.locked_author_name
            if project.locked_matricule:
                project.cover.matricule = project.locked_matricule
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
        return {"project": project.model_dump(mode="json"), "report": report}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/projects/{project_id}/global-audit")
def global_audit_protocol(
    project_id: str,
    request: Request,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module2", action="humanize_protocol")
    use_ai = True if payload is None else bool(payload.use_ai)
    report = humanize_entire_protocol(project, use_ai=use_ai)
    _sync_module2_queue(project)
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
    current_user: UserORM = Depends(require_module_write("module2")),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    report = sync_structured_from_sections(project)
    saved = project_storage.save(project)
    return {"project": saved.model_dump(mode="json"), "report": report}


@app.post("/api/projects/{project_id}/generate-toc")
@app.post("/api/projects/{project_id}/generate-lists")
def generate_project_toc(
    project_id: str,
    request: Request,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module2", action="generate_toc")
    res = generate_all_lists_and_toc(project, use_gemini_decision=True)
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
    return {
        "project": saved.model_dump(mode="json"),
        "entries_count": res["entries_count"],
        "abbreviations_count": res["abbreviations_count"],
        "tables_count": res["tables_count"],
        "figures_count": res["figures_count"],
        "entries": [e.model_dump(mode="json") for e in entries],
        "pagination_summary": pagination_summary,
    }


@app.post("/api/projects/{project_id}/validate-references")
def validate_project_references(
    project_id: str,
    request: Request,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    cost = assert_ai_quota_available(db, current_user, module_key="module2", action="validate_references")
    ref_report = build_and_validate_vancouver_references(
        project, use_gemini_decision=True, mark_validated=True
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
    return {
        "project": saved.model_dump(mode="json"),
        **ref_report,
    }


@app.post("/api/projects/{project_id}/export/docx")
def export_docx(
    project_id: str,
    request: Request,
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    project = get_authorized_project(project_id, current_user)
    out_path = settings.storage_dir / "exports" / f"{project.project_id}_protocole.docx"
    compile_protocol_docx(project, out_path)
    toc_depth = int(getattr(project.formatting, "toc_depth", 2) or 2)
    if update_docx_fields_via_word_com(out_path, toc_depth=toc_depth):
        sync_toc_from_exported_docx(project, out_path)
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    project = get_authorized_project(project_id, current_user)
    out_path = settings.storage_dir / "exports" / f"{project.project_id}_protocole.pdf"
    try:
        compile_protocol_pdf(project, out_path)
        sync_toc_from_exported_docx(project, out_path.with_suffix(".docx"))
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


@app.post("/api/projects/{project_id}/crf/sync")
def sync_project_crf(
    project_id: str,
    force_extract: bool = False,
    current_user: UserORM = Depends(require_module_write("module3")),
) -> Dict[str, object]:
    project = get_authorized_project(project_id, current_user)
    if force_extract or not project.crf_fields:
        project.crf_fields = extract_crf_fields_from_project(project)
    else:
        ensure_project_crf_fields(project)
    sync_crf_to_project(project, sync_annexe1_markdown=True)
    saved = project_storage.save(project)
    return {
        "project": saved.model_dump(mode="json"),
        "crf_fields": [f.model_dump(mode="json") for f in saved.crf_fields],
        "annexe1_html": render_crf_annexe1_html(saved),
    }


@app.put("/api/projects/{project_id}/crf/schema")
def update_project_crf_schema(
    project_id: str,
    payload: UpdateCRFSchemaRequest,
    current_user: UserORM = Depends(require_module_write("module3")),
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
    current_user: UserORM = Depends(get_active_user),
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
    current_user: UserORM = Depends(require_module_write("module3")),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(require_module_write("module3")),
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
    current_user: UserORM = Depends(get_active_user_allow_query_token),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(get_active_user),
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
    current_user: UserORM = Depends(require_module_write("module1")),
) -> Dict[str, Any]:
    project = get_authorized_project(project_id, current_user)
    return refresh_all_working_memory(project)


class AntigravityAgentRequest(BaseModel):
    prompt: str = "Confirme ton statut opérationnel pour SmartResearch FMSB."
    agent_model: str = "antigravity-preview-latest"


@app.post("/api/antigravity/test")
def test_antigravity_agent(
    payload: Optional[AntigravityAgentRequest] = None,
    _admin: UserORM = Depends(require_admin),
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
    current_user: UserORM,
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
    current_user: UserORM = Depends(get_active_user),
) -> PresentationDeck:
    """Retrieve, auto-migrate if needed, and return the presentation deck with Welcome first."""
    _, deck = _load_valid_deck(project_id, current_user)
    return deck


@app.put("/api/projects/{project_id}/presentation")
def update_presentation(
    project_id: str,
    payload: PresentationDeck,
    current_user: UserORM = Depends(require_module_write("module4")),
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
    current_user: UserORM = Depends(require_module_write("module4")),
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(get_active_user),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(get_active_user_allow_query_token),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(get_active_user_allow_query_token),
    db: Session = Depends(get_db),
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
    current_user: UserORM = Depends(get_active_user),
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
    current_user: UserORM = Depends(get_active_user_allow_query_token),
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
