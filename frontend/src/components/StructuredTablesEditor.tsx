import { useEffect, useState } from "react";
import {
  Calendar,
  CheckCircle2,
  DollarSign,
  FileCheck,
  FileText,
  Image as ImageIcon,
  ListFilter,
  Plus,
  Table as TableIcon,
  Trash2,
} from "lucide-react";
import { api } from "../api/client";
import { useLanguage } from "../i18n/LanguageContext";
import type {
  AbbreviationItem,
  AnnexesConfig,
  BudgetItem,
  CanvasSectionSpec,
  LiteratureSynthesisItem,
  ProtocolProject,
  TimelineGrid,
  VariableItem,
} from "../types/protocol";
import { TimelineGridEditor } from "./TimelineGridEditor";

interface StructuredTablesEditorProps {
  project: ProtocolProject;
  canvas?: CanvasSectionSpec[];
  onProjectUpdated: (updated: ProtocolProject) => void;
  onError: (msg: string) => void;
}

export function StructuredTablesEditor({
  project,
  canvas,
  onProjectUpdated,
  onError,
}: StructuredTablesEditorProps) {
  const { lang } = useLanguage();
  const isEn = lang === "en";
  const [activeTab, setActiveTab] = useState<
    "abbreviations" | "variables" | "synthesis" | "timeline" | "budget" | "annexes" | "figures"
  >("variables");
  const [figUploadFile, setFigUploadFile] = useState<File | null>(null);
  const [figUploadSection, setFigUploadSection] = useState("chap1_justification");
  const [figUploadTitle, setFigUploadTitle] = useState("");
  const [figUploadCaption, setFigUploadCaption] = useState("");

  const [abbreviations, setAbbreviations] = useState<AbbreviationItem[]>(
    project.abbreviations || []
  );
  const [variables, setVariables] = useState<VariableItem[]>(
    project.variables || []
  );
  const [synthesis, setSynthesis] = useState<LiteratureSynthesisItem[]>(
    project.literature_synthesis || []
  );
  const [timelineGrid, setTimelineGrid] = useState<TimelineGrid>(
    project.timeline_grid || {
      periods: ["Mai 2026", "Juil 2026", "Oct 2026", "Mars 2027", "Avril 2027", "Mai 2027", "Juin 2027"],
      rows: [
        { activity: "Rédaction du protocole", active_periods: ["Mai 2026"] },
        { activity: "Obtention des autorisations & clairance", active_periods: ["Juil 2026"] },
        { activity: "Collecte des données", active_periods: ["Oct 2026", "Mars 2027"] },
        { activity: "Analyse des données", active_periods: ["Avril 2027"] },
        { activity: "Rédaction de thèse et corrections", active_periods: ["Mai 2027"] },
        { activity: "Dépôt et soutenance", active_periods: ["Juin 2027"] },
      ],
    }
  );
  const [budget, setBudget] = useState<BudgetItem[]>(
    project.budget || [
      { category: "Matériel", designation: "Impression du protocole", quantity: 10, unit_cost_fcfa: 5000, total_fcfa: 50000 },
      { category: "Logistique", designation: "Frais d'évaluation éthique", quantity: 1, unit_cost_fcfa: 25000, total_fcfa: 25000 },
      { category: "Logistique", designation: "Autorisations de recherche", quantity: 2, unit_cost_fcfa: 20000, total_fcfa: 40000 },
      { category: "Collecte", designation: "Photocopie fiches de collecte", quantity: 100, unit_cost_fcfa: 250, total_fcfa: 25000 },
      { category: "Statistiques", designation: "Analyse statistique avancée", quantity: 1, unit_cost_fcfa: 100000, total_fcfa: 100000 },
    ]
  );
  const [annexesConfig, setAnnexesConfig] = useState<AnnexesConfig>(
    project.annexes_config || {
      study_goal_short: "",
      include_data_collection_form: true,
      include_info_notice: true,
      include_consent_form: true,
      include_ethics_clearance_request: true,
      include_research_auth_requests: true,
      include_confidentiality_declaration: true,
      include_commitment_letter: true,
    }
  );

  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (project.timeline_grid) {
      setTimelineGrid(project.timeline_grid);
    }
  }, [project.timeline_grid]);

  useEffect(() => {
    if (project.budget) {
      setBudget(project.budget);
    }
  }, [project.budget]);

  async function handleSaveStructured(customPayload?: Record<string, any>) {
    setSaving(true);
    onError("");
    try {
      const payload = {
        abbreviations,
        variables,
        literature_synthesis: synthesis,
        timeline_grid: timelineGrid,
        budget,
        annexes_config: annexesConfig,
        ...(customPayload || {}),
      };
      const updated = await api.updateStructuredData(project.project_id, payload);
      onProjectUpdated(updated);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const grandTotalFCFA = budget.reduce(
    (sum, b) => sum + (b.total_fcfa > 0 ? b.total_fcfa : b.quantity * b.unit_cost_fcfa),
    0
  );

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
        <div>
          <h3 className="text-base font-bold text-slate-900">
            {isEn ? "Official FMSB Tables & Appendices" : "Tableaux & Annexes officiels FMSB"}
          </h3>
          <p className="text-xs text-slate-500">
            {isEn
              ? "These structures are automatically compiled into official Word and PDF styles."
              : "Ces structures sont automatiquement compilées dans les styles officiels Word et PDF."}
          </p>
        </div>
        <button
          type="button"
          disabled={saving}
          onClick={() => handleSaveStructured()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          <CheckCircle2 className="h-4 w-4" />
          {saving
            ? isEn
              ? "Saving..."
              : "Sauvegarde..."
            : isEn
            ? "Save all tables"
            : "Enregistrer tous les tableaux"}
        </button>
      </div>

      {/* Tabs */}
      <div className="mt-4 flex flex-wrap gap-2 border-b border-slate-100 pb-3">
        <button
          type="button"
          onClick={() => setActiveTab("variables")}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
            activeTab === "variables"
              ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
              : "text-slate-600 hover:bg-slate-50"
          }`}
        >
          <TableIcon className="h-3.5 w-3.5" />
          {isEn ? `1. Variables & CRF (${variables.length})` : `1. Variables & Fiche de collecte (${variables.length})`}
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("synthesis")}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
            activeTab === "synthesis"
              ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
              : "text-slate-600 hover:bg-slate-50"
          }`}
        >
          <ListFilter className="h-3.5 w-3.5" />
          {isEn ? `2. Literature synthesis (${synthesis.length})` : `2. Synthèse littérature (${synthesis.length})`}
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("timeline")}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
            activeTab === "timeline"
              ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
              : "text-slate-600 hover:bg-slate-50"
          }`}
        >
          <Calendar className="h-3.5 w-3.5" />
          {isEn ? "3. Gantt Timeline" : "3. Chronogramme Gantt"}
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("budget")}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
            activeTab === "budget"
              ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
              : "text-slate-600 hover:bg-slate-50"
          }`}
        >
          <DollarSign className="h-3.5 w-3.5" />
          {isEn
            ? `4. Provisional Budget (${grandTotalFCFA.toLocaleString("en-US")} FCFA)`
            : `4. Budget prévisionnel (${grandTotalFCFA.toLocaleString("fr-FR")} FCFA)`}
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("abbreviations")}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
            activeTab === "abbreviations"
              ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
              : "text-slate-600 hover:bg-slate-50"
          }`}
        >
          <FileText className="h-3.5 w-3.5" />
          {isEn ? `5. Abbreviations (${abbreviations.length})` : `5. Abréviations (${abbreviations.length})`}
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("annexes")}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
            activeTab === "annexes"
              ? "bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200"
              : "text-slate-600 hover:bg-slate-50"
          }`}
        >
          <FileCheck className="h-3.5 w-3.5 text-emerald-600" />
          {isEn ? "6. Appendices Pack (Notice, Ethics, etc.)" : "6. Pack Annexes FMSB (Notice, Éthique, etc.)"}
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("figures")}
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
            activeTab === "figures"
              ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
              : "text-slate-600 hover:bg-slate-50"
          }`}
        >
          <ImageIcon className="h-3.5 w-3.5" />
          {isEn ? `7. Figures & Images (${project.images.length})` : `7. Figures & Images (${project.images.length})`}
        </button>
      </div>

      {/* 1. Variables & Fiche de collecte */}
      {activeTab === "variables" && (
        <div className="mt-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-600">
              {isEn ? (
                <>
                  Table inserted in <strong>Chapter III (Section III.3)</strong> and used as the basis for <strong>Appendix 3: Data Collection Form</strong>.
                </>
              ) : (
                <>
                  Tableau inséré dans <strong>Chapitre III (Section III.3)</strong> et servant de base à l&apos;<strong>Annexe 3 : Fiche de collecte</strong>.
                </>
              )}
            </span>
            <button
              type="button"
              onClick={() =>
                setVariables([
                  ...variables,
                  {
                    name: isEn ? "New variable" : "Nouvelle variable",
                    category: isEn ? "Clinical data" : "Données cliniques",
                    role: "Indépendante",
                    definition: "",
                    measurement_scale: "Qualitative nominale",
                    modalities: isEn ? "[ ] Yes   [ ] No" : "[ ] Oui   [ ] Non",
                    collection_tool: isEn ? "Data collection form" : "Fiche de collecte",
                  },
                ])
              }
              className="inline-flex items-center gap-1 rounded bg-slate-800 px-2.5 py-1 text-xs font-medium text-white hover:bg-slate-900"
            >
              <Plus className="h-3 w-3" /> {isEn ? "Add variable" : "Ajouter une variable"}
            </button>
          </div>

          <div className="max-h-96 overflow-x-auto overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-100 text-slate-700">
                <tr>
                  <th className="p-2">{isEn ? "Variable" : "Variable"}</th>
                  <th className="p-2">{isEn ? "Category" : "Catégorie"}</th>
                  <th className="p-2">{isEn ? "Role" : "Rôle"}</th>
                  <th className="p-2">{isEn ? "Scale" : "Échelle"}</th>
                  <th className="p-2">{isEn ? "Modalities / Values" : "Modalités / Valeurs"}</th>
                  <th className="p-2 w-10"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {variables.map((v, i) => (
                  <tr key={i}>
                    <td className="p-1.5">
                      <input
                        type="text"
                        value={v.name}
                        onChange={(e) => {
                          const copy = [...variables];
                          copy[i].name = e.target.value;
                          setVariables(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5">
                      <input
                        type="text"
                        value={v.category}
                        onChange={(e) => {
                          const copy = [...variables];
                          copy[i].category = e.target.value;
                          setVariables(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5">
                      <select
                        value={v.role}
                        onChange={(e) => {
                          const copy = [...variables];
                          copy[i].role = e.target.value;
                          setVariables(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      >
                        <option value="Dépendante">{isEn ? "Dependent" : "Dépendante"}</option>
                        <option value="Indépendante">{isEn ? "Independent" : "Indépendante"}</option>
                        <option value="Facteur de confusion">{isEn ? "Confounder" : "Facteur de confusion"}</option>
                      </select>
                    </td>
                    <td className="p-1.5">
                      <select
                        value={v.measurement_scale}
                        onChange={(e) => {
                          const copy = [...variables];
                          copy[i].measurement_scale = e.target.value;
                          setVariables(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      >
                        <option value="Qualitative nominale">{isEn ? "Nominal qualitative" : "Qualitative nominale"}</option>
                        <option value="Qualitative ordinale">{isEn ? "Ordinal qualitative" : "Qualitative ordinale"}</option>
                        <option value="Quantitative continue">{isEn ? "Continuous quantitative" : "Quantitative continue"}</option>
                        <option value="Quantitative discrète">{isEn ? "Discrete quantitative" : "Quantitative discrète"}</option>
                      </select>
                    </td>
                    <td className="p-1.5">
                      <input
                        type="text"
                        value={v.modalities}
                        onChange={(e) => {
                          const copy = [...variables];
                          copy[i].modalities = e.target.value;
                          setVariables(copy);
                        }}
                        placeholder={isEn ? "e.g. [ ] Yes [ ] No" : "Ex: [ ] Oui [ ] Non"}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5 text-center">
                      <button
                        type="button"
                        onClick={() => setVariables(variables.filter((_, idx) => idx !== i))}
                        className="text-rose-500 hover:text-rose-700"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 2. Synthèse littérature */}
      {activeTab === "synthesis" && (
        <div className="mt-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-600">
              {isEn ? (
                <>Summary table of previous studies inserted at the end of <strong>Chapter II (Section II.2)</strong>.</>
              ) : (
                <>Tableau de synthèse des études antérieures inséré en fin de <strong>Chapitre II (Section II.2)</strong>.</>
              )}
            </span>
            <button
              type="button"
              onClick={() =>
                setSynthesis([
                  ...synthesis,
                  {
                    authors_country_year: isEn ? "Author et al., Cameroon (2024)" : "Auteur et al., Cameroun (2024)",
                    study_design: isEn ? "Cross-sectional" : "Transversale",
                    population: isEn ? "N=150 adults" : "N=150 adultes",
                    main_results: isEn ? "Prevalence 32.5%, mean age 45 years" : "Prévalence 32.5%, âge moyen 45 ans",
                    limits: isEn ? "Single-center" : "Mono-centrique",
                  },
                ])
              }
              className="inline-flex items-center gap-1 rounded bg-slate-800 px-2.5 py-1 text-xs font-medium text-white hover:bg-slate-900"
            >
              <Plus className="h-3 w-3" /> {isEn ? "Add study" : "Ajouter une étude"}
            </button>
          </div>

          <div className="max-h-96 overflow-x-auto overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-100 text-slate-700">
                <tr>
                  <th className="p-2">{isEn ? "Author(s), Country (Year)" : "Auteur(s), Pays (Année)"}</th>
                  <th className="p-2">{isEn ? "Design" : "Devis"}</th>
                  <th className="p-2">{isEn ? "Population" : "Population"}</th>
                  <th className="p-2">{isEn ? "Main results" : "Résultats principaux"}</th>
                  <th className="p-2">{isEn ? "Limitations" : "Limites"}</th>
                  <th className="p-2 w-10"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {synthesis.map((s, i) => (
                  <tr key={i}>
                    <td className="p-1.5">
                      <input
                        type="text"
                        value={s.authors_country_year}
                        onChange={(e) => {
                          const copy = [...synthesis];
                          copy[i].authors_country_year = e.target.value;
                          setSynthesis(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5">
                      <input
                        type="text"
                        value={s.study_design}
                        onChange={(e) => {
                          const copy = [...synthesis];
                          copy[i].study_design = e.target.value;
                          setSynthesis(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5">
                      <input
                        type="text"
                        value={s.population}
                        onChange={(e) => {
                          const copy = [...synthesis];
                          copy[i].population = e.target.value;
                          setSynthesis(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5">
                      <textarea
                        rows={2}
                        value={s.main_results}
                        onChange={(e) => {
                          const copy = [...synthesis];
                          copy[i].main_results = e.target.value;
                          setSynthesis(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5">
                      <input
                        type="text"
                        value={s.limits}
                        onChange={(e) => {
                          const copy = [...synthesis];
                          copy[i].limits = e.target.value;
                          setSynthesis(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5 text-center">
                      <button
                        type="button"
                        onClick={() => setSynthesis(synthesis.filter((_, idx) => idx !== i))}
                        className="text-rose-500 hover:text-rose-700"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 3. Chronogramme Gantt */}
      {activeTab === "timeline" && (
        <div className="mt-4 space-y-3">
          <span className="text-xs text-slate-600">
            {isEn ? (
              <>Gantt matrix inserted in <strong>Chapter III (Section III.9)</strong>. Edit dates, activities, and check active periods.</>
            ) : (
              <>Matrice de Gantt insérée dans <strong>Chapitre III (Section III.9)</strong>. Modifiez les dates, les activités et cochez les périodes actives.</>
            )}
          </span>
          <TimelineGridEditor
            timelineGrid={timelineGrid}
            onChange={setTimelineGrid}
            variant="tables"
          />
        </div>
      )}

      {/* 4. Budget prévisionnel */}
      {activeTab === "budget" && (
        <div className="mt-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-600">
              {isEn ? (
                <>Itemized budget inserted in <strong>Chapter III (Section III.10)</strong> with automatic total calculation.</>
              ) : (
                <>Budget chiffré inséré dans <strong>Chapitre III (Section III.10)</strong> avec calcul automatique du total.</>
              )}
            </span>
            <button
              type="button"
              onClick={() =>
                setBudget([
                  ...budget,
                  {
                    category: isEn ? "Logistics" : "Logistique",
                    designation: isEn ? "New budget line" : "Nouvelle ligne de dépense",
                    quantity: 1,
                    unit_cost_fcfa: 10000,
                    total_fcfa: 10000,
                  },
                ])
              }
              className="inline-flex items-center gap-1 rounded bg-slate-800 px-2.5 py-1 text-xs font-medium text-white hover:bg-slate-900"
            >
              <Plus className="h-3 w-3" /> {isEn ? "Add line" : "Ajouter une ligne"}
            </button>
          </div>

          <div className="max-h-96 overflow-x-auto overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-[#6C2826] text-white">
                <tr>
                  <th className="p-2">{isEn ? "Item / Description" : "Désignation"}</th>
                  <th className="p-2">{isEn ? "Category" : "Catégorie"}</th>
                  <th className="p-2 w-24">{isEn ? "Unit price (FCFA)" : "Prix unitaire (FCFA)"}</th>
                  <th className="p-2 w-20">{isEn ? "Quantity" : "Quantité"}</th>
                  <th className="p-2 w-28">{isEn ? "Total (FCFA)" : "Total (FCFA)"}</th>
                  <th className="p-2 w-10"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {budget.map((b, i) => {
                  const lineTotal = b.quantity * b.unit_cost_fcfa;
                  return (
                    <tr key={i}>
                      <td className="p-1.5">
                        <input
                          type="text"
                          value={b.designation}
                          onChange={(e) => {
                            const copy = [...budget];
                            copy[i].designation = e.target.value;
                            setBudget(copy);
                          }}
                          className="w-full rounded border border-slate-300 px-2 py-1"
                        />
                      </td>
                      <td className="p-1.5">
                        <input
                          type="text"
                          value={b.category}
                          onChange={(e) => {
                            const copy = [...budget];
                            copy[i].category = e.target.value;
                            setBudget(copy);
                          }}
                          className="w-full rounded border border-slate-300 px-2 py-1"
                        />
                      </td>
                      <td className="p-1.5">
                        <input
                          type="number"
                          value={b.unit_cost_fcfa}
                          onChange={(e) => {
                            const copy = [...budget];
                            copy[i].unit_cost_fcfa = Number(e.target.value);
                            copy[i].total_fcfa = copy[i].quantity * copy[i].unit_cost_fcfa;
                            setBudget(copy);
                          }}
                          className="w-full rounded border border-slate-300 px-2 py-1"
                        />
                      </td>
                      <td className="p-1.5">
                        <input
                          type="number"
                          value={b.quantity}
                          onChange={(e) => {
                            const copy = [...budget];
                            copy[i].quantity = Number(e.target.value);
                            copy[i].total_fcfa = copy[i].quantity * copy[i].unit_cost_fcfa;
                            setBudget(copy);
                          }}
                          className="w-full rounded border border-slate-300 px-2 py-1"
                        />
                      </td>
                      <td className="p-1.5 font-semibold text-slate-800">
                        {lineTotal.toLocaleString("fr-FR")}
                      </td>
                      <td className="p-1.5 text-center">
                        <button
                          type="button"
                          onClick={() => setBudget(budget.filter((_, idx) => idx !== i))}
                          className="text-rose-500 hover:text-rose-700"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot className="bg-slate-100 font-bold text-slate-900">
                <tr>
                  <td colSpan={4} className="p-2 text-right">
                    {isEn ? "GRAND TOTAL:" : "TOTAL GÉNÉRAL :"}
                  </td>
                  <td colSpan={2} className="p-2 text-[#6C2826]">
                    {grandTotalFCFA.toLocaleString(isEn ? "en-US" : "fr-FR")} FCFA
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      )}

      {/* 5. Abréviations */}
      {activeTab === "abbreviations" && (
        <div className="mt-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-600">
              {isEn ? (
                <>List of acronyms inserted in the <strong>Preliminary Pages</strong>.</>
              ) : (
                <>Liste des sigles insérée dans les <strong>Pages liminaires</strong>.</>
              )}
            </span>
            <button
              type="button"
              onClick={() =>
                setAbbreviations([
                  ...abbreviations,
                  {
                    acronym: isEn ? "FMBS" : "FMSB",
                    full_form: isEn
                      ? "Faculty of Medicine and Biomedical Sciences"
                      : "Faculté de Médecine et des Sciences Biomédicales",
                  },
                ])
              }
              className="inline-flex items-center gap-1 rounded bg-slate-800 px-2.5 py-1 text-xs font-medium text-white hover:bg-slate-900"
            >
              <Plus className="h-3 w-3" /> {isEn ? "Add acronym" : "Ajouter un sigle"}
            </button>
          </div>

          <div className="max-h-80 overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-100 text-slate-700">
                <tr>
                  <th className="p-2 w-32">{isEn ? "Acronym" : "Sigle / Acronyme"}</th>
                  <th className="p-2">{isEn ? "Full meaning" : "Signification complète"}</th>
                  <th className="p-2 w-10"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {abbreviations.map((a, i) => (
                  <tr key={i}>
                    <td className="p-1.5">
                      <input
                        type="text"
                        value={a.acronym}
                        onChange={(e) => {
                          const copy = [...abbreviations];
                          copy[i].acronym = e.target.value;
                          setAbbreviations(copy);
                        }}
                        className="w-full font-bold uppercase rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5">
                      <input
                        type="text"
                        value={a.full_form}
                        onChange={(e) => {
                          const copy = [...abbreviations];
                          copy[i].full_form = e.target.value;
                          setAbbreviations(copy);
                        }}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </td>
                    <td className="p-1.5 text-center">
                      <button
                        type="button"
                        onClick={() => setAbbreviations(abbreviations.filter((_, idx) => idx !== i))}
                        className="text-rose-500 hover:text-rose-700"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 6. Pack Annexes FMSB */}
      {activeTab === "annexes" && (
        <div className="mt-4 space-y-4">
          <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-3 text-xs text-emerald-950">
            <strong>
              {isEn ? "Administrative & Ethics Appendices Generator:" : "Générateur d'Annexes administratives & éthiques :"}
            </strong>{" "}
            {isEn
              ? "Official forms and letters are automatically pre-filled with the title, student name, registration number, host institutions, and supervisor defined on the cover page."
              : "Les fiches et lettres officielles sont automatiquement pré-remplies avec le titre, le nom, le matricule, les structures d'accueil et le directeur définis dans la page de garde."}
          </div>

          <div className="grid grid-cols-1 gap-2 text-xs text-slate-800 md:grid-cols-2">
            <label className="flex items-center gap-2 rounded border border-slate-200 p-2">
              <input
                type="checkbox"
                checked={annexesConfig.include_info_notice}
                onChange={(e) =>
                  setAnnexesConfig({ ...annexesConfig, include_info_notice: e.target.checked })
                }
              />
              <span>
                {isEn
                  ? "Appendix 1: Participant Information Notice"
                  : "Annexe 1 : Notice d'information du participant"}
              </span>
            </label>
            <label className="flex items-center gap-2 rounded border border-slate-200 p-2">
              <input
                type="checkbox"
                checked={annexesConfig.include_consent_form}
                onChange={(e) =>
                  setAnnexesConfig({ ...annexesConfig, include_consent_form: e.target.checked })
                }
              />
              <span>
                {isEn
                  ? "Appendix 2: Informed Consent Form"
                  : "Annexe 2 : Formulaire de consentement éclairé"}
              </span>
            </label>
            <label className="flex items-center gap-2 rounded border border-slate-200 p-2">
              <input
                type="checkbox"
                checked={annexesConfig.include_data_collection_form}
                onChange={(e) =>
                  setAnnexesConfig({ ...annexesConfig, include_data_collection_form: e.target.checked })
                }
              />
              <span>
                {isEn
                  ? "Appendix 3: Data Collection Form (generated from variables / CRF)"
                  : "Annexe 3 : Fiche de collecte (générée depuis les variables / CRF)"}
              </span>
            </label>
            <label className="flex items-center gap-2 rounded border border-slate-200 p-2">
              <input
                type="checkbox"
                checked={annexesConfig.include_research_auth_requests}
                onChange={(e) =>
                  setAnnexesConfig({ ...annexesConfig, include_research_auth_requests: e.target.checked })
                }
              />
              <span>
                {isEn
                  ? "Appendix 4: Research Authorization Requests (Faculty & host sites)"
                  : "Annexe 4 : Demandes d'autorisation de recherche (Faculté & sites d'accueil)"}
              </span>
            </label>
            <label className="flex items-center gap-2 rounded border border-slate-200 p-2">
              <input
                type="checkbox"
                checked={annexesConfig.include_ethics_clearance_request}
                onChange={(e) =>
                  setAnnexesConfig({ ...annexesConfig, include_ethics_clearance_request: e.target.checked })
                }
              />
              <span>
                {isEn
                  ? "Appendix 5: Ethical Clearance Request (Faculty / University Ethics Committee)"
                  : "Annexe 5 : Demande de clairance éthique (Comité d'éthique de votre Faculté / Université)"}
              </span>
            </label>
            <label className="flex items-center gap-2 rounded border border-slate-200 p-2">
              <input
                type="checkbox"
                checked={annexesConfig.include_confidentiality_declaration}
                onChange={(e) =>
                  setAnnexesConfig({ ...annexesConfig, include_confidentiality_declaration: e.target.checked })
                }
              />
              <span>
                {isEn
                  ? "Appendix 6: Confidentiality Declaration (Declaration of Helsinki)"
                  : "Annexe 6 : Déclaration de confidentialité (Déclaration d'Helsinki)"}
              </span>
            </label>
            <label className="flex items-center gap-2 rounded border border-slate-200 p-2 md:col-span-2">
              <input
                type="checkbox"
                checked={annexesConfig.include_commitment_letter}
                onChange={(e) =>
                  setAnnexesConfig({ ...annexesConfig, include_commitment_letter: e.target.checked })
                }
              />
              <span>
                {isEn
                  ? "Appendix 7: Feedback & Commitment Letter"
                  : "Annexe 7 : Lettre d'engagement de rétro-information"}
              </span>
            </label>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700">
              {isEn
                ? "Short study goal (inserted into official letters)"
                : "But résumé de l'étude (inséré dans les lettres officielles)"}
            </label>
            <textarea
              rows={2}
              value={annexesConfig.study_goal_short}
              onChange={(e) =>
                setAnnexesConfig({ ...annexesConfig, study_goal_short: e.target.value })
              }
              placeholder={
                isEn
                  ? "e.g. Evaluate the impact and risk factors among admitted patients..."
                  : "Ex: Évaluer l'impact et les facteurs de risque chez les patients admis..."
              }
              className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-xs focus:border-indigo-500 focus:outline-none"
            />
          </div>
        </div>
      )}

      {/* 7. Figures & Images */}
      {activeTab === "figures" && (
        <div className="mt-4 space-y-4">
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3.5 space-y-2.5">
            <div className="text-xs font-bold text-slate-800">
              {isEn ? "Import a new figure into the protocol" : "Importer une nouvelle figure dans le protocole"}
            </div>
            <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
              <input
                type="file"
                accept=".png,.jpg,.jpeg,.webp"
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  setFigUploadFile(f);
                  if (f && !figUploadTitle) {
                    setFigUploadTitle(f.name.replace(/\.[^.]+$/, ""));
                  }
                }}
                className="text-xs"
              />
              <select
                value={figUploadSection}
                onChange={(e) => setFigUploadSection(e.target.value)}
                className="rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
              >
                {(canvas && canvas.length > 0
                  ? canvas.map((s) => s.id)
                  : Object.keys(project.sections)
                )
                  .filter(
                    (id) =>
                      ![
                        "cover_page",
                        "toc",
                        "abbreviations",
                        "list_of_tables",
                        "list_of_figures",
                        "references",
                        "annexes",
                        "annexe_notice_information",
                        "annexe_consentement",
                        "annexe_fiche_collecte",
                        "annexe_autorisation_recherche",
                        "annexe_clairance_ethique",
                        "annexe_confidentialite",
                        "annexe_engagement",
                      ].includes(id)
                  )
                  .map((id) => {
                    const label = canvas?.find((s) => s.id === id)?.title || id;
                    return (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    );
                  })}
              </select>
              <input
                type="text"
                value={figUploadTitle}
                onChange={(e) => setFigUploadTitle(e.target.value)}
                placeholder={isEn ? "Figure title" : "Titre de la figure"}
                className="rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              />
              <input
                type="text"
                value={figUploadCaption}
                onChange={(e) => setFigUploadCaption(e.target.value)}
                placeholder={isEn ? "Caption / source (optional)" : "Légende / source (optionnelle)"}
                className="rounded border border-slate-300 px-2.5 py-1.5 text-xs"
              />
            </div>
            <button
              type="button"
              disabled={!figUploadFile || saving}
              onClick={async () => {
                if (!figUploadFile) return;
                setSaving(true);
                onError("");
                try {
                  const updated = await api.uploadImage(project.project_id, {
                    file: figUploadFile,
                    target_section_id: figUploadSection,
                    title: figUploadTitle.trim() || figUploadFile.name,
                    caption: figUploadCaption.trim(),
                    anchor_hint: "",
                  });
                  onProjectUpdated(updated);
                  setFigUploadFile(null);
                  setFigUploadTitle("");
                  setFigUploadCaption("");
                } catch (err) {
                  onError(err instanceof Error ? err.message : String(err));
                } finally {
                  setSaving(false);
                }
              }}
              className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {isEn ? "Upload & include figure" : "Importer & inclure la figure"}
            </button>
          </div>

          {project.images.length === 0 ? (
            <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-xs text-slate-500">
              {isEn
                ? "No figures saved yet. Upload one above or directly inside a section in Module 1."
                : "Aucune figure enregistrée. Ajoutez-en une ci-dessus ou directement dans une section du Module 1."}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {project.images.map((img) => (
                <div
                  key={img.asset_id}
                  className="flex flex-col justify-between gap-2 rounded-lg border border-slate-200 bg-white p-3 text-xs"
                >
                  <div className="flex gap-3">
                    {img.source_url && (
                      <img
                        src={img.source_url}
                        alt={img.title}
                        className="h-16 w-16 shrink-0 rounded border border-slate-200 object-cover"
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="font-semibold text-slate-800">{img.title}</div>
                      <div className="text-[11px] text-slate-500">
                        Section : {canvas?.find((s) => s.id === img.target_section_id)?.title || img.target_section_id}
                      </div>
                      <div className="mt-0.5 text-[11px] text-slate-600">
                        {img.caption || (isEn ? "No caption" : "Sans légende")}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center justify-end gap-2 border-t border-slate-100 pt-2">
                    <button
                      type="button"
                      onClick={async () => {
                        const updated = await api.toggleImage(
                          project.project_id,
                          img.asset_id,
                          { approved: !img.approved }
                        );
                        onProjectUpdated(updated);
                      }}
                      className={`rounded px-2.5 py-1 text-[11px] font-semibold ${
                        img.approved
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {img.approved
                        ? isEn
                          ? "Included in Word"
                          : "Incluse dans Word"
                        : isEn
                        ? "Excluded"
                        : "Exclue"}
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        const updated = await api.deleteImage(
                          project.project_id,
                          img.asset_id
                        );
                        onProjectUpdated(updated);
                      }}
                      className="rounded px-2 py-1 text-rose-600 hover:bg-rose-50"
                      title={isEn ? "Delete" : "Supprimer"}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
