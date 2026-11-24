from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.request_lang import bi, get_request_lang
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
    get_ai_routing_policy,
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

MODULE_LABELS_EN: Dict[str, str] = {
    "module1": "Module 1 (Drafting & Research)",
    "module2": "Module 2 (Word / PDF Editing)",
    "module3": "Module 3 (Patient Data Entry Mask)",
    "module4": "Module 4 (PowerPoint Presentation)",
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
    "extract_crf": 1,
    "generate_sub_annex": 1,
    "generate_crf": 2,
    "humanize_protocol": 3,
    "pptx_generate_all": 5,
    "copilot_chat": 1,
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
            "message": bi(
                "Aucune licence active n'est associée à votre compte. Contactez l'administrateur.",
                "No active license is associated with your account. Please contact the administrator.",
            ),
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

    label = (MODULE_LABELS_EN if get_request_lang() == "en" else MODULE_LABELS).get(module_key, module_key)
    raise HTTPException(
        status_code=403,
        detail={
            "code": "MODULE_EXPIRED",
            "module_key": module_key,
            "expires_at": exp.isoformat() if exp else None,
            "message": bi(
                f"Votre accès en écriture au {label} a expiré. "
                "Vous restez en lecture seule avec téléchargement autorisé. "
                "Contactez l'administrateur pour renouveler.",
                f"Your write access to {label} has expired. "
                "You remain in read-only mode with downloads allowed. "
                "Please contact the administrator to renew.",
            ),
        },
    )


def get_student_daily_38_usage(db: Any, user: Any) -> Dict[str, Any]:
    """Compute today's primary reasoning model usage for a user (including admin) and whether fallback relay is active."""
    store = as_store(db)
    settings = get_settings()
    primary = (settings.gemini_primary_reasoning_model or "gemini-3.5-flash-lite").strip()
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

    # 1. Enforce AI rate limits (RPM, TPM, RPD, and optional daily token cap) for ALL users
    reset_request_tokens_used()
    settings = get_settings()
    max_rpm = int(getattr(settings, "ai_user_max_rpm", 10) or 0)
    max_rpd = int(getattr(settings, "ai_user_max_rpd", 250) or 0)
    max_tpm = int(getattr(settings, "ai_user_max_tpm", 100_000) or 0)
    max_tokens_day = int(getattr(settings, "ai_user_max_tokens_per_day", 0) or 0)
    if (max_rpm > 0 or max_rpd > 0 or max_tpm > 0 or max_tokens_day > 0) and cost > 0:
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
                        "message": bi(
                            f"Limite de fréquence atteinte ({used_rpm}/{max_rpm} requêtes IA par minute). "
                            "Veuillez patienter quelques secondes avant de relancer.",
                            f"Rate limit reached ({used_rpm}/{max_rpm} AI requests per minute). "
                            "Please wait a few seconds before retrying.",
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
                        "message": bi(
                            f"Plafond journalier atteint ({used_rpd}/{max_rpd} requêtes IA aujourd'hui). "
                            "Votre quota journalier sera réinitialisé demain.",
                            f"Daily request cap reached ({used_rpd}/{max_rpd} AI requests today). "
                            "Your daily quota will reset tomorrow.",
                        ),
                    },
                )
        if max_tpm > 0:
            used_tokens_min = store.sum_user_tokens_today(user.id, minute_ago)
            if used_tokens_min >= max_tpm:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "AI_RATE_LIMIT_TPM",
                        "limit_tpm": max_tpm,
                        "used_tpm": used_tokens_min,
                        "message": bi(
                            f"Limite de débit atteinte ({used_tokens_min}/{max_tpm} tokens IA par minute). "
                            "Veuillez patienter quelques secondes avant de relancer.",
                            f"Token throughput limit reached ({used_tokens_min}/{max_tpm} AI tokens per minute). "
                            "Please wait a few seconds before retrying.",
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
                        "message": bi(
                            f"Plafond journalier de tokens atteint ({used_tokens_today}/{max_tokens_day} tokens consommés aujourd'hui). "
                            "Votre quota journalier sera réinitialisé demain.",
                            f"Daily token cap reached ({used_tokens_today}/{max_tokens_day} tokens used today). "
                            "Your daily quota will reset tomorrow.",
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
                    "message": bi(
                        f"Crédits IA insuffisants ({remaining} restant(s), {cost} requis). "
                        "Contactez l'administrateur pour recharger votre quota.",
                        f"Insufficient AI credits ({remaining} remaining, {cost} required). "
                        "Please contact the administrator to recharge your quota.",
                    ),
                },
            )

    # 3. Apply reasoning policy, daily cap, per-user Gemini API key, and custom model chain routing
    daily_38 = get_student_daily_38_usage(store, user)
    eff_key_id, eff_raw_key = resolve_user_gemini_api_key(store, user)
    custom_chain = resolve_user_model_chain(store, user)

    active_override_models = (
        custom_chain["copilot_text_models"]
        if (action == "copilot_chat" and custom_chain.get("has_copilot_text_chain"))
        else custom_chain["eligible_models"]
    )
    has_active_override = (
        custom_chain.get("has_copilot_text_chain")
        if (action == "copilot_chat" and custom_chain.get("has_copilot_text_chain"))
        else custom_chain.get("has_main_chain")
    )
    skipped_items = (
        custom_chain.get("copilot_text_skipped", [])
        if (action == "copilot_chat" and custom_chain.get("has_copilot_text_chain"))
        else custom_chain["skipped"]
    )

    if custom_chain["enabled"] and has_active_override and not active_override_models:
        only_tpm = any(item.get("reason") == "tpm_cap" for item in skipped_items)
        if only_tpm:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "AI_MODEL_CHAIN_TPM_EXHAUSTED",
                    "skipped": skipped_items,
                    "message": bi(
                        "Limite de débit (TPM) momentanément atteinte sur tous les modèles configurés pour votre compte. "
                        "Veuillez patienter quelques secondes avant de relancer.",
                        "Token-per-minute (TPM) limit temporarily reached on all models configured for your account. "
                        "Please wait a few seconds before retrying.",
                    ),
                },
            )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "AI_MODEL_CHAIN_RPD_EXHAUSTED",
                "skipped": skipped_items,
                "message": bi(
                    "Plafond journalier (RPD) atteint sur tous les modèles IA configurés pour votre compte aujourd'hui. "
                    "Vos quotas journaliers seront réinitialisés demain.",
                    "Daily request cap (RPD) reached on all AI models configured for your account today. "
                    "Your daily quotas will reset tomorrow.",
                ),
            },
        )
    set_ai_routing_policy(
        prefer_fallback_35=daily_38["fallback_active"],
        reason="student_daily_38_cap" if daily_38["fallback_active"] else "",
        user_id=user.id,
        daily_status=daily_38,
        api_key=eff_raw_key,
        api_key_id=eff_key_id,
        custom_model_chain=custom_chain["eligible_models"],
        custom_chain_enabled=bool(custom_chain["enabled"] and custom_chain.get("has_main_chain")),
        custom_chain_skipped=custom_chain["skipped"],
        copilot_text_chain=custom_chain.get("copilot_text_models", []),
        copilot_voice_chain=custom_chain.get("copilot_voice_models", []),
    )
    return max(0, cost)


def _evaluate_model_entries_against_usage(
    entries: List[Dict[str, Any]],
    usage_by_model: Dict[str, Dict[str, int]],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    eligible: List[str] = []
    skipped: List[Dict[str, Any]] = []
    for entry in entries:
        model_id = str(entry["model"]).strip()
        max_rpd = entry.get("max_rpd")
        max_tpm = entry.get("max_tpm")
        used = usage_by_model.get(model_id.lower(), {"rpd_used": 0, "tpm_used": 0})
        rpd_used = int(used.get("rpd_used", 0))
        tpm_used = int(used.get("tpm_used", 0))

        if max_rpd is not None and rpd_used >= max_rpd:
            skipped.append(
                {
                    "model": model_id,
                    "reason": "rpd_cap",
                    "rpd_used": rpd_used,
                    "max_rpd": max_rpd,
                }
            )
            continue
        if max_tpm is not None and tpm_used >= max_tpm:
            skipped.append(
                {
                    "model": model_id,
                    "reason": "tpm_cap",
                    "tpm_used": tpm_used,
                    "max_tpm": max_tpm,
                }
            )
            continue
        eligible.append(model_id)
    return eligible, skipped


def resolve_user_model_chain(store: Any, user: Any) -> Dict[str, Any]:
    """Evaluate a user's custom ordered model chains (main + Assistant text/voice) against RPD/TPM caps."""
    from app.core.model_catalog import normalize_model_routing_config

    cfg = normalize_model_routing_config(getattr(user, "model_routing_config", None))
    if not cfg:
        return {
            "enabled": False,
            "has_main_chain": False,
            "has_copilot_text_chain": False,
            "eligible_models": [],
            "skipped": [],
            "copilot_text_models": [],
            "copilot_text_skipped": [],
            "copilot_voice_models": [],
            "usage_by_model": {},
        }

    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    minute_ago = now - timedelta(seconds=60)
    usage_by_model = store.get_user_model_usage_windows(user.id, day_start, minute_ago)

    main_entries = list(cfg.get("models") or [])
    text_entries = list(cfg.get("copilot_text_models") or [])
    voice_entries = list(cfg.get("copilot_voice_models") or [])

    eligible, skipped = _evaluate_model_entries_against_usage(main_entries, usage_by_model)
    copilot_text_eligible, copilot_text_skipped = _evaluate_model_entries_against_usage(
        text_entries, usage_by_model
    )
    copilot_voice_eligible, _ = _evaluate_model_entries_against_usage(
        voice_entries, usage_by_model
    )

    return {
        "enabled": True,
        "has_main_chain": bool(main_entries),
        "has_copilot_text_chain": bool(text_entries),
        "eligible_models": eligible,
        "skipped": skipped,
        "copilot_text_models": copilot_text_eligible,
        "copilot_text_skipped": copilot_text_skipped,
        "copilot_voice_models": copilot_voice_eligible,
        "usage_by_model": usage_by_model,
    }


def resolve_user_gemini_api_key(store: Any, user: Any) -> Tuple[str, str]:
    """Resolve (api_key_id, raw_api_key) for a user, safely falling back to system GEMINI_API_KEY."""
    settings = get_settings()
    default_key = (settings.gemini_api_key or "").strip()
    assigned_id = (getattr(user, "gemini_api_key_id", None) or "").strip()
    if assigned_id and assigned_id != "default":
        try:
            rec = store.get_gemini_api_key(assigned_id)
            if rec is not None and rec.is_active and (rec.api_key or "").strip():
                return rec.id, rec.api_key.strip()
        except Exception:
            pass
    return "default", default_key


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

    policy = get_ai_routing_policy()
    api_key_id = str(policy.get("api_key_id") or "").strip()
    if not api_key_id:
        api_key_id, _ = resolve_user_gemini_api_key(store, user)

    store.create_usage_event(
        user_id=user.id,
        project_id=project_id,
        module_key=module_key,
        action=action,
        model_used=(model_used or "")[:128],
        credits_cost=effective_cost if (status == "ok") else 0,
        tokens_used=effective_tokens if (status == "ok") else 0,
        api_key_id=api_key_id or "default",
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
