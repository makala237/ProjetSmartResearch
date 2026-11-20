from datetime import datetime, timezone
import math
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from fastapi import HTTPException
import jwt

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    ensure_utc,
    hash_password,
    verify_password,
)
from app.db.datastore import UserRecord, as_store
from app.schemas.auth import AIQuotaOut, ModuleEntitlementOut, UserMeOut
from app.services.licensing_service import get_student_daily_38_usage

MODULE_KEYS = ("module1", "module2", "module3", "module4")


def create_user_account(
    db: Any,
    email: str,
    full_name: str,
    password: str,
    role: str = "student",
    matricule: str = "",
    phone: str = "",
    must_change_password: bool = True,
    created_by_admin_id: Optional[str] = None,
) -> UserRecord:
    """Create a user account with Argon2id hash and initial AI quota record."""
    store = as_store(db)
    return store.create_user(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        role=role,
        matricule=matricule,
        phone=phone,
        must_change_password=must_change_password,
        created_by_admin_id=created_by_admin_id,
    )


def authenticate_and_login(
    db: Any,
    email: str,
    password: str,
    ip_address: str = "",
) -> Tuple[UserRecord, str, str]:
    """Authenticate credentials and rotate active_session_id (strict single-session)."""
    store = as_store(db)
    raw_ident = (email or "").strip()
    norm_ident = raw_ident.lower()
    user = store.get_user_by_email(norm_ident) if norm_ident else None
    if user is None and raw_ident:
        matches = store.find_users_by_full_name(raw_ident)
        if len(matches) > 1:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "AMBIGUOUS_USERNAME",
                    "message": (
                        "Plusieurs comptes portent ce nom. "
                        "Veuillez vous connecter avec votre nom d'utilisateur ou e-mail."
                    ),
                },
            )
        if len(matches) == 1:
            user = matches[0]
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": "Identifiant ou mot de passe incorrect.",
            },
        )
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ACCOUNT_DISABLED",
                "message": "Votre compte est désactivé. Contactez l'administrateur.",
            },
        )

    new_sid = str(uuid4())
    rotated = store.rotate_session_login(
        user_id=user.id,
        new_sid=new_sid,
        ip_address=ip_address or "",
        now=datetime.now(timezone.utc),
    )
    user = rotated or user

    access_token = create_access_token(user.id, user.role, new_sid)
    refresh_token = create_refresh_token(user.id, user.role, new_sid)
    return user, access_token, refresh_token


def verify_session_token(
    db: Any,
    token: str,
    expected_type: str = "access",
) -> UserRecord:
    """Decode JWT token and verify user active state plus single active_session_id."""
    store = as_store(db)
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "TOKEN_EXPIRED", "message": "Session expirée. Veuillez vous reconnecter."},
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_TOKEN", "message": "Jeton d'authentification invalide."},
        ) from exc

    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_TOKEN_TYPE", "message": "Type de jeton invalide."},
        )

    user_id = payload.get("sub")
    sid = payload.get("sid")
    user = store.get_user(user_id) if user_id else None
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "USER_NOT_FOUND", "message": "Compte utilisateur introuvable."},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail={"code": "ACCOUNT_DISABLED", "message": "Compte désactivé par l'administrateur."},
        )
    if not user.active_session_id or user.active_session_id != sid:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "SESSION_REVOKED",
                "message": "Session révoquée ou ouverte sur un autre appareil. Veuillez vous reconnecter.",
            },
        )
    return user


def refresh_session_tokens(db: Any, refresh_token: str) -> Tuple[UserRecord, str, str]:
    """Validate refresh token and rotate session tokens."""
    store = as_store(db)
    user = verify_session_token(store, refresh_token, expected_type="refresh")
    new_sid = str(uuid4())
    updated = store.update_user(user.id, active_session_id=new_sid) or user
    return (
        updated,
        create_access_token(updated.id, updated.role, new_sid),
        create_refresh_token(updated.id, updated.role, new_sid),
    )


def change_user_password(
    db: Any,
    user: Any,
    current_password: str,
    new_password: str,
) -> Tuple[str, str]:
    """Verify current password, store new Argon2id hash, clear must_change_password, rotate sid."""
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_CURRENT_PASSWORD",
                "message": "Le mot de passe actuel est incorrect.",
            },
        )
    clean_new = new_password.strip()
    if len(clean_new) < 8:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "WEAK_PASSWORD",
                "message": "Le nouveau mot de passe doit contenir au moins 8 caractères.",
            },
        )

    store = as_store(db)
    new_sid = str(uuid4())
    new_hash = hash_password(new_password)
    store.update_user(
        user.id,
        password_hash=new_hash,
        must_change_password=False,
        active_session_id=new_sid,
    )
    user.password_hash = new_hash
    user.must_change_password = False
    user.active_session_id = new_sid
    return (
        create_access_token(user.id, user.role, new_sid),
        create_refresh_token(user.id, user.role, new_sid),
    )


def logout_user(db: Any, user: Any) -> None:
    """Invalidate active_session_id immediately."""
    as_store(db).update_user(user.id, active_session_id=None)
    user.active_session_id = None


def build_user_me_out(db: Any, user: Any) -> UserMeOut:
    """Hydrate authenticated user profile with module entitlements, AI quota and project link."""
    store = as_store(db)
    now = datetime.now(timezone.utc)
    rows = store.list_entitlements(user.id)
    by_key = {r.module_key: r for r in rows}

    entitlements: Dict[str, ModuleEntitlementOut] = {}
    for mk in MODULE_KEYS:
        row = by_key.get(mk)
        if user.role == "admin":
            entitlements[mk] = ModuleEntitlementOut(
                module_key=mk,
                starts_at=ensure_utc(row.starts_at) if row else None,
                expires_at=ensure_utc(row.expires_at) if row else None,
                is_active=True,
                days_remaining=9999,
            )
            continue

        if row is None:
            entitlements[mk] = ModuleEntitlementOut(module_key=mk, is_active=False, days_remaining=0)
            continue

        exp = ensure_utc(row.expires_at)
        starts = ensure_utc(row.starts_at)
        active = bool(exp and exp > now)
        days_rem = max(0, math.ceil((exp - now).total_seconds() / 86400)) if active and exp else 0
        entitlements[mk] = ModuleEntitlementOut(
            module_key=mk,
            starts_at=starts,
            expires_at=exp,
            is_active=active,
            days_remaining=days_rem,
        )

    daily_38 = get_student_daily_38_usage(store, user)
    quota_row = store.get_quota(user.id)
    if quota_row:
        ai_quota = AIQuotaOut(
            credits_total=quota_row.credits_total,
            credits_used=quota_row.credits_used,
            credits_remaining=max(0, quota_row.credits_total - quota_row.credits_used),
            daily_38_limit=daily_38["daily_limit"],
            daily_38_used=daily_38["used_today"],
            daily_38_remaining=daily_38["remaining_today"],
            active_reasoning_model=daily_38["active_reasoning_model"],
            using_35_fallback=daily_38["fallback_active"],
        )
    else:
        ai_quota = AIQuotaOut(
            credits_total=100000 if user.role == "admin" else 0,
            credits_used=0,
            credits_remaining=100000 if user.role == "admin" else 0,
            daily_38_limit=daily_38["daily_limit"],
            daily_38_used=daily_38["used_today"],
            daily_38_remaining=daily_38["remaining_today"],
            active_reasoning_model=daily_38["active_reasoning_model"],
            using_35_fallback=daily_38["fallback_active"],
        )

    first_proj = store.get_latest_project_meta_for_user(user.id)

    return UserMeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        matricule=user.matricule,
        phone=user.phone,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        last_login_at=ensure_utc(user.last_login_at),
        has_project=first_proj is not None,
        project_id=first_proj.project_id if first_proj else None,
        entitlements=entitlements,
        ai_quota=ai_quota,
    )
