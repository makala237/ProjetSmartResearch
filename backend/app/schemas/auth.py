from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class LoginRequest(BaseModel):
    email: Optional[str] = Field(default=None, max_length=255)
    username: Optional[str] = Field(default=None, max_length=255)
    identifier: Optional[str] = Field(default=None, max_length=255)
    password: str = Field(..., min_length=1)

    def resolved_identifier(self) -> str:
        for candidate in (self.identifier, self.username, self.email):
            if candidate is not None and candidate.strip():
                return candidate.strip()
        return ""

    @model_validator(mode="after")
    def _validate_identifier(self) -> "LoginRequest":
        if len(self.resolved_identifier()) < 2:
            raise ValueError("Identifiant requis (au moins 2 caractères).")
        return self


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
    active_reasoning_model: str = "gemini-3.5-flash-lite"
    using_35_fallback: bool = False


class OnboardingStateOut(BaseModel):
    welcome_tour_completed: bool = False
    welcome_tour_dismissed: bool = False
    seen_features: List[str] = Field(default_factory=list)
    tips_enabled: bool = True


class OnboardingStateUpdateRequest(BaseModel):
    welcome_tour_completed: Optional[bool] = None
    welcome_tour_dismissed: Optional[bool] = None
    mark_feature_seen: Optional[str] = Field(default=None, max_length=64)
    tips_enabled: Optional[bool] = None
    reset: bool = False


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
    onboarding_state: OnboardingStateOut = Field(default_factory=OnboardingStateOut)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_change_password: bool = False
    user: UserMeOut
