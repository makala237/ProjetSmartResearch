from datetime import datetime, timezone
import math
from typing import Dict, Optional, Tuple
from uuid import uuid4

from fastapi import HTTPException
import jwt
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    ensure_utc,
    hash_password,
    verify_password,
)
from app.db.models import (
    ProjectORM,
    UserAIQuotaORM,
    UserModuleEntitlementORM,
    UserORM,
    utcnow,
)
from app.schemas.auth import AIQuotaOut, ModuleEntitlementOut, UserMeOut

MODULE_KEYS = ("module1", "module2", "module3", "module4")


def create_user_account(
    db: Session,
    email: str,
    full_name: str,
    password: str,
    role: str = "student",
    matricule: str = "",
    phone: str = "",
    must_change_password: bool = True,
    created_by_admin_id: Optional[str] = None,
) -> UserORM:
    """Create a user account with Argon2id hash and initial AI quota record."""
    norm_email = email.strip().lower()
    existing = db.query(UserORM).filter(UserORM.email == norm_email).first()
    if existing is not None:
        raise ValueError("EMAIL_EXISTS")

    user = UserORM(
        email=norm_email,
        full_name=full_name.strip(),
        matricule=matricule.strip(),
        phone=phone.strip(),
        password_hash=hash_password(password),
        role=role if role in ("admin", "student") else "student",
        is_active=True,
        must_change_password=must_change_password,
        created_by_admin_id=created_by_admin_id,
    )
    db.add(user)
    db.flush()
    db.add(
        UserAIQuotaORM(
            user_id=user.id,
            credits_total=100000 if user.role == "admin" else 0,
            credits_used=0,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def authenticate_and_login(
    db: Session,
    email: str,
    password: str,
    ip_address: str = "",
) -> Tuple[UserORM, str, str]:
    """Authenticate credentials and rotate active_session_id (strict single-session)."""
    norm_email = (email or "").strip().lower()
    user = db.query(UserORM).filter(UserORM.email == norm_email).first()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": "Email ou mot de passe incorrect.",
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

    if user.active_session_id:
        user.session_switch_count = (user.session_switch_count or 0) + 1

    new_sid = str(uuid4())
    user.active_session_id = new_sid
    user.last_login_at = utcnow()
    user.last_login_ip = ip_address or ""
    db.commit()
    db.refresh(user)

    access_token = create_access_token(user.id, user.role, new_sid)
    refresh_token = create_refresh_token(user.id, user.role, new_sid)
    return user, access_token, refresh_token


def verify_session_token(
    db: Session,
    token: str,
    expected_type: str = "access",
) -> UserORM:
    """Decode JWT token and verify user active state plus single active_session_id."""
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
    user = db.get(UserORM, user_id) if user_id else None
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


def refresh_session_tokens(db: Session, refresh_token: str) -> Tuple[UserORM, str, str]:
    """Validate refresh token and rotate session tokens."""
    user = verify_session_token(db, refresh_token, expected_type="refresh")
    new_sid = str(uuid4())
    user.active_session_id = new_sid
    db.commit()
    db.refresh(user)
    return (
        user,
        create_access_token(user.id, user.role, new_sid),
        create_refresh_token(user.id, user.role, new_sid),
    )


def change_user_password(
    db: Session,
    user: UserORM,
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

    new_sid = str(uuid4())
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.active_session_id = new_sid
    db.commit()
    db.refresh(user)
    return (
        create_access_token(user.id, user.role, new_sid),
        create_refresh_token(user.id, user.role, new_sid),
    )


def logout_user(db: Session, user: UserORM) -> None:
    """Invalidate active_session_id immediately."""
    user.active_session_id = None
    db.commit()


def build_user_me_out(db: Session, user: UserORM) -> UserMeOut:
    """Hydrate authenticated user profile with module entitlements, AI quota and project link."""
    now = datetime.now(timezone.utc)
    rows = (
        db.query(UserModuleEntitlementORM)
        .filter(UserModuleEntitlementORM.user_id == user.id)
        .all()
    )
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

    quota_row = db.get(UserAIQuotaORM, user.id)
    if quota_row:
        ai_quota = AIQuotaOut(
            credits_total=quota_row.credits_total,
            credits_used=quota_row.credits_used,
            credits_remaining=max(0, quota_row.credits_total - quota_row.credits_used),
        )
    else:
        ai_quota = AIQuotaOut(
            credits_total=100000 if user.role == "admin" else 0,
            credits_used=0,
            credits_remaining=100000 if user.role == "admin" else 0,
        )

    first_proj = (
        db.query(ProjectORM)
        .filter(ProjectORM.owner_user_id == user.id)
        .order_by(ProjectORM.updated_at.desc())
        .first()
    )

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
