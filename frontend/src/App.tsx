import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Airplay,
  ClipboardCheck,
  FileUp,
  FolderPlus,
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
import { ChangePasswordModal } from "./components/ChangePasswordModal";
import { LoginScreen } from "./components/LoginScreen";
import { Module1Assistant } from "./components/Module1Assistant";
import { Module2Editor } from "./components/Module2Editor";
import { Module3DataEntry } from "./components/Module3DataEntry";
import { Module4Presentation } from "./components/Module4Presentation";
import type { ModuleKey, TokenResponse, UserMeOut } from "./types/auth";
import type {
  CanvasSectionSpec,
  CoverMetadata,
  EntryMode,
  FormattingRules,
  ModelsStatus,
  ProtocolProject,
  StepStatus,
} from "./types/protocol";

export default function App() {
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
  const [modelsStatus, setModelsStatus] = useState<ModelsStatus | null>(null);

  const [activeModule, setActiveModule] = useState<ModuleKey>("module1");
  const [selectedSectionId, setSelectedSectionId] = useState<string>("cover_page");
  const [showCreateModal, setShowCreateModal] = useState(false);

  const [newName, setNewName] = useState("");
  const [newMode, setNewMode] = useState<EntryMode>("new_topic");
  const [newIdea, setNewIdea] = useState("");
  const [newStudyType, setNewStudyType] = useState("Étude transversale analytique");

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadWorkspaceData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [canvasRes, projectsRes, modelsRes] = await Promise.all([
        api.getCanvas(),
        api.listProjects(),
        api.getModelsStatus(),
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
      setModelsStatus(modelsRes);
      if (canvasRes.sections.length > 0) {
        setSelectedSectionId((prev) => prev || canvasRes.sections[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

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
  }, [currentUser?.id, currentUser?.must_change_password, loadWorkspaceData]);

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
      setShowCreateModal(false);
      setShowPasswordModal(false);
      setActiveView("studio");
      setAuthNotice(null);
      if (window.location.pathname !== "/") {
        window.history.pushState({}, "", "/");
      }
    }
  }

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
      });
      setProjects([created, ...projects]);
      setActiveProject(created);
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
  ) {
    if (!activeProject) return;
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
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
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
          {ent.days_remaining} j
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-0.5 rounded-full bg-rose-100 px-1.5 py-0.5 text-[10px] font-bold text-rose-800">
        <Lock className="h-2.5 w-2.5" /> Lecture seule
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
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-600">
        Vérification de votre session SmartResearch FMSB...
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
    <div className="min-h-screen bg-slate-50">
      {/* Top Navbar */}
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-3.5 py-2.5 sm:px-6 sm:py-3">
          <div className="flex min-w-0 items-center gap-2.5 sm:gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#6C2826] text-sm font-bold text-white">
              SR
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-bold text-slate-900 sm:text-base">
                SmartResearch <span className="text-[#6C2826]">FMSB</span>
              </h1>
              <p className="hidden text-xs text-slate-500 sm:block">
                Assistant de rédaction de protocole médical
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
              {isAdmin ? "Admin" : `${currentUser.ai_quota.credits_remaining} cr`}
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
          <div className="hidden flex-wrap items-center gap-2.5 md:flex">
            {/* Gemini Status Pill */}
            <div
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                modelsStatus?.connected
                  ? "bg-emerald-50 text-emerald-700"
                  : "bg-amber-50 text-amber-800"
              }`}
              title={
                modelsStatus?.connected
                  ? `${modelsStatus.available_models.length} modèles Gemini détectés`
                  : modelsStatus?.error || "Vérification en cours"
              }
            >
              <Activity className="h-3.5 w-3.5" />
              {modelsStatus?.connected
                ? `Gemini OK (${modelsStatus.available_models.length})`
                : "Gemini hors ligne"}
            </div>

            {/* AI Credits Gauge */}
            <div
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${
                isAdmin
                  ? "bg-indigo-50 text-indigo-800"
                  : currentUser.ai_quota.credits_remaining <= 5
                  ? "bg-amber-100 text-amber-900"
                  : "bg-indigo-50 text-indigo-800"
              }`}
              title="Solde de crédits d'appels IA Gemini"
            >
              <Sparkles className="h-3.5 w-3.5" />
              {isAdmin
                ? "IA : Illimité"
                : `IA : ${currentUser.ai_quota.credits_remaining} / ${currentUser.ai_quota.credits_total} cr`}
            </div>

            {/* Project Selector (Studio mode) */}
            {activeView === "studio" && projects.length > 0 && (
              <select
                value={activeProject?.project_id ?? ""}
                onChange={(e) => {
                  const found = projects.find((p) => p.project_id === e.target.value);
                  if (found) setActiveProject(found);
                }}
                className="max-w-[200px] rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700"
              >
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
                onClick={() => setShowCreateModal(true)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
              >
                <FolderPlus className="h-3.5 w-3.5" />
                Nouveau protocole
              </button>
            )}

            {/* Admin Portal Switch */}
            {isAdmin && (
              <button
                type="button"
                onClick={activeView === "admin" ? exitAdminPortal : openAdminPortal}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                  activeView === "admin"
                    ? "bg-slate-900 text-white"
                    : "border border-[#6C2826]/30 bg-rose-50 text-[#6C2826] hover:bg-rose-100"
                }`}
              >
                <Shield className="h-3.5 w-3.5" />
                {activeView === "admin" ? "Studio" : "Portail Admin"}
              </button>
            )}

            {/* User Badge & Account Actions */}
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs">
              <span className="max-w-[140px] truncate font-semibold text-slate-800">
                {currentUser.full_name}
              </span>
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                  isAdmin ? "bg-indigo-100 text-indigo-800" : "bg-slate-200 text-slate-700"
                }`}
              >
                {isAdmin ? "Admin" : "Étudiant"}
              </span>
            </div>

            <button
              type="button"
              onClick={() => setShowPasswordModal(true)}
              className="rounded-lg border border-slate-300 bg-white p-1.5 text-slate-600 hover:bg-slate-50 hover:text-slate-900"
              title="Changer mon mot de passe"
            >
              <KeyRound className="h-3.5 w-3.5" />
            </button>

            <button
              type="button"
              onClick={handleLogout}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              title="Déconnexion"
            >
              <LogOut className="h-3.5 w-3.5" />
              <span>Déconnexion</span>
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
                {isAdmin ? "Admin" : "Étudiant"}
              </span>
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
                        {ent?.is_active ? `${ent.days_remaining} j` : "Expiré"}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}

            {activeView === "studio" && projects.length > 0 && (
              <div>
                <label className="block text-[11px] font-semibold text-slate-600">
                  Protocole actif
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
                    setShowCreateModal(true);
                    setMobileMenuOpen(false);
                  }}
                  className="col-span-2 inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-3 text-xs font-semibold text-white"
                >
                  <FolderPlus className="h-4 w-4" />
                  Nouveau protocole
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
                  className="col-span-2 inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-lg bg-[#6C2826] px-3 text-xs font-semibold text-white"
                >
                  <Shield className="h-4 w-4" />
                  {activeView === "admin" ? "Ouvrir le Studio" : "Ouvrir Portail Admin"}
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
                Mot de passe
              </button>
              <button
                type="button"
                onClick={handleLogout}
                className="inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 text-xs font-semibold text-rose-700"
              >
                <LogOut className="h-3.5 w-3.5" />
                Déconnexion
              </button>
            </div>
          </div>
        )}

        {/* Four-Module Switcher (Desktop md+) */}
        {activeView === "studio" && activeProject && (
          <div className="hidden border-t border-slate-100 bg-slate-50/80 md:block">
            <div className="mx-auto flex max-w-7xl flex-wrap gap-2 px-4 py-2 sm:px-6">
              <button
                type="button"
                onClick={() => setActiveModule("module1")}
                className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module1"
                    ? "bg-white text-indigo-700 shadow-sm ring-1 ring-slate-200"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <Layers className="h-3.5 w-3.5" />
                Module 1 : Rédaction &amp; Recherche
                {renderModuleBadge("module1")}
              </button>
              <button
                type="button"
                onClick={() => setActiveModule("module2")}
                className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module2"
                    ? "bg-white text-emerald-700 shadow-sm ring-1 ring-slate-200"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <PenSquare className="h-3.5 w-3.5" />
                Module 2 : Édition Word / PDF
                {renderModuleBadge("module2")}
              </button>
              <button
                type="button"
                onClick={() => setActiveModule("module3")}
                className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module3"
                    ? "bg-white text-[#6C2826] shadow-sm ring-1 ring-[#6C2826]/30"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <ClipboardCheck className="h-3.5 w-3.5" />
                Module 3 : Masque &amp; Patients
                <span className="rounded-full bg-[#6C2826]/10 px-1.5 py-0.5 text-[10px] font-bold text-[#6C2826]">
                  {activeProject.patient_records?.length ?? 0}
                </span>
                {renderModuleBadge("module3")}
              </button>
              <button
                type="button"
                onClick={() => setActiveModule("module4")}
                className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module4"
                    ? "bg-white text-[#6C2826] shadow-sm ring-1 ring-[#6C2826]/30"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <Airplay className="h-3.5 w-3.5 text-[#6C2826]" />
                Module 4 : Diaporama PPTX
                <span className="rounded-full bg-[#6C2826]/10 px-1.5 py-0.5 text-[10px] font-bold text-[#6C2826]">
                  CAMES
                </span>
                {renderModuleBadge("module4")}
              </button>
            </div>
          </div>
        )}
      </header>

      {/* Main Content */}
      <main className="mx-auto max-w-7xl px-3.5 py-4 pb-24 sm:px-6 sm:py-6 md:pb-8">
        {error && (
          <div className="mb-4 flex items-center justify-between rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
            <span>{error}</span>
            <button
              type="button"
              onClick={() => setError(null)}
              className="ml-4 text-xs font-semibold underline"
            >
              Fermer
            </button>
          </div>
        )}

        {activeView === "admin" && isAdmin ? (
          <AdminPortal currentAdminId={currentUser.id} onExitAdmin={exitAdminPortal} />
        ) : (
          <>
            {isModuleReadOnly && activeProject && (
              <div className="mb-4 flex items-start gap-2.5 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-950">
                <Lock className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
                <div>
                  <span className="font-bold">Mode lecture seule sur ce module :</span> votre
                  licence d'édition pour ce module a expiré (ou n'est pas incluse dans votre offre
                  actuelle). La consultation et le téléchargement de vos fichiers Word, PDF, CSV et
                  PowerPoint restent autorisés ; contactez l'administrateur pour renouveler l'accès en écriture ou les appels IA.
                </div>
              </div>
            )}

            {loading ? (
              <div className="py-20 text-center text-sm text-slate-500">
                Chargement du canevas FMSB...
              </div>
            ) : !activeProject || (showCreateModal && canCreateProject) ? (
              <div className="mx-auto max-w-2xl rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-bold text-slate-900">
                    Démarrer un protocole de recherche FMSB
                  </h2>
                  {activeProject && (
                    <button
                      type="button"
                      onClick={() => setShowCreateModal(false)}
                      className="text-xs font-medium text-slate-500 hover:text-slate-800"
                    >
                      Annuler
                    </button>
                  )}
                </div>
                <p className="mt-1 text-sm text-slate-600">
                  Choisissez votre point de départ. Le protocole suivra strictement le
                  canevas officiel en {canvas.length} étapes.
                </p>

                {!isAdmin && (
                  <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-900">
                    <Lock className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
                    <span>
                      <strong>Règle « 1 utilisateur = 1 protocole » :</strong> ce compte est limité à un seul protocole. Le titre et votre identité seront immédiatement verrouillés dès la création.
                    </span>
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
                        Mode A : Nouveau thème / Idée
                      </div>
                      <div className="mt-1 text-xs text-slate-600">
                        Recherche bibliographique guidée puis rédaction section par section.
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
                        Mode B : Analyser un brouillon
                      </div>
                      <div className="mt-1 text-xs text-slate-600">
                        Importez un document existant pour vérifier la conformité FMSB.
                      </div>
                    </button>
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-slate-700">
                      Titre provisoire ou thème du mémoire / thèse *
                    </label>
                    <input
                      type="text"
                      required
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder="Ex. Facteurs associés à l'hypertension artérielle..."
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-slate-700">
                      Type d'étude pressenti
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
                      Description de l'idée de recherche / Contexte clinique
                    </label>
                    <textarea
                      rows={3}
                      value={newIdea}
                      onChange={(e) => setNewIdea(e.target.value)}
                      placeholder="Précisez la population, le lieu (ex. Yaoundé), l'exposition et le critère de jugement..."
                      className="mt-1 w-full rounded-lg border border-slate-300 p-3 text-sm focus:border-indigo-500 focus:outline-none"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={saving}
                    className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {saving ? "Création..." : "Créer le projet de protocole"}
                  </button>
                </form>
              </div>
            ) : activeModule === "module1" ? (
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
                onError={(msg) => setError(msg || null)}
                saving={saving}
                isAdmin={isAdmin}
              />
            ) : activeModule === "module2" ? (
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
            ) : activeModule === "module3" ? (
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
            ) : (
              <Module4Presentation
                project={activeProject}
                onError={(msg) => setError(msg || null)}
              />
            )}
          </>
        )}
      </main>

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
                ? "text-indigo-700"
                : "text-slate-600"
            } disabled:opacity-40`}
          >
            {!isAdmin && (
              <span
                className={`absolute right-3 top-2 h-2 w-2 rounded-full ${moduleStatusDotClass("module1")}`}
              />
            )}
            <Layers className="h-4 w-4" />
            <span>M1 Rédac</span>
          </button>

          <button
            type="button"
            disabled={!activeProject}
            onClick={() => selectMobileModule("module2")}
            className={`relative flex min-h-[58px] flex-col items-center justify-center gap-0.5 px-1 text-[11px] font-semibold transition ${
              activeView === "studio" && activeModule === "module2"
                ? "text-emerald-700"
                : "text-slate-600"
            } disabled:opacity-40`}
          >
            {!isAdmin && (
              <span
                className={`absolute right-3 top-2 h-2 w-2 rounded-full ${moduleStatusDotClass("module2")}`}
              />
            )}
            <PenSquare className="h-4 w-4" />
            <span>M2 Word</span>
          </button>

          <button
            type="button"
            disabled={!activeProject}
            onClick={() => selectMobileModule("module3")}
            className={`relative flex min-h-[58px] flex-col items-center justify-center gap-0.5 px-1 text-[11px] font-semibold transition ${
              activeView === "studio" && activeModule === "module3"
                ? "text-[#6C2826]"
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
              M3 Saisie
              {activeProject ? ` (${activeProject.patient_records?.length ?? 0})` : ""}
            </span>
          </button>

          <button
            type="button"
            disabled={!activeProject}
            onClick={() => selectMobileModule("module4")}
            className={`relative flex min-h-[58px] flex-col items-center justify-center gap-0.5 px-1 text-[11px] font-semibold transition ${
              activeView === "studio" && activeModule === "module4"
                ? "text-[#6C2826]"
                : "text-slate-600"
            } disabled:opacity-40`}
          >
            {!isAdmin && (
              <span
                className={`absolute right-3 top-2 h-2 w-2 rounded-full ${moduleStatusDotClass("module4")}`}
              />
            )}
            <Airplay className="h-4 w-4" />
            <span>M4 Slides</span>
          </button>

          {isAdmin && (
            <button
              type="button"
              onClick={() => {
                setMobileMenuOpen(false);
                openAdminPortal();
              }}
              className={`flex min-h-[58px] flex-col items-center justify-center gap-0.5 px-1 text-[11px] font-semibold transition ${
                activeView === "admin" ? "text-[#6C2826]" : "text-slate-600"
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
