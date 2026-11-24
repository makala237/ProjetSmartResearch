"""Dual-mode persistence layer supporting SQLAlchemy (SQLite/PostgreSQL) and Cloud Firestore + BlobStorage."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import ensure_utc
from app.db.models import (
    AdminAuditLogORM,
    GeminiApiKeyORM,
    LicensePlanORM,
    PresentationORM,
    ProjectORM,
    UsageEventORM,
    UserAIQuotaORM,
    UserLicenseGrantORM,
    UserModuleEntitlementORM,
    UserORM,
    utcnow,
)
from app.services.blob_storage import BlobStorage, build_object_key, get_blob_storage

logger = logging.getLogger(__name__)

MODULE_KEYS: Tuple[str, ...] = ("module1", "module2", "module3", "module4")
_GET_ALL_CHUNK_SIZE = 100
_BATCH_DELETE_CHUNK_SIZE = 400
_IN_QUERY_CHUNK_SIZE = 10


def _to_utc(value: Any, fallback: Optional[datetime] = None) -> Optional[datetime]:
    """Normalize datetime, Firestore Timestamp, or ISO string into UTC-aware datetime."""
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return fallback
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            return ensure_utc(datetime.fromisoformat(raw))
        except ValueError:
            return fallback
    if hasattr(value, "timestamp"):
        try:
            return datetime.fromtimestamp(float(value.timestamp()), tz=timezone.utc)
        except Exception:
            return fallback
    return fallback


def _email_lock_id(email: str) -> str:
    """Return deterministic document ID for email uniqueness lock in Firestore."""
    norm = (email or "").strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _project_blob_key(project_id: str) -> str:
    return build_object_key("projects", project_id, "project.json")


def _presentation_blob_key(project_id: str) -> str:
    return build_object_key("presentations", project_id, "deck.json")


# =========================================================================
# TYPED DATA RECORDS (DATABASE-AGNOSTIC)
# =========================================================================


@dataclass
class UserRecord:
    id: str
    email: str
    full_name: str
    matricule: str
    phone: str
    password_hash: str
    role: str
    is_active: bool
    must_change_password: bool
    active_session_id: Optional[str]
    session_switch_count: int
    last_login_at: Optional[datetime]
    last_login_ip: Optional[str]
    created_at: datetime
    created_by_admin_id: Optional[str]
    gemini_api_key_id: Optional[str] = None
    model_routing_config: Optional[Dict[str, Any]] = None


@dataclass
class GeminiApiKeyRecord:
    id: str
    name: str
    owner_label: str
    api_key: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class LicensePlanRecord:
    id: str
    name: str
    description: str
    price_fcfa: int
    module1_days: int
    module2_days: int
    module3_days: int
    module4_days: int
    ai_quota_included: int
    is_active: bool
    created_at: datetime


@dataclass
class UserLicenseGrantRecord:
    id: str
    user_id: str
    plan_id: Optional[str]
    granted_by_admin_id: Optional[str]
    amount_paid_fcfa: int
    payment_reference: str
    notes: str
    granted_at: datetime


@dataclass
class UserModuleEntitlementRecord:
    user_id: str
    module_key: str
    starts_at: datetime
    expires_at: datetime
    updated_at: datetime


@dataclass
class UserAIQuotaRecord:
    user_id: str
    credits_total: int
    credits_used: int
    daily_38_limit: Optional[int]
    updated_at: datetime


@dataclass
class ProjectMetaRecord:
    project_id: str
    owner_user_id: str
    name: str
    locked_title: str
    locked_author_name: str
    locked_matricule: str
    topic_locked: bool
    sections_validated_count: int
    patients_count: int
    created_at: datetime
    updated_at: datetime
    blob_key: str = ""
    data_json: Optional[Dict[str, Any]] = None


@dataclass
class PresentationMetaRecord:
    project_id: str
    owner_user_id: str
    version: str
    slides_count: int
    updated_at: datetime
    blob_key: str = ""
    data_json: Optional[Dict[str, Any]] = None


@dataclass
class UsageEventRecord:
    id: str
    user_id: str
    project_id: Optional[str]
    module_key: str
    action: str
    model_used: str
    credits_cost: int
    status: str
    ip_address: str
    created_at: datetime
    tokens_used: int = 0
    api_key_id: str = "default"


@dataclass
class AdminAuditLogRecord:
    id: str
    admin_id: Optional[str]
    target_user_id: Optional[str]
    action: str
    details_json: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


# =========================================================================
# ABSTRACT DATASTORE INTERFACE
# =========================================================================


class BaseDataStore(ABC):
    """Unified persistence contract implemented by SQLDataStore and FirestoreDataStore."""

    @property
    @abstractmethod
    def dialect_name(self) -> str:
        """Return active backend dialect ('sqlite', 'postgresql', or 'firestore')."""

    def close(self) -> None:
        """Release request-scoped connections if applicable."""

    @abstractmethod
    def bootstrap(
        self,
        admin_email: str,
        admin_password_hash: str,
        admin_name: str,
        starter_plans: List[Dict[str, Any]],
    ) -> UserRecord:
        """Idempotently ensure bootstrap admin account and default license plans exist."""

    # --- Users ---
    @abstractmethod
    def get_user(self, user_id: str) -> Optional[UserRecord]: ...

    @abstractmethod
    def get_user_by_email(self, email: str) -> Optional[UserRecord]: ...

    @abstractmethod
    def find_users_by_full_name(self, full_name: str) -> List[UserRecord]: ...

    @staticmethod
    def _normalize_full_name(name: str) -> str:
        return " ".join((name or "").strip().casefold().split())

    @abstractmethod
    def get_first_admin(self) -> Optional[UserRecord]: ...

    @abstractmethod
    def list_users(self, role: Optional[str] = None) -> List[UserRecord]: ...

    @abstractmethod
    def get_users_by_ids(self, user_ids: List[str]) -> Dict[str, UserRecord]: ...

    @abstractmethod
    def count_users(self, role: Optional[str] = None) -> int: ...

    @abstractmethod
    def create_user(
        self,
        email: str,
        full_name: str,
        password_hash: str,
        role: str = "student",
        matricule: str = "",
        phone: str = "",
        must_change_password: bool = True,
        created_by_admin_id: Optional[str] = None,
        initial_credits: Optional[int] = None,
        gemini_api_key_id: Optional[str] = None,
        model_routing_config: Optional[Dict[str, Any]] = None,
    ) -> UserRecord: ...

    @abstractmethod
    def update_user(self, user_id: str, **fields: Any) -> Optional[UserRecord]: ...

    @abstractmethod
    def rotate_session_login(
        self,
        user_id: str,
        new_sid: str,
        ip_address: str,
        now: datetime,
    ) -> Optional[UserRecord]: ...

    @abstractmethod
    def delete_user_cascade(self, user_id: str) -> Optional[str]: ...

    # --- Module Entitlements ---
    @abstractmethod
    def get_entitlement(self, user_id: str, module_key: str) -> Optional[UserModuleEntitlementRecord]: ...

    @abstractmethod
    def list_entitlements(self, user_id: str) -> List[UserModuleEntitlementRecord]: ...

    @abstractmethod
    def list_entitlements_for_users(
        self,
        user_ids: List[str],
    ) -> Dict[str, Dict[str, UserModuleEntitlementRecord]]: ...

    @abstractmethod
    def upsert_entitlement(
        self,
        user_id: str,
        module_key: str,
        starts_at: datetime,
        expires_at: datetime,
        updated_at: datetime,
    ) -> UserModuleEntitlementRecord: ...

    # --- AI Quotas ---
    @abstractmethod
    def get_quota(self, user_id: str) -> Optional[UserAIQuotaRecord]: ...

    @abstractmethod
    def list_quotas_for_users(self, user_ids: List[str]) -> Dict[str, UserAIQuotaRecord]: ...

    @abstractmethod
    def ensure_quota(self, user_id: str, credits_total: int = 0) -> UserAIQuotaRecord: ...

    @abstractmethod
    def debit_quota_atomic(self, user_id: str, amount: int, now: datetime) -> UserAIQuotaRecord: ...

    @abstractmethod
    def add_quota_credits_atomic(
        self,
        user_id: str,
        add_credits: int,
        now: datetime,
        daily_38_limit: Optional[int] = None,
        update_daily_limit: bool = False,
    ) -> Tuple[UserAIQuotaRecord, int, Optional[int]]: ...

    @abstractmethod
    def ensure_admin_min_quota(
        self,
        user_id: str,
        min_credits: int = 100000,
        now: Optional[datetime] = None,
    ) -> UserAIQuotaRecord: ...

    # --- License Plans ---
    @abstractmethod
    def get_plan(self, plan_id: str) -> Optional[LicensePlanRecord]: ...

    @abstractmethod
    def get_plan_by_name(
        self,
        name: str,
        exclude_id: Optional[str] = None,
    ) -> Optional[LicensePlanRecord]: ...

    @abstractmethod
    def list_plans(self, include_inactive: bool = False) -> List[LicensePlanRecord]: ...

    @abstractmethod
    def get_plans_by_ids(self, plan_ids: List[str]) -> Dict[str, LicensePlanRecord]: ...

    @abstractmethod
    def count_plans(self) -> int: ...

    @abstractmethod
    def create_plan(
        self,
        name: str,
        description: str = "",
        price_fcfa: int = 0,
        module1_days: int = 0,
        module2_days: int = 0,
        module3_days: int = 0,
        module4_days: int = 0,
        ai_quota_included: int = 0,
        is_active: bool = True,
        created_at: Optional[datetime] = None,
    ) -> LicensePlanRecord: ...

    @abstractmethod
    def update_plan(self, plan_id: str, **fields: Any) -> Optional[LicensePlanRecord]: ...

    @abstractmethod
    def delete_plan(self, plan_id: str) -> bool: ...

    # --- License Grants ---
    @abstractmethod
    def create_grant(
        self,
        user_id: str,
        plan_id: Optional[str],
        granted_by_admin_id: Optional[str],
        amount_paid_fcfa: int,
        payment_reference: str,
        notes: str,
        granted_at: datetime,
    ) -> UserLicenseGrantRecord: ...

    @abstractmethod
    def list_grants(
        self,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[UserLicenseGrantRecord]: ...

    @abstractmethod
    def has_grants_for_plan(self, plan_id: str) -> bool: ...

    # --- Usage Events ---
    @abstractmethod
    def create_usage_event(
        self,
        user_id: str,
        module_key: str,
        action: str,
        project_id: Optional[str] = None,
        model_used: str = "",
        credits_cost: int = 0,
        tokens_used: int = 0,
        status: str = "ok",
        ip_address: str = "",
        created_at: Optional[datetime] = None,
    ) -> UsageEventRecord: ...

    @abstractmethod
    def list_usage_events(
        self,
        user_id: Optional[str] = None,
        module_key: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[UsageEventRecord]: ...

    @abstractmethod
    def count_user_gemini38_today(self, user_id: str, day_start: datetime) -> int: ...

    @abstractmethod
    def sum_user_tokens_today(self, user_id: str, day_start: datetime) -> int: ...

    @abstractmethod
    def count_user_ai_calls_since(
        self,
        user_id: str,
        day_start: datetime,
        minute_ago: datetime,
    ) -> Tuple[int, int]: ...

    @abstractmethod
    def get_user_model_usage_windows(
        self,
        user_id: str,
        day_start: datetime,
        minute_ago: datetime,
    ) -> Dict[str, Dict[str, int]]: ...

    @abstractmethod
    def aggregate_ok_events_by_users(
        self,
        user_ids: List[str],
        export_actions: frozenset,
    ) -> Tuple[Dict[str, int], Dict[str, int]]: ...

    # --- Admin Audit Logs ---
    @abstractmethod
    def create_audit_log(
        self,
        admin_id: Optional[str],
        target_user_id: Optional[str],
        action: str,
        details_json: Dict[str, Any],
        created_at: Optional[datetime] = None,
    ) -> AdminAuditLogRecord: ...

    @abstractmethod
    def list_audit_logs(
        self,
        target_user_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[AdminAuditLogRecord]: ...

    # --- Projects & Presentations ---
    @abstractmethod
    def get_project_meta(self, project_id: str) -> Optional[ProjectMetaRecord]: ...

    @abstractmethod
    def get_project_payload(
        self,
        project_id: str,
    ) -> Optional[Tuple[ProjectMetaRecord, Dict[str, Any]]]: ...

    @abstractmethod
    def get_latest_project_meta_for_user(self, user_id: str) -> Optional[ProjectMetaRecord]: ...

    @abstractmethod
    def list_project_metas(self, owner_user_id: Optional[str] = None) -> List[ProjectMetaRecord]: ...

    @abstractmethod
    def list_project_payloads(
        self,
        owner_user_id: Optional[str] = None,
    ) -> List[Tuple[ProjectMetaRecord, Dict[str, Any]]]: ...

    @abstractmethod
    def list_project_metas_for_users(self, user_ids: List[str]) -> Dict[str, ProjectMetaRecord]: ...

    @abstractmethod
    def count_projects(self, owner_user_id: Optional[str] = None) -> int: ...

    @abstractmethod
    def sum_patients_count(self) -> int: ...

    @abstractmethod
    def save_project(
        self,
        meta: ProjectMetaRecord,
        payload_dict: Dict[str, Any],
    ) -> ProjectMetaRecord: ...

    @abstractmethod
    def delete_project(self, project_id: str) -> bool: ...

    @abstractmethod
    def get_presentation_payload(self, project_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def save_presentation(
        self,
        project_id: str,
        owner_user_id: str,
        version: str,
        slides_count: int,
        payload_dict: Dict[str, Any],
        updated_at: datetime,
    ) -> None: ...


# =========================================================================
# SQLALCHEMY DATASTORE (SQLITE / POSTGRESQL)
# =========================================================================


def _orm_to_user(row: UserORM) -> UserRecord:
    return UserRecord(
        id=row.id,
        email=row.email,
        full_name=row.full_name,
        matricule=row.matricule or "",
        phone=row.phone or "",
        password_hash=row.password_hash,
        role=row.role,
        is_active=bool(row.is_active),
        must_change_password=bool(row.must_change_password),
        active_session_id=row.active_session_id,
        session_switch_count=int(row.session_switch_count or 0),
        last_login_at=_to_utc(row.last_login_at),
        last_login_ip=row.last_login_ip,
        created_at=_to_utc(row.created_at) or utcnow(),
        created_by_admin_id=row.created_by_admin_id,
        gemini_api_key_id=getattr(row, "gemini_api_key_id", None),
        model_routing_config=getattr(row, "model_routing_config", None),
    )


def _orm_to_api_key(row: GeminiApiKeyORM) -> GeminiApiKeyRecord:
    return GeminiApiKeyRecord(
        id=row.id,
        name=row.name,
        owner_label=row.owner_label or "",
        api_key=row.api_key or "",
        is_active=bool(row.is_active),
        created_at=_to_utc(row.created_at) or utcnow(),
        updated_at=_to_utc(row.updated_at) or utcnow(),
    )


def _orm_to_plan(row: LicensePlanORM) -> LicensePlanRecord:
    return LicensePlanRecord(
        id=row.id,
        name=row.name,
        description=row.description or "",
        price_fcfa=int(row.price_fcfa or 0),
        module1_days=int(row.module1_days or 0),
        module2_days=int(row.module2_days or 0),
        module3_days=int(row.module3_days or 0),
        module4_days=int(row.module4_days or 0),
        ai_quota_included=int(row.ai_quota_included or 0),
        is_active=bool(row.is_active),
        created_at=_to_utc(row.created_at) or utcnow(),
    )


def _orm_to_grant(row: UserLicenseGrantORM) -> UserLicenseGrantRecord:
    return UserLicenseGrantRecord(
        id=row.id,
        user_id=row.user_id,
        plan_id=row.plan_id,
        granted_by_admin_id=row.granted_by_admin_id,
        amount_paid_fcfa=int(row.amount_paid_fcfa or 0),
        payment_reference=row.payment_reference or "",
        notes=row.notes or "",
        granted_at=_to_utc(row.granted_at) or utcnow(),
    )


def _orm_to_entitlement(row: UserModuleEntitlementORM) -> UserModuleEntitlementRecord:
    now = utcnow()
    return UserModuleEntitlementRecord(
        user_id=row.user_id,
        module_key=row.module_key,
        starts_at=_to_utc(row.starts_at) or now,
        expires_at=_to_utc(row.expires_at) or now,
        updated_at=_to_utc(row.updated_at) or now,
    )


def _orm_to_quota(row: UserAIQuotaORM) -> UserAIQuotaRecord:
    return UserAIQuotaRecord(
        user_id=row.user_id,
        credits_total=int(row.credits_total or 0),
        credits_used=int(row.credits_used or 0),
        daily_38_limit=int(row.daily_38_limit) if row.daily_38_limit is not None else None,
        updated_at=_to_utc(row.updated_at) or utcnow(),
    )


def _orm_to_project_meta(row: ProjectORM, include_json: bool = False) -> ProjectMetaRecord:
    now = utcnow()
    return ProjectMetaRecord(
        project_id=row.project_id,
        owner_user_id=row.owner_user_id,
        name=row.name,
        locked_title=row.locked_title or "",
        locked_author_name=row.locked_author_name or "",
        locked_matricule=row.locked_matricule or "",
        topic_locked=bool(row.topic_locked),
        sections_validated_count=int(row.sections_validated_count or 0),
        patients_count=int(row.patients_count or 0),
        created_at=_to_utc(row.created_at) or now,
        updated_at=_to_utc(row.updated_at) or now,
        blob_key="",
        data_json=row.data_json if include_json else None,
    )


def _orm_to_event(row: UsageEventORM) -> UsageEventRecord:
    return UsageEventRecord(
        id=row.id,
        user_id=row.user_id,
        project_id=row.project_id,
        module_key=row.module_key,
        action=row.action,
        model_used=row.model_used or "",
        credits_cost=int(row.credits_cost or 0),
        tokens_used=int(getattr(row, "tokens_used", 0) or 0),
        api_key_id=str(getattr(row, "api_key_id", "default") or "default"),
        status=row.status or "ok",
        ip_address=row.ip_address or "",
        created_at=_to_utc(row.created_at) or utcnow(),
    )


def _orm_to_audit(row: AdminAuditLogORM) -> AdminAuditLogRecord:
    return AdminAuditLogRecord(
        id=row.id,
        admin_id=row.admin_id,
        target_user_id=row.target_user_id,
        action=row.action,
        details_json=dict(row.details_json or {}),
        created_at=_to_utc(row.created_at) or utcnow(),
    )


class SQLDataStore(BaseDataStore):
    """SQLAlchemy-backed DataStore implementation preserving full SQLite/PostgreSQL semantics."""

    def __init__(self, session: Session, owns_session: bool = True) -> None:
        self.db = session
        self.owns_session = owns_session

    @property
    def dialect_name(self) -> str:
        bind = self.db.get_bind()
        return str(getattr(getattr(bind, "dialect", None), "name", "sql"))

    def close(self) -> None:
        if self.owns_session:
            self.db.close()

    def bootstrap(
        self,
        admin_email: str,
        admin_password_hash: str,
        admin_name: str,
        starter_plans: List[Dict[str, Any]],
    ) -> UserRecord:
        admin = self.get_first_admin()
        if admin is None:
            existing_by_email = self.get_user_by_email(admin_email)
            if existing_by_email is not None:
                admin = self.update_user(
                    existing_by_email.id,
                    role="admin",
                    is_active=True,
                    must_change_password=False,
                ) or existing_by_email
                self.ensure_admin_min_quota(admin.id, min_credits=100000, now=utcnow())
            else:
                admin = self.create_user(
                    email=admin_email,
                    full_name=admin_name or "Administrateur Principal",
                    password_hash=admin_password_hash,
                    role="admin",
                    must_change_password=False,
                    initial_credits=100000,
                )

        if self.count_plans() == 0:
            for p in starter_plans:
                self.create_plan(**p)
        return admin

    # --- Users ---
    def get_user(self, user_id: str) -> Optional[UserRecord]:
        row = self.db.get(UserORM, user_id)
        return _orm_to_user(row) if row is not None else None

    def get_user_by_email(self, email: str) -> Optional[UserRecord]:
        norm = (email or "").strip().lower()
        row = self.db.query(UserORM).filter(UserORM.email == norm).first()
        return _orm_to_user(row) if row is not None else None

    def find_users_by_full_name(self, full_name: str) -> List[UserRecord]:
        target = self._normalize_full_name(full_name)
        if not target:
            return []
        rows = self.db.query(UserORM).all()
        return [
            _orm_to_user(r)
            for r in rows
            if self._normalize_full_name(r.full_name) == target
        ]

    def get_first_admin(self) -> Optional[UserRecord]:
        row = self.db.query(UserORM).filter(UserORM.role == "admin").first()
        return _orm_to_user(row) if row is not None else None

    def list_users(self, role: Optional[str] = None) -> List[UserRecord]:
        q = self.db.query(UserORM)
        if role in ("student", "admin"):
            q = q.filter(UserORM.role == role)
        rows = q.order_by(UserORM.created_at.desc()).all()
        return [_orm_to_user(r) for r in rows]

    def get_users_by_ids(self, user_ids: List[str]) -> Dict[str, UserRecord]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}
        rows = self.db.query(UserORM).filter(UserORM.id.in_(clean)).all()
        return {r.id: _orm_to_user(r) for r in rows}

    def count_users(self, role: Optional[str] = None) -> int:
        q = self.db.query(UserORM)
        if role in ("student", "admin"):
            q = q.filter(UserORM.role == role)
        return q.count()

    def create_user(
        self,
        email: str,
        full_name: str,
        password_hash: str,
        role: str = "student",
        matricule: str = "",
        phone: str = "",
        must_change_password: bool = True,
        created_by_admin_id: Optional[str] = None,
        initial_credits: Optional[int] = None,
        gemini_api_key_id: Optional[str] = None,
        model_routing_config: Optional[Dict[str, Any]] = None,
    ) -> UserRecord:
        norm_email = email.strip().lower()
        existing = self.db.query(UserORM).filter(UserORM.email == norm_email).first()
        if existing is not None:
            raise ValueError("EMAIL_EXISTS")

        resolved_role = role if role in ("admin", "student") else "student"
        credits = initial_credits if initial_credits is not None else (100000 if resolved_role == "admin" else 0)
        norm_key_id = (gemini_api_key_id or "").strip() or None
        if norm_key_id == "default":
            norm_key_id = None
        row = UserORM(
            email=norm_email,
            full_name=full_name.strip(),
            matricule=matricule.strip(),
            phone=phone.strip(),
            password_hash=password_hash,
            role=resolved_role,
            is_active=True,
            must_change_password=must_change_password,
            created_by_admin_id=created_by_admin_id,
            gemini_api_key_id=norm_key_id,
            model_routing_config=model_routing_config,
        )
        self.db.add(row)
        self.db.flush()
        self.db.add(
            UserAIQuotaORM(
                user_id=row.id,
                credits_total=credits,
                credits_used=0,
            )
        )
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_user(row)

    def update_user(self, user_id: str, **fields: Any) -> Optional[UserRecord]:
        row = self.db.get(UserORM, user_id)
        if row is None:
            return None
        for key, value in fields.items():
            if hasattr(row, key):
                setattr(row, key, value)
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_user(row)

    def rotate_session_login(
        self,
        user_id: str,
        new_sid: str,
        ip_address: str,
        now: datetime,
    ) -> Optional[UserRecord]:
        row = self.db.get(UserORM, user_id)
        if row is None:
            return None
        if row.active_session_id:
            row.session_switch_count = (row.session_switch_count or 0) + 1
        row.active_session_id = new_sid
        row.last_login_at = now
        row.last_login_ip = ip_address or ""
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_user(row)

    def delete_user_cascade(self, user_id: str) -> Optional[str]:
        row = self.db.get(UserORM, user_id)
        if row is None:
            return None
        email = row.email
        self.db.delete(row)
        self.db.commit()
        return email

    # --- Module Entitlements ---
    def get_entitlement(self, user_id: str, module_key: str) -> Optional[UserModuleEntitlementRecord]:
        row = (
            self.db.query(UserModuleEntitlementORM)
            .filter(
                UserModuleEntitlementORM.user_id == user_id,
                UserModuleEntitlementORM.module_key == module_key,
            )
            .first()
        )
        return _orm_to_entitlement(row) if row is not None else None

    def list_entitlements(self, user_id: str) -> List[UserModuleEntitlementRecord]:
        rows = (
            self.db.query(UserModuleEntitlementORM)
            .filter(UserModuleEntitlementORM.user_id == user_id)
            .all()
        )
        return [_orm_to_entitlement(r) for r in rows]

    def list_entitlements_for_users(
        self,
        user_ids: List[str],
    ) -> Dict[str, Dict[str, UserModuleEntitlementRecord]]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}
        rows = (
            self.db.query(UserModuleEntitlementORM)
            .filter(UserModuleEntitlementORM.user_id.in_(clean))
            .all()
        )
        out: Dict[str, Dict[str, UserModuleEntitlementRecord]] = {}
        for row in rows:
            out.setdefault(row.user_id, {})[row.module_key] = _orm_to_entitlement(row)
        return out

    def upsert_entitlement(
        self,
        user_id: str,
        module_key: str,
        starts_at: datetime,
        expires_at: datetime,
        updated_at: datetime,
    ) -> UserModuleEntitlementRecord:
        row = (
            self.db.query(UserModuleEntitlementORM)
            .filter(
                UserModuleEntitlementORM.user_id == user_id,
                UserModuleEntitlementORM.module_key == module_key,
            )
            .first()
        )
        if row is None:
            row = UserModuleEntitlementORM(
                user_id=user_id,
                module_key=module_key,
                starts_at=starts_at,
                expires_at=expires_at,
                updated_at=updated_at,
            )
            self.db.add(row)
        else:
            row.starts_at = starts_at
            row.expires_at = expires_at
            row.updated_at = updated_at
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_entitlement(row)

    # --- AI Quotas ---
    def get_quota(self, user_id: str) -> Optional[UserAIQuotaRecord]:
        row = self.db.get(UserAIQuotaORM, user_id)
        return _orm_to_quota(row) if row is not None else None

    def list_quotas_for_users(self, user_ids: List[str]) -> Dict[str, UserAIQuotaRecord]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}
        rows = self.db.query(UserAIQuotaORM).filter(UserAIQuotaORM.user_id.in_(clean)).all()
        return {r.user_id: _orm_to_quota(r) for r in rows}

    def ensure_quota(self, user_id: str, credits_total: int = 0) -> UserAIQuotaRecord:
        row = self.db.get(UserAIQuotaORM, user_id)
        if row is None:
            row = UserAIQuotaORM(
                user_id=user_id,
                credits_total=credits_total,
                credits_used=0,
                updated_at=utcnow(),
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        return _orm_to_quota(row)

    def debit_quota_atomic(self, user_id: str, amount: int, now: datetime) -> UserAIQuotaRecord:
        row = self.db.get(UserAIQuotaORM, user_id)
        if row is None:
            row = UserAIQuotaORM(
                user_id=user_id,
                credits_total=0,
                credits_used=max(0, int(amount)),
                updated_at=now,
            )
            self.db.add(row)
        else:
            row.credits_used = int(row.credits_used or 0) + max(0, int(amount))
            row.updated_at = now
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_quota(row)

    def add_quota_credits_atomic(
        self,
        user_id: str,
        add_credits: int,
        now: datetime,
        daily_38_limit: Optional[int] = None,
        update_daily_limit: bool = False,
    ) -> Tuple[UserAIQuotaRecord, int, Optional[int]]:
        row = self.db.get(UserAIQuotaORM, user_id)
        if row is None:
            row = UserAIQuotaORM(user_id=user_id, credits_total=0, credits_used=0, updated_at=now)
            self.db.add(row)
            self.db.flush()

        old_total = int(row.credits_total or 0)
        old_daily = row.daily_38_limit
        row.credits_total = max(int(row.credits_used or 0), old_total + int(add_credits))
        if update_daily_limit:
            row.daily_38_limit = (
                int(daily_38_limit) if (daily_38_limit is not None and int(daily_38_limit) >= 1) else None
            )
        row.updated_at = now
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_quota(row), old_total, old_daily

    def ensure_admin_min_quota(
        self,
        user_id: str,
        min_credits: int = 100000,
        now: Optional[datetime] = None,
    ) -> UserAIQuotaRecord:
        ts = now or utcnow()
        row = self.db.get(UserAIQuotaORM, user_id)
        if row is None:
            row = UserAIQuotaORM(
                user_id=user_id,
                credits_total=min_credits,
                credits_used=0,
                updated_at=ts,
            )
            self.db.add(row)
        else:
            row.credits_total = max(int(row.credits_total or 0), min_credits)
            row.updated_at = ts
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_quota(row)

    # --- License Plans ---
    def get_plan(self, plan_id: str) -> Optional[LicensePlanRecord]:
        row = self.db.get(LicensePlanORM, plan_id)
        return _orm_to_plan(row) if row is not None else None

    def get_plan_by_name(
        self,
        name: str,
        exclude_id: Optional[str] = None,
    ) -> Optional[LicensePlanRecord]:
        clean = name.strip()
        q = self.db.query(LicensePlanORM).filter(LicensePlanORM.name == clean)
        if exclude_id is not None:
            q = q.filter(LicensePlanORM.id != exclude_id)
        row = q.first()
        return _orm_to_plan(row) if row is not None else None

    def list_plans(self, include_inactive: bool = False) -> List[LicensePlanRecord]:
        q = self.db.query(LicensePlanORM)
        if not include_inactive:
            q = q.filter(LicensePlanORM.is_active.is_(True))
        rows = q.order_by(LicensePlanORM.price_fcfa.asc(), LicensePlanORM.created_at.desc()).all()
        return [_orm_to_plan(r) for r in rows]

    def get_plans_by_ids(self, plan_ids: List[str]) -> Dict[str, LicensePlanRecord]:
        clean = list({pid for pid in plan_ids if pid})
        if not clean:
            return {}
        rows = self.db.query(LicensePlanORM).filter(LicensePlanORM.id.in_(clean)).all()
        return {r.id: _orm_to_plan(r) for r in rows}

    def count_plans(self) -> int:
        return self.db.query(LicensePlanORM).count()

    def create_plan(
        self,
        name: str,
        description: str = "",
        price_fcfa: int = 0,
        module1_days: int = 0,
        module2_days: int = 0,
        module3_days: int = 0,
        module4_days: int = 0,
        ai_quota_included: int = 0,
        is_active: bool = True,
        created_at: Optional[datetime] = None,
    ) -> LicensePlanRecord:
        row = LicensePlanORM(
            name=name.strip(),
            description=description.strip(),
            price_fcfa=int(price_fcfa),
            module1_days=int(module1_days),
            module2_days=int(module2_days),
            module3_days=int(module3_days),
            module4_days=int(module4_days),
            ai_quota_included=int(ai_quota_included),
            is_active=bool(is_active),
            created_at=created_at or utcnow(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_plan(row)

    def update_plan(self, plan_id: str, **fields: Any) -> Optional[LicensePlanRecord]:
        row = self.db.get(LicensePlanORM, plan_id)
        if row is None:
            return None
        for key, value in fields.items():
            if hasattr(row, key):
                setattr(row, key, value)
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_plan(row)

    def delete_plan(self, plan_id: str) -> bool:
        row = self.db.get(LicensePlanORM, plan_id)
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    # --- License Grants ---
    def create_grant(
        self,
        user_id: str,
        plan_id: Optional[str],
        granted_by_admin_id: Optional[str],
        amount_paid_fcfa: int,
        payment_reference: str,
        notes: str,
        granted_at: datetime,
    ) -> UserLicenseGrantRecord:
        row = UserLicenseGrantORM(
            user_id=user_id,
            plan_id=plan_id,
            granted_by_admin_id=granted_by_admin_id,
            amount_paid_fcfa=amount_paid_fcfa,
            payment_reference=payment_reference,
            notes=notes,
            granted_at=granted_at,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_grant(row)

    def list_grants(
        self,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[UserLicenseGrantRecord]:
        q = self.db.query(UserLicenseGrantORM)
        if user_id:
            q = q.filter(UserLicenseGrantORM.user_id == user_id)
        q = q.order_by(UserLicenseGrantORM.granted_at.desc())
        if limit is not None:
            q = q.limit(limit)
        return [_orm_to_grant(r) for r in q.all()]

    def has_grants_for_plan(self, plan_id: str) -> bool:
        return self.db.query(UserLicenseGrantORM).filter(UserLicenseGrantORM.plan_id == plan_id).count() > 0

    # --- Usage Events ---
    def create_usage_event(
        self,
        user_id: str,
        module_key: str,
        action: str,
        project_id: Optional[str] = None,
        model_used: str = "",
        credits_cost: int = 0,
        tokens_used: int = 0,
        api_key_id: str = "default",
        status: str = "ok",
        ip_address: str = "",
        created_at: Optional[datetime] = None,
    ) -> UsageEventRecord:
        row = UsageEventORM(
            user_id=user_id,
            project_id=project_id,
            module_key=module_key,
            action=action,
            model_used=(model_used or "")[:128],
            credits_cost=max(0, int(credits_cost)),
            tokens_used=max(0, int(tokens_used)),
            api_key_id=((api_key_id or "default").strip() or "default")[:64],
            status=(status or "ok")[:32],
            ip_address=(ip_address or "")[:64],
            created_at=created_at or utcnow(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_event(row)

    def list_usage_events(
        self,
        user_id: Optional[str] = None,
        module_key: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[UsageEventRecord]:
        q = self.db.query(UsageEventORM)
        if user_id:
            q = q.filter(UsageEventORM.user_id == user_id)
        if module_key:
            q = q.filter(UsageEventORM.module_key == module_key)
        if status:
            q = q.filter(UsageEventORM.status == status)
        q = q.order_by(UsageEventORM.created_at.desc())
        if limit is not None:
            q = q.limit(limit)
        return [_orm_to_event(r) for r in q.all()]

    def count_user_gemini38_today(self, user_id: str, day_start: datetime) -> int:
        from app.core.config import get_settings

        now = datetime.now(timezone.utc)
        primary = (get_settings().gemini_primary_reasoning_model or "gemini-3.5-flash-lite").strip().lower()
        rows = (
            self.db.query(UsageEventORM)
            .filter(
                UsageEventORM.user_id == user_id,
                UsageEventORM.status == "ok",
            )
            .all()
        )
        count = 0
        for ev in rows:
            if (_to_utc(ev.created_at) or now) < day_start:
                continue
            model = str(ev.model_used or "").lower()
            if "gemini-3.8" in model or (primary and primary in model):
                count += 1
        return count

    def sum_user_tokens_today(self, user_id: str, day_start: datetime) -> int:
        now = datetime.now(timezone.utc)
        rows = (
            self.db.query(UsageEventORM)
            .filter(
                UsageEventORM.user_id == user_id,
                UsageEventORM.status == "ok",
            )
            .all()
        )
        return sum(
            max(0, int(getattr(ev, "tokens_used", 0) or 0))
            for ev in rows
            if (_to_utc(ev.created_at) or now) >= day_start
        )

    def count_user_ai_calls_since(
        self,
        user_id: str,
        day_start: datetime,
        minute_ago: datetime,
    ) -> Tuple[int, int]:
        now = datetime.now(timezone.utc)
        non_ai_actions = {"export_docx", "export_pdf", "export_pptx", "export_csv", "patient_upsert"}
        rows = (
            self.db.query(UsageEventORM)
            .filter(
                UsageEventORM.user_id == user_id,
                UsageEventORM.status == "ok",
            )
            .all()
        )
        rpm_count = 0
        rpd_count = 0
        for ev in rows:
            if (ev.action or "") in non_ai_actions:
                continue
            ts = _to_utc(ev.created_at) or now
            if ts >= day_start:
                rpd_count += 1
            if ts >= minute_ago:
                rpm_count += 1
        return rpm_count, rpd_count

    def get_user_model_usage_windows(
        self,
        user_id: str,
        day_start: datetime,
        minute_ago: datetime,
    ) -> Dict[str, Dict[str, int]]:
        now = datetime.now(timezone.utc)
        earliest = min(day_start, minute_ago)
        non_ai_actions = {"export_docx", "export_pdf", "export_pptx", "export_csv", "patient_upsert"}
        rows = (
            self.db.query(UsageEventORM)
            .filter(
                UsageEventORM.user_id == user_id,
                UsageEventORM.status == "ok",
            )
            .all()
        )
        stats: Dict[str, Dict[str, int]] = {}
        for ev in rows:
            if (ev.action or "") in non_ai_actions:
                continue
            model = str(ev.model_used or "").strip().lower()
            if not model:
                continue
            ts = _to_utc(ev.created_at) or now
            if ts < earliest:
                continue
            bucket = stats.setdefault(model, {"rpd_used": 0, "tpm_used": 0})
            if ts >= day_start:
                bucket["rpd_used"] += 1
            if ts >= minute_ago:
                bucket["tpm_used"] += max(0, int(getattr(ev, "tokens_used", 0) or 0))
        return stats

    def aggregate_ok_events_by_users(
        self,
        user_ids: List[str],
        export_actions: frozenset,
    ) -> Tuple[Dict[str, int], Dict[str, int]]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}, {}
        rows = (
            self.db.query(UsageEventORM.user_id, UsageEventORM.action, func.count(UsageEventORM.id))
            .filter(UsageEventORM.user_id.in_(clean), UsageEventORM.status == "ok")
            .group_by(UsageEventORM.user_id, UsageEventORM.action)
            .all()
        )
        exports_by_user: Dict[str, int] = {}
        ai_calls_by_user: Dict[str, int] = {}
        for uid, act, cnt in rows:
            if act in export_actions:
                exports_by_user[uid] = exports_by_user.get(uid, 0) + int(cnt)
            elif act != "patient_upsert":
                ai_calls_by_user[uid] = ai_calls_by_user.get(uid, 0) + int(cnt)
        return exports_by_user, ai_calls_by_user

    # --- Admin Audit Logs ---
    def create_audit_log(
        self,
        admin_id: Optional[str],
        target_user_id: Optional[str],
        action: str,
        details_json: Dict[str, Any],
        created_at: Optional[datetime] = None,
    ) -> AdminAuditLogRecord:
        row = AdminAuditLogORM(
            admin_id=admin_id,
            target_user_id=target_user_id,
            action=action,
            details_json=details_json or {},
            created_at=created_at or utcnow(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_audit(row)

    def list_audit_logs(
        self,
        target_user_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[AdminAuditLogRecord]:
        q = self.db.query(AdminAuditLogORM)
        if target_user_id:
            q = q.filter(AdminAuditLogORM.target_user_id == target_user_id)
        rows = q.order_by(AdminAuditLogORM.created_at.desc()).limit(limit).all()
        return [_orm_to_audit(r) for r in rows]

    # --- Projects & Presentations ---
    def get_project_meta(self, project_id: str) -> Optional[ProjectMetaRecord]:
        row = self.db.get(ProjectORM, project_id)
        return _orm_to_project_meta(row, include_json=False) if row is not None else None

    def get_project_payload(
        self,
        project_id: str,
    ) -> Optional[Tuple[ProjectMetaRecord, Dict[str, Any]]]:
        row = self.db.get(ProjectORM, project_id)
        if row is None:
            return None
        meta = _orm_to_project_meta(row, include_json=True)
        return meta, dict(row.data_json or {})

    def get_latest_project_meta_for_user(self, user_id: str) -> Optional[ProjectMetaRecord]:
        row = (
            self.db.query(ProjectORM)
            .filter(ProjectORM.owner_user_id == user_id)
            .order_by(ProjectORM.updated_at.desc())
            .first()
        )
        return _orm_to_project_meta(row, include_json=False) if row is not None else None

    def list_project_metas(self, owner_user_id: Optional[str] = None) -> List[ProjectMetaRecord]:
        q = self.db.query(ProjectORM)
        if owner_user_id:
            q = q.filter(ProjectORM.owner_user_id == owner_user_id)
        rows = q.order_by(ProjectORM.updated_at.desc()).all()
        return [_orm_to_project_meta(r, include_json=False) for r in rows]

    def list_project_payloads(
        self,
        owner_user_id: Optional[str] = None,
    ) -> List[Tuple[ProjectMetaRecord, Dict[str, Any]]]:
        q = self.db.query(ProjectORM)
        if owner_user_id:
            q = q.filter(ProjectORM.owner_user_id == owner_user_id)
        rows = q.order_by(ProjectORM.updated_at.desc()).all()
        return [(_orm_to_project_meta(r, include_json=True), dict(r.data_json or {})) for r in rows]

    def list_project_metas_for_users(self, user_ids: List[str]) -> Dict[str, ProjectMetaRecord]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}
        rows = (
            self.db.query(ProjectORM)
            .filter(ProjectORM.owner_user_id.in_(clean))
            .order_by(ProjectORM.updated_at.desc())
            .all()
        )
        out: Dict[str, ProjectMetaRecord] = {}
        for r in rows:
            if r.owner_user_id not in out:
                out[r.owner_user_id] = _orm_to_project_meta(r, include_json=False)
        return out

    def count_projects(self, owner_user_id: Optional[str] = None) -> int:
        q = self.db.query(ProjectORM)
        if owner_user_id:
            q = q.filter(ProjectORM.owner_user_id == owner_user_id)
        return q.count()

    def sum_patients_count(self) -> int:
        return int(self.db.query(func.coalesce(func.sum(ProjectORM.patients_count), 0)).scalar() or 0)

    def save_project(
        self,
        meta: ProjectMetaRecord,
        payload_dict: Dict[str, Any],
    ) -> ProjectMetaRecord:
        row = self.db.get(ProjectORM, meta.project_id)
        if row is None:
            row = ProjectORM(
                project_id=meta.project_id,
                owner_user_id=meta.owner_user_id,
                name=meta.name,
                locked_title=meta.locked_title,
                locked_author_name=meta.locked_author_name,
                locked_matricule=meta.locked_matricule,
                topic_locked=bool(meta.topic_locked),
                data_json=payload_dict,
                sections_validated_count=meta.sections_validated_count,
                patients_count=meta.patients_count,
                created_at=meta.created_at,
                updated_at=meta.updated_at,
            )
            self.db.add(row)
        else:
            row.owner_user_id = meta.owner_user_id
            row.name = meta.name
            row.locked_title = meta.locked_title
            row.locked_author_name = meta.locked_author_name
            row.locked_matricule = meta.locked_matricule
            row.topic_locked = bool(meta.topic_locked)
            row.data_json = payload_dict
            row.sections_validated_count = meta.sections_validated_count
            row.patients_count = meta.patients_count
            row.updated_at = meta.updated_at
        self.db.commit()
        return _orm_to_project_meta(row, include_json=False)

    def delete_project(self, project_id: str) -> bool:
        row = self.db.get(ProjectORM, project_id)
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    def get_presentation_payload(self, project_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.get(PresentationORM, project_id)
        if row is None:
            return None
        return dict(row.data_json or {})

    def save_presentation(
        self,
        project_id: str,
        owner_user_id: str,
        version: str,
        slides_count: int,
        payload_dict: Dict[str, Any],
        updated_at: datetime,
    ) -> None:
        proj_row = self.db.get(ProjectORM, project_id)
        if proj_row is None:
            return
        resolved_owner = proj_row.owner_user_id or owner_user_id
        existing = self.db.get(PresentationORM, project_id)
        if existing is None:
            self.db.add(
                PresentationORM(
                    project_id=project_id,
                    owner_user_id=resolved_owner,
                    version=version or "3.1.0",
                    slides_count=slides_count,
                    data_json=payload_dict,
                    updated_at=updated_at,
                )
            )
        else:
            existing.owner_user_id = resolved_owner
            existing.version = version or "3.1.0"
            existing.slides_count = slides_count
            existing.data_json = payload_dict
            existing.updated_at = updated_at
        self.db.commit()

    # --- Named Gemini API Keys ---
    def list_gemini_api_keys(self) -> List[GeminiApiKeyRecord]:
        rows = self.db.query(GeminiApiKeyORM).order_by(GeminiApiKeyORM.created_at.asc()).all()
        return [_orm_to_api_key(r) for r in rows]

    def get_gemini_api_key(self, key_id: str) -> Optional[GeminiApiKeyRecord]:
        row = self.db.get(GeminiApiKeyORM, key_id)
        return _orm_to_api_key(row) if row is not None else None

    def create_gemini_api_key(
        self,
        name: str,
        api_key: str,
        owner_label: str = "",
        is_active: bool = True,
    ) -> GeminiApiKeyRecord:
        now = utcnow()
        row = GeminiApiKeyORM(
            name=name.strip()[:128],
            owner_label=(owner_label or "").strip()[:128],
            api_key=api_key.strip(),
            is_active=bool(is_active),
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_api_key(row)

    def update_gemini_api_key(self, key_id: str, **fields: Any) -> Optional[GeminiApiKeyRecord]:
        row = self.db.get(GeminiApiKeyORM, key_id)
        if row is None:
            return None
        for k, v in fields.items():
            if hasattr(row, k) and v is not None:
                setattr(row, k, v)
        row.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(row)
        return _orm_to_api_key(row)

    def delete_gemini_api_key(self, key_id: str) -> bool:
        row = self.db.get(GeminiApiKeyORM, key_id)
        if row is None:
            return False
        # Reassign any users on this key back to default (None)
        self.db.query(UserORM).filter(UserORM.gemini_api_key_id == key_id).update(
            {"gemini_api_key_id": None}
        )
        self.db.delete(row)
        self.db.commit()
        return True


# =========================================================================
# CLOUD FIRESTORE + BLOB STORAGE DATASTORE
# =========================================================================


def _get_firestore_helpers():
    """Lazily load Firestore query/transaction helpers so SQL-only setups require no cloud SDK."""
    try:
        from google.cloud import firestore  # type: ignore

        desc_dir = firestore.Query.DESCENDING
        asc_dir = firestore.Query.ASCENDING
        transactional_dec = firestore.transactional
    except Exception:  # pragma: no cover
        desc_dir = "DESCENDING"
        asc_dir = "ASCENDING"

        def transactional_dec(fn: Callable[..., Any]) -> Callable[..., Any]:
            def _wrapper(tx: Any, *args: Any, **kwargs: Any) -> Any:
                return fn(tx, *args, **kwargs)

            return _wrapper

    try:
        from google.cloud.firestore_v1.base_query import FieldFilter  # type: ignore
    except Exception:  # pragma: no cover
        FieldFilter = None

    return desc_dir, asc_dir, transactional_dec, FieldFilter


def _apply_where(query: Any, field: str, op: str, value: Any, field_filter_cls: Any) -> Any:
    if field_filter_cls is not None:
        return query.where(filter=field_filter_cls(field, op, value))
    return query.where(field, op, value)


def _doc_to_user(doc_id: str, data: Dict[str, Any]) -> UserRecord:
    return UserRecord(
        id=str(data.get("id") or doc_id),
        email=str(data.get("email") or ""),
        full_name=str(data.get("full_name") or ""),
        matricule=str(data.get("matricule") or ""),
        phone=str(data.get("phone") or ""),
        password_hash=str(data.get("password_hash") or ""),
        role=str(data.get("role") or "student"),
        is_active=bool(data.get("is_active", True)),
        must_change_password=bool(data.get("must_change_password", True)),
        active_session_id=data.get("active_session_id"),
        session_switch_count=int(data.get("session_switch_count") or 0),
        last_login_at=_to_utc(data.get("last_login_at")),
        last_login_ip=data.get("last_login_ip"),
        created_at=_to_utc(data.get("created_at")) or utcnow(),
        created_by_admin_id=data.get("created_by_admin_id"),
        gemini_api_key_id=data.get("gemini_api_key_id") or None,
        model_routing_config=data.get("model_routing_config") or None,
    )


def _doc_to_api_key(doc_id: str, data: Dict[str, Any]) -> GeminiApiKeyRecord:
    return GeminiApiKeyRecord(
        id=str(data.get("id") or doc_id),
        name=str(data.get("name") or ""),
        owner_label=str(data.get("owner_label") or ""),
        api_key=str(data.get("api_key") or ""),
        is_active=bool(data.get("is_active", True)),
        created_at=_to_utc(data.get("created_at")) or utcnow(),
        updated_at=_to_utc(data.get("updated_at")) or utcnow(),
    )


def _doc_to_plan(doc_id: str, data: Dict[str, Any]) -> LicensePlanRecord:
    return LicensePlanRecord(
        id=str(data.get("id") or doc_id),
        name=str(data.get("name") or ""),
        description=str(data.get("description") or ""),
        price_fcfa=int(data.get("price_fcfa") or 0),
        module1_days=int(data.get("module1_days") or 0),
        module2_days=int(data.get("module2_days") or 0),
        module3_days=int(data.get("module3_days") or 0),
        module4_days=int(data.get("module4_days") or 0),
        ai_quota_included=int(data.get("ai_quota_included") or 0),
        is_active=bool(data.get("is_active", True)),
        created_at=_to_utc(data.get("created_at")) or utcnow(),
    )


def _doc_to_grant(doc_id: str, data: Dict[str, Any]) -> UserLicenseGrantRecord:
    return UserLicenseGrantRecord(
        id=str(data.get("id") or doc_id),
        user_id=str(data.get("user_id") or ""),
        plan_id=data.get("plan_id"),
        granted_by_admin_id=data.get("granted_by_admin_id"),
        amount_paid_fcfa=int(data.get("amount_paid_fcfa") or 0),
        payment_reference=str(data.get("payment_reference") or ""),
        notes=str(data.get("notes") or ""),
        granted_at=_to_utc(data.get("granted_at")) or utcnow(),
    )


def _doc_to_entitlement(data: Dict[str, Any]) -> UserModuleEntitlementRecord:
    now = utcnow()
    return UserModuleEntitlementRecord(
        user_id=str(data.get("user_id") or ""),
        module_key=str(data.get("module_key") or ""),
        starts_at=_to_utc(data.get("starts_at")) or now,
        expires_at=_to_utc(data.get("expires_at")) or now,
        updated_at=_to_utc(data.get("updated_at")) or now,
    )


def _doc_to_quota(doc_id: str, data: Dict[str, Any]) -> UserAIQuotaRecord:
    raw_limit = data.get("daily_38_limit")
    return UserAIQuotaRecord(
        user_id=str(data.get("user_id") or doc_id),
        credits_total=int(data.get("credits_total") or 0),
        credits_used=int(data.get("credits_used") or 0),
        daily_38_limit=int(raw_limit) if raw_limit is not None else None,
        updated_at=_to_utc(data.get("updated_at")) or utcnow(),
    )


def _doc_to_project_meta(doc_id: str, data: Dict[str, Any]) -> ProjectMetaRecord:
    now = utcnow()
    pid = str(data.get("project_id") or doc_id)
    return ProjectMetaRecord(
        project_id=pid,
        owner_user_id=str(data.get("owner_user_id") or ""),
        name=str(data.get("name") or ""),
        locked_title=str(data.get("locked_title") or ""),
        locked_author_name=str(data.get("locked_author_name") or ""),
        locked_matricule=str(data.get("locked_matricule") or ""),
        topic_locked=bool(data.get("topic_locked", False)),
        sections_validated_count=int(data.get("sections_validated_count") or 0),
        patients_count=int(data.get("patients_count") or 0),
        created_at=_to_utc(data.get("created_at")) or now,
        updated_at=_to_utc(data.get("updated_at")) or now,
        blob_key=str(data.get("blob_key") or _project_blob_key(pid)),
        data_json=None,
    )


def _doc_to_event(doc_id: str, data: Dict[str, Any]) -> UsageEventRecord:
    return UsageEventRecord(
        id=str(data.get("id") or doc_id),
        user_id=str(data.get("user_id") or ""),
        project_id=data.get("project_id"),
        module_key=str(data.get("module_key") or ""),
        action=str(data.get("action") or ""),
        model_used=str(data.get("model_used") or ""),
        credits_cost=int(data.get("credits_cost") or 0),
        tokens_used=int(data.get("tokens_used") or 0),
        api_key_id=str(data.get("api_key_id") or "default"),
        status=str(data.get("status") or "ok"),
        ip_address=str(data.get("ip_address") or ""),
        created_at=_to_utc(data.get("created_at")) or utcnow(),
    )


def _doc_to_audit(doc_id: str, data: Dict[str, Any]) -> AdminAuditLogRecord:
    return AdminAuditLogRecord(
        id=str(data.get("id") or doc_id),
        admin_id=data.get("admin_id"),
        target_user_id=data.get("target_user_id"),
        action=str(data.get("action") or ""),
        details_json=dict(data.get("details_json") or {}),
        created_at=_to_utc(data.get("created_at")) or utcnow(),
    )


class FirestoreDataStore(BaseDataStore):
    """Cloud Firestore (Enterprise) metadata store paired with BlobStorage (GCS/Local) for large JSON blobs."""

    def __init__(
        self,
        client: Any = None,
        blob_storage: Optional[BlobStorage] = None,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> None:
        if client is None:
            from google.cloud import firestore  # Lazy import so local SQLite dev needs no cloud SDK

            kwargs: Dict[str, Any] = {}
            if project_id:
                kwargs["project"] = project_id
            if database:
                kwargs["database"] = database
            client = firestore.Client(**kwargs)
        self.client = client
        self.blobs = blob_storage or get_blob_storage()
        self._desc, self._asc, self._transactional, self._field_filter = _get_firestore_helpers()

    @property
    def dialect_name(self) -> str:
        return "firestore"

    def _col(self, name: str) -> Any:
        return self.client.collection(name)

    def _where(self, query: Any, field: str, op: str, value: Any) -> Any:
        return _apply_where(query, field, op, value, self._field_filter)

    def _get_all_chunked(self, refs: List[Any], chunk_size: int = _GET_ALL_CHUNK_SIZE) -> List[Any]:
        snaps: List[Any] = []
        for idx in range(0, len(refs), chunk_size):
            chunk = refs[idx : idx + chunk_size]
            if chunk:
                snaps.extend(self.client.get_all(chunk))
        return snaps

    def _delete_docs_in_batches(self, refs: Iterable[Any]) -> None:
        ref_list = list(refs)
        for idx in range(0, len(ref_list), _BATCH_DELETE_CHUNK_SIZE):
            chunk = ref_list[idx : idx + _BATCH_DELETE_CHUNK_SIZE]
            batch = self.client.batch()
            for ref in chunk:
                batch.delete(ref)
            batch.commit()

    def bootstrap(
        self,
        admin_email: str,
        admin_password_hash: str,
        admin_name: str,
        starter_plans: List[Dict[str, Any]],
    ) -> UserRecord:
        admin = self.get_first_admin()
        if admin is None:
            existing_by_email = self.get_user_by_email(admin_email)
            if existing_by_email is not None:
                admin = self.update_user(
                    existing_by_email.id,
                    role="admin",
                    is_active=True,
                    must_change_password=False,
                ) or existing_by_email
                self.ensure_admin_min_quota(admin.id, min_credits=100000, now=utcnow())
            else:
                try:
                    admin = self.create_user(
                        email=admin_email,
                        full_name=admin_name or "Administrateur Principal",
                        password_hash=admin_password_hash,
                        role="admin",
                        must_change_password=False,
                        initial_credits=100000,
                    )
                except ValueError:
                    admin = self.get_first_admin() or self.get_user_by_email(admin_email)
                    if admin is None:
                        raise

        if self.count_plans() == 0:
            for p in starter_plans:
                if self.get_plan_by_name(str(p.get("name") or "")) is None:
                    self.create_plan(**p)
        return admin

    # --- Users ---
    def get_user(self, user_id: str) -> Optional[UserRecord]:
        snap = self._col("users").document(user_id).get()
        if not snap.exists:
            return None
        return _doc_to_user(snap.id, snap.to_dict() or {})

    def get_user_by_email(self, email: str) -> Optional[UserRecord]:
        norm = (email or "").strip().lower()
        if not norm:
            return None
        lock_snap = self._col("user_emails").document(_email_lock_id(norm)).get()
        if lock_snap.exists:
            uid = (lock_snap.to_dict() or {}).get("user_id")
            if uid:
                found = self.get_user(str(uid))
                if found is not None:
                    return found
        snaps = list(self._where(self._col("users"), "email", "==", norm).limit(1).stream())
        if not snaps:
            return None
        return _doc_to_user(snaps[0].id, snaps[0].to_dict() or {})

    def find_users_by_full_name(self, full_name: str) -> List[UserRecord]:
        target = self._normalize_full_name(full_name)
        if not target:
            return []
        return [
            u
            for u in self.list_users()
            if self._normalize_full_name(u.full_name) == target
        ]

    def get_first_admin(self) -> Optional[UserRecord]:
        snaps = list(self._where(self._col("users"), "role", "==", "admin").limit(1).stream())
        if not snaps:
            return None
        return _doc_to_user(snaps[0].id, snaps[0].to_dict() or {})

    def list_users(self, role: Optional[str] = None) -> List[UserRecord]:
        q = self._col("users")
        if role in ("student", "admin"):
            q = self._where(q, "role", "==", role)
        q = q.order_by("created_at", direction=self._desc)
        return [_doc_to_user(s.id, s.to_dict() or {}) for s in q.stream()]

    def get_users_by_ids(self, user_ids: List[str]) -> Dict[str, UserRecord]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}
        refs = [self._col("users").document(uid) for uid in clean]
        out: Dict[str, UserRecord] = {}
        for snap in self._get_all_chunked(refs):
            if snap.exists:
                out[snap.id] = _doc_to_user(snap.id, snap.to_dict() or {})
        return out

    def count_users(self, role: Optional[str] = None) -> int:
        q = self._col("users")
        if role in ("student", "admin"):
            q = self._where(q, "role", "==", role)
        return sum(1 for _ in q.stream())

    def create_user(
        self,
        email: str,
        full_name: str,
        password_hash: str,
        role: str = "student",
        matricule: str = "",
        phone: str = "",
        must_change_password: bool = True,
        created_by_admin_id: Optional[str] = None,
        initial_credits: Optional[int] = None,
        gemini_api_key_id: Optional[str] = None,
        model_routing_config: Optional[Dict[str, Any]] = None,
    ) -> UserRecord:
        norm_email = email.strip().lower()
        resolved_role = role if role in ("admin", "student") else "student"
        credits = initial_credits if initial_credits is not None else (100000 if resolved_role == "admin" else 0)
        norm_key_id = (gemini_api_key_id or "").strip() or None
        if norm_key_id == "default":
            norm_key_id = None
        uid = str(uuid4())
        now = utcnow()

        user_doc = {
            "id": uid,
            "email": norm_email,
            "full_name": full_name.strip(),
            "matricule": matricule.strip(),
            "phone": phone.strip(),
            "password_hash": password_hash,
            "role": resolved_role,
            "is_active": True,
            "must_change_password": bool(must_change_password),
            "active_session_id": None,
            "session_switch_count": 0,
            "last_login_at": None,
            "last_login_ip": None,
            "created_at": now,
            "created_by_admin_id": created_by_admin_id,
            "gemini_api_key_id": norm_key_id,
            "model_routing_config": model_routing_config,
        }
        quota_doc = {
            "user_id": uid,
            "credits_total": int(credits),
            "credits_used": 0,
            "daily_38_limit": None,
            "updated_at": now,
        }
        lock_ref = self._col("user_emails").document(_email_lock_id(norm_email))
        user_ref = self._col("users").document(uid)
        quota_ref = self._col("user_ai_quotas").document(uid)

        @self._transactional
        def _txn(transaction: Any) -> None:
            lock_snap = lock_ref.get(transaction=transaction)
            if lock_snap.exists:
                raise ValueError("EMAIL_EXISTS")
            transaction.set(
                lock_ref,
                {"user_id": uid, "email": norm_email, "created_at": now},
            )
            transaction.set(user_ref, user_doc)
            transaction.set(quota_ref, quota_doc)

        # Fallback check for legacy/non-lock docs before transaction
        if list(self._where(self._col("users"), "email", "==", norm_email).limit(1).stream()):
            raise ValueError("EMAIL_EXISTS")

        _txn(self.client.transaction())
        return _doc_to_user(uid, user_doc)

    def update_user(self, user_id: str, **fields: Any) -> Optional[UserRecord]:
        ref = self._col("users").document(user_id)
        snap = ref.get()
        if not snap.exists:
            return None
        if fields:
            ref.update(fields)
        updated = ref.get()
        return _doc_to_user(updated.id, updated.to_dict() or {})

    def rotate_session_login(
        self,
        user_id: str,
        new_sid: str,
        ip_address: str,
        now: datetime,
    ) -> Optional[UserRecord]:
        ref = self._col("users").document(user_id)

        @self._transactional
        def _txn(transaction: Any) -> Optional[Dict[str, Any]]:
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return None
            data = dict(snap.to_dict() or {})
            switch_count = int(data.get("session_switch_count") or 0)
            if data.get("active_session_id"):
                switch_count += 1
            updates = {
                "active_session_id": new_sid,
                "session_switch_count": switch_count,
                "last_login_at": now,
                "last_login_ip": ip_address or "",
            }
            transaction.update(ref, updates)
            data.update(updates)
            return data

        merged = _txn(self.client.transaction())
        return _doc_to_user(user_id, merged) if merged is not None else None

    def delete_user_cascade(self, user_id: str) -> Optional[str]:
        user_ref = self._col("users").document(user_id)
        snap = user_ref.get()
        if not snap.exists:
            return None
        user_data = snap.to_dict() or {}
        email = str(user_data.get("email") or "")

        # Delete presentations & blobs owned by user
        pres_refs: List[Any] = []
        for ps in self._where(self._col("presentations"), "owner_user_id", "==", user_id).stream():
            pdata = ps.to_dict() or {}
            bkey = str(pdata.get("blob_key") or _presentation_blob_key(ps.id))
            self.blobs.delete(bkey)
            pres_refs.append(ps.reference)
        if pres_refs:
            self._delete_docs_in_batches(pres_refs)

        # Delete projects & blobs owned by user (plus presentation blob safety cleanup)
        proj_refs: List[Any] = []
        for pr in self._where(self._col("projects"), "owner_user_id", "==", user_id).stream():
            pdata = pr.to_dict() or {}
            self.blobs.delete(str(pdata.get("blob_key") or _project_blob_key(pr.id)))
            self.blobs.delete(_presentation_blob_key(pr.id))
            proj_refs.append(pr.reference)
            # Also ensure linked presentation doc removed if owner_user_id drifted
            pres_doc = self._col("presentations").document(pr.id)
            if pres_doc.get().exists:
                pres_doc.delete()
        if proj_refs:
            self._delete_docs_in_batches(proj_refs)

        # Delete usage events
        ev_refs = [s.reference for s in self._where(self._col("usage_events"), "user_id", "==", user_id).stream()]
        if ev_refs:
            self._delete_docs_in_batches(ev_refs)

        # Delete license grants
        grant_refs = [
            s.reference for s in self._where(self._col("user_license_grants"), "user_id", "==", user_id).stream()
        ]
        if grant_refs:
            self._delete_docs_in_batches(grant_refs)

        # Delete module entitlements (known keys + query fallback)
        ent_refs: Dict[str, Any] = {
            f"{user_id}__{mk}": self._col("user_module_entitlements").document(f"{user_id}__{mk}")
            for mk in MODULE_KEYS
        }
        for s in self._where(self._col("user_module_entitlements"), "user_id", "==", user_id).stream():
            ent_refs[s.id] = s.reference
        self._delete_docs_in_batches(ent_refs.values())

        # Delete AI quota, email uniqueness lock, and user doc
        tail_refs = [
            self._col("user_ai_quotas").document(user_id),
            user_ref,
        ]
        if email:
            tail_refs.append(self._col("user_emails").document(_email_lock_id(email)))
        self._delete_docs_in_batches(tail_refs)
        return email

    # --- Module Entitlements ---
    def get_entitlement(self, user_id: str, module_key: str) -> Optional[UserModuleEntitlementRecord]:
        snap = self._col("user_module_entitlements").document(f"{user_id}__{module_key}").get()
        if not snap.exists:
            return None
        return _doc_to_entitlement(snap.to_dict() or {})

    def list_entitlements(self, user_id: str) -> List[UserModuleEntitlementRecord]:
        refs = [self._col("user_module_entitlements").document(f"{user_id}__{mk}") for mk in MODULE_KEYS]
        out: List[UserModuleEntitlementRecord] = []
        for snap in self._get_all_chunked(refs):
            if snap.exists:
                out.append(_doc_to_entitlement(snap.to_dict() or {}))
        return out

    def list_entitlements_for_users(
        self,
        user_ids: List[str],
    ) -> Dict[str, Dict[str, UserModuleEntitlementRecord]]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}
        refs = [
            self._col("user_module_entitlements").document(f"{uid}__{mk}")
            for uid in clean
            for mk in MODULE_KEYS
        ]
        out: Dict[str, Dict[str, UserModuleEntitlementRecord]] = {}
        for snap in self._get_all_chunked(refs):
            if snap.exists:
                rec = _doc_to_entitlement(snap.to_dict() or {})
                out.setdefault(rec.user_id, {})[rec.module_key] = rec
        return out

    def upsert_entitlement(
        self,
        user_id: str,
        module_key: str,
        starts_at: datetime,
        expires_at: datetime,
        updated_at: datetime,
    ) -> UserModuleEntitlementRecord:
        doc = {
            "user_id": user_id,
            "module_key": module_key,
            "starts_at": starts_at,
            "expires_at": expires_at,
            "updated_at": updated_at,
        }
        self._col("user_module_entitlements").document(f"{user_id}__{module_key}").set(doc)
        return _doc_to_entitlement(doc)

    # --- AI Quotas ---
    def get_quota(self, user_id: str) -> Optional[UserAIQuotaRecord]:
        snap = self._col("user_ai_quotas").document(user_id).get()
        if not snap.exists:
            return None
        return _doc_to_quota(snap.id, snap.to_dict() or {})

    def list_quotas_for_users(self, user_ids: List[str]) -> Dict[str, UserAIQuotaRecord]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}
        refs = [self._col("user_ai_quotas").document(uid) for uid in clean]
        out: Dict[str, UserAIQuotaRecord] = {}
        for snap in self._get_all_chunked(refs):
            if snap.exists:
                out[snap.id] = _doc_to_quota(snap.id, snap.to_dict() or {})
        return out

    def ensure_quota(self, user_id: str, credits_total: int = 0) -> UserAIQuotaRecord:
        ref = self._col("user_ai_quotas").document(user_id)

        @self._transactional
        def _txn(transaction: Any) -> Dict[str, Any]:
            snap = ref.get(transaction=transaction)
            if snap.exists:
                return dict(snap.to_dict() or {})
            doc = {
                "user_id": user_id,
                "credits_total": int(credits_total),
                "credits_used": 0,
                "daily_38_limit": None,
                "updated_at": utcnow(),
            }
            transaction.set(ref, doc)
            return doc

        return _doc_to_quota(user_id, _txn(self.client.transaction()))

    def debit_quota_atomic(self, user_id: str, amount: int, now: datetime) -> UserAIQuotaRecord:
        ref = self._col("user_ai_quotas").document(user_id)
        debit = max(0, int(amount))

        @self._transactional
        def _txn(transaction: Any) -> Dict[str, Any]:
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                doc = {
                    "user_id": user_id,
                    "credits_total": 0,
                    "credits_used": debit,
                    "daily_38_limit": None,
                    "updated_at": now,
                }
                transaction.set(ref, doc)
                return doc
            data = dict(snap.to_dict() or {})
            data["credits_used"] = int(data.get("credits_used") or 0) + debit
            data["updated_at"] = now
            transaction.update(
                ref,
                {"credits_used": data["credits_used"], "updated_at": now},
            )
            return data

        return _doc_to_quota(user_id, _txn(self.client.transaction()))

    def add_quota_credits_atomic(
        self,
        user_id: str,
        add_credits: int,
        now: datetime,
        daily_38_limit: Optional[int] = None,
        update_daily_limit: bool = False,
    ) -> Tuple[UserAIQuotaRecord, int, Optional[int]]:
        ref = self._col("user_ai_quotas").document(user_id)

        @self._transactional
        def _txn(transaction: Any) -> Tuple[Dict[str, Any], int, Optional[int]]:
            snap = ref.get(transaction=transaction)
            if snap.exists:
                data = dict(snap.to_dict() or {})
            else:
                data = {
                    "user_id": user_id,
                    "credits_total": 0,
                    "credits_used": 0,
                    "daily_38_limit": None,
                    "updated_at": now,
                }
            old_total = int(data.get("credits_total") or 0)
            used = int(data.get("credits_used") or 0)
            old_daily = int(data["daily_38_limit"]) if data.get("daily_38_limit") is not None else None
            new_total = max(used, old_total + int(add_credits))
            data["credits_total"] = new_total
            if update_daily_limit:
                data["daily_38_limit"] = (
                    int(daily_38_limit) if (daily_38_limit is not None and int(daily_38_limit) >= 1) else None
                )
            data["updated_at"] = now
            transaction.set(ref, data)
            return data, old_total, old_daily

        doc, old_total, old_daily = _txn(self.client.transaction())
        return _doc_to_quota(user_id, doc), old_total, old_daily

    def ensure_admin_min_quota(
        self,
        user_id: str,
        min_credits: int = 100000,
        now: Optional[datetime] = None,
    ) -> UserAIQuotaRecord:
        ts = now or utcnow()
        ref = self._col("user_ai_quotas").document(user_id)

        @self._transactional
        def _txn(transaction: Any) -> Dict[str, Any]:
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                doc = {
                    "user_id": user_id,
                    "credits_total": min_credits,
                    "credits_used": 0,
                    "daily_38_limit": None,
                    "updated_at": ts,
                }
                transaction.set(ref, doc)
                return doc
            data = dict(snap.to_dict() or {})
            data["credits_total"] = max(int(data.get("credits_total") or 0), min_credits)
            data["updated_at"] = ts
            transaction.update(ref, {"credits_total": data["credits_total"], "updated_at": ts})
            return data

        return _doc_to_quota(user_id, _txn(self.client.transaction()))

    # --- License Plans ---
    def get_plan(self, plan_id: str) -> Optional[LicensePlanRecord]:
        snap = self._col("license_plans").document(plan_id).get()
        if not snap.exists:
            return None
        return _doc_to_plan(snap.id, snap.to_dict() or {})

    def get_plan_by_name(
        self,
        name: str,
        exclude_id: Optional[str] = None,
    ) -> Optional[LicensePlanRecord]:
        clean = name.strip()
        snaps = list(self._where(self._col("license_plans"), "name", "==", clean).stream())
        for snap in snaps:
            if exclude_id is not None and snap.id == exclude_id:
                continue
            return _doc_to_plan(snap.id, snap.to_dict() or {})
        return None

    def list_plans(self, include_inactive: bool = False) -> List[LicensePlanRecord]:
        q = self._col("license_plans")
        if not include_inactive:
            q = self._where(q, "is_active", "==", True)
        q = q.order_by("price_fcfa", direction=self._asc).order_by("created_at", direction=self._desc)
        return [_doc_to_plan(s.id, s.to_dict() or {}) for s in q.stream()]

    def get_plans_by_ids(self, plan_ids: List[str]) -> Dict[str, LicensePlanRecord]:
        clean = list({pid for pid in plan_ids if pid})
        if not clean:
            return {}
        refs = [self._col("license_plans").document(pid) for pid in clean]
        out: Dict[str, LicensePlanRecord] = {}
        for snap in self._get_all_chunked(refs):
            if snap.exists:
                out[snap.id] = _doc_to_plan(snap.id, snap.to_dict() or {})
        return out

    def count_plans(self) -> int:
        return sum(1 for _ in self._col("license_plans").stream())

    def create_plan(
        self,
        name: str,
        description: str = "",
        price_fcfa: int = 0,
        module1_days: int = 0,
        module2_days: int = 0,
        module3_days: int = 0,
        module4_days: int = 0,
        ai_quota_included: int = 0,
        is_active: bool = True,
        created_at: Optional[datetime] = None,
    ) -> LicensePlanRecord:
        pid = str(uuid4())
        doc = {
            "id": pid,
            "name": name.strip(),
            "description": description.strip(),
            "price_fcfa": int(price_fcfa),
            "module1_days": int(module1_days),
            "module2_days": int(module2_days),
            "module3_days": int(module3_days),
            "module4_days": int(module4_days),
            "ai_quota_included": int(ai_quota_included),
            "is_active": bool(is_active),
            "created_at": created_at or utcnow(),
        }
        self._col("license_plans").document(pid).set(doc)
        return _doc_to_plan(pid, doc)

    def update_plan(self, plan_id: str, **fields: Any) -> Optional[LicensePlanRecord]:
        ref = self._col("license_plans").document(plan_id)
        snap = ref.get()
        if not snap.exists:
            return None
        if fields:
            ref.update(fields)
        updated = ref.get()
        return _doc_to_plan(updated.id, updated.to_dict() or {})

    def delete_plan(self, plan_id: str) -> bool:
        ref = self._col("license_plans").document(plan_id)
        if not ref.get().exists:
            return False
        ref.delete()
        return True

    # --- License Grants ---
    def create_grant(
        self,
        user_id: str,
        plan_id: Optional[str],
        granted_by_admin_id: Optional[str],
        amount_paid_fcfa: int,
        payment_reference: str,
        notes: str,
        granted_at: datetime,
    ) -> UserLicenseGrantRecord:
        gid = str(uuid4())
        doc = {
            "id": gid,
            "user_id": user_id,
            "plan_id": plan_id,
            "granted_by_admin_id": granted_by_admin_id,
            "amount_paid_fcfa": int(amount_paid_fcfa),
            "payment_reference": payment_reference,
            "notes": notes,
            "granted_at": granted_at,
        }
        self._col("user_license_grants").document(gid).set(doc)
        return _doc_to_grant(gid, doc)

    def list_grants(
        self,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[UserLicenseGrantRecord]:
        q = self._col("user_license_grants")
        if user_id:
            q = self._where(q, "user_id", "==", user_id)
        q = q.order_by("granted_at", direction=self._desc)
        if limit is not None:
            q = q.limit(limit)
        return [_doc_to_grant(s.id, s.to_dict() or {}) for s in q.stream()]

    def has_grants_for_plan(self, plan_id: str) -> bool:
        snaps = list(self._where(self._col("user_license_grants"), "plan_id", "==", plan_id).limit(1).stream())
        return len(snaps) > 0

    # --- Usage Events ---
    def create_usage_event(
        self,
        user_id: str,
        module_key: str,
        action: str,
        project_id: Optional[str] = None,
        model_used: str = "",
        credits_cost: int = 0,
        tokens_used: int = 0,
        api_key_id: str = "default",
        status: str = "ok",
        ip_address: str = "",
        created_at: Optional[datetime] = None,
    ) -> UsageEventRecord:
        eid = str(uuid4())
        doc = {
            "id": eid,
            "user_id": user_id,
            "project_id": project_id,
            "module_key": module_key,
            "action": action,
            "model_used": (model_used or "")[:128],
            "credits_cost": max(0, int(credits_cost)),
            "tokens_used": max(0, int(tokens_used)),
            "api_key_id": ((api_key_id or "default").strip() or "default")[:64],
            "status": (status or "ok")[:32],
            "ip_address": (ip_address or "")[:64],
            "created_at": created_at or utcnow(),
        }
        self._col("usage_events").document(eid).set(doc)
        return _doc_to_event(eid, doc)

    def list_usage_events(
        self,
        user_id: Optional[str] = None,
        module_key: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[UsageEventRecord]:
        q = self._col("usage_events")
        if user_id:
            q = self._where(q, "user_id", "==", user_id)
        if module_key:
            q = self._where(q, "module_key", "==", module_key)
        if status and not user_id and not module_key and limit is None:
            # Dashboard stats case: filter by status == "ok" cleanly on dedicated single-field index
            q = self._where(q, "status", "==", status)
            return [_doc_to_event(s.id, s.to_dict() or {}) for s in q.stream()]
        q = q.order_by("created_at", direction=self._desc)
        if limit is not None and not status:
            q = q.limit(limit)
        items = [_doc_to_event(s.id, s.to_dict() or {}) for s in q.stream()]
        if status:
            items = [e for e in items if e.status == status]
        if limit is not None:
            items = items[:limit]
        return items

    def count_user_gemini38_today(self, user_id: str, day_start: datetime) -> int:
        from app.core.config import get_settings

        primary = (get_settings().gemini_primary_reasoning_model or "gemini-3.5-flash-lite").strip().lower()
        q = self._col("usage_events")
        q = self._where(q, "user_id", "==", user_id)
        q = self._where(q, "status", "==", "ok")
        q = self._where(q, "created_at", ">=", day_start)
        count = 0
        for snap in q.stream():
            data = snap.to_dict() or {}
            model = str(data.get("model_used") or "").lower()
            if "gemini-3.8" in model or (primary and primary in model):
                count += 1
        return count

    def sum_user_tokens_today(self, user_id: str, day_start: datetime) -> int:
        q = self._col("usage_events")
        q = self._where(q, "user_id", "==", user_id)
        q = self._where(q, "status", "==", "ok")
        q = self._where(q, "created_at", ">=", day_start)
        total = 0
        for snap in q.stream():
            data = snap.to_dict() or {}
            total += max(0, int(data.get("tokens_used") or 0))
        return total

    def count_user_ai_calls_since(
        self,
        user_id: str,
        day_start: datetime,
        minute_ago: datetime,
    ) -> Tuple[int, int]:
        earliest = min(day_start, minute_ago)
        q = self._col("usage_events")
        q = self._where(q, "user_id", "==", user_id)
        q = self._where(q, "status", "==", "ok")
        q = self._where(q, "created_at", ">=", earliest)
        rpm_count = 0
        rpd_count = 0
        non_ai_actions = {"export_docx", "export_pdf", "export_pptx", "export_csv", "patient_upsert"}
        for snap in q.stream():
            data = snap.to_dict() or {}
            act = str(data.get("action") or "")
            if act in non_ai_actions:
                continue
            ts = _to_utc(data.get("created_at"))
            if ts is None:
                continue
            if ts >= day_start:
                rpd_count += 1
            if ts >= minute_ago:
                rpm_count += 1
        return rpm_count, rpd_count

    def get_user_model_usage_windows(
        self,
        user_id: str,
        day_start: datetime,
        minute_ago: datetime,
    ) -> Dict[str, Dict[str, int]]:
        earliest = min(day_start, minute_ago)
        q = self._col("usage_events")
        q = self._where(q, "user_id", "==", user_id)
        q = self._where(q, "status", "==", "ok")
        q = self._where(q, "created_at", ">=", earliest)
        non_ai_actions = {"export_docx", "export_pdf", "export_pptx", "export_csv", "patient_upsert"}
        stats: Dict[str, Dict[str, int]] = {}
        for snap in q.stream():
            data = snap.to_dict() or {}
            if str(data.get("action") or "") in non_ai_actions:
                continue
            model = str(data.get("model_used") or "").strip().lower()
            if not model:
                continue
            ts = _to_utc(data.get("created_at"))
            if ts is None:
                continue
            bucket = stats.setdefault(model, {"rpd_used": 0, "tpm_used": 0})
            if ts >= day_start:
                bucket["rpd_used"] += 1
            if ts >= minute_ago:
                bucket["tpm_used"] += max(0, int(data.get("tokens_used") or 0))
        return stats

    def aggregate_ok_events_by_users(
        self,
        user_ids: List[str],
        export_actions: frozenset,
    ) -> Tuple[Dict[str, int], Dict[str, int]]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}, {}
        exports_by_user: Dict[str, int] = {}
        ai_calls_by_user: Dict[str, int] = {}

        if len(clean) == 1:
            uid = clean[0]
            q = self._where(self._where(self._col("usage_events"), "user_id", "==", uid), "status", "==", "ok")
            stream = q.stream()
        else:
            target_set = set(clean)
            q = self._where(self._col("usage_events"), "status", "==", "ok")
            stream = (s for s in q.stream() if (s.to_dict() or {}).get("user_id") in target_set)

        for snap in stream:
            data = snap.to_dict() or {}
            uid = str(data.get("user_id") or "")
            act = str(data.get("action") or "")
            if act in export_actions:
                exports_by_user[uid] = exports_by_user.get(uid, 0) + 1
            elif act != "patient_upsert":
                ai_calls_by_user[uid] = ai_calls_by_user.get(uid, 0) + 1
        return exports_by_user, ai_calls_by_user

    # --- Admin Audit Logs ---
    def create_audit_log(
        self,
        admin_id: Optional[str],
        target_user_id: Optional[str],
        action: str,
        details_json: Dict[str, Any],
        created_at: Optional[datetime] = None,
    ) -> AdminAuditLogRecord:
        lid = str(uuid4())
        doc = {
            "id": lid,
            "admin_id": admin_id,
            "target_user_id": target_user_id,
            "action": action,
            "details_json": details_json or {},
            "created_at": created_at or utcnow(),
        }
        self._col("admin_audit_logs").document(lid).set(doc)
        return _doc_to_audit(lid, doc)

    def list_audit_logs(
        self,
        target_user_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[AdminAuditLogRecord]:
        q = self._col("admin_audit_logs")
        if target_user_id:
            q = self._where(q, "target_user_id", "==", target_user_id)
        q = q.order_by("created_at", direction=self._desc).limit(limit)
        return [_doc_to_audit(s.id, s.to_dict() or {}) for s in q.stream()]

    # --- Projects & Presentations (Metadata in Firestore, large JSON in BlobStorage) ---
    def get_project_meta(self, project_id: str) -> Optional[ProjectMetaRecord]:
        snap = self._col("projects").document(project_id).get()
        if not snap.exists:
            return None
        return _doc_to_project_meta(snap.id, snap.to_dict() or {})

    def get_project_payload(
        self,
        project_id: str,
    ) -> Optional[Tuple[ProjectMetaRecord, Dict[str, Any]]]:
        snap = self._col("projects").document(project_id).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        meta = _doc_to_project_meta(snap.id, data)
        blob_key = meta.blob_key or _project_blob_key(project_id)
        raw = self.blobs.download_bytes(blob_key)
        if raw is None:
            logger.warning("Project %s metadata exists in Firestore, but blob %s was not found.", project_id, blob_key)
            return None
        payload = json.loads(raw.decode("utf-8"))
        return meta, payload

    def get_latest_project_meta_for_user(self, user_id: str) -> Optional[ProjectMetaRecord]:
        q = (
            self._where(self._col("projects"), "owner_user_id", "==", user_id)
            .order_by("updated_at", direction=self._desc)
            .limit(1)
        )
        snaps = list(q.stream())
        if not snaps:
            return None
        return _doc_to_project_meta(snaps[0].id, snaps[0].to_dict() or {})

    def list_project_metas(self, owner_user_id: Optional[str] = None) -> List[ProjectMetaRecord]:
        q = self._col("projects")
        if owner_user_id:
            q = self._where(q, "owner_user_id", "==", owner_user_id)
        q = q.order_by("updated_at", direction=self._desc)
        return [_doc_to_project_meta(s.id, s.to_dict() or {}) for s in q.stream()]

    def list_project_payloads(
        self,
        owner_user_id: Optional[str] = None,
    ) -> List[Tuple[ProjectMetaRecord, Dict[str, Any]]]:
        metas = self.list_project_metas(owner_user_id=owner_user_id)
        out: List[Tuple[ProjectMetaRecord, Dict[str, Any]]] = []
        for meta in metas:
            blob_key = meta.blob_key or _project_blob_key(meta.project_id)
            raw = self.blobs.download_bytes(blob_key)
            if raw is None:
                continue
            try:
                out.append((meta, json.loads(raw.decode("utf-8"))))
            except Exception:
                continue
        return out

    def list_project_metas_for_users(self, user_ids: List[str]) -> Dict[str, ProjectMetaRecord]:
        clean = list({uid for uid in user_ids if uid})
        if not clean:
            return {}
        out: Dict[str, ProjectMetaRecord] = {}
        if len(clean) <= _IN_QUERY_CHUNK_SIZE:
            q = self._where(self._col("projects"), "owner_user_id", "in", clean)
            candidates = [_doc_to_project_meta(s.id, s.to_dict() or {}) for s in q.stream()]
        else:
            target_set = set(clean)
            candidates = [
                m for m in self.list_project_metas() if m.owner_user_id in target_set
            ]
        for meta in candidates:
            prev = out.get(meta.owner_user_id)
            if prev is None or meta.updated_at > prev.updated_at:
                out[meta.owner_user_id] = meta
        return out

    def count_projects(self, owner_user_id: Optional[str] = None) -> int:
        q = self._col("projects")
        if owner_user_id:
            q = self._where(q, "owner_user_id", "==", owner_user_id)
        return sum(1 for _ in q.stream())

    def sum_patients_count(self) -> int:
        total = 0
        for snap in self._col("projects").stream():
            data = snap.to_dict() or {}
            total += int(data.get("patients_count") or 0)
        return total

    def save_project(
        self,
        meta: ProjectMetaRecord,
        payload_dict: Dict[str, Any],
    ) -> ProjectMetaRecord:
        blob_key = meta.blob_key or _project_blob_key(meta.project_id)
        raw_bytes = json.dumps(payload_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # Write blob first so Firestore metadata never points to a nonexistent object.
        self.blobs.upload_bytes(blob_key, raw_bytes, content_type="application/json")

        doc = {
            "project_id": meta.project_id,
            "owner_user_id": meta.owner_user_id,
            "name": meta.name,
            "locked_title": meta.locked_title,
            "locked_author_name": meta.locked_author_name,
            "locked_matricule": meta.locked_matricule,
            "topic_locked": bool(meta.topic_locked),
            "sections_validated_count": int(meta.sections_validated_count),
            "patients_count": int(meta.patients_count),
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
            "blob_key": blob_key,
        }
        self._col("projects").document(meta.project_id).set(doc)
        return _doc_to_project_meta(meta.project_id, doc)

    def delete_project(self, project_id: str) -> bool:
        proj_ref = self._col("projects").document(project_id)
        snap = proj_ref.get()
        if not snap.exists:
            return False
        data = snap.to_dict() or {}
        self.blobs.delete(str(data.get("blob_key") or _project_blob_key(project_id)))

        pres_ref = self._col("presentations").document(project_id)
        pres_snap = pres_ref.get()
        if pres_snap.exists:
            pres_data = pres_snap.to_dict() or {}
            self.blobs.delete(str(pres_data.get("blob_key") or _presentation_blob_key(project_id)))
            pres_ref.delete()
        else:
            self.blobs.delete(_presentation_blob_key(project_id))

        proj_ref.delete()
        return True

    def get_presentation_payload(self, project_id: str) -> Optional[Dict[str, Any]]:
        snap = self._col("presentations").document(project_id).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        blob_key = str(data.get("blob_key") or _presentation_blob_key(project_id))
        raw = self.blobs.download_bytes(blob_key)
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))

    def save_presentation(
        self,
        project_id: str,
        owner_user_id: str,
        version: str,
        slides_count: int,
        payload_dict: Dict[str, Any],
        updated_at: datetime,
    ) -> None:
        proj_meta = self.get_project_meta(project_id)
        if proj_meta is None:
            return
        resolved_owner = proj_meta.owner_user_id or owner_user_id
        blob_key = _presentation_blob_key(project_id)
        raw_bytes = json.dumps(payload_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.blobs.upload_bytes(blob_key, raw_bytes, content_type="application/json")

        doc = {
            "project_id": project_id,
            "owner_user_id": resolved_owner,
            "version": version or "3.1.0",
            "slides_count": int(slides_count),
            "updated_at": updated_at,
            "blob_key": blob_key,
        }
        self._col("presentations").document(project_id).set(doc)

    # --- Named Gemini API Keys ---
    def list_gemini_api_keys(self) -> List[GeminiApiKeyRecord]:
        snaps = list(self._col("gemini_api_keys").stream())
        items = [_doc_to_api_key(s.id, s.to_dict() or {}) for s in snaps]
        items.sort(key=lambda r: r.created_at)
        return items

    def get_gemini_api_key(self, key_id: str) -> Optional[GeminiApiKeyRecord]:
        snap = self._col("gemini_api_keys").document(key_id).get()
        if not snap.exists:
            return None
        return _doc_to_api_key(snap.id, snap.to_dict() or {})

    def create_gemini_api_key(
        self,
        name: str,
        api_key: str,
        owner_label: str = "",
        is_active: bool = True,
    ) -> GeminiApiKeyRecord:
        kid = str(uuid4())
        now = utcnow()
        doc = {
            "id": kid,
            "name": name.strip()[:128],
            "owner_label": (owner_label or "").strip()[:128],
            "api_key": api_key.strip(),
            "is_active": bool(is_active),
            "created_at": now,
            "updated_at": now,
        }
        self._col("gemini_api_keys").document(kid).set(doc)
        return _doc_to_api_key(kid, doc)

    def update_gemini_api_key(self, key_id: str, **fields: Any) -> Optional[GeminiApiKeyRecord]:
        ref = self._col("gemini_api_keys").document(key_id)
        snap = ref.get()
        if not snap.exists:
            return None
        updates = {k: v for k, v in fields.items() if v is not None}
        updates["updated_at"] = utcnow()
        ref.update(updates)
        updated = ref.get()
        return _doc_to_api_key(updated.id, updated.to_dict() or {})

    def delete_gemini_api_key(self, key_id: str) -> bool:
        ref = self._col("gemini_api_keys").document(key_id)
        snap = ref.get()
        if not snap.exists:
            return False
        # Reassign any users on this key back to default (None)
        for usnap in self._where(self._col("users"), "gemini_api_key_id", "==", key_id).stream():
            self._col("users").document(usnap.id).update({"gemini_api_key_id": None})
        ref.delete()
        return True


def as_store(db: Any) -> BaseDataStore:
    """Adapt either a BaseDataStore or a raw SQLAlchemy Session into BaseDataStore."""
    if isinstance(db, BaseDataStore):
        return db
    return SQLDataStore(db, owns_session=False)
