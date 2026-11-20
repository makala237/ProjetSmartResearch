from datetime import datetime, timezone
import math
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_admin
from app.core.security import ensure_utc, hash_password
from app.db.database import get_db
from app.db.datastore import (
    UserLicenseGrantRecord,
    UserModuleEntitlementRecord,
    UserRecord,
    as_store,
)
from app.db.models import utcnow
from app.schemas.admin import (
    AdminAdjustEntitlementRequest,
    AdminAuditLogOut,
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminDashboardStatsOut,
    AdminGrantPlanRequest,
    AdminGrantRecordOut,
    AdminRechargeAIRequest,
    AdminResetPasswordRequest,
    AdminResetPasswordResponse,
    AdminStudentStatsRow,
    AdminUpdateProjectIdentityRequest,
    AdminUpdateUserCoverRequest,
    AdminUpdateUserRequest,
    AdminUserDetailOut,
    LicensePlanCreateRequest,
    LicensePlanOut,
    LicensePlanUpdateRequest,
    UsageEventOut,
)
from app.schemas.auth import AIQuotaOut, ModuleEntitlementOut
from app.schemas.protocol import CoverMetadata, CreateProjectRequest, StepStatus
from app.services.auth_service import create_user_account
from app.services.licensing_service import (
    MODULE_KEYS,
    adjust_user_module_entitlement,
    apply_license_plan_to_user,
    get_student_daily_38_usage,
    recharge_user_ai_quota,
)
from app.services.storage import project_storage
from app.services.working_memory import update_section_working_memory

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)

EXPORT_ACTIONS = frozenset({"export_docx", "export_pdf", "export_csv", "export_pptx", "export_pptx_pdf"})


def _generate_temp_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    p1 = "".join(secrets.choice(alphabet) for _ in range(4))
    p2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"FMSB-{p1}-{p2}"


def _build_student_rows(
    db: Any,
    users: List[Any],
) -> List[AdminStudentStatsRow]:
    if not users:
        return []
    store = as_store(db)
    user_ids = [u.id for u in users]
    now = datetime.now(timezone.utc)

    ent_by_user: Dict[str, Dict[str, UserModuleEntitlementRecord]] = store.list_entitlements_for_users(user_ids)
    quota_by_user = store.list_quotas_for_users(user_ids)
    proj_by_user = store.list_project_metas_for_users(user_ids)
    exports_by_user, ai_calls_by_user = store.aggregate_ok_events_by_users(user_ids, EXPORT_ACTIONS)

    result: List[AdminStudentStatsRow] = []
    for u in users:
        user_ents = ent_by_user.get(u.id, {})
        ents_out: Dict[str, ModuleEntitlementOut] = {}
        active_days: List[int] = []
        had_any_ent = False

        for mk in MODULE_KEYS:
            er = user_ents.get(mk)
            if er is not None:
                had_any_ent = True
            if u.role == "admin":
                ents_out[mk] = ModuleEntitlementOut(
                    module_key=mk,
                    starts_at=ensure_utc(er.starts_at) if er else None,
                    expires_at=ensure_utc(er.expires_at) if er else None,
                    is_active=True,
                    days_remaining=9999,
                )
            elif er is None:
                ents_out[mk] = ModuleEntitlementOut(module_key=mk, is_active=False, days_remaining=0)
            else:
                exp = ensure_utc(er.expires_at)
                starts = ensure_utc(er.starts_at)
                active = bool(exp and exp > now)
                days_rem = max(0, math.ceil((exp - now).total_seconds() / 86400)) if active and exp else 0
                if active:
                    active_days.append(days_rem)
                ents_out[mk] = ModuleEntitlementOut(
                    module_key=mk,
                    starts_at=starts,
                    expires_at=exp,
                    is_active=active,
                    days_remaining=days_rem,
                )

        if u.role == "admin":
            summary = "admin"
        elif not u.is_active:
            summary = "disabled"
        elif active_days:
            summary = "expiring_soon" if min(active_days) <= 7 else "active"
        elif had_any_ent:
            summary = "expired"
        else:
            summary = "none"

        daily_38 = get_student_daily_38_usage(store, u)
        q = quota_by_user.get(u.id)
        if q:
            ai_quota = AIQuotaOut(
                credits_total=int(q.credits_total),
                credits_used=int(q.credits_used),
                credits_remaining=max(0, int(q.credits_total) - int(q.credits_used)),
                daily_38_limit=daily_38["daily_limit"],
                daily_38_used=daily_38["used_today"],
                daily_38_remaining=daily_38["remaining_today"],
                active_reasoning_model=daily_38["active_reasoning_model"],
                using_35_fallback=daily_38["fallback_active"],
            )
        else:
            ai_quota = AIQuotaOut(
                credits_total=100000 if u.role == "admin" else 0,
                credits_used=0,
                credits_remaining=100000 if u.role == "admin" else 0,
                daily_38_limit=daily_38["daily_limit"],
                daily_38_used=daily_38["used_today"],
                daily_38_remaining=daily_38["remaining_today"],
                active_reasoning_model=daily_38["active_reasoning_model"],
                using_35_fallback=daily_38["fallback_active"],
            )

        pr = proj_by_user.get(u.id)
        result.append(
            AdminStudentStatsRow(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                matricule=u.matricule,
                phone=u.phone,
                role=u.role,
                is_active=u.is_active,
                must_change_password=u.must_change_password,
                session_switch_count=int(u.session_switch_count or 0),
                last_login_at=ensure_utc(u.last_login_at),
                last_login_ip=u.last_login_ip,
                created_at=ensure_utc(u.created_at) or now,
                project_id=pr.project_id if pr else None,
                project_title=(pr.locked_title or pr.name) if pr else None,
                topic_locked=bool(pr.topic_locked) if pr else False,
                sections_validated_count=int(pr.sections_validated_count) if pr else 0,
                patients_count=int(pr.patients_count) if pr else 0,
                exports_count=exports_by_user.get(u.id, 0),
                ai_calls_count=ai_calls_by_user.get(u.id, 0),
                entitlements=ents_out,
                ai_quota=ai_quota,
                status_summary=summary,
            )
        )
    return result


def _hydrate_grants(db: Any, grants: List[UserLicenseGrantRecord]) -> List[AdminGrantRecordOut]:
    if not grants:
        return []
    store = as_store(db)
    uids = list({g.user_id for g in grants})
    pids = list({g.plan_id for g in grants if g.plan_id})
    users = store.get_users_by_ids(uids) if uids else {}
    plans = store.get_plans_by_ids(pids) if pids else {}
    now = datetime.now(timezone.utc)
    out: List[AdminGrantRecordOut] = []
    for g in grants:
        u = users.get(g.user_id)
        p = plans.get(g.plan_id) if g.plan_id else None
        out.append(
            AdminGrantRecordOut(
                id=g.id,
                user_id=g.user_id,
                user_email=u.email if u else "",
                user_name=u.full_name if u else "",
                plan_id=g.plan_id,
                plan_name=p.name if p else "Ajustement / Plan supprimé",
                granted_by_admin_id=g.granted_by_admin_id,
                amount_paid_fcfa=int(g.amount_paid_fcfa or 0),
                payment_reference=g.payment_reference or "",
                notes=g.notes or "",
                granted_at=ensure_utc(g.granted_at) or now,
            )
        )
    return out


# =========================================================================
# DASHBOARD KPIS
# =========================================================================
@router.get("/dashboard", response_model=AdminDashboardStatsOut)
def get_admin_dashboard(db: Any = Depends(get_db)) -> AdminDashboardStatsOut:
    store = as_store(db)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_admins = store.count_users(role="admin")
    students = store.list_users(role="student")
    student_rows = _build_student_rows(store, students)

    active_cnt = sum(1 for r in student_rows if r.status_summary == "active")
    exp_soon_rows = [r for r in student_rows if r.status_summary == "expiring_soon"]
    expired_cnt = sum(1 for r in student_rows if r.status_summary in ("expired", "none"))
    disabled_cnt = sum(1 for r in student_rows if r.status_summary == "disabled")

    all_grants = store.list_grants()
    total_rev = sum(int(g.amount_paid_fcfa or 0) for g in all_grants)
    month_rev = sum(
        int(g.amount_paid_fcfa or 0)
        for g in all_grants
        if (ensure_utc(g.granted_at) or now) >= month_start
    )

    total_projects = store.count_projects()
    total_patients = store.sum_patients_count()

    all_events = store.list_usage_events(status="ok")
    total_exports = sum(1 for e in all_events if e.action in EXPORT_ACTIONS)
    total_ai_used = sum(int(e.credits_cost or 0) for e in all_events)
    month_ai_used = sum(
        int(e.credits_cost or 0)
        for e in all_events
        if (ensure_utc(e.created_at) or now) >= month_start
    )

    return AdminDashboardStatsOut(
        total_admins=total_admins,
        total_students=len(student_rows),
        active_students=active_cnt,
        expiring_soon_students=len(exp_soon_rows),
        expired_students=expired_cnt,
        disabled_students=disabled_cnt,
        total_revenue_fcfa=total_rev,
        month_revenue_fcfa=month_rev,
        total_projects=total_projects,
        total_patient_records=total_patients,
        total_exports=total_exports,
        total_ai_credits_used=total_ai_used,
        month_ai_credits_used=month_ai_used,
        recent_grants=_hydrate_grants(store, all_grants[:8]),
        expiring_soon_list=exp_soon_rows[:10],
    )


# =========================================================================
# LICENSE PLANS CRUD
# =========================================================================
@router.get("/plans", response_model=List[LicensePlanOut])
def list_license_plans(
    include_inactive: bool = False,
    db: Any = Depends(get_db),
) -> List[LicensePlanOut]:
    store = as_store(db)
    plans = store.list_plans(include_inactive=include_inactive)
    return [
        LicensePlanOut(
            id=p.id,
            name=p.name,
            description=p.description,
            price_fcfa=p.price_fcfa,
            module1_days=p.module1_days,
            module2_days=p.module2_days,
            module3_days=p.module3_days,
            module4_days=p.module4_days,
            ai_quota_included=p.ai_quota_included,
            is_active=p.is_active,
            created_at=ensure_utc(p.created_at) or datetime.now(timezone.utc),
        )
        for p in plans
    ]


@router.post("/plans", response_model=LicensePlanOut)
def create_license_plan(
    payload: LicensePlanCreateRequest,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> LicensePlanOut:
    store = as_store(db)
    clean_name = payload.name.strip()
    if store.get_plan_by_name(clean_name) is not None:
        raise HTTPException(status_code=409, detail="Un plan avec ce nom existe déjà.")

    plan = store.create_plan(
        name=clean_name,
        description=payload.description.strip(),
        price_fcfa=payload.price_fcfa,
        module1_days=payload.module1_days,
        module2_days=payload.module2_days,
        module3_days=payload.module3_days,
        module4_days=payload.module4_days,
        ai_quota_included=payload.ai_quota_included,
        is_active=payload.is_active,
        created_at=utcnow(),
    )
    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=None,
        action="create_license_plan",
        details_json={"plan_id": plan.id, "name": plan.name, "price_fcfa": plan.price_fcfa},
        created_at=utcnow(),
    )
    return LicensePlanOut(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        price_fcfa=plan.price_fcfa,
        module1_days=plan.module1_days,
        module2_days=plan.module2_days,
        module3_days=plan.module3_days,
        module4_days=plan.module4_days,
        ai_quota_included=plan.ai_quota_included,
        is_active=plan.is_active,
        created_at=ensure_utc(plan.created_at) or datetime.now(timezone.utc),
    )


@router.patch("/plans/{plan_id}", response_model=LicensePlanOut)
def update_license_plan(
    plan_id: str,
    payload: LicensePlanUpdateRequest,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> LicensePlanOut:
    store = as_store(db)
    plan = store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan de licence introuvable.")

    updates: Dict[str, Any] = {}
    if payload.name is not None:
        clean_name = payload.name.strip()
        if store.get_plan_by_name(clean_name, exclude_id=plan_id) is not None:
            raise HTTPException(status_code=409, detail="Un autre plan utilise déjà ce nom.")
        updates["name"] = clean_name
    if payload.description is not None:
        updates["description"] = payload.description.strip()
    if payload.price_fcfa is not None:
        updates["price_fcfa"] = payload.price_fcfa
    if payload.module1_days is not None:
        updates["module1_days"] = payload.module1_days
    if payload.module2_days is not None:
        updates["module2_days"] = payload.module2_days
    if payload.module3_days is not None:
        updates["module3_days"] = payload.module3_days
    if payload.module4_days is not None:
        updates["module4_days"] = payload.module4_days
    if payload.ai_quota_included is not None:
        updates["ai_quota_included"] = payload.ai_quota_included
    if payload.is_active is not None:
        updates["is_active"] = payload.is_active

    plan = store.update_plan(plan_id, **updates) or plan
    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=None,
        action="update_license_plan",
        details_json={"plan_id": plan.id, "name": plan.name},
        created_at=utcnow(),
    )
    return LicensePlanOut(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        price_fcfa=plan.price_fcfa,
        module1_days=plan.module1_days,
        module2_days=plan.module2_days,
        module3_days=plan.module3_days,
        module4_days=plan.module4_days,
        ai_quota_included=plan.ai_quota_included,
        is_active=plan.is_active,
        created_at=ensure_utc(plan.created_at) or datetime.now(timezone.utc),
    )


@router.delete("/plans/{plan_id}")
def delete_or_archive_license_plan(
    plan_id: str,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    store = as_store(db)
    plan = store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan de licence introuvable.")
    if store.has_grants_for_plan(plan_id):
        store.update_plan(plan_id, is_active=False)
        action = "archive_license_plan"
        status_out = "archived"
    else:
        store.delete_plan(plan_id)
        action = "delete_license_plan"
        status_out = "deleted"
    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=None,
        action=action,
        details_json={"plan_id": plan_id, "name": plan.name},
        created_at=utcnow(),
    )
    return {"status": status_out, "plan_id": plan_id}


# =========================================================================
# USERS MANAGEMENT, LICENSES, QUOTAS & LOCKED IDENTITY OVERRIDE
# =========================================================================
@router.get("/users", response_model=List[AdminStudentStatsRow])
def list_users(
    role: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    db: Any = Depends(get_db),
) -> List[AdminStudentStatsRow]:
    store = as_store(db)
    users = store.list_users(role=role if role in ("student", "admin") else None)
    rows = _build_student_rows(store, users)

    if search and search.strip():
        needle = search.strip().casefold()
        rows = [
            r
            for r in rows
            if needle in r.email.casefold()
            or needle in r.full_name.casefold()
            or needle in r.matricule.casefold()
            or needle in (r.project_title or "").casefold()
        ]
    if status and status.strip():
        target_st = status.strip()
        rows = [r for r in rows if r.status_summary == target_st]
    return rows


@router.post("/users", response_model=AdminCreateUserResponse)
def admin_create_user(
    payload: AdminCreateUserRequest,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> AdminCreateUserResponse:
    store = as_store(db)
    role = payload.role if payload.role in ("student", "admin") else "student"
    temp_pw = (payload.temporary_password or "").strip() or _generate_temp_password()

    plan = None
    if role == "student" and payload.plan_id:
        plan = store.get_plan(payload.plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan de licence introuvable.")

    try:
        user = create_user_account(
            db=store,
            email=payload.email,
            full_name=payload.full_name,
            password=temp_pw,
            role=role,
            matricule=payload.matricule,
            phone=payload.phone,
            must_change_password=True,
            created_by_admin_id=admin_user.id,
        )
    except ValueError as exc:
        if str(exc) == "EMAIL_EXISTS":
            raise HTTPException(status_code=409, detail="Un compte avec cet identifiant ou e-mail existe déjà.") from exc
        raise

    if plan is not None and role == "student":
        apply_license_plan_to_user(
            db=store,
            admin_user=admin_user,
            target_user=user,
            plan=plan,
            amount_paid_fcfa=payload.amount_paid_fcfa,
            payment_reference=payload.payment_reference,
            notes=payload.grant_notes,
        )

    created_pid: Optional[str] = None
    if role == "student":
        init_title = (payload.initial_protocol_title or "").strip()
        cov: Optional[CoverMetadata] = None
        if payload.cover is not None:
            cov = payload.cover.model_copy(deep=True)
            if not cov.title.strip() and init_title:
                cov.title = init_title
        elif init_title:
            cov = CoverMetadata(
                title=init_title,
                author_name=user.full_name,
                matricule=user.matricule,
                student_phone=user.phone,
                student_email=user.email if "@" in user.email else "",
            )

        if cov is not None and cov.title.strip():
            if not cov.author_name.strip():
                cov.author_name = user.full_name
            if not cov.matricule.strip() and user.matricule:
                cov.matricule = user.matricule
            if not cov.student_phone.strip() and user.phone:
                cov.student_phone = user.phone
            if not cov.student_email.strip() and user.email and "@" in user.email:
                cov.student_email = user.email

            created_proj = project_storage.create(
                CreateProjectRequest(
                    name=cov.title.strip(),
                    study_type=payload.study_type.strip() or "Étude transversale analytique",
                    cover=cov,
                ),
                owner_user_id=user.id,
                owner_role="student",
                owner_full_name=cov.author_name or user.full_name,
                owner_matricule=cov.matricule or user.matricule,
            )
            if "cover_page" in created_proj.sections:
                created_proj.sections["cover_page"].status = StepStatus.VALIDATED
                created_proj.sections["cover_page"].content_markdown = cov.title.strip()
                created_proj = project_storage.save(created_proj)
            update_section_working_memory(created_proj, "cover_page")
            created_pid = created_proj.project_id

    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=user.id,
        action="create_user",
        details_json={
            "email": user.email,
            "role": role,
            "plan_id": payload.plan_id if role == "student" else None,
            "created_project_id": created_pid,
        },
        created_at=utcnow(),
    )
    fresh_user = store.get_user(user.id) or user
    row_out = _build_student_rows(store, [fresh_user])[0]
    return AdminCreateUserResponse(
        user=row_out,
        temporary_password=temp_pw,
        created_project_id=created_pid,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
def get_user_detail(
    user_id: str,
    db: Any = Depends(get_db),
) -> AdminUserDetailOut:
    store = as_store(db)
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    row_out = _build_student_rows(store, [user])[0]
    grants = store.list_grants(user_id=user_id, limit=50)
    events = store.list_usage_events(user_id=user_id, limit=50)
    logs = store.list_audit_logs(target_user_id=user_id, limit=50)

    admin_ids = list({l.admin_id for l in logs if l.admin_id})
    admins = {uid: u.email for uid, u in store.get_users_by_ids(admin_ids).items()} if admin_ids else {}
    now = datetime.now(timezone.utc)
    proj_obj = project_storage.get(row_out.project_id) if row_out.project_id else None

    return AdminUserDetailOut(
        user=row_out,
        grants=_hydrate_grants(store, grants),
        recent_events=[
            UsageEventOut(
                id=e.id,
                user_id=e.user_id,
                user_email=user.email,
                project_id=e.project_id,
                module_key=e.module_key,
                action=e.action,
                model_used=e.model_used,
                credits_cost=e.credits_cost,
                status=e.status,
                ip_address=e.ip_address,
                created_at=ensure_utc(e.created_at) or now,
            )
            for e in events
        ],
        recent_audit_logs=[
            AdminAuditLogOut(
                id=l.id,
                admin_id=l.admin_id,
                admin_email=admins.get(l.admin_id or "", ""),
                target_user_id=l.target_user_id,
                target_email=user.email,
                action=l.action,
                details_json=l.details_json or {},
                created_at=ensure_utc(l.created_at) or now,
            )
            for l in logs
        ],
        project_cover=proj_obj.cover if proj_obj else None,
        project_study_type=proj_obj.study_type if proj_obj else None,
    )


@router.patch("/users/{user_id}", response_model=AdminStudentStatsRow)
def update_user(
    user_id: str,
    payload: AdminUpdateUserRequest,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> AdminStudentStatsRow:
    store = as_store(db)
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if user.id == admin_user.id:
        if payload.is_active is False:
            raise HTTPException(status_code=400, detail="Vous ne pouvez pas désactiver votre propre compte.")
        if payload.role is not None and payload.role != "admin":
            raise HTTPException(status_code=400, detail="Vous ne pouvez pas rétrograder votre propre compte.")

    updates: Dict[str, Any] = {}
    if payload.full_name is not None:
        updates["full_name"] = payload.full_name.strip()
    if payload.matricule is not None:
        updates["matricule"] = payload.matricule.strip()
    if payload.phone is not None:
        updates["phone"] = payload.phone.strip()
    if payload.role in ("student", "admin"):
        updates["role"] = payload.role
        if payload.role == "admin":
            store.ensure_admin_min_quota(user.id, min_credits=100000, now=utcnow())
    if payload.is_active is not None:
        updates["is_active"] = payload.is_active
        if not payload.is_active:
            updates["active_session_id"] = None

    user = store.update_user(user.id, **updates) or user
    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=user.id,
        action="update_user",
        details_json=payload.model_dump(exclude_none=True),
        created_at=utcnow(),
    )
    fresh_user = store.get_user(user_id) or user
    return _build_student_rows(store, [fresh_user])[0]


@router.post("/users/{user_id}/reset-password", response_model=AdminResetPasswordResponse)
def reset_user_password(
    user_id: str,
    payload: Optional[AdminResetPasswordRequest] = None,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> AdminResetPasswordResponse:
    store = as_store(db)
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    temp_pw = (
        payload.new_temporary_password.strip()
        if payload and payload.new_temporary_password
        else _generate_temp_password()
    )
    store.update_user(
        user.id,
        password_hash=hash_password(temp_pw),
        must_change_password=True,
        active_session_id=None,
    )
    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=user.id,
        action="reset_password",
        details_json={"email": user.email},
        created_at=utcnow(),
    )
    return AdminResetPasswordResponse(user_id=user.id, temporary_password=temp_pw)


@router.post("/users/{user_id}/grant-plan", response_model=AdminStudentStatsRow)
def grant_plan_to_user(
    user_id: str,
    payload: AdminGrantPlanRequest,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> AdminStudentStatsRow:
    store = as_store(db)
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    plan = store.get_plan(payload.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan de licence introuvable.")

    apply_license_plan_to_user(
        db=store,
        admin_user=admin_user,
        target_user=user,
        plan=plan,
        amount_paid_fcfa=payload.amount_paid_fcfa,
        payment_reference=payload.payment_reference,
        notes=payload.notes,
    )
    fresh_user = store.get_user(user_id) or user
    return _build_student_rows(store, [fresh_user])[0]


@router.patch("/users/{user_id}/entitlements/{module_key}", response_model=AdminStudentStatsRow)
def adjust_module_license(
    user_id: str,
    module_key: str,
    payload: AdminAdjustEntitlementRequest,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> AdminStudentStatsRow:
    store = as_store(db)
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    adjust_user_module_entitlement(
        db=store,
        admin_user=admin_user,
        target_user=user,
        module_key=module_key,
        expires_at=payload.expires_at,
        add_days=payload.add_days,
        revoke=payload.revoke,
        notes=payload.notes,
    )
    fresh_user = store.get_user(user_id) or user
    return _build_student_rows(store, [fresh_user])[0]


@router.post("/users/{user_id}/recharge-ai", response_model=AdminStudentStatsRow)
def recharge_ai_credits(
    user_id: str,
    payload: AdminRechargeAIRequest,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> AdminStudentStatsRow:
    store = as_store(db)
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    recharge_user_ai_quota(
        db=store,
        admin_user=admin_user,
        target_user=user,
        add_credits=payload.add_credits,
        notes=payload.notes,
        daily_38_limit=payload.daily_38_limit,
    )
    fresh_user = store.get_user(user_id) or user
    return _build_student_rows(store, [fresh_user])[0]


@router.patch("/users/{user_id}/project-identity", response_model=AdminStudentStatsRow)
def override_student_project_identity(
    user_id: str,
    payload: AdminUpdateProjectIdentityRequest,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> AdminStudentStatsRow:
    store = as_store(db)
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    proj_meta = store.get_latest_project_meta_for_user(user_id)
    if proj_meta is None:
        raise HTTPException(status_code=404, detail="Cet utilisateur n'a aucun protocole enregistré.")

    proj = project_storage.get(proj_meta.project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Protocole introuvable.")

    old_vals = {
        "title": proj.cover.title,
        "author_name": proj.cover.author_name,
        "matricule": proj.cover.matricule,
        "topic_locked": proj.topic_locked,
    }

    if payload.title is not None:
        clean_title = payload.title.strip()
        proj.name = clean_title
        proj.cover.title = clean_title
        proj.locked_title = clean_title
    if payload.author_name is not None:
        clean_auth = payload.author_name.strip()
        proj.cover.author_name = clean_auth
        proj.locked_author_name = clean_auth
    if payload.matricule is not None:
        clean_mat = payload.matricule.strip()
        proj.cover.matricule = clean_mat
        proj.locked_matricule = clean_mat
    if payload.topic_locked is not None:
        proj.topic_locked = payload.topic_locked

    project_storage.save(proj)

    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=user.id,
        action="override_project_identity",
        details_json={
            "project_id": proj.project_id,
            "old": old_vals,
            "new": {
                "title": proj.cover.title,
                "author_name": proj.cover.author_name,
                "matricule": proj.cover.matricule,
                "topic_locked": proj.topic_locked,
            },
            "reason": payload.reason.strip(),
        },
        created_at=utcnow(),
    )
    fresh_user = store.get_user(user_id) or user
    return _build_student_rows(store, [fresh_user])[0]


@router.patch("/users/{user_id}/cover", response_model=AdminStudentStatsRow)
def update_user_full_cover(
    user_id: str,
    payload: AdminUpdateUserCoverRequest,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> AdminStudentStatsRow:
    store = as_store(db)
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    next_cover = payload.cover.model_copy(deep=True)
    if not next_cover.author_name.strip():
        next_cover.author_name = user.full_name
    if not next_cover.matricule.strip() and user.matricule:
        next_cover.matricule = user.matricule
    if not next_cover.student_phone.strip() and user.phone:
        next_cover.student_phone = user.phone
    if not next_cover.student_email.strip() and user.email and "@" in user.email:
        next_cover.student_email = user.email

    proj_meta = store.get_latest_project_meta_for_user(user_id)

    if proj_meta is None:
        proj_title = (next_cover.title or "").strip() or f"Protocole de {user.full_name}"
        next_cover.title = proj_title
        proj = project_storage.create(
            CreateProjectRequest(
                name=proj_title,
                study_type=(payload.study_type or "").strip() or "Étude transversale analytique",
                cover=next_cover,
            ),
            owner_user_id=user.id,
            owner_role="student" if user.role != "admin" else "admin",
            owner_full_name=next_cover.author_name or user.full_name,
            owner_matricule=next_cover.matricule or user.matricule,
        )
        if payload.topic_locked is not None:
            proj.topic_locked = payload.topic_locked
    else:
        proj = project_storage.get(proj_meta.project_id)
        if proj is None:
            raise HTTPException(status_code=404, detail="Protocole introuvable.")

        existing_cover = proj.cover
        for path_attr, url_attr in (
            ("university_logo_path", "university_logo_url"),
            ("faculty_logo_path", "faculty_logo_url"),
        ):
            if not getattr(next_cover, path_attr) and getattr(existing_cover, path_attr):
                setattr(next_cover, path_attr, getattr(existing_cover, path_attr))
            if not getattr(next_cover, url_attr) and getattr(existing_cover, url_attr):
                setattr(next_cover, url_attr, getattr(existing_cover, url_attr))

        proj.cover = next_cover
        if (next_cover.title or "").strip():
            proj.name = next_cover.title.strip()
        proj.locked_title = (next_cover.title or proj.name or "").strip()
        proj.locked_author_name = (next_cover.author_name or "").strip()
        proj.locked_matricule = (next_cover.matricule or "").strip()
        if payload.study_type is not None and payload.study_type.strip():
            proj.study_type = payload.study_type.strip()
        if payload.topic_locked is not None:
            proj.topic_locked = payload.topic_locked

    if "cover_page" in proj.sections:
        proj.sections["cover_page"].status = StepStatus.VALIDATED
        proj.sections["cover_page"].content_markdown = (proj.cover.title or proj.name or "").strip()

    saved_proj = project_storage.save(proj)
    update_section_working_memory(saved_proj, "cover_page")

    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=user.id,
        action="update_student_cover",
        details_json={
            "project_id": saved_proj.project_id,
            "title": saved_proj.cover.title,
            "author_name": saved_proj.cover.author_name,
            "matricule": saved_proj.cover.matricule,
            "university_fr": saved_proj.cover.university_fr,
            "faculty_fr": saved_proj.cover.faculty_fr,
            "department_fr": saved_proj.cover.department_fr,
            "reason": payload.reason.strip(),
        },
        created_at=utcnow(),
    )
    fresh_user = store.get_user(user_id) or user
    return _build_student_rows(store, [fresh_user])[0]


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    admin_user: UserRecord = Depends(require_admin),
    db: Any = Depends(get_db),
) -> Dict[str, Any]:
    if user_id == admin_user.id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas supprimer votre propre compte.")
    store = as_store(db)
    deleted_email = store.delete_user_cascade(user_id)
    if deleted_email is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=None,
        action="delete_user",
        details_json={"deleted_user_id": user_id, "deleted_email": deleted_email},
        created_at=utcnow(),
    )
    return {"deleted": True, "user_id": user_id}


# =========================================================================
# STUDENT USAGE ANALYTICS, USAGE EVENTS & AUDIT LOGS
# =========================================================================
@router.get("/stats/students", response_model=List[AdminStudentStatsRow])
def get_student_stats(
    search: Optional[str] = Query(default=None),
    sort_by: str = Query(default="last_login"),
    db: Any = Depends(get_db),
) -> List[AdminStudentStatsRow]:
    store = as_store(db)
    students = store.list_users(role="student")
    rows = _build_student_rows(store, students)

    if search and search.strip():
        needle = search.strip().casefold()
        rows = [
            r
            for r in rows
            if needle in r.email.casefold()
            or needle in r.full_name.casefold()
            or needle in r.matricule.casefold()
            or needle in (r.project_title or "").casefold()
        ]

    if sort_by == "sections":
        rows.sort(key=lambda r: r.sections_validated_count, reverse=True)
    elif sort_by == "patients":
        rows.sort(key=lambda r: r.patients_count, reverse=True)
    elif sort_by == "ai_used":
        rows.sort(key=lambda r: r.ai_quota.credits_used, reverse=True)
    elif sort_by == "exports":
        rows.sort(key=lambda r: r.exports_count, reverse=True)
    else:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        rows.sort(key=lambda r: r.last_login_at or epoch, reverse=True)

    return rows


@router.get("/usage-events", response_model=List[UsageEventOut])
def list_usage_events(
    user_id: Optional[str] = Query(default=None),
    module_key: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Any = Depends(get_db),
) -> List[UsageEventOut]:
    store = as_store(db)
    events = store.list_usage_events(user_id=user_id, module_key=module_key, limit=limit)

    uids = list({e.user_id for e in events})
    emails = {uid: u.email for uid, u in store.get_users_by_ids(uids).items()} if uids else {}
    now = datetime.now(timezone.utc)
    return [
        UsageEventOut(
            id=e.id,
            user_id=e.user_id,
            user_email=emails.get(e.user_id, ""),
            project_id=e.project_id,
            module_key=e.module_key,
            action=e.action,
            model_used=e.model_used,
            credits_cost=e.credits_cost,
            status=e.status,
            ip_address=e.ip_address,
            created_at=ensure_utc(e.created_at) or now,
        )
        for e in events
    ]


@router.get("/audit-logs", response_model=List[AdminAuditLogOut])
def list_admin_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    db: Any = Depends(get_db),
) -> List[AdminAuditLogOut]:
    store = as_store(db)
    logs = store.list_audit_logs(limit=limit)
    uids = list({x for l in logs for x in (l.admin_id, l.target_user_id) if x})
    emails = {uid: u.email for uid, u in store.get_users_by_ids(uids).items()} if uids else {}
    now = datetime.now(timezone.utc)
    return [
        AdminAuditLogOut(
            id=l.id,
            admin_id=l.admin_id,
            admin_email=emails.get(l.admin_id or "", ""),
            target_user_id=l.target_user_id,
            target_email=emails.get(l.target_user_id or "", ""),
            action=l.action,
            details_json=l.details_json or {},
            created_at=ensure_utc(l.created_at) or now,
        )
        for l in logs
    ]
