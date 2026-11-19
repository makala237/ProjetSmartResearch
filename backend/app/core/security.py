from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import jwt

from app.core.config import get_settings

_ph = PasswordHasher()


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize naive datetimes returned by SQLite into timezone-aware UTC datetimes."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hash_password(plain_password: str) -> str:
    """Hash a plain password using Argon2id."""
    return _ph.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plain password against a stored Argon2id hash."""
    if not plain_password or not password_hash:
        return False
    try:
        return _ph.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def _create_token(
    user_id: str,
    role: str,
    session_id: str,
    token_type: str,
    expires_delta: timedelta,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "sid": session_id,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.effective_jwt_secret(), algorithm="HS256")


def create_access_token(user_id: str, role: str, session_id: str) -> str:
    """Mint a short-lived signed JWT access token bound to active_session_id."""
    settings = get_settings()
    return _create_token(
        user_id=user_id,
        role=role,
        session_id=session_id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )


def create_refresh_token(user_id: str, role: str, session_id: str) -> str:
    """Mint a longer-lived signed JWT refresh token bound to active_session_id."""
    settings = get_settings()
    return _create_token(
        user_id=user_id,
        role=role,
        session_id=session_id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.jwt_refresh_token_expire_days),
    )


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate signature/expiry of a JWT token."""
    settings = get_settings()
    return jwt.decode(token, settings.effective_jwt_secret(), algorithms=["HS256"])
