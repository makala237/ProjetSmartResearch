from pathlib import Path
import sys
from uuid import uuid4

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.database import SessionLocal, init_db
from app.db.models import AdminAuditLogORM, LicensePlanORM, ProjectORM, UserORM
from app.main import app
from tests._auth_helper import login_as_admin


def test_saas_phase2_licensing_quotas_and_admin_api() -> None:
    init_db()
    suffix = uuid4().hex[:6]
    student_email = f"phase2_student_{suffix}@test.local"
    custom_plan_name = f"Plan Test Quota1 {suffix}"

    created_user_id = None
    created_plan_id = None

    try:
        admin_client = TestClient(app)
        login_as_admin(admin_client)

        # 1. Seeded plans available + custom plan CRUD (ai_quota=1 for quota boundary test)
        r_plans = admin_client.get("/api/admin/plans")
        assert r_plans.status_code == 200, r_plans.text
        seeded = r_plans.json()
        assert len(seeded) >= 2

        r_new_plan = admin_client.post(
            "/api/admin/plans",
            json={
                "name": custom_plan_name,
                "description": "Plan test unitaire granulaire",
                "price_fcfa": 12500,
                "module1_days": 30,
                "module2_days": 30,
                "module3_days": 180,
                "module4_days": 30,
                "ai_quota_included": 1,
                "is_active": True,
            },
        )
        assert r_new_plan.status_code == 200, r_new_plan.text
        created_plan_id = r_new_plan.json()["id"]

        r_upd_plan = admin_client.patch(
            f"/api/admin/plans/{created_plan_id}",
            json={"price_fcfa": 14000},
        )
        assert r_upd_plan.status_code == 200 and r_upd_plan.json()["price_fcfa"] == 14000

        # 2. Admin creates student manually with license plan + pre-created locked protocol
        r_create_u = admin_client.post(
            "/api/admin/users",
            json={
                "email": student_email,
                "full_name": "Etudiant Phase Deux",
                "matricule": f"22M{suffix[:3].upper()}",
                "phone": "677112233",
                "role": "student",
                "plan_id": created_plan_id,
                "amount_paid_fcfa": 14000,
                "payment_reference": f"OM-{suffix}",
                "initial_protocol_title": f"Étude sur le diabète à Yaoundé ({suffix})",
            },
        )
        assert r_create_u.status_code == 200, r_create_u.text
        u_payload = r_create_u.json()
        created_user_id = u_payload["user"]["id"]
        temp_pw = u_payload["temporary_password"]
        project_id = u_payload["created_project_id"]
        assert project_id is not None
        assert u_payload["user"]["topic_locked"] is True
        assert u_payload["user"]["entitlements"]["module3"]["days_remaining"] >= 179
        assert u_payload["user"]["ai_quota"]["credits_remaining"] == 1

        # 3. Student logs in, changes temporary password, verifies RBAC blocked on /api/admin/*
        stu_client = TestClient(app)
        r_login = stu_client.post(
            "/api/auth/login",
            json={"email": student_email, "password": temp_pw},
        )
        assert r_login.status_code == 200, r_login.text
        stu_client.headers["Authorization"] = f"Bearer {r_login.json()['access_token']}"

        r_chg = stu_client.post(
            "/api/auth/change-password",
            json={"current_password": temp_pw, "new_password": "StudentSecure2026!"},
        )
        assert r_chg.status_code == 200, r_chg.text
        stu_client.headers["Authorization"] = f"Bearer {r_chg.json()['access_token']}"

        r_rbac = stu_client.get("/api/admin/dashboard")
        assert r_rbac.status_code == 403 and r_rbac.json()["detail"]["code"] == "ADMIN_REQUIRED"

        # Generate default presentation deck before testing expiry
        r_pres_init = stu_client.get(f"/api/projects/{project_id}/presentation")
        assert r_pres_init.status_code == 200, r_pres_init.text

        # 4. Granular expiry behavior: expire M1, M2, M4 while keeping M3 active (180d)
        for exp_mod in ("module1", "module2", "module4"):
            r_rev = admin_client.patch(
                f"/api/admin/users/{created_user_id}/entitlements/{exp_mod}",
                json={"revoke": True, "notes": "Test expiration granulaire"},
            )
            assert r_rev.status_code == 200, r_rev.text
            assert r_rev.json()["entitlements"][exp_mod]["is_active"] is False

        # Module 3 remains active: write (patient) and export CSV BOTH return 200
        r_pat = stu_client.post(
            f"/api/projects/{project_id}/patients",
            json={"patient_code": "PAT-001", "hospital_site": "HCY", "values": {"age": 42}},
        )
        assert r_pat.status_code == 200, r_pat.text
        r_csv = stu_client.get(f"/api/projects/{project_id}/patients/export.csv")
        assert r_csv.status_code == 200 and "PAT-001" in r_csv.text

        # Module 1 expired: READ 200, WRITE/AI -> 403 MODULE_EXPIRED
        assert stu_client.get(f"/api/projects/{project_id}").status_code == 200
        r_m1_write = stu_client.patch(
            f"/api/projects/{project_id}/sections/intro_contexte",
            json={"content_markdown": "Tentative modification expirée"},
        )
        assert r_m1_write.status_code == 403 and r_m1_write.json()["detail"]["code"] == "MODULE_EXPIRED"
        r_m1_ai = stu_client.post(f"/api/projects/{project_id}/sections/intro_contexte/audit")
        assert r_m1_ai.status_code == 403 and r_m1_ai.json()["detail"]["code"] == "MODULE_EXPIRED"

        # Module 2 expired: EXPORT .docx -> 200 (>10KB), WRITE/AI -> 403 MODULE_EXPIRED
        r_docx = stu_client.post(f"/api/projects/{project_id}/export/docx")
        assert r_docx.status_code == 200 and len(r_docx.content) > 10_000
        r_m2_write = stu_client.patch(
            f"/api/projects/{project_id}/formatting",
            json={"formatting": {"font_name": "Times New Roman"}},
        )
        assert r_m2_write.status_code == 403 and r_m2_write.json()["detail"]["code"] == "MODULE_EXPIRED"
        r_m2_ai = stu_client.post(f"/api/projects/{project_id}/global-audit")
        assert r_m2_ai.status_code == 403 and r_m2_ai.json()["detail"]["code"] == "MODULE_EXPIRED"

        # Module 4 expired: READ + EXPORT .pptx -> 200 (>50KB), WRITE/AI -> 403 MODULE_EXPIRED
        pres_data = stu_client.get(f"/api/projects/{project_id}/presentation").json()
        r_pptx = stu_client.get(f"/api/projects/{project_id}/presentation/export/pptx")
        assert r_pptx.status_code == 200 and len(r_pptx.content) > 50_000
        r_m4_write = stu_client.put(f"/api/projects/{project_id}/presentation", json=pres_data)
        assert r_m4_write.status_code == 403 and r_m4_write.json()["detail"]["code"] == "MODULE_EXPIRED"
        r_m4_ai = stu_client.post(f"/api/projects/{project_id}/presentation/audit")
        assert r_m4_ai.status_code == 403 and r_m4_ai.json()["detail"]["code"] == "MODULE_EXPIRED"

        # 5. Plan renewal stacking: re-granting stacks Module 3 (~360d), reactivates M1/M2/M4, sets credits=2
        r_renew = admin_client.post(
            f"/api/admin/users/{created_user_id}/grant-plan",
            json={"plan_id": created_plan_id, "payment_reference": f"MTN-{suffix}", "notes": "Renouvellement"},
        )
        assert r_renew.status_code == 200, r_renew.text
        renew_user = r_renew.json()
        assert renew_user["entitlements"]["module1"]["is_active"] is True
        assert renew_user["entitlements"]["module3"]["days_remaining"] >= 358

        # Set ai credits back to 1 (via usage or new user? Wait: student currently has 2 total, 0 used!)
        # 6. AI quota enforcement: first consume 1 credit via audit (leaving 1), then test 5-cr block, 1-cr debit -> 0, then block on 1-cr, then recharge +20
        r_aud1 = stu_client.post(f"/api/projects/{project_id}/presentation/audit")
        assert r_aud1.status_code == 200, r_aud1.text
        assert stu_client.get("/api/auth/me").json()["ai_quota"]["credits_remaining"] == 1

        # Request 5-credit generate-all while only 1 credit remains -> blocked BEFORE Gemini
        r_gen_all = stu_client.post(f"/api/projects/{project_id}/presentation/generate-all")
        assert r_gen_all.status_code == 403
        assert r_gen_all.json()["detail"]["code"] == "AI_QUOTA_EXCEEDED"
        assert stu_client.get("/api/auth/me").json()["ai_quota"]["credits_remaining"] == 1

        # Consume last credit with 1-credit audit -> balance drops to 0
        r_aud2 = stu_client.post(f"/api/projects/{project_id}/presentation/audit")
        assert r_aud2.status_code == 200, r_aud2.text
        assert stu_client.get("/api/auth/me").json()["ai_quota"]["credits_remaining"] == 0

        # Next audit blocked at 0 credits
        r_aud3 = stu_client.post(f"/api/projects/{project_id}/presentation/audit")
        assert r_aud3.status_code == 403 and r_aud3.json()["detail"]["code"] == "AI_QUOTA_EXCEEDED"

        # Recharge +20 credits restores remaining to 20
        r_rech = admin_client.post(
            f"/api/admin/users/{created_user_id}/recharge-ai",
            json={"add_credits": 20, "notes": "Recharge commerciale test"},
        )
        assert r_rech.status_code == 200 and r_rech.json()["ai_quota"]["credits_remaining"] == 20

        # 7. Admin override locked project identity, password reset revoking session, and admin stats/telemetry
        r_id_ovr = admin_client.patch(
            f"/api/admin/users/{created_user_id}/project-identity",
            json={"title": f"Titre Officiel Validé Jury ({suffix})", "reason": "Correction directeur de thèse"},
        )
        assert r_id_ovr.status_code == 200
        assert r_id_ovr.json()["project_title"] == f"Titre Officiel Validé Jury ({suffix})"

        # Verify stats & telemetry accuracy
        r_detail = admin_client.get(f"/api/admin/users/{created_user_id}")
        assert r_detail.status_code == 200, r_detail.text
        det = r_detail.json()
        assert len(det["grants"]) == 2
        assert det["user"]["patients_count"] == 1
        assert det["user"]["exports_count"] == 3  # csv + docx + pptx
        assert det["user"]["ai_quota"]["credits_used"] == 2
        assert det["user"]["ai_calls_count"] >= 2

        r_dash = admin_client.get("/api/admin/dashboard")
        assert r_dash.status_code == 200
        dash = r_dash.json()
        assert dash["total_revenue_fcfa"] >= 28000
        assert dash["total_patient_records"] >= 1
        assert dash["total_exports"] >= 3
        assert dash["total_ai_credits_used"] >= 2

        r_st = admin_client.get(f"/api/admin/stats/students?search={suffix}")
        assert r_st.status_code == 200 and len(r_st.json()) == 1
        assert admin_client.get(f"/api/admin/usage-events?user_id={created_user_id}").status_code == 200
        assert admin_client.get("/api/admin/audit-logs?limit=20").status_code == 200

        # Password reset revokes active student session immediately
        r_rst = admin_client.post(f"/api/admin/users/{created_user_id}/reset-password", json={})
        assert r_rst.status_code == 200 and r_rst.json()["temporary_password"].startswith("FMSB-")
        r_stu_after_rst = stu_client.get(f"/api/projects/{project_id}")
        assert r_stu_after_rst.status_code == 401
        assert r_stu_after_rst.json()["detail"]["code"] == "SESSION_REVOKED"
    finally:
        # Clean up test user, projects, audit logs, and custom plan
        cleanup_db = SessionLocal()
        try:
            if created_user_id:
                cleanup_db.query(AdminAuditLogORM).filter(AdminAuditLogORM.target_user_id == created_user_id).delete()
                for p in cleanup_db.query(ProjectORM).filter(ProjectORM.owner_user_id == created_user_id).all():
                    cleanup_db.delete(p)
                u = cleanup_db.get(UserORM, created_user_id)
                if u is not None:
                    cleanup_db.delete(u)
            if created_plan_id:
                pl = cleanup_db.get(LicensePlanORM, created_plan_id)
                if pl is not None:
                    cleanup_db.delete(pl)
            cleanup_db.commit()
        finally:
            cleanup_db.close()


if __name__ == "__main__":
    test_saas_phase2_licensing_quotas_and_admin_api()
    print("[OK] test_saas_phase2_licensing_quotas_and_admin_api")
    print("ALL SAAS PHASE 2 TESTS PASSED")
