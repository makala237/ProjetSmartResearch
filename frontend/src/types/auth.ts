export type ModuleKey = "module1" | "module2" | "module3" | "module4";

export interface ModuleEntitlementOut {
  module_key: ModuleKey | string;
  starts_at?: string | null;
  expires_at?: string | null;
  is_active: boolean;
  days_remaining: number;
}

export interface AIQuotaOut {
  credits_total: number;
  credits_used: number;
  credits_remaining: number;
}

export interface UserMeOut {
  id: string;
  email: string;
  full_name: string;
  matricule: string;
  phone: string;
  role: "admin" | "student" | string;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at?: string | null;
  has_project: boolean;
  project_id?: string | null;
  entitlements: Record<string, ModuleEntitlementOut>;
  ai_quota: AIQuotaOut;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  must_change_password: boolean;
  user: UserMeOut;
}
