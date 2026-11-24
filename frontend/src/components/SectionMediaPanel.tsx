import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowRight,
  Check,
  GitBranch,
  ImagePlus,
  Loader2,
  MapPin,
  Pencil,
  Plus,
  Sparkles,
  Table2,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, withAuthToken } from "../api/client";
import { useLanguage } from "../i18n/LanguageContext";
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
  const { lang } = useLanguage();
  const isEn = lang === "en";
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
          title: isEn ? "Operational list of study variables" : "Liste opérationnelle des variables d'étude",
          colsCount: 5,
          rowsCount: project.variables.length,
          anchor_hint: "",
          headers: isEn
            ? ["Variable", "Role", "Definition", "Scale", "Tool"]
            : ["Variable", "Rôle", "Définition", "Échelle", "Outil"],
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
          title: isEn ? "Synthesis of previous studies" : "Synthèse des études antérieures",
          colsCount: 5,
          rowsCount: project.literature_synthesis.length,
          anchor_hint: "",
          headers: isEn
            ? ["Author(s), Country (Year)", "Design", "Population", "Main results", "Limitations"]
            : [
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
          title: isEn ? "Activity timeline" : "Chronogramme des activités",
          colsCount: periods.length + 1,
          rowsCount: project.timeline_grid.rows.length,
          anchor_hint: "",
          headers: [isEn ? "Activity" : "Activité", ...periods],
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
          title: isEn ? "Estimated study budget" : "Budget prévisionnel de l'étude",
          colsCount: 4,
          rowsCount: budgetRows.length,
          anchor_hint: "",
          headers: isEn
            ? ["Item", "Unit price (FCFA)", "Quantity", "Total price (FCFA)"]
            : [
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
    isEn,
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
  const [figPreviewUrl, setFigPreviewUrl] = useState<string | null>(null);
  const [isDraggingFig, setIsDraggingFig] = useState(false);
  const [figTitle, setFigTitle] = useState("");
  const [figCaption, setFigCaption] = useState("");
  const [figAnchor, setFigAnchor] = useState("");
  const [busyUploadFig, setBusyUploadFig] = useState(false);

  useEffect(() => {
    if (!figFile) {
      setFigPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(figFile);
    setFigPreviewUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [figFile]);

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
    setFigFile(null);
    setIsDraggingFig(false);
  }, [sectionId]);

  const anchorSelectLabel = isEn
    ? `Placement in section (${headingCount} heading(s), ${sentenceCount} sentence(s))`
    : `Emplacement dans la section (${headingCount} titre(s), ${sentenceCount} phrase(s))`;

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
        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs text-slate-800 shadow-2xs transition focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
      >
        <option value="">
          {isEn ? "At the end of this section" : "À la fin de cette section"}
        </option>
        {hasCustomVal && (
          <option value={val}>
            {isEn ? "Current placement" : "Emplacement actuel"} : «{" "}
            {truncateDisplay(val, 80)} »
          </option>
        )}
        {anchorOptions.map((opt, idx) => (
          <option key={`${opt.kind}-${idx}`} value={opt.value}>
            {isEn ? "After" : "Après"} {opt.label}
          </option>
        ))}
      </select>
    );
  }

  async function handleSuggestDiagram() {
    if (!dgTitle.trim()) {
      onError(
        isEn
          ? "Please enter a diagram title first (e.g. Conceptual framework)."
          : "Indiquez d'abord un titre pour le schéma (ex: Modèle conceptuel)."
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
      onError(
        isEn
          ? "Add a title and at least one block to the diagram."
          : "Ajoutez un titre et au moins un bloc au schéma."
      );
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
        isEn
          ? "Please enter a table title first so AI can propose its structure."
          : "Indiquez d'abord un titre de tableau pour que l'IA puisse le proposer."
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
      onError(isEn ? "Please enter a table title." : "Veuillez saisir un titre pour le tableau.");
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
      onError(isEn ? "Table title cannot be empty." : "Le nom du tableau ne peut pas être vide.");
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
      onError(
        isEn
          ? "Select an image file first (.png, .jpg, .jpeg)."
          : "Sélectionnez d'abord un fichier image (.png, .jpg, .jpeg)."
      );
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
      onError(isEn ? "Figure title cannot be empty." : "Le nom de la figure ne peut pas être vide.");
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
      onError(isEn ? "Diagram title cannot be empty." : "Le nom du schéma ne peut pas être vide.");
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

  const hasBodyContent =
    showTableForm ||
    showFigureForm ||
    showDiagramForm ||
    effectiveTables.length > 0 ||
    sectionImages.length > 0 ||
    customDiagrams.length > 0;

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-sm">
      {/* Top Media Bar */}
      <div
        className={`flex flex-wrap items-center justify-between gap-3 bg-slate-50/80 px-4 py-3 ${
          hasBodyContent ? "border-b border-slate-200/80" : ""
        }`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-bold uppercase tracking-wide text-slate-700">
            {isEn
              ? "Tables, Diagrams & Figures"
              : "Tableaux, Schémas & Figures"}
          </span>
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${
                effectiveTables.length > 0
                  ? "bg-indigo-100 text-indigo-800"
                  : "bg-slate-200/70 text-slate-600"
              }`}
            >
              <Table2 className="h-3 w-3" />
              {effectiveTables.length}{" "}
              {isEn
                ? effectiveTables.length === 1
                  ? "table"
                  : "tables"
                : effectiveTables.length <= 1
                ? "tableau"
                : "tableaux"}
            </span>
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${
                customDiagrams.length > 0
                  ? "bg-rose-100 text-[#6C2826]"
                  : "bg-slate-200/70 text-slate-600"
              }`}
            >
              <GitBranch className="h-3 w-3" />
              {customDiagrams.length}{" "}
              {isEn
                ? customDiagrams.length === 1
                  ? "diagram"
                  : "diagrams"
                : customDiagrams.length <= 1
                ? "schéma"
                : "schémas"}
            </span>
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${
                sectionImages.length > 0
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-slate-200/70 text-slate-600"
              }`}
            >
              <ImagePlus className="h-3 w-3" />
              {sectionImages.length}{" "}
              {sectionImages.length <= 1 ? "figure" : "figures"}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            aria-pressed={showTableForm}
            onClick={() => {
              setShowTableForm((v) => !v);
              setShowFigureForm(false);
              setShowDiagramForm(false);
            }}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
              showTableForm
                ? "border-indigo-600 bg-indigo-600 text-white shadow-sm"
                : "border-indigo-200 bg-indigo-50/70 text-indigo-800 hover:bg-indigo-100"
            }`}
          >
            <Table2 className="h-3.5 w-3.5" />
            {isEn ? "+ Table" : "+ Tableau"}
          </button>

          <button
            type="button"
            aria-pressed={showDiagramForm}
            onClick={() => {
              setShowDiagramForm((v) => !v);
              setShowTableForm(false);
              setShowFigureForm(false);
            }}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
              showDiagramForm
                ? "border-[#6C2826] bg-[#6C2826] text-white shadow-sm"
                : "border-rose-200 bg-rose-50/70 text-[#6C2826] hover:bg-rose-100"
            }`}
          >
            <GitBranch className="h-3.5 w-3.5" />
            {isEn ? "+ Diagram" : "+ Schéma"}
          </button>

          <button
            type="button"
            aria-pressed={showFigureForm}
            onClick={() => {
              setShowFigureForm((v) => !v);
              setShowTableForm(false);
              setShowDiagramForm(false);
            }}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
              showFigureForm
                ? "border-emerald-600 bg-emerald-600 text-white shadow-sm"
                : "border-emerald-200 bg-emerald-50/70 text-emerald-800 hover:bg-emerald-100"
            }`}
          >
            <ImagePlus className="h-3.5 w-3.5" />
            + Figure
          </button>
        </div>
      </div>

      {hasBodyContent && (
        <div className="space-y-4 p-4">
          {/* Add Table Form */}
          {showTableForm && (
            <div className="overflow-hidden rounded-xl border border-indigo-200 bg-white shadow-sm">
              <div className="flex items-center justify-between gap-2 border-b border-indigo-100 bg-indigo-50/60 px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-600 text-white">
                    <Table2 className="h-4 w-4" />
                  </span>
                  <div>
                    <div className="text-xs font-bold text-indigo-950">
                      {isEn
                        ? "New Section Table (Manual or AI-Assisted)"
                        : "Nouveau tableau dans cette section (manuel ou assisté par IA)"}
                    </div>
                    <div className="text-[11px] text-indigo-700">
                      {isEn
                        ? "Define title, placement anchor, and grid cells"
                        : "Définissez l'intitulé, l'emplacement dans le texte et les cellules"}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setShowTableForm(false)}
                  className="rounded-lg p-1 text-slate-500 hover:bg-indigo-100/60 hover:text-slate-700"
                  title={isEn ? "Close" : "Fermer"}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-3.5 p-4">
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-700">
                      {isEn ? "Table Title *" : "Titre du tableau *"}
                    </label>
                    <input
                      type="text"
                      value={tblTitle}
                      onChange={(e) => setTblTitle(e.target.value)}
                      placeholder={
                        isEn
                          ? "E.g. Inclusion and exclusion criteria"
                          : "Ex. Critères d'inclusion et d'exclusion"
                      }
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                    />
                  </div>
                  <div>
                    <label className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-700">
                      <MapPin className="h-3 w-3 text-indigo-600" />
                      {anchorSelectLabel}
                    </label>
                    {renderAnchorSelect(
                      tblAnchor,
                      setTblAnchor,
                      isEn ? "New table placement" : "Emplacement du nouveau tableau"
                    )}
                  </div>
                </div>

                {/* AI Table Assistant Strip */}
                <div className="rounded-lg border border-indigo-100 bg-indigo-50/40 p-3">
                  <div className="flex flex-wrap items-end gap-2">
                    <div className="min-w-[200px] flex-1">
                      <label className="block text-[11px] font-semibold text-indigo-950">
                        {isEn
                          ? "Optional AI instruction (auto-populate columns & rows)"
                          : "Consigne optionnelle pour l'IA (remplissage automatique)"}
                      </label>
                      <input
                        type="text"
                        value={tblGoal}
                        onChange={(e) => setTblGoal(e.target.value)}
                        placeholder={
                          isEn
                            ? "E.g. Compare risk factors across regional studies..."
                            : "Ex. Comparer les facteurs de risque selon les études camerounaises..."
                        }
                        className="mt-1 w-full rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-xs text-slate-900 focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <button
                      type="button"
                      disabled={busySuggestTable}
                      onClick={handleSuggestTable}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50"
                    >
                      {busySuggestTable ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Sparkles className="h-3.5 w-3.5" />
                      )}
                      {isEn ? "Fill with AI" : "Remplir avec l'IA"}
                    </button>
                  </div>
                </div>

                {/* Editable Table Grid */}
                <div className="overflow-x-auto rounded-lg border border-slate-200">
                  <table className="min-w-full border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-100/90">
                        <th className="w-9 border-r border-slate-200 px-2 py-2 text-center text-[10px] font-bold text-slate-500">
                          #
                        </th>
                        {tblHeaders.map((h, ci) => (
                          <th key={ci} className="border-r border-slate-200 p-1">
                            <div className="flex items-center gap-1">
                              <input
                                type="text"
                                value={h}
                                onChange={(e) => {
                                  const next = [...tblHeaders];
                                  next[ci] = e.target.value;
                                  setTblHeaders(next);
                                }}
                                placeholder={
                                  isEn ? `Column ${ci + 1}` : `Colonne ${ci + 1}`
                                }
                                className="w-full rounded bg-transparent px-2 py-1 font-bold text-slate-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                              />
                              {tblHeaders.length > 1 && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setTblHeaders(
                                      tblHeaders.filter((_, idx) => idx !== ci)
                                    );
                                    setTblRows(
                                      tblRows.map((r) =>
                                        r.filter((_, idx) => idx !== ci)
                                      )
                                    );
                                  }}
                                  className="shrink-0 rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                                  title={
                                    isEn
                                      ? "Delete this column"
                                      : "Supprimer cette colonne"
                                  }
                                >
                                  <X className="h-3 w-3" />
                                </button>
                              )}
                            </div>
                          </th>
                        ))}
                        <th className="w-9 p-1" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200">
                      {tblRows.map((row, ri) => (
                        <tr
                          key={ri}
                          className="even:bg-slate-50/50 hover:bg-indigo-50/20"
                        >
                          <td className="border-r border-slate-200 px-2 py-1 text-center text-[11px] font-medium text-slate-400">
                            {ri + 1}
                          </td>
                          {tblHeaders.map((_, ci) => (
                            <td
                              key={ci}
                              className="border-r border-slate-200 p-1"
                            >
                              <input
                                type="text"
                                value={row[ci] ?? ""}
                                onChange={(e) => {
                                  const next = tblRows.map((r) => [...r]);
                                  next[ri][ci] = e.target.value;
                                  setTblRows(next);
                                }}
                                placeholder="—"
                                className="w-full rounded bg-transparent px-2 py-1 text-slate-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                              />
                            </td>
                          ))}
                          <td className="p-1 text-center">
                            <button
                              type="button"
                              onClick={() =>
                                setTblRows(tblRows.filter((_, i) => i !== ri))
                              }
                              className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                              title={
                                isEn ? "Delete row" : "Supprimer cette ligne"
                              }
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3">
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        setTblRows([...tblRows, tblHeaders.map(() => "")])
                      }
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                    >
                      <Plus className="h-3.5 w-3.5 text-indigo-600" />
                      {isEn ? "Add Row" : "Ajouter une ligne"}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setTblHeaders([
                          ...tblHeaders,
                          `${isEn ? "Column" : "Colonne"} ${
                            tblHeaders.length + 1
                          }`,
                        ]);
                        setTblRows(tblRows.map((r) => [...r, ""]));
                      }}
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                    >
                      <Plus className="h-3.5 w-3.5 text-indigo-600" />
                      {isEn ? "Add Column" : "Ajouter une colonne"}
                    </button>
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setShowTableForm(false)}
                      className="rounded-lg border border-slate-300 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                    >
                      {isEn ? "Cancel" : "Annuler"}
                    </button>
                    <button
                      type="button"
                      disabled={busySaveTable}
                      onClick={handleSaveTable}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50"
                    >
                      {busySaveTable ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Check className="h-3.5 w-3.5" />
                      )}
                      {busySaveTable
                        ? isEn
                          ? "Saving..."
                          : "Ajout..."
                        : isEn
                        ? "Save Table"
                        : "Enregistrer le tableau"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Add Figure Form */}
          {showFigureForm && (
            <div className="overflow-hidden rounded-xl border border-emerald-200 bg-white shadow-sm">
              <div className="flex items-center justify-between gap-2 border-b border-emerald-100 bg-emerald-50/60 px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-600 text-white">
                    <ImagePlus className="h-4 w-4" />
                  </span>
                  <div>
                    <div className="text-xs font-bold text-emerald-950">
                      {isEn
                        ? "Import Figure into Section (.png, .jpg, .jpeg, .webp)"
                        : "Importer une figure dans cette section (.png, .jpg, .jpeg, .webp)"}
                    </div>
                    <div className="text-[11px] text-emerald-800">
                      {isEn
                        ? "High-resolution clinical, anatomical, or map illustration"
                        : "Illustration clinique, anatomique, carte ou photographie"}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setShowFigureForm(false)}
                  className="rounded-lg p-1 text-slate-500 hover:bg-emerald-100/60 hover:text-slate-700"
                  title={isEn ? "Close" : "Fermer"}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-3.5 p-4">
                {/* Drag & Drop / Click File Zone with Live Preview */}
                <div
                  onDragOver={(e) => {
                    e.preventDefault();
                    setIsDraggingFig(true);
                  }}
                  onDragLeave={() => setIsDraggingFig(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setIsDraggingFig(false);
                    const f = e.dataTransfer.files?.[0] ?? null;
                    if (f) {
                      setFigFile(f);
                      if (!figTitle) {
                        setFigTitle(f.name.replace(/\.[^.]+$/, ""));
                      }
                    }
                  }}
                  className={`rounded-xl border-2 border-dashed p-4 transition ${
                    isDraggingFig
                      ? "border-emerald-500 bg-emerald-50/70"
                      : figFile
                      ? "border-emerald-300 bg-emerald-50/30"
                      : "border-slate-300 bg-slate-50/60 hover:border-emerald-400 hover:bg-emerald-50/20"
                  }`}
                >
                  {figFile && figPreviewUrl ? (
                    <div className="flex flex-wrap items-center justify-between gap-4">
                      <div className="flex items-center gap-3">
                        <img
                          src={figPreviewUrl}
                          alt={figTitle || figFile.name}
                          className="h-20 w-24 rounded-lg border border-emerald-200 bg-white object-contain p-1 shadow-2xs"
                        />
                        <div className="text-xs">
                          <div className="font-bold text-slate-800">
                            {figFile.name}
                          </div>
                          <div className="mt-0.5 text-[11px] text-slate-500">
                            {Math.max(1, Math.round(figFile.size / 1024))} KB
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <label className="cursor-pointer rounded-lg border border-emerald-300 bg-white px-3 py-1.5 text-xs font-semibold text-emerald-800 hover:bg-emerald-50">
                          {isEn ? "Change image" : "Changer d'image"}
                          <input
                            type="file"
                            accept=".png,.jpg,.jpeg,.webp"
                            onChange={(e) => {
                              const f = e.target.files?.[0] ?? null;
                              if (f) {
                                setFigFile(f);
                                if (!figTitle) {
                                  setFigTitle(f.name.replace(/\.[^.]+$/, ""));
                                }
                              }
                            }}
                            className="hidden"
                          />
                        </label>
                        <button
                          type="button"
                          onClick={() => setFigFile(null)}
                          className="rounded-lg border border-slate-200 bg-white p-1.5 text-slate-500 hover:bg-rose-50 hover:text-rose-600"
                          title={isEn ? "Remove file" : "Retirer le fichier"}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  ) : (
                    <label className="flex cursor-pointer flex-col items-center justify-center py-3 text-center">
                      <span className="mb-2 inline-flex h-10 w-10 items-center justify-center rounded-full bg-emerald-100 text-emerald-700">
                        <Upload className="h-5 w-5" />
                      </span>
                      <span className="text-xs font-semibold text-slate-800">
                        {isEn
                          ? "Click to choose an image or drag and drop it here"
                          : "Cliquez pour choisir une image ou glissez-déposez-la ici"}
                      </span>
                      <span className="mt-0.5 text-[11px] text-slate-500">
                        PNG, JPG, JPEG, WEBP
                      </span>
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
                        className="hidden"
                      />
                    </label>
                  )}
                </div>

                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-700">
                      {isEn ? "Figure Title *" : "Titre de la figure *"}
                    </label>
                    <input
                      type="text"
                      value={figTitle}
                      onChange={(e) => setFigTitle(e.target.value)}
                      placeholder={
                        isEn
                          ? "E.g. Study recruitment flowchart"
                          : "Ex. Diagramme de flux de l'étude"
                      }
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-900 focus:border-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                    />
                  </div>
                  <div>
                    <label className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-700">
                      <MapPin className="h-3 w-3 text-emerald-600" />
                      {anchorSelectLabel}
                    </label>
                    {renderAnchorSelect(
                      figAnchor,
                      setFigAnchor,
                      isEn
                        ? "New figure placement"
                        : "Emplacement de la nouvelle figure"
                    )}
                  </div>
                  <div className="md:col-span-2">
                    <label className="block text-[11px] font-semibold text-slate-700">
                      {isEn
                        ? "Caption / Source (optional)"
                        : "Légende / source (optionnelle)"}
                    </label>
                    <input
                      type="text"
                      value={figCaption}
                      onChange={(e) => setFigCaption(e.target.value)}
                      placeholder={
                        isEn
                          ? "E.g. Adapted from WHO 2024"
                          : "Ex. Adapté de OMS 2024"
                      }
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-900 focus:border-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                    />
                  </div>
                </div>

                <div className="flex justify-end gap-2 border-t border-slate-100 pt-3">
                  <button
                    type="button"
                    onClick={() => setShowFigureForm(false)}
                    className="rounded-lg border border-slate-300 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                  >
                    {isEn ? "Cancel" : "Annuler"}
                  </button>
                  <button
                    type="button"
                    disabled={busyUploadFig || !figFile}
                    onClick={handleUploadFigure}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {busyUploadFig ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Upload className="h-3.5 w-3.5" />
                    )}
                    {isEn ? "Import Figure" : "Importer la figure"}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Add Diagram Form */}
          {showDiagramForm && (
            <div className="overflow-hidden rounded-xl border border-rose-200 bg-white shadow-sm">
              <div className="flex items-center justify-between gap-2 border-b border-rose-100 bg-rose-50/60 px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-[#6C2826] text-white">
                    <GitBranch className="h-4 w-4" />
                  </span>
                  <div>
                    <div className="text-xs font-bold text-rose-950">
                      {isEn
                        ? "New Vector Diagram (Conceptual Framework, Flowchart, Decision Tree)"
                        : "Nouveau schéma vectoriel (Modèle conceptuel, Flux, Arbre décisionnel)"}
                    </div>
                    <div className="text-[11px] text-[#6C2826]">
                      {isEn
                        ? "Build connected blocks manually or generate them with AI"
                        : "Construisez des blocs connectés ou générez-les par IA"}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setShowDiagramForm(false)}
                  className="rounded-lg p-1 text-slate-500 hover:bg-rose-100/60 hover:text-slate-700"
                  title={isEn ? "Close" : "Fermer"}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-3.5 p-4">
                <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-700">
                      {isEn ? "Diagram Title *" : "Titre du schéma *"}
                    </label>
                    <input
                      type="text"
                      value={dgTitle}
                      onChange={(e) => setDgTitle(e.target.value)}
                      placeholder={
                        isEn
                          ? "E.g. Conceptual framework of the study"
                          : "Ex. Modèle conceptuel de l'étude"
                      }
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-900 focus:border-rose-500 focus:outline-none focus:ring-2 focus:ring-rose-500/20"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-700">
                      {isEn ? "Flow Orientation" : "Disposition du flux"}
                    </label>
                    <div className="mt-1 grid grid-cols-2 gap-1.5 rounded-lg border border-slate-200 bg-slate-50 p-1">
                      <button
                        type="button"
                        onClick={() => setDgLayout("vertical")}
                        className={`inline-flex items-center justify-center gap-1 rounded-md px-2 py-1 text-xs font-semibold transition ${
                          dgLayout === "vertical"
                            ? "bg-white text-[#6C2826] shadow-2xs ring-1 ring-slate-200"
                            : "text-slate-600 hover:text-slate-900"
                        }`}
                      >
                        <ArrowDown className="h-3.5 w-3.5" />
                        {isEn ? "Vertical" : "Verticale"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setDgLayout("horizontal")}
                        className={`inline-flex items-center justify-center gap-1 rounded-md px-2 py-1 text-xs font-semibold transition ${
                          dgLayout === "horizontal"
                            ? "bg-white text-[#6C2826] shadow-2xs ring-1 ring-slate-200"
                            : "text-slate-600 hover:text-slate-900"
                        }`}
                      >
                        <ArrowRight className="h-3.5 w-3.5" />
                        {isEn ? "Horizontal" : "Horizontale"}
                      </button>
                    </div>
                  </div>
                  <div>
                    <label className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-700">
                      <MapPin className="h-3 w-3 text-[#6C2826]" />
                      {anchorSelectLabel}
                    </label>
                    {renderAnchorSelect(
                      dgAnchor,
                      setDgAnchor,
                      isEn
                        ? "New diagram placement"
                        : "Emplacement du nouveau schéma"
                    )}
                  </div>
                </div>

                {/* AI Diagram Generator Strip */}
                <div className="rounded-lg border border-rose-100 bg-rose-50/40 p-3">
                  <div className="flex flex-wrap items-end gap-2">
                    <div className="min-w-[220px] flex-1">
                      <label className="block text-[11px] font-semibold text-rose-950">
                        {isEn
                          ? "Optional AI instruction (auto-build connected blocks)"
                          : "Consigne IA optionnelle (génération automatique des blocs)"}
                      </label>
                      <input
                        type="text"
                        value={dgGoal}
                        onChange={(e) => setDgGoal(e.target.value)}
                        placeholder={
                          isEn
                            ? "E.g. Connect preoperative, intraoperative factors and clinical outcomes..."
                            : "Ex. Relier les caractéristiques préopératoires, peropératoires et résultats..."
                        }
                        className="mt-1 w-full rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-xs text-slate-900 focus:border-rose-500 focus:outline-none"
                      />
                    </div>
                    <button
                      type="button"
                      disabled={busySuggestDg}
                      onClick={handleSuggestDiagram}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-[#571f1d] disabled:opacity-50"
                    >
                      {busySuggestDg ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Sparkles className="h-3.5 w-3.5" />
                      )}
                      {isEn
                        ? "Generate Blocks with AI"
                        : "Générer les blocs avec l'IA"}
                    </button>
                  </div>
                </div>

                {/* Visual Flow Block Builder */}
                <div className="space-y-1.5">
                  {dgNodes.map((node, idx) => (
                    <div key={node.id}>
                      <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-100 px-2.5 py-0.5 text-[11px] font-bold text-[#6C2826]">
                            {isEn ? `Block ${idx + 1}` : `Bloc ${idx + 1}`}
                          </span>
                          <button
                            type="button"
                            onClick={() => {
                              setDgNodes(dgNodes.filter((_, i) => i !== idx));
                              setDgConns(
                                dgConns.filter(
                                  (c) =>
                                    c.from_id !== node.id && c.to_id !== node.id
                                )
                              );
                            }}
                            className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium text-rose-600 hover:bg-rose-50 hover:text-rose-800"
                            title={
                              isEn ? "Remove block" : "Supprimer ce bloc"
                            }
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            {isEn ? "Remove" : "Retirer"}
                          </button>
                        </div>

                        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-12">
                          <div className="md:col-span-3">
                            <label className="block text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                              {isEn ? "Shape" : "Forme"}
                            </label>
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
                              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                            >
                              <option value="rounded">
                                {isEn ? "Rounded box" : "Rectangle arrondi"}
                              </option>
                              <option value="rectangle">
                                {isEn ? "Square box" : "Rectangle carré"}
                              </option>
                              <option value="circle">
                                {isEn ? "Circle" : "Cercle"}
                              </option>
                              <option value="diamond">
                                {isEn ? "Diamond" : "Losange"}
                              </option>
                            </select>
                          </div>

                          <div className="md:col-span-4">
                            <label className="block text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                              {isEn ? "Block Heading" : "Titre du bloc"}
                            </label>
                            <input
                              type="text"
                              value={node.title}
                              onChange={(e) => {
                                const next = [...dgNodes];
                                next[idx] = { ...node, title: e.target.value };
                                setDgNodes(next);
                              }}
                              placeholder={
                                isEn ? "Block title" : "Titre du bloc"
                              }
                              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-900"
                            />
                          </div>

                          <div className="md:col-span-5">
                            <label className="block text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                              {isEn
                                ? "Bullet items (separated by ;)"
                                : "Éléments internes (séparés par ;)"}
                            </label>
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
                              placeholder={
                                isEn
                                  ? "Item 1 ; Item 2"
                                  : "Élément 1 ; Élément 2"
                              }
                              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-800"
                            />
                          </div>
                        </div>
                      </div>

                      {idx < dgNodes.length - 1 && (
                        <div className="flex justify-center py-0.5 text-slate-400">
                          {dgLayout === "horizontal" ? (
                            <ArrowRight className="h-4 w-4" />
                          ) : (
                            <ArrowDown className="h-4 w-4" />
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3">
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
                          title: isEn ? "New Block" : "Nouveau bloc",
                          items: [isEn ? "Item 1" : "Élément 1"],
                        },
                      ]);
                      if (prev) {
                        setDgConns([
                          ...dgConns,
                          { from_id: prev.id, to_id: newId, label: "" },
                        ]);
                      }
                    }}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                  >
                    <Plus className="h-3.5 w-3.5 text-[#6C2826]" />
                    {isEn
                      ? "Add Connected Block"
                      : "Ajouter un bloc connecté"}
                  </button>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setShowDiagramForm(false)}
                      className="rounded-lg border border-slate-300 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                    >
                      {isEn ? "Cancel" : "Annuler"}
                    </button>
                    <button
                      type="button"
                      disabled={busySaveDg}
                      onClick={handleSaveDiagram}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-4 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-[#571f1d] disabled:opacity-50"
                    >
                      {busySaveDg ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Check className="h-3.5 w-3.5" />
                      )}
                      {busySaveDg
                        ? isEn
                          ? "Saving..."
                          : "Ajout..."
                        : isEn
                        ? "Save Diagram"
                        : "Enregistrer le schéma"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Existing Effective Tables in Section */}
          {effectiveTables.length > 0 && (
            <div className="space-y-2.5">
              {effectiveTables.map((tbl) => {
                const isEditing = editingTableId === tbl.table_id;
                return (
                  <div
                    key={tbl.table_id}
                    className="rounded-xl border border-slate-200 bg-white p-3.5 text-xs shadow-2xs"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="inline-flex items-center gap-1 rounded-md bg-indigo-50 px-2 py-0.5 text-[11px] font-bold text-indigo-700 ring-1 ring-indigo-200/80">
                            <Table2 className="h-3 w-3" />
                            {isEn ? "Table" : "Tableau"}
                          </span>
                          <span className="font-bold text-slate-900">
                            {tbl.title}
                          </span>
                          {tbl.isStructured && (
                            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-800 ring-1 ring-amber-200">
                              {isEn ? "FMSB Structured" : "Structuré FMSB"}
                            </span>
                          )}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                          <span>
                            {tbl.colsCount} col. × {tbl.rowsCount}{" "}
                            {isEn ? "rows" : "lig."}
                          </span>
                          <span>·</span>
                          <span className="inline-flex items-center gap-1">
                            <MapPin className="h-3 w-3 text-slate-400" />
                            {tbl.anchor_hint
                              ? `${isEn ? "After" : "Après"} « ${truncateDisplay(
                                  tbl.anchor_hint,
                                  48
                                )} »`
                              : isEn
                              ? "End of section"
                              : "À la fin de la section"}
                          </span>
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
                          className="inline-flex items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50/60 px-2.5 py-1 text-indigo-700 hover:bg-indigo-100"
                          title={
                            isEn
                              ? "Edit table title, placement, or cells"
                              : "Modifier le nom, l'emplacement ou le contenu du tableau"
                          }
                        >
                          <Pencil className="h-3.5 w-3.5" />
                          <span className="text-[11px] font-semibold">
                            {isEditing
                              ? isEn
                                ? "Close"
                                : "Fermer"
                              : isEn
                              ? "Edit"
                              : "Modifier"}
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
                                err instanceof Error
                                  ? err.message
                                  : String(err)
                              );
                            }
                          }}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50/50 px-2.5 py-1 text-rose-600 hover:bg-rose-100 hover:text-rose-800"
                          title={
                            isEn
                              ? "Delete this table"
                              : "Supprimer ce tableau du Module 1 et du Module 2"
                          }
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          <span className="text-[11px] font-semibold">
                            {isEn ? "Delete" : "Supprimer"}
                          </span>
                        </button>
                      </div>
                    </div>

                    {/* Read-only compact table preview when collapsed */}
                    {!isEditing && tbl.headers.length > 0 && (
                      <div className="mt-2.5 max-h-36 overflow-auto rounded-lg border border-slate-200/80">
                        <table className="min-w-full border-collapse text-[11px]">
                          <thead>
                            <tr className="bg-slate-50 text-left font-semibold text-slate-700">
                              {tbl.headers.map((h, ci) => (
                                <th
                                  key={ci}
                                  className="border-b border-r border-slate-200/80 px-2.5 py-1.5 last:border-r-0"
                                >
                                  {h}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-100">
                            {tbl.rows.slice(0, 4).map((r, ri) => (
                              <tr key={ri} className="even:bg-slate-50/40">
                                {tbl.headers.map((_, ci) => (
                                  <td
                                    key={ci}
                                    className="border-r border-slate-100 px-2.5 py-1 text-slate-600 last:border-r-0"
                                  >
                                    {truncateDisplay(r[ci] ?? "—", 52)}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {isEditing && (
                      <div className="mt-3 space-y-3 border-t border-slate-200 pt-3">
                        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                          <div>
                            <label className="block text-[11px] font-semibold text-slate-700">
                              {isEn
                                ? "Table Name / Title"
                                : "Nom / Titre du tableau"}
                            </label>
                            <input
                              type="text"
                              value={editTblTitle}
                              onChange={(e) => setEditTblTitle(e.target.value)}
                              className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                            />
                          </div>
                          <div>
                            <label className="block text-[11px] font-semibold text-slate-700">
                              {anchorSelectLabel}
                            </label>
                            {renderAnchorSelect(
                              editTblAnchor,
                              setEditTblAnchor,
                              isEn ? "Table placement" : "Emplacement du tableau"
                            )}
                          </div>
                        </div>

                        {editTblHeaders.length > 0 && (
                          <div className="overflow-x-auto rounded-lg border border-slate-200">
                            <table className="min-w-full border-collapse text-xs">
                              <thead>
                                <tr className="border-b border-slate-200 bg-slate-100">
                                  {editTblHeaders.map((h, ci) => (
                                    <th
                                      key={ci}
                                      className="border-r border-slate-200 p-1"
                                    >
                                      <div className="flex items-center gap-1">
                                        <input
                                          type="text"
                                          value={h}
                                          onChange={(e) => {
                                            const next = [...editTblHeaders];
                                            next[ci] = e.target.value;
                                            setEditTblHeaders(next);
                                          }}
                                          className="w-full bg-transparent px-1.5 py-0.5 font-bold focus:bg-white focus:outline-none"
                                        />
                                        {editTblHeaders.length > 1 && (
                                          <button
                                            type="button"
                                            onClick={() => {
                                              setEditTblHeaders(
                                                editTblHeaders.filter(
                                                  (_, idx) => idx !== ci
                                                )
                                              );
                                              setEditTblRows(
                                                editTblRows.map((row) =>
                                                  row.filter(
                                                    (_, idx) => idx !== ci
                                                  )
                                                )
                                              );
                                            }}
                                            className="rounded p-0.5 text-slate-400 hover:text-rose-600"
                                            title={
                                              isEn
                                                ? "Delete column"
                                                : "Supprimer colonne"
                                            }
                                          >
                                            <X className="h-3 w-3" />
                                          </button>
                                        )}
                                      </div>
                                    </th>
                                  ))}
                                  <th className="w-8 p-1" />
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-200">
                                {editTblRows.map((r, ri) => (
                                  <tr key={ri} className="even:bg-slate-50/50">
                                    {editTblHeaders.map((_, ci) => (
                                      <td
                                        key={ci}
                                        className="border-r border-slate-200 p-1"
                                      >
                                        <input
                                          type="text"
                                          value={r[ci] ?? ""}
                                          onChange={(e) => {
                                            const next = editTblRows.map(
                                              (row) => [...row]
                                            );
                                            next[ri][ci] = e.target.value;
                                            setEditTblRows(next);
                                          }}
                                          className="w-full bg-transparent px-1.5 py-0.5 focus:bg-white focus:outline-none"
                                        />
                                      </td>
                                    ))}
                                    <td className="p-1 text-center">
                                      <button
                                        type="button"
                                        onClick={() =>
                                          setEditTblRows(
                                            editTblRows.filter(
                                              (_, idx) => idx !== ri
                                            )
                                          )
                                        }
                                        className="rounded p-0.5 text-slate-400 hover:text-rose-600"
                                        title={
                                          isEn
                                            ? "Delete row"
                                            : "Supprimer ligne"
                                        }
                                      >
                                        <Trash2 className="h-3.5 w-3.5" />
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}

                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex gap-2">
                            <button
                              type="button"
                              onClick={() =>
                                setEditTblRows([
                                  ...editTblRows,
                                  editTblHeaders.map(() => ""),
                                ])
                              }
                              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
                            >
                              <Plus className="h-3 w-3" />{" "}
                              {isEn ? "Row" : "Ligne"}
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setEditTblHeaders([
                                  ...editTblHeaders,
                                  `${isEn ? "Column" : "Colonne"} ${
                                    editTblHeaders.length + 1
                                  }`,
                                ]);
                                setEditTblRows(
                                  editTblRows.map((r) => [...r, ""])
                                );
                              }}
                              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
                            >
                              <Plus className="h-3 w-3" />{" "}
                              {isEn ? "Column" : "Colonne"}
                            </button>
                          </div>
                          <div className="flex gap-2">
                            <button
                              type="button"
                              onClick={() => setEditingTableId(null)}
                              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                            >
                              <X className="h-3.5 w-3.5" />
                              {isEn ? "Cancel" : "Annuler"}
                            </button>
                            <button
                              type="button"
                              disabled={busyUpdateTable}
                              onClick={() =>
                                handleUpdateExistingTable(tbl.table_id)
                              }
                              className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                            >
                              {busyUpdateTable ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Check className="h-3.5 w-3.5" />
                              )}
                              {isEn ? "Save" : "Enregistrer"}
                            </button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Existing Figures in Section */}
          {sectionImages.length > 0 && (
            <div className="space-y-2.5">
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
                    className="rounded-xl border border-slate-200 bg-white p-3.5 text-xs shadow-2xs"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex min-w-0 flex-1 items-center gap-3">
                        {imgSrc && (
                          <img
                            src={imgSrc}
                            alt={img.title}
                            className="h-16 w-16 shrink-0 rounded-lg border border-slate-200 bg-slate-50 object-cover"
                          />
                        )}
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 text-[11px] font-bold text-emerald-700 ring-1 ring-emerald-200/80">
                              <ImagePlus className="h-3 w-3" />
                              Figure
                            </span>
                            <span className="truncate font-bold text-slate-900">
                              {img.title}
                            </span>
                          </div>
                          <div className="mt-1 truncate text-[11px] text-slate-500">
                            {img.caption ||
                              (isEn ? "No caption" : "Sans légende")}{" "}
                            ·{" "}
                            <span className="inline-flex items-center gap-0.5">
                              <MapPin className="inline h-3 w-3 text-slate-400" />
                              {img.anchor_hint
                                ? `${isEn ? "After" : "Après"} « ${truncateDisplay(
                                    img.anchor_hint,
                                    48
                                  )} »`
                                : isEn
                                ? "End of section"
                                : "À la fin de la section"}
                            </span>
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
                          className="inline-flex items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50/60 px-2.5 py-1 text-indigo-700 hover:bg-indigo-100"
                          title={
                            isEn
                              ? "Edit figure title, caption, or placement"
                              : "Modifier le nom, la légende ou l'emplacement de la figure"
                          }
                        >
                          <Pencil className="h-3.5 w-3.5" />
                          <span className="text-[11px] font-semibold">
                            {isEditing
                              ? isEn
                                ? "Close"
                                : "Fermer"
                              : isEn
                              ? "Edit"
                              : "Modifier"}
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
                                err instanceof Error
                                  ? err.message
                                  : String(err)
                              );
                            }
                          }}
                          className={`rounded-lg px-2.5 py-1 text-[11px] font-semibold ${
                            img.approved
                              ? "bg-emerald-100 text-emerald-800"
                              : "bg-slate-100 text-slate-600"
                          }`}
                        >
                          {img.approved
                            ? isEn
                              ? "Included (Module 2)"
                              : "Incluse (Module 2)"
                            : isEn
                            ? "Excluded"
                            : "Exclue"}
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
                                err instanceof Error
                                  ? err.message
                                  : String(err)
                              );
                            }
                          }}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50/50 px-2 py-1 text-rose-600 hover:bg-rose-100 hover:text-rose-800"
                          title={
                            isEn
                              ? "Delete this figure"
                              : "Supprimer cette figure"
                          }
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>

                    {isEditing && (
                      <div className="mt-3 space-y-2.5 border-t border-slate-200 pt-3">
                        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                          <div>
                            <label className="block text-[11px] font-semibold text-slate-700">
                              {isEn
                                ? "Figure Name / Title"
                                : "Nom / Titre de la figure"}
                            </label>
                            <input
                              type="text"
                              value={editImgTitle}
                              onChange={(e) => setEditImgTitle(e.target.value)}
                              className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                            />
                          </div>
                          <div>
                            <label className="block text-[11px] font-semibold text-slate-700">
                              {anchorSelectLabel}
                            </label>
                            {renderAnchorSelect(
                              editImgAnchor,
                              setEditImgAnchor,
                              isEn
                                ? "Figure placement"
                                : "Emplacement de la figure"
                            )}
                          </div>
                          <div className="md:col-span-2">
                            <label className="block text-[11px] font-semibold text-slate-700">
                              {isEn ? "Caption / Source" : "Légende / Source"}
                            </label>
                            <input
                              type="text"
                              value={editImgCaption}
                              onChange={(e) =>
                                setEditImgCaption(e.target.value)
                              }
                              placeholder={
                                isEn
                                  ? "E.g. Adapted from WHO 2024"
                                  : "Ex. Adapté de OMS 2024"
                              }
                              className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                            />
                          </div>
                        </div>
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => setEditingImageId(null)}
                            className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                          >
                            <X className="h-3.5 w-3.5" />
                            {isEn ? "Cancel" : "Annuler"}
                          </button>
                          <button
                            type="button"
                            disabled={busyUpdateImage}
                            onClick={() =>
                              handleUpdateExistingImage(img.asset_id)
                            }
                            className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                          >
                            {busyUpdateImage ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Check className="h-3.5 w-3.5" />
                            )}
                            {isEn ? "Save" : "Enregistrer"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Existing Vector Diagrams in Section */}
          {customDiagrams.length > 0 && (
            <div className="space-y-2.5">
              {customDiagrams.map((dg) => {
                const isEditing = editingDiagramId === dg.diagram_id;
                return (
                  <div
                    key={dg.diagram_id}
                    className="rounded-xl border border-slate-200 bg-white p-3.5 text-xs shadow-2xs"
                  >
                    <div className="mb-2.5 flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="inline-flex items-center gap-1 rounded-md bg-rose-50 px-2 py-0.5 text-[11px] font-bold text-[#6C2826] ring-1 ring-rose-200/80">
                            <GitBranch className="h-3 w-3" />
                            {isEn ? "Vector Diagram" : "Schéma vectoriel"}
                          </span>
                          <span className="font-bold text-slate-900">
                            {dg.title}
                          </span>
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-600">
                            {dg.nodes.length} {isEn ? "blocks" : "blocs"}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-500">
                          <MapPin className="h-3 w-3 text-slate-400" />
                          {dg.anchor_hint
                            ? `${isEn ? "After" : "Après"} « ${truncateDisplay(
                                dg.anchor_hint,
                                48
                              )} »`
                            : isEn
                            ? "End of section"
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
                          className="inline-flex items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50/60 px-2.5 py-1 text-indigo-700 hover:bg-indigo-100"
                          title={
                            isEn
                              ? "Edit diagram title, placement, or blocks"
                              : "Modifier le nom, l'emplacement ou les blocs de ce schéma"
                          }
                        >
                          <Pencil className="h-3.5 w-3.5" />
                          <span className="text-[11px] font-semibold">
                            {isEditing
                              ? isEn
                                ? "Close"
                                : "Fermer"
                              : isEn
                              ? "Edit"
                              : "Modifier"}
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
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50/50 px-2.5 py-1 text-rose-600 hover:bg-rose-100 hover:text-rose-800"
                          title={
                            isEn
                              ? "Delete this diagram"
                              : "Supprimer ce schéma"
                          }
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          <span className="text-[11px] font-semibold">
                            {isEn ? "Delete" : "Supprimer"}
                          </span>
                        </button>
                      </div>
                    </div>

                    {isEditing && (
                      <div className="mb-3 space-y-3 border-t border-b border-slate-200 py-3">
                        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
                          <div>
                            <label className="block text-[11px] font-semibold text-slate-700">
                              {isEn
                                ? "Diagram Name / Title"
                                : "Nom / Titre du schéma"}
                            </label>
                            <input
                              type="text"
                              value={editDgTitle}
                              onChange={(e) => setEditDgTitle(e.target.value)}
                              className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                            />
                          </div>
                          <div>
                            <label className="block text-[11px] font-semibold text-slate-700">
                              {isEn ? "Orientation" : "Disposition"}
                            </label>
                            <select
                              value={editDgLayout}
                              onChange={(e) =>
                                setEditDgLayout(
                                  e.target.value as "vertical" | "horizontal"
                                )
                              }
                              className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                            >
                              <option value="vertical">
                                {isEn
                                  ? "Vertical (top → bottom)"
                                  : "Verticale (haut → bas)"}
                              </option>
                              <option value="horizontal">
                                {isEn
                                  ? "Horizontal (left → right)"
                                  : "Horizontale (gauche → droite)"}
                              </option>
                            </select>
                          </div>
                          <div>
                            <label className="block text-[11px] font-semibold text-slate-700">
                              {anchorSelectLabel}
                            </label>
                            {renderAnchorSelect(
                              editDgAnchor,
                              setEditDgAnchor,
                              isEn
                                ? "Diagram placement"
                                : "Emplacement du schéma"
                            )}
                          </div>
                          <div className="md:col-span-3">
                            <label className="block text-[11px] font-semibold text-slate-700">
                              {isEn
                                ? "Diagram Caption (optional)"
                                : "Légende du schéma (optionnelle)"}
                            </label>
                            <input
                              type="text"
                              value={editDgCaption}
                              onChange={(e) => setEditDgCaption(e.target.value)}
                              className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                            />
                          </div>
                        </div>

                        <div className="space-y-2">
                          {editDgNodes.map((node, nIdx) => (
                            <div
                              key={node.id}
                              className="grid grid-cols-1 gap-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5 md:grid-cols-12"
                            >
                              <select
                                value={node.shape}
                                onChange={(e) => {
                                  const next = [...editDgNodes];
                                  next[nIdx] = {
                                    ...node,
                                    shape: e.target
                                      .value as DiagramNode["shape"],
                                  };
                                  setEditDgNodes(next);
                                }}
                                className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs md:col-span-3"
                              >
                                <option value="rounded">
                                  {isEn ? "Rounded box" : "Rectangle arrondi"}
                                </option>
                                <option value="rectangle">
                                  {isEn ? "Square box" : "Rectangle carré"}
                                </option>
                                <option value="circle">
                                  {isEn ? "Circle" : "Cercle"}
                                </option>
                                <option value="diamond">
                                  {isEn ? "Diamond" : "Losange"}
                                </option>
                              </select>
                              <input
                                type="text"
                                value={node.title}
                                onChange={(e) => {
                                  const next = [...editDgNodes];
                                  next[nIdx] = {
                                    ...node,
                                    title: e.target.value,
                                  };
                                  setEditDgNodes(next);
                                }}
                                className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-semibold md:col-span-4"
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
                                className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs md:col-span-4"
                              />
                              <button
                                type="button"
                                disabled={editDgNodes.length <= 1}
                                onClick={() =>
                                  setEditDgNodes(
                                    editDgNodes.filter((_, i) => i !== nIdx)
                                  )
                                }
                                className="flex items-center justify-center text-rose-600 hover:text-rose-800 disabled:opacity-30 md:col-span-1"
                                title={
                                  isEn ? "Remove block" : "Supprimer ce bloc"
                                }
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          ))}
                        </div>

                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              setEditDgNodes([
                                ...editDgNodes,
                                {
                                  id: `n${editDgNodes.length + 1}_${Date.now()
                                    .toString()
                                    .slice(-3)}`,
                                  shape: "rounded",
                                  title: isEn ? "New Block" : "Nouveau bloc",
                                  items: [isEn ? "Item 1" : "Élément 1"],
                                },
                              ])
                            }
                            className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
                          >
                            <Plus className="h-3 w-3" />{" "}
                            {isEn ? "Add Block" : "Ajouter un bloc"}
                          </button>
                          <div className="flex gap-2">
                            <button
                              type="button"
                              onClick={() => setEditingDiagramId(null)}
                              className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                            >
                              <X className="h-3.5 w-3.5" />
                              {isEn ? "Cancel" : "Annuler"}
                            </button>
                            <button
                              type="button"
                              disabled={busyUpdateDiagram}
                              onClick={() =>
                                handleUpdateExistingDiagram(dg.diagram_id)
                              }
                              className="inline-flex items-center gap-1 rounded-lg bg-[#6C2826] px-3 py-1 text-xs font-semibold text-white hover:bg-[#571f1d] disabled:opacity-50"
                            >
                              {busyUpdateDiagram ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Check className="h-3.5 w-3.5" />
                              )}
                              {isEn ? "Save" : "Enregistrer"}
                            </button>
                          </div>
                        </div>
                      </div>
                    )}

                    <div className="rounded-lg border border-slate-100 bg-slate-50/50 p-2">
                      <img
                        src={api.diagramSvgUrl(
                          project.project_id,
                          sectionId,
                          dg.diagram_id
                        )}
                        alt={dg.title}
                        className="mx-auto max-h-56 max-w-md rounded bg-white object-contain p-1.5 shadow-2xs"
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
