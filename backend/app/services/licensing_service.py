from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.security import ensure_utc
from app.db.datastore import (
    UserAIQuotaRecord,
    UserLicenseGrantRecord,
    UserModuleEntitlementRecord,
    as_store,
)
from app.db.models import utcnow
from app.services.gemini_client import (
    consume_request_tokens_used,
    reset_request_tokens_used,
    set_ai_routing_policy,
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
    "harmonize_section": 1,
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
    db: Any,
    user: Any,
    module_key: str,
) -> Tuple[bool, Optional[datetime]]:
    """Return (is_active, expires_at) for the given user and module key."""
    if user.role == "admin":
        return True, None
    store = as_store(db)
    ent = store.get_entitlement(user.id, module_key)
    if ent is None:
        return False, None
    exp = ensure_utc(ent.expires_at)
    now = datetime.now(timezone.utc)
    return bool(exp and exp > now), exp


def assert_any_module_active(db: Any, user: Any) -> None:
    """Require at least one currently active module license for students."""
    if user.role == "admin":
        return
    store = as_store(db)
    now = datetime.now(timezone.utc)
    ents = store.list_entitlements(user.id)
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


def assert_module_write_allowed(db: Any, user: Any, module_key: str) -> None:
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


def get_student_daily_38_usage(db: Any, user: Any) -> Dict[str, Any]:
    """Compute today's primary reasoning model usage for a user (including admin) and whether fallback relay is active."""
    store = as_store(db)
    settings = get_settings()
    primary = (settings.gemini_primary_reasoning_model or "gemini-3.5-flash").strip()
    fallback = (settings.gemini_fallback_reasoning_model or "gemini-3.1-flash-lite").strip()

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    quota = store.get_quota(user.id)
    configured_limit = quota.daily_38_limit if (quota and quota.daily_38_limit is not None) else None
    limit = int(configured_limit if configured_limit is not None else settings.student_daily_gemini38_limit)

    used_today = store.count_user_gemini38_today(user.id, day_start)

    fallback_active = bool(limit > 0 and used_today >= limit)
    remaining_today = max(0, limit - used_today)
    return {
        "daily_limit": limit,
        "used_today": used_today,
        "remaining_today": remaining_today,
        "fallback_active": fallback_active,
        "active_reasoning_model": fallback if fallback_active else primary,
    }


def assert_ai_quota_available(
    db: Any,
    user: Any,
    module_key: str,
    action: str,
    credits_cost: Optional[int] = None,
) -> int:
    """Verify active module entitlement, rate limits (RPM/RPD) for all users, and AI credits before Gemini calls."""
    store = as_store(db)
    if module_key == "general":
        assert_any_module_active(store, user)
    else:
        assert_module_write_allowed(store, user, module_key)

    cost = AI_ACTION_COSTS.get(action, 1) if credits_cost is None else int(credits_cost)

    # 1. Enforce AI rate limits (RPM, RPD, and daily token cap) for ALL users, including administrators
    reset_request_tokens_used()
    settings = get_settings()
    max_rpm = int(getattr(settings, "ai_user_max_rpm", 10) or 0)
    max_rpd = int(getattr(settings, "ai_user_max_rpd", 40) or 0)
    max_tokens_day = int(getattr(settings, "ai_user_max_tokens_per_day", 100_000) or 0)
    if (max_rpm > 0 or max_rpd > 0 or max_tokens_day > 0) and cost > 0:
        now_utc = datetime.now(timezone.utc)
        day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        minute_ago = now_utc - timedelta(seconds=60)
        if max_rpm > 0 or max_rpd > 0:
            used_rpm, used_rpd = store.count_user_ai_calls_since(user.id, day_start, minute_ago)
            if max_rpm > 0 and used_rpm >= max_rpm:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "AI_RATE_LIMIT_RPM",
                        "limit_rpm": max_rpm,
                        "used_rpm": used_rpm,
                        "message": (
                            f"Limite de fréquence atteinte ({used_rpm}/{max_rpm} requêtes IA par minute). "
                            "Veuillez patienter quelques secondes avant de relancer."
                        ),
                    },
                )
            if max_rpd > 0 and used_rpd >= max_rpd:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "AI_RATE_LIMIT_RPD",
                        "limit_rpd": max_rpd,
                        "used_rpd": used_rpd,
                        "message": (
                            f"Plafond journalier atteint ({used_rpd}/{max_rpd} requêtes IA aujourd'hui). "
                            "Votre quota journalier sera réinitialisé demain."
                        ),
                    },
                )
        if max_tokens_day > 0:
            used_tokens_today = store.sum_user_tokens_today(user.id, day_start)
            if used_tokens_today >= max_tokens_day:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "AI_RATE_LIMIT_TOKENS_DAY",
                        "limit_tokens_day": max_tokens_day,
                        "used_tokens_today": used_tokens_today,
                        "message": (
                            f"Plafond journalier de tokens atteint ({used_tokens_today}/{max_tokens_day} tokens consommés aujourd'hui). "
                            "Votre quota journalier sera réinitialisé demain."
                        ),
                    },
                )

    # 2. For non-admin accounts, verify prepaid AI credits balance
    if user.role != "admin" and cost > 0:
        quota = store.ensure_quota(user.id, credits_total=0)
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

    # 3. Apply reasoning policy and daily Gemini 3.8 limit for all users (including admin)
    daily_38 = get_student_daily_38_usage(store, user)
    set_ai_routing_policy(
        prefer_fallback_35=daily_38["fallback_active"],
        reason="student_daily_38_cap" if daily_38["fallback_active"] else "",
        user_id=user.id,
        daily_status=daily_38,
    )
    return max(0, cost)


def record_usage_and_debit(
    db: Any,
    user: Any,
    module_key: str,
    action: str,
    credits_cost: int = 0,
    project_id: Optional[str] = None,
    model_used: str = "",
    status: str = "ok",
    ip_address: str = "",
    tokens_used: Optional[int] = None,
) -> None:
    """Debit AI credits (on successful non-admin action) and append an immutable telemetry event."""
    store = as_store(db)
    now = utcnow()
    effective_cost = max(0, int(credits_cost))
    effective_tokens = max(0, int(tokens_used)) if tokens_used is not None else consume_request_tokens_used()
    if effective_cost > 0 and user.role != "admin" and status == "ok":
        store.debit_quota_atomic(user.id, effective_cost, now)

    store.create_usage_event(
        user_id=user.id,
        project_id=project_id,
        module_key=module_key,
        action=action,
        model_used=(model_used or "")[:128],
        credits_cost=effective_cost if (status == "ok") else 0,
        tokens_used=effective_tokens if (status == "ok") else 0,
        status=status[:32],
        ip_address=(ip_address or "")[:64],
        created_at=now,
    )


def apply_license_plan_to_user(
    db: Any,
    admin_user: Any,
    target_user: Any,
    plan: Any,
    amount_paid_fcfa: Optional[int] = None,
    payment_reference: str = "",
    notes: str = "",
) -> UserLicenseGrantRecord:
    """Apply a LicensePlan to a user, stacking durations onto unexpired modules and adding AI credits."""
    store = as_store(db)
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
        ent = store.get_entitlement(target_user.id, mk)
        if ent is None:
            starts_at = now
            new_exp = now + timedelta(days=days)
        else:
            current_exp = ensure_utc(ent.expires_at) or now
            base = current_exp if current_exp > now else now
            starts_at = now if current_exp <= now else (ensure_utc(ent.starts_at) or now)
            new_exp = base + timedelta(days=days)
        store.upsert_entitlement(
            user_id=target_user.id,
            module_key=mk,
            starts_at=starts_at,
            expires_at=new_exp,
            updated_at=now,
        )
        applied_windows[mk] = new_exp.isoformat()

    ai_added = max(0, int(plan.ai_quota_included or 0))
    store.add_quota_credits_atomic(
        user_id=target_user.id,
        add_credits=ai_added,
        now=now,
        update_daily_limit=False,
    )

    paid = int(plan.price_fcfa if amount_paid_fcfa is None else amount_paid_fcfa)
    grant = store.create_grant(
        user_id=target_user.id,
        plan_id=plan.id,
        granted_by_admin_id=admin_user.id,
        amount_paid_fcfa=max(0, paid),
        payment_reference=payment_reference.strip(),
        notes=notes.strip(),
        granted_at=now,
    )
    store.create_audit_log(
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
    return grant


def adjust_user_module_entitlement(
    db: Any,
    admin_user: Any,
    target_user: Any,
    module_key: str,
    expires_at: Optional[datetime] = None,
    add_days: Optional[int] = None,
    revoke: bool = False,
    notes: str = "",
) -> UserModuleEntitlementRecord:
    """Manually extend, set exact expiry, or revoke a single module license for a user."""
    if module_key not in MODULE_KEYS:
        raise HTTPException(status_code=400, detail=f"Module invalide : {module_key}")

    store = as_store(db)
    now = utcnow()
    ent = store.get_entitlement(target_user.id, module_key)
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
        starts_at = now
    elif not revoke and (ensure_utc(ent.expires_at) or now) <= now:
        starts_at = now
    else:
        starts_at = ensure_utc(ent.starts_at) or now

    updated = store.upsert_entitlement(
        user_id=target_user.id,
        module_key=module_key,
        starts_at=starts_at,
        expires_at=target_exp,
        updated_at=now,
    )

    store.create_audit_log(
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
    return updated


def recharge_user_ai_quota(
    db: Any,
    admin_user: Any,
    target_user: Any,
    add_credits: int,
    notes: str = "",
    daily_38_limit: Optional[int] = None,
) -> UserAIQuotaRecord:
    """Adjust or top up a user's AI credit allowance (and optional daily Gemini 3.8 cap) and record an admin audit entry."""
    store = as_store(db)
    now = utcnow()
    quota, old_total, old_daily = store.add_quota_credits_atomic(
        user_id=target_user.id,
        add_credits=int(add_credits),
        now=now,
        daily_38_limit=daily_38_limit,
        update_daily_limit=(daily_38_limit is not None),
    )

    store.create_audit_log(
        admin_id=admin_user.id,
        target_user_id=target_user.id,
        action="recharge_ai_quota",
        details_json={
            "add_credits": int(add_credits),
            "old_total": old_total,
            "new_total": quota.credits_total,
            "credits_used": int(quota.credits_used or 0),
            "old_daily_38_limit": old_daily,
            "new_daily_38_limit": quota.daily_38_limit,
            "notes": notes.strip(),
        },
        created_at=now,
    )
    return quota
