import { useCallback, useEffect, useRef, useState } from "react";
import {
  Airplay,
  AlertCircle,
  AlertTriangle,
  Bell,
  CheckCircle2,
  ClipboardCheck,
  FileUp,
  FolderPlus,
  Globe,
  KeyRound,
  Layers,
  Lightbulb,
  Lock,
  LogOut,
  Menu,
  PenSquare,
  Shield,
  Sparkles,
  X,
} from "lucide-react";
import {
  api,
  clearAuthTokens,
  getAccessToken,
  setUnauthorizedHandler,
} from "./api/client";
import { AdminPortal } from "./components/AdminPortal";
import { AssistantSmartResearchWidget } from "./components/AssistantSmartResearchWidget";
import { ChangePasswordModal } from "./components/ChangePasswordModal";
import { LoginScreen } from "./components/LoginScreen";
import { Module1Assistant } from "./components/Module1Assistant";
import { Module2Editor } from "./components/Module2Editor";
import { Module3DataEntry } from "./components/Module3DataEntry";
import { Module4Presentation } from "./components/Module4Presentation";
import { useLanguage } from "./i18n/LanguageContext";
import type { AppLang } from "./i18n/translations";
import type { ModuleKey, TokenResponse, UserMeOut } from "./types/auth";
import type {
  CanvasSectionSpec,
  CoverMetadata,
  EntryMode,
  FormattingRules,
  ProtocolProject,
  StepStatus,
} from "./types/protocol";
import { formatErrorForRole, sanitizeTechnicalMessage } from "./utils/errorFormatter";

interface AdminErrorAlert {
  id: string;
  created_at: string;
  user_id: string;
  user_name: string;
  user_email: string;
  user_role: string;
  module: string;
  context: string;
  status_code?: number | null;
  error_message: string;
  resolved?: boolean;
  resolved_at?: string | null;
  resolved_by?: string;
  resolution_note?: string;
}

interface ResolvedErrorNotification {
  id: string;
  alert_id: string;
  module: string;
  resolved_at: string;
  message_fr: string;
  message_en: string;
  note: string;
}

export default function App() {
  const { lang, setLang, t } = useLanguage();
  const [currentUser, setCurrentUser] = useState<UserMeOut | null>(null);
  const [authLoading, setAuthLoading] = useState<boolean>(() => Boolean(getAccessToken()));
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const [showPasswordModal, setShowPasswordModal] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const [activeView, setActiveView] = useState<"studio" | "admin">(() =>
    typeof window !== "undefined" && window.location.pathname.startsWith("/admin")
      ? "admin"
      : "studio"
  );

  const [canvas, setCanvas] = useState<CanvasSectionSpec[]>([]);
  const [projects, setProjects] = useState<ProtocolProject[]>([]);
  const [activeProject, setActiveProject] = useState<ProtocolProject | null>(null);

  const [activeModule, setActiveModule] = useState<ModuleKey>("module1");
  const [selectedSectionId, setSelectedSectionId] = useState<string>("cover_page");
  const [showCreateModal, setShowCreateModal] = useState(false);

  const [newName, setNewName] = useState("");
  const [newMode, setNewMode] = useState<EntryMode>("new_topic");
  const [newIdea, setNewIdea] = useState("");
  const [newStudyType, setNewStudyType] = useState("Étude transversale analytique");
  const [newLang, setNewLang] = useState<AppLang>(lang);

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastSyncedProjectIdRef = useRef<string | null>(null);
  const lastReportedErrorRef = useRef<{ msg: string; ts: number }>({ msg: "", ts: 0 });

  // Live Admin Error Alert feed & real-time toast
  const [adminAlerts, setAdminAlerts] = useState<AdminErrorAlert[]>([]);
  const [liveAdminToast, setLiveAdminToast] = useState<AdminErrorAlert | null>(null);
  const [showAdminAlertsMenu, setShowAdminAlertsMenu] = useState(false);
  const [unreadAdminAlerts, setUnreadAdminAlerts] = useState(0);
  const [resolvingAlertId, setResolvingAlertId] = useState<string | null>(null);
  const [resolvedNotifications, setResolvedNotifications] = useState<ResolvedErrorNotification[]>([]);
  const seenAdminAlertIdsRef = useRef<Set<string>>(new Set());
  const adminAlertsInitializedRef = useRef(false);

  // Automatically report any runtime error to backend so admins receive the real error in real time
  useEffect(() => {
    if (!error || !currentUser) return;
    const now = Date.now();
    if (
      lastReportedErrorRef.current.msg === error &&
      now - lastReportedErrorRef.current.ts < 5000
    ) {
      return;
    }
    lastReportedErrorRef.current = { msg: error, ts: now };
    api
      .reportClientError({
        message: sanitizeTechnicalMessage(error),
        module: activeModule,
        context: activeView === "admin" ? "admin_portal" : selectedSectionId,
      })
      .catch(() => {
        /* never loop on telemetry failure */
      });
  }, [error, currentUser, activeModule, activeView, selectedSectionId]);

  // Poll live error alerts for Admin users so they see student & system incidents as they happen
  useEffect(() => {
    if (!currentUser || currentUser.role !== "admin") {
      adminAlertsInitializedRef.current = false;
      seenAdminAlertIdsRef.current.clear();
      setAdminAlerts([]);
      setLiveAdminToast(null);
      setUnreadAdminAlerts(0);
      return;
    }

    let cancelled = false;
    const pollAlerts = async () => {
      try {
        const items = await api.adminListErrorAlerts(20);
        if (cancelled) return;
        setAdminAlerts(items);
        if (!adminAlertsInitializedRef.current) {
          items.forEach((a) => seenAdminAlertIdsRef.current.add(a.id));
          adminAlertsInitializedRef.current = true;
          return;
        }
        const newlyArrived = items.filter((a) => !seenAdminAlertIdsRef.current.has(a.id));
        if (newlyArrived.length > 0) {
          newlyArrived.forEach((a) => seenAdminAlertIdsRef.current.add(a.id));
          setUnreadAdminAlerts((c) => c + newlyArrived.length);
          setLiveAdminToast(newlyArrived[0]);
        }
      } catch {
        /* silent background poll */
      }
    };

    pollAlerts();
    const interval = window.setInterval(pollAlerts, 15000);
    const onFocus = () => pollAlerts();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [currentUser]);

  // Poll "issue fixed" notifications targeted to the current user's account
  useEffect(() => {
    if (!currentUser) {
      setResolvedNotifications([]);
      return;
    }
    let cancelled = false;
    const pollResolved = async () => {
      try {
        const items = await api.listResolvedErrorNotifications();
        if (!cancelled) {
          setResolvedNotifications(items);
        }
      } catch {
        /* silent background poll */
      }
    };
    pollResolved();
    const interval = window.setInterval(pollResolved, 20000);
    const onFocus = () => pollResolved();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [currentUser]);

  const handleResolveAdminAlert = useCallback(async (alertId: string) => {
    setResolvingAlertId(alertId);
    try {
      const res = await api.adminResolveErrorAlert(alertId);
      setAdminAlerts((prev) =>
        prev.map((a) =>
          a.id === alertId
            ? {
                ...a,
                resolved: true,
                resolved_at: res.resolved_at,
                resolved_by: res.resolved_by,
                resolution_note: res.resolution_note,
              }
            : a
        )
      );
      setLiveAdminToast((prev) => (prev && prev.id === alertId ? null : prev));
    } catch {
      /* ignore transient resolve failure */
    } finally {
      setResolvingAlertId(null);
    }
  }, []);

  const handleDismissResolvedNotification = useCallback(async (notificationId: string) => {
    setResolvedNotifications((prev) => prev.filter((n) => n.id !== notificationId));
    try {
      await api.dismissResolvedErrorNotification(notificationId);
    } catch {
      /* already removed locally */
    }
  }, []);

  const loadWorkspaceData = useCallback(async (targetLang?: AppLang) => {
    setLoading(true);
    setError(null);
    const effectiveLang: AppLang = targetLang ?? lang;
    try {
      const [canvasRes, projectsRes] = await Promise.all([
        api.getCanvas(effectiveLang),
        api.listProjects(),
      ]);
      setCanvas(canvasRes.sections);
      setProjects(projectsRes);
      setActiveProject((prev) => {
        if (prev) {
          const match = projectsRes.find((p) => p.project_id === prev.project_id);
          if (match) return match;
        }
        return projectsRes[0] ?? null;
      });
      if (canvasRes.sections.length > 0) {
        setSelectedSectionId((prev) => prev || canvasRes.sections[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [lang]);

  useEffect(() => {
    if (!activeProject) return;
    if (lastSyncedProjectIdRef.current === activeProject.project_id) return;
    lastSyncedProjectIdRef.current = activeProject.project_id;
    const projLang: AppLang = activeProject.language === "en" ? "en" : "fr";
    if (projLang !== lang) {
      setLang(projLang);
      api
        .getCanvas(projLang)
        .then((res) => setCanvas(res.sections))
        .catch(() => {});
    }
  }, [activeProject?.project_id, activeProject?.language, lang, setLang]);

  const handleChangeLanguage = useCallback(
    async (nextLang: AppLang) => {
      setLang(nextLang);
      setNewLang(nextLang);
      try {
        const res = await api.getCanvas(nextLang);
        setCanvas(res.sections);
      } catch {
        // Ignore transient canvas fetch failure
      }
      if (activeProject) {
        try {
          const updated = await api.updateProjectLanguage(
            activeProject.project_id,
            nextLang
          );
          setActiveProject(updated);
          setProjects((prev) =>
            prev.map((p) => (p.project_id === updated.project_id ? updated : p))
          );
        } catch {
          // Non-fatal if user has no active write license; UI language & canvas still adapt
        }
      }
    },
    [activeProject, setLang]
  );

  const refreshMe = useCallback(() => {
    if (!getAccessToken()) return;
    api
      .getMe()
      .then((me) => setCurrentUser(me))
      .catch(() => {
        // Unauthorized handler handles revoked sessions
      });
  }, []);

  useEffect(() => {
    setUnauthorizedHandler((reason) => {
      setCurrentUser(null);
      setProjects([]);
      setActiveProject(null);
      setActiveView("studio");
      setAuthNotice(reason || "Votre session a expiré. Veuillez vous reconnecter.");
    });

    const onPopState = () => {
      if (window.location.pathname.startsWith("/admin")) {
        setActiveView("admin");
      } else {
        setActiveView("studio");
      }
    };
    window.addEventListener("popstate", onPopState);

    if (getAccessToken()) {
      api
        .getMe()
        .then((me) => {
          setCurrentUser(me);
          if (me.role !== "admin" && window.location.pathname.startsWith("/admin")) {
            window.history.replaceState({}, "", "/");
            setActiveView("studio");
          }
        })
        .catch(() => {
          clearAuthTokens();
          setCurrentUser(null);
        })
        .finally(() => setAuthLoading(false));
    } else {
      setAuthLoading(false);
    }

    return () => {
      setUnauthorizedHandler(null);
      window.removeEventListener("popstate", onPopState);
    };
  }, []);

  useEffect(() => {
    if (currentUser && !currentUser.must_change_password) {
      loadWorkspaceData();
    }
  }, [currentUser?.id, currentUser?.must_change_password]);

  function handleAuthenticated(tokenRes: TokenResponse) {
    setAuthNotice(null);
    setError(null);
    setCurrentUser(tokenRes.user);
    if (tokenRes.user.role !== "admin" && window.location.pathname.startsWith("/admin")) {
      window.history.replaceState({}, "", "/");
      setActiveView("studio");
    }
  }

  async function handleLogout() {
    try {
      if (getAccessToken()) {
        await api.logout();
      }
    } catch {
      // Ignore logout network error
    } finally {
      clearAuthTokens();
      setCurrentUser(null);
      setProjects([]);
      setActiveProject(null);
      lastSyncedProjectIdRef.current = null;
      setShowCreateModal(false);
      setShowPasswordModal(false);
      setActiveView("studio");
      setAuthNotice(null);
      if (window.location.pathname !== "/") {
        window.history.pushState({}, "", "/");
      }
    }
  }

  const handleOpenUserWorkspace = useCallback(
    async (projectId: string, _studentName: string) => {
      setShowCreateModal(false);
      setLoading(true);
      setError(null);
      setActiveModule("module1");
      setSelectedSectionId("cover_page");
      setActiveView("studio");
      if (window.location.pathname.startsWith("/admin")) {
        window.history.pushState({}, "", "/");
      }
      try {
        const proj = await api.getProject(projectId);
        setActiveProject(proj);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    []
  );

  function openAdminPortal() {
    if (currentUser?.role !== "admin") return;
    setActiveView("admin");
    if (!window.location.pathname.startsWith("/admin")) {
      window.history.pushState({}, "", "/admin");
    }
  }

  function exitAdminPortal() {
    setActiveView("studio");
    if (window.location.pathname.startsWith("/admin")) {
      window.history.pushState({}, "", "/");
    }
    loadWorkspaceData();
    refreshMe();
  }

  async function handleCreateProject(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const created = await api.createProject({
        name: newName.trim(),
        entry_mode: newMode,
        research_idea: newIdea.trim(),
        study_type: newStudyType.trim(),
        language: newLang,
      });
      setProjects([created, ...projects]);
      setActiveProject(created);
      if (newLang !== lang) {
        setLang(newLang);
        api
          .getCanvas(newLang)
          .then((res) => setCanvas(res.sections))
          .catch(() => {});
      }
      setShowCreateModal(false);
      setNewName("");
      setNewIdea("");
      refreshMe();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveSection(
    sectionId: string,
    content: string,
    status: StepStatus,
    userNotes: string,
    contentHtml?: string
  ): Promise<boolean> {
    if (!activeProject) return false;
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateSection(activeProject.project_id, sectionId, {
        content_markdown: content,
        content_html: contentHtml,
        status,
        user_notes: userNotes,
      });
      setActiveProject(updated);
      setProjects((prev) =>
        prev.map((p) => (p.project_id === updated.project_id ? updated : p))
      );
      refreshMe();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveCover(cover: CoverMetadata) {
    if (!activeProject) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateCover(activeProject.project_id, cover);
      setActiveProject(updated);
      setProjects((prev) =>
        prev.map((p) => (p.project_id === updated.project_id ? updated : p))
      );
      refreshMe();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveFormatting(formatting: FormattingRules) {
    if (!activeProject) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateFormatting(activeProject.project_id, formatting);
      setActiveProject(updated);
      setProjects((prev) =>
        prev.map((p) => (p.project_id === updated.project_id ? updated : p))
      );
      refreshMe();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  function renderModuleBadge(moduleKey: ModuleKey) {
    if (!currentUser || currentUser.role === "admin") return null;
    const ent = currentUser.entitlements?.[moduleKey];
    if (ent?.is_active) {
      const soon = ent.days_remaining <= 7;
      return (
        <span
          className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
            soon ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"
          }`}
        >
          {ent.days_remaining} {lang === "en" ? "d" : "j"}
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-0.5 rounded-full bg-rose-100 px-1.5 py-0.5 text-[10px] font-bold text-rose-800">
        <Lock className="h-2.5 w-2.5" /> {t("nav.readonly_badge")}
      </span>
    );
  }

  function moduleStatusDotClass(moduleKey: ModuleKey): string {
    if (!currentUser || currentUser.role === "admin") return "bg-emerald-500";
    const ent = currentUser.entitlements?.[moduleKey];
    if (!ent?.is_active) return "bg-rose-500";
    return ent.days_remaining <= 7 ? "bg-amber-500" : "bg-emerald-500";
  }

  function selectMobileModule(mk: ModuleKey) {
    setActiveModule(mk);
    setMobileMenuOpen(false);
    if (activeView !== "studio") {
      setActiveView("studio");
      if (window.location.pathname.startsWith("/admin")) {
        window.history.pushState({}, "", "/");
      }
    }
  }

  if (authLoading) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[#F4F7FB] text-sm font-medium text-slate-600">
        <img
          src="/logo-smart-research.png"
          alt="Smart Research"
          className="h-24 w-auto animate-pulse object-contain"
        />
        <span>{t("nav.checking_session")}</span>
      </div>
    );
  }

  if (!currentUser) {
    return <LoginScreen onAuthenticated={handleAuthenticated} notice={authNotice} />;
  }

  if (currentUser.must_change_password) {
    return (
      <div className="min-h-screen bg-slate-100">
        <ChangePasswordModal
          userEmail={currentUser.email}
          mandatory={true}
          onSuccess={(res) => {
            setCurrentUser(res.user);
            setAuthNotice(null);
          }}
          onLogout={handleLogout}
        />
      </div>
    );
  }

  const isAdmin = currentUser.role === "admin";
  const canCreateProject = isAdmin || projects.length === 0;
  const activeEntitlement = currentUser.entitlements?.[activeModule];
  const isModuleReadOnly = !isAdmin && !activeEntitlement?.is_active;

  return (
    <div className="min-h-screen bg-[#F4F7FB]">
      {/* Top Navbar */}
      <header className="sticky top-0 z-20 border-b border-slate-200/80 bg-white/95 shadow-xs backdrop-blur">
        <div className="h-1 w-full bg-gradient-to-r from-[#004A8F] via-[#0284C7] to-[#1FA84F]" />
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-3.5 py-2 sm:px-6 sm:py-2.5">
          <div className="flex min-w-0 items-center gap-2.5 sm:gap-3">
            <img
              src="/logo-mark.png"
              alt="Logo Smart Research"
              className="h-10 w-10 shrink-0 rounded-xl border border-slate-200/80 bg-white object-contain p-0.5 shadow-xs"
            />
            <div className="min-w-0">
              <h1 className="truncate text-sm font-extrabold tracking-tight text-slate-900 sm:text-base">
                <span className="text-[#004A8F]">Smart</span>{" "}
                <span className="text-[#1FA84F]">Research</span>
              </h1>
              <p className="hidden text-[11px] font-medium text-slate-500 sm:block">
                {t("app.tagline")}
              </p>
            </div>
          </div>

          {/* Mobile Compact Header Controls (< md) */}
          <div className="flex items-center gap-2 md:hidden">
            <div
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                isAdmin
                  ? "bg-indigo-50 text-indigo-800"
                  : currentUser.ai_quota.credits_remaining <= 5
                  ? "bg-amber-100 text-amber-900"
                  : "bg-indigo-50 text-indigo-800"
              }`}
            >
              <Sparkles className="h-3 w-3" />
              {isAdmin ? t("nav.role_admin") : `${currentUser.ai_quota.credits_remaining} cr`}
            </div>
            <button
              type="button"
              aria-label="Menu compte et protocole"
              onClick={() => setMobileMenuOpen((v) => !v)}
              className="inline-flex min-h-[40px] min-w-[40px] items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
            >
              {mobileMenuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
          </div>

          {/* Desktop Controls (md+) */}
          <div className="hidden flex-wrap items-center gap-2 md:flex">
            {/* AI Credits Gauge */}
            <div
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${
                isAdmin
                  ? "bg-indigo-50 text-indigo-800"
                  : currentUser.ai_quota.credits_remaining <= 5
                  ? "bg-amber-100 text-amber-900"
                  : "bg-indigo-50 text-indigo-800"
              }`}
              title={t("nav.ai_credits_tooltip")}
            >
              <Sparkles className="h-3.5 w-3.5" />
              {isAdmin
                ? t("nav.ai_unlimited")
                : t("nav.ai_credits", {
                    remaining: currentUser.ai_quota.credits_remaining,
                    total: currentUser.ai_quota.credits_total,
                  })}
            </div>

            {/* Default Language Menu */}
            <div
              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700"
              title={t("nav.language_label")}
            >
              <Globe className="h-3.5 w-3.5 text-[#004A8F]" />
              <select
                aria-label="Langue par défaut"
                data-testid="default-language-select"
                value={lang}
                onChange={(e) =>
                  handleChangeLanguage(e.target.value === "en" ? "en" : "fr")
                }
                className="cursor-pointer bg-transparent pr-1 text-xs font-semibold text-slate-800 focus:outline-none"
              >
                <option value="fr">FR · Français</option>
                <option value="en">EN · English</option>
              </select>
            </div>

            {/* Project Selector (Studio mode) */}
            {activeView === "studio" && (projects.length > 0 || activeProject) && (
              <select
                value={activeProject?.project_id ?? ""}
                onChange={(e) => {
                  const found = projects.find((p) => p.project_id === e.target.value);
                  if (found) setActiveProject(found);
                }}
                className="max-w-[180px] rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700"
              >
                {activeProject &&
                  !projects.some((p) => p.project_id === activeProject.project_id) && (
                    <option value={activeProject.project_id}>
                      [Assistance] {activeProject.name}
                    </option>
                  )}
                {projects.map((p) => (
                  <option key={p.project_id} value={p.project_id}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}

            {/* New Protocol (Admin or first protocol for student) */}
            {activeView === "studio" && canCreateProject && (
              <button
                type="button"
                onClick={() => {
                  setNewLang(lang);
                  setShowCreateModal(true);
                }}
                className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-[#004A8F] to-[#0284C7] px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs transition hover:brightness-105"
              >
                <FolderPlus className="h-3.5 w-3.5" />
                {t("nav.new_protocol")}
              </button>
            )}

            {/* Admin Portal Switch & Live Error Alerts Bell */}
            {isAdmin && (
              <>
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => {
                      setShowAdminAlertsMenu((v) => !v);
                      setUnreadAdminAlerts(0);
                    }}
                    className={`relative inline-flex items-center justify-center rounded-lg border p-1.5 transition ${
                      unreadAdminAlerts > 0
                        ? "border-rose-300 bg-rose-50 text-rose-700 hover:bg-rose-100"
                        : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                    }`}
                    title={
                      lang === "en"
                        ? "Live system & student error alerts"
                        : "Alertes erreurs temps réel (étudiants & système)"
                    }
                  >
                    <Bell className="h-3.5 w-3.5" />
                    {unreadAdminAlerts > 0 && (
                      <span className="absolute -right-1.5 -top-1.5 inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-rose-600 px-1 text-[10px] font-bold text-white">
                        {unreadAdminAlerts > 9 ? "9+" : unreadAdminAlerts}
                      </span>
                    )}
                  </button>

                  {showAdminAlertsMenu && (
                    <div className="absolute right-0 top-full z-50 mt-2 w-96 max-w-[90vw] rounded-xl border border-slate-200 bg-white p-3 text-xs shadow-xl">
                      <div className="flex items-center justify-between border-b border-slate-100 pb-2">
                        <span className="font-bold text-slate-900">
                          {lang === "en"
                            ? "Real-Time Error Feed (Admin)"
                            : "Journal d'erreurs en direct (Admin)"}
                        </span>
                        <button
                          type="button"
                          onClick={() => setShowAdminAlertsMenu(false)}
                          className="text-slate-400 hover:text-slate-700"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      {adminAlerts.length === 0 ? (
                        <p className="py-4 text-center text-slate-500">
                          {lang === "en"
                            ? "No recent errors recorded."
                            : "Aucune erreur récente enregistrée."}
                        </p>
                      ) : (
                        <div className="mt-2 max-h-80 space-y-2 overflow-y-auto pr-1">
                          {adminAlerts.map((a) => (
                            <div
                              key={a.id}
                              className={`rounded-lg border p-2.5 ${
                                a.resolved
                                  ? "border-emerald-200 bg-emerald-50/70"
                                  : "border-rose-200 bg-rose-50/70"
                              }`}
                            >
                              <div
                                className={`flex items-center justify-between gap-2 text-[11px] font-semibold ${
                                  a.resolved ? "text-emerald-900" : "text-rose-900"
                                }`}
                              >
                                <span className="truncate">
                                  {a.user_name || a.user_email || "Utilisateur"}
                                  {a.module ? ` · ${a.module}` : ""}
                                </span>
                                <span
                                  className={`shrink-0 font-normal ${
                                    a.resolved ? "text-emerald-700" : "text-rose-700"
                                  }`}
                                >
                                  {new Date(a.created_at).toLocaleTimeString([], {
                                    hour: "2-digit",
                                    minute: "2-digit",
                                  })}
                                </span>
                              </div>
                              <p
                                className={`mt-1 break-words font-mono text-[11px] ${
                                  a.resolved ? "text-emerald-950/80" : "text-rose-950"
                                }`}
                              >
                                {sanitizeTechnicalMessage(a.error_message)}
                              </p>
                              <div className="mt-2 flex items-center justify-between gap-2">
                                {a.resolved ? (
                                  <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800">
                                    <CheckCircle2 className="h-3 w-3" />
                                    {lang === "en" ? "Fixed · User notified" : "Résolu · Utilisateur notifié"}
                                  </span>
                                ) : (
                                  <button
                                    type="button"
                                    disabled={resolvingAlertId === a.id}
                                    onClick={() => handleResolveAdminAlert(a.id)}
                                    className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-[11px] font-semibold text-white shadow-xs transition hover:bg-emerald-700 disabled:opacity-60"
                                  >
                                    <CheckCircle2 className="h-3 w-3" />
                                    {resolvingAlertId === a.id
                                      ? lang === "en"
                                        ? "Notifying..."
                                        : "Notification..."
                                      : lang === "en"
                                      ? "Mark Fixed & Notify User"
                                      : "Marquer résolu & notifier"}
                                  </button>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <button
                  type="button"
                  onClick={activeView === "admin" ? exitAdminPortal : openAdminPortal}
                  className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
                    activeView === "admin"
                      ? "bg-slate-900 text-white shadow-xs"
                      : "border border-[#004A8F]/25 bg-sky-50 text-[#004A8F] hover:bg-sky-100"
                  }`}
                >
                  <Shield className="h-3.5 w-3.5" />
                  {activeView === "admin" ? t("nav.studio") : t("nav.admin")}
                </button>
              </>
            )}

            {/* User Badge & Account Actions */}
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs">
              <span className="max-w-[130px] truncate font-semibold text-slate-800">
                {currentUser.full_name}
              </span>
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                  isAdmin ? "bg-indigo-100 text-indigo-800" : "bg-slate-200 text-slate-700"
                }`}
              >
                {isAdmin ? t("nav.role_admin") : t("nav.role_student")}
              </span>
            </div>

            <button
              type="button"
              onClick={() => setShowPasswordModal(true)}
              className="rounded-lg border border-slate-300 bg-white p-1.5 text-slate-600 hover:bg-slate-50 hover:text-slate-900"
              title={t("nav.change_password_title")}
            >
              <KeyRound className="h-3.5 w-3.5" />
            </button>

            <button
              type="button"
              onClick={handleLogout}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              title={t("nav.logout")}
            >
              <LogOut className="h-3.5 w-3.5" />
              <span>{t("nav.logout")}</span>
            </button>
          </div>
        </div>

        {/* Expandable Mobile Account & Project Menu Sheet */}
        {mobileMenuOpen && (
          <div className="space-y-3 border-t border-slate-200 bg-slate-50 px-4 py-3.5 md:hidden">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-bold text-slate-900">
                  {currentUser.full_name}
                </div>
                <div className="truncate text-xs text-slate-500">{currentUser.email}</div>
              </div>
              <span
                className={`shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-bold ${
                  isAdmin ? "bg-indigo-100 text-indigo-800" : "bg-slate-200 text-slate-700"
                }`}
              >
                {isAdmin ? t("nav.role_admin") : t("nav.role_student")}
              </span>
            </div>

            <div className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs">
              <span className="inline-flex items-center gap-1.5 font-semibold text-slate-700">
                <Globe className="h-3.5 w-3.5 text-[#004A8F]" />
                {t("nav.language_label")}
              </span>
              <select
                aria-label={t("nav.language_label")}
                value={lang}
                onChange={(e) =>
                  handleChangeLanguage(e.target.value === "en" ? "en" : "fr")
                }
                className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-semibold text-slate-800"
              >
                <option value="fr">FR · Français</option>
                <option value="en">EN · English</option>
              </select>
            </div>

            {!isAdmin && (
              <div className="grid grid-cols-2 gap-1.5 text-[11px]">
                {(["module1", "module2", "module3", "module4"] as ModuleKey[]).map((mk, idx) => {
                  const ent = currentUser.entitlements?.[mk];
                  return (
                    <div
                      key={mk}
                      className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-2.5 py-1.5"
                    >
                      <span className="font-semibold text-slate-700">M{idx + 1}</span>
                      <span
                        className={
                          ent?.is_active
                            ? "font-bold text-emerald-700"
                            : "font-semibold text-rose-700"
                        }
                      >
                        {ent?.is_active ? `${ent.days_remaining} ${lang === "en" ? "d" : "j"}` : t("nav.expired")}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}

            {activeView === "studio" && projects.length > 0 && (
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">
                  {t("nav.active_protocol")}
                </label>
                <select
                  value={activeProject?.project_id ?? ""}
                  onChange={(e) => {
                    const found = projects.find((p) => p.project_id === e.target.value);
                    if (found) setActiveProject(found);
                    setMobileMenuOpen(false);
                  }}
                  className="mt-1 min-h-[42px] w-full rounded-lg border border-slate-300 bg-white px-3 text-xs font-medium text-slate-800"
                >
                  {projects.map((p) => (
                    <option key={p.project_id} value={p.project_id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="grid grid-cols-2 gap-2 pt-1">
              {activeView === "studio" && canCreateProject && (
                <button
                  type="button"
                  onClick={() => {
                    setNewLang(lang);
                    setShowCreateModal(true);
                    setMobileMenuOpen(false);
                  }}
                  className="col-span-2 inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-xl bg-gradient-to-r from-[#004A8F] to-[#0284C7] px-3 text-xs font-semibold text-white shadow-xs"
                >
                  <FolderPlus className="h-4 w-4" />
                  {t("nav.new_protocol")}
                </button>
              )}
              {isAdmin && (
                <button
                  type="button"
                  onClick={() => {
                    setMobileMenuOpen(false);
                    if (activeView === "admin") exitAdminPortal();
                    else openAdminPortal();
                  }}
                  className="col-span-2 inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-xl bg-[#004A8F] px-3 text-xs font-semibold text-white"
                >
                  <Shield className="h-4 w-4" />
                  {activeView === "admin" ? t("nav.studio_open_mobile") : t("nav.admin_open_mobile")}
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setMobileMenuOpen(false);
                  setShowPasswordModal(true);
                }}
                className="inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-xs font-semibold text-slate-700"
              >
                <KeyRound className="h-3.5 w-3.5" />
                {t("nav.password")}
              </button>
              <button
                type="button"
                onClick={handleLogout}
                className="inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 text-xs font-semibold text-rose-700"
              >
                <LogOut className="h-3.5 w-3.5" />
                {t("nav.logout")}
              </button>
            </div>
          </div>
        )}

        {/* Four-Module Switcher (Desktop md+) */}
        {activeView === "studio" && activeProject && (
          <div className="hidden border-t border-slate-100 bg-[#F4F7FB]/80 md:block">
            <div className="mx-auto flex max-w-7xl flex-wrap gap-2 px-4 py-2 sm:px-6">
              <button
                type="button"
                onClick={() => setActiveModule("module1")}
                className={`inline-flex items-center gap-2 rounded-xl px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module1"
                    ? "bg-white text-[#004A8F] shadow-sm ring-1 ring-[#004A8F]/25"
                    : "text-slate-600 hover:bg-white/60 hover:text-slate-900"
                }`}
              >
                <Layers className="h-3.5 w-3.5" />
                {t("mod1.tab")}
                {renderModuleBadge("module1")}
              </button>
              <button
                type="button"
                onClick={() => setActiveModule("module2")}
                className={`inline-flex items-center gap-2 rounded-xl px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module2"
                    ? "bg-white text-[#1FA84F] shadow-sm ring-1 ring-[#1FA84F]/30"
                    : "text-slate-600 hover:bg-white/60 hover:text-slate-900"
                }`}
              >
                <PenSquare className="h-3.5 w-3.5" />
                {t("mod2.tab")}
                {renderModuleBadge("module2")}
              </button>
              <button
                type="button"
                onClick={() => setActiveModule("module3")}
                className={`inline-flex items-center gap-2 rounded-xl px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module3"
                    ? "bg-white text-[#0284C7] shadow-sm ring-1 ring-[#0284C7]/30"
                    : "text-slate-600 hover:bg-white/60 hover:text-slate-900"
                }`}
              >
                <ClipboardCheck className="h-3.5 w-3.5" />
                {t("mod3.tab")}
                <span className="rounded-full bg-[#0284C7]/10 px-1.5 py-0.5 text-[10px] font-bold text-[#0284C7]">
                  {activeProject.patient_records?.length ?? 0}
                </span>
                {renderModuleBadge("module3")}
              </button>
              <button
                type="button"
                onClick={() => setActiveModule("module4")}
                className={`inline-flex items-center gap-2 rounded-xl px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module4"
                    ? "bg-white text-[#004A8F] shadow-sm ring-1 ring-[#004A8F]/25"
                    : "text-slate-600 hover:bg-white/60 hover:text-slate-900"
                }`}
              >
                <Airplay className="h-3.5 w-3.5 text-[#004A8F]" />
                {t("mod4.tab")}
                <span className="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-bold text-[#1FA84F]">
                  CAMES
                </span>
                {renderModuleBadge("module4")}
              </button>
            </div>
          </div>
        )}
      </header>

      {/* Always-Visible Floating Viewport Error / Warning Banner (no scrolling needed) */}
      {error && (() => {
        const formatted = formatErrorForRole(error, isAdmin, lang);
        const isWarn = formatted.severity === "warning";
        return (
          <div
            role="alert"
            aria-live="assertive"
            className={`fixed left-1/2 top-4 z-50 flex w-[min(94vw,720px)] -translate-x-1/2 items-start justify-between gap-3 rounded-xl border px-4 py-3.5 text-sm shadow-xl backdrop-blur ${
              isWarn
                ? "border-amber-300 bg-amber-50/95 text-amber-950"
                : "border-rose-300 bg-rose-50/95 text-rose-950"
            }`}
          >
            <div className="flex min-w-0 items-start gap-2.5">
              {isWarn ? (
                <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
              ) : (
                <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" />
              )}
              <div className="min-w-0">
                {isAdmin && (
                  <div className="mb-0.5 text-[11px] font-bold uppercase tracking-wide text-rose-700">
                    {lang === "en" ? "Admin · Real Error Details" : "Admin · Erreur technique réelle"}
                  </div>
                )}
                <p className={`break-words leading-snug ${isAdmin ? "font-mono text-xs" : "font-medium"}`}>
                  {formatted.displayMessage}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setError(null)}
              className="shrink-0 rounded-md p-1 text-current/70 hover:bg-black/5 hover:text-current"
              aria-label={t("nav.close")}
              title={t("nav.close")}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        );
      })()}

      {/* Real-Time Admin Incident Toast (shows student & system errors live as they occur) */}
      {isAdmin && liveAdminToast && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-5 right-5 z-50 w-[min(92vw,440px)] rounded-xl border border-rose-300 bg-slate-900 p-4 text-xs text-white shadow-2xl"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-2 font-bold text-rose-300">
              <Bell className="h-4 w-4 shrink-0" />
              <span>
                {lang === "en" ? "Live User Error Alert" : "Alerte erreur en direct"}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setLiveAdminToast(null)}
              className="rounded p-0.5 text-slate-400 hover:bg-white/10 hover:text-white"
              aria-label={t("nav.close")}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="mt-1.5 text-[11px] text-slate-300">
            <strong className="text-white">
              {liveAdminToast.user_name || liveAdminToast.user_email}
            </strong>
            {liveAdminToast.module ? ` · ${liveAdminToast.module}` : ""}
            {liveAdminToast.context ? ` (${liveAdminToast.context})` : ""}
          </div>
          <p className="mt-2 max-h-28 overflow-y-auto break-words rounded-lg bg-black/40 p-2 font-mono text-[11px] text-rose-200">
            {sanitizeTechnicalMessage(liveAdminToast.error_message)}
          </p>
          <div className="mt-2.5 flex items-center justify-end gap-2">
            <button
              type="button"
              disabled={resolvingAlertId === liveAdminToast.id}
              onClick={() => handleResolveAdminAlert(liveAdminToast.id)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-[11px] font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-60"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              {resolvingAlertId === liveAdminToast.id
                ? lang === "en"
                  ? "Notifying..."
                  : "Notification..."
                : lang === "en"
                ? "Mark Fixed & Notify User"
                : "Marquer résolu & notifier"}
            </button>
          </div>
        </div>
      )}

      {/* Affected User "Issue Fixed — You Can Retry" Dismissible Notification */}
      {resolvedNotifications.length > 0 && !(isAdmin && liveAdminToast) && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-5 right-5 z-50 flex w-[min(92vw,420px)] flex-col gap-2.5"
        >
          {resolvedNotifications.slice(0, 2).map((notif) => (
            <div
              key={notif.id}
              className="rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-xs text-emerald-950 shadow-2xl"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 font-bold text-emerald-800">
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                  <span>
                    {lang === "en"
                      ? "Issue Fixed — You Can Retry"
                      : "Incident résolu — Vous pouvez réessayer"}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handleDismissResolvedNotification(notif.id)}
                  className="rounded p-0.5 text-emerald-700/70 hover:bg-emerald-100 hover:text-emerald-900"
                  aria-label={t("nav.close")}
                  title={t("nav.close")}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <p className="mt-1.5 leading-relaxed text-emerald-900">
                {lang === "en" ? notif.message_en : notif.message_fr}
              </p>
              {notif.note && (
                <p className="mt-1.5 rounded-lg bg-white/80 px-2.5 py-1.5 text-[11px] font-medium text-emerald-900 ring-1 ring-emerald-200">
                  {notif.note}
                </p>
              )}
              <div className="mt-2.5 flex items-center justify-end">
                <button
                  type="button"
                  onClick={() => handleDismissResolvedNotification(notif.id)}
                  className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1 text-[11px] font-semibold text-white transition hover:bg-emerald-700"
                >
                  {lang === "en" ? "Got it, dismiss" : "Compris, fermer"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Main Content */}
      <main className="mx-auto max-w-7xl px-3.5 py-4 pb-24 sm:px-6 sm:py-6 md:pb-8">

        {activeView === "admin" && isAdmin ? (
          <AdminPortal
            currentAdminId={currentUser.id}
            onExitAdmin={exitAdminPortal}
            onOpenUserWorkspace={handleOpenUserWorkspace}
          />
        ) : (
          <>
            {isAdmin &&
              activeProject &&
              activeProject.owner_user_id &&
              activeProject.owner_user_id !== currentUser.id && (
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-300 bg-rose-50 px-4 py-3 text-xs text-rose-950">
                  <div className="flex items-start gap-2.5">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-700" />
                    <div>
                      <span className="font-bold">
                        {lang === "en"
                          ? "Admin Support Mode — Student Workspace:"
                          : "Mode assistance Admin — Espace de travail de :"}
                      </span>{" "}
                      <strong>
                        {activeProject.cover?.author_name ||
                          activeProject.locked_author_name ||
                          activeProject.owner_user_id}
                      </strong>
                      {" · "}
                      {lang === "en"
                        ? "You can assist on sections/exports; document import remains blocked to protect their work."
                        : "Vous pouvez assister l'étudiant sur ses sections et exports ; l'importation de document reste bloquée par sécurité."}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setActiveProject(projects[0] ?? null)}
                      className="rounded-lg border border-rose-300 bg-white px-2.5 py-1 text-[11px] font-semibold text-rose-900 hover:bg-rose-100"
                    >
                      {lang === "en" ? "Back to my workspace" : "Retour à mon espace"}
                    </button>
                    <button
                      type="button"
                      onClick={openAdminPortal}
                      className="rounded-lg bg-rose-700 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-rose-800"
                    >
                      {lang === "en" ? "Admin Portal" : "Portail Admin"}
                    </button>
                  </div>
                </div>
              )}

            {isModuleReadOnly && activeProject && (
              <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-950">
                <Lock className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
                <div>
                  <span className="font-bold">{t("nav.read_only_title")}</span>{" "}
                  {t("nav.read_only_body")}
                </div>
              </div>
            )}

            {loading ? (
              <div className="py-20 text-center text-sm text-slate-500">
                {t("nav.loading_canvas")}
              </div>
            ) : !activeProject || (showCreateModal && canCreateProject) ? (
              <div className="mx-auto max-w-2xl rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-bold text-slate-900">
                    {t("create.title")}
                  </h2>
                  {activeProject && (
                    <button
                      type="button"
                      onClick={() => setShowCreateModal(false)}
                      className="text-xs font-medium text-slate-500 hover:text-slate-800"
                    >
                      {t("nav.cancel")}
                    </button>
                  )}
                </div>
                <p className="mt-1 text-sm text-slate-600">
                  {t("create.subtitle", { count: canvas.length })}
                </p>

                {!isAdmin && (
                  <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-900">
                    <Lock className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
                    <span>{t("create.lock_notice")}</span>
                  </div>
                )}

                <form onSubmit={handleCreateProject} className="mt-5 space-y-4">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <button
                      type="button"
                      onClick={() => setNewMode("new_topic")}
                      className={`rounded-xl border p-4 text-left transition ${
                        newMode === "new_topic"
                          ? "border-indigo-500 bg-indigo-50/70 ring-1 ring-indigo-500"
                          : "border-slate-200 hover:bg-slate-50"
                      }`}
                    >
                      <Lightbulb className="h-5 w-5 text-indigo-600" />
                      <div className="mt-2 text-sm font-semibold text-slate-900">
                        {t("create.mode_a_title")}
                      </div>
                      <div className="mt-1 text-xs text-slate-600">
                        {t("create.mode_a_desc")}
                      </div>
                    </button>

                    <button
                      type="button"
                      onClick={() => setNewMode("existing_protocol")}
                      className={`rounded-xl border p-4 text-left transition ${
                        newMode === "existing_protocol"
                          ? "border-indigo-500 bg-indigo-50/70 ring-1 ring-indigo-500"
                          : "border-slate-200 hover:bg-slate-50"
                      }`}
                    >
                      <FileUp className="h-5 w-5 text-emerald-600" />
                      <div className="mt-2 text-sm font-semibold text-slate-900">
                        {t("create.mode_b_title")}
                      </div>
                      <div className="mt-1 text-xs text-slate-600">
                        {t("create.mode_b_desc")}
                      </div>
                    </button>
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-slate-700">
                      {t("create.field_title")}
                    </label>
                    <input
                      type="text"
                      required
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder={t("create.field_title_placeholder")}
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                    />
                  </div>

                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div>
                      <label className="block text-xs font-semibold text-slate-700">
                        {t("create.field_study_type")}
                      </label>
                      <input
                        type="text"
                        value={newStudyType}
                        onChange={(e) => setNewStudyType(e.target.value)}
                        className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-slate-700">
                        {t("create.field_language")}
                      </label>
                      <select
                        value={newLang}
                        onChange={(e) =>
                          setNewLang(e.target.value === "en" ? "en" : "fr")
                        }
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                      >
                        <option value="fr">Français (FR)</option>
                        <option value="en">English (EN)</option>
                      </select>
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-slate-700">
                      {t("create.field_idea")}
                    </label>
                    <textarea
                      rows={3}
                      value={newIdea}
                      onChange={(e) => setNewIdea(e.target.value)}
                      placeholder={t("create.field_idea_placeholder")}
                      className="mt-1 w-full rounded-lg border border-slate-300 p-3 text-sm focus:border-indigo-500 focus:outline-none"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={saving}
                    className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {saving ? t("create.submitting") : t("create.submit")}
                  </button>
                </form>
              </div>
            ) : (
              <>
                <Module1Assistant
                  project={activeProject}
                  canvas={canvas}
                  selectedSectionId={selectedSectionId}
                  onSelectSection={setSelectedSectionId}
                  onProjectUpdated={(updated) => {
                    setActiveProject(updated);
                    setProjects((prev) =>
                      prev.map((p) => (p.project_id === updated.project_id ? updated : p))
                    );
                    refreshMe();
                  }}
                  onSaveSection={handleSaveSection}
                  onSaveCover={handleSaveCover}
                  onOpenModule2={() => setActiveModule("module2")}
                  onError={(msg) => setError(msg || null)}
                  saving={saving}
                  isAdmin={isAdmin}
                  isVisible={activeModule === "module1"}
                />
                {activeModule === "module2" && (
                  <Module2Editor
                    project={activeProject}
                    canvas={canvas}
                    onProjectUpdated={(updated) => {
                      setActiveProject(updated);
                      setProjects((prev) =>
                        prev.map((p) => (p.project_id === updated.project_id ? updated : p))
                      );
                      refreshMe();
                    }}
                    onSaveFormatting={handleSaveFormatting}
                    onError={(msg) => setError(msg || null)}
                    saving={saving}
                  />
                )}
                {activeModule === "module3" && (
                  <Module3DataEntry
                    project={activeProject}
                    onProjectUpdated={(updated) => {
                      setActiveProject(updated);
                      setProjects((prev) =>
                        prev.map((p) => (p.project_id === updated.project_id ? updated : p))
                      );
                      refreshMe();
                    }}
                    onOpenModule2={() => setActiveModule("module2")}
                    onError={(msg) => setError(msg || null)}
                  />
                )}
                {activeModule === "module4" && (
                  <Module4Presentation
                    project={activeProject}
                    onError={(msg) => setError(msg || null)}
                  />
                )}
              </>
            )}
          </>
        )}
      </main>

      {currentUser && activeProject && activeView === "studio" && (
        <AssistantSmartResearchWidget
          project={activeProject}
          activeModule={activeModule}
          selectedSectionId={selectedSectionId}
          sectionTitle={
            canvas.find((c) => c.id === selectedSectionId)?.title || selectedSectionId
          }
          lang={lang}
          onProjectUpdate={(updated) => {
            setActiveProject(updated);
            setProjects((prev) =>
              prev.map((p) => (p.project_id === updated.project_id ? updated : p))
            );
          }}
          onNavigateModule={(modKey) => setActiveModule(modKey)}
        />
      )}

      {/* Fixed Mobile Bottom Navigation Bar (< md) */}
      {(activeProject || isAdmin) && (
        <nav
          aria-label="Navigation mobile"
          className={`fixed inset-x-0 bottom-0 z-30 grid border-t border-slate-200 bg-white/95 backdrop-blur safe-bottom md:hidden ${
            isAdmin ? "grid-cols-5" : "grid-cols-4"
          }`}
        >
          <button
            type="button"
            disabled={!activeProject}
            onClick={() => selectMobileModule("module1")}
            className={`relative flex min-h-[58px] flex-col items-center justify-center gap-0.5 px-1 text-[11px] font-semibold transition ${
              activeView === "studio" && activeModule === "module1"
                ? "text-[#004A8F]"
                : "text-slate-600"
            } disabled:opacity-40`}
          >
            {!isAdmin && (
              <span
                className={`absolute right-3 top-2 h-2 w-2 rounded-full ${moduleStatusDotClass("module1")}`}
              />
            )}
            <Layers className="h-4 w-4" />
            <span>{t("mod1.mobile")}</span>
          </button>

          <button
            type="button"
            disabled={!activeProject}
            onClick={() => selectMobileModule("module2")}
            className={`relative flex min-h-[58px] flex-col items-center justify-center gap-0.5 px-1 text-[11px] font-semibold transition ${
              activeView === "studio" && activeModule === "module2"
                ? "text-[#1FA84F]"
                : "text-slate-600"
            } disabled:opacity-40`}
          >
            {!isAdmin && (
              <span
                className={`absolute right-3 top-2 h-2 w-2 rounded-full ${moduleStatusDotClass("module2")}`}
              />
            )}
            <PenSquare className="h-4 w-4" />
            <span>{t("mod2.mobile")}</span>
          </button>

          <button
            type="button"
            disabled={!activeProject}
            onClick={() => selectMobileModule("module3")}
            className={`relative flex min-h-[58px] flex-col items-center justify-center gap-0.5 px-1 text-[11px] font-semibold transition ${
              activeView === "studio" && activeModule === "module3"
                ? "text-[#0284C7]"
                : "text-slate-600"
            } disabled:opacity-40`}
          >
            {!isAdmin && (
              <span
                className={`absolute right-3 top-2 h-2 w-2 rounded-full ${moduleStatusDotClass("module3")}`}
              />
            )}
            <ClipboardCheck className="h-4 w-4" />
            <span>
              {t("mod3.mobile")}
              {activeProject ? ` (${activeProject.patient_records?.length ?? 0})` : ""}
            </span>
          </button>

          <button
            type="button"
            disabled={!activeProject}
            onClick={() => selectMobileModule("module4")}
            className={`relative flex min-h-[58px] flex-col items-center justify-center gap-0.5 px-1 text-[11px] font-semibold transition ${
              activeView === "studio" && activeModule === "module4"
                ? "text-[#004A8F]"
                : "text-slate-600"
            } disabled:opacity-40`}
          >
            {!isAdmin && (
              <span
                className={`absolute right-3 top-2 h-2 w-2 rounded-full ${moduleStatusDotClass("module4")}`}
              />
            )}
            <Airplay className="h-4 w-4" />
            <span>{t("mod4.mobile")}</span>
          </button>

          {isAdmin && (
            <button
              type="button"
              onClick={() => {
                setMobileMenuOpen(false);
                openAdminPortal();
              }}
              className={`flex min-h-[58px] flex-col items-center justify-center gap-0.5 px-1 text-[11px] font-semibold transition ${
                activeView === "admin" ? "text-[#004A8F]" : "text-slate-600"
              }`}
            >
              <Shield className="h-4 w-4" />
              <span>Admin</span>
            </button>
          )}
        </nav>
      )}

      {showPasswordModal && (
        <ChangePasswordModal
          userEmail={currentUser.email}
          mandatory={false}
          onSuccess={(res) => {
            setCurrentUser(res.user);
            setShowPasswordModal(false);
          }}
          onCancel={() => setShowPasswordModal(false)}
        />
      )}
    </div>
  );
}
