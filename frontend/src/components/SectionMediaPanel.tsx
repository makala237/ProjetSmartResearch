import { useMemo, useState } from "react";
import {
  GitBranch,
  ImagePlus,
  Loader2,
  Plus,
  Sparkles,
  Table2,
  Trash2,
  Upload,
} from "lucide-react";
import { api } from "../api/client";
import type {
  DiagramConnection,
  DiagramNode,
  ProtocolProject,
} from "../types/protocol";

interface SectionMediaPanelProps {
  project: ProtocolProject;
  sectionId: string;
  sectionText: string;
  onProjectUpdated: (updated: ProtocolProject) => void;
  onError: (msg: string) => void;
}

export function SectionMediaPanel({
  project,
  sectionId,
  sectionText,
  onProjectUpdated,
  onError,
}: SectionMediaPanelProps) {
  const secState = project.sections[sectionId];
  const customTables = secState?.custom_tables ?? [];
  const customDiagrams = secState?.custom_diagrams ?? [];
  const sectionImages = project.images.filter(
    (img) => img.target_section_id === sectionId
  );

  const effectiveTables = useMemo(() => {
    const activeCustom = customTables.filter(
      (ct) => (ct.headers?.length ?? 0) > 0 || (ct.rows?.length ?? 0) > 0
    );
    if (activeCustom.length > 0) {
      return activeCustom.map((ct) => ({
        table_id: ct.table_id,
        title: ct.title,
        colsCount: ct.headers.length,
        rowsCount: ct.rows.length,
        anchor_hint: ct.anchor_hint,
        isStructured: false,
      }));
    }
    if (secState?.suppress_structured_table) {
      return [];
    }
    if (sectionId === "chap1_variables" && (project.variables?.length ?? 0) > 0) {
      return [
        {
          table_id: "__structured_variables__",
          title: "Liste opérationnelle des variables d'étude",
          colsCount: 5,
          rowsCount: project.variables.length,
          anchor_hint: "",
          isStructured: true,
        },
      ];
    }
    if (
      sectionId === "chap2_etat_connaissances" &&
      (project.literature_synthesis?.length ?? 0) > 0
    ) {
      return [
        {
          table_id: "__structured_synthesis__",
          title: "Synthèse des études antérieures",
          colsCount: 5,
          rowsCount: project.literature_synthesis.length,
          anchor_hint: "",
          isStructured: true,
        },
      ];
    }
    if (
      sectionId === "chap3_chronogramme" &&
      (project.timeline_grid?.rows?.length ?? 0) > 0
    ) {
      return [
        {
          table_id: "__structured_timeline__",
          title: "Chronogramme des activités",
          colsCount: (project.timeline_grid.periods?.length ?? 0) + 1,
          rowsCount: project.timeline_grid.rows.length,
          anchor_hint: "",
          isStructured: true,
        },
      ];
    }
    if (sectionId === "chap3_budget" && (project.budget?.length ?? 0) > 0) {
      return [
        {
          table_id: "__structured_budget__",
          title: "Budget prévisionnel de l'étude",
          colsCount: 4,
          rowsCount: project.budget.length + 1,
          anchor_hint: "",
          isStructured: true,
        },
      ];
    }
    return [];
  }, [
    customTables,
    secState?.suppress_structured_table,
    sectionId,
    project.variables,
    project.literature_synthesis,
    project.timeline_grid,
    project.budget,
  ]);

  // Extract paragraph/heading snippets as anchor options
  const anchorOptions = useMemo(() => {
    const lines = sectionText
      .split(/\n+/)
      .map((l) => l.trim())
      .filter((l) => l.length >= 6);
    return lines.slice(0, 18).map((l) => (l.length > 70 ? l.slice(0, 70) : l));
  }, [sectionText]);

  // --- Table form state ---
  const [showTableForm, setShowTableForm] = useState(false);
  const [tblTitle, setTblTitle] = useState("");
  const [tblGoal, setTblGoal] = useState("");
  const [tblAnchor, setTblAnchor] = useState("");
  const [tblHeaders, setTblHeaders] = useState<string[]>([
    "Paramètre",
    "Description / Valeur",
  ]);
  const [tblRows, setTblRows] = useState<string[][]>([["", ""]]);
  const [busySuggestTable, setBusySuggestTable] = useState(false);
  const [busySaveTable, setBusySaveTable] = useState(false);

  // --- Figure form state ---
  const [showFigureForm, setShowFigureForm] = useState(false);
  const [figFile, setFigFile] = useState<File | null>(null);
  const [figTitle, setFigTitle] = useState("");
  const [figCaption, setFigCaption] = useState("");
  const [figAnchor, setFigAnchor] = useState("");
  const [busyUploadFig, setBusyUploadFig] = useState(false);

  // --- Diagram form state ---
  const [showDiagramForm, setShowDiagramForm] = useState(false);
  const [dgTitle, setDgTitle] = useState("");
  const [dgGoal, setDgGoal] = useState("");
  const [dgAnchor, setDgAnchor] = useState("");
  const [dgLayout, setDgLayout] = useState<"vertical" | "horizontal">("vertical");
  const [dgNodes, setDgNodes] = useState<DiagramNode[]>([
    { id: "n1", shape: "rounded", title: "1. Variables indépendantes", items: ["Facteur A", "Facteur B"] },
    { id: "n2", shape: "rounded", title: "2. Critère de jugement", items: ["Résultat principal"] },
  ]);
  const [dgConns, setDgConns] = useState<DiagramConnection[]>([
    { from_id: "n1", to_id: "n2", label: "" },
  ]);
  const [busySuggestDg, setBusySuggestDg] = useState(false);
  const [busySaveDg, setBusySaveDg] = useState(false);

  async function handleSuggestDiagram() {
    if (!dgTitle.trim()) {
      onError("Indiquez d'abord un titre pour le schéma (ex: Modèle conceptuel).");
      return;
    }
    setBusySuggestDg(true);
    onError("");
    try {
      const res = await api.suggestSectionDiagram(project.project_id, sectionId, {
        title: dgTitle.trim(),
        goal: dgGoal.trim(),
        anchor_hint: dgAnchor,
      });
      const sug = res.suggestion;
      if (sug.title) setDgTitle(sug.title);
      if (sug.layout) setDgLayout(sug.layout);
      if (sug.nodes?.length) setDgNodes(sug.nodes);
      if (sug.connections) setDgConns(sug.connections);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusySuggestDg(false);
    }
  }

  async function handleSaveDiagram() {
    if (!dgTitle.trim() || dgNodes.length === 0) {
      onError("Ajoutez un titre et au moins un bloc au schéma.");
      return;
    }
    setBusySaveDg(true);
    onError("");
    try {
      const updated = await api.addSectionDiagram(project.project_id, sectionId, {
        title: dgTitle.trim(),
        caption: "",
        anchor_hint: dgAnchor.trim(),
        layout: dgLayout,
        nodes: dgNodes,
        connections: dgConns,
      });
      onProjectUpdated(updated);
      setDgTitle("");
      setDgGoal("");
      setDgAnchor("");
      setShowDiagramForm(false);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusySaveDg(false);
    }
  }

  async function handleSuggestTable() {
    if (!tblTitle.trim()) {
      onError("Indiquez d'abord un titre de tableau pour que l'IA puisse le proposer.");
      return;
    }
    setBusySuggestTable(true);
    onError("");
    try {
      const res = await api.suggestSectionTable(project.project_id, sectionId, {
        title: tblTitle.trim(),
        goal: tblGoal.trim(),
        anchor_hint: tblAnchor,
      });
      const sug = res.suggestion;
      if (sug.title) setTblTitle(sug.title);
      if (sug.headers?.length) setTblHeaders(sug.headers);
      if (sug.rows?.length) setTblRows(sug.rows);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusySuggestTable(false);
    }
  }

  async function handleSaveTable() {
    if (!tblTitle.trim()) {
      onError("Veuillez saisir un titre pour le tableau.");
      return;
    }
    setBusySaveTable(true);
    onError("");
    try {
      const updated = await api.addSectionTable(project.project_id, sectionId, {
        title: tblTitle.trim(),
        anchor_hint: tblAnchor.trim(),
        headers: tblHeaders,
        rows: tblRows,
      });
      onProjectUpdated(updated);
      setTblTitle("");
      setTblGoal("");
      setTblAnchor("");
      setTblHeaders(["Paramètre", "Description / Valeur"]);
      setTblRows([["", ""]]);
      setShowTableForm(false);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusySaveTable(false);
    }
  }

  async function handleUploadFigure() {
    if (!figFile) {
      onError("Sélectionnez d'abord un fichier image (.png, .jpg, .jpeg).");
      return;
    }
    setBusyUploadFig(true);
    onError("");
    try {
      const updated = await api.uploadImage(project.project_id, {
        file: figFile,
        target_section_id: sectionId,
        title: figTitle.trim() || figFile.name,
        caption: figCaption.trim(),
        anchor_hint: figAnchor.trim(),
      });
      onProjectUpdated(updated);
      setFigFile(null);
      setFigTitle("");
      setFigCaption("");
      setFigAnchor("");
      setShowFigureForm(false);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyUploadFig(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/70 p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-700">
          Tableaux, Schémas & Figures ({customTables.length} tableau(x),{" "}
          {customDiagrams.length} schéma(s), {sectionImages.length} figure(s))
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              setShowTableForm((v) => !v);
              setShowFigureForm(false);
              setShowDiagramForm(false);
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100"
          >
            <Table2 className="h-3.5 w-3.5 text-indigo-600" />
            + Tableau
          </button>
          <button
            type="button"
            onClick={() => {
              setShowDiagramForm((v) => !v);
              setShowTableForm(false);
              setShowFigureForm(false);
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100"
          >
            <GitBranch className="h-3.5 w-3.5 text-rose-700" />
            + Schéma
          </button>
          <button
            type="button"
            onClick={() => {
              setShowFigureForm((v) => !v);
              setShowTableForm(false);
              setShowDiagramForm(false);
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100"
          >
            <ImagePlus className="h-3.5 w-3.5 text-emerald-600" />
            + Figure
          </button>
        </div>
      </div>

      {/* Add Table Form */}
      {showTableForm && (
        <div className="space-y-3 rounded-lg border border-indigo-200 bg-white p-3.5">
          <div className="text-xs font-bold text-indigo-950">
            Nouveau tableau dans cette section (manuel ou assisté par IA)
          </div>
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Titre du tableau
              </label>
              <input
                type="text"
                value={tblTitle}
                onChange={(e) => setTblTitle(e.target.value)}
                placeholder="Ex. Critères d'inclusion et d'exclusion"
                className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Insérer sous le titre / paragraphe
              </label>
              <select
                value={tblAnchor}
                onChange={(e) => setTblAnchor(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              >
                <option value="">À la fin de cette section</option>
                {anchorOptions.map((opt, idx) => (
                  <option key={idx} value={opt}>
                    Après : « {opt} »
                  </option>
                ))}
              </select>
            </div>
            <div className="md:col-span-2 flex flex-wrap gap-2 items-end">
              <div className="flex-1 min-w-[200px]">
                <label className="block text-[11px] font-medium text-slate-600">
                  Consigne optionnelle pour l'IA (si génération auto)
                </label>
                <input
                  type="text"
                  value={tblGoal}
                  onChange={(e) => setTblGoal(e.target.value)}
                  placeholder="Ex. Comparer les facteurs de risque selon les études camerounaises..."
                  className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                />
              </div>
              <button
                type="button"
                disabled={busySuggestTable}
                onClick={handleSuggestTable}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {busySuggestTable ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                Remplir avec l'IA
              </button>
            </div>
          </div>

          {/* Editable Table Grid */}
          <div className="overflow-x-auto">
            <table className="min-w-full border border-slate-200 text-xs">
              <thead>
                <tr className="bg-slate-100">
                  {tblHeaders.map((h, ci) => (
                    <th key={ci} className="border border-slate-200 p-1">
                      <input
                        type="text"
                        value={h}
                        onChange={(e) => {
                          const next = [...tblHeaders];
                          next[ci] = e.target.value;
                          setTblHeaders(next);
                        }}
                        className="w-full bg-transparent px-1.5 py-1 font-semibold focus:outline-none"
                      />
                    </th>
                  ))}
                  <th className="w-8 border border-slate-200 p-1" />
                </tr>
              </thead>
              <tbody>
                {tblRows.map((row, ri) => (
                  <tr key={ri}>
                    {tblHeaders.map((_, ci) => (
                      <td key={ci} className="border border-slate-200 p-1">
                        <input
                          type="text"
                          value={row[ci] ?? ""}
                          onChange={(e) => {
                            const next = tblRows.map((r) => [...r]);
                            next[ri][ci] = e.target.value;
                            setTblRows(next);
                          }}
                          className="w-full px-1.5 py-1 focus:outline-none"
                        />
                      </td>
                    ))}
                    <td className="border border-slate-200 p-1 text-center">
                      <button
                        type="button"
                        onClick={() =>
                          setTblRows(tblRows.filter((_, i) => i !== ri))
                        }
                        className="text-rose-500 hover:text-rose-700"
                        title="Supprimer ligne"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() =>
                  setTblRows([...tblRows, tblHeaders.map(() => "")])
                }
                className="inline-flex items-center gap-1 rounded border border-slate-300 px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50"
              >
                <Plus className="h-3 w-3" /> Ligne
              </button>
              <button
                type="button"
                onClick={() => {
                  setTblHeaders([
                    ...tblHeaders,
                    `Colonne ${tblHeaders.length + 1}`,
                  ]);
                  setTblRows(tblRows.map((r) => [...r, ""]));
                }}
                className="inline-flex items-center gap-1 rounded border border-slate-300 px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50"
              >
                <Plus className="h-3 w-3" /> Colonne
              </button>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setShowTableForm(false)}
                className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600"
              >
                Annuler
              </button>
              <button
                type="button"
                disabled={busySaveTable}
                onClick={handleSaveTable}
                className="rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                {busySaveTable ? "Ajout..." : "Enregistrer le tableau"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add Figure Form */}
      {showFigureForm && (
        <div className="space-y-3 rounded-lg border border-emerald-200 bg-white p-3.5">
          <div className="text-xs font-bold text-emerald-950">
            Importer une figure dans cette section (.png, .jpg, .jpeg)
          </div>
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Fichier image local
              </label>
              <input
                type="file"
                accept=".png,.jpg,.jpeg,.webp"
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  setFigFile(f);
                  if (f && !figTitle) {
                    setFigTitle(f.name.replace(/\.[^.]+$/, ""));
                  }
                }}
                className="mt-1 w-full text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Insérer sous le titre / paragraphe
              </label>
              <select
                value={figAnchor}
                onChange={(e) => setFigAnchor(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              >
                <option value="">À la fin de cette section</option>
                {anchorOptions.map((opt, idx) => (
                  <option key={idx} value={opt}>
                    Après : « {opt} »
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Titre de la figure
              </label>
              <input
                type="text"
                value={figTitle}
                onChange={(e) => setFigTitle(e.target.value)}
                placeholder="Ex. Diagramme de flux de l'étude"
                className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Légende / source (optionnelle)
              </label>
              <input
                type="text"
                value={figCaption}
                onChange={(e) => setFigCaption(e.target.value)}
                placeholder="Ex. Adapté de OMS 2024"
                className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowFigureForm(false)}
              className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600"
            >
              Annuler
            </button>
            <button
              type="button"
              disabled={busyUploadFig || !figFile}
              onClick={handleUploadFigure}
              className="inline-flex items-center gap-1.5 rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {busyUploadFig ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="h-3.5 w-3.5" />
              )}
              Importer la figure
            </button>
          </div>
        </div>
      )}

      {/* Unified Effective Tables in Section (synced 1:1 with Module 2 & Word export) */}
      {effectiveTables.length > 0 && (
        <div className="space-y-1.5">
          {effectiveTables.map((tbl) => (
            <div
              key={tbl.table_id}
              className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-white px-3 py-2 text-xs"
            >
              <div className="min-w-0">
                <span className="font-semibold text-slate-800">
                  Tableau : {tbl.title}
                </span>{" "}
                <span className="text-slate-500">
                  ({tbl.colsCount} col. × {tbl.rowsCount} lig.
                  {tbl.isStructured ? " · Structuré FMSB" : ""}
                  {tbl.anchor_hint ? ` · après « ${tbl.anchor_hint.slice(0, 35)}... »` : ""})
                </span>
              </div>
              <button
                type="button"
                onClick={async () => {
                  onError("");
                  try {
                    const updated = await api.deleteSectionTable(
                      project.project_id,
                      sectionId,
                      tbl.table_id
                    );
                    onProjectUpdated(updated);
                  } catch (err) {
                    onError(err instanceof Error ? err.message : String(err));
                  }
                }}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-rose-600 hover:bg-rose-50 hover:text-rose-800"
                title="Supprimer ce tableau du Module 1 et du Module 2"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span className="text-[11px] font-medium">Supprimer</span>
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Existing Figures in Section */}
      {sectionImages.length > 0 && (
        <div className="space-y-1.5">
          {sectionImages.map((img) => {
            const imgSrc = img.local_path
              ? `/api/projects/${project.project_id}/images/${img.asset_id}/file`
              : img.source_url;
            return (
              <div
                key={img.asset_id}
                className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-white px-3 py-2 text-xs"
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  {imgSrc && (
                    <img
                      src={imgSrc}
                      alt={img.title}
                      className="h-12 w-12 rounded border border-slate-200 object-cover shrink-0 bg-slate-50"
                    />
                  )}
                  <div className="min-w-0">
                    <div className="truncate font-semibold text-slate-800">
                      Figure : {img.title}
                    </div>
                    <div className="truncate text-[11px] text-slate-500">
                      {img.caption || "Sans légende"}
                      {img.anchor_hint
                        ? ` · après « ${img.anchor_hint.slice(0, 35)}... »`
                        : ""}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={async () => {
                      onError("");
                      try {
                        const updated = await api.toggleImage(
                          project.project_id,
                          img.asset_id,
                          { approved: !img.approved }
                        );
                        onProjectUpdated(updated);
                      } catch (err) {
                        onError(err instanceof Error ? err.message : String(err));
                      }
                    }}
                    className={`rounded px-2 py-0.5 text-[11px] font-semibold ${
                      img.approved
                        ? "bg-emerald-100 text-emerald-800"
                        : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {img.approved ? "Incluse (Module 2)" : "Exclue"}
                  </button>
                  <button
                    type="button"
                    onClick={async () => {
                      onError("");
                      try {
                        const updated = await api.deleteImage(
                          project.project_id,
                          img.asset_id
                        );
                        onProjectUpdated(updated);
                      } catch (err) {
                        onError(err instanceof Error ? err.message : String(err));
                      }
                    }}
                    className="text-rose-600 hover:text-rose-800"
                    title="Supprimer cette figure"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Add Diagram Form */}
      {showDiagramForm && (
        <div className="space-y-3 rounded-lg border border-rose-200 bg-white p-3.5">
          <div className="text-xs font-bold text-rose-950">
            Nouveau schéma vectoriel (Modèle conceptuel, Flux, Arbre décisionnel)
          </div>
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Titre du schéma
              </label>
              <input
                type="text"
                value={dgTitle}
                onChange={(e) => setDgTitle(e.target.value)}
                placeholder="Ex. Modèle conceptuel de l'étude"
                className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Disposition
              </label>
              <select
                value={dgLayout}
                onChange={(e) => setDgLayout(e.target.value as "vertical" | "horizontal")}
                className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              >
                <option value="vertical">Verticale (haut → bas)</option>
                <option value="horizontal">Horizontale (gauche → droite)</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                Insérer sous le paragraphe
              </label>
              <select
                value={dgAnchor}
                onChange={(e) => setDgAnchor(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              >
                <option value="">À la fin de cette section</option>
                {anchorOptions.map((opt, idx) => (
                  <option key={idx} value={opt}>
                    Après : « {opt} »
                  </option>
                ))}
              </select>
            </div>
            <div className="md:col-span-3 flex flex-wrap items-end gap-2">
              <div className="min-w-[220px] flex-1">
                <label className="block text-[11px] font-medium text-slate-600">
                  Consigne IA (optionnelle)
                </label>
                <input
                  type="text"
                  value={dgGoal}
                  onChange={(e) => setDgGoal(e.target.value)}
                  placeholder="Ex. Relier les caractéristiques préopératoires, peropératoires et résultats..."
                  className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                />
              </div>
              <button
                type="button"
                disabled={busySuggestDg}
                onClick={handleSuggestDiagram}
                className="inline-flex items-center gap-1.5 rounded-lg bg-rose-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-800 disabled:opacity-50"
              >
                {busySuggestDg ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                Générer les blocs avec l'IA
              </button>
            </div>
          </div>

          <div className="space-y-2">
            {dgNodes.map((node, idx) => (
              <div
                key={node.id}
                className="grid grid-cols-1 gap-2 rounded border border-slate-200 bg-slate-50 p-2 md:grid-cols-12"
              >
                <select
                  value={node.shape}
                  onChange={(e) => {
                    const next = [...dgNodes];
                    next[idx] = {
                      ...node,
                      shape: e.target.value as DiagramNode["shape"],
                    };
                    setDgNodes(next);
                  }}
                  className="rounded border border-slate-300 bg-white px-2 py-1 text-xs md:col-span-3"
                >
                  <option value="rounded">Rectangle arrondi</option>
                  <option value="rectangle">Rectangle carré</option>
                  <option value="circle">Cercle</option>
                  <option value="diamond">Losange</option>
                </select>
                <input
                  type="text"
                  value={node.title}
                  onChange={(e) => {
                    const next = [...dgNodes];
                    next[idx] = { ...node, title: e.target.value };
                    setDgNodes(next);
                  }}
                  placeholder="Titre du bloc"
                  className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-semibold md:col-span-4"
                />
                <input
                  type="text"
                  value={node.items.join(" ; ")}
                  onChange={(e) => {
                    const next = [...dgNodes];
                    next[idx] = {
                      ...node,
                      items: e.target.value
                        .split(";")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    };
                    setDgNodes(next);
                  }}
                  placeholder="Éléments séparés par ;"
                  className="rounded border border-slate-300 bg-white px-2 py-1 text-xs md:col-span-4"
                />
                <button
                  type="button"
                  onClick={() => {
                    setDgNodes(dgNodes.filter((_, i) => i !== idx));
                    setDgConns(
                      dgConns.filter(
                        (c) => c.from_id !== node.id && c.to_id !== node.id
                      )
                    );
                  }}
                  className="text-rose-600 hover:text-rose-800 md:col-span-1 flex justify-center items-center"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <button
              type="button"
              onClick={() => {
                const newId = `n${dgNodes.length + 1}_${Date.now().toString().slice(-3)}`;
                const prev = dgNodes[dgNodes.length - 1];
                setDgNodes([
                  ...dgNodes,
                  { id: newId, shape: "rounded", title: "Nouveau bloc", items: ["Élément 1"] },
                ]);
                if (prev) {
                  setDgConns([...dgConns, { from_id: prev.id, to_id: newId, label: "" }]);
                }
              }}
              className="inline-flex items-center gap-1 rounded border border-slate-300 px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50"
            >
              <Plus className="h-3 w-3" /> Ajouter un bloc connecté
            </button>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setShowDiagramForm(false)}
                className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600"
              >
                Annuler
              </button>
              <button
                type="button"
                disabled={busySaveDg}
                onClick={handleSaveDiagram}
                className="rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                {busySaveDg ? "Ajout..." : "Enregistrer le schéma"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Existing Vector Diagrams in Section */}
      {customDiagrams.length > 0 && (
        <div className="space-y-2">
          {customDiagrams.map((dg) => (
            <div
              key={dg.diagram_id}
              className="rounded border border-slate-200 bg-white p-3 text-xs"
            >
              <div className="mb-2 flex items-center justify-between">
                <div className="font-semibold text-slate-800">
                  Schéma vectoriel : {dg.title}{" "}
                  <span className="font-normal text-slate-500">
                    ({dg.nodes.length} blocs)
                  </span>
                </div>
                <button
                  type="button"
                  onClick={async () => {
                    const updated = await api.deleteSectionDiagram(
                      project.project_id,
                      sectionId,
                      dg.diagram_id
                    );
                    onProjectUpdated(updated);
                  }}
                  className="text-rose-600 hover:text-rose-800"
                  title="Supprimer ce schéma"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
              <img
                src={api.diagramSvgUrl(project.project_id, sectionId, dg.diagram_id)}
                alt={dg.title}
                className="mx-auto max-h-52 max-w-md rounded border border-slate-100 bg-white p-1 object-contain"
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
