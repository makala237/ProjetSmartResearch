from io import BytesIO
from pathlib import Path
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import SessionLocal, init_db
from app.db.models import AdminAuditLogORM, LicensePlanORM, ProjectORM, UsageEventORM, UserORM
from app.main import app
from tests._auth_helper import login_as_admin


def _dummy_png_bytes(rgb=(0, 74, 143)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), rgb).save(buf, format="PNG")
    return buf.getvalue()


def test_admin_creation_full_permissions_and_locked_student_cover() -> None:
    init_db()
    suffix = uuid4().hex[:8]
    second_admin_email = f"sec_admin_{suffix}@smartresearch.test"
    student_email = f"stu_cover_{suffix}@smartresearch.test"
    created_user_ids: list[str] = []
    created_plan_id: str | None = None

    root_admin = TestClient(app)
    login_as_admin(root_admin)

    try:
        # 1. Create a temporary student license plan
        r_plan = root_admin.post(
            "/api/admin/plans",
            json={
                "name": f"Pack Cover Lock {suffix}",
                "description": "Test plan",
                "price_fcfa": 25000,
                "module1_days": 30,
                "module2_days": 30,
                "module3_days": 90,
                "module4_days": 30,
                "ai_quota_included": 50,
                "is_active": True,
            },
        )
        assert r_plan.status_code == 200, r_plan.text
        created_plan_id = r_plan.json()["id"]

        # 2. Root Admin creates a Second Administrator with full admin role (no plan/project)
        r_new_adm = root_admin.post(
            "/api/admin/users",
            json={
                "email": second_admin_email,
                "full_name": "Co-Administrateur Smart Research",
                "phone": "690000111",
                "role": "admin",
                "plan_id": created_plan_id,  # Should be ignored for admins
                "initial_protocol_title": "Should Not Be Created",
            },
        )
        assert r_new_adm.status_code == 200, r_new_adm.text
        adm_data = r_new_adm.json()
        second_admin_id = adm_data["user"]["id"]
        created_user_ids.append(second_admin_id)
        assert adm_data["user"]["role"] == "admin"
        assert adm_data["user"]["status_summary"] == "admin"
        assert adm_data["created_project_id"] is None
        assert adm_data["user"]["ai_quota"]["credits_remaining"] >= 100000

        # 3. Second Admin logs in, changes password, and exercises full admin privileges
        adm2_client = TestClient(app)
        r_adm2_login = adm2_client.post(
            "/api/auth/login",
            json={"email": second_admin_email, "password": adm_data["temporary_password"]},
        )
        assert r_adm2_login.status_code == 200, r_adm2_login.text
        adm2_client.headers["Authorization"] = f"Bearer {r_adm2_login.json()['access_token']}"

        r_adm2_chg = adm2_client.post(
            "/api/auth/change-password",
            json={
                "current_password": adm_data["temporary_password"],
                "new_password": "AdminSecond2026!",
            },
        )
        assert r_adm2_chg.status_code == 200, r_adm2_chg.text
        adm2_client.headers["Authorization"] = f"Bearer {r_adm2_chg.json()['access_token']}"

        r_dash = adm2_client.get("/api/admin/dashboard")
        assert r_dash.status_code == 200, r_dash.text
        assert r_dash.json()["total_admins"] >= 2

        # 4. Second Admin creates a Student WITH a full multi-university Cover Page & logos
        full_cover = {
            "title": f"Prévalence et facteurs associés à l'HTA ({suffix})",
            "degree_label": "Doctorat en Médecine",
            "university_fr": "UNIVERSITÉ DE DOUALA",
            "university_en": "THE UNIVERSITY OF DOUALA",
            "university_po_box": "2701 Douala",
            "university_email": "rectorat@univ-douala.com",
            "faculty_fr": "FACULTÉ DE MÉDECINE ET DES SCIENCES PHARMACEUTIQUES",
            "faculty_en": "FACULTY OF MEDICINE AND PHARMACEUTICAL SCIENCES",
            "faculty_po_box": "2701 Douala",
            "faculty_email": "decanat@fmsp-douala.cm",
            "department_fr": "MÉDECINE INTERNE",
            "department_en": "INTERNAL MEDICINE",
            "author_name": "Ngo Bikoi Mireille",
            "matricule": f"21M{suffix[:4].upper()}",
            "student_level": "Étudiante en 7ème année de médecine générale",
            "student_phone": "677889900",
            "student_email": student_email,
            "director_info": {
                "name": "Pr KINGUE Samuel",
                "rank": "Professeur Titulaire",
                "specialty": "Cardiologie",
            },
            "co_directors_info": [
                {
                    "name": "Dr BOOMBHI Jérôme",
                    "rank": "Maître de Conférences",
                    "specialty": "Cardiologie",
                }
            ],
            "academic_year": "2026-2027",
            "study_period_label": "Janvier à Décembre 2026",
            "host_institutions": ["Hôpital Laquintinie de Douala"],
            "ethics_committees": ["Comité d'Éthique de l'Université de Douala"],
        }
        r_stu = adm2_client.post(
            "/api/admin/users",
            json={
                "email": student_email,
                "full_name": "Ngo Bikoi Mireille",
                "matricule": full_cover["matricule"],
                "phone": "677889900",
                "role": "student",
                "plan_id": created_plan_id,
                "amount_paid_fcfa": 25000,
                "payment_reference": f"REF-{suffix}",
                "study_type": "Étude transversale analytique",
                "cover": full_cover,
            },
        )
        assert r_stu.status_code == 200, r_stu.text
        stu_payload = r_stu.json()
        student_id = stu_payload["user"]["id"]
        created_user_ids.append(student_id)
        pid = stu_payload["created_project_id"]
        assert pid is not None
        assert stu_payload["user"]["topic_locked"] is True

        # Admin uploads university cover logo for the student's pre-created protocol
        r_logo = adm2_client.post(
            f"/api/projects/{pid}/cover/logo/university",
            files={"file": ("uni.png", _dummy_png_bytes(), "image/png")},
        )
        assert r_logo.status_code == 200, r_logo.text

        # Check admin detail includes full project_cover
        r_det = adm2_client.get(f"/api/admin/users/{student_id}")
        assert r_det.status_code == 200, r_det.text
        det_cover = r_det.json()["project_cover"]
        assert det_cover is not None
        assert det_cover["university_fr"] == "UNIVERSITÉ DE DOUALA"
        assert det_cover["director_info"]["name"] == "Pr KINGUE Samuel"
        assert len(det_cover["co_directors_info"]) == 1

        # 5. Student logs in and verifies cover page is locked & read-only while normal sections remain editable
        stu_client = TestClient(app)
        r_stu_login = stu_client.post(
            "/api/auth/login",
            json={"email": student_email, "password": stu_payload["temporary_password"]},
        )
        assert r_stu_login.status_code == 200, r_stu_login.text
        stu_client.headers["Authorization"] = f"Bearer {r_stu_login.json()['access_token']}"

        r_stu_chg = stu_client.post(
            "/api/auth/change-password",
            json={
                "current_password": stu_payload["temporary_password"],
                "new_password": "StudentLock2026!",
            },
        )
        assert r_stu_chg.status_code == 200, r_stu_chg.text
        stu_client.headers["Authorization"] = f"Bearer {r_stu_chg.json()['access_token']}"

        r_get_proj = stu_client.get(f"/api/projects/{pid}")
        assert r_get_proj.status_code == 200, r_get_proj.text
        proj_json = r_get_proj.json()
        assert proj_json["sections"]["cover_page"]["status"] == "validated"
        assert proj_json["cover"]["faculty_fr"] == "FACULTÉ DE MÉDECINE ET DES SCIENCES PHARMACEUTIQUES"

        # Student CAN read the uploaded cover logo
        r_stu_logo_get = stu_client.get(f"/api/projects/{pid}/cover/logo/university")
        assert r_stu_logo_get.status_code == 200

        # Student is BLOCKED from editing cover fields, uploading logo, deleting logo, or editing section cover_page
        cov_attempt = dict(proj_json["cover"])
        cov_attempt["academic_year"] = "2029-2030"
        r_blk_patch = stu_client.patch(f"/api/projects/{pid}/cover", json=cov_attempt)
        assert r_blk_patch.status_code == 403 and r_blk_patch.json()["detail"]["code"] == "COVER_ADMIN_ONLY"

        r_blk_up = stu_client.post(
            f"/api/projects/{pid}/cover/logo/university",
            files={"file": ("hack.png", _dummy_png_bytes((200, 10, 10)), "image/png")},
        )
        assert r_blk_up.status_code == 403 and r_blk_up.json()["detail"]["code"] == "COVER_ADMIN_ONLY"

        r_blk_del = stu_client.delete(f"/api/projects/{pid}/cover/logo/university")
        assert r_blk_del.status_code == 403 and r_blk_del.json()["detail"]["code"] == "COVER_ADMIN_ONLY"

        r_blk_sec = stu_client.patch(
            f"/api/projects/{pid}/sections/cover_page",
            json={"content_markdown": "Tentative contournement"},
        )
        assert r_blk_sec.status_code == 403 and r_blk_sec.json()["detail"]["code"] == "COVER_ADMIN_ONLY"

        # Normal protocol drafting section STILL works for student
        r_ok_sec = stu_client.patch(
            f"/api/projects/{pid}/sections/intro_contexte",
            json={"content_markdown": "Contexte clinique initial.", "status": "in_progress", "user_notes": ""},
        )
        assert r_ok_sec.status_code == 200, r_ok_sec.text

        # 6. Admin updates the student's full cover page via /api/admin/users/{id}/cover & checks persistence + role promotion
        updated_cov = dict(det_cover)
        updated_cov["academic_year"] = "2027-2028"
        updated_cov["department_fr"] = "CARDIOLOGIE"
        updated_cov["department_en"] = "CARDIOLOGY"
        r_adm_cov = adm2_client.patch(
            f"/api/admin/users/{student_id}/cover",
            json={
                "cover": updated_cov,
                "study_type": "Étude prospective observationnelle",
                "topic_locked": True,
                "reason": "Validation du département par le décanat",
            },
        )
        assert r_adm_cov.status_code == 200, r_adm_cov.text

        r_det_after = adm2_client.get(f"/api/admin/users/{student_id}")
        assert r_det_after.status_code == 200
        assert r_det_after.json()["project_cover"]["academic_year"] == "2027-2028"
        assert r_det_after.json()["project_cover"]["department_fr"] == "CARDIOLOGIE"
        # Existing university logo preserved
        assert r_det_after.json()["project_cover"]["university_logo_url"]
        assert r_det_after.json()["project_study_type"] == "Étude prospective observationnelle"

        # Promote student -> admin via PATCH /api/admin/users/{id} verifies full admin quota & role update
        r_prom = adm2_client.patch(f"/api/admin/users/{student_id}", json={"role": "admin"})
        assert r_prom.status_code == 200
        assert r_prom.json()["role"] == "admin"
        assert r_prom.json()["ai_quota"]["credits_total"] >= 100000

    finally:
        db = SessionLocal()
        try:
            for uid in created_user_ids:
                db.query(UsageEventORM).filter(UsageEventORM.user_id == uid).delete()
                db.query(AdminAuditLogORM).filter(
                    (AdminAuditLogORM.target_user_id == uid) | (AdminAuditLogORM.admin_id == uid)
                ).delete()
                projs = db.query(ProjectORM).filter(ProjectORM.owner_user_id == uid).all()
                for pr in projs:
                    db.delete(pr)
                u = db.get(UserORM, uid)
                if u is not None:
                    db.delete(u)
            if created_plan_id:
                pl = db.get(LicensePlanORM, created_plan_id)
                if pl is not None:
                    db.delete(pl)
            db.commit()
        finally:
            db.close()


if __name__ == "__main__":
    test_admin_creation_full_permissions_and_locked_student_cover()
    print("[OK] test_admin_creation_full_permissions_and_locked_student_cover")
    print("ALL ADMIN ROLE & LOCKED COVER TESTS PASSED")
