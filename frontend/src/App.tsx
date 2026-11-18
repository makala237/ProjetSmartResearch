import { useEffect, useState } from "react";
import {
  Activity,
  Airplay,
  ClipboardCheck,
  FileUp,
  FolderPlus,
  Layers,
  Lightbulb,
  PenSquare,
} from "lucide-react";
import { api } from "./api/client";
import { Module1Assistant } from "./components/Module1Assistant";
import { Module2Editor } from "./components/Module2Editor";
import { Module3DataEntry } from "./components/Module3DataEntry";
import { Module4Presentation } from "./components/Module4Presentation";
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
  const [canvas, setCanvas] = useState<CanvasSectionSpec[]>([]);
  const [projects, setProjects] = useState<ProtocolProject[]>([]);
  const [activeProject, setActiveProject] = useState<ProtocolProject | null>(null);
  const [modelsStatus, setModelsStatus] = useState<ModelsStatus | null>(null);

  const [activeModule, setActiveModule] = useState<"module1" | "module2" | "module3" | "module4">("module1");
  const [selectedSectionId, setSelectedSectionId] = useState<string>("cover_page");
  const [showCreateModal, setShowCreateModal] = useState(false);

  const [newName, setNewName] = useState("");
  const [newMode, setNewMode] = useState<EntryMode>("new_topic");
  const [newIdea, setNewIdea] = useState("");
  const [newStudyType, setNewStudyType] = useState("Étude transversale analytique");

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.getCanvas(), api.listProjects(), api.getModelsStatus()])
      .then(([canvasRes, projectsRes, modelsRes]) => {
        setCanvas(canvasRes.sections);
        setProjects(projectsRes);
        setActiveProject(projectsRes[0] ?? null);
        setModelsStatus(modelsRes);
        if (canvasRes.sections.length > 0) {
          setSelectedSectionId(canvasRes.sections[0].id);
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

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
      const updated = await api.updateSection(
        activeProject.project_id,
        sectionId,
        {
          content_markdown: content,
          content_html: contentHtml,
          status,
          user_notes: userNotes,
        }
      );
      setActiveProject(updated);
      setProjects((prev) =>
        prev.map((p) => (p.project_id === updated.project_id ? updated : p))
      );
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
      const updated = await api.updateFormatting(
        activeProject.project_id,
        formatting
      );
      setActiveProject(updated);
      setProjects((prev) =>
        prev.map((p) => (p.project_id === updated.project_id ? updated : p))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Top Navbar */}
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#6C2826] text-sm font-bold text-white">
              SR
            </div>
            <div>
              <h1 className="text-base font-bold text-slate-900">
                SmartResearch <span className="text-[#6C2826]">FMSB</span>
              </h1>
              <p className="text-xs text-slate-500">
                Assistant de rédaction de protocole médical
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {/* Gemini Status Pill */}
            <div
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${
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
                ? `Gemini API OK (${modelsStatus.available_models.length} modèles)`
                : "Gemini API hors ligne"}
            </div>

            {/* Project Selector */}
            {projects.length > 0 && (
              <select
                value={activeProject?.project_id ?? ""}
                onChange={(e) => {
                  const found = projects.find(
                    (p) => p.project_id === e.target.value
                  );
                  if (found) setActiveProject(found);
                }}
                className="max-w-[220px] rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700"
              >
                {projects.map((p) => (
                  <option key={p.project_id} value={p.project_id}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}

            <button
              type="button"
              onClick={() => setShowCreateModal(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
            >
              <FolderPlus className="h-3.5 w-3.5" />
              Nouveau protocole
            </button>
          </div>
        </div>

        {/* Three-Module Switcher */}
        {activeProject && (
          <div className="border-t border-slate-100 bg-slate-50/80">
            <div className="mx-auto flex max-w-7xl flex-wrap gap-2 px-4 py-2 sm:px-6">
              <button
                type="button"
                onClick={() => setActiveModule("module1")}
                className={`inline-flex items-center gap-2 rounded-lg px-4 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module1"
                    ? "bg-white text-indigo-700 shadow-sm ring-1 ring-slate-200"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <Layers className="h-3.5 w-3.5" />
                Module 1 : Recherche, Rédaction & Validation étape par étape
              </button>
              <button
                type="button"
                onClick={() => setActiveModule("module2")}
                className={`inline-flex items-center gap-2 rounded-lg px-4 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module2"
                    ? "bg-white text-emerald-700 shadow-sm ring-1 ring-slate-200"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <PenSquare className="h-3.5 w-3.5" />
                Module 2 : Studio d'édition & Export Word / PDF
              </button>
              <button
                type="button"
                onClick={() => setActiveModule("module3")}
                className={`inline-flex items-center gap-2 rounded-lg px-4 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module3"
                    ? "bg-white text-[#6C2826] shadow-sm ring-1 ring-[#6C2826]/30"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <ClipboardCheck className="h-3.5 w-3.5" />
                Module 3 : Masque de Saisie & Patients
                <span className="rounded-full bg-[#6C2826]/10 px-1.5 py-0.5 text-[10px] font-bold text-[#6C2826]">
                  {activeProject.patient_records?.length ?? 0}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setActiveModule("module4")}
                className={`inline-flex items-center gap-2 rounded-lg px-4 py-1.5 text-xs font-semibold transition ${
                  activeModule === "module4"
                    ? "bg-white text-[#6C2826] shadow-sm ring-1 ring-[#6C2826]/30"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <Airplay className="h-3.5 w-3.5 text-[#6C2826]" />
                Module 4 : Diaporama & Soutenance (PowerPoint)
                <span className="rounded-full bg-[#6C2826]/10 px-1.5 py-0.5 text-[10px] font-bold text-[#6C2826]">
                  CAMES
                </span>
              </button>
            </div>
          </div>
        )}
      </header>

      {/* Main Content */}
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {error && (
          <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
            {error}
          </div>
        )}

        {loading ? (
          <div className="py-20 text-center text-sm text-slate-500">
            Chargement du canevas FMSB...
          </div>
        ) : !activeProject || showCreateModal ? (
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
                prev.map((p) =>
                  p.project_id === updated.project_id ? updated : p
                )
              );
            }}
            onSaveSection={handleSaveSection}
            onSaveCover={handleSaveCover}
            onError={(msg) => setError(msg || null)}
            saving={saving}
          />
        ) : activeModule === "module2" ? (
          <Module2Editor
            project={activeProject}
            canvas={canvas}
            onProjectUpdated={(updated) => {
              setActiveProject(updated);
              setProjects((prev) =>
                prev.map((p) =>
                  p.project_id === updated.project_id ? updated : p
                )
              );
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
                prev.map((p) =>
                  p.project_id === updated.project_id ? updated : p
                )
              );
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
      </main>
    </div>
  );
}
