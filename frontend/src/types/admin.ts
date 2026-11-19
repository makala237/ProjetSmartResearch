import type { AIQuotaOut, ModuleEntitlementOut } from "./auth";

export interface LicensePlanOut {
  id: string;
  name: string;
  description: string;
  price_fcfa: number;
  module1_days: number;
  module2_days: number;
  module3_days: number;
  module4_days: number;
  ai_quota_included: number;
  is_active: boolean;
  created_at: string;
}

export interface LicensePlanCreateRequest {
  name: string;
  description?: string;
  price_fcfa: number;
  module1_days: number;
  module2_days: number;
  module3_days: number;
  module4_days: number;
  ai_quota_included: number;
  is_active?: boolean;
}

export interface LicensePlanUpdateRequest {
  name?: string;
  description?: string;
  price_fcfa?: number;
  module1_days?: number;
  module2_days?: number;
  module3_days?: number;
  module4_days?: number;
  ai_quota_included?: number;
  is_active?: boolean;
}

export interface AdminCreateUserRequest {
  email: string;
  full_name: string;
  matricule?: string;
  phone?: string;
  role: "student" | "admin";
  temporary_password?: string;
  plan_id?: string | null;
  amount_paid_fcfa?: number | null;
  payment_reference?: string;
  grant_notes?: string;
  initial_protocol_title?: string;
  study_type?: string;
}

export interface AdminUpdateUserRequest {
  full_name?: string;
  matricule?: string;
  phone?: string;
  role?: "student" | "admin";
  is_active?: boolean;
}

export interface AdminResetPasswordResponse {
  user_id: string;
  temporary_password: string;
}

export interface AdminGrantPlanRequest {
  plan_id: string;
  amount_paid_fcfa?: number;
  payment_reference?: string;
  notes?: string;
}

export interface AdminAdjustEntitlementRequest {
  expires_at?: string;
  add_days?: number;
  revoke?: boolean;
  notes?: string;
}

export interface AdminRechargeAIRequest {
  add_credits: number;
  notes?: string;
}

export interface AdminUpdateProjectIdentityRequest {
  title?: string;
  author_name?: string;
  matricule?: string;
  topic_locked?: boolean;
  reason?: string;
}

export interface AdminGrantRecordOut {
  id: string;
  user_id: string;
  user_email: string;
  user_name: string;
  plan_id?: string | null;
  plan_name: string;
  granted_by_admin_id?: string | null;
  amount_paid_fcfa: number;
  payment_reference: string;
  notes: string;
  granted_at: string;
}

export interface UsageEventOut {
  id: string;
  user_id: string;
  user_email: string;
  project_id?: string | null;
  module_key: string;
  action: string;
  model_used: string;
  credits_cost: number;
  status: string;
  ip_address: string;
  created_at: string;
}

export interface AdminAuditLogOut {
  id: string;
  admin_id?: string | null;
  admin_email: string;
  target_user_id?: string | null;
  target_email: string;
  action: string;
  details_json: Record<string, unknown>;
  created_at: string;
}

export interface AdminStudentStatsRow {
  id: string;
  email: string;
  full_name: string;
  matricule: string;
  phone: string;
  role: string;
  is_active: boolean;
  must_change_password: boolean;
  session_switch_count: number;
  last_login_at?: string | null;
  last_login_ip?: string | null;
  created_at: string;
  project_id?: string | null;
  project_title?: string | null;
  topic_locked: boolean;
  sections_validated_count: number;
  patients_count: number;
  exports_count: number;
  ai_calls_count: number;
  entitlements: Record<string, ModuleEntitlementOut>;
  ai_quota: AIQuotaOut;
  status_summary: "admin" | "active" | "expiring_soon" | "expired" | "disabled" | "none" | string;
}

export interface AdminCreateUserResponse {
  user: AdminStudentStatsRow;
  temporary_password: string;
  created_project_id?: string | null;
}

export interface AdminUserDetailOut {
  user: AdminStudentStatsRow;
  grants: AdminGrantRecordOut[];
  recent_events: UsageEventOut[];
  recent_audit_logs: AdminAuditLogOut[];
}

export interface AdminDashboardStatsOut {
  total_students: number;
  active_students: number;
  expiring_soon_students: number;
  expired_students: number;
  disabled_students: number;
  total_revenue_fcfa: number;
  month_revenue_fcfa: number;
  total_projects: number;
  total_patient_records: number;
  total_exports: number;
  total_ai_credits_used: number;
  month_ai_credits_used: number;
  recent_grants: AdminGrantRecordOut[];
  expiring_soon_list: AdminStudentStatsRow[];
}
