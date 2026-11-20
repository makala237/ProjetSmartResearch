from pathlib import Path
import sys
from uuid import uuid4

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.db.database import SessionLocal, init_db
from app.db.models import AdminAuditLogORM, LicensePlanORM, ProjectORM, UserORM
from app.main import app
from app.services.auth_service import create_user_account
from app.services.licensing_service import apply_license_plan_to_user
from tests._auth_helper import login_as_admin


def test_saas_phase1_auth_isolation_and_topic_lock() -> None:
    init_db()
    settings = get_settings()

    suffix = uuid4().hex[:6]
    email_a = f"student_a_{suffix}@test.local"
    email_b = f"student_b_{suffix}@test.local"

    db = SessionLocal()
    try:
        admin_row = db.query(UserORM).filter(UserORM.role == "admin").first()
        starter_plan = (
            db.query(LicensePlanORM)
            .filter(LicensePlanORM.module1_days > 0, LicensePlanORM.module4_days > 0)
            .first()
        )
        assert admin_row is not None and starter_plan is not None

        user_a = create_user_account(
            db,
            email=email_a,
            full_name="Etudiant Test Alpha",
            password="TempPass123!",
            role="student",
            matricule="21M001",
            phone="690000001",
            must_change_password=True,
        )
        apply_license_plan_to_user(db, admin_row, user_a, starter_plan, notes="Phase 1 test grant")

        user_b = create_user_account(
            db,
            email=email_b,
            full_name="Etudiant Test Beta",
            password="PassBeta123!",
            role="student",
            matricule="21M002",
            phone="690000002",
            must_change_password=False,
        )
        apply_license_plan_to_user(db, admin_row, user_b, starter_plan, notes="Phase 1 test grant")
    finally:
        db.close()

    try:
        # 1. Anonymous protection across API endpoints
        anon = TestClient(app)
        for method, path in [
            ("GET", "/api/projects"),
            ("GET", "/api/projects/5221c9f3-65e"),
            ("GET", "/api/projects/5221c9f3-65e/presentation"),
            ("GET", "/api/projects/5221c9f3-65e/presentation/previews/1"),
            ("GET", "/api/projects/5221c9f3-65e/patients/export.csv"),
            ("GET", "/api/models"),
            ("POST", "/api/projects"),
        ]:
            resp = anon.request(method, path, json={"name": "Anon"} if method == "POST" else None)
            assert resp.status_code == 401, f"Expected 401 on anonymous {method} {path}, got {resp.status_code}"

        # 2. Legacy JSON projects migrated & accessible to admin
        admin_client = TestClient(app)
        admin_login = login_as_admin(admin_client)
        assert admin_login["user"]["role"] == "admin"

        r_admin_list = admin_client.get("/api/projects")
        assert r_admin_list.status_code == 200
        migrated_ids = {p["project_id"] for p in r_admin_list.json()}
        assert "5221c9f3-65e" in migrated_ids
        assert "113fa64e-056" in migrated_ids

        # Refresh & logout flow on admin client
        r_me = admin_client.get("/api/auth/me")
        assert r_me.status_code == 200 and r_me.json()["email"] == settings.bootstrap_admin_email

        r_ref = admin_client.post(
            "/api/auth/refresh",
            json={"refresh_token": admin_login["refresh_token"]},
        )
        assert r_ref.status_code == 200, r_ref.text
        admin_client.headers["Authorization"] = f"Bearer {r_ref.json()['access_token']}"

        r_logout = admin_client.post("/api/auth/logout")
        assert r_logout.status_code == 200
        assert admin_client.get("/api/projects").status_code == 401

        # 3. Forced first-login password change for Student A
        client_a1 = TestClient(app)
        r_la = client_a1.post("/api/auth/login", json={"email": email_a, "password": "TempPass123!"})
        assert r_la.status_code == 200, r_la.text
        assert r_la.json()["must_change_password"] is True
        client_a1.headers["Authorization"] = f"Bearer {r_la.json()['access_token']}"

        r_blocked = client_a1.get("/api/projects")
        assert r_blocked.status_code == 403
        assert r_blocked.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"

        r_chg = client_a1.post(
            "/api/auth/change-password",
            json={"current_password": "TempPass123!", "new_password": "NewPassAlpha123!"},
        )
        assert r_chg.status_code == 200, r_chg.text
        assert r_chg.json()["must_change_password"] is False
        client_a1.headers["Authorization"] = f"Bearer {r_chg.json()['access_token']}"
        assert client_a1.get("/api/projects").status_code == 200

        # 4. Strict single-session enforcement: second login revokes client_a1 immediately
        client_a2 = TestClient(app)
        r_la2 = client_a2.post("/api/auth/login", json={"email": email_a, "password": "NewPassAlpha123!"})
        assert r_la2.status_code == 200, r_la2.text
        client_a2.headers["Authorization"] = f"Bearer {r_la2.json()['access_token']}"

        r_revoked = client_a1.get("/api/projects")
        assert r_revoked.status_code == 401
        assert r_revoked.json()["detail"]["code"] == "SESSION_REVOKED"
        assert client_a2.get("/api/projects").status_code == 200

        db_check = SessionLocal()
        try:
            refreshed_a = db_check.get(UserORM, user_a.id)
            assert refreshed_a is not None and refreshed_a.session_switch_count >= 1
        finally:
            db_check.close()

        # 5. Multi-user isolation & immediate 1-user-1-protocol creation
        r_create_a = client_a2.post(
            "/api/projects",
            json={
                "name": "Protocole Étudiant A — Paludisme",
                "entry_mode": "new_topic",
                "research_idea": "Facteurs associés au paludisme grave",
                "study_type": "Étude transversale analytique",
            },
        )
        assert r_create_a.status_code == 200, r_create_a.text
        proj_a = r_create_a.json()
        proj_a_id = proj_a["project_id"]
        assert proj_a["owner_user_id"] == user_a.id
        assert proj_a["topic_locked"] is True
        assert proj_a["locked_title"] == "Protocole Étudiant A — Paludisme"
        assert proj_a["locked_author_name"] == "Etudiant Test Alpha"
        assert proj_a["locked_matricule"] == "21M001"

        client_b = TestClient(app)
        r_lb = client_b.post("/api/auth/login", json={"email": email_b, "password": "PassBeta123!"})
        assert r_lb.status_code == 200, r_lb.text
        client_b.headers["Authorization"] = f"Bearer {r_lb.json()['access_token']}"

        r_create_b = client_b.post(
            "/api/projects",
            json={
                "name": "Protocole Étudiant B — Diabète",
                "entry_mode": "new_topic",
                "research_idea": "Dépistage du diabète gestationnel",
                "study_type": "Étude transversale descriptive",
            },
        )
        assert r_create_b.status_code == 200, r_create_b.text
        proj_b = r_create_b.json()
        proj_b_id = proj_b["project_id"]
        assert proj_b["topic_locked"] is True

        list_a = client_a2.get("/api/projects").json()
        list_b = client_b.get("/api/projects").json()
        assert [p["project_id"] for p in list_a] == [proj_a_id]
        assert [p["project_id"] for p in list_b] == [proj_b_id]

        # Cross-user isolation checks (A -> B and A -> admin legacy project)
        for method, url, payload in [
            ("GET", f"/api/projects/{proj_b_id}", None),
            ("PATCH", f"/api/projects/{proj_b_id}/sections/intro_contexte", {"content_markdown": "Hack"}),
            ("GET", f"/api/projects/{proj_b_id}/presentation", None),
            ("GET", f"/api/projects/{proj_b_id}/patients/export.csv", None),
            ("POST", f"/api/projects/{proj_b_id}/export/docx", None),
            ("GET", "/api/projects/5221c9f3-65e", None),
        ]:
            resp = client_a2.request(method, url, json=payload)
            assert resp.status_code == 403, f"Expected 403 on cross-access {method} {url}, got {resp.status_code}"
            assert resp.json()["detail"]["code"] == "PROJECT_FORBIDDEN"

        # 6. Immediate 1-user-1-protocol limit & Topic Lock
        r_second = client_a2.post(
            "/api/projects",
            json={"name": "Deuxième Protocole Interdit", "entry_mode": "new_topic"},
        )
        assert r_second.status_code == 403
        assert r_second.json()["detail"]["code"] == "ONE_PROTOCOL_LIMIT"

        r_del_a = client_a2.delete(f"/api/projects/{proj_a_id}")
        assert r_del_a.status_code == 403
        assert r_del_a.json()["detail"]["code"] == "ADMIN_REQUIRED"

        # Cover page is strictly admin-only for ANY student edit
        cover_ok = dict(proj_a["cover"])
        for field_name, bad_val in [
            ("director", "Pr. Test Jury"),
            ("academic_year", "2026-2027"),
            ("title", "Sujet Détourné Pour Un Camarade"),
            ("author_name", "Autre Etudiant"),
            ("matricule", "99Z999"),
        ]:
            bad_cover = dict(cover_ok)
            bad_cover[field_name] = bad_val
            r_patch_bad = client_a2.patch(f"/api/projects/{proj_a_id}/cover", json=bad_cover)
            assert r_patch_bad.status_code == 403, f"Expected 403 changing {field_name}, got {r_patch_bad.status_code}"
            assert r_patch_bad.json()["detail"]["code"] == "COVER_ADMIN_ONLY"

        # Admin override on locked cover field
        admin_client2 = TestClient(app)
        login_as_admin(admin_client2)
        admin_cover = dict(cover_ok)
        admin_cover["title"] = "Protocole Corrigé par Admin"
        admin_cover["director"] = "Pr. Test Jury"
        r_admin_patch = admin_client2.patch(f"/api/projects/{proj_a_id}/cover", json=admin_cover)
        assert r_admin_patch.status_code == 200, r_admin_patch.text
        assert r_admin_patch.json()["locked_title"] == "Protocole Corrigé par Admin"
        assert r_admin_patch.json()["cover"]["title"] == "Protocole Corrigé par Admin"
        assert r_admin_patch.json()["cover"]["director"] == "Pr. Test Jury"
    finally:
        # Clean up test users, audit logs & cascaded projects from DB
        cleanup_db = SessionLocal()
        try:
            for uid in (user_a.id, user_b.id):
                cleanup_db.query(AdminAuditLogORM).filter(AdminAuditLogORM.target_user_id == uid).delete()
                orphan_projs = cleanup_db.query(ProjectORM).filter(ProjectORM.owner_user_id == uid).all()
                for op in orphan_projs:
                    cleanup_db.delete(op)
                u = cleanup_db.get(UserORM, uid)
                if u is not None:
                    cleanup_db.delete(u)
            cleanup_db.commit()
        finally:
            cleanup_db.close()


if __name__ == "__main__":
    test_saas_phase1_auth_isolation_and_topic_lock()
    print("[OK] test_saas_phase1_auth_isolation_and_topic_lock")
    print("ALL SAAS PHASE 1 TESTS PASSED")
