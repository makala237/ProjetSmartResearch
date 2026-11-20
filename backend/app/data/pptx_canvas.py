from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List

# Exact paths relative to workspace root
TPL_COVER_BG = "tamplates/tamplate image de fond première et deuxième diapositive.png"
TPL_LOGO = "tamplates/fmsb_uy1_logo.png"
TPL_PLAN = "tamplates/tamplate diapositive Plan.png"
TPL_INTRO = "tamplates/tamplate diapositive introduction.png"
TPL_OBJ_GEN = "tamplates/tamplate diapositive objectif générale.png"
TPL_OBJ_SPEC = "tamplates/tamplate objectifs spécifiques.png"
# Note: filename in tamplates/ has two consecutive spaces between "diapositive" and "méthodologie"
TPL_METHODOLOGY = "tamplates/tamplate diapositive  méthodologie.png"
# Note: filename in tamplates/ has a space before ".png"
TPL_CHRONOGRAM = "tamplates/Tamplate diapositive chronogramme des activités .png"
TPL_BUDGET = "tamplates/Tamplate diapositive budget.png"
TPL_THANKS = "tamplates/Merci pour votre attention !.png"

# Strict CAMES / FMSB presentation constraints
CAMES_MIN_BODY_PT = 24
CAMES_MIN_TITLE_PT = 26
CAMES_MAX_LINES_PER_SLIDE = 6
CAMES_IDEAL_WPM = 130
CAMES_MIN_DURATION_SEC = 600   # 10 min
CAMES_MAX_DURATION_SEC = 900   # 15 min

PLAN_6_AXES: List[str] = [
    "1. INTRODUCTION & CONTEXTE",
    "2. OBJECTIF GÉNÉRAL",
    "3. OBJECTIFS SPÉCIFIQUES",
    "4. MÉTHODOLOGIE DE L'ÉTUDE",
    "5. CHRONOGRAMME DES ACTIVITÉS",
    "6. BUDGET PRÉVISIONNEL",
]

PLAN_6_AXES_EN: List[str] = [
    "1. INTRODUCTION & BACKGROUND",
    "2. GENERAL OBJECTIVE",
    "3. SPECIFIC OBJECTIVES",
    "4. STUDY METHODOLOGY",
    "5. ACTIVITY TIMELINE",
    "6. ESTIMATED BUDGET",
]


def get_plan_axes(lang: str = "fr") -> List[str]:
    """Return the 6 canonical defense outline axes in 'fr' or 'en'."""
    return PLAN_6_AXES_EN if str(lang or "").strip().lower().startswith("en") else PLAN_6_AXES


@dataclass(frozen=True)
class PptxBlockSpec:
    """Canonical block definition in the FMSB PowerPoint protocol skeleton."""

    block_id: str
    slide_type: str
    title: str
    breadcrumb: str
    template_path: str
    source_section_ids: List[str] = field(default_factory=list)
    layout_mode: str = "standard"
    dynamic_slides: bool = False
    min_slides: int = 1
    max_slides: int = 1
    supports_media: bool = False
    media_section_ids: List[str] = field(default_factory=list)
    locked_as_is: bool = False
    ai_guidance: str = ""


PPTX_CANVAS_BLOCKS: List[PptxBlockSpec] = [
    PptxBlockSpec(
        block_id="welcome",
        slide_type="welcome",
        title="BIENVENUE À LA SOUTENANCE DU PROTOCOLE DE RECHERCHE",
        breadcrumb="ACCUEIL SOUTENANCE",
        template_path=TPL_COVER_BG,
        source_section_ids=["cover_page"],
        layout_mode="welcome",
        dynamic_slides=False,
    ),
    PptxBlockSpec(
        block_id="cover",
        slide_type="cover",
        title="PAGE DE GARDE OFFICIELLE",
        breadcrumb="COUVERTURE FMSB / CAMES",
        template_path=TPL_COVER_BG,
        source_section_ids=["cover_page"],
        layout_mode="cover_fmsb",
        dynamic_slides=False,
    ),
    PptxBlockSpec(
        block_id="plan",
        slide_type="plan",
        title="PLAN DU DIAPORAMA",
        breadcrumb="SOMMAIRE EXÉCUTIF",
        template_path=TPL_PLAN,
        source_section_ids=[],
        layout_mode="plan_6pills",
        dynamic_slides=False,
    ),
    PptxBlockSpec(
        block_id="introduction",
        slide_type="introduction",
        title="Introduction",
        breadcrumb="1. INTRODUCTION & CONTEXTE",
        template_path=TPL_INTRO,
        source_section_ids=[
            "intro_contexte",
            "intro_interet",
            "chap1_justification",
            "chap1_questions",
            "chap1_hypotheses",
        ],
        layout_mode="standard",
        dynamic_slides=True,
        min_slides=1,
        max_slides=6,
        supports_media=True,
        media_section_ids=["intro_contexte", "chap2_rappel_connaissances"],
        ai_guidance=(
            "Synthétiser en style télégraphique CAMES : définition clinique, ampleur épidémiologique "
            "(Monde, Afrique, Cameroun), justification locale, question(s) et hypothèse(s) de recherche. "
            "Garder chaque puce <= 18 mots avec chiffres et références clés."
        ),
    ),
    PptxBlockSpec(
        block_id="objective_general",
        slide_type="objective_general",
        title="Objectif général",
        breadcrumb="2. OBJECTIF GÉNÉRAL",
        template_path=TPL_OBJ_GEN,
        source_section_ids=["chap1_objectifs"],
        layout_mode="standard",
        dynamic_slides=False,
        min_slides=1,
        max_slides=1,
        ai_guidance=(
            "Formuler exactement l'objectif général du protocole en 1 phrase claire et percutante (verbe d'action à l'infinitif)."
        ),
    ),
    PptxBlockSpec(
        block_id="objective_specific",
        slide_type="objective_specific",
        title="Objectifs spécifiques",
        breadcrumb="3. OBJECTIFS SPÉCIFIQUES",
        template_path=TPL_OBJ_SPEC,
        source_section_ids=["chap1_objectifs"],
        layout_mode="standard",
        dynamic_slides=True,
        min_slides=1,
        max_slides=3,
        ai_guidance=(
            "Lister tous les objectifs spécifiques exacts numérotés (OS 1, OS 2...) sans en omettre ni en inventer. "
            "Limiter à 3 objectifs par diapositive pour garantir la lisibilité >= 24pt."
        ),
    ),
    PptxBlockSpec(
        block_id="meth_type_site_duree",
        slide_type="methodology",
        title="Méthodologie — Type, Sites & Période",
        breadcrumb="4. MÉTHODOLOGIE > TYPE, LIEUX & DURÉE",
        template_path=TPL_METHODOLOGY,
        source_section_ids=["chap3_type_site_duree"],
        layout_mode="standard",
        dynamic_slides=True,
        min_slides=1,
        max_slides=2,
        ai_guidance=(
            "Extraire avec exactitude : type d'étude, hôpitaux/sites retenus, durée totale et période d'inclusion."
        ),
    ),
    PptxBlockSpec(
        block_id="meth_population",
        slide_type="methodology",
        title="Méthodologie — Population & Échantillonnage",
        breadcrumb="4. MÉTHODOLOGIE > POPULATION & CRITÈRES",
        template_path=TPL_METHODOLOGY,
        source_section_ids=["chap3_population_echantillon"],
        layout_mode="standard",
        dynamic_slides=True,
        min_slides=1,
        max_slides=2,
        ai_guidance=(
            "Dégager population cible, critères d'inclusion, critères d'exclusion/non-inclusion, "
            "technique d'échantillonnage et taille calculée (formule de Schwartz le cas échéant)."
        ),
    ),
    PptxBlockSpec(
        block_id="meth_procedure",
        slide_type="methodology",
        title="Méthodologie — Procédure & Collecte",
        breadcrumb="4. MÉTHODOLOGIE > PROCÉDURE",
        template_path=TPL_METHODOLOGY,
        source_section_ids=["chap3_outils_procedure"],
        layout_mode="procedure_flow",
        dynamic_slides=True,
        min_slides=1,
        max_slides=2,
        ai_guidance=(
            "Structurer le déroulement opérationnel en 3 à 4 étapes chronologiques (Démarches & Éthique -> "
            "Recrutement & Collecte CRF -> Saisie & Contrôle qualité -> Analyse)."
        ),
    ),
    PptxBlockSpec(
        block_id="meth_variables",
        slide_type="methodology",
        title="Méthodologie — Variables d'étude",
        breadcrumb="4. MÉTHODOLOGIE > VARIABLES",
        template_path=TPL_METHODOLOGY,
        source_section_ids=["chap1_variables"],
        layout_mode="standard",
        dynamic_slides=True,
        min_slides=1,
        max_slides=5,
        supports_media=True,
        media_section_ids=["chap1_variables", "chap1_cadre_theorique"],
        ai_guidance=(
            "Regrouper les variables réelles en 4 catégories synthétiques : sociodémographiques, "
            "cliniques/diagnostiques, thérapeutiques/peropératoires et évolutives/critères de jugement."
        ),
    ),
    PptxBlockSpec(
        block_id="meth_analyse_ethique",
        slide_type="methodology",
        title="Méthodologie — Analyse Statistique & Éthique",
        breadcrumb="4. MÉTHODOLOGIE > ANALYSE & ÉTHIQUE",
        template_path=TPL_METHODOLOGY,
        source_section_ids=["chap3_analyse_donnees", "chap3_ethique"],
        layout_mode="standard",
        dynamic_slides=True,
        min_slides=1,
        max_slides=2,
        ai_guidance=(
            "Préciser logiciels statistiques, tests statistiques (Chi-2, Fisher, Student, seuil p), "
            "clairance éthique (CIER-FMSB), consentement éclairé et anonymisation."
        ),
    ),
    PptxBlockSpec(
        block_id="chronogram",
        slide_type="chronogram",
        title="Chronogramme des activités",
        breadcrumb="5. CHRONOGRAMME",
        template_path=TPL_CHRONOGRAM,
        source_section_ids=["chap3_chronogramme"],
        layout_mode="chronogram_7pills",
        dynamic_slides=False,
        locked_as_is=False,
        ai_guidance=(
            "Synthétiser jusqu'à 7 étapes clés du chronogramme au format 'Période : Activité' "
            "(ex. 'Mai 2026 : Rédaction du protocole'), ordonnées du jalon final en haut au jalon initial en bas."
        ),
    ),
    PptxBlockSpec(
        block_id="budget",
        slide_type="budget",
        title="Budget prévisionnel",
        breadcrumb="6. BUDGET PRÉVISIONNEL",
        template_path=TPL_BUDGET,
        source_section_ids=["chap3_budget"],
        layout_mode="radial_budget",
        dynamic_slides=False,
    ),
    PptxBlockSpec(
        block_id="acknowledgements",
        slide_type="acknowledgements",
        title="Merci pour votre attention !",
        breadcrumb="CLÔTURE",
        template_path=TPL_THANKS,
        source_section_ids=["cover_page"],
        layout_mode="acknowledgements",
        dynamic_slides=False,
    ),
]

PPTX_BLOCK_BY_ID: Dict[str, PptxBlockSpec] = {b.block_id: b for b in PPTX_CANVAS_BLOCKS}

PPTX_BLOCK_EN_OVERRIDES: Dict[str, Dict[str, str]] = {
    "welcome": {
        "title": "WELCOME TO THE RESEARCH PROTOCOL DEFENSE",
        "breadcrumb": "DEFENSE WELCOME",
    },
    "cover": {
        "title": "OFFICIAL COVER PAGE",
        "breadcrumb": "FMSB / CAMES COVER",
    },
    "plan": {
        "title": "PRESENTATION OUTLINE",
        "breadcrumb": "EXECUTIVE OUTLINE",
    },
    "introduction": {
        "title": "Introduction",
        "breadcrumb": "1. INTRODUCTION & BACKGROUND",
        "ai_guidance": (
            "Summarize in CAMES telegraphic style: clinical definition, epidemiological burden "
            "(Global, Africa, Cameroon), local justification, research question(s), and hypothesis(es). "
            "Keep each bullet <= 18 words with exact figures and key citations."
        ),
    },
    "objective_general": {
        "title": "General Objective",
        "breadcrumb": "2. GENERAL OBJECTIVE",
        "ai_guidance": (
            "State the exact general objective of the protocol in 1 clear, impactful sentence (infinitive action verb)."
        ),
    },
    "objective_specific": {
        "title": "Specific Objectives",
        "breadcrumb": "3. SPECIFIC OBJECTIVES",
        "ai_guidance": (
            "List all exact numbered specific objectives (SO 1, SO 2...) without omitting or inventing any. "
            "Limit to 3 objectives per slide to maintain >= 24pt readability."
        ),
    },
    "meth_type_site_duree": {
        "title": "Methodology — Design, Sites & Period",
        "breadcrumb": "4. METHODOLOGY > DESIGN, SITES & DURATION",
        "ai_guidance": (
            "Extract accurately: study design, selected hospitals/sites, total duration, and enrollment period."
        ),
    },
    "meth_population": {
        "title": "Methodology — Population & Sampling",
        "breadcrumb": "4. METHODOLOGY > POPULATION & CRITERIA",
        "ai_guidance": (
            "Highlight target population, inclusion criteria, exclusion/non-inclusion criteria, "
            "sampling technique, and calculated sample size (Schwartz formula where applicable)."
        ),
    },
    "meth_procedure": {
        "title": "Methodology — Procedure & Data Collection",
        "breadcrumb": "4. METHODOLOGY > PROCEDURE",
        "ai_guidance": (
            "Structure operational workflow into 3 to 4 sequential steps (Ethics & Approvals -> "
            "Recruitment & CRF Capture -> Data Entry & QC -> Statistical Analysis)."
        ),
    },
    "meth_variables": {
        "title": "Methodology — Study Variables",
        "breadcrumb": "4. METHODOLOGY > VARIABLES",
        "ai_guidance": (
            "Group real study variables into 4 concise categories: sociodemographic, "
            "clinical/diagnostic, therapeutic/intraoperative, and outcome endpoints."
        ),
    },
    "meth_analyse_ethique": {
        "title": "Methodology — Statistical Analysis & Ethics",
        "breadcrumb": "4. METHODOLOGY > ANALYSIS & ETHICS",
        "ai_guidance": (
            "Specify statistical software, tests (Chi-square, Fisher, Student t, p-value threshold), "
            "ethical clearance (IRB / CIER-FMSB), informed consent, and anonymization."
        ),
    },
    "chronogram": {
        "title": "Activity Timeline",
        "breadcrumb": "5. TIMELINE",
        "ai_guidance": (
            "Summarize up to 7 key timeline milestones as 'Period: Activity' "
            "(e.g., 'May 2026: Protocol drafting'), ordered from final milestone at top to initial milestone at bottom."
        ),
    },
    "budget": {
        "title": "Estimated Budget",
        "breadcrumb": "6. ESTIMATED BUDGET",
    },
    "acknowledgements": {
        "title": "Thank you for your attention!",
        "breadcrumb": "CLOSING",
    },
}


def get_localized_pptx_blocks(lang: str = "fr") -> List[PptxBlockSpec]:
    """Return PPTX canvas block specs localized to 'fr' or 'en' without modifying IDs or templates."""
    if not str(lang or "").strip().lower().startswith("en"):
        return list(PPTX_CANVAS_BLOCKS)
    localized: List[PptxBlockSpec] = []
    for block in PPTX_CANVAS_BLOCKS:
        ov = PPTX_BLOCK_EN_OVERRIDES.get(block.block_id)
        if not ov:
            localized.append(block)
        else:
            localized.append(
                replace(
                    block,
                    title=ov.get("title", block.title),
                    breadcrumb=ov.get("breadcrumb", block.breadcrumb),
                    ai_guidance=ov.get("ai_guidance", block.ai_guidance),
                )
            )
    return localized

SLIDE_TEMPLATE_CATALOG: List[Dict[str, str]] = [
    {
        "id": "tpl-intro",
        "label": "Introduction (Ampoule)",
        "slide_type": "introduction",
        "path": TPL_INTRO,
    },
    {
        "id": "tpl-obj-gen",
        "label": "Objectif général (Cible)",
        "slide_type": "objective_general",
        "path": TPL_OBJ_GEN,
    },
    {
        "id": "tpl-obj-spec",
        "label": "Objectifs spécifiques (Multi-cibles)",
        "slide_type": "objective_specific",
        "path": TPL_OBJ_SPEC,
    },
    {
        "id": "tpl-methodology",
        "label": "Méthodologie (Engrenage)",
        "slide_type": "methodology",
        "path": TPL_METHODOLOGY,
    },
    {
        "id": "tpl-plan",
        "label": "Plan du diaporama (6 pilules)",
        "slide_type": "plan",
        "path": TPL_PLAN,
    },
    {
        "id": "tpl-chronogram",
        "label": "Chronogramme des activités (Gantt 7 étapes)",
        "slide_type": "chronogram",
        "path": TPL_CHRONOGRAM,
    },
    {
        "id": "tpl-budget",
        "label": "Budget prévisionnel (Roue 10 postes)",
        "slide_type": "budget",
        "path": TPL_BUDGET,
    },
    {
        "id": "tpl-thanks",
        "label": "Remerciements",
        "slide_type": "acknowledgements",
        "path": TPL_THANKS,
    },
    {
        "id": "tpl-cover-bg",
        "label": "Fond médical Accueil / Garde",
        "slide_type": "cover",
        "path": TPL_COVER_BG,
    },
]


def resolve_template_path(workspace_root: Path, rel_path: str) -> Path:
    return (workspace_root / rel_path).resolve()
