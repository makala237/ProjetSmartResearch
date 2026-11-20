from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class ModuleEntitlementOut(BaseModel):
    module_key: str
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_active: bool = False
    days_remaining: int = 0


class AIQuotaOut(BaseModel):
    credits_total: int = 0
    credits_used: int = 0
    credits_remaining: int = 0
    daily_38_limit: int = 15
    daily_38_used: int = 0
    daily_38_remaining: int = 15
    active_reasoning_model: str = "gemini-3.8-flash"
    using_35_fallback: bool = False


class UserMeOut(BaseModel):
    id: str
    email: str
    full_name: str
    matricule: str = ""
    phone: str = ""
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: Optional[datetime] = None
    has_project: bool = False
    project_id: Optional[str] = None
    entitlements: Dict[str, ModuleEntitlementOut] = Field(default_factory=dict)
    ai_quota: AIQuotaOut = Field(default_factory=AIQuotaOut)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_change_password: bool = False
    user: UserMeOut
