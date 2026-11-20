from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import CanvasSectionSpec


def normalize_lang(lang: Optional[str]) -> str:
    """Normalize any language specifier to 'fr' or 'en' (default 'fr')."""
    return "en" if str(lang or "").strip().lower().startswith("en") else "fr"


CHAPTER_TITLES_BY_LANG: Dict[str, Dict[str, str]] = {
    "fr": {
        "introduction": "INTRODUCTION",
        "chapitre_1": "CHAPITRE I : PROBLÉMATIQUE",
        "chapitre_2": "CHAPITRE II : REVUE DE LA LITTÉRATURE",
        "chapitre_3": "CHAPITRE III : MÉTHODOLOGIE",
        "references": "RÉFÉRENCES",
        "annexes": "ANNEXES",
    },
    "en": {
        "introduction": "INTRODUCTION",
        "chapitre_1": "CHAPTER I: PROBLEM STATEMENT",
        "chapitre_2": "CHAPTER II: LITERATURE REVIEW",
        "chapitre_3": "CHAPTER III: METHODOLOGY",
        "references": "REFERENCES",
        "annexes": "APPENDICES",
    },
}


DOC_LABELS_BY_LANG: Dict[str, Dict[str, str]] = {
    "fr": {
        "anchor_toc": "TABLE DES MATIERES",
        "anchor_abbreviations": "ABREVIATIONS, ACRONYMES & SIGLES",
        "list_of_tables_title": "LISTE DES TABLEAUX",
        "list_of_figures_title": "LISTE DES FIGURES",
        "table_seq": "Tableau",
        "figure_seq": "Figure",
        "annexe_prefix": "ANNEXE",
        "cover_written_by": "Protocole de thèse de {degree} rédigé par :",
        "cover_reg_no": "Matricule : {matricule}",
        "cover_supervisor": "Directeur",
        "cover_cosupervisor": "Co-directeur(s)",
        "cover_to_complete": "À renseigner",
        "cover_academic_year": "Année académique {year}",
        "toc_col_section": "Rubrique / Section du Protocole",
        "toc_col_page": "Page",
        "toc_summary_note": "Sommaire exécutif conforme CAMES / FMSB (1 page) — synchronisation dynamique Word (.docx) et PDF.",
        "no_abbreviations": "Aucune abréviation enregistrée.",
        "no_tables": "Aucun tableau enregistré dans le protocole.",
        "no_figures": "Aucune figure enregistrée dans le protocole.",
        "abbr_headers": "Sigle / Acronyme|Signification complète",
        "var_headers": "Variable|Rôle|Définition opérationnelle|Échelle|Outil",
        "lit_headers": "Auteurs / Pays / Année|Devis d'étude|Population|Résultats principaux|Limites",
        "chrono_header_activity": "Activités",
        "budget_headers": "Désignation|Catégorie|Quantité|PU (FCFA)|Total (FCFA)",
        "budget_total_label": "TOTAL GÉNÉRAL",
        "list_title_header_table": "Titre du Tableau",
        "list_title_header_figure": "Titre de la Figure",
        "crf_default_section": "SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES",
        "crf_header_title": "FICHE DE COLLECTE DES DONNÉES (CRF)",
        "crf_patient_code": "Code participant :",
        "crf_inclusion_date": "Date d'inclusion :",
        "crf_site": "Site hospitalier :",
        "crf_investigator": "Investigateur :",
        "crf_yes": "Oui",
        "crf_no": "Non",
        "crf_day": "JJ",
        "crf_month": "MM",
        "crf_year": "AAAA",
        "toc_note": "Note : Sommaire synthétique CAMES calibré sur les Grands titres & Sous-titres principaux (tenue sur 1 page A4), synchronisé avec Microsoft Word.",
    },
    "en": {
        "anchor_toc": "TABLE OF CONTENTS",
        "anchor_abbreviations": "ABBREVIATIONS, ACRONYMS & SYMBOLS",
        "list_of_tables_title": "LIST OF TABLES",
        "list_of_figures_title": "LIST OF FIGURES",
        "table_seq": "Table",
        "figure_seq": "Figure",
        "annexe_prefix": "APPENDIX",
        "cover_written_by": "{degree} Thesis Protocol written by:",
        "cover_reg_no": "Registration No.: {matricule}",
        "cover_supervisor": "Supervisor",
        "cover_cosupervisor": "Co-supervisor(s)",
        "cover_to_complete": "To be completed",
        "cover_academic_year": "Academic Year {year}",
        "toc_col_section": "Section / Protocol Heading",
        "toc_col_page": "Page",
        "toc_summary_note": "Executive summary compliant with CAMES / FMSB standards (1 page) — dynamically synchronized for Word (.docx) and PDF.",
        "no_abbreviations": "No abbreviations recorded.",
        "no_tables": "No tables recorded in the protocol.",
        "no_figures": "No figures recorded in the protocol.",
        "abbr_headers": "Acronym / Symbol|Full Meaning",
        "var_headers": "Variable|Role|Operational Definition|Measurement Scale|Tool",
        "lit_headers": "Authors / Country / Year|Study Design|Population|Main Results|Limitations",
        "chrono_header_activity": "Activities",
        "budget_headers": "Item / Designation|Category|Quantity|Unit Cost (FCFA)|Total (FCFA)",
        "budget_total_label": "GRAND TOTAL",
        "list_title_header_table": "Table Title",
        "list_title_header_figure": "Figure Title",
        "crf_default_section": "SECTION A: IDENTIFICATION & SOCIODEMOGRAPHIC CHARACTERISTICS",
        "crf_header_title": "CASE REPORT FORM (CRF)",
        "crf_patient_code": "Participant ID:",
        "crf_inclusion_date": "Enrollment Date:",
        "crf_site": "Hospital Site:",
        "crf_investigator": "Investigator:",
        "crf_yes": "Yes",
        "crf_no": "No",
        "crf_day": "DD",
        "crf_month": "MM",
        "crf_year": "YYYY",
        "toc_note": "Note: Executive CAMES Table of Contents calibrated to Main Headings & Sub-headings (fits 1 A4 page), synchronized with Microsoft Word.",
    },
}


_EN_CANVAS_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "cover_page": {
        "chapter_title": "Preliminary Pages",
        "title": "Cover Page & Identification",
        "description": "Official bilingual academic cover page metadata (CAMES / Medical Faculty standards).",
        "guidance_points": [
            "Clear, precise protocol title (18 pt inside the official bordered frame)",
            "Target degree, department (FR/EN), candidate full name and registration number",
            "Supervisor and co-supervisor(s) with academic title, rank, and specialty",
            "Academic year",
        ],
    },
    "toc": {
        "chapter_title": "Preliminary Pages",
        "title": "Table of Contents",
        "description": "Complete protocol summary automatically generated per CAMES/FMSB structure and refreshable in Word.",
        "guidance_points": [
            "List all chapters and subsections in the exact official protocol order.",
            "Update final pagination in Microsoft Word (F9) after export if edited manually.",
        ],
        "cames_concepts_required": [
            "Strict 2–3 level hierarchy (Main headings, Chapters, Subsections)",
            "Roman pagination (preliminary pages) and Arabic pagination (protocol body)",
        ],
        "concepts_to_avoid": ["Narrative summaries or chapter descriptions inside the Table of Contents"],
    },
    "abbreviations": {
        "chapter_title": "Preliminary Pages",
        "title": "Abbreviations, Acronyms & Symbols",
        "description": "Single alphabetical A–Z list of acronyms and abbreviations used across the protocol (CAMES standard).",
        "guidance_points": [
            "Every acronym used in the text must be defined at first mention and listed alphabetically here.",
        ],
        "cames_concepts_required": [
            "Alphabetical A–Z ordering with zero duplicates",
            "Exact acronym in column 1 and full expansion in column 2",
        ],
        "concepts_to_avoid": ["Multiple fragmented tables", "Page number column"],
        "table_guidance": "Single alphabetical 2-column table (Acronym / Symbol | Full Meaning).",
    },
    "list_of_tables": {
        "chapter_title": "Preliminary Pages",
        "title": "List of Tables",
        "description": "Numbered index of all tables included in the protocol with A4 page references.",
        "guidance_points": [
            "Ensure every table carries a concise descriptive title and sequential numbering.",
        ],
        "cames_concepts_required": ["Continuous sequential numbering (Table 1, Table 2...) with exact page numbers"],
        "concepts_to_avoid": ["Re-embedding data tables inside this list"],
    },
    "list_of_figures": {
        "chapter_title": "Preliminary Pages",
        "title": "List of Figures",
        "description": "Numbered index of all figures, illustrations, and conceptual diagrams with A4 page references.",
        "guidance_points": [
            "Ensure every figure and diagram carries a sequential number, clear title, and caption.",
        ],
        "cames_concepts_required": ["Continuous sequential numbering (Figure 1, Figure 2...) with exact page numbers"],
        "concepts_to_avoid": ["Embedding figures directly inside the index list"],
    },
    "intro_contexte": {
        "chapter_title": "INTRODUCTION",
        "title": "Background & Epidemiology (Global, Africa, Cameroon)",
        "description": "Strict CAMES/FMSB epidemiological funnel (Global → Africa → Cameroon) establishing disease burden and public health relevance.",
        "guidance_points": [
            "Follow a strict funnel: global burden → African context → local/national reality.",
            "Cite recent, referenced epidemiological figures (prevalence, incidence, morbidity, mortality).",
            "Clearly state the observed clinical and public health problem.",
        ],
        "cames_concepts_required": [
            "Concise clinical definition of the condition or issue (2–3 sentences max)",
            "Graduated epidemiological data: Global → Sub-Saharan Africa → Local/Cameroon",
            "Public health burden and hospital/community impact",
        ],
        "concepts_to_avoid": [
            "Detailed pathophysiology and anatomy (reserved for Chapter II.1)",
            "Exhaustive comparison of prior studies (reserved for Chapter II.2)",
            "Study methods, sample size, or specific objectives",
        ],
        "table_guidance": "No tables permitted in the Introduction under CAMES/FMSB guidelines.",
        "notes": "Shares with 1.2 the strict global limit for the entire Introduction: <= 2 pages and <= 5 references total.",
    },
    "intro_interet": {
        "chapter_title": "INTRODUCTION",
        "title": "Significance of Research & Transition to Objectives",
        "description": "Brief synthesis of study relevance and concluding transition statement introducing the general purpose.",
        "guidance_points": [
            "Highlight clinical (patient care), scientific (knowledge), and public health significance.",
            "Conclude with a transition sentence opening onto the problem statement and general aim.",
        ],
        "cames_concepts_required": [
            "Threefold relevance: scientific, clinical/practical, and public health",
            "Closing transition statement introducing the research aim",
        ],
        "concepts_to_avoid": [
            "Repeating epidemiological statistics already stated in Background",
            "Listing specific objectives, timeline, or budget details",
        ],
        "notes": "Shares the overall Introduction limit (<= 2 pages and <= 5 references total).",
    },
    "chap1_justification": {
        "chapter_title": "Chapter I: Problem Statement",
        "title": "1. Rationale / Justification of the Study",
        "description": "CAMES/FINER argument demonstrating the local knowledge gap, limitations of prior studies, and clinical urgency.",
        "guidance_points": [
            "Pinpoint the exact knowledge gap (absent, scarce, or outdated local data).",
            "Explain methodological or contextual limitations of external studies.",
            "Demonstrate feasibility and relevance using FINER criteria (Feasible, Interesting, Novel, Ethical, Relevant).",
        ],
        "cames_concepts_required": [
            "Explicit identification of the local knowledge gap",
            "Critical appraisal of existing data limitations and limited generalizability",
            "Clinical and logistical feasibility in target study hospitals",
        ],
        "concepts_to_avoid": [
            "Verbatim repetition of global background from Introduction",
            "Detailed statistical testing procedures",
        ],
    },
    "chap1_questions": {
        "chapter_title": "Chapter I: Problem Statement",
        "title": "2. Research Question(s)",
        "description": "Single primary research question structured using PICO/PECO and optional focused secondary questions.",
        "guidance_points": [
            "Formulate one clear, measurable primary research question (Population, Exposure/Intervention, Comparator, Outcome, Setting).",
            "Keep secondary questions (1 to 3 max) strictly aligned with planned analyses.",
        ],
        "cames_concepts_required": [
            "Single PICO/PECO-structured primary research question",
            "Direct alignment with specific objectives",
        ],
        "concepts_to_avoid": ["Long literature review paragraphs", "Tables or figures"],
    },
    "chap1_hypotheses": {
        "chapter_title": "Chapter I: Problem Statement",
        "title": "3. Research Hypothesis(es)",
        "description": "Primary hypothesis stated affirmatively as a testable anticipated answer, plus secondary hypotheses.",
        "guidance_points": [
            "The primary hypothesis MUST be phrased in the affirmative (never as a question).",
            "If purely descriptive, justify the absence of an analytical hypothesis or state an operational hypothesis.",
        ],
        "cames_concepts_required": [
            "Affirmative primary hypothesis linking explanatory factor(s) and outcome",
            "Strict consistency with research questions and statistical plan",
        ],
        "concepts_to_avoid": ["Interrogative sentences", "Lengthy bibliographic justifications"],
        "notes": "Affirmative phrasing strictly required by CAMES/FMSB standards.",
    },
    "chap1_objectifs": {
        "chapter_title": "Chapter I: Problem Statement",
        "title": "4. Objectives (General and Specific)",
        "description": "One general objective and 3 to 5 operational specific objectives written with measurable action verbs.",
        "guidance_points": [
            "One single general objective stating population, setting, and study period.",
            "3 to 5 specific objectives using measurable action verbs (Describe, Determine, Identify, Compare, Evaluate) — avoid vague verbs like 'Understand' or 'Know'.",
        ],
        "cames_concepts_required": [
            "Single general objective mirroring the title and primary research question",
            "Sequential specific objectives (descriptive to analytical) aligned with statistical analysis",
        ],
        "concepts_to_avoid": ["Non-measurable verbs (understand, know, raise awareness)", "Unnecessary tables"],
    },
    "chap1_variables": {
        "chapter_title": "Chapter I: Problem Statement",
        "title": "5. Study Variables",
        "description": "Complete operationalization of dependent, independent, and confounding variables per CAMES norms.",
        "guidance_points": [
            "Explicitly classify dependent outcome variable(s), independent variables (sociodemographic, clinical, paraclinical, therapeutic), and confounders.",
            "Provide operational definition, measurement scale (continuous/discrete quantitative, nominal/ordinal/dichotomous qualitative), units, and collection tool.",
        ],
        "cames_concepts_required": [
            "Clear separation of Dependent vs Independent vs Confounding variables",
            "Operational definitions and clinical categorization thresholds",
            "Exact measurement scales and modalities (consistent with Appendix 1 CRF)",
        ],
        "concepts_to_avoid": ["Detailed statistical test formulas (reserved for Chapter III.7)"],
        "table_guidance": "Operational variables table (Variable | Role | Operational Definition | Scale | Tool) synced from project.variables.",
    },
    "chap1_cadre_theorique": {
        "chapter_title": "Chapter I: Problem Statement",
        "title": "6. Significance & Conceptual Framework",
        "description": "Explanatory conceptual framework linking determinants, intermediate factors, and clinical outcome, plus expected impact.",
        "guidance_points": [
            "Explain the conceptual or pathophysiological model connecting independent variables to the outcome.",
            "Specify concrete benefits for patients, clinicians, and health policymakers.",
            "Provide a clean vector conceptual diagram illustrating causal/associative pathways.",
        ],
        "cames_concepts_required": [
            "Conceptual framework architecture (distal/proximal determinants → clinical outcome)",
            "Concrete operational impact (practice improvement, advocacy, training)",
        ],
        "concepts_to_avoid": ["Repeating background epidemiology or full literature review"],
        "figure_guidance": "Structured vector conceptual framework diagram (nodes, annotated arrows, and caption).",
    },
    "chap2_rappel_connaissances": {
        "chapter_title": "Chapter II: Literature Review",
        "title": "1. Fundamental Knowledge Review (1/3)",
        "description": "CAMES fundamental foundation (1/3 of Chapter II): definitions, anatomy/pathophysiology, clinical presentation, diagnosis, and management.",
        "guidance_points": [
            "Respect CAMES proportion: roughly 1/3 of the Literature Review chapter.",
            "Organize logically: Definitions & classifications → Etiopathogenesis / Pathophysiology → Positive & differential diagnosis → Treatment & prognosis.",
        ],
        "cames_concepts_required": [
            "Up-to-date international nosological definitions and classifications",
            "Recognized pathophysiological mechanisms and etiological factors",
            "Diagnostic workup (clinical, imaging, labs) and standard therapeutic options",
        ],
        "concepts_to_avoid": [
            "Comparative analysis of other authors' clinical series (reserved for II.2 State of Knowledge)",
            "Candidate's own study methodology",
        ],
        "table_guidance": "Only if an official clinical staging or classification requires a summary table.",
        "figure_guidance": "Referenced pathophysiological, anatomical, or diagnostic decision algorithm figure.",
        "notes": "CAMES target: ~1/3 of Chapter II volume.",
    },
    "chap2_etat_connaissances": {
        "chapter_title": "Chapter II: Literature Review",
        "title": "2. State of Knowledge / Empirical Review (2/3)",
        "description": "Critical comparative synthesis of published studies (2/3 of Chapter II): designs, findings (Global/Africa/Cameroon), and limitations.",
        "guidance_points": [
            "Respect CAMES proportion: roughly 2/3 of the Literature Review chapter.",
            "Critically analyze comparable studies (Global → Africa → Cameroon): design, sample size, endpoints, numeric findings, and biases.",
            "Include the comparative synthesis table and conclude with a transition justifying your methodology.",
        ],
        "cames_concepts_required": [
            "Critical appraisal of validated literature methods and instruments",
            "Thematic comparison of results (frequencies, associated factors, outcomes) across high-income settings, Africa, and locally",
            "Concluding synthesis highlighting unresolved discrepancies addressed by the protocol",
        ],
        "concepts_to_avoid": ["Basic anatomical reviews or introductory definitions already covered in II.1"],
        "table_guidance": "Prior studies comparative synthesis table (Authors/Country/Year | Design | Population | Main Results | Limitations).",
        "figure_guidance": "Comparative charts or methodological figures from validated Open Access literature.",
        "notes": "CAMES target: ~2/3 of Chapter II volume.",
    },
    "chap3_type_site_duree": {
        "chapter_title": "Chapter III: Methodology",
        "title": "1–3. Study Design, Setting & Duration / Period",
        "description": "STROBE/SPIRIT/CAMES study design specification, detailed clinical setting description, and exact study timeline.",
        "guidance_points": [
            "Explicitly state study design (e.g., analytical cross-sectional, prospective/retrospective cohort, case-control, clinical trial).",
            "Describe hospital study sites (tier, location, bed capacity, technical facilities, justification of selection).",
            "Distinguish overall thesis duration from active participant recruitment/data collection period.",
        ],
        "cames_concepts_required": [
            "Exact study design classification (aim, temporality, single vs multicenter)",
            "Factual description of clinical wards and diagnostic capabilities",
            "Distinction between total study duration and active enrollment period",
        ],
        "concepts_to_avoid": ["Eligibility criteria (III.4)", "Budget details or statistical test formulas"],
    },
    "chap3_population_echantillon": {
        "chapter_title": "Chapter III: Methodology",
        "title": "4. Study Population & Sample Size Calculation",
        "description": "Target vs source population, STROBE eligibility criteria, sampling technique, and mathematical sample size calculation.",
        "guidance_points": [
            "Clearly distinguish target population from source population.",
            "Separate inclusion criteria, non-inclusion criteria (pre-enrollment ineligibility), and exclusion criteria (unusable records, withdrawal).",
            "Detail sampling method and present the sample size formula in LaTeX ($...$) with referenced parameter values.",
        ],
        "cames_concepts_required": [
            "Target population vs Source population",
            "Inclusion, non-inclusion, and exclusion criteria (strict CAMES methodological distinction)",
            "Sampling technique (probabilistic or non-probabilistic / consecutive / exhaustive)",
            "Sample size formula in LaTeX ($...$) justifying p, Zα, precision d, or statistical power",
        ],
        "concepts_to_avoid": ["Full variables list (already in Chapter I.5)"],
        "table_guidance": "Optional summary table of eligibility criteria or sample size parameters if extensive.",
    },
    "chap3_outils_procedure": {
        "chapter_title": "Chapter III: Methodology",
        "title": "5–6. Data Collection Tools & Procedures",
        "description": "Description of collection instruments and step-by-step chronological field workflow from screening to follow-up (referencing Appendices).",
        "guidance_points": [
            "Describe data collection instruments: structured Case Report Form (Appendix 1), validated clinical scales, devices, and pre-testing.",
            "Detail chronological field steps: participant screening, information (Appendix 2), consent (Appendix 3), data capture, follow-up, and quality control.",
        ],
        "cames_concepts_required": [
            "Structure of the Case Report Form (Appendix 1) and pre-testing/validation",
            "Step-by-step participant pathway (screening → consent → measurement → follow-up)",
            "Quality assurance and double verification procedures",
        ],
        "concepts_to_avoid": ["Reproducing the entire CRF inline (reserved for Appendix 1)"],
        "figure_guidance": "Operational procedure flowchart diagram (from screening through data validation).",
    },
    "chap3_analyse_donnees": {
        "chapter_title": "Chapter III: Methodology",
        "title": "7. Statistical Data Analysis",
        "description": "Comprehensive statistical analysis plan (descriptive, bivariate, multivariate) mapped to each specific objective.",
        "guidance_points": [
            "Specify data entry/management software and statistical packages (R, SPSS, Stata, EpiInfo).",
            "Detail descriptive statistics (mean ± SD or median [IQR] per normality; counts and percentages).",
            "Specify comparative tests (Chi-square/Fisher exact, Student t/Mann-Whitney, logistic regression) and significance threshold (95% CI, p < 0.05).",
        ],
        "cames_concepts_required": [
            "Database entry, cleaning, and handling of missing data",
            "Parametric and non-parametric descriptive statistics",
            "Bivariate comparative tests and multivariate modeling (confounder adjustment)",
            "Significance level (α = 5%, p < 0.05) and 95% confidence intervals",
        ],
        "concepts_to_avoid": ["Fabricated results", "Empty placeholder future result tables unless requested"],
    },
    "chap3_ethique": {
        "chapter_title": "Chapter III: Methodology",
        "title": "8. Ethical & Administrative Considerations",
        "description": "Compliance with Helsinki/CIOMS/CAMES ethics: IRB clearance, hospital administrative authorizations, informed consent, and data privacy.",
        "guidance_points": [
            "State submission to the Institutional Ethics Review Board (CIER-FMSB / IRB) and national ethics committee if applicable.",
            "Specify administrative authorizations from hospital directors (Appendices 4–5).",
            "Guarantee free informed consent (and assent for minors), non-maleficence, coded anonymization, and secure storage.",
        ],
        "cames_concepts_required": [
            "Explicit reference to the Declaration of Helsinki and bioethical principles (autonomy, beneficence, non-maleficence, justice)",
            "Ethical clearance (IRB / CIER) and hospital administrative approvals",
            "Practical anonymization, encrypted storage, and right to withdraw without penalty",
        ],
        "concepts_to_avoid": ["Full verbatim text of consent or information forms (reserved for Appendices 2 and 3)"],
    },
    "chap3_chronogramme": {
        "chapter_title": "Chapter III: Methodology",
        "title": "9. Work Schedule / Timeline",
        "description": "Complete provisional Gantt timeline from protocol approval through thesis defense.",
        "guidance_points": [
            "Present a structured Gantt chart covering all phases (protocol writing, ethics, pre-test, collection, analysis, thesis drafting, defense).",
        ],
        "cames_concepts_required": [
            "Logical monthly/quarterly sequencing of research activities",
            "Consistency with the study period stated in Section III.1–3",
        ],
        "concepts_to_avoid": ["Redundant long narrative paragraphs"],
        "table_guidance": "Gantt timeline table generated automatically from project.timeline_grid.",
    },
    "chap3_budget": {
        "chapter_title": "Chapter III: Methodology",
        "title": "10. Provisional Budget",
        "description": "Detailed, realistic financial cost estimate in FCFA specifying funding source.",
        "guidance_points": [
            "Itemize categories: stationery/printing, logistics/transport, lab/clinical supplies, statistical support, 5–10% contingency, and funding source.",
        ],
        "cames_concepts_required": [
            "Itemized financial table (Designation | Unit Cost | Quantity | Total in FCFA)",
            "Grand total and explicit funding source (self-funded / grant)",
        ],
        "concepts_to_avoid": ["Disproportionate narrative justifications"],
        "table_guidance": "Structured FCFA budget table generated from project.budget.",
    },
    "references": {
        "chapter_title": "REFERENCES",
        "title": "Bibliography (Vancouver Style)",
        "description": "Numbered reference list ordered by appearance in the text following strict ICMJE / CAMES Vancouver norms.",
        "guidance_points": [
            "Strict Vancouver formatting (numbered list, no table).",
            "Verify exact correspondence between every in-text citation [n] and the bibliography list.",
        ],
        "cames_concepts_required": [
            "Sequential ordering by order of first citation in the text",
            "Medline journal abbreviations and strict ICMJE punctuation (Authors. Title. Journal. Year;Vol(Issue):Pages.)",
        ],
        "concepts_to_avoid": ["Reference tables", "Uncited orphan bibliography entries"],
    },
    "annexes": {
        "chapter_title": "APPENDICES",
        "title": "Appendices (CRF, Information Sheet, Consent Form, Ethics & Admin Requests)",
        "description": "Numbered attachments referenced in the protocol (each appendix beginning on a new page).",
        "guidance_points": [
            "Appendix 1: Case Report Form / Data Collection Sheet (drawn checkboxes and input boxes)",
            "Appendix 2: Participant Information Sheet",
            "Appendix 3: Informed Consent Form (and assent form for minors if applicable)",
            "Appendices 4–7: Ethical clearance request, hospital authorizations, confidentiality declaration, and commitment letter",
        ],
        "cames_concepts_required": [
            "100% alignment between Appendix 1 CRF fields, Chapter I.5 variables, and Module 3 data mask",
            "Complete ethical compliance across participant information and consent forms",
        ],
        "concepts_to_avoid": ["Repeating body protocol paragraphs"],
        "table_guidance": "Appendix 1 rendered as a graphical Case Report Form with drawn checkboxes and input boxes.",
    },
}


def get_chapter_titles(lang: Optional[str]) -> Dict[str, str]:
    """Return official chapter titles in the requested protocol language."""
    return CHAPTER_TITLES_BY_LANG[normalize_lang(lang)]


def get_doc_labels(lang: Optional[str]) -> Dict[str, str]:
    """Return structural document labels (cover, TOC, tables, figures, CRF) in 'fr' or 'en'."""
    return DOC_LABELS_BY_LANG[normalize_lang(lang)]


def get_localized_canvas(lang: Optional[str]) -> List[CanvasSectionSpec]:
    """Return the 24-section CAMES/FMSB canvas localized to 'fr' or 'en' without altering IDs or constraints."""
    norm = normalize_lang(lang)
    if norm == "fr":
        return list(FMSB_SECTIONS)

    localized: List[CanvasSectionSpec] = []
    for spec in FMSB_SECTIONS:
        ov = _EN_CANVAS_OVERRIDES.get(spec.id)
        if not ov:
            localized.append(spec.model_copy(deep=True))
            continue

        new_constraints = spec.constraints.model_copy(
            update={
                k: ov[k]
                for k in ("table_guidance", "figure_guidance", "notes")
                if k in ov
            }
        )
        update_dict: Dict[str, Any] = {
            "chapter_title": ov.get("chapter_title", spec.chapter_title),
            "title": ov.get("title", spec.title),
            "description": ov.get("description", spec.description),
            "guidance_points": ov.get("guidance_points", spec.guidance_points),
            "cames_concepts_required": ov.get("cames_concepts_required", spec.cames_concepts_required),
            "concepts_to_avoid": ov.get("concepts_to_avoid", spec.concepts_to_avoid),
            "constraints": new_constraints,
        }
        localized.append(spec.model_copy(update=update_dict))
    return localized


def get_localized_section_spec(section_id: str, lang: Optional[str]) -> CanvasSectionSpec:
    """Return localized CanvasSectionSpec for a single section ID."""
    for spec in get_localized_canvas(lang):
        if spec.id == section_id:
            return spec
    raise ValueError(f"Section inconnue : {section_id}")


def get_language_prompt_directive(lang: Optional[str]) -> str:
    """Return strict LLM output-language instruction injected into drafting, research, audit, and PPTX prompts."""
    if normalize_lang(lang) == "en":
        return (
            "MANDATORY OUTPUT LANGUAGE: ENGLISH.\n"
            "- Write 100% of `content_markdown`, headings, `assistant_commentary`, `suggested_tables` "
            "(titles, headers, rows), `suggested_diagrams` (titles, captions, node labels, edge labels), "
            "summaries, and recommendations in formal academic medical English.\n"
            "- Only keep original French proper institution names when appropriate, and keep citation tags [n] intact."
        )
    return (
        "LANGUE DE RÉDACTION OBLIGATOIRE : FRANÇAIS.\n"
        "- Rédige 100 % du texte, des sous-titres, du commentaire pédagogique (`assistant_commentary`), "
        "des tableaux et schémas suggérés en français médical académique rigoureux conforme au référentiel CAMES / FMSB."
    )
