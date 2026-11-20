import { useEffect, useMemo, useState } from "react";
import {
  Check,
  GitBranch,
  ImagePlus,
  Loader2,
  Pencil,
  Plus,
  Sparkles,
  Table2,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, withAuthToken } from "../api/client";
import type {
  DiagramConnection,
  DiagramNode,
  ProtocolProject,
} from "../types/protocol";

interface SectionMediaPanelProps {
  project: ProtocolProject;
  sectionId: string;
  sectionText: string;
  sectionHtml?: string;
  onProjectUpdated: (updated: ProtocolProject) => void;
  onError: (msg: string) => void;
}

export interface SectionAnchorOption {
  kind: "heading" | "sentence";
  index: number;
  value: string;
  label: string;
}

function cleanAnchorRawText(raw: string): string {
  return raw
    .replace(/^\s{0,3}#{1,6}\s+/, "")
    .replace(/^\s*(?:[-*•])\s+/, "")
    .replace(/[*_`~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function truncateDisplay(s: string, maxLen = 88): string {
  return s.length > maxLen ? `${s.slice(0, maxLen).trimEnd()}…` : s;
}

function splitFrenchSentencesExhaustive(text: string): string[] {
  const clean = text.trim();
  if (!clean) return [];

  // Protect common French/medical abbreviations and decimals so dots inside them don't split sentences
  let protectedStr = clean;
  const abbrs = [
    "Dr.",
    "Pr.",
    "M.",
    "Mme.",
    "Mlle.",
    "p. ex.",
    "ex.",
    "et al.",
    "cf.",
    "fig.",
    "Fig.",
    "Tab.",
    "vol.",
    "vs.",
    "env.",
    "approx.",
    "c.-à-d.",
    "op. cit.",
    "coll.",
  ];
  for (const ab of abbrs) {
    protectedStr = protectedStr.split(ab).join(ab.replace(/\./g, "@@DOT@@"));
  }
  // Protect decimal numbers (e.g. 3.5 or 0.05) and numbered prefixes at the start of a block (e.g. "1. ")
  protectedStr = protectedStr
    .replace(/(\d)\.(\d)/g, "$1@@DOT@@$2")
    .replace(/^(\d{1,2})\.\s+/g, "$1@@DOT@@ ");

  const rawParts = protectedStr.split(/(?<=[.!?])\s+(?=[A-ZÀ-Ÿa-zà-ÿ0-9«"(\[])/);
  return rawParts
    .map((p) => cleanAnchorRawText(p.replace(/@@DOT@@/g, ".")))
    .filter((p) => p.length >= 4 && /[A-Za-zÀ-ÿ0-9]/.test(p));
}

function isProbableHeadingLine(text: string, hadMarkdownHeading: boolean): boolean {
  if (hadMarkdownHeading) return true;
  if (!text || text.length > 105) return false;
  if (/[.!?]$/.test(text)) return false;
  // Numbered or Roman subheading without trailing period (e.g. "1.1 Cadre d'étude", "II. Collecte")
  if (/^(?:[IVX]{1,5}\.?|[0-9]+(?:\.[0-9]+)*\.?)\s+[A-ZÀ-Ÿ]/.test(text)) {
    return true;
  }
  return false;
}

export function extractSectionAnchors(
  sectionText: string,
  sectionHtml?: string
): {
  options: SectionAnchorOption[];
  headingCount: number;
  sentenceCount: number;
} {
  interface RawBlock {
    isHeading: boolean;
    text: string;
  }

  const blocks: RawBlock[] = [];
  const rawMd = (sectionText || "").trim();
  const hasMdHeadings = /(^|\n)\s{0,3}#{1,6}\s+/.test(rawMd);
  const rawHtml = (sectionHtml || "").trim();

  if (rawHtml && /<(h[1-6]|p|li)\b/i.test(rawHtml) && typeof DOMParser !== "undefined") {
    try {
      const doc = new DOMParser().parseFromString(rawHtml, "text/html");
      const nodes = doc.body.querySelectorAll("h1, h2, h3, h4, h5, h6, p, li");
      nodes.forEach((el) => {
        if (el.closest("table")) return;
        const tag = el.tagName.toLowerCase();
        if (tag === "li" && el.querySelector("p")) return;
        const rawElText = (el.textContent || "").replace(/\s+/g, " ").trim();
        if (!rawElText) return;
        const mdHeading = /^#{1,6}\s+/.test(rawElText);
        const clean = cleanAnchorRawText(rawElText);
        if (!clean) return;
        blocks.push({
          isHeading:
            tag.startsWith("h") || isProbableHeadingLine(clean, mdHeading),
          text: clean,
        });
      });
    } catch {
      // Fall back to Markdown line parsing below
    }
  }

  if (blocks.length === 0 || (hasMdHeadings && blocks.every((b) => !b.isHeading))) {
    blocks.length = 0;
    const lines = rawMd.split(/\n+/);
    for (const ln of lines) {
      const trimmed = ln.trim();
      if (!trimmed) continue;
      if (/^\s*\|.*\|\s*$/.test(trimmed)) continue;
      const mdHead = /^\s{0,3}#{1,6}\s+/.test(trimmed);
      const isBoldOnly = /^\*\*[^*]+\*\*$/.test(trimmed) && trimmed.length < 110;
      const clean = cleanAnchorRawText(trimmed);
      if (!clean) continue;
      blocks.push({
        isHeading: isProbableHeadingLine(clean, mdHead || isBoldOnly),
        text: clean,
      });
    }
  }

  const options: SectionAnchorOption[] = [];
  const seenValues = new Set<string>();
  let headingCount = 0;
  let sentenceCount = 0;

  for (const blk of blocks) {
    if (blk.isHeading) {
      const val = blk.text.slice(0, 140);
      if (!val || seenValues.has(val)) continue;
      seenValues.add(val);
      headingCount += 1;
      options.push({
        kind: "heading",
        index: headingCount,
        value: val,
        label: `[Titre ${headingCount}] ${truncateDisplay(val)}`,
      });
    } else {
      const sentences = splitFrenchSentencesExhaustive(blk.text);
      for (const sent of sentences) {
        // Keep exact substring up to 140 chars WITHOUT '...' so backend docx matching hits reliably
        const val = sent.slice(0, 140);
        if (!val || seenValues.has(val)) continue;
        seenValues.add(val);
        sentenceCount += 1;
        options.push({
          kind: "sentence",
          index: sentenceCount,
          value: val,
          label: `[Phrase ${sentenceCount}] ${truncateDisplay(val)}`,
        });
      }
    }
  }

  return { options, headingCount, sentenceCount };
}

export function SectionMediaPanel({
  project,
  sectionId,
  sectionText,
  sectionHtml,
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
        anchor_hint: ct.anchor_hint || "",
        headers: ct.headers,
        rows: ct.rows,
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
          headers: ["Variable", "Rôle", "Définition", "Échelle", "Outil"],
          rows: project.variables.map((v) => [
            v.name,
            v.role,
            v.definition || "—",
            v.measurement_scale || "—",
            v.collection_tool || "—",
          ]),
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
          headers: [
            "Auteur(s), Pays (Année)",
            "Devis",
            "Population",
            "Résultats principaux",
            "Limites",
          ],
          rows: project.literature_synthesis.map((s) => [
            s.authors_country_year,
            s.study_design,
            s.population,
            s.main_results,
            s.limits || "—",
          ]),
          isStructured: true,
        },
      ];
    }
    if (
      sectionId === "chap3_chronogramme" &&
      (project.timeline_grid?.rows?.length ?? 0) > 0
    ) {
      const periods = project.timeline_grid.periods ?? [];
      return [
        {
          table_id: "__structured_timeline__",
          title: "Chronogramme des activités",
          colsCount: periods.length + 1,
          rowsCount: project.timeline_grid.rows.length,
          anchor_hint: "",
          headers: ["Activité", ...periods],
          rows: project.timeline_grid.rows.map((r) => [
            r.activity,
            ...periods.map((p) => (r.active_periods.includes(p) ? "■" : "")),
          ]),
          isStructured: true,
        },
      ];
    }
    if (sectionId === "chap3_budget" && (project.budget?.length ?? 0) > 0) {
      const budgetRows = project.budget.map((b) => {
        const lineTotal =
          b.total_fcfa > 0 ? b.total_fcfa : b.quantity * b.unit_cost_fcfa;
        return [
          b.designation,
          String(b.unit_cost_fcfa),
          String(b.quantity),
          String(lineTotal),
        ];
      });
      return [
        {
          table_id: "__structured_budget__",
          title: "Budget prévisionnel de l'étude",
          colsCount: 4,
          rowsCount: budgetRows.length,
          anchor_hint: "",
          headers: [
            "Désignation",
            "Prix unitaire (FCFA)",
            "Quantité",
            "Prix total (FCFA)",
          ],
          rows: budgetRows,
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

  // Exhaustive extraction of ALL headings, subheadings, and sentences across the entire section
  const {
    options: anchorOptions,
    headingCount,
    sentenceCount,
  } = useMemo(
    () =>
      extractSectionAnchors(
        sectionText,
        sectionHtml ?? secState?.content_html ?? ""
      ),
    [sectionText, sectionHtml, secState?.content_html]
  );

  // --- Create forms state ---
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

  const [showFigureForm, setShowFigureForm] = useState(false);
  const [figFile, setFigFile] = useState<File | null>(null);
  const [figTitle, setFigTitle] = useState("");
  const [figCaption, setFigCaption] = useState("");
  const [figAnchor, setFigAnchor] = useState("");
  const [busyUploadFig, setBusyUploadFig] = useState(false);

  const [showDiagramForm, setShowDiagramForm] = useState(false);
  const [dgTitle, setDgTitle] = useState("");
  const [dgGoal, setDgGoal] = useState("");
  const [dgAnchor, setDgAnchor] = useState("");
  const [dgLayout, setDgLayout] = useState<"vertical" | "horizontal">("vertical");
  const [dgNodes, setDgNodes] = useState<DiagramNode[]>([
    {
      id: "n1",
      shape: "rounded",
      title: "1. Variables indépendantes",
      items: ["Facteur A", "Facteur B"],
    },
    {
      id: "n2",
      shape: "rounded",
      title: "2. Critère de jugement",
      items: ["Résultat principal"],
    },
  ]);
  const [dgConns, setDgConns] = useState<DiagramConnection[]>([
    { from_id: "n1", to_id: "n2", label: "" },
  ]);
  const [busySuggestDg, setBusySuggestDg] = useState(false);
  const [busySaveDg, setBusySaveDg] = useState(false);

  // --- Inline Edit states (Tables, Figures, Diagrams) ---
  const [editingTableId, setEditingTableId] = useState<string | null>(null);
  const [editTblTitle, setEditTblTitle] = useState("");
  const [editTblAnchor, setEditTblAnchor] = useState("");
  const [editTblHeaders, setEditTblHeaders] = useState<string[]>([]);
  const [editTblRows, setEditTblRows] = useState<string[][]>([]);
  const [busyUpdateTable, setBusyUpdateTable] = useState(false);

  const [editingImageId, setEditingImageId] = useState<string | null>(null);
  const [editImgTitle, setEditImgTitle] = useState("");
  const [editImgCaption, setEditImgCaption] = useState("");
  const [editImgAnchor, setEditImgAnchor] = useState("");
  const [busyUpdateImage, setBusyUpdateImage] = useState(false);

  const [editingDiagramId, setEditingDiagramId] = useState<string | null>(null);
  const [editDgTitle, setEditDgTitle] = useState("");
  const [editDgCaption, setEditDgCaption] = useState("");
  const [editDgAnchor, setEditDgAnchor] = useState("");
  const [editDgLayout, setEditDgLayout] = useState<"vertical" | "horizontal">(
    "vertical"
  );
  const [editDgNodes, setEditDgNodes] = useState<DiagramNode[]>([]);
  const [busyUpdateDiagram, setBusyUpdateDiagram] = useState(false);

  useEffect(() => {
    setEditingTableId(null);
    setEditingImageId(null);
    setEditingDiagramId(null);
    setShowTableForm(false);
    setShowFigureForm(false);
    setShowDiagramForm(false);
  }, [sectionId]);

  const anchorSelectLabel = `Emplacement dans la section (${headingCount} titre(s), ${sentenceCount} phrase(s))`;

  function renderAnchorSelect(
    val: string,
    onChange: (nextVal: string) => void,
    ariaLabel?: string
  ) {
    const hasCustomVal =
      Boolean(val.trim()) && !anchorOptions.some((o) => o.value === val);
    return (
      <select
        aria-label={ariaLabel || anchorSelectLabel}
        value={val}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
      >
        <option value="">À la fin de cette section</option>
        {hasCustomVal && (
          <option value={val}>
            Emplacement actuel : « {truncateDisplay(val, 80)} »
          </option>
        )}
        {anchorOptions.map((opt, idx) => (
          <option key={`${opt.kind}-${idx}`} value={opt.value}>
            Après {opt.label}
          </option>
        ))}
      </select>
    );
  }

  async function handleSuggestDiagram() {
    if (!dgTitle.trim()) {
      onError(
        "Indiquez d'abord un titre pour le schéma (ex: Modèle conceptuel)."
      );
      return;
    }
    setBusySuggestDg(true);
    onError("");
    try {
      const res = await api.suggestSectionDiagram(
        project.project_id,
        sectionId,
        {
          title: dgTitle.trim(),
          goal: dgGoal.trim(),
          anchor_hint: dgAnchor,
        }
      );
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
      const updated = await api.addSectionDiagram(
        project.project_id,
        sectionId,
        {
          title: dgTitle.trim(),
          caption: "",
          anchor_hint: dgAnchor.trim(),
          layout: dgLayout,
          nodes: dgNodes,
          connections: dgConns,
        }
      );
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
      onError(
        "Indiquez d'abord un titre de tableau pour que l'IA puisse le proposer."
      );
      return;
    }
    setBusySuggestTable(true);
    onError("");
    try {
      const res = await api.suggestSectionTable(
        project.project_id,
        sectionId,
        {
          title: tblTitle.trim(),
          goal: tblGoal.trim(),
          anchor_hint: tblAnchor,
        }
      );
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

  async function handleUpdateExistingTable(tableId: string) {
    if (!editTblTitle.trim()) {
      onError("Le nom du tableau ne peut pas être vide.");
      return;
    }
    setBusyUpdateTable(true);
    onError("");
    try {
      const updated = await api.updateSectionTable(
        project.project_id,
        sectionId,
        tableId,
        {
          title: editTblTitle.trim(),
          anchor_hint: editTblAnchor.trim(),
          headers: editTblHeaders,
          rows: editTblRows,
        }
      );
      onProjectUpdated(updated);
      setEditingTableId(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyUpdateTable(false);
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

  async function handleUpdateExistingImage(assetId: string) {
    if (!editImgTitle.trim()) {
      onError("Le nom de la figure ne peut pas être vide.");
      return;
    }
    setBusyUpdateImage(true);
    onError("");
    try {
      const updated = await api.toggleImage(project.project_id, assetId, {
        title: editImgTitle.trim(),
        caption: editImgCaption.trim(),
        anchor_hint: editImgAnchor.trim(),
      });
      onProjectUpdated(updated);
      setEditingImageId(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyUpdateImage(false);
    }
  }

  async function handleUpdateExistingDiagram(diagramId: string) {
    if (!editDgTitle.trim()) {
      onError("Le nom du schéma ne peut pas être vide.");
      return;
    }
    setBusyUpdateDiagram(true);
    onError("");
    try {
      const updated = await api.updateSectionDiagram(
        project.project_id,
        sectionId,
        diagramId,
        {
          title: editDgTitle.trim(),
          caption: editDgCaption.trim(),
          anchor_hint: editDgAnchor.trim(),
          layout: editDgLayout,
          nodes: editDgNodes,
        }
      );
      onProjectUpdated(updated);
      setEditingDiagramId(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyUpdateDiagram(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/70 p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-700">
          Tableaux, Schémas &amp; Figures ({effectiveTables.length} tableau(x),{" "}
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
                {anchorSelectLabel}
              </label>
              {renderAnchorSelect(
                tblAnchor,
                setTblAnchor,
                "Emplacement du nouveau tableau"
              )}
            </div>
            <div className="flex flex-wrap items-end gap-2 md:col-span-2">
              <div className="min-w-[200px] flex-1">
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
                {anchorSelectLabel}
              </label>
              {renderAnchorSelect(
                figAnchor,
                setFigAnchor,
                "Emplacement de la nouvelle figure"
              )}
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
                onChange={(e) =>
                  setDgLayout(e.target.value as "vertical" | "horizontal")
                }
                className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              >
                <option value="vertical">Verticale (haut → bas)</option>
                <option value="horizontal">Horizontale (gauche → droite)</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600">
                {anchorSelectLabel}
              </label>
              {renderAnchorSelect(
                dgAnchor,
                setDgAnchor,
                "Emplacement du nouveau schéma"
              )}
            </div>
            <div className="flex flex-wrap items-end gap-2 md:col-span-3">
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
                  className="flex items-center justify-center text-rose-600 hover:text-rose-800 md:col-span-1"
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
                const newId = `n${dgNodes.length + 1}_${Date.now()
                  .toString()
                  .slice(-3)}`;
                const prev = dgNodes[dgNodes.length - 1];
                setDgNodes([
                  ...dgNodes,
                  {
                    id: newId,
                    shape: "rounded",
                    title: "Nouveau bloc",
                    items: ["Élément 1"],
                  },
                ]);
                if (prev) {
                  setDgConns([
                    ...dgConns,
                    { from_id: prev.id, to_id: newId, label: "" },
                  ]);
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

      {/* Existing Effective Tables in Section (Editable Name, Anchor & Grid) */}
      {effectiveTables.length > 0 && (
        <div className="space-y-2">
          {effectiveTables.map((tbl) => {
            const isEditing = editingTableId === tbl.table_id;
            return (
              <div
                key={tbl.table_id}
                className="rounded border border-slate-200 bg-white px-3 py-2 text-xs"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-slate-800">
                      Tableau : {tbl.title}
                    </div>
                    <div className="text-[11px] text-slate-500">
                      {tbl.colsCount} col. × {tbl.rowsCount} lig.
                      {tbl.isStructured ? " · Structuré FMSB" : ""} ·
                      Emplacement :{" "}
                      {tbl.anchor_hint
                        ? `après « ${truncateDisplay(tbl.anchor_hint, 48)} »`
                        : "À la fin de la section"}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => {
                        if (isEditing) {
                          setEditingTableId(null);
                        } else {
                          setEditingTableId(tbl.table_id);
                          setEditTblTitle(tbl.title);
                          setEditTblAnchor(tbl.anchor_hint || "");
                          setEditTblHeaders([...tbl.headers]);
                          setEditTblRows(tbl.rows.map((r) => [...r]));
                        }
                      }}
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-indigo-700 hover:bg-indigo-50"
                      title="Modifier le nom, l'emplacement ou le contenu du tableau"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      <span className="text-[11px] font-medium">
                        {isEditing ? "Fermer" : "Modifier"}
                      </span>
                    </button>
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
                          if (editingTableId === tbl.table_id) {
                            setEditingTableId(null);
                          }
                          onProjectUpdated(updated);
                        } catch (err) {
                          onError(
                            err instanceof Error ? err.message : String(err)
                          );
                        }
                      }}
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-rose-600 hover:bg-rose-50 hover:text-rose-800"
                      title="Supprimer ce tableau du Module 1 et du Module 2"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      <span className="text-[11px] font-medium">Supprimer</span>
                    </button>
                  </div>
                </div>

                {isEditing && (
                  <div className="mt-3 space-y-2.5 border-t border-slate-200 pt-3">
                    <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                      <div>
                        <label className="block text-[11px] font-medium text-slate-600">
                          Nom / Titre du tableau
                        </label>
                        <input
                          type="text"
                          value={editTblTitle}
                          onChange={(e) => setEditTblTitle(e.target.value)}
                          className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-slate-600">
                          {anchorSelectLabel}
                        </label>
                        {renderAnchorSelect(
                          editTblAnchor,
                          setEditTblAnchor,
                          "Emplacement du tableau"
                        )}
                      </div>
                    </div>

                    {editTblHeaders.length > 0 && (
                      <div className="overflow-x-auto">
                        <table className="min-w-full border border-slate-200 text-xs">
                          <thead>
                            <tr className="bg-slate-100">
                              {editTblHeaders.map((h, ci) => (
                                <th
                                  key={ci}
                                  className="border border-slate-200 p-1"
                                >
                                  <input
                                    type="text"
                                    value={h}
                                    onChange={(e) => {
                                      const next = [...editTblHeaders];
                                      next[ci] = e.target.value;
                                      setEditTblHeaders(next);
                                    }}
                                    className="w-full bg-transparent px-1 py-0.5 font-semibold focus:outline-none"
                                  />
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {editTblRows.map((r, ri) => (
                              <tr key={ri}>
                                {editTblHeaders.map((_, ci) => (
                                  <td
                                    key={ci}
                                    className="border border-slate-200 p-1"
                                  >
                                    <input
                                      type="text"
                                      value={r[ci] ?? ""}
                                      onChange={(e) => {
                                        const next = editTblRows.map((row) => [
                                          ...row,
                                        ]);
                                        next[ri][ci] = e.target.value;
                                        setEditTblRows(next);
                                      }}
                                      className="w-full px-1 py-0.5 focus:outline-none"
                                    />
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setEditingTableId(null)}
                        className="inline-flex items-center gap-1 rounded border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        <X className="h-3.5 w-3.5" />
                        Annuler
                      </button>
                      <button
                        type="button"
                        disabled={busyUpdateTable}
                        onClick={() => handleUpdateExistingTable(tbl.table_id)}
                        className="inline-flex items-center gap-1 rounded bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                      >
                        {busyUpdateTable ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Check className="h-3.5 w-3.5" />
                        )}
                        Enregistrer
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Existing Figures in Section (Editable Name, Caption & Anchor) */}
      {sectionImages.length > 0 && (
        <div className="space-y-2">
          {sectionImages.map((img) => {
            const imgSrc = img.local_path
              ? withAuthToken(
                  `/api/projects/${project.project_id}/images/${img.asset_id}/file`
                )
              : img.source_url;
            const isEditing = editingImageId === img.asset_id;
            return (
              <div
                key={img.asset_id}
                className="rounded border border-slate-200 bg-white px-3 py-2 text-xs"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex min-w-0 flex-1 items-center gap-2.5">
                    {imgSrc && (
                      <img
                        src={imgSrc}
                        alt={img.title}
                        className="h-12 w-12 shrink-0 rounded border border-slate-200 bg-slate-50 object-cover"
                      />
                    )}
                    <div className="min-w-0">
                      <div className="truncate font-semibold text-slate-800">
                        Figure : {img.title}
                      </div>
                      <div className="truncate text-[11px] text-slate-500">
                        {img.caption || "Sans légende"} · Emplacement :{" "}
                        {img.anchor_hint
                          ? `après « ${truncateDisplay(img.anchor_hint, 48)} »`
                          : "À la fin de la section"}
                      </div>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => {
                        if (isEditing) {
                          setEditingImageId(null);
                        } else {
                          setEditingImageId(img.asset_id);
                          setEditImgTitle(img.title);
                          setEditImgCaption(img.caption || "");
                          setEditImgAnchor(img.anchor_hint || "");
                        }
                      }}
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-indigo-700 hover:bg-indigo-50"
                      title="Modifier le nom, la légende ou l'emplacement de la figure"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      <span className="text-[11px] font-medium">
                        {isEditing ? "Fermer" : "Modifier"}
                      </span>
                    </button>
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
                          onError(
                            err instanceof Error ? err.message : String(err)
                          );
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
                          if (editingImageId === img.asset_id) {
                            setEditingImageId(null);
                          }
                          onProjectUpdated(updated);
                        } catch (err) {
                          onError(
                            err instanceof Error ? err.message : String(err)
                          );
                        }
                      }}
                      className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-rose-600 hover:bg-rose-50 hover:text-rose-800"
                      title="Supprimer cette figure"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>

                {isEditing && (
                  <div className="mt-3 space-y-2.5 border-t border-slate-200 pt-3">
                    <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                      <div>
                        <label className="block text-[11px] font-medium text-slate-600">
                          Nom / Titre de la figure
                        </label>
                        <input
                          type="text"
                          value={editImgTitle}
                          onChange={(e) => setEditImgTitle(e.target.value)}
                          className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-slate-600">
                          {anchorSelectLabel}
                        </label>
                        {renderAnchorSelect(
                          editImgAnchor,
                          setEditImgAnchor,
                          "Emplacement de la figure"
                        )}
                      </div>
                      <div className="md:col-span-2">
                        <label className="block text-[11px] font-medium text-slate-600">
                          Légende / Source
                        </label>
                        <input
                          type="text"
                          value={editImgCaption}
                          onChange={(e) => setEditImgCaption(e.target.value)}
                          placeholder="Ex. Adapté de OMS 2024"
                          className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                        />
                      </div>
                    </div>
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setEditingImageId(null)}
                        className="inline-flex items-center gap-1 rounded border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        <X className="h-3.5 w-3.5" />
                        Annuler
                      </button>
                      <button
                        type="button"
                        disabled={busyUpdateImage}
                        onClick={() => handleUpdateExistingImage(img.asset_id)}
                        className="inline-flex items-center gap-1 rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                      >
                        {busyUpdateImage ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Check className="h-3.5 w-3.5" />
                        )}
                        Enregistrer
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Existing Vector Diagrams in Section (Editable Name, Anchor, Layout & Nodes) */}
      {customDiagrams.length > 0 && (
        <div className="space-y-2">
          {customDiagrams.map((dg) => {
            const isEditing = editingDiagramId === dg.diagram_id;
            return (
              <div
                key={dg.diagram_id}
                className="rounded border border-slate-200 bg-white p-3 text-xs"
              >
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-slate-800">
                      Schéma vectoriel : {dg.title}{" "}
                      <span className="font-normal text-slate-500">
                        ({dg.nodes.length} blocs)
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-500">
                      Emplacement :{" "}
                      {dg.anchor_hint
                        ? `après « ${truncateDisplay(dg.anchor_hint, 48)} »`
                        : "À la fin de la section"}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => {
                        if (isEditing) {
                          setEditingDiagramId(null);
                        } else {
                          setEditingDiagramId(dg.diagram_id);
                          setEditDgTitle(dg.title);
                          setEditDgCaption(dg.caption || "");
                          setEditDgAnchor(dg.anchor_hint || "");
                          setEditDgLayout(dg.layout || "vertical");
                          setEditDgNodes(
                            dg.nodes.map((n) => ({
                              ...n,
                              items: [...(n.items || [])],
                            }))
                          );
                        }
                      }}
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-indigo-700 hover:bg-indigo-50"
                      title="Modifier le nom, l'emplacement ou les blocs de ce schéma"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      <span className="text-[11px] font-medium">
                        {isEditing ? "Fermer" : "Modifier"}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        const updated = await api.deleteSectionDiagram(
                          project.project_id,
                          sectionId,
                          dg.diagram_id
                        );
                        if (editingDiagramId === dg.diagram_id) {
                          setEditingDiagramId(null);
                        }
                        onProjectUpdated(updated);
                      }}
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-rose-600 hover:bg-rose-50 hover:text-rose-800"
                      title="Supprimer ce schéma"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      <span className="text-[11px] font-medium">Supprimer</span>
                    </button>
                  </div>
                </div>

                {isEditing && (
                  <div className="mb-3 space-y-2.5 border-t border-b border-slate-200 py-3">
                    <div className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
                      <div>
                        <label className="block text-[11px] font-medium text-slate-600">
                          Nom / Titre du schéma
                        </label>
                        <input
                          type="text"
                          value={editDgTitle}
                          onChange={(e) => setEditDgTitle(e.target.value)}
                          className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-slate-600">
                          Disposition
                        </label>
                        <select
                          value={editDgLayout}
                          onChange={(e) =>
                            setEditDgLayout(
                              e.target.value as "vertical" | "horizontal"
                            )
                          }
                          className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                        >
                          <option value="vertical">Verticale (haut → bas)</option>
                          <option value="horizontal">
                            Horizontale (gauche → droite)
                          </option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-slate-600">
                          {anchorSelectLabel}
                        </label>
                        {renderAnchorSelect(
                          editDgAnchor,
                          setEditDgAnchor,
                          "Emplacement du schéma"
                        )}
                      </div>
                      <div className="md:col-span-3">
                        <label className="block text-[11px] font-medium text-slate-600">
                          Légende du schéma (optionnelle)
                        </label>
                        <input
                          type="text"
                          value={editDgCaption}
                          onChange={(e) => setEditDgCaption(e.target.value)}
                          className="mt-1 w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                        />
                      </div>
                    </div>

                    <div className="space-y-1.5">
                      {editDgNodes.map((node, nIdx) => (
                        <div
                          key={node.id}
                          className="grid grid-cols-1 gap-2 rounded border border-slate-200 bg-slate-50 p-2 md:grid-cols-12"
                        >
                          <select
                            value={node.shape}
                            onChange={(e) => {
                              const next = [...editDgNodes];
                              next[nIdx] = {
                                ...node,
                                shape: e.target.value as DiagramNode["shape"],
                              };
                              setEditDgNodes(next);
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
                              const next = [...editDgNodes];
                              next[nIdx] = { ...node, title: e.target.value };
                              setEditDgNodes(next);
                            }}
                            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-semibold md:col-span-4"
                          />
                          <input
                            type="text"
                            value={(node.items || []).join(" ; ")}
                            onChange={(e) => {
                              const next = [...editDgNodes];
                              next[nIdx] = {
                                ...node,
                                items: e.target.value
                                  .split(";")
                                  .map((s) => s.trim())
                                  .filter(Boolean),
                              };
                              setEditDgNodes(next);
                            }}
                            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs md:col-span-5"
                          />
                        </div>
                      ))}
                    </div>

                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setEditingDiagramId(null)}
                        className="inline-flex items-center gap-1 rounded border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        <X className="h-3.5 w-3.5" />
                        Annuler
                      </button>
                      <button
                        type="button"
                        disabled={busyUpdateDiagram}
                        onClick={() => handleUpdateExistingDiagram(dg.diagram_id)}
                        className="inline-flex items-center gap-1 rounded bg-rose-700 px-3 py-1 text-xs font-semibold text-white hover:bg-rose-800 disabled:opacity-50"
                      >
                        {busyUpdateDiagram ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Check className="h-3.5 w-3.5" />
                        )}
                        Enregistrer
                      </button>
                    </div>
                  </div>
                )}

                <img
                  src={api.diagramSvgUrl(
                    project.project_id,
                    sectionId,
                    dg.diagram_id
                  )}
                  alt={dg.title}
                  className="mx-auto max-h-52 max-w-md rounded border border-slate-100 bg-white object-contain p-1"
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
