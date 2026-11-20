import re
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import UserORM
from app.schemas.protocol import CoverMetadata, ProtocolProject
from app.services.auth_service import verify_session_token
from app.services.licensing_service import (
    assert_any_module_active,
    assert_module_write_allowed,
)
from app.services.storage import project_storage


def get_client_ip(request: Request) -> str:
    """Extract client IP respecting Cloud Run X-Forwarded-For header when present."""
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return ""


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        tok = parts[1].strip()
        return tok or None
    return None


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> UserORM:
    """Authenticate Bearer JWT token and enforce strict active_session_id."""
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": "NOT_AUTHENTICATED", "message": "Authentification requise."},
        )
    return verify_session_token(db, token, expected_type="access")


def get_active_user(
    current_user: UserORM = Depends(get_current_user),
) -> UserORM:
    """Require an authenticated user who has completed first-login password change."""
    if current_user.must_change_password:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PASSWORD_CHANGE_REQUIRED",
                "message": "Veuillez changer votre mot de passe temporaire avant d'accéder aux modules.",
            },
        )
    return current_user


def get_active_user_allow_query_token(
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> UserORM:
    """Authenticate Bearer header or ?access_token= query parameter for binary downloads/previews."""
    token = _extract_bearer_token(authorization) or ((access_token or "").strip() or None)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": "NOT_AUTHENTICATED", "message": "Authentification requise."},
        )
    user = verify_session_token(db, token, expected_type="access")
    if user.must_change_password:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PASSWORD_CHANGE_REQUIRED",
                "message": "Veuillez changer votre mot de passe temporaire avant d'accéder aux ressources.",
            },
        )
    return user


def require_admin(
    current_user: UserORM = Depends(get_active_user),
) -> UserORM:
    """Restrict endpoint strictly to active admin accounts."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ADMIN_REQUIRED",
                "message": "Action réservée aux administrateurs.",
            },
        )
    return current_user


def require_module_write(module_key: str):
    """Dependency enforcing active write entitlement on a specific licensed module."""
    def _checker(
        current_user: UserORM = Depends(get_active_user),
        db: Session = Depends(get_db),
    ) -> UserORM:
        assert_module_write_allowed(db, current_user, module_key)
        return current_user

    return _checker


def require_any_active_license():
    """Dependency requiring at least one active module license for students."""
    def _checker(
        current_user: UserORM = Depends(get_active_user),
        db: Session = Depends(get_db),
    ) -> UserORM:
        assert_any_module_active(db, current_user)
        return current_user

    return _checker


def get_authorized_project(project_id: str, current_user: UserORM) -> ProtocolProject:
    """Fetch a project and enforce strict multi-user ownership isolation."""
    project = project_storage.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    if current_user.role != "admin" and project.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PROJECT_FORBIDDEN",
                "message": "Accès refusé : ce protocole appartient à un autre utilisateur.",
            },
        )
    return project


def _norm_locked_str(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().casefold()


def assert_cover_admin_only(current_user: UserORM) -> None:
    """Block any cover-page mutation when requested by a non-admin user."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "COVER_ADMIN_ONLY",
                "message": "La page de garde du mémoire est définie et verrouillée par l'administrateur. Seul un administrateur peut la modifier.",
            },
        )


def enforce_topic_lock_on_cover_update(
    project: ProtocolProject,
    incoming_cover: CoverMetadata,
    current_user: UserORM,
) -> None:
    """Enforce admin-only cover editing while synchronizing locked identity fields."""
    if current_user.role != "admin":
        assert_cover_admin_only(current_user)
        return

    if project.topic_locked:
        project.locked_title = (incoming_cover.title or project.locked_title or "").strip()
        project.locked_author_name = (incoming_cover.author_name or project.locked_author_name or "").strip()
        project.locked_matricule = (incoming_cover.matricule or project.locked_matricule or "").strip()
