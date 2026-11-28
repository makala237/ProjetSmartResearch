import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  BookOpen,
  CheckCircle2,
  ClipboardCopy,
  Clock,
  Cpu,
  CreditCard,
  Edit3,
  ExternalLink,
  FileText,
  GraduationCap,
  KeyRound,
  LifeBuoy,
  Lock,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserCheck,
  UserPlus,
  Users,
  UserX,
  Zap,
} from "lucide-react";
import { api } from "../api/client";
import {
  CoverPageForm,
  createDefaultCoverMetadata,
  emptySupervisor,
  finalizeCoverMetadataPayload,
  withStructuredSupervisors,
} from "./CoverPageForm";
import { ModelRoutingEditor } from "./ModelRoutingEditor";
import type {
  CourseNoteRecord,
  CoverMetadata,
  PedagogySectionCatalogItem,
  SupervisorInfo,
} from "../types/protocol";
import type {
  AdminAuditLogOut,
  AdminDashboardStatsOut,
  AdminStudentStatsRow,
  AdminUserDetailOut,
  GeminiApiKeyStatsOut,
  LicensePlanOut,
  ModelCatalogResponse,
  UsageEventOut,
  UserModelRoutingConfig,
} from "../types/admin";

interface AdminPortalProps {
  currentAdminId: string;
  onExitAdmin: () => void;
  onOpenUserWorkspace?: (projectId: string, studentName: string) => void | Promise<void>;
}

type AdminTab = "dashboard" | "users" | "assistance" | "plans" | "api_keys" | "pedagogy" | "stats";

interface AdminSupportRequestItem {
  id: string;
  created_at: string;
  user_id: string;
  user_name: string;
  user_email: string;
  project_id: string;
  project_name: string;
  module: string;
  section_id: string;
  category: string;
  message: string;
  resolved: boolean;
  resolved_at?: string | null;
  resolved_by?: string;
  resolution_note?: string;
}

const SUPPORT_CATEGORY_LABELS: Record<string, string> = {
  formatting: "Mise en forme",
  content: "Contenu / Rédaction",
  tables_figures: "Tableaux / Schémas",
  references: "Références Vancouver",
  export: "Export Word / PDF",
  other: "Autre",
};

const MODULE_META: Array<{ key: string; short: string; label: string }> = [
  { key: "module1", short: "M1", label: "Rédaction" },
  { key: "module2", short: "M2", label: "Word/PDF" },
  { key: "module3", short: "M3", label: "Masque CRF" },
  { key: "module4", short: "M4", label: "PowerPoint" },
];

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("fr-FR", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

function fmtDateTime(iso?: string | null): string {
  if (!iso) return "Jamais";
  try {
    return new Date(iso).toLocaleString("fr-FR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function statusBadge(status: string): { label: string; cls: string } {
  switch (status) {
    case "admin":
      return { label: "Admin", cls: "bg-indigo-100 text-indigo-800" };
    case "active":
      return { label: "Actif", cls: "bg-emerald-100 text-emerald-800" };
    case "expiring_soon":
      return { label: "Expire ≤ 7 j", cls: "bg-amber-100 text-amber-800" };
    case "expired":
      return { label: "Expiré (LS)", cls: "bg-rose-100 text-rose-800" };
    case "disabled":
      return { label: "Suspendu", cls: "bg-slate-200 text-slate-700" };
    default:
      return { label: "Aucune licence", cls: "bg-slate-100 text-slate-600" };
  }
}

export function AdminPortal({
  currentAdminId,
  onExitAdmin,
  onOpenUserWorkspace,
}: AdminPortalProps) {
  const [tab, setTab] = useState<AdminTab>("dashboard");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [dashboard, setDashboard] = useState<AdminDashboardStatsOut | null>(null);
  const [plans, setPlans] = useState<LicensePlanOut[]>([]);
  const [users, setUsers] = useState<AdminStudentStatsRow[]>([]);
  const [apiKeys, setApiKeys] = useState<GeminiApiKeyStatsOut[]>([]);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [userDetail, setUserDetail] = useState<AdminUserDetailOut | null>(null);

  // Named Gemini API Key form & expanded card state
  const [newKeyName, setNewKeyName] = useState("");
  const [newKeyOwner, setNewKeyOwner] = useState("");
  const [newKeySecret, setNewKeySecret] = useState("");
  const [expandedKeyId, setExpandedKeyId] = useState<string | null>("default");
  const [newUserApiKeyId, setNewUserApiKeyId] = useState<string>("default");
  const [modelCatalog, setModelCatalog] = useState<ModelCatalogResponse | null>(null);
  const [newUserModelRouting, setNewUserModelRouting] = useState<UserModelRoutingConfig>({
    enabled: false,
    models: [],
  });
  const [drawerModelRouting, setDrawerModelRouting] = useState<UserModelRoutingConfig>({
    enabled: false,
    models: [],
  });

  // User list filters
  const [userSearch, setUserSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  // Create User form state
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newFullName, setNewFullName] = useState("");
  const [newMatricule, setNewMatricule] = useState("");
  const [newPhone, setNewPhone] = useState("");
  const [newRole, setNewRole] = useState<"student" | "admin">("student");
  const [newPlanId, setNewPlanId] = useState("");
  const [newAmountFcfa, setNewAmountFcfa] = useState<string>("");
  const [newPaymentRef, setNewPaymentRef] = useState("");
  const [newStudyType, setNewStudyType] = useState("Étude transversale analytique");
  const [newCover, setNewCover] = useState<CoverMetadata>(() =>
    createDefaultCoverMetadata()
  );
  const [newCoDirDraft, setNewCoDirDraft] =
    useState<SupervisorInfo>(emptySupervisor);
  const [stagedUniLogo, setStagedUniLogo] = useState<File | null>(null);
  const [stagedFacLogo, setStagedFacLogo] = useState<File | null>(null);
  const [newTempPassInput, setNewTempPassInput] = useState("");

  // Revealed temp credentials banner (after create or reset-password)
  const [revealedCreds, setRevealedCreds] = useState<{
    email: string;
    password: string;
    label: string;
  } | null>(null);

  // Selected user quick forms
  const [grantPlanId, setGrantPlanId] = useState("");
  const [grantAmount, setGrantAmount] = useState<string>("");
  const [grantRef, setGrantRef] = useState("");
  const [grantNotes, setGrantNotes] = useState("");
  const [rechargeCredits, setRechargeCredits] = useState<string>("50");
  const [rechargeDaily38Limit, setRechargeDaily38Limit] = useState<string>("");
  const [rechargeNotes, setRechargeNotes] = useState("");
  const [confirmDeleteUserId, setConfirmDeleteUserId] = useState<string | null>(null);
  const [confirmRoleChangeId, setConfirmRoleChangeId] = useState<string | null>(null);

  // Full Admin Cover Editor state
  const [editCover, setEditCover] = useState<CoverMetadata>(() =>
    createDefaultCoverMetadata()
  );
  const [editCoDirDraft, setEditCoDirDraft] =
    useState<SupervisorInfo>(emptySupervisor);
  const [editStudyType, setEditStudyType] = useState("Étude transversale analytique");
  const [ovrLocked, setOvrLocked] = useState(true);
  const [ovrReason, setOvrReason] = useState("");

  // Plan CRUD form
  const [editingPlanId, setEditingPlanId] = useState<string | null>(null);
  const [planName, setPlanName] = useState("");
  const [planDesc, setPlanDesc] = useState("");
  const [planPrice, setPlanPrice] = useState<string>("15000");
  const [planM1, setPlanM1] = useState<string>("30");
  const [planM2, setPlanM2] = useState<string>("30");
  const [planM3, setPlanM3] = useState<string>("180");
  const [planM4, setPlanM4] = useState<string>("30");
  const [planAi, setPlanAi] = useState<string>("150");
  const [planActive, setPlanActive] = useState(true);

  // Student stats & logs
  const [statsRows, setStatsRows] = useState<AdminStudentStatsRow[]>([]);
  const [statsSearch, setStatsSearch] = useState("");
  const [statsSortBy, setStatsSortBy] = useState("last_login");
  const [usageEvents, setUsageEvents] = useState<UsageEventOut[]>([]);
  const [auditLogs, setAuditLogs] = useState<AdminAuditLogOut[]>([]);

  // Assistance / document update requests
  const [supportRequests, setSupportRequests] = useState<AdminSupportRequestItem[]>([]);
  const [supportFilter, setSupportFilter] = useState<"pending" | "resolved" | "all">("pending");
  const [resolveNotes, setResolveNotes] = useState<Record<string, string>>({});
  const [resolvingRequestId, setResolvingRequestId] = useState<string | null>(null);

  // Pedagogy / Course notes state
  const [pedagogyNotes, setPedagogyNotes] = useState<CourseNoteRecord[]>([]);
  const [pedagogySectionsCatalog, setPedagogySectionsCatalog] = useState<PedagogySectionCatalogItem[]>([]);
  const [pedagogyFilterSectionId, setPedagogyFilterSectionId] = useState<string>("");
  const [newNoteTitle, setNewNoteTitle] = useState("");
  const [newNoteContent, setNewNoteContent] = useState("");
  const [newNoteFile, setNewNoteFile] = useState<File | null>(null);
  const [newNoteSectionIds, setNewNoteSectionIds] = useState<string[]>([]);
  const [editingNoteId, setEditingNoteId] = useState<string | null>(null);
  const [editingNoteSectionIds, setEditingNoteSectionIds] = useState<string[]>([]);
  const [pedagogyBusy, setPedagogyBusy] = useState(false);

  const loadAllCore = useCallback(async () => {
    setError(null);
    try {
      const [dashRes, plansRes, usersRes, keysRes, catalogRes, reqsRes] = await Promise.all([
        api.adminGetDashboard(),
        api.adminListPlans(true),
        api.adminListUsers({
          role: roleFilter || undefined,
          search: userSearch || undefined,
          status: statusFilter || undefined,
        }),
        api.adminListApiKeys().catch(() => [] as GeminiApiKeyStatsOut[]),
        api.adminGetModelCatalog().catch(() => null),
        api.adminListUpdateRequests(100).catch(() => [] as AdminSupportRequestItem[]),
      ]);
      setDashboard(dashRes);
      setPlans(plansRes);
      setUsers(usersRes);
      setApiKeys(keysRes);
      setSupportRequests(reqsRes);
      if (catalogRes) setModelCatalog(catalogRes);
      if (!newPlanId && plansRes.some((p) => p.is_active)) {
        const firstActive = plansRes.find((p) => p.is_active);
        if (firstActive) {
          setNewPlanId(firstActive.id);
          setNewAmountFcfa(String(firstActive.price_fcfa));
          setGrantPlanId(firstActive.id);
          setGrantAmount(String(firstActive.price_fcfa));
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [roleFilter, userSearch, statusFilter, newPlanId]);

  const loadUserDetail = useCallback(async (uid: string) => {
    try {
      const det = await api.adminGetUserDetail(uid);
      setUserDetail(det);
      setDrawerModelRouting(
        det.user.model_routing_config && det.user.model_routing_config.enabled
          ? {
              enabled: true,
              models: det.user.model_routing_config.models.map((m) => ({ ...m })),
              copilot_text_models: (
                det.user.model_routing_config.copilot_text_models ?? []
              ).map((m) => ({ ...m })),
              copilot_voice_models: (
                det.user.model_routing_config.copilot_voice_models ?? []
              ).map((m) => ({ ...m })),
              supervisor_media_models: (
                det.user.model_routing_config.supervisor_media_models ?? []
              ).map((m) => ({ ...m })),
            }
          : {
              enabled: false,
              models: [],
              copilot_text_models: [],
              copilot_voice_models: [],
              supervisor_media_models: [],
            }
      );
      if (det.project_cover) {
        setEditCover(withStructuredSupervisors(det.project_cover));
      } else {
        setEditCover(
          createDefaultCoverMetadata({
            title: det.user.project_title || "",
            author_name: det.user.full_name || "",
            matricule: det.user.matricule || "",
            student_phone: det.user.phone || "",
            student_email: det.user.email || "",
          })
        );
      }
      setEditCoDirDraft(emptySupervisor());
      setEditStudyType(det.project_study_type || "Étude transversale analytique");
      setOvrLocked(det.user.topic_locked);
      setOvrReason("");
      setConfirmDeleteUserId(null);
      setConfirmRoleChangeId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadStatsTab = useCallback(async () => {
    try {
      const [stRes, evRes, logRes] = await Promise.all([
        api.adminGetStudentStats({
          search: statsSearch || undefined,
          sort_by: statsSortBy,
        }),
        api.adminListUsageEvents({ limit: 60 }),
        api.adminListAuditLogs(60),
      ]);
      setStatsRows(stRes);
      setUsageEvents(evRes);
      setAuditLogs(logRes);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [statsSearch, statsSortBy]);

  const loadPedagogyTab = useCallback(async () => {
    try {
      const res = await api.adminListPedagogyNotes(pedagogyFilterSectionId || undefined);
      setPedagogyNotes(res.notes);
      setPedagogySectionsCatalog(res.sections_catalog);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [pedagogyFilterSectionId]);

  useEffect(() => {
    loadAllCore();
  }, [loadAllCore]);

  useEffect(() => {
    if (selectedUserId) {
      loadUserDetail(selectedUserId);
    }
  }, [selectedUserId, loadUserDetail]);

  useEffect(() => {
    if (tab === "stats") {
      loadStatsTab();
    } else if (tab === "pedagogy") {
      loadPedagogyTab();
    }
  }, [tab, loadStatsTab, loadPedagogyTab]);

  async function refreshAll() {
    setBusy(true);
    await loadAllCore();
    if (selectedUserId) await loadUserDetail(selectedUserId);
    if (tab === "stats") await loadStatsTab();
    if (tab === "pedagogy") await loadPedagogyTab();
    setBusy(false);
  }

  async function handleCreateApiKey(e: React.FormEvent) {
    e.preventDefault();
    if (!newKeyName.trim() || !newKeySecret.trim()) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const created = await api.adminCreateApiKey({
        name: newKeyName.trim(),
        owner_label: newKeyOwner.trim(),
        api_key: newKeySecret.trim(),
        is_active: true,
      });
      setNewKeyName("");
      setNewKeyOwner("");
      setNewKeySecret("");
      setExpandedKeyId(created.id);
      setNotice(`Clé API « ${created.name} » ajoutée au pool.`);
      await loadAllCore();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleApiKey(k: GeminiApiKeyStatsOut) {
    setBusy(true);
    setError(null);
    try {
      await api.adminUpdateApiKey(k.id, { is_active: !k.is_active });
      setNotice(`Clé « ${k.name} » ${!k.is_active ? "activée" : "désactivée (repli sur défaut)"}.`);
      await loadAllCore();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteApiKey(k: GeminiApiKeyStatsOut) {
    setBusy(true);
    setError(null);
    try {
      await api.adminDeleteApiKey(k.id);
      setNotice(`Clé « ${k.name} » supprimée (utilisateurs replacés sur la clé par défaut).`);
      await loadAllCore();
      if (selectedUserId) await loadUserDetail(selectedUserId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleAssignUserApiKey(userId: string, keyId: string) {
    setBusy(true);
    setError(null);
    try {
      await api.adminUpdateUser(userId, { gemini_api_key_id: keyId || "default" });
      const keyLabel = apiKeys.find((k) => k.id === keyId)?.name || "Clé système par défaut";
      setNotice(`Clé API routée vers « ${keyLabel} ».`);
      await loadAllCore();
      if (selectedUserId === userId) {
        await loadUserDetail(userId);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveUserModelRouting(userId: string) {
    setBusy(true);
    setError(null);
    try {
      const hasAnyChain =
        drawerModelRouting.models.length > 0 ||
        (drawerModelRouting.copilot_text_models?.length ?? 0) > 0 ||
        (drawerModelRouting.copilot_voice_models?.length ?? 0) > 0 ||
        (drawerModelRouting.supervisor_media_models?.length ?? 0) > 0;
      const payloadCfg: UserModelRoutingConfig =
        drawerModelRouting.enabled && hasAnyChain
          ? drawerModelRouting
          : { enabled: false, models: [], copilot_text_models: [], copilot_voice_models: [], supervisor_media_models: [] };
      await api.adminUpdateUser(userId, { model_routing_config: payloadCfg });
      setNotice(
        payloadCfg.enabled
          ? `Chaîne personnalisée enregistrée (${payloadCfg.models.length} modèle(s) texte principal${
              payloadCfg.models[0]?.model ? ` : ${payloadCfg.models[0].model}` : ""
            }${(payloadCfg.copilot_voice_models?.length ?? 0) > 0 ? `, micro : ${payloadCfg.copilot_voice_models![0].model}` : ""}).`
          : "Chaîne de modèles réinitialisée sur la configuration système par défaut."
      );
      await loadAllCore();
      if (selectedUserId === userId) {
        await loadUserDetail(userId);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function copyCredentials(email: string, pw: string) {
    const txt = `Smart Research\nIdentifiant : ${email}\nMot de passe temporaire : ${pw}`;
    navigator.clipboard?.writeText(txt).then(() => {
      setNotice("Identifiants copiés dans le presse-papiers.");
    });
  }

  async function handleCreateUser(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const isStudent = newRole === "student";
      const finalCover = isStudent
        ? finalizeCoverMetadataPayload(
            {
              ...newCover,
              author_name: newCover.author_name.trim() || newFullName.trim(),
              matricule: newCover.matricule.trim() || newMatricule.trim(),
              student_phone: newCover.student_phone.trim() || newPhone.trim(),
              student_email:
                newCover.student_email.trim() ||
                (newEmail.includes("@") ? newEmail.trim() : ""),
            },
            newCoDirDraft
          )
        : undefined;

      const res = await api.adminCreateUser({
        email: newEmail.trim(),
        full_name: newFullName.trim(),
        matricule: isStudent ? newMatricule.trim() : "",
        phone: newPhone.trim(),
        role: newRole,
        temporary_password: newTempPassInput.trim() || undefined,
        plan_id: isStudent && newPlanId ? newPlanId : undefined,
        amount_paid_fcfa:
          isStudent && newAmountFcfa ? Number(newAmountFcfa) : undefined,
        payment_reference: isStudent ? newPaymentRef.trim() : "",
        study_type: isStudent
          ? newStudyType.trim() || "Étude transversale analytique"
          : undefined,
        cover: finalCover,
        initial_protocol_title: isStudent
          ? finalCover?.title.trim() || undefined
          : undefined,
        gemini_api_key_id: newUserApiKeyId && newUserApiKeyId !== "default" ? newUserApiKeyId : "default",
        model_routing_config:
          newUserModelRouting.enabled &&
          (newUserModelRouting.models.length > 0 ||
            (newUserModelRouting.copilot_text_models?.length ?? 0) > 0 ||
            (newUserModelRouting.copilot_voice_models?.length ?? 0) > 0 ||
            (newUserModelRouting.supervisor_media_models?.length ?? 0) > 0)
            ? newUserModelRouting
            : undefined,
      });

      if (isStudent && res.created_project_id) {
        for (const [slot, file] of [
          ["university", stagedUniLogo],
          ["faculty", stagedFacLogo],
        ] as const) {
          if (file) {
            try {
              await api.uploadCoverLogo(res.created_project_id, slot, file);
            } catch (logoErr) {
              setError(
                logoErr instanceof Error
                  ? `Compte créé, mais erreur lors de l'import du logo (${slot}) : ${logoErr.message}`
                  : `Compte créé, mais erreur d'import du logo (${slot}).`
              );
            }
          }
        }
      }

      setRevealedCreds({
        email: res.user.email,
        password: res.temporary_password,
        label:
          newRole === "admin"
            ? `Compte Administrateur ${res.user.full_name} créé avec toutes les permissions`
            : `Compte Étudiant ${res.user.full_name} créé avec page de garde verrouillée`,
      });
      setSelectedUserId(res.user.id);
      setShowCreateUser(false);
      setNewEmail("");
      setNewFullName("");
      setNewUserModelRouting({ enabled: false, models: [] });
      setNewMatricule("");
      setNewPhone("");
      setNewPaymentRef("");
      setNewStudyType("Étude transversale analytique");
      setNewCover(createDefaultCoverMetadata());
      setNewCoDirDraft(emptySupervisor());
      setStagedUniLogo(null);
      setStagedFacLogo(null);
      setNewTempPassInput("");
      await loadAllCore();
      await loadUserDetail(res.user.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleChangeUserRole(
    u: AdminStudentStatsRow,
    targetRole: "student" | "admin"
  ) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.adminUpdateUser(u.id, { role: targetRole });
      setConfirmRoleChangeId(null);
      setNotice(
        targetRole === "admin"
          ? `${u.full_name} promu Administrateur avec accès complet.`
          : `${u.full_name} repassé au rôle Étudiant.`
      );
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleActive(u: AdminStudentStatsRow) {
    setBusy(true);
    setError(null);
    try {
      await api.adminUpdateUser(u.id, { is_active: !u.is_active });
      setNotice(u.is_active ? "Compte suspendu." : "Compte réactivé.");
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleResetPassword(u: AdminStudentStatsRow) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.adminResetPassword(u.id);
      setRevealedCreds({
        email: u.email,
        password: res.temporary_password,
        label: `Nouveau mot de passe temporaire pour ${u.full_name}`,
      });
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteUser(uid: string) {
    setBusy(true);
    setError(null);
    try {
      await api.adminDeleteUser(uid);
      setNotice("Utilisateur supprimé.");
      setSelectedUserId(null);
      setUserDetail(null);
      setConfirmDeleteUserId(null);
      await loadAllCore();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleGrantPlan(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedUserId || !grantPlanId) return;
    setBusy(true);
    setError(null);
    try {
      await api.adminGrantPlan(selectedUserId, {
        plan_id: grantPlanId,
        amount_paid_fcfa: grantAmount ? Number(grantAmount) : undefined,
        payment_reference: grantRef.trim(),
        notes: grantNotes.trim(),
      });
      setGrantRef("");
      setGrantNotes("");
      setNotice("Licence attribuée / prolongée avec succès.");
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleAdjustModule(moduleKey: string, opts: { add_days?: number; revoke?: boolean }) {
    if (!selectedUserId) return;
    setBusy(true);
    setError(null);
    try {
      await api.adminAdjustEntitlement(selectedUserId, moduleKey, opts);
      setNotice(`Licence ${moduleKey.toUpperCase()} mise à jour.`);
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleRechargeAI(creditsDelta: number) {
    const parsedDaily = rechargeDaily38Limit.trim()
      ? Number(rechargeDaily38Limit)
      : undefined;
    if (!selectedUserId || (!creditsDelta && parsedDaily === undefined)) return;
    setBusy(true);
    setError(null);
    try {
      await api.adminRechargeAI(selectedUserId, {
        add_credits: creditsDelta,
        daily_38_limit:
          parsedDaily !== undefined && !Number.isNaN(parsedDaily)
            ? parsedDaily
            : undefined,
        notes: rechargeNotes.trim(),
      });
      setRechargeNotes("");
      setRechargeDaily38Limit("");
      setNotice(
        `Quota IA mis à jour (${creditsDelta > 0 ? `+${creditsDelta} crédits` : "plafond journalier IA"}).`
      );
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveStudentCover(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedUserId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const payloadCover = finalizeCoverMetadataPayload(editCover, editCoDirDraft);
      setEditCoDirDraft(emptySupervisor());
      setEditCover(payloadCover);
      await api.adminUpdateUserCover(selectedUserId, {
        cover: payloadCover,
        study_type: editStudyType.trim() || undefined,
        topic_locked: ovrLocked,
        reason: ovrReason.trim() || "Mise à jour de la page de garde par l'administrateur",
      });
      setNotice("Page de garde du mémoire enregistrée et verrouillée côté étudiant.");
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSavePlan(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = {
      name: planName.trim(),
      description: planDesc.trim(),
      price_fcfa: Number(planPrice) || 0,
      module1_days: Number(planM1) || 0,
      module2_days: Number(planM2) || 0,
      module3_days: Number(planM3) || 0,
      module4_days: Number(planM4) || 0,
      ai_quota_included: Number(planAi) || 0,
      is_active: planActive,
    };
    try {
      if (editingPlanId) {
        await api.adminUpdatePlan(editingPlanId, body);
        setNotice("Plan mis à jour.");
      } else {
        await api.adminCreatePlan(body);
        setNotice("Nouveau plan créé.");
      }
      resetPlanForm();
      await loadAllCore();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function startEditPlan(p: LicensePlanOut) {
    setEditingPlanId(p.id);
    setPlanName(p.name);
    setPlanDesc(p.description);
    setPlanPrice(String(p.price_fcfa));
    setPlanM1(String(p.module1_days));
    setPlanM2(String(p.module2_days));
    setPlanM3(String(p.module3_days));
    setPlanM4(String(p.module4_days));
    setPlanAi(String(p.ai_quota_included));
    setPlanActive(p.is_active);
  }

  function resetPlanForm() {
    setEditingPlanId(null);
    setPlanName("");
    setPlanDesc("");
    setPlanPrice("15000");
    setPlanM1("30");
    setPlanM2("30");
    setPlanM3("180");
    setPlanM4("30");
    setPlanAi("150");
    setPlanActive(true);
  }

  async function handleDeletePlan(planId: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.adminDeletePlan(planId);
      setNotice(
        res.status === "archived"
          ? "Plan archivé (déjà utilisé par des licences existantes)."
          : "Plan supprimé."
      );
      await loadAllCore();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const pendingSupportCount = supportRequests.filter((r) => !r.resolved).length;

  async function handleResolveSupportRequest(requestId: string) {
    setResolvingRequestId(requestId);
    setError(null);
    setNotice(null);
    try {
      const note = (resolveNotes[requestId] || "").trim();
      await api.adminResolveUpdateRequest(
        requestId,
        note || "Ajustement appliqué sur votre document."
      );
      setNotice("Demande d'assistance marquée comme résolue et étudiant notifié.");
      setResolveNotes((prev) => {
        const next = { ...prev };
        delete next[requestId];
        return next;
      });
      const fresh = await api.adminListUpdateRequests(100);
      setSupportRequests(fresh);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setResolvingRequestId(null);
    }
  }

  return (
    <div className="space-y-5">
      {/* Admin Header Strip */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200/90 bg-white px-5 py-4 shadow-sm">
        <div className="flex items-center gap-3">
          <img
            src="/logo-mark.png"
            alt="Smart Research"
            className="h-10 w-10 rounded-xl border border-slate-200 bg-white object-contain p-1 shadow-xs"
          />
          <div>
            <h2 className="text-base font-bold text-slate-900">
              Smart Research — Portail Administrateur &amp; Commercialisation
            </h2>
            <p className="text-xs text-slate-500">
              Gestion des étudiants, licences par module, quotas IA et statistiques d'utilisation
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={refreshAll}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
            Actualiser
          </button>
          <button
            type="button"
            onClick={onExitAdmin}
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-slate-800"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Retour au Studio
          </button>
        </div>
      </div>

      {/* Sub-navigation tabs */}
      <div className="flex flex-nowrap gap-2 overflow-x-auto no-scrollbar pb-1">
        {(
          [
            { id: "dashboard", label: "Tableau de bord KPI", icon: Activity, badge: 0 },
            { id: "users", label: "Utilisateurs & Licences", icon: Users, badge: 0 },
            {
              id: "assistance",
              label: "Demandes d'assistance",
              icon: LifeBuoy,
              badge: pendingSupportCount,
            },
            { id: "plans", label: "Plans & Tarifs", icon: CreditCard, badge: 0 },
            { id: "api_keys", label: `Clés API Gemini (${apiKeys.length})`, icon: KeyRound, badge: 0 },
            { id: "pedagogy", label: "Notes de cours", icon: BookOpen, badge: 0 },
            { id: "stats", label: "Statistiques Étudiants & Journaux", icon: BarChart3, badge: 0 },
          ] as const
        ).map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`inline-flex min-h-[40px] shrink-0 items-center gap-2 rounded-xl px-4 py-2 text-xs font-semibold transition ${
                active
                  ? "bg-gradient-to-r from-[#004A8F] to-[#0284C7] text-white shadow-sm"
                  : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {t.label}
              {t.badge > 0 && (
                <span
                  className={`ml-0.5 inline-flex h-5 min-w-[20px] items-center justify-center rounded-full px-1.5 text-[10px] font-bold ${
                    active
                      ? "bg-amber-300 text-slate-900"
                      : "bg-amber-500 text-white"
                  }`}
                >
                  {t.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {pendingSupportCount > 0 && tab !== "assistance" && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-300 bg-amber-50/90 px-4 py-2.5 text-xs text-amber-950">
          <div className="flex items-center gap-2">
            <LifeBuoy className="h-4 w-4 shrink-0 text-amber-700" />
            <span>
              <strong>
                {pendingSupportCount} demande{pendingSupportCount > 1 ? "s" : ""} d&apos;assistance
              </strong>{" "}
              en attente de traitement.
            </span>
          </div>
          <button
            type="button"
            onClick={() => {
              setSupportFilter("pending");
              setTab("assistance");
            }}
            className="rounded-lg bg-amber-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-amber-700"
          >
            Voir les demandes
          </button>
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="flex items-center justify-between rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs text-emerald-800">
          <span className="inline-flex items-center gap-1.5 font-medium">
            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            {notice}
          </span>
          <button
            type="button"
            onClick={() => setNotice(null)}
            className="text-[11px] font-semibold underline"
          >
            Fermer
          </button>
        </div>
      )}

      {revealedCreds && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-xs text-indigo-950">
          <div>
            <div className="font-bold">{revealedCreds.label}</div>
            <div className="mt-0.5 font-mono">
              Identifiant : <strong>{revealedCreds.email}</strong> · Mot de passe temporaire :{" "}
              <strong className="rounded bg-white px-1.5 py-0.5 text-[#6C2826]">
                {revealedCreds.password}
              </strong>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => copyCredentials(revealedCreds.email, revealedCreds.password)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 font-semibold text-white hover:bg-indigo-700"
            >
              <ClipboardCopy className="h-3.5 w-3.5" />
              Copier identifiants
            </button>
            <button
              type="button"
              onClick={() => setRevealedCreds(null)}
              className="rounded-lg border border-indigo-200 bg-white px-2.5 py-1.5 font-semibold text-indigo-800"
            >
              OK
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-12 text-center text-sm text-slate-500">
          Chargement des données administrateur...
        </div>
      ) : tab === "dashboard" && dashboard ? (
        <div className="space-y-5">
          {/* 6 KPI Cards */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <div className="text-xs font-semibold text-slate-500">Utilisateurs actifs</div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-2xl font-bold text-emerald-700">
                  {dashboard.active_students}
                </span>
                <span className="text-xs text-slate-500">
                  / {dashboard.total_students} étudiants · {dashboard.total_admins ?? 0} admin(s)
                </span>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <div className="text-xs font-semibold text-slate-500">Expire sous 7 jours</div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-2xl font-bold text-amber-600">
                  {dashboard.expiring_soon_students}
                </span>
                <span className="text-xs text-slate-500">à relancer</span>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <div className="text-xs font-semibold text-slate-500">Expirés / Suspendus</div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-2xl font-bold text-rose-700">
                  {dashboard.expired_students}
                </span>
                <span className="text-xs text-slate-500">
                  expirés · {dashboard.disabled_students} suspendus
                </span>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <div className="text-xs font-semibold text-slate-500">Chiffre d'affaires licences</div>
              <div className="mt-2 text-2xl font-bold text-[#6C2826]">
                {dashboard.total_revenue_fcfa.toLocaleString("fr-FR")} FCFA
              </div>
              <div className="mt-1 text-xs text-slate-500">
                Ce mois : {dashboard.month_revenue_fcfa.toLocaleString("fr-FR")} FCFA
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <div className="text-xs font-semibold text-slate-500">Protocoles &amp; Collecte</div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-2xl font-bold text-slate-900">
                  {dashboard.total_projects}
                </span>
                <span className="text-xs text-slate-500">
                  protocoles · {dashboard.total_patient_records} fiches patients
                </span>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <div className="text-xs font-semibold text-slate-500">Consommation IA &amp; Exports</div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-2xl font-bold text-indigo-700">
                  {dashboard.total_ai_credits_used}
                </span>
                <span className="text-xs text-slate-500">
                  crédits IA ({dashboard.month_ai_credits_used} ce mois) · {dashboard.total_exports} exports
                </span>
              </div>
            </div>
          </div>

          {/* Alerts + Recent Grants */}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900">
                <AlertTriangle className="h-4 w-4 text-amber-500" />
                Licences expirant prochainement (≤ 7 jours)
              </h3>
              {dashboard.expiring_soon_list.length === 0 ? (
                <p className="mt-4 text-xs text-slate-500">
                  Aucune licence étudiant n'expire dans les 7 prochains jours.
                </p>
              ) : (
                <div className="mt-3 divide-y divide-slate-100">
                  {dashboard.expiring_soon_list.map((s) => (
                    <div
                      key={s.id}
                      className="flex items-center justify-between gap-2 py-2.5 text-xs"
                    >
                      <div className="min-w-0">
                        <div className="font-semibold text-slate-900 truncate">{s.full_name}</div>
                        <div className="text-slate-500 truncate">{s.email}</div>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedUserId(s.id);
                          setTab("users");
                        }}
                        className="shrink-0 rounded-lg border border-amber-300 bg-amber-50 px-2.5 py-1 font-semibold text-amber-900 hover:bg-amber-100"
                      >
                        Renouveler
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900">
                <CreditCard className="h-4 w-4 text-[#6C2826]" />
                Dernières attributions de licences
              </h3>
              {dashboard.recent_grants.length === 0 ? (
                <p className="mt-4 text-xs text-slate-500">
                  Aucune attribution enregistrée pour le moment.
                </p>
              ) : (
                <div className="mt-3 divide-y divide-slate-100">
                  {dashboard.recent_grants.map((g) => (
                    <div key={g.id} className="flex items-center justify-between gap-3 py-2.5 text-xs">
                      <div className="min-w-0">
                        <div className="font-semibold text-slate-900 truncate">
                          {g.user_name || g.user_email} · {g.plan_name}
                        </div>
                        <div className="text-slate-500">
                          {fmtDateTime(g.granted_at)}
                          {g.payment_reference ? ` · Réf: ${g.payment_reference}` : ""}
                        </div>
                      </div>
                      <span className="shrink-0 rounded-full bg-emerald-50 px-2.5 py-0.5 font-bold text-emerald-800">
                        {g.amount_paid_fcfa.toLocaleString("fr-FR")} FCFA
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : tab === "users" ? (
        <div className="space-y-4">
          {/* Toolbar & Filters */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
            <div className="flex w-full flex-wrap items-center gap-2.5 sm:w-auto">
              <div className="relative w-full sm:w-60">
                <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                <input
                  type="text"
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                  placeholder="Nom, email, matricule ou titre..."
                  className="min-h-[38px] w-full rounded-lg border border-slate-300 py-1.5 pl-8 pr-3 text-xs focus:border-[#6C2826] focus:outline-none"
                />
              </div>
              <select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700"
              >
                <option value="">Tous rôles</option>
                <option value="student">Étudiants</option>
                <option value="admin">Administrateurs</option>
              </select>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700"
              >
                <option value="">Tous statuts</option>
                <option value="active">Actif</option>
                <option value="expiring_soon">Expire ≤ 7 j</option>
                <option value="expired">Expiré</option>
                <option value="none">Aucune licence</option>
                <option value="disabled">Suspendu</option>
              </select>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  if (showCreateUser && newRole === "student") {
                    setShowCreateUser(false);
                  } else {
                    setNewRole("student");
                    setShowCreateUser(true);
                  }
                }}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[#004A8F] px-3.5 py-2 text-xs font-semibold text-white hover:bg-[#003b73]"
              >
                <UserPlus className="h-3.5 w-3.5" />
                {showCreateUser && newRole === "student"
                  ? "Fermer le formulaire"
                  : "Créer un étudiant"}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (showCreateUser && newRole === "admin") {
                    setShowCreateUser(false);
                  } else {
                    setNewRole("admin");
                    setShowCreateUser(true);
                  }
                }}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-700 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-800"
              >
                <ShieldCheck className="h-3.5 w-3.5" />
                {showCreateUser && newRole === "admin"
                  ? "Fermer le formulaire"
                  : "Créer un administrateur"}
              </button>
            </div>
          </div>

          {/* Create User Form */}
          {showCreateUser && (
            <form
              onSubmit={handleCreateUser}
              className="space-y-5 rounded-2xl border border-indigo-200 bg-white p-5 shadow-sm"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-bold text-slate-900">
                  Nouveau compte utilisateur (création manuelle administrateur)
                </h3>
                <span className="text-[11px] text-slate-500">
                  Mot de passe temporaire requis à la 1ʳᵉ connexion
                </span>
              </div>

              {/* 2-Card Visual Role Selector */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => setNewRole("student")}
                  className={`flex items-start gap-3 rounded-xl border p-3.5 text-left transition ${
                    newRole === "student"
                      ? "border-[#004A8F] bg-sky-50/70 ring-1 ring-[#004A8F]"
                      : "border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  <GraduationCap
                    className={`mt-0.5 h-5 w-5 shrink-0 ${
                      newRole === "student" ? "text-[#004A8F]" : "text-slate-400"
                    }`}
                  />
                  <div>
                    <div className="text-xs font-bold text-slate-900">
                      Utilisateur simple (Étudiant)
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-600">
                      1 protocole unique · Page de garde définie ici (verrouillée côté étudiant) · Licences modulaires.
                    </div>
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => setNewRole("admin")}
                  className={`flex items-start gap-3 rounded-xl border p-3.5 text-left transition ${
                    newRole === "admin"
                      ? "border-indigo-600 bg-indigo-50/70 ring-1 ring-indigo-600"
                      : "border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  <ShieldCheck
                    className={`mt-0.5 h-5 w-5 shrink-0 ${
                      newRole === "admin" ? "text-indigo-700" : "text-slate-400"
                    }`}
                  />
                  <div>
                    <div className="text-xs font-bold text-slate-900">
                      Administrateur (Accès complet)
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-600">
                      Toutes les permissions : Portail Admin, création de comptes, gestion des pages de garde et accès illimité.
                    </div>
                  </div>
                </button>
              </div>

              {/* Account Credentials & General Fields */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <div>
                  <label className="block text-[11px] font-semibold text-slate-600">
                    Identifiant (Nom d'utilisateur ou e-mail) *
                  </label>
                  <input
                    type="text"
                    required
                    value={newEmail}
                    onChange={(e) => {
                      const val = e.target.value;
                      setNewCover((prev) => ({
                        ...prev,
                        student_email:
                          !prev.student_email || prev.student_email === newEmail
                            ? val.includes("@")
                              ? val
                              : ""
                            : prev.student_email,
                      }));
                      setNewEmail(val);
                    }}
                    placeholder={
                      newRole === "admin"
                        ? "admin.fmsb ou admin@smartresearch.cm"
                        : "jean.kamga ou etudiant@fmsb.cm"
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-slate-600">
                    Nom complet *
                  </label>
                  <input
                    type="text"
                    required
                    value={newFullName}
                    onChange={(e) => {
                      const val = e.target.value;
                      setNewCover((prev) => ({
                        ...prev,
                        author_name:
                          !prev.author_name || prev.author_name === newFullName
                            ? val
                            : prev.author_name,
                      }));
                      setNewFullName(val);
                    }}
                    placeholder="Ex. Kouayi Ngnamfa Duchelle"
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                  />
                </div>
                {newRole === "student" && (
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-600">
                      Matricule
                    </label>
                    <input
                      type="text"
                      value={newMatricule}
                      onChange={(e) => {
                        const val = e.target.value;
                        setNewCover((prev) => ({
                          ...prev,
                          matricule:
                            !prev.matricule || prev.matricule === newMatricule
                              ? val
                              : prev.matricule,
                        }));
                        setNewMatricule(val);
                      }}
                      placeholder="20M1493"
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                    />
                  </div>
                )}
                <div>
                  <label className="block text-[11px] font-semibold text-slate-600">
                    Téléphone
                  </label>
                  <input
                    type="text"
                    value={newPhone}
                    onChange={(e) => {
                      const val = e.target.value;
                      setNewCover((prev) => ({
                        ...prev,
                        student_phone:
                          !prev.student_phone || prev.student_phone === newPhone
                            ? val
                            : prev.student_phone,
                      }));
                      setNewPhone(val);
                    }}
                    placeholder="691 38 46 52"
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                  />
                </div>

                <div className={newRole === "admin" ? "lg:col-span-1" : "sm:col-span-2"}>
                  <label className="block text-[11px] font-semibold text-slate-600">
                    Mot de passe temporaire (laisser vide = auto)
                  </label>
                  <input
                    type="text"
                    minLength={8}
                    value={newTempPassInput}
                    onChange={(e) => setNewTempPassInput(e.target.value)}
                    placeholder="Ex. FMSB-2026-PASS"
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-semibold text-slate-600">
                    Clé API Gemini assignée
                  </label>
                  <select
                    value={newUserApiKeyId}
                    onChange={(e) => setNewUserApiKeyId(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                  >
                    {apiKeys.length === 0 ? (
                      <option value="default">Clé système par défaut (GEMINI_API_KEY)</option>
                    ) : (
                      apiKeys
                        .filter((k) => k.is_default || k.is_active)
                        .map((k) => (
                          <option key={k.id} value={k.id}>
                            {k.name} {k.owner_label ? `(${k.owner_label})` : ""} · {k.masked_key}
                          </option>
                        ))
                    )}
                  </select>
                </div>

                <div className="sm:col-span-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                  <div className="mb-2 flex items-center gap-1.5 text-[11px] font-bold text-slate-800">
                    <Cpu className="h-3.5 w-3.5 text-indigo-600" />
                    Modèles Gemini & ordre de priorité (Primaire → Secondaire → Backups)
                  </div>
                  <ModelRoutingEditor
                    catalog={modelCatalog}
                    value={newUserModelRouting}
                    onChange={setNewUserModelRouting}
                    compact
                  />
                </div>

                {newRole === "student" && (
                  <>
                    <div>
                      <label className="block text-[11px] font-semibold text-slate-600">
                        Plan de licence initial
                      </label>
                      <select
                        value={newPlanId}
                        onChange={(e) => {
                          const pid = e.target.value;
                          setNewPlanId(pid);
                          const found = plans.find((p) => p.id === pid);
                          if (found) setNewAmountFcfa(String(found.price_fcfa));
                        }}
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                      >
                        <option value="">-- Sans plan initial --</option>
                        {plans
                          .filter((p) => p.is_active)
                          .map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.name} ({p.price_fcfa.toLocaleString("fr-FR")} F)
                            </option>
                          ))}
                      </select>
                    </div>

                    <div>
                      <label className="block text-[11px] font-semibold text-slate-600">
                        Montant payé (FCFA)
                      </label>
                      <input
                        type="number"
                        min={0}
                        value={newAmountFcfa}
                        onChange={(e) => setNewAmountFcfa(e.target.value)}
                        className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                      />
                    </div>

                    <div>
                      <label className="block text-[11px] font-semibold text-slate-600">
                        Référence paiement
                      </label>
                      <input
                        type="text"
                        value={newPaymentRef}
                        onChange={(e) => setNewPaymentRef(e.target.value)}
                        placeholder="Ex. OM-2026-041"
                        className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                      />
                    </div>

                    <div>
                      <label className="block text-[11px] font-semibold text-slate-600">
                        Type d&apos;étude pressenti
                      </label>
                      <input
                        type="text"
                        value={newStudyType}
                        onChange={(e) => setNewStudyType(e.target.value)}
                        className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                      />
                    </div>
                  </>
                )}
              </div>

              {newRole === "admin" ? (
                <div className="rounded-xl border border-indigo-200 bg-indigo-50/70 p-4 text-xs text-indigo-950">
                  <div className="flex items-center gap-2 font-bold">
                    <ShieldCheck className="h-4 w-4 text-indigo-700" />
                    Permissions complètes Administrateur attribuées automatiquement
                  </div>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-[11px] text-indigo-900">
                    <li>Accès complet au Portail Administrateur (KPIs, comptes étudiants &amp; admins, plans, journaux).</li>
                    <li>Création et modification de toutes les pages de garde officielles des mémoires/thèses.</li>
                    <li>Accès illimité aux Modules 1 à 4 et crédits IA illimités, sans restriction de protocole.</li>
                  </ul>
                </div>
              ) : (
                <div className="space-y-3 rounded-xl border border-amber-200 bg-amber-50/30 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-200/70 pb-2.5">
                    <div className="flex items-center gap-2 text-xs font-bold text-slate-900">
                      <FileText className="h-4 w-4 text-[#004A8F]" />
                      Page de garde du mémoire (définie à la création — non modifiable par l&apos;étudiant)
                    </div>
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-[10px] font-bold text-amber-900">
                      <Lock className="h-3 w-3" /> Verrouillée côté étudiant dès la création
                    </span>
                  </div>
                  <CoverPageForm
                    value={newCover}
                    onChange={setNewCover}
                    titleRequired={true}
                    stagedLogos={{
                      university: stagedUniLogo,
                      faculty: stagedFacLogo,
                    }}
                    onStageLogo={(slot, file) => {
                      if (slot === "university") setStagedUniLogo(file);
                      else setStagedFacLogo(file);
                    }}
                    onError={(msg) => setError(msg || null)}
                    coDirDraft={newCoDirDraft}
                    onCoDirDraftChange={setNewCoDirDraft}
                  />
                </div>
              )}

              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setShowCreateUser(false)}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700"
                >
                  Annuler
                </button>
                <button
                  type="submit"
                  disabled={busy}
                  className={`rounded-lg px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-60 ${
                    newRole === "admin"
                      ? "bg-indigo-700 hover:bg-indigo-800"
                      : "bg-[#004A8F] hover:bg-[#003b73]"
                  }`}
                >
                  {newRole === "admin"
                    ? "Créer le compte Administrateur"
                    : "Créer l'étudiant & verrouiller la page de garde"}
                </button>
              </div>
            </form>
          )}

          {/* Split Users List + Detail Inspector */}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
            {/* Users List */}
            <div className="lg:col-span-5 rounded-2xl border border-slate-200 bg-white p-3 shadow-xs">
              <div className="mb-2 px-2 text-xs font-bold uppercase tracking-wide text-slate-500">
                {users.length} compte(s) affiché(s)
              </div>
              <div className="max-h-[680px] space-y-2 overflow-y-auto pr-1">
                {users.map((u) => {
                  const st = statusBadge(u.status_summary);
                  const isSel = selectedUserId === u.id;
                  return (
                    <button
                      key={u.id}
                      type="button"
                      onClick={() => setSelectedUserId(u.id)}
                      className={`w-full rounded-xl border p-3 text-left transition ${
                        isSel
                          ? "border-[#6C2826] bg-rose-50/40 ring-1 ring-[#6C2826]"
                          : "border-slate-200 hover:bg-slate-50"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-bold text-slate-900">
                            {u.full_name}
                          </div>
                          <div className="truncate text-xs text-slate-500">
                            {u.email}
                            {u.matricule ? ` · ${u.matricule}` : ""}
                          </div>
                        </div>
                        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${st.cls}`}>
                          {st.label}
                        </span>
                      </div>

                      {u.role === "student" && (
                        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]">
                          {MODULE_META.map((m) => {
                            const ent = u.entitlements[m.key];
                            const active = Boolean(ent?.is_active);
                            return (
                              <span
                                key={m.key}
                                className={`rounded px-1.5 py-0.5 font-semibold ${
                                  active
                                    ? "bg-emerald-50 text-emerald-800"
                                    : "bg-slate-100 text-slate-500"
                                }`}
                              >
                                {m.short}: {active ? `${ent.days_remaining}j` : "Off"}
                              </span>
                            );
                          })}
                          <span className="ml-auto inline-flex items-center gap-1.5 font-semibold text-indigo-700">
                            <span>IA: {u.ai_quota.credits_remaining} cr</span>
                            <span
                              className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                u.ai_quota.using_35_fallback
                                  ? "bg-amber-100 text-amber-800"
                                  : "bg-emerald-100 text-emerald-700"
                              }`}
                            >
                              {u.ai_quota.using_35_fallback
                                ? "Relais"
                                : `Principal (${u.ai_quota.daily_38_used ?? 0}/${u.ai_quota.daily_38_limit ?? 15})`}
                            </span>
                          </span>
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Detail Inspector */}
            <div className="lg:col-span-7 space-y-4">
              {!userDetail ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-12 text-center text-xs text-slate-500">
                  Sélectionnez un utilisateur dans la liste pour gérer ses licences par module, son quota IA et son protocole verrouillé.
                </div>
              ) : (
                <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
                  {/* Identity & Account Controls */}
                  <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 pb-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-base font-bold text-slate-900">
                          {userDetail.user.full_name}
                        </h3>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                            statusBadge(userDetail.user.status_summary).cls
                          }`}
                        >
                          {statusBadge(userDetail.user.status_summary).label}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-slate-600">
                        {userDetail.user.email}
                        {userDetail.user.matricule ? ` · Matricule : ${userDetail.user.matricule}` : ""}
                        {userDetail.user.phone ? ` · Tél : ${userDetail.user.phone}` : ""}
                      </div>
                      <div className="mt-1 text-[11px] text-slate-500">
                        Dernière connexion : {fmtDateTime(userDetail.user.last_login_at)}
                        {userDetail.user.last_login_ip ? ` (${userDetail.user.last_login_ip})` : ""} ·
                        Changements d'appareil : <strong>{userDetail.user.session_switch_count}</strong>
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => handleResetPassword(userDetail.user)}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                      >
                        <KeyRound className="h-3.5 w-3.5" />
                        Réinit. MDP
                      </button>
                      {userDetail.user.id !== currentAdminId && (
                        <>
                          {confirmRoleChangeId === userDetail.user.id ? (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                handleChangeUserRole(
                                  userDetail.user,
                                  userDetail.user.role === "admin"
                                    ? "student"
                                    : "admin"
                                )
                              }
                              className="inline-flex items-center gap-1 rounded-lg bg-indigo-700 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-indigo-800"
                            >
                              <ShieldCheck className="h-3.5 w-3.5" />
                              {userDetail.user.role === "admin"
                                ? "Confirmer passage Étudiant"
                                : "Confirmer promotion Admin"}
                            </button>
                          ) : (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                setConfirmRoleChangeId(userDetail.user.id)
                              }
                              className="inline-flex items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1.5 text-xs font-semibold text-indigo-800 hover:bg-indigo-100"
                            >
                              <ShieldCheck className="h-3.5 w-3.5" />
                              {userDetail.user.role === "admin"
                                ? "Passer Étudiant"
                                : "Promouvoir Admin"}
                            </button>
                          )}

                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleToggleActive(userDetail.user)}
                            className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-semibold ${
                              userDetail.user.is_active
                                ? "border border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100"
                                : "border border-emerald-300 bg-emerald-50 text-emerald-900 hover:bg-emerald-100"
                            }`}
                          >
                            {userDetail.user.is_active ? (
                              <>
                                <UserX className="h-3.5 w-3.5" /> Suspendre
                              </>
                            ) : (
                              <>
                                <UserCheck className="h-3.5 w-3.5" /> Réactiver
                              </>
                            )}
                          </button>

                          {confirmDeleteUserId === userDetail.user.id ? (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => handleDeleteUser(userDetail.user.id)}
                              className="inline-flex items-center gap-1 rounded-lg bg-rose-700 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-rose-800"
                            >
                              <Trash2 className="h-3.5 w-3.5" /> Confirmer suppression
                            </button>
                          ) : (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => setConfirmDeleteUserId(userDetail.user.id)}
                              className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-100"
                            >
                              <Trash2 className="h-3.5 w-3.5" /> Supprimer
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </div>

                  {/* Per-module Licenses */}
                  <div>
                    <h4 className="text-xs font-bold uppercase tracking-wide text-slate-600">
                      Licences par Module
                    </h4>
                    <div className="mt-2 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                      {MODULE_META.map((m) => {
                        const ent = userDetail.user.entitlements[m.key];
                        const active = Boolean(ent?.is_active);
                        return (
                          <div
                            key={m.key}
                            className={`rounded-xl border p-3 ${
                              active ? "border-emerald-200 bg-emerald-50/30" : "border-slate-200 bg-slate-50/70"
                            }`}
                          >
                            <div className="flex items-center justify-between text-xs">
                              <span className="font-bold text-slate-900">
                                {m.short} — {m.label}
                              </span>
                              <span
                                className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                                  active ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"
                                }`}
                              >
                                {active ? `${ent.days_remaining} j` : "Expiré"}
                              </span>
                            </div>
                            <div className="mt-1 text-[11px] text-slate-500">
                              Expiration : {fmtDate(ent?.expires_at)}
                            </div>
                            {userDetail.user.role === "student" && (
                              <div className="mt-2 flex flex-wrap gap-1.5">
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => handleAdjustModule(m.key, { add_days: 30 })}
                                  className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
                                >
                                  +30 j
                                </button>
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => handleAdjustModule(m.key, { add_days: 90 })}
                                  className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
                                >
                                  +90 j
                                </button>
                                {active && (
                                  <button
                                    type="button"
                                    disabled={busy}
                                    onClick={() => handleAdjustModule(m.key, { revoke: true })}
                                    className="ml-auto rounded border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] font-semibold text-rose-700 hover:bg-rose-100"
                                  >
                                    Expirer
                                  </button>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Per-User Gemini API Key Switcher */}
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-200 bg-indigo-50/50 px-3.5 py-2.5 text-xs">
                    <div className="flex items-center gap-2">
                      <KeyRound className="h-4 w-4 text-indigo-700" />
                      <div>
                        <span className="font-bold text-indigo-950">Clé API Gemini routée :</span>{" "}
                        <span className="text-indigo-800">
                          {userDetail.user.gemini_api_key_name || "Clé système par défaut"}
                        </span>
                      </div>
                    </div>
                    <select
                      aria-label="Clé API Gemini assignée à cet utilisateur"
                      disabled={busy}
                      value={userDetail.user.gemini_api_key_id || "default"}
                      onChange={(e) => handleAssignUserApiKey(userDetail.user.id, e.target.value)}
                      className="rounded-lg border border-indigo-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-800 shadow-2xs"
                    >
                      {apiKeys.length === 0 ? (
                        <option value="default">Clé système par défaut (GEMINI_API_KEY)</option>
                      ) : (
                        apiKeys.map((k) => (
                          <option key={k.id} value={k.id} disabled={!k.is_default && !k.is_active}>
                            {k.name} {k.owner_label ? `— ${k.owner_label}` : ""} ({k.masked_key})
                            {!k.is_active && !k.is_default ? " [Inactive]" : ""}
                          </option>
                        ))
                      )}
                    </select>
                  </div>

                  {/* Per-User Ordered Gemini Model Chain & RPD/TPM Limits */}
                  <div className="rounded-xl border border-slate-200 bg-white p-3.5 space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Cpu className="h-4 w-4 text-indigo-700" />
                        <div>
                          <div className="text-xs font-bold text-slate-900">
                            Chaîne de modèles Gemini & plafonds RPD / TPM
                          </div>
                          <div className="text-[11px] text-slate-500">
                            Ordre Primaire → Secondaire → Backups avec bascule automatique sur dépassement RPD/TPM ou erreur.
                          </div>
                        </div>
                      </div>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => handleSaveUserModelRouting(userDetail.user.id)}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                      >
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        Enregistrer la chaîne
                      </button>
                    </div>
                    <ModelRoutingEditor
                      catalog={modelCatalog}
                      value={drawerModelRouting}
                      onChange={setDrawerModelRouting}
                      dailyUsage={userDetail.model_daily_usage}
                    />
                  </div>

                  {/* Grant Plan + AI Quota Recharge */}
                  {userDetail.user.role === "student" && (
                    <div className="grid grid-cols-1 gap-4 border-t border-slate-100 pt-4 sm:grid-cols-2">
                      <form onSubmit={handleGrantPlan} className="space-y-2 rounded-xl border border-slate-200 p-3.5">
                        <div className="flex items-center gap-1.5 text-xs font-bold text-slate-800">
                          <Plus className="h-3.5 w-3.5 text-[#6C2826]" />
                          Attribuer / Prolonger un plan
                        </div>
                        <select
                          value={grantPlanId}
                          onChange={(e) => {
                            const pid = e.target.value;
                            setGrantPlanId(pid);
                            const found = plans.find((p) => p.id === pid);
                            if (found) setGrantAmount(String(found.price_fcfa));
                          }}
                          className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                        >
                          {plans
                            .filter((p) => p.is_active)
                            .map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name} ({p.price_fcfa.toLocaleString("fr-FR")} F)
                              </option>
                            ))}
                        </select>
                        <div className="grid grid-cols-2 gap-2">
                          <input
                            type="number"
                            min={0}
                            value={grantAmount}
                            onChange={(e) => setGrantAmount(e.target.value)}
                            placeholder="Montant FCFA"
                            className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                          />
                          <input
                            type="text"
                            value={grantRef}
                            onChange={(e) => setGrantRef(e.target.value)}
                            placeholder="Réf. paiement"
                            className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                          />
                        </div>
                        <input
                          type="text"
                          value={grantNotes}
                          onChange={(e) => setGrantNotes(e.target.value)}
                          placeholder="Notes / motif (optionnel)"
                          className="w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                        />
                        <button
                          type="submit"
                          disabled={busy || !grantPlanId}
                          className="w-full rounded-lg bg-[#6C2826] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#571f1d]"
                        >
                          Appliquer le plan (cumulatif)
                        </button>
                      </form>

                      <div className="space-y-2 rounded-xl border border-slate-200 p-3.5">
                        <div className="flex items-center justify-between text-xs font-bold text-slate-800">
                          <span className="inline-flex items-center gap-1.5">
                            <Sparkles className="h-3.5 w-3.5 text-indigo-600" />
                            Quota Crédits IA
                          </span>
                          <span className="text-indigo-700">
                            {userDetail.user.ai_quota.credits_remaining} /{" "}
                            {userDetail.user.ai_quota.credits_total} restants
                          </span>
                        </div>
                        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
                          <span>
                            Utilisés : {userDetail.user.ai_quota.credits_used} · Appels IA réussis :{" "}
                            {userDetail.user.ai_calls_count}
                          </span>
                          <span
                            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold ${
                              userDetail.user.ai_quota.using_35_fallback
                                ? "bg-amber-100 text-amber-800"
                                : "bg-emerald-100 text-emerald-800"
                            }`}
                          >
                            {userDetail.user.ai_quota.using_35_fallback
                              ? `Mode Relais (${userDetail.user.ai_quota.daily_38_used ?? 0}/${userDetail.user.ai_quota.daily_38_limit ?? 15})`
                              : `Moteur Principal (${userDetail.user.ai_quota.daily_38_used ?? 0}/${userDetail.user.ai_quota.daily_38_limit ?? 15}/j)`}
                          </span>
                        </div>
                        <div className="flex gap-2">
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleRechargeAI(50)}
                            className="flex-1 rounded-lg border border-indigo-200 bg-indigo-50 py-1.5 text-xs font-semibold text-indigo-800 hover:bg-indigo-100"
                          >
                            +50 crédits
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleRechargeAI(100)}
                            className="flex-1 rounded-lg border border-indigo-200 bg-indigo-50 py-1.5 text-xs font-semibold text-indigo-800 hover:bg-indigo-100"
                          >
                            +100 crédits
                          </button>
                        </div>
                        <div className="flex gap-2">
                          <input
                            type="number"
                            value={rechargeCredits}
                            onChange={(e) => setRechargeCredits(e.target.value)}
                            placeholder="Crédits +"
                            className="w-20 rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
                          />
                          <input
                            type="number"
                            min={1}
                            max={500}
                            value={rechargeDaily38Limit}
                            onChange={(e) => setRechargeDaily38Limit(e.target.value)}
                            placeholder={`Plafond/j (${userDetail.user.ai_quota.daily_38_limit ?? 15})`}
                            title="Nouveau plafond journalier du moteur principal avant bascule en mode relais"
                            className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
                          />
                          <input
                            type="text"
                            value={rechargeNotes}
                            onChange={(e) => setRechargeNotes(e.target.value)}
                            placeholder="Note recharge"
                            className="min-w-0 flex-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                          />
                        </div>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => handleRechargeAI(Number(rechargeCredits) || 0)}
                          className="inline-flex w-full items-center justify-center gap-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
                        >
                          <Zap className="h-3.5 w-3.5" />
                          Appliquer crédits / plafond journalier
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Admin Privileges Card OR Student Full Cover Editor */}
                  {userDetail.user.role === "admin" ? (
                    <div className="rounded-xl border border-indigo-200 bg-indigo-50/70 p-4 text-xs text-indigo-950">
                      <div className="flex items-center gap-2 font-bold">
                        <ShieldCheck className="h-4 w-4 text-indigo-700" />
                        Compte Administrateur — Toutes permissions actives
                      </div>
                      <p className="mt-1.5 text-[11px] text-indigo-900">
                        Cet utilisateur dispose d&apos;un accès complet au Portail Administrateur, à la gestion des utilisateurs et des pages de garde, ainsi que d&apos;un accès illimité à tous les protocoles, modules et crédits IA.
                      </p>
                    </div>
                  ) : (
                    <form
                      onSubmit={handleSaveStudentCover}
                      className="space-y-4 rounded-xl border border-amber-200 bg-amber-50/30 p-4"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-200/70 pb-2.5">
                        <div className="flex items-center gap-1.5 text-xs font-bold text-slate-900">
                          <Lock className="h-3.5 w-3.5 text-amber-700" />
                          Page de garde du mémoire (modifiable uniquement par l&apos;administrateur)
                          {userDetail.user.project_id
                            ? ` · ${userDetail.user.project_id}`
                            : " · Protocole créé à l'enregistrement"}
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[11px] font-medium text-slate-600">
                            {userDetail.user.sections_validated_count}/24 sections validées ·{" "}
                            {userDetail.user.patients_count} fiches patients ·{" "}
                            {userDetail.user.exports_count} exports
                          </span>
                          {userDetail.user.project_id && onOpenUserWorkspace && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => {
                                setBusy(true);
                                Promise.resolve(
                                  onOpenUserWorkspace(
                                    userDetail.user.project_id!,
                                    userDetail.user.full_name
                                  )
                                ).finally(() => setBusy(false));
                              }}
                              className="inline-flex items-center gap-1.5 rounded-lg bg-[#004A8F] px-3 py-1.5 text-[11px] font-semibold text-white shadow-xs transition hover:bg-[#003a70] disabled:opacity-60"
                              title="Ouvrir le protocole de cet étudiant dans le Studio pour l'assister (l'importation de document reste bloquée par sécurité)"
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                              {busy ? "Ouverture..." : "Ouvrir l'espace de travail"}
                            </button>
                          )}
                        </div>
                      </div>

                      <CoverPageForm
                        value={editCover}
                        onChange={setEditCover}
                        titleRequired={true}
                        projectId={userDetail.user.project_id ?? null}
                        onProjectUpdated={() => {
                          if (selectedUserId) {
                            void loadUserDetail(selectedUserId);
                            void loadAllCore();
                          }
                        }}
                        onError={(msg) => setError(msg || null)}
                        coDirDraft={editCoDirDraft}
                        onCoDirDraftChange={setEditCoDirDraft}
                      />

                      <div className="grid grid-cols-1 gap-2.5 border-t border-amber-200/70 pt-3 sm:grid-cols-3">
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-700">
                            Type d&apos;étude
                          </label>
                          <input
                            type="text"
                            value={editStudyType}
                            onChange={(e) => setEditStudyType(e.target.value)}
                            className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs"
                          />
                        </div>
                        <div className="flex items-end pb-1.5">
                          <label className="flex items-center gap-2 text-xs font-medium text-slate-700">
                            <input
                              id="ovr-locked-check"
                              type="checkbox"
                              checked={ovrLocked}
                              onChange={(e) => setOvrLocked(e.target.checked)}
                            />
                            Verrou actif (1 utilisateur = 1 protocole)
                          </label>
                        </div>
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-700">
                            Motif (journal d&apos;audit)
                          </label>
                          <input
                            type="text"
                            value={ovrReason}
                            onChange={(e) => setOvrReason(e.target.value)}
                            placeholder="Motif de la modification"
                            className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs"
                          />
                        </div>
                      </div>

                      <div className="flex justify-end">
                        <button
                          type="submit"
                          disabled={busy}
                          className="rounded-lg bg-amber-700 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-800 disabled:opacity-60"
                        >
                          Enregistrer la page de garde
                        </button>
                      </div>
                    </form>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : tab === "plans" ? (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
          {/* Create / Edit Plan Form */}
          <form
            onSubmit={handleSavePlan}
            className="lg:col-span-5 space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
          >
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-slate-900">
                {editingPlanId ? "Modifier le plan de licence" : "Créer un nouveau plan"}
              </h3>
              {editingPlanId && (
                <button
                  type="button"
                  onClick={resetPlanForm}
                  className="text-xs font-semibold text-slate-500 underline"
                >
                  Nouveau
                </button>
              )}
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-slate-600">Nom du plan *</label>
              <input
                type="text"
                required
                value={planName}
                onChange={(e) => setPlanName(e.target.value)}
                placeholder="Ex. Pack Thèse 1M + Collecte 6M"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
              />
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-slate-600">Description</label>
              <textarea
                rows={2}
                value={planDesc}
                onChange={(e) => setPlanDesc(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-xs"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">Prix (FCFA)</label>
                <input
                  type="number"
                  min={0}
                  value={planPrice}
                  onChange={(e) => setPlanPrice(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                />
              </div>
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">Crédits IA inclus</label>
                <input
                  type="number"
                  min={0}
                  value={planAi}
                  onChange={(e) => setPlanAi(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                />
              </div>
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">Module 1 (jours)</label>
                <input
                  type="number"
                  min={0}
                  value={planM1}
                  onChange={(e) => setPlanM1(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                />
              </div>
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">Module 2 (jours)</label>
                <input
                  type="number"
                  min={0}
                  value={planM2}
                  onChange={(e) => setPlanM2(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                />
              </div>
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">Module 3 CRF (jours)</label>
                <input
                  type="number"
                  min={0}
                  value={planM3}
                  onChange={(e) => setPlanM3(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                />
              </div>
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">Module 4 PPTX (jours)</label>
                <input
                  type="number"
                  min={0}
                  value={planM4}
                  onChange={(e) => setPlanM4(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                />
              </div>
            </div>

            <label className="flex items-center gap-2 text-xs text-slate-700">
              <input
                type="checkbox"
                checked={planActive}
                onChange={(e) => setPlanActive(e.target.checked)}
              />
              Plan actif à la commercialisation
            </label>

            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-[#6C2826] px-4 py-2 text-xs font-semibold text-white hover:bg-[#571f1d]"
            >
              {editingPlanId ? "Mettre à jour le plan" : "Créer le plan"}
            </button>
          </form>

          {/* Plans Catalog List */}
          <div className="lg:col-span-7 space-y-3">
            {plans.map((p) => (
              <div
                key={p.id}
                className={`rounded-2xl border bg-white p-4 shadow-xs ${
                  p.is_active ? "border-slate-200" : "border-slate-200 opacity-65"
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2">
                      <h4 className="text-sm font-bold text-slate-900">{p.name}</h4>
                      {!p.is_active && (
                        <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold text-slate-700">
                          Archivé
                        </span>
                      )}
                    </div>
                    {p.description && <p className="mt-1 text-xs text-slate-500">{p.description}</p>}
                  </div>
                  <div className="text-right">
                    <div className="text-base font-bold text-[#6C2826]">
                      {p.price_fcfa.toLocaleString("fr-FR")} FCFA
                    </div>
                    <div className="text-[11px] font-semibold text-indigo-700">
                      {p.ai_quota_included} crédits IA
                    </div>
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-2.5 text-xs">
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded bg-slate-100 px-2 py-0.5 font-medium text-slate-700">
                      M1: {p.module1_days}j
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 font-medium text-slate-700">
                      M2: {p.module2_days}j
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 font-medium text-slate-700">
                      M3: {p.module3_days}j
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 font-medium text-slate-700">
                      M4: {p.module4_days}j
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => startEditPlan(p)}
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                    >
                      <Edit3 className="h-3 w-3" /> Modifier
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => handleDeletePlan(p.id)}
                      className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-700 hover:bg-rose-100"
                    >
                      <Trash2 className="h-3 w-3" /> Supprimer
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        /* Tab 4: Student Analytics & Audit Logs */
        <div className="space-y-5">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-sm font-bold text-slate-900">
                Statistiques d'utilisation par étudiant ({statsRows.length})
              </h3>
              <div className="flex flex-wrap gap-2">
                <input
                  type="text"
                  value={statsSearch}
                  onChange={(e) => setStatsSearch(e.target.value)}
                  placeholder="Filtrer étudiant..."
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                />
                <select
                  value={statsSortBy}
                  onChange={(e) => setStatsSortBy(e.target.value)}
                  className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700"
                >
                  <option value="last_login">Tri : Dernière connexion</option>
                  <option value="sections">Tri : Avancement rédaction</option>
                  <option value="patients">Tri : Fiches patients</option>
                  <option value="ai_used">Tri : Crédits IA consommés</option>
                  <option value="exports">Tri : Nombre d'exports</option>
                </select>
              </div>
            </div>

            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-xs">
                <thead className="bg-slate-50 text-left text-[11px] font-bold uppercase text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Étudiant</th>
                    <th className="px-3 py-2">Protocole</th>
                    <th className="px-3 py-2">Sections</th>
                    <th className="px-3 py-2">Patients</th>
                    <th className="px-3 py-2">Exports</th>
                    <th className="px-3 py-2">Crédits IA</th>
                    <th className="px-3 py-2">Appareils</th>
                    <th className="px-3 py-2">Dernière activité</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {statsRows.map((r) => (
                    <tr
                      key={r.id}
                      onClick={() => {
                        setSelectedUserId(r.id);
                        setTab("users");
                      }}
                      className="cursor-pointer hover:bg-slate-50"
                    >
                      <td className="px-3 py-2.5">
                        <div className="font-semibold text-slate-900">{r.full_name}</div>
                        <div className="text-[11px] text-slate-500">{r.email}</div>
                      </td>
                      <td className="max-w-[220px] truncate px-3 py-2.5 text-slate-700">
                        {r.project_title || "Aucun"}
                      </td>
                      <td className="px-3 py-2.5 font-semibold text-slate-800">
                        {r.sections_validated_count}/24
                      </td>
                      <td className="px-3 py-2.5 font-semibold text-[#6C2826]">
                        {r.patients_count}
                      </td>
                      <td className="px-3 py-2.5 font-semibold text-emerald-700">
                        {r.exports_count}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="font-semibold text-indigo-700">
                          {r.ai_quota.credits_used}
                        </span>{" "}
                        / {r.ai_quota.credits_total}
                      </td>
                      <td className="px-3 py-2.5 text-slate-600">{r.session_switch_count}</td>
                      <td className="px-3 py-2.5 text-[11px] text-slate-500">
                        {fmtDateTime(r.last_login_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <h4 className="text-xs font-bold uppercase tracking-wide text-slate-700">
                Derniers événements d'utilisation ({usageEvents.length})
              </h4>
              <div className="mt-3 max-h-72 divide-y divide-slate-100 overflow-y-auto text-xs">
                {usageEvents.map((ev) => (
                  <div key={ev.id} className="flex items-center justify-between gap-2 py-2">
                    <div className="min-w-0">
                      <div className="truncate font-semibold text-slate-800">
                        {ev.user_email} · {ev.action}
                      </div>
                      <div className="text-[11px] text-slate-500">
                        {ev.module_key} · {fmtDateTime(ev.created_at)}
                      </div>
                    </div>
                    <span className="shrink-0 rounded bg-slate-100 px-2 py-0.5 font-semibold text-slate-700">
                      -{ev.credits_cost} cr
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <h4 className="text-xs font-bold uppercase tracking-wide text-slate-700">
                Journal d'audit administrateur ({auditLogs.length})
              </h4>
              <div className="mt-3 max-h-72 divide-y divide-slate-100 overflow-y-auto text-xs">
                {auditLogs.map((al) => (
                  <div key={al.id} className="py-2">
                    <div className="font-semibold text-slate-800">
                      {al.action} · par {al.admin_email || "système"}
                      {al.target_email ? ` → ${al.target_email}` : ""}
                    </div>
                    <div className="text-[11px] text-slate-500">{fmtDateTime(al.created_at)}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ==================== TAB 5: NAMED GEMINI API KEYS ==================== */}
      {tab === "api_keys" && (
        <div className="space-y-5">
          {/* Add New Named Gemini API Key */}
          <form
            onSubmit={handleCreateApiKey}
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
          >
            <div className="flex items-center gap-2 text-sm font-bold text-slate-900">
              <KeyRound className="h-4 w-4 text-[#004A8F]" />
              Ajouter une clé API Gemini nommée au pool
            </div>
            <p className="mt-1 text-xs text-slate-500">
              Par défaut, tous les utilisateurs existants restent routés sur la clé système (<code>GEMINI_API_KEY</code>) sans aucune interruption de service.
              Ajoutez des clés nommées pour répartir le trafic par groupe/utilisateur et suivre leur consommation individuellement.
            </p>
            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-4">
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">
                  Nom de la clé *
                </label>
                <input
                  type="text"
                  required
                  minLength={2}
                  value={newKeyName}
                  onChange={(e) => setNewKeyName(e.target.value)}
                  placeholder="Ex. Clé Promo FMSB 2026"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
                />
              </div>
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">
                  Propriétaire / Projet
                </label>
                <input
                  type="text"
                  value={newKeyOwner}
                  onChange={(e) => setNewKeyOwner(e.target.value)}
                  placeholder="Ex. Compte Dr. X / Projet B"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
                />
              </div>
              <div className="md:col-span-2">
                <label className="block text-[11px] font-semibold text-slate-600">
                  Clé API Gemini *
                </label>
                <div className="mt-1 flex gap-2">
                  <input
                    type="password"
                    required
                    minLength={10}
                    value={newKeySecret}
                    onChange={(e) => setNewKeySecret(e.target.value)}
                    placeholder="AQ.Ab8... ou AIza..."
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs"
                  />
                  <button
                    type="submit"
                    disabled={busy || !newKeyName.trim() || !newKeySecret.trim()}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-[#004A8F] px-4 py-2 text-xs font-semibold text-white hover:bg-[#003a70] disabled:opacity-50"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    Ajouter
                  </button>
                </div>
              </div>
            </div>
          </form>

          {/* Key Cards & Per-Key User Consumption Stats */}
          <div className="space-y-4">
            {apiKeys.map((k) => {
              const isExpanded = expandedKeyId === k.id;
              return (
                <div
                  key={k.id}
                  className={`rounded-2xl border bg-white p-5 shadow-xs ${
                    k.is_default
                      ? "border-[#004A8F]/35"
                      : k.is_active
                      ? "border-slate-200"
                      : "border-slate-200 bg-slate-50/60 opacity-80"
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h4 className="text-sm font-bold text-slate-900">{k.name}</h4>
                        {k.is_default && (
                          <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-bold text-[#004A8F]">
                            Par défaut
                          </span>
                        )}
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                            k.is_active
                              ? "bg-emerald-100 text-emerald-800"
                              : "bg-slate-200 text-slate-600"
                          }`}
                        >
                          {k.is_active ? "Active" : "Désactivée"}
                        </span>
                        <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-700">
                          {k.masked_key}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        Propriétaire : <strong>{k.owner_label || "Non précisé"}</strong>
                        {k.last_used_at ? ` · Dernière utilisation : ${fmtDateTime(k.last_used_at)}` : ""}
                      </p>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setExpandedKeyId(isExpanded ? null : k.id)}
                        className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                      >
                        {isExpanded
                          ? "Masquer le détail utilisateurs"
                          : `Voir les utilisateurs (${k.users.length})`}
                      </button>
                      {!k.is_default && (
                        <>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleToggleApiKey(k)}
                            className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100"
                          >
                            {k.is_active ? "Désactiver" : "Activer"}
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleDeleteApiKey(k)}
                            className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-100"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            Supprimer
                          </button>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Per-key KPI bar */}
                  <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                    <div className="rounded-xl bg-slate-50 px-3 py-2">
                      <div className="text-[11px] text-slate-500">Utilisateurs assignés</div>
                      <div className="text-base font-bold text-slate-900">{k.assigned_users_count}</div>
                    </div>
                    <div className="rounded-xl bg-slate-50 px-3 py-2">
                      <div className="text-[11px] text-slate-500">Requêtes IA (total / auj.)</div>
                      <div className="text-base font-bold text-slate-900">
                        {k.total_calls.toLocaleString("fr-FR")}{" "}
                        <span className="text-xs font-semibold text-indigo-700">
                          / {k.calls_today} auj.
                        </span>
                      </div>
                    </div>
                    <div className="rounded-xl bg-slate-50 px-3 py-2">
                      <div className="text-[11px] text-slate-500">Tokens consommés (total / auj.)</div>
                      <div className="text-base font-bold text-slate-900">
                        {k.total_tokens.toLocaleString("fr-FR")}{" "}
                        <span className="text-xs font-semibold text-emerald-700">
                          / {k.tokens_today.toLocaleString("fr-FR")} auj.
                        </span>
                      </div>
                    </div>
                    <div className="rounded-xl bg-slate-50 px-3 py-2">
                      <div className="text-[11px] text-slate-500">Crédits débités</div>
                      <div className="text-base font-bold text-slate-900">
                        {k.total_credits.toLocaleString("fr-FR")} cr
                      </div>
                    </div>
                  </div>

                  {/* Expandable per-user breakdown + quick key switcher */}
                  {isExpanded && (
                    <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
                      <table className="w-full text-left text-xs">
                        <thead className="bg-slate-100 text-[11px] font-bold uppercase text-slate-600">
                          <tr>
                            <th className="px-3 py-2">Utilisateur</th>
                            <th className="px-3 py-2">Statut</th>
                            <th className="px-3 py-2 text-right">Appels (total / auj.)</th>
                            <th className="px-3 py-2 text-right">Tokens (total / auj.)</th>
                            <th className="px-3 py-2 text-right">Crédits</th>
                            <th className="px-3 py-2">Changer de clé</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {k.users.length === 0 ? (
                            <tr>
                              <td colSpan={6} className="px-3 py-4 text-center text-slate-500">
                                Aucun utilisateur assigné ni consommation enregistrée sur cette clé.
                              </td>
                            </tr>
                          ) : (
                            k.users.map((u) => (
                              <tr key={u.user_id} className="hover:bg-slate-50/70">
                                <td className="px-3 py-2">
                                  <div className="font-semibold text-slate-900">{u.full_name}</div>
                                  <div className="text-[11px] text-slate-500">{u.email}</div>
                                </td>
                                <td className="px-3 py-2">
                                  <span
                                    className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                                      u.is_assigned
                                        ? "bg-emerald-100 text-emerald-800"
                                        : "bg-slate-100 text-slate-600"
                                    }`}
                                  >
                                    {u.is_assigned ? "Assigné" : "Historique"}
                                  </span>
                                </td>
                                <td className="px-3 py-2 text-right font-mono">
                                  {u.calls_total} <span className="text-indigo-700">/ {u.calls_today}</span>
                                </td>
                                <td className="px-3 py-2 text-right font-mono">
                                  {u.tokens_total.toLocaleString("fr-FR")}{" "}
                                  <span className="text-emerald-700">
                                    / {u.tokens_today.toLocaleString("fr-FR")}
                                  </span>
                                </td>
                                <td className="px-3 py-2 text-right font-mono">{u.credits_total} cr</td>
                                <td className="px-3 py-2">
                                  <select
                                    aria-label={`Changer la clé API de ${u.full_name}`}
                                    disabled={busy}
                                    value={u.is_assigned ? k.id : users.find((x) => x.id === u.user_id)?.gemini_api_key_id || "default"}
                                    onChange={(e) => handleAssignUserApiKey(u.user_id, e.target.value)}
                                    className="rounded border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-800"
                                  >
                                    {apiKeys.map((opt) => (
                                      <option
                                        key={opt.id}
                                        value={opt.id}
                                        disabled={!opt.is_default && !opt.is_active}
                                      >
                                        {opt.name}
                                      </option>
                                    ))}
                                  </select>
                                </td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* TAB: DEMANDES D'ASSISTANCE */}
      {tab === "assistance" && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div>
              <h2 className="flex items-center gap-2 text-base font-bold text-slate-900">
                <LifeBuoy className="h-4 w-4 text-[#004A8F]" />
                Demandes d&apos;assistance documentaire
              </h2>
              <p className="mt-0.5 text-xs text-slate-500">
                Suivez les demandes envoyées depuis le bouton Assistance, ouvrez directement le protocole de l&apos;étudiant et marquez les demandes résolues.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {(
                [
                  { id: "pending", label: `En attente (${pendingSupportCount})` },
                  {
                    id: "resolved",
                    label: `Résolues (${supportRequests.length - pendingSupportCount})`,
                  },
                  { id: "all", label: `Toutes (${supportRequests.length})` },
                ] as const
              ).map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setSupportFilter(f.id)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                    supportFilter === f.id
                      ? "bg-[#004A8F] text-white"
                      : "border border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  {f.label}
                </button>
              ))}
              <button
                type="button"
                onClick={async () => {
                  try {
                    const fresh = await api.adminListUpdateRequests(100);
                    setSupportRequests(fresh);
                  } catch (err) {
                    setError(err instanceof Error ? err.message : String(err));
                  }
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Actualiser
              </button>
            </div>
          </div>

          {(() => {
            const filtered = supportRequests.filter((r) => {
              if (supportFilter === "pending") return !r.resolved;
              if (supportFilter === "resolved") return r.resolved;
              return true;
            });

            if (filtered.length === 0) {
              return (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center">
                  <CheckCircle2 className="mx-auto h-8 w-8 text-emerald-500" />
                  <p className="mt-2 text-sm font-semibold text-slate-800">
                    {supportFilter === "pending"
                      ? "Aucune demande d'assistance en attente."
                      : "Aucune demande dans cette catégorie."}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    Les nouvelles demandes soumises par les étudiants apparaîtront automatiquement ici.
                  </p>
                </div>
              );
            }

            return (
              <div className="space-y-3">
                {filtered.map((req) => (
                  <div
                    key={req.id}
                    className={`rounded-2xl border bg-white p-4 shadow-sm transition ${
                      req.resolved
                        ? "border-slate-200"
                        : "border-amber-300 ring-1 ring-amber-200/70"
                    }`}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-bold ${
                              req.resolved
                                ? "bg-emerald-100 text-emerald-800"
                                : "bg-amber-100 text-amber-800"
                            }`}
                          >
                            {req.resolved ? (
                              <CheckCircle2 className="h-3 w-3" />
                            ) : (
                              <Clock className="h-3 w-3" />
                            )}
                            {req.resolved ? "Résolue" : "En attente"}
                          </span>
                          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-semibold text-slate-700">
                            {SUPPORT_CATEGORY_LABELS[req.category] || req.category || "Général"}
                          </span>
                          {req.module && (
                            <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-800">
                              {req.module}
                              {req.section_id ? ` · ${req.section_id}` : ""}
                            </span>
                          )}
                          <span className="text-[11px] text-slate-400">
                            {fmtDateTime(req.created_at)}
                          </span>
                        </div>

                        <div className="text-sm font-bold text-slate-900">
                          {req.user_name || "Étudiant"}{" "}
                          {req.user_email && (
                            <span className="text-xs font-normal text-slate-500">
                              ({req.user_email})
                            </span>
                          )}
                        </div>

                        {(req.project_name || req.project_id) && (
                          <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-600">
                            <FileText className="h-3.5 w-3.5 text-slate-400" />
                            <span className="font-medium text-slate-800">
                              {req.project_name || "Protocole sans titre"}
                            </span>
                            {req.project_id && (
                              <code className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                                {req.project_id}
                              </code>
                            )}
                          </div>
                        )}
                      </div>

                      {req.project_id && onOpenUserWorkspace && (
                        <button
                          type="button"
                          onClick={() =>
                            onOpenUserWorkspace(req.project_id, req.user_name || "Étudiant")
                          }
                          className="inline-flex items-center gap-1.5 rounded-xl border border-[#004A8F]/30 bg-sky-50 px-3 py-1.5 text-xs font-semibold text-[#004A8F] hover:bg-sky-100"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                          Ouvrir le projet
                        </button>
                      )}
                    </div>

                    <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50/80 p-3 text-xs leading-relaxed text-slate-800 whitespace-pre-wrap">
                      {req.message}
                    </div>

                    {req.resolved ? (
                      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-emerald-200 bg-emerald-50/60 px-3 py-2 text-xs text-emerald-900">
                        <div>
                          <span className="font-semibold">
                            Résolue le {fmtDateTime(req.resolved_at)}
                          </span>
                          {req.resolved_by ? ` par ${req.resolved_by}` : ""}
                          {req.resolution_note ? (
                            <span className="block mt-0.5 text-emerald-800">
                              Note : {req.resolution_note}
                            </span>
                          ) : null}
                        </div>
                      </div>
                    ) : (
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <input
                          type="text"
                          value={resolveNotes[req.id] ?? ""}
                          onChange={(e) =>
                            setResolveNotes((prev) => ({
                              ...prev,
                              [req.id]: e.target.value,
                            }))
                          }
                          placeholder="Note de résolution visible par l'étudiant (ex: Sections complétées et conformes CAMES/Vancouver)..."
                          className="min-w-[260px] flex-1 rounded-xl border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-800 focus:border-[#004A8F] focus:outline-none"
                        />
                        <button
                          type="button"
                          disabled={resolvingRequestId === req.id}
                          onClick={() => handleResolveSupportRequest(req.id)}
                          className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-60"
                        >
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          {resolvingRequestId === req.id
                            ? "Validation..."
                            : "Marquer résolu"}
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            );
          })()}
        </div>
      )}

      {/* ================================================================ */}
      {/* TAB: PEDAGOGY / COURSE NOTES (MODULE 1 LEARNING BASE)           */}
      {/* ================================================================ */}
      {tab === "pedagogy" && (
        <div className="space-y-4">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-2 text-base font-bold text-slate-900">
                  <BookOpen className="h-4 w-4 text-[#004A8F]" />
                  Notes de cours & Référentiel pédagogique (Module 1)
                </h2>
                <p className="mt-0.5 text-xs text-slate-500">
                  Associez manuellement des notes de cours (texte ou fichier .txt, .md, .docx, .pdf) aux sections du protocole pour guider le Module 1 (Génération du plan) et le Module 3 (Validation ≥ 97 %).
                </p>
              </div>
              <select
                value={pedagogyFilterSectionId}
                onChange={(e) => setPedagogyFilterSectionId(e.target.value)}
                className="rounded-xl border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-800"
              >
                <option value="">Toutes les sections ({pedagogyNotes.length})</option>
                {pedagogySectionsCatalog.map((sec) => (
                  <option key={sec.id} value={sec.id}>
                    {sec.title} ({sec.id})
                  </option>
                ))}
              </select>
            </div>

            {/* Form to create/upload a new course note */}
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                if (!newNoteSectionIds.length) {
                  setError("Sélectionnez au moins une section à associer.");
                  return;
                }
                setPedagogyBusy(true);
                setError(null);
                setNotice(null);
                try {
                  if (newNoteFile) {
                    await api.adminUploadPedagogyNote(
                      newNoteFile,
                      newNoteTitle.trim(),
                      newNoteSectionIds
                    );
                  } else {
                    await api.adminCreatePedagogyNote({
                      title: newNoteTitle.trim(),
                      content: newNoteContent.trim(),
                      section_ids: newNoteSectionIds,
                    });
                  }
                  setNewNoteTitle("");
                  setNewNoteContent("");
                  setNewNoteFile(null);
                  setNewNoteSectionIds([]);
                  setNotice("Note de cours enregistrée et associée avec succès.");
                  await loadPedagogyTab();
                } catch (err) {
                  setError(err instanceof Error ? err.message : String(err));
                } finally {
                  setPedagogyBusy(false);
                }
              }}
              className="mt-4 space-y-3 rounded-xl border border-slate-200 bg-slate-50/70 p-4"
            >
              <div className="text-xs font-bold uppercase tracking-wide text-slate-700">
                Ajouter une note de cours
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <input
                  type="text"
                  value={newNoteTitle}
                  onChange={(e) => setNewNoteTitle(e.target.value)}
                  placeholder="Titre de la note (ex. Méthode de calcul de Schwartz)"
                  required={!newNoteFile}
                  className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900"
                />
                <input
                  type="file"
                  accept=".txt,.md,.docx,.pdf"
                  onChange={(e) => setNewNoteFile(e.target.files?.[0] ?? null)}
                  className="rounded-xl border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700"
                />
              </div>
              {!newNoteFile && (
                <textarea
                  value={newNoteContent}
                  onChange={(e) => setNewNoteContent(e.target.value)}
                  rows={4}
                  required
                  placeholder="Collez ici les principes méthodologiques, critères d'évaluation et consignes de cours pour la section..."
                  className="w-full rounded-xl border border-slate-300 bg-white p-3 text-xs text-slate-900"
                />
              )}
              <div>
                <div className="mb-1.5 text-[11px] font-semibold text-slate-700">
                  Sections associées ({newNoteSectionIds.length} sélectionnée(s)) :
                </div>
                <div className="grid max-h-40 grid-cols-1 gap-1.5 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2.5 sm:grid-cols-2 md:grid-cols-3">
                  {pedagogySectionsCatalog.map((sec) => {
                    const checked = newNoteSectionIds.includes(sec.id);
                    return (
                      <label
                        key={sec.id}
                        className={`flex cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1 text-[11px] ${
                          checked ? "bg-sky-50 font-semibold text-[#004A8F]" : "text-slate-700 hover:bg-slate-50"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() =>
                            setNewNoteSectionIds((prev) =>
                              prev.includes(sec.id)
                                ? prev.filter((id) => id !== sec.id)
                                : [...prev, sec.id]
                            )
                          }
                        />
                        <span className="truncate">{sec.title}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={pedagogyBusy}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-[#004A8F] px-4 py-2 text-xs font-semibold text-white hover:bg-[#003a72] disabled:opacity-50"
                >
                  <Plus className="h-3.5 w-3.5" />
                  {pedagogyBusy ? "Enregistrement..." : "Associer cette note de cours"}
                </button>
              </div>
            </form>
          </div>

          {/* List of existing notes (built-in + admin) */}
          <div className="space-y-3">
            {pedagogyNotes.map((note) => {
              const isEditing = editingNoteId === note.note_id;
              return (
                <div
                  key={note.note_id}
                  className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-bold text-slate-900">{note.title}</span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
                            note.source === "builtin"
                              ? "bg-indigo-100 text-indigo-800"
                              : "bg-emerald-100 text-emerald-800"
                          }`}
                        >
                          {note.source === "builtin" ? "Intégrée (pedagogie/)" : "Admin"}
                        </span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                            note.enabled
                              ? "bg-emerald-50 text-emerald-700"
                              : "bg-slate-200 text-slate-600"
                          }`}
                        >
                          {note.enabled ? "Active" : "Désactivée"}
                        </span>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {note.section_ids.map((sid) => {
                          const found = pedagogySectionsCatalog.find((s) => s.id === sid);
                          return (
                            <span
                              key={sid}
                              className="rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-700"
                            >
                              {found ? found.title : sid}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            await api.adminUpdatePedagogyNote(note.note_id, {
                              enabled: !note.enabled,
                            });
                            await loadPedagogyTab();
                          } catch (err) {
                            setError(err instanceof Error ? err.message : String(err));
                          }
                        }}
                        className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
                      >
                        {note.enabled ? "Désactiver" : "Activer"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (isEditing) {
                            setEditingNoteId(null);
                          } else {
                            setEditingNoteId(note.note_id);
                            setEditingNoteSectionIds([...note.section_ids]);
                          }
                        }}
                        className="rounded-lg border border-sky-200 bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-[#004A8F] hover:bg-sky-100"
                      >
                        {isEditing ? "Fermer" : "Sections"}
                      </button>
                      {note.source !== "builtin" && (
                        <button
                          type="button"
                          onClick={async () => {
                            try {
                              await api.adminDeletePedagogyNote(note.note_id);
                              await loadPedagogyTab();
                            } catch (err) {
                              setError(err instanceof Error ? err.message : String(err));
                            }
                          }}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-200 px-2.5 py-1 text-[11px] font-semibold text-rose-700 hover:bg-rose-50"
                        >
                          <Trash2 className="h-3 w-3" />
                          Supprimer
                        </button>
                      )}
                    </div>
                  </div>
                  <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs text-slate-600">
                    {note.content}
                  </p>
                  {isEditing && (
                    <div className="mt-3 rounded-xl border border-sky-200 bg-sky-50/50 p-3">
                      <div className="mb-2 text-[11px] font-bold text-[#004A8F]">
                        Modifier l'association aux sections :
                      </div>
                      <div className="grid max-h-40 grid-cols-1 gap-1.5 overflow-y-auto rounded-lg bg-white p-2 sm:grid-cols-2 md:grid-cols-3">
                        {pedagogySectionsCatalog.map((sec) => {
                          const checked = editingNoteSectionIds.includes(sec.id);
                          return (
                            <label key={sec.id} className="flex cursor-pointer items-center gap-1.5 text-[11px] text-slate-700">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() =>
                                  setEditingNoteSectionIds((prev) =>
                                    prev.includes(sec.id)
                                      ? prev.filter((id) => id !== sec.id)
                                      : [...prev, sec.id]
                                  )
                                }
                              />
                              <span className="truncate">{sec.title}</span>
                            </label>
                          );
                        })}
                      </div>
                      <div className="mt-2 flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={async () => {
                            try {
                              await api.adminUpdatePedagogyNote(note.note_id, {
                                section_ids: editingNoteSectionIds,
                              });
                              setEditingNoteId(null);
                              setNotice("Association de sections mise à jour.");
                              await loadPedagogyTab();
                            } catch (err) {
                              setError(err instanceof Error ? err.message : String(err));
                            }
                          }}
                          className="rounded-lg bg-[#004A8F] px-3 py-1 text-xs font-semibold text-white hover:bg-[#003a72]"
                        >
                          Enregistrer les sections
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
