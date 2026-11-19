from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import ensure_utc
from app.db.models import (
    AdminAuditLogORM,
    LicensePlanORM,
    UsageEventORM,
    UserAIQuotaORM,
    UserLicenseGrantORM,
    UserModuleEntitlementORM,
    UserORM,
    utcnow,
)

MODULE_KEYS: Tuple[str, ...] = ("module1", "module2", "module3", "module4")

MODULE_LABELS: Dict[str, str] = {
    "module1": "Module 1 (Rédaction & Recherche)",
    "module2": "Module 2 (Édition Word / PDF)",
    "module3": "Module 3 (Masque de Saisie Patients)",
    "module4": "Module 4 (Diaporama PowerPoint)",
}

AI_ACTION_COSTS: Dict[str, int] = {
    "translate_department": 1,
    "audit_section": 1,
    "suggest_table": 1,
    "suggest_diagram": 1,
    "generate_toc": 1,
    "pptx_generate_slide": 1,
    "pptx_audit": 1,
    "humanize_section": 1,
    "research": 2,
    "segment": 2,
    "draft_section": 2,
    "fix_section": 2,
    "antigravity_section": 2,
    "import_protocol": 2,
    "global_audit": 2,
    "validate_references": 2,
    "humanize_protocol": 3,
    "pptx_generate_all": 5,
}


def is_module_active(
    db: Session,
    user: UserORM,
    module_key: str,
) -> Tuple[bool, Optional[datetime]]:
    """Return (is_active, expires_at) for the given user and module key."""
    if user.role == "admin":
        return True, None
    ent = (
        db.query(UserModuleEntitlementORM)
        .filter(
            UserModuleEntitlementORM.user_id == user.id,
            UserModuleEntitlementORM.module_key == module_key,
        )
        .first()
    )
    if ent is None:
        return False, None
    exp = ensure_utc(ent.expires_at)
    now = datetime.now(timezone.utc)
    return bool(exp and exp > now), exp


def assert_any_module_active(db: Session, user: UserORM) -> None:
    """Require at least one currently active module license for students."""
    if user.role == "admin":
        return
    now = datetime.now(timezone.utc)
    ents = (
        db.query(UserModuleEntitlementORM)
        .filter(UserModuleEntitlementORM.user_id == user.id)
        .all()
    )
    for ent in ents:
        exp = ensure_utc(ent.expires_at)
        if exp and exp > now:
            return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "NO_ACTIVE_LICENSE",
            "message": "Aucune licence active n'est associée à votre compte. Contactez l'administrateur.",
        },
    )


def assert_module_write_allowed(db: Session, user: UserORM, module_key: str) -> None:
    """Block mutations/AI on expired modules while preserving read-only + export access."""
    if module_key not in MODULE_KEYS:
        raise ValueError(f"Module inconnu : {module_key}")
    if user.role == "admin":
        return

    active, exp = is_module_active(db, user, module_key)
    if active:
        return

    label = MODULE_LABELS.get(module_key, module_key)
    raise HTTPException(
        status_code=403,
        detail={
            "code": "MODULE_EXPIRED",
            "module_key": module_key,
            "expires_at": exp.isoformat() if exp else None,
            "message": (
                f"Votre accès en écriture au {label} a expiré. "
                "Vous restez en lecture seule avec téléchargement autorisé. "
                "Contactez l'administrateur pour renouveler."
            ),
        },
    )


def assert_ai_quota_available(
    db: Session,
    user: UserORM,
    module_key: str,
    action: str,
    credits_cost: Optional[int] = None,
) -> int:
    """Verify both active module write entitlement and sufficient AI credits before Gemini calls."""
    if module_key == "general":
        assert_any_module_active(db, user)
    else:
        assert_module_write_allowed(db, user, module_key)

    cost = AI_ACTION_COSTS.get(action, 1) if credits_cost is None else int(credits_cost)
    if user.role == "admin" or cost <= 0:
        return max(0, cost)

    quota = db.get(UserAIQuotaORM, user.id)
    if quota is None:
        quota = UserAIQuotaORM(user_id=user.id, credits_total=0, credits_used=0, updated_at=utcnow())
        db.add(quota)
        db.commit()
        db.refresh(quota)

    remaining = max(0, quota.credits_total - quota.credits_used)
    if remaining < cost:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AI_QUOTA_EXCEEDED",
                "credits_required": cost,
                "credits_remaining": remaining,
                "message": (
                    f"Crédits IA insuffisants ({remaining} restant(s), {cost} requis). "
                    "Contactez l'administrateur pour recharger votre quota."
                ),
            },
        )
    return cost


def record_usage_and_debit(
    db: Session,
    user: UserORM,
    module_key: str,
    action: str,
    credits_cost: int = 0,
    project_id: Optional[str] = None,
    model_used: str = "",
    status: str = "ok",
    ip_address: str = "",
) -> None:
    """Debit AI credits (on successful non-admin action) and append an immutable telemetry event."""
    now = utcnow()
    effective_cost = max(0, int(credits_cost))
    if effective_cost > 0 and user.role != "admin" and status == "ok":
        quota = db.get(UserAIQuotaORM, user.id)
        if quota is None:
            quota = UserAIQuotaORM(user_id=user.id, credits_total=0, credits_used=0, updated_at=now)
            db.add(quota)
            db.flush()
        quota.credits_used = int(quota.credits_used or 0) + effective_cost
        quota.updated_at = now

    ev = UsageEventORM(
        user_id=user.id,
        project_id=project_id,
        module_key=module_key,
        action=action,
        model_used=(model_used or "")[:128],
        credits_cost=effective_cost if (status == "ok") else 0,
        status=status[:32],
        ip_address=(ip_address or "")[:64],
        created_at=now,
    )
    db.add(ev)
    db.commit()


def apply_license_plan_to_user(
    db: Session,
    admin_user: UserORM,
    target_user: UserORM,
    plan: LicensePlanORM,
    amount_paid_fcfa: Optional[int] = None,
    payment_reference: str = "",
    notes: str = "",
) -> UserLicenseGrantORM:
    """Apply a LicensePlan to a user, stacking durations onto unexpired modules and adding AI credits."""
    now = utcnow()
    days_by_module = {
        "module1": int(plan.module1_days or 0),
        "module2": int(plan.module2_days or 0),
        "module3": int(plan.module3_days or 0),
        "module4": int(plan.module4_days or 0),
    }

    applied_windows: Dict[str, str] = {}
    for mk, days in days_by_module.items():
        if days <= 0:
            continue
        ent = (
            db.query(UserModuleEntitlementORM)
            .filter(
                UserModuleEntitlementORM.user_id == target_user.id,
                UserModuleEntitlementORM.module_key == mk,
            )
            .first()
        )
        if ent is None:
            new_exp = now + timedelta(days=days)
            ent = UserModuleEntitlementORM(
                user_id=target_user.id,
                module_key=mk,
                starts_at=now,
                expires_at=new_exp,
                updated_at=now,
            )
            db.add(ent)
        else:
            current_exp = ensure_utc(ent.expires_at) or now
            base = current_exp if current_exp > now else now
            if current_exp <= now:
                ent.starts_at = now
            new_exp = base + timedelta(days=days)
            ent.expires_at = new_exp
            ent.updated_at = now
        applied_windows[mk] = new_exp.isoformat()

    quota = db.get(UserAIQuotaORM, target_user.id)
    if quota is None:
        quota = UserAIQuotaORM(
            user_id=target_user.id,
            credits_total=max(0, int(plan.ai_quota_included or 0)),
            credits_used=0,
            updated_at=now,
        )
        db.add(quota)
    else:
        quota.credits_total = int(quota.credits_total or 0) + max(0, int(plan.ai_quota_included or 0))
        quota.updated_at = now

    paid = int(plan.price_fcfa if amount_paid_fcfa is None else amount_paid_fcfa)
    grant = UserLicenseGrantORM(
        user_id=target_user.id,
        plan_id=plan.id,
        granted_by_admin_id=admin_user.id,
        amount_paid_fcfa=max(0, paid),
        payment_reference=payment_reference.strip(),
        notes=notes.strip(),
        granted_at=now,
    )
    db.add(grant)
    db.add(
        AdminAuditLogORM(
            admin_id=admin_user.id,
            target_user_id=target_user.id,
            action="grant_license_plan",
            details_json={
                "plan_id": plan.id,
                "plan_name": plan.name,
                "amount_paid_fcfa": max(0, paid),
                "payment_reference": payment_reference.strip(),
                "ai_quota_added": int(plan.ai_quota_included or 0),
                "applied_windows": applied_windows,
                "notes": notes.strip(),
            },
            created_at=now,
        )
    )
    db.commit()
    db.refresh(grant)
    return grant


def adjust_user_module_entitlement(
    db: Session,
    admin_user: UserORM,
    target_user: UserORM,
    module_key: str,
    expires_at: Optional[datetime] = None,
    add_days: Optional[int] = None,
    revoke: bool = False,
    notes: str = "",
) -> UserModuleEntitlementORM:
    """Manually extend, set exact expiry, or revoke a single module license for a user."""
    if module_key not in MODULE_KEYS:
        raise HTTPException(status_code=400, detail=f"Module invalide : {module_key}")

    now = utcnow()
    ent = (
        db.query(UserModuleEntitlementORM)
        .filter(
            UserModuleEntitlementORM.user_id == target_user.id,
            UserModuleEntitlementORM.module_key == module_key,
        )
        .first()
    )
    old_exp = ensure_utc(ent.expires_at).isoformat() if ent and ent.expires_at else None

    if revoke:
        target_exp = now - timedelta(seconds=1)
    elif expires_at is not None:
        target_exp = ensure_utc(expires_at) or now
    elif add_days is not None:
        current_exp = ensure_utc(ent.expires_at) if ent and ent.expires_at else now
        base = current_exp if (current_exp and current_exp > now) else now
        target_exp = base + timedelta(days=int(add_days))
    else:
        raise HTTPException(
            status_code=400,
            detail="Spécifiez revoke=true, expires_at ou add_days.",
        )

    if ent is None:
        ent = UserModuleEntitlementORM(
            user_id=target_user.id,
            module_key=module_key,
            starts_at=now,
            expires_at=target_exp,
            updated_at=now,
        )
        db.add(ent)
    else:
        if not revoke and (ensure_utc(ent.expires_at) or now) <= now:
            ent.starts_at = now
        ent.expires_at = target_exp
        ent.updated_at = now

    db.add(
        AdminAuditLogORM(
            admin_id=admin_user.id,
            target_user_id=target_user.id,
            action="adjust_module_entitlement",
            details_json={
                "module_key": module_key,
                "revoke": revoke,
                "add_days": add_days,
                "old_expires_at": old_exp,
                "new_expires_at": target_exp.isoformat(),
                "notes": notes.strip(),
            },
            created_at=now,
        )
    )
    db.commit()
    db.refresh(ent)
    return ent


def recharge_user_ai_quota(
    db: Session,
    admin_user: UserORM,
    target_user: UserORM,
    add_credits: int,
    notes: str = "",
) -> UserAIQuotaORM:
    """Adjust or top up a user's AI credit allowance and record an admin audit entry."""
    now = utcnow()
    quota = db.get(UserAIQuotaORM, target_user.id)
    if quota is None:
        quota = UserAIQuotaORM(user_id=target_user.id, credits_total=0, credits_used=0, updated_at=now)
        db.add(quota)
        db.flush()

    old_total = int(quota.credits_total or 0)
    quota.credits_total = max(int(quota.credits_used or 0), old_total + int(add_credits))
    quota.updated_at = now

    db.add(
        AdminAuditLogORM(
            admin_id=admin_user.id,
            target_user_id=target_user.id,
            action="recharge_ai_quota",
            details_json={
                "add_credits": int(add_credits),
                "old_total": old_total,
                "new_total": quota.credits_total,
                "credits_used": int(quota.credits_used or 0),
                "notes": notes.strip(),
            },
            created_at=now,
        )
    )
    db.commit()
    db.refresh(quota)
    return quota
