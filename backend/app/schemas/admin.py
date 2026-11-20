from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.auth import AIQuotaOut, ModuleEntitlementOut
from app.schemas.protocol import CoverMetadata


class LicensePlanCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    description: str = ""
    price_fcfa: int = Field(0, ge=0)
    module1_days: int = Field(0, ge=0)
    module2_days: int = Field(0, ge=0)
    module3_days: int = Field(0, ge=0)
    module4_days: int = Field(0, ge=0)
    ai_quota_included: int = Field(0, ge=0)
    is_active: bool = True


class LicensePlanUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=128)
    description: Optional[str] = None
    price_fcfa: Optional[int] = Field(None, ge=0)
    module1_days: Optional[int] = Field(None, ge=0)
    module2_days: Optional[int] = Field(None, ge=0)
    module3_days: Optional[int] = Field(None, ge=0)
    module4_days: Optional[int] = Field(None, ge=0)
    ai_quota_included: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class LicensePlanOut(BaseModel):
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


class AdminCreateUserRequest(BaseModel):
    email: str = Field(..., min_length=2, max_length=255)
    full_name: str = Field(..., min_length=2, max_length=255)
    matricule: str = ""
    phone: str = ""
    role: str = "student"  # student | admin
    temporary_password: Optional[str] = Field(None, min_length=8)
    plan_id: Optional[str] = None
    amount_paid_fcfa: Optional[int] = Field(None, ge=0)
    payment_reference: str = ""
    grant_notes: str = ""
    initial_protocol_title: Optional[str] = None
    study_type: str = "Étude transversale analytique"
    cover: Optional[CoverMetadata] = None


class AdminUpdateUserRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=255)
    matricule: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class AdminCreateUserResponse(BaseModel):
    user: "AdminStudentStatsRow"
    temporary_password: str
    created_project_id: Optional[str] = None


class AdminResetPasswordRequest(BaseModel):
    new_temporary_password: Optional[str] = Field(None, min_length=8)


class AdminResetPasswordResponse(BaseModel):
    user_id: str
    temporary_password: str


class AdminGrantPlanRequest(BaseModel):
    plan_id: str
    amount_paid_fcfa: Optional[int] = Field(None, ge=0)
    payment_reference: str = ""
    notes: str = ""


class AdminAdjustEntitlementRequest(BaseModel):
    expires_at: Optional[datetime] = None
    add_days: Optional[int] = None
    revoke: bool = False
    notes: str = ""


class AdminRechargeAIRequest(BaseModel):
    add_credits: int
    daily_38_limit: Optional[int] = Field(None, ge=0, le=5000)
    notes: str = ""


class AdminUpdateProjectIdentityRequest(BaseModel):
    title: Optional[str] = None
    author_name: Optional[str] = None
    matricule: Optional[str] = None
    topic_locked: Optional[bool] = None
    reason: str = ""


class AdminUpdateUserCoverRequest(BaseModel):
    cover: CoverMetadata
    study_type: Optional[str] = None
    topic_locked: Optional[bool] = None
    reason: str = ""


class AdminGrantRecordOut(BaseModel):
    id: str
    user_id: str
    user_email: str = ""
    user_name: str = ""
    plan_id: Optional[str] = None
    plan_name: str = ""
    granted_by_admin_id: Optional[str] = None
    amount_paid_fcfa: int
    payment_reference: str = ""
    notes: str = ""
    granted_at: datetime


class UsageEventOut(BaseModel):
    id: str
    user_id: str
    user_email: str = ""
    project_id: Optional[str] = None
    module_key: str
    action: str
    model_used: str = ""
    credits_cost: int = 0
    status: str = "ok"
    ip_address: str = ""
    created_at: datetime


class AdminAuditLogOut(BaseModel):
    id: str
    admin_id: Optional[str] = None
    admin_email: str = ""
    target_user_id: Optional[str] = None
    target_email: str = ""
    action: str
    details_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AdminStudentStatsRow(BaseModel):
    id: str
    email: str
    full_name: str
    matricule: str = ""
    phone: str = ""
    role: str
    is_active: bool
    must_change_password: bool
    session_switch_count: int = 0
    last_login_at: Optional[datetime] = None
    last_login_ip: Optional[str] = None
    created_at: datetime
    project_id: Optional[str] = None
    project_title: Optional[str] = None
    topic_locked: bool = False
    sections_validated_count: int = 0
    patients_count: int = 0
    exports_count: int = 0
    ai_calls_count: int = 0
    entitlements: Dict[str, ModuleEntitlementOut] = Field(default_factory=dict)
    ai_quota: AIQuotaOut = Field(default_factory=AIQuotaOut)
    status_summary: str = "none"  # admin | active | expiring_soon | expired | disabled | none


class AdminUserDetailOut(BaseModel):
    user: AdminStudentStatsRow
    grants: List[AdminGrantRecordOut] = Field(default_factory=list)
    recent_events: List[UsageEventOut] = Field(default_factory=list)
    recent_audit_logs: List[AdminAuditLogOut] = Field(default_factory=list)
    project_cover: Optional[CoverMetadata] = None
    project_study_type: Optional[str] = None


class AdminDashboardStatsOut(BaseModel):
    total_admins: int = 0
    total_students: int = 0
    active_students: int = 0
    expiring_soon_students: int = 0
    expired_students: int = 0
    disabled_students: int = 0
    total_revenue_fcfa: int = 0
    month_revenue_fcfa: int = 0
    total_projects: int = 0
    total_patient_records: int = 0
    total_exports: int = 0
    total_ai_credits_used: int = 0
    month_ai_credits_used: int = 0
    recent_grants: List[AdminGrantRecordOut] = Field(default_factory=list)
    expiring_soon_list: List[AdminStudentStatsRow] = Field(default_factory=list)
