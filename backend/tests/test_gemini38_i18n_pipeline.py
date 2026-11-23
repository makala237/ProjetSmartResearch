from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings
from app.data.pptx_canvas import get_plan_axes
from app.db.database import SessionLocal, init_db
from app.db.models import (
    AdminAuditLogORM,
    ProjectORM,
    UsageEventORM,
    UserAIQuotaORM,
    UserLicenseGrantORM,
    UserModuleEntitlementORM,
    UserORM,
)
from app.main import app
from app.services.docx_template import compile_protocol_docx
from app.services.gemini_client import GeminiRouter, reset_ai_routing_policy
from app.services.licensing_service import assert_ai_quota_available, get_student_daily_38_usage
from app.services.pipeline_tracker import (
    emit_pipeline_step,
    finish_pipeline_task,
    get_pipeline_task_snapshot,
    reset_pipeline_context,
    start_pipeline_task,
    update_pipeline_model,
)
from app.services.presentation_orchestrator import PresentationOrchestrator
from app.services.storage import project_storage
from tests._auth_helper import login_as_admin


def _delete_user_and_related(db, uid: str) -> None:
    db.query(UsageEventORM).filter(UsageEventORM.user_id == uid).delete()
    db.query(AdminAuditLogORM).filter(
        (AdminAuditLogORM.target_user_id == uid) | (AdminAuditLogORM.admin_id == uid)
    ).delete()
    db.query(UserLicenseGrantORM).filter(UserLicenseGrantORM.user_id == uid).delete()
    db.query(UserModuleEntitlementORM).filter(UserModuleEntitlementORM.user_id == uid).delete()
    db.query(UserAIQuotaORM).filter(UserAIQuotaORM.user_id == uid).delete()
    for pr in db.query(ProjectORM).filter(ProjectORM.owner_user_id == uid).all():
        db.delete(pr)
    u = db.get(UserORM, uid)
    if u is not None:
        db.delete(u)


def test_gemini38_primary_and_student_daily_fallback() -> None:
    init_db()
    settings = get_settings()
    assert settings.gemini_primary_reasoning_model == "gemini-3.8-flash"
    assert settings.gemini_fallback_reasoning_model == "gemini-3.5-flash"

    chains = settings.model_chains()
    for role in ("research", "segmentation", "drafting", "audit"):
        chain = chains[role]
        assert chain[0] == "gemini-3.8-flash", f"{role} chain does not start with gemini-3.8-flash: {chain}"
        assert "gemini-3.5-flash" in chain, f"{role} chain missing gemini-3.5-flash fallback: {chain}"
        assert chain.index("gemini-3.8-flash") < chain.index("gemini-3.5-flash")

    reset_ai_routing_policy()
    router = GeminiRouter()
    default_candidates, default_fb = router._resolve_candidates("drafting")
    assert default_fb is False
    assert default_candidates[0] == "gemini-3.8-flash", f"Unexpected default candidates: {default_candidates}"

    suffix = uuid4().hex[:8]
    student_email = f"stu_38cap_{suffix}@smartresearch.test"
    other_student_email = f"stu_other_{suffix}@smartresearch.test"
    admin_client = TestClient(app)
    login_as_admin(admin_client)

    r_health = admin_client.get("/api/health")
    assert r_health.status_code == 200, r_health.text
    assert r_health.json()["model_chains"]["drafting"][0] == "gemini-3.8-flash"

    created_user_ids: list[str] = []
    created_project_ids: list[str] = []

    try:
        r_plans = admin_client.get("/api/admin/plans")
        assert r_plans.status_code == 200, r_plans.text
        plan_id = next((p["id"] for p in r_plans.json() if p.get("is_active")), None)

        r_stu = admin_client.post(
            "/api/admin/users",
            json={
                "email": student_email,
                "full_name": f"Étudiant Cap 3.8 {suffix}",
                "role": "student",
                "plan_id": plan_id,
                "initial_protocol_title": f"Protocole Test Cap 3.8 {suffix}",
            },
        )
        assert r_stu.status_code == 200, r_stu.text
        stu_payload = r_stu.json()
        stu_id = stu_payload["user"]["id"]
        created_user_ids.append(stu_id)
        if stu_payload.get("created_project_id"):
            created_project_ids.append(stu_payload["created_project_id"])

        r_rech = admin_client.post(
            f"/api/admin/users/{stu_id}/recharge-ai",
            json={"add_credits": 50, "daily_38_limit": 3, "notes": "Test cap 3.8"},
        )
        assert r_rech.status_code == 200, r_rech.text
        assert r_rech.json()["ai_quota"]["daily_38_limit"] == 3

        db = SessionLocal()
        try:
            ent = (
                db.query(UserModuleEntitlementORM)
                .filter(
                    UserModuleEntitlementORM.user_id == stu_id,
                    UserModuleEntitlementORM.module_key == "module1",
                )
                .first()
            )
            now = datetime.now(timezone.utc)
            if ent is None:
                db.add(
                    UserModuleEntitlementORM(
                        user_id=stu_id,
                        module_key="module1",
                        starts_at=now,
                        expires_at=now + timedelta(days=30),
                        updated_at=now,
                    )
                )
                db.commit()
        finally:
            db.close()

        stu_client = TestClient(app)
        r_login = stu_client.post(
            "/api/auth/login",
            json={"email": student_email, "password": stu_payload["temporary_password"]},
        )
        assert r_login.status_code == 200, r_login.text
        stu_client.headers["Authorization"] = f"Bearer {r_login.json()['access_token']}"
        r_stu_chg = stu_client.post(
            "/api/auth/change-password",
            json={"current_password": stu_payload["temporary_password"], "new_password": "StudentCap2026!"},
        )
        assert r_stu_chg.status_code == 200, r_stu_chg.text
        stu_client.headers["Authorization"] = f"Bearer {r_stu_chg.json()['access_token']}"

        r_me0 = stu_client.get("/api/auth/me")
        assert r_me0.status_code == 200
        q0 = r_me0.json()["ai_quota"]
        assert q0["daily_38_limit"] == 3
        assert q0["daily_38_used"] == 0
        assert q0["using_35_fallback"] is False
        assert q0["active_reasoning_model"] == "gemini-3.8-flash"

        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            for _ in range(3):
                db.add(
                    UsageEventORM(
                        user_id=stu_id,
                        project_id=stu_payload.get("created_project_id"),
                        module_key="module1",
                        action="draft_section",
                        model_used="gemini-3.8-flash",
                        credits_cost=1,
                        status="ok",
                        created_at=now,
                    )
                )
            db.commit()

            stu_orm = db.get(UserORM, stu_id)
            assert stu_orm is not None
            usage = get_student_daily_38_usage(db, stu_orm)
            assert usage["used_today"] == 3
            assert usage["fallback_active"] is True
            assert usage["active_reasoning_model"] == "gemini-3.5-flash"

            assert_ai_quota_available(db, stu_orm, module_key="module1", action="draft_section")
            capped_candidates, capped_fb = router._resolve_candidates("drafting")
            assert capped_fb is True
            assert all("3.8" not in m for m in capped_candidates), (
                f"Expected gemini-3.8* excluded when cap reached, got {capped_candidates}"
            )
            assert capped_candidates[0] == "gemini-3.5-flash"
        finally:
            reset_ai_routing_policy()
            db.close()

        r_me1 = stu_client.get("/api/auth/me")
        assert r_me1.status_code == 200
        q1 = r_me1.json()["ai_quota"]
        assert q1["daily_38_used"] == 3
        assert q1["daily_38_remaining"] == 0
        assert q1["using_35_fallback"] is True
        assert q1["active_reasoning_model"] == "gemini-3.5-flash"

        r_raise = admin_client.post(
            f"/api/admin/users/{stu_id}/recharge-ai",
            json={"add_credits": 0, "daily_38_limit": 10, "notes": "Raise daily 3.8 cap"},
        )
        assert r_raise.status_code == 200, r_raise.text
        q2 = r_raise.json()["ai_quota"]
        assert q2["daily_38_limit"] == 10
        assert q2["daily_38_remaining"] == 7
        assert q2["using_35_fallback"] is False
        assert q2["active_reasoning_model"] == "gemini-3.8-flash"

        r_adm_me = admin_client.get("/api/auth/me")
        assert r_adm_me.status_code == 200
        adm_q = r_adm_me.json()["ai_quota"]
        assert adm_q["using_35_fallback"] is False
        assert adm_q["active_reasoning_model"] == "gemini-3.8-flash"

        r_other = admin_client.post(
            "/api/admin/users",
            json={
                "email": other_student_email,
                "full_name": f"Autre Étudiant {suffix}",
                "role": "student",
                "plan_id": plan_id,
                "initial_protocol_title": f"Protocole Autre {suffix}",
            },
        )
        assert r_other.status_code == 200, r_other.text
        other_payload = r_other.json()
        other_id = other_payload["user"]["id"]
        created_user_ids.append(other_id)
        if other_payload.get("created_project_id"):
            created_project_ids.append(other_payload["created_project_id"])

        other_client = TestClient(app)
        r_other_login = other_client.post(
            "/api/auth/login",
            json={"email": other_student_email, "password": other_payload["temporary_password"]},
        )
        assert r_other_login.status_code == 200
        other_client.headers["Authorization"] = f"Bearer {r_other_login.json()['access_token']}"
        r_other_chg = other_client.post(
            "/api/auth/change-password",
            json={"current_password": other_payload["temporary_password"], "new_password": "OtherCap2026!"},
        )
        assert r_other_chg.status_code == 200, r_other_chg.text
        other_client.headers["Authorization"] = f"Bearer {r_other_chg.json()['access_token']}"

        tid = f"pipe_test_{uuid4().hex[:10]}"
        start_pipeline_task(
            task_id=tid,
            user_id=stu_id,
            project_id=stu_payload.get("created_project_id") or "",
            operation="draft",
            title_fr="Rédaction test",
            title_en="Test drafting",
            lang="en",
            initial_model="gemini-3.8-flash",
        )
        try:
            emit_pipeline_step(
                "context",
                "Analyse du contexte",
                "Analyzing clinical context",
                detail="Checking Module 1",
                status="running",
                progress_pct=35,
            )
            update_pipeline_model("gemini-3.5-flash", fallback_triggered=True, note="Cap reached")
            finish_pipeline_task("done", final_label_fr="Terminé", final_label_en="Done")
        finally:
            reset_pipeline_context()

        snap = get_pipeline_task_snapshot(tid, requesting_user_id=stu_id, is_admin=False)
        assert snap is not None
        assert snap["status"] == "done"
        assert snap["active_model"] == "smart-research-center"
        assert snap["fallback_triggered"] is False
        assert snap["progress_pct"] == 100
        assert len(snap["steps"]) >= 2

        snap_adm = get_pipeline_task_snapshot(tid, requesting_user_id=stu_id, is_admin=True)
        assert snap_adm is not None
        assert snap_adm["active_model"] == "gemini-3.5-flash"
        assert snap_adm["fallback_triggered"] is True

        r_own = stu_client.get(f"/api/pipeline/progress/{tid}")
        assert r_own.status_code == 200, r_own.text
        assert r_own.json()["task_id"] == tid
        assert r_own.json()["active_model"] == "smart-research-center"

        r_adm_p = admin_client.get(f"/api/pipeline/progress/{tid}")
        assert r_adm_p.status_code == 200, r_adm_p.text
        assert r_adm_p.json()["active_model"] == "gemini-3.5-flash"

        r_forbidden = other_client.get(f"/api/pipeline/progress/{tid}")
        assert r_forbidden.status_code == 403, r_forbidden.text

        r_missing = admin_client.get(f"/api/pipeline/progress/nonexistent_{suffix}")
        assert r_missing.status_code == 404

    finally:
        reset_ai_routing_policy()
        for pid in created_project_ids:
            try:
                project_storage.delete(pid)
            except Exception:
                pass
        db = SessionLocal()
        try:
            for uid in created_user_ids:
                _delete_user_and_related(db, uid)
            db.commit()
        finally:
            db.close()


def test_i18n_canvas_language_docx_pptx_and_live_pipeline(tmp_path: Path) -> None:
    admin_client = TestClient(app)
    login_as_admin(admin_client)

    r_en_canvas = admin_client.get("/api/canvas?lang=en")
    assert r_en_canvas.status_code == 200, r_en_canvas.text
    en_payload = r_en_canvas.json()
    assert en_payload["language"] == "en"
    en_sections = en_payload["sections"]
    assert len(en_sections) == 34
    chap1_items = [s for s in en_sections if s.get("chapter_id") == "chapitre_1"]
    assert chap1_items and chap1_items[0]["chapter_title"] == "CHAPTER I: INTRODUCTION"

    r_fr_canvas = admin_client.get("/api/canvas?lang=fr")
    assert r_fr_canvas.status_code == 200
    assert r_fr_canvas.json()["language"] == "fr"
    assert r_fr_canvas.json()["sections"][0]["chapter_title"] == "Pages liminaires"

    r_create = admin_client.post(
        "/api/projects",
        json={
            "name": f"I18n Protocol Test {uuid4().hex[:6]}",
            "entry_mode": "new_topic",
            "research_idea": "Factors associated with uncontrolled hypertension in adult clinics.",
            "study_type": "Cross-sectional analytical study",
            "language": "fr",
        },
    )
    assert r_create.status_code == 200, r_create.text
    pid = r_create.json()["project_id"]

    try:
        r_lang = admin_client.patch(f"/api/projects/{pid}/language", json={"language": "en"})
        assert r_lang.status_code == 200, r_lang.text
        assert r_lang.json()["language"] == "en"

        proj = project_storage.get(pid)
        assert proj is not None
        assert proj.language == "en"
        proj.cover.title = "Determinants of Uncontrolled Hypertension in Outpatient Care"
        proj.cover.author_name = "Dr Jane Doe"
        proj.cover.academic_year = "2026-2027"
        proj.sections["intro_contexte"].content_markdown = "Hypertension remains a leading cardiovascular risk."
        proj.sections["chap1_objectifs"].content_markdown = "To determine factors associated with poor BP control."
        project_storage.save(proj)

        out_docx = tmp_path / "protocol_en.docx"
        compile_protocol_docx(proj, out_docx)
        assert out_docx.exists() and out_docx.stat().st_size > 10_000

        with zipfile.ZipFile(out_docx, "r") as zf:
            xml = zf.read("word/document.xml").decode("utf-8")

        for expected in (
            "TABLE OF CONTENTS",
            "LIST OF TABLES",
            "LIST OF FIGURES",
            "CHAPTER I: INTRODUCTION",
            "CHAPTER III: METHODOLOGY",
            "CHAPTER V: APPENDICES",
            "Supervisor",
            "Academic Year 2026-2027",
        ):
            assert expected in xml, f"Expected English marker '{expected}' not found in compiled DOCX"

        orch = PresentationOrchestrator()
        deck = orch.build_default_deck_from_protocol(proj)
        assert len(deck.slides) >= 10
        all_slide_text = " ".join(
            [s.title or "" for s in deck.slides]
            + [el.content or "" for s in deck.slides for el in s.elements]
        )
        axes_en = get_plan_axes("en")
        assert axes_en[0] in all_slide_text, f"Expected English plan axis '{axes_en[0]}' in PPTX deck"
        assert "INTRODUCTION" in all_slide_text or "PROBLEM" in all_slide_text

        live_tid = f"live_toc_{uuid4().hex[:10]}"
        r_toc = admin_client.post(
            f"/api/projects/{pid}/generate-toc",
            headers={"X-Pipeline-Task-Id": live_tid},
        )
        assert r_toc.status_code == 200, r_toc.text

        r_prog = admin_client.get(f"/api/pipeline/progress/{live_tid}")
        assert r_prog.status_code == 200, r_prog.text
        prog = r_prog.json()
        assert prog["task_id"] == live_tid
        assert prog["status"] == "done"
        assert prog["progress_pct"] == 100
        assert len(prog["steps"]) >= 1
    finally:
        try:
            project_storage.delete(pid)
        except Exception:
            pass


def test_draft_section_with_ai_cross_memory_and_no_model_leak() -> None:
    from unittest.mock import patch
    from app.services.drafting import DraftSectionOutput, draft_section_with_ai

    admin_client = TestClient(app)
    login_as_admin(admin_client)
    r_create = admin_client.post(
        "/api/projects",
        json={
            "name": f"Draft Cross Memory Test {uuid4().hex[:6]}",
            "entry_mode": "new_topic",
            "research_idea": "Hypertension artérielle au Cameroun",
            "study_type": "Étude transversale",
            "language": "fr",
        },
    )
    assert r_create.status_code == 200, r_create.text
    pid = r_create.json()["project_id"]

    captured: dict[str, str] = {}

    def _fake_orchestrate(project, section_id: str, action: str, user_instruction=None, **kwargs):
        emit_pipeline_step(
            "memory",
            "Lecture de la mémoire transversale du protocole...",
            "Reading cross-section working memory...",
            progress_pct=15,
        )
        emit_pipeline_step(
            "decision_route",
            "Décision d'orchestration documentaire...",
            "Routing literature & figure needs...",
            progress_pct=40,
        )
        return {
            "orchestration_trace": {
                "section_id": section_id,
                "action": action,
                "initial_text_present": False,
                "decision_model": "gemini-decision-1",
                "added_references_count": 0,
            },
            "cross_section_memory": "MEMOIRE_TRANSVERSALE_VERIFIEE",
        }

    def _fake_generate(role: str, contents: str, schema, temperature: float = 0.2):
        captured["prompt"] = contents
        return {
            "model_used": "gemini-3.8-flash",
            "data": DraftSectionOutput(
                content_markdown="L'hypertension artérielle constitue un enjeu majeur de santé publique au Cameroun.",
                cited_order_indices=[],
                assistant_commentary="Brouillon généré avec mémoire transversale.",
                suggested_tables=[],
                suggested_diagrams=[],
            ),
        }

    tid = f"draft_leak_{uuid4().hex[:10]}"
    start_pipeline_task(
        task_id=tid,
        user_id="stu_1",
        project_id=pid,
        operation="draft",
        title_fr="Rédaction IA",
        title_en="AI Drafting",
        lang="fr",
        initial_model="gemini-3.8-flash",
    )
    try:
        proj = project_storage.get(pid)
        assert proj is not None
        with (
            patch("app.services.drafting.orchestrate_section_action", side_effect=_fake_orchestrate),
            patch("app.services.drafting.gemini_router.generate_structured", side_effect=_fake_generate),
            patch("app.services.drafting.update_section_working_memory", return_value={"updated": True}),
        ):
            res = draft_section_with_ai(proj, "intro_contexte", "Rédiger le contexte")
        finish_pipeline_task("done")
    finally:
        reset_pipeline_context()
        try:
            project_storage.delete(pid)
        except Exception:
            pass

    assert "MEMOIRE_TRANSVERSALE_VERIFIEE" in captured.get("prompt", "")
    assert "hypertension artérielle" in res["content_markdown"].lower()

    snap_stu = get_pipeline_task_snapshot(tid, requesting_user_id="stu_1", is_admin=False)
    assert snap_stu is not None
    assert snap_stu["active_model"] == "smart-research-center"
    assert snap_stu["fallback_triggered"] is False
    for st in snap_stu["steps"]:
        assert "gemini" not in st["label"].lower(), f"Leaked model name in label: {st}"
        assert "gemini" not in (st.get("detail") or "").lower(), f"Leaked model name in detail: {st}"


if __name__ == "__main__":
    import tempfile

    test_gemini38_primary_and_student_daily_fallback()
    print("[OK] test_gemini38_primary_and_student_daily_fallback")
    with tempfile.TemporaryDirectory() as td:
        test_i18n_canvas_language_docx_pptx_and_live_pipeline(Path(td))
    print("[OK] test_i18n_canvas_language_docx_pptx_and_live_pipeline")
    test_draft_section_with_ai_cross_memory_and_no_model_leak()
    print("[OK] test_draft_section_with_ai_cross_memory_and_no_model_leak")
    print("ALL GEMINI 3.8 + I18N + PIPELINE TRACKER TESTS PASSED")
