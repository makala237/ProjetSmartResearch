import type { ModuleKey } from "../types/auth";

export interface WelcomeTourStep {
  id: string;
  targetModule?: ModuleKey;
  titleFr: string;
  titleEn: string;
  summaryFr: string;
  summaryEn: string;
  tipsFr: string[];
  tipsEn: string[];
}

export interface FeatureGuideSpec {
  id: string;
  titleFr: string;
  titleEn: string;
  summaryFr: string;
  summaryEn: string;
  tipsFr: string[];
  tipsEn: string[];
  askPromptFr: string;
  askPromptEn: string;
}

export const WELCOME_TOUR_STEPS: WelcomeTourStep[] = [
  {
    id: "welcome",
    titleFr: "Bienvenue dans Smart Research",
    titleEn: "Welcome to Smart Research",
    summaryFr:
      "Votre espace complet de rédaction scientifique CAMES/FMSB, de l'idée initiale jusqu'à l'export Word/PDF et la soutenance PowerPoint.",
    summaryEn:
      "Your end-to-end CAMES/FMSB academic protocol studio, from initial topic to Word/PDF compilation and PowerPoint defense.",
    tipsFr: [
      "Travaillez dans l'ordre des modules ou naviguez librement selon vos besoins.",
      "Un guide discret (« Assistant Smart Research ») reste toujours accessible en bas à droite sans jamais écraser vos textes.",
    ],
    tipsEn: [
      "Work sequentially through Modules 1 to 4 or jump directly to any module.",
      "Assistant Smart Research stays floating at the bottom-right to answer questions without overwriting your sections.",
    ],
  },
  {
    id: "module1_drafting",
    targetModule: "module1",
    titleFr: "Module 1 — Rédaction structurée en 5 chapitres",
    titleEn: "Module 1 — 5-Chapter Structured Drafting",
    summaryFr:
      "Rédigez chaque section du protocole (Introduction, Revue de littérature, Méthodologie, Références, Annexes) avec des sources vérifiées et votre bibliothèque interne.",
    summaryEn:
      "Draft each protocol section across the 5-chapter FMSB structure using verified bibliographic references and your private internal library.",
    tipsFr: [
      "Importez vos propres brouillons ou thèses comme squelette ou comme source interne par section.",
      "Activez la Sentinelle de cohérence et l'Humaniseur académique avant de passer à la mise en page.",
    ],
    tipsEn: [
      "Import your own drafts or theses as either a protocol skeleton or a per-section internal library source.",
      "Run the Coherence Sentinel and Academic Humanizer before exporting.",
    ],
  },
  {
    id: "module2_edition",
    targetModule: "module2",
    titleFr: "Module 2 — Édition Word / PDF & Page de garde FMSB",
    titleEn: "Module 2 — Word / PDF Formatting & Cover Page",
    summaryFr:
      "Personnalisez la page de garde (cadres, position du département, logos université/faculté), la table des matières, le budget et le chronogramme.",
    summaryEn:
      "Customize cover page framing, department placement, university/faculty logos, TOC, budget, and timeline before exporting DOCX or PDF.",
    tipsFr: [
      "Vérifiez l'aperçu temps réel avant de télécharger votre fichier Word (.docx) ou PDF prêt pour dépôt.",
    ],
    tipsEn: [
      "Inspect the live cover and chapter layout preview before exporting your submission-ready DOCX or PDF.",
    ],
  },
  {
    id: "module3_crf",
    targetModule: "module3",
    titleFr: "Module 3 — Fiche de collecte, Masque de saisie & Kobo",
    titleEn: "Module 3 — Case Report Form & KoboToolbox Export",
    summaryFr:
      "Transformez automatiquement votre fiche d'enquête en masque de saisie patients, exportez en Excel/XLSForm pour KoboToolbox ou analyse statistique.",
    summaryEn:
      "Automatically convert your data collection sheet into an interactive patient CRF mask, CSV dataset, or XLSForm for KoboToolbox.",
    tipsFr: [
      "Chaque variable générée reste synchronisée avec le chapitre Méthodologie et l'Annexe Fiche de collecte.",
    ],
    tipsEn: [
      "CRF fields stay synced between Chapter III variables, Annex V, and your patient registry.",
    ],
  },
  {
    id: "module4_pptx",
    targetModule: "module4",
    titleFr: "Module 4 — Diaporama de soutenance PowerPoint",
    titleEn: "Module 4 — Defense PowerPoint Generator",
    summaryFr:
      "Générez un diaporama structuré selon les critères CAMES avec graphiques de budget, chronogramme visuel et notes d'orateur.",
    summaryEn:
      "Generate a CAMES-compliant defense presentation complete with budget visuals, Gantt chart, and presenter notes.",
    tipsFr: [
      "Choisissez le modèle graphique souhaité puis prévisualisez chaque diapositive avant téléchargement.",
    ],
    tipsEn: [
      "Pick a slide theme and preview every generated slide before exporting PPTX or PDF.",
    ],
  },
  {
    id: "copilot_assistant",
    titleFr: "Assistant Smart Research — Votre copilote non destructif",
    titleEn: "Assistant Smart Research — Your Non-Destructive Copilot",
    summaryFr:
      "Posez librement vos questions sur une source, enregistrez les directives globales de votre directeur de thèse et restaurez d'anciennes versions en un clic.",
    summaryEn:
      "Ask questions freely without altering text, enforce your supervisor's global directives across all chapters, and restore previous versions in one click.",
    tipsFr: [
      "Discussion : posez une question sans jamais écraser le texte actif.",
      "Directives : vos consignes encadreur s'appliquent en priorité à tout le protocole.",
      "Versions : chaque génération ou édition sauvegarde automatiquement un point de restauration.",
    ],
    tipsEn: [
      "Chat: ask about sources or methods safely — text only changes if you click Apply.",
      "Directives: supervisor instructions take top priority in every drafted section.",
      "Versions: every AI draft or manual save creates a one-click restore point.",
    ],
  },
];

export const FEATURE_GUIDES: Record<string, FeatureGuideSpec> = {
  module1: {
    id: "module1",
    titleFr: "Module 1 — Rédaction & Recherche",
    titleEn: "Module 1 — Drafting & Research",
    summaryFr:
      "C'est ici que vous rédigez les 5 chapitres FMSB en vous appuyant sur votre bibliothèque interne et les bases scientifiques.",
    summaryEn:
      "Draft all 5 FMSB chapters grounded in your private internal library and verified bibliographic searches.",
    tipsFr: [
      "Sélectionnez une sous-section à gauche, ajoutez une consigne optionnelle puis générez.",
      "Consultez les sources citées `[1]`, `[2]` directement dans la colonne de droite.",
    ],
    tipsEn: [
      "Pick any sub-section on the left, enter optional instructions, and draft.",
      "Inspect exact Vancouver references `[n]` in the right-hand bibliography pane.",
    ],
    askPromptFr: "Explique-moi comment organiser efficacement ma rédaction dans ce Module 1.",
    askPromptEn: "Explain the best workflow to draft sections efficiently in Module 1.",
  },
  module2: {
    id: "module2",
    titleFr: "Module 2 — Édition Word / PDF",
    titleEn: "Module 2 — Word / PDF Editing",
    summaryFr:
      "Réglez la mise en page officielle : bordures, position du département sur la page de garde, marges, table des matières et tableaux structurés.",
    summaryEn:
      "Fine-tune official formatting: cover borders, department placement above title or inside header columns, TOC, and structured tables.",
    tipsFr: [
      "Choisissez « Centré au-dessus du titre » si votre faculté adopte le modèle FMSB type Fokou.",
      "Cliquez sur Enregistrer la mise en forme avant de lancer l'export Word ou PDF.",
    ],
    tipsEn: [
      "Choose 'Centered above protocol title' for FMSB / Fokou cover typography.",
      "Save formatting rules before downloading your DOCX or PDF.",
    ],
    askPromptFr: "Quels réglages vérifier dans le Module 2 avant d'exporter en Word ou PDF ?",
    askPromptEn: "What formatting settings should I verify in Module 2 before exporting DOCX or PDF?",
  },
  module3: {
    id: "module3",
    titleFr: "Module 3 — Masque de saisie patients",
    titleEn: "Module 3 — Patient Data Entry Mask",
    summaryFr:
      "Saisissez vos fiches patients en direct ou exportez votre questionnaire structuré vers KoboToolbox (.xlsx XLSForm) et CSV.",
    summaryEn:
      "Record clinical patient entries or export your validated CRF schema to KoboToolbox XLSForm (.xlsx) and CSV.",
    tipsFr: [
      "Utilisez « Synchroniser depuis la fiche d'enquête » après toute modification de l'Annexe Fiche de collecte.",
    ],
    tipsEn: [
      "Click Sync from Data Collection Sheet whenever you update Annex V.",
    ],
    askPromptFr: "Comment synchroniser ma fiche de collecte et exporter vers KoboToolbox dans le Module 3 ?",
    askPromptEn: "How do I sync my CRF sheet and export to KoboToolbox in Module 3?",
  },
  module4: {
    id: "module4",
    titleFr: "Module 4 — Présentation de soutenance",
    titleEn: "Module 4 — Defense PowerPoint",
    summaryFr:
      "Transformez votre protocole validé en diapositives claires (problématique, objectifs, méthodologie, éthique, budget et chronogramme).",
    summaryEn:
      "Convert your validated protocol into structured defense slides including objectives, methodology, ethics, budget, and Gantt timeline.",
    tipsFr: [
      "Générez d'abord toutes les diapositives, puis éditez ou régénérez une diapo isolée si besoin.",
    ],
    tipsEn: [
      "Generate all slides first, then fine-tune individual slides or presenter notes.",
    ],
    askPromptFr: "Quelles bonnes pratiques suivre pour réussir ma soutenance avec le Module 4 ?",
    askPromptEn: "What best practices should I follow to prepare my protocol defense with Module 4?",
  },
  import_draft: {
    id: "import_draft",
    titleFr: "Importation de brouillon / document (PDF / DOCX)",
    titleEn: "Draft or Document Import (PDF / DOCX)",
    summaryFr:
      "Deux modes s'offrent à vous : remplacer le squelette du protocole, ou alimenter la bibliothèque interne sans rien écraser.",
    summaryEn:
      "Choose between replacing the protocol skeleton or enriching your per-section internal library without overwriting existing drafts.",
    tipsFr: [
      "Si vous importez une thèse, un article ou les notes de votre encadreur, choisissez « Source interne ».",
    ],
    tipsEn: [
      "Select 'Internal Source' when uploading reference articles, theses, or supervisor notes.",
    ],
    askPromptFr: "Quand faut-il importer un document en mode Squelette versus Source interne ?",
    askPromptEn: "When should I import a document as Skeleton vs. Internal Source?",
  },
  internal_library: {
    id: "internal_library",
    titleFr: "Bibliothèque interne par section",
    titleEn: "Per-Section Internal Library",
    summaryFr:
      "Lors d'une génération IA, Smart Research consulte d'abord ces extraits privés avant d'interroger les bases externes.",
    summaryEn:
      "When drafting a section, Smart Research inspects these private excerpts first before querying external databases.",
    tipsFr: [
      "Les thèses et articles identifiés avec référence restent citables en `[n]` ; les brouillons servent uniquement de matière brute.",
    ],
    tipsEn: [
      "Theses and papers retain citable Vancouver entries; unreferenced notes stay non-citable background material.",
    ],
    askPromptFr: "Comment l'IA utilise-t-elle ma bibliothèque interne lors de la rédaction d'une section ?",
    askPromptEn: "How does the AI prioritize my per-section internal library during drafting?",
  },
  coherence_sentinel: {
    id: "coherence_sentinel",
    titleFr: "Sentinelle de cohérence transversale",
    titleEn: "Cross-Section Coherence Sentinel",
    summaryFr:
      "Détecte automatiquement les contradictions entre chapitres (type d'étude, période, taille d'échantillon, budget ou objectifs).",
    summaryEn:
      "Automatically detects cross-chapter contradictions (study design, duration, sample size, budget, or objectives).",
    tipsFr: [
      "Un clic sur « Harmoniser » met à jour la section concernée tout en archivant l'ancienne version.",
    ],
    tipsEn: [
      "Harmonizing updates only the conflicting part while preserving an undo snapshot.",
    ],
    askPromptFr: "Pourquoi la Sentinelle de cohérence signale-t-elle des écarts entre chapitres ?",
    askPromptEn: "How does the Coherence Sentinel detect mismatches across protocol chapters?",
  },
  humanizer: {
    id: "humanizer",
    titleFr: "Humaniseur académique",
    titleEn: "Academic Style Humanizer",
    summaryFr:
      "Fluidifie la rédaction scientifique en français ou en anglais tout en préservant exactement les chiffres et citations `[n]`.",
    summaryEn:
      "Polishes academic prose for natural flow while strictly preserving all statistics and `[n]` citations.",
    tipsFr: [
      "Vous pouvez toujours comparer ou restaurer la version précédente dans l'onglet Versions de l'Assistant.",
    ],
    tipsEn: [
      "You can always inspect or roll back to the pre-humanization version in the Assistant Versions tab.",
    ],
    askPromptFr: "Que modifie exactement l'Humaniseur académique sur mon texte ?",
    askPromptEn: "What exactly does the Academic Humanizer change in my text?",
  },
  supervisor_directives: {
    id: "supervisor_directives",
    titleFr: "Directives globales de l'encadreur",
    titleEn: "Global Supervisor Directives",
    summaryFr:
      "Toute consigne activée ici prime sur les règles génériques dans l'ensemble des 5 chapitres du protocole.",
    summaryEn:
      "Every active directive stored here overrides generic defaults across all 5 chapters of your protocol.",
    tipsFr: [
      "Idéal pour imposer un hôpital précis, une période, un schéma d'étude ou des exigences particulières de votre directeur.",
    ],
    tipsEn: [
      "Ideal for enforcing your supervisor's exact study site, timeframe, design, or formatting preferences.",
    ],
    askPromptFr: "Comment rédiger des directives d'encadreur efficaces qui guident tout mon protocole ?",
    askPromptEn: "How should I write clear supervisor directives so all chapters follow them?",
  },
  section_versions: {
    id: "section_versions",
    titleFr: "Mémoire & Restauration de versions",
    titleEn: "Section Version Memory & Restore",
    summaryFr:
      "Chaque génération IA, sauvegarde manuelle ou proposition appliquée crée automatiquement un point de sauvegarde restaurable en 1 clic.",
    summaryEn:
      "Every AI draft, manual edit, or applied assistant proposal creates an automatic 1-click restore snapshot.",
    tipsFr: [
      "Restaurer une ancienne version sauvegarde aussi votre texte actuel juste avant de revenir en arrière : aucune perte possible.",
    ],
    tipsEn: [
      "Restoring a previous snapshot also archives your current text first — nothing is ever lost.",
    ],
    askPromptFr: "Comment retrouver et restaurer un ancien passage supprimé dans cette section ?",
    askPromptEn: "How can I recover and restore an earlier passage in this section?",
  },
};
