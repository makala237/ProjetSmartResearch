import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import (
    get_language_prompt_directive,
    get_localized_canvas,
    normalize_lang,
)
from app.data.pptx_canvas import (
    CAMES_IDEAL_WPM,
    CAMES_MAX_DURATION_SEC,
    CAMES_MAX_LINES_PER_SLIDE,
    CAMES_MIN_BODY_PT,
    CAMES_MIN_DURATION_SEC,
    PLAN_6_AXES,
    PPTX_BLOCK_BY_ID,
    PPTX_CANVAS_BLOCKS,
    PptxBlockSpec,
    TPL_BUDGET,
    TPL_CHRONOGRAM,
    TPL_COVER_BG,
    TPL_INTRO,
    TPL_METHODOLOGY,
    TPL_OBJ_GEN,
    TPL_OBJ_SPEC,
    TPL_PLAN,
    TPL_THANKS,
    get_localized_pptx_blocks,
    get_plan_axes,
)
from app.schemas.presentation import (
    PresentationAuditIssue,
    PresentationAuditReport,
    PresentationDeck,
    QueueDeckItem,
    Slide,
    SlideElement,
)
from app.schemas.protocol import ImageAsset, ProtocolProject
from app.services.gemini_client import GeminiRouter
from app.services.slide_layout_engine import (
    compute_deck_layout,
    split_elements_to_fit_slides,
)

logger = logging.getLogger(__name__)
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
CURRENT_DECK_VERSION = "3.1.0"
GENERIC_SEED_VARIABLE_NAMES = {
    "A1. Âge du participant",
    "A2. Sexe",
    "A3. Consentement éclairé obtenu",
    "B1. Antécédents médico-chirurgicaux particuliers",
    "B2. Observations cliniques principales",
}


def _clean_text_for_display(text: str) -> str:
    """Strip Markdown formatting, HTML tags and citation brackets cleanly."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"[*_#`]+", "", clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def _format_fcfa(amount: float) -> str:
    rounded = int(round(amount or 0))
    return f"{rounded:,}".replace(",", " ") + " FCFA"


def _truncate_words(text: str, max_len: int = 155) -> str:
    txt = _clean_text_for_display(text)
    if len(txt) <= max_len:
        return txt
    cut = txt[: max_len - 1]
    if " " in cut and cut.rfind(" ") >= max_len // 2:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-") + "…"


def _shorten_label(label: str, max_len: int = 32) -> str:
    txt = _clean_text_for_display(label)
    short = re.sub(r"\s*\([^)]*\)", "", txt).strip()
    if len(short) <= max_len:
        return short
    if len(txt) <= max_len:
        return txt
    return _truncate_words(short, max_len)


# =========================================================================
# STRUCTURED SCHEMAS FOR THE 3-ROLE AI PIPELINE
# =========================================================================
class _ProcStepAI(BaseModel):
    step_title: str = Field(..., description="Titre court (2 à 4 mots)")
    step_detail: str = Field(..., description="Description concise (10 à 18 mots)")


class _StatCardAI(BaseModel):
    metric_value: str = Field(..., description="Chiffre ou indicateur court (ex: 12 %, n=120)")
    metric_detail: str = Field(..., description="Signification scientifique concise")


class _DraftedBlockAI(BaseModel):
    bullets: List[str] = Field(
        default_factory=list,
        description="Puces CAMES synthétiques (<= 18 mots chacune, fidèles aux faits du protocole)",
    )
    procedure_steps: List[_ProcStepAI] = Field(
        default_factory=list,
        description="Uniquement si le bloc décrit une procédure séquentielle (3 ou 4 étapes)",
    )
    stat_cards: List[_StatCardAI] = Field(
        default_factory=list,
        description="Optionnel : indicateurs numériques clés extraits du protocole",
    )
    presenter_speech: str = Field(
        default="",
        description="Discours oral naturel pour le présentateur devant le jury (75 à 120 mots, français académique)",
    )


class _TaggedElementAI(BaseModel):
    index: int
    element_type: str = Field(
        default="bullet_main",
        description="bullet_main | stat_callout | procedure_step",
    )
    refined_text: str = Field(..., description="Texte corrigé/vérifié fidèle au protocole")
    stat_label: str = Field(default="", description="Titre ou unité si stat_callout ou procedure_step")
    factual_ok: bool = Field(default=True)


class _AuditedBlockAI(BaseModel):
    elements: List[_TaggedElementAI] = Field(default_factory=list)
    fidelity_warnings: List[str] = Field(default_factory=list)


class _LayoutDirectiveAI(BaseModel):
    block_id: str
    preferred_layout: str = Field(
        default="standard",
        description="standard | split_media | procedure_flow | stat_grid",
    )
    attach_figure_asset_id: str = Field(
        default="",
        description="ID d'une figure approuvée pertinente si available, sinon vide",
    )
    rationale: str = ""


class _DecisionLayoutPlanAI(BaseModel):
    directives: List[_LayoutDirectiveAI] = Field(default_factory=list)
    overall_decision_notes: str = Field(default="")


class _AuditReportAI(BaseModel):
    summary: str
    extra_warnings: List[str] = Field(default_factory=list)


class PresentationOrchestrator:
    """Orchestration Engine for FMSB / CAMES Presentations.

    Combines deterministic real-protocol extractors, a 3-role Gemini AI pipeline
    (Gemini 3.5 Flash Lite drafting + Gemini 3.1 Flash Lite audit/tagging + Gemini Decision 1 layout),
    and the physical 16:9 slide_layout_engine.
    """

    def __init__(self) -> None:
        self.router = GeminiRouter()

    # =========================================================================
    # DETERMINISTIC REAL-PROTOCOL DATA EXTRACTORS
    # =========================================================================
    def _section_title_for(self, section_id: str, lang: str = "fr") -> str:
        for spec in get_localized_canvas(lang):
            if spec.id == section_id:
                return spec.title
        return section_id

    def _get_combined_section_markdown(
        self,
        project: ProtocolProject,
        section_ids: List[str],
    ) -> str:
        lang = normalize_lang(getattr(project, "language", "fr"))
        chunks: List[str] = []
        for sid in section_ids:
            sec = project.sections.get(sid)
            if sec and (sec.content_markdown or "").strip():
                chunks.append(f"## {self._section_title_for(sid, lang=lang)}\n{sec.content_markdown.strip()}")
        return "\n\n".join(chunks)

    def _extract_bullets_from_markdown(
        self,
        md_text: str,
        max_items: int = 8,
    ) -> List[str]:
        if not (md_text or "").strip():
            return []
        bullets: List[str] = []
        in_table = False

        for raw_line in md_text.splitlines():
            ln = raw_line.strip()
            if not ln:
                in_table = False
                continue
            if ln.startswith("|"):
                in_table = True
                continue
            if in_table and not ln.startswith("|"):
                in_table = False
            if ln.startswith("#") or re.match(r"^(CHAPITRE\b|CHAPTER\b|\d+\.\d+(?:\.\d+)*\.?\s+)", ln, re.IGNORECASE):
                continue

            # Explicit list item (exclude short numbered section headers without terminal punctuation)
            if re.match(r"^([-*•]|\d+[.)])\s+", ln):
                item = re.sub(r"^([-*•]|\d+[.)])\s+", "", ln)
                item = _clean_text_for_display(item)
                is_short_heading = len(item) < 60 and not item.endswith((".", "?", "!")) and ":" not in item
                if len(item) >= 18 and not is_short_heading:
                    bullets.append(_truncate_words(item, 155))
                continue

            # Narrative prose paragraph -> capture lead factual sentence per paragraph
            clean_p = _clean_text_for_display(ln)
            is_heading_line = len(clean_p) < 65 and not clean_p.endswith((".", "?", "!"))
            if len(clean_p) >= 30 and not is_heading_line:
                sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean_p) if len(s.strip()) >= 25]
                if sentences:
                    bullets.append(_truncate_words(sentences[0], 155))

        seen: set[str] = set()
        unique: List[str] = []
        for b in bullets:
            k = b.lower()
            if k not in seen:
                seen.add(k)
                unique.append(b)
            if len(unique) >= max_items:
                break
        return unique

    def _extract_objectives(self, project: ProtocolProject) -> Tuple[str, List[str]]:
        """Extract exact General Objective and Specific Objectives from chap1_objectifs."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        sec = project.sections.get("chap1_objectifs")
        raw = (sec.content_markdown if sec else "") or ""
        if not raw.strip():
            if lang == "en":
                return (
                    'Section "General Objective" not yet drafted in the protocol (Module 1).',
                    ['Section "Specific Objectives" not yet drafted in the protocol (Module 1).'],
                )
            return (
                "Section « Objectif général » non encore rédigée dans le protocole (Module 1).",
                ["Section « Objectifs spécifiques » non encore rédigée dans le protocole (Module 1)."],
            )

        obj_gen_lines: List[str] = []
        obj_spec_lines: List[str] = []
        mode = "none"

        for raw_ln in raw.splitlines():
            ln = raw_ln.strip()
            if not ln:
                continue
            low = ln.lower()
            if (
                "objectif général" in low
                or "objectif general" in low
                or "general objective" in low
                or "general aim" in low
            ):
                mode = "general"
                continue
            if (
                "objectifs spécifiques" in low
                or "objectifs specifiques" in low
                or "specific objectives" in low
            ):
                mode = "specific"
                continue
            if ln.startswith("#"):
                continue

            clean_ln = _clean_text_for_display(ln)
            if not clean_ln:
                continue
            if mode == "general":
                obj_gen_lines.append(clean_ln)
            elif mode == "specific":
                item = re.sub(r"^([-*•]|\d+[.)]|(?:OS|SO)\s*\d+\s*:?)\s*", "", clean_ln, flags=re.IGNORECASE).strip()
                if len(item) >= 10:
                    obj_spec_lines.append(item)

        obj_general = " ".join(obj_gen_lines).strip()
        if not obj_general:
            fallback = self._extract_bullets_from_markdown(raw, max_items=1)
            obj_general = fallback[0] if fallback else _clean_text_for_display(raw)[:240]

        if not obj_spec_lines:
            obj_spec_lines = [
                re.sub(r"^([-*•]|\d+[.)])\s*", "", b)
                for b in self._extract_bullets_from_markdown(raw, max_items=6)
                if b != obj_general
            ]

        prefix_lbl = "SO" if lang == "en" else "OS"
        sep = ": " if lang == "en" else " : "
        empty_spec = (
            'No structured specific objectives detected in "Objectives".'
            if lang == "en"
            else "Aucun objectif spécifique structuré détecté dans « Objectifs »."
        )
        formatted_specs = [
            f"{prefix_lbl} {idx}{sep}{txt}" for idx, txt in enumerate(obj_spec_lines, start=1)
        ] or [empty_spec]

        return obj_general, formatted_specs

    def _extract_budget_pills(self, project: ProtocolProject) -> Tuple[str, List[str]]:
        """Compute real total FCFA and <= 10 concise pill labels via the unified budget_engine."""
        from app.services.budget_engine import resolve_authoritative_budget

        lang = normalize_lang(getattr(project, "language", "fr"))
        items, total_amount = resolve_authoritative_budget(project)

        if not items or total_amount <= 0:
            return ("To be budgeted" if lang == "en" else "À budgétiser"), []

        total_str = _format_fcfa(total_amount)
        funding_pill = (
            "Funding:\nCandidate's own funds"
            if lang == "en"
            else "Financement :\nFonds propres du candidat"
        )

        # Preserve order; consolidate extras if > 9 items so financing fits cleanly as 10th pill
        ordered = list(items)
        if len(ordered) > 9:
            kept = ordered[:8]
            rest_sum = sum(
                (b.total_fcfa if b.total_fcfa > 0 else b.quantity * b.unit_cost_fcfa)
                for b in ordered[8:]
            )
            pills = [
                f"{_shorten_label(b.designation, 30)}\n{_format_fcfa(b.total_fcfa if b.total_fcfa > 0 else b.quantity * b.unit_cost_fcfa)}"
                for b in kept
            ]
            other_lbl = f"Other items ({len(ordered) - 8})" if lang == "en" else f"Autres postes ({len(ordered) - 8})"
            pills.append(f"{other_lbl}\n{_format_fcfa(rest_sum)}")
            pills.append(funding_pill)
            return total_str, pills

        pills = [
            f"{_shorten_label(b.designation, 32)}\n{_format_fcfa(b.total_fcfa if b.total_fcfa > 0 else b.quantity * b.unit_cost_fcfa)}"
            for b in ordered
        ]
        if len(pills) < 10:
            pills.append(funding_pill)
        return total_str, pills[:10]

    def _extract_variables_bullets(self, project: ProtocolProject) -> List[str]:
        """Group real study variables by category; ignore generic CRF seed variables when section is unwritten."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        sec = project.sections.get("chap1_variables")
        raw = (sec.content_markdown if sec else "") or ""
        var_names = {(v.name or "").strip() for v in (project.variables or []) if (v.name or "").strip()}
        is_generic_seed_only = bool(var_names) and var_names.issubset(GENERIC_SEED_VARIABLE_NAMES)

        if project.variables and (raw.strip() or not is_generic_seed_only):
            by_cat: Dict[str, List[str]] = {}
            default_cat = "Study variables" if lang == "en" else "Variables d'étude"
            for v in project.variables:
                raw_cat = (v.category or default_cat).strip()
                cat = re.sub(r"^SECTION\s+[A-Z0-9]+\s*:\s*", "", raw_cat, flags=re.IGNORECASE).strip() or raw_cat
                cat_fmt = cat[:1].upper() + cat[1:].lower() if cat.isupper() else cat
                by_cat.setdefault(cat_fmt, [])
                clean_vname = re.sub(r"^[A-Z]\d+(?:\.\d+)*\.?\s*", "", (v.name or "").strip()).split("—")[0].strip()
                if clean_vname and clean_vname not in by_cat[cat_fmt]:
                    by_cat[cat_fmt].append(clean_vname)
            bullets: List[str] = []
            for cat, names in by_cat.items():
                preview = ", ".join(names[:4])
                more_lbl = "more" if lang == "en" else "autres"
                suffix = f" (+{len(names) - 4} {more_lbl})" if len(names) > 4 else ""
                sep = ": " if lang == "en" else " : "
                bullets.append(f"{cat} ({len(names)}){sep}{preview}{suffix}.")
            return bullets

        extracted = self._extract_bullets_from_markdown(raw, max_items=6)
        return extracted or [
            'Section "Study Variables" not yet drafted in the protocol (Module 1).'
            if lang == "en"
            else "Section « Liste des variables » non encore rédigée dans le protocole (Module 1)."
        ]

    def _extract_procedure_steps(self, project: ProtocolProject) -> List[Tuple[str, str]]:
        """Extract 3-4 sequential workflow steps from chap3_outils_procedure or its diagrams."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        sec = project.sections.get("chap3_outils_procedure")
        if sec and sec.custom_diagrams:
            dg = sec.custom_diagrams[0]
            if dg.nodes and len(dg.nodes) >= 2:
                steps: List[Tuple[str, str]] = []
                for nd in dg.nodes[:4]:
                    detail = "; ".join(nd.items[:2]) if nd.items else nd.title
                    steps.append((_shorten_label(nd.title, 28), _shorten_label(detail, 110)))
                return steps

        raw = (sec.content_markdown if sec else "") or ""
        bullets = self._extract_bullets_from_markdown(raw, max_items=4)
        if not bullets:
            return [
                (
                    ("1. Preparation", 'Section "Data Collection Tools & Procedures" not yet drafted in Module 1.')
                    if lang == "en"
                    else ("1. Préparation", "Section « Outils & Procédure » non encore rédigée dans le Module 1.")
                ),
            ]
        steps = []
        for idx, b in enumerate(bullets[:4], start=1):
            if ":" in b[:45]:
                t, rest = b.split(":", 1)
                clean_t = _truncate_words(t, 24)
                steps.append((f"{idx}. {clean_t}", _truncate_words(rest.strip(), 100)))
            else:
                step_lbl = f"Step {idx}" if lang == "en" else f"Étape {idx}"
                steps.append((step_lbl, _truncate_words(b, 100)))
        return steps

    def _extract_chronogram_pills(self, project: ProtocolProject) -> List[str]:
        """Extract up to 7 chronological steps '<Period> : <Activity>' ordered top (latest) to bottom (earliest)."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        steps: List[Tuple[str, str]] = []
        tg = project.timeline_grid
        if tg and tg.rows:
            matched_rows = set()
            for p in tg.periods or []:
                active_acts: List[str] = []
                for idx, r in enumerate(tg.rows):
                    if p in (r.active_periods or []):
                        matched_rows.add(idx)
                        act = _shorten_label(r.activity, 46)
                        if act and act not in active_acts:
                            active_acts.append(act)
                if active_acts:
                    steps.append((_clean_text_for_display(p), _truncate_words(" ; ".join(active_acts), 68)))

            for idx, r in enumerate(tg.rows):
                if idx not in matched_rows:
                    fallback_step = f"Step {idx + 1}" if lang == "en" else f"Étape {idx + 1}"
                    p_lbl = ", ".join(r.active_periods or []) or fallback_step
                    steps.append((_clean_text_for_display(p_lbl), _shorten_label(r.activity, 55)))

        if not steps:
            sec = project.sections.get("chap3_chronogramme")
            if sec and sec.custom_tables:
                tbl = sec.custom_tables[0]
                for idx, row in enumerate(tbl.rows[:7]):
                    cells = [_clean_text_for_display(c) for c in row if (c or "").strip()]
                    if len(cells) >= 2:
                        steps.append((cells[1], _shorten_label(cells[0], 55)))
                    elif cells:
                        steps.append((f"Phase {idx + 1}", _shorten_label(cells[0], 55)))
            if not steps and sec and (sec.content_markdown or "").strip():
                bullets = self._extract_bullets_from_markdown(sec.content_markdown, max_items=7)
                for idx, b in enumerate(bullets):
                    if ":" in b[:30]:
                        p_lbl, act = b.split(":", 1)
                        steps.append((p_lbl.strip(), _shorten_label(act.strip(), 55)))
                    else:
                        step_lbl = f"Step {idx + 1}" if lang == "en" else f"Étape {idx + 1}"
                        steps.append((step_lbl, _shorten_label(b, 55)))

        if not steps:
            if lang == "en":
                steps = [
                    ("May 2026", "Protocol drafting"),
                    ("Jul 2026", "Approvals & ethical clearance"),
                    ("Oct 2026", "Data collection (Phase 1)"),
                    ("Mar 2027", "Data collection (Phase 2)"),
                    ("Apr 2027", "Statistical analysis"),
                    ("May 2027", "Thesis writing & revisions"),
                    ("Jun 2027", "Submission & public defense"),
                ]
            else:
                steps = [
                    ("Mai 2026", "Rédaction du protocole"),
                    ("Juil 2026", "Autorisations & clairance éthique"),
                    ("Oct 2026", "Collecte des données (Phase 1)"),
                    ("Mars 2027", "Collecte des données (Phase 2)"),
                    ("Avril 2027", "Analyse des données"),
                    ("Mai 2027", "Rédaction de thèse & corrections"),
                    ("Juin 2027", "Dépôt & soutenance publique"),
                ]

        kept = steps[:7]
        sep = ": " if lang == "en" else " : "
        return [f"{p}{sep}{a}" if p else a for p, a in reversed(kept)]

    def _extract_chronogram_notes(self, project: ProtocolProject) -> str:
        lang = normalize_lang(getattr(project, "language", "fr"))
        if project.timeline_grid and project.timeline_grid.rows:
            heading = (
                "Provisional research activity timeline:"
                if lang == "en"
                else "Chronogramme prévisionnel des activités du protocole :"
            )
            planned_lbl = "Scheduled" if lang == "en" else "Planifié"
            lines = [heading]
            for r in project.timeline_grid.rows:
                periods = ", ".join(r.active_periods) if r.active_periods else planned_lbl
                lines.append(f"• {r.activity} ({periods})")
            return "\n".join(lines)
        sec = project.sections.get("chap3_chronogramme")
        if sec and (sec.content_markdown or "").strip():
            bullets = self._extract_bullets_from_markdown(sec.content_markdown, max_items=6)
            if bullets:
                hdr = "Activity timeline:" if lang == "en" else "Chronogramme des activités :"
                return hdr + "\n" + "\n".join(f"• {b}" for b in bullets)
        if lang == "en":
            return (
                "Provisional activity timeline (Gantt chart):\n"
                "Visual overview of protocol drafting, approvals, data collection, analysis, and thesis defense."
            )
        return (
            "Chronogramme prévisionnel des activités (diagramme de Gantt) :\n"
            "Présentation visuelle des phases de rédaction, autorisations, collecte, analyse et soutenance."
        )

    def _find_approved_figures(
        self,
        project: ProtocolProject,
        preferred_section_ids: Optional[List[str]] = None,
    ) -> List[ImageAsset]:
        valid: List[ImageAsset] = []
        for img in project.images or []:
            if not img.approved or not img.local_path:
                continue
            p = Path(img.local_path)
            if not p.is_absolute():
                p = WORKSPACE_ROOT / p
            if p.exists():
                valid.append(img)
        if preferred_section_ids:
            pref_set = set(preferred_section_ids)
            valid.sort(key=lambda im: 0 if im.target_section_id in pref_set else 1)
        return valid

    def _build_presenter_notes(self, block_title: str, bullets: List[str], lang: str = "fr") -> str:
        norm_lang = normalize_lang(lang)
        if not bullets:
            return (
                f'Slide overview: "{block_title}".'
                if norm_lang == "en"
                else f"Présentation de la diapositive : {block_title}."
            )
        intro = (
            f'On this slide covering "{block_title}", we highlight the following key points:'
            if norm_lang == "en"
            else f"Pour cette diapositive consacrée à « {block_title} », nous mettons en avant les points suivants :"
        )
        pts = "\n".join(f"- {b}" for b in bullets[:4])
        return f"{intro}\n{pts}"

    # =========================================================================
    # BLOCK BUILDERS (SHARED BY DETERMINISTIC & AI FLOWS)
    # =========================================================================
    def _build_invariant_header_slides(self, project: ProtocolProject) -> List[Slide]:
        lang = normalize_lang(getattr(project, "language", "fr"))
        cover = project.cover
        default_cand = "Medical Candidate" if lang == "en" else "Impétrant(e) en Médecine"
        cand_name = (cover.author_name or default_cand).strip()
        cand_mat = (cover.matricule or "—").strip()
        if lang == "en":
            dept_name = (cover.department_en or cover.department_fr or "PUBLIC HEALTH").strip()
            default_title = "Research Protocol Title"
            default_missing = "To be specified"
            default_degree = "Doctor of Medicine"
        else:
            dept_name = (cover.department_fr or "SANTE PUBLIQUE").strip()
            default_title = "Titre du protocole de recherche"
            default_missing = "À renseigner"
            default_degree = "Doctorat en Médecine Générale"
        title_str = (cover.title or project.name or default_title).strip()
        dir_name = (cover.director or default_missing).strip()
        co_dir_name = ", ".join(cover.co_directors) if cover.co_directors else default_missing
        acad_year = (cover.academic_year or "2025-2026").strip()
        degree_lbl = (cover.degree_label or default_degree).strip()

        s1 = Slide(
            id="slide-1",
            slide_index=1,
            slide_type="welcome",
            section_key="cover_page",
            source_section_ids=["cover_page"],
            title=(
                "WELCOME TO THE RESEARCH PROTOCOL DEFENSE"
                if lang == "en"
                else "BIENVENUE À LA SOUTENANCE DU PROTOCOLE DE RECHERCHE"
            ),
            subtitle=(
                f"CANDIDATE: {cand_name.upper()}"
                if lang == "en"
                else f"DE L'IMPÉTRANT(E) : {cand_name.upper()}"
            ),
            breadcrumb="DEFENSE WELCOME" if lang == "en" else "ACCUEIL SOUTENANCE",
            layout_mode="welcome",
            background_image_path=TPL_COVER_BG,
            elements=[
                SlideElement(
                    id="el-1-welcome",
                    element_type="author",
                    content=cand_name.upper(),
                    order=1,
                )
            ],
            presenter_notes=(
                (
                    "Mr. Chairman of the Jury, Honorable Members of the Jury, dear teachers and colleagues.\n"
                    f"Welcome to the research protocol defense session of {cand_name}."
                )
                if lang == "en"
                else (
                    "Monsieur le Président du Jury, Honorables membres du jury, chers maîtres, chers collègues.\n"
                    f"Soyez les bienvenus à la séance de soutenance de protocole de recherche de {cand_name}."
                )
            ),
            is_validated=True,
        )

        s2 = Slide(
            id="slide-2",
            slide_index=2,
            slide_type="cover",
            section_key="cover_page",
            source_section_ids=["cover_page"],
            title=title_str,
            subtitle=(
                f"Supervisor: {dir_name}\nCo-supervisor(s): {co_dir_name}"
                if lang == "en"
                else f"Directeur : {dir_name}\nCo-directeur(s) : {co_dir_name}"
            ),
            breadcrumb="OFFICIAL COVER PAGE" if lang == "en" else "PAGE DE GARDE OFFICIELLE",
            layout_mode="cover_fmsb",
            background_image_path=TPL_COVER_BG,
            elements=[
                SlideElement(id="el-2-dept", element_type="department", content=dept_name.upper(), order=1),
                SlideElement(
                    id="el-2-degree",
                    element_type="degree",
                    content=(
                        f"{degree_lbl} Thesis Protocol prepared by:"
                        if lang == "en"
                        else f"Protocole de thèse de {degree_lbl} rédigé par :"
                    ),
                    order=2,
                ),
                SlideElement(
                    id="el-2-author",
                    element_type="author",
                    content=(
                        f"{cand_name}\nRegistration No.: {cand_mat}"
                        if lang == "en"
                        else f"{cand_name}\nMatricule : {cand_mat}"
                    ),
                    order=3,
                ),
                SlideElement(
                    id="el-2-directors",
                    element_type="directors",
                    content=(
                        f"Supervisor: {dir_name}\nCo-supervisor(s): {co_dir_name}"
                        if lang == "en"
                        else f"Directeur : {dir_name}\nCo-directeur(s) : {co_dir_name}"
                    ),
                    order=4,
                ),
                SlideElement(
                    id="el-2-year",
                    element_type="year",
                    content=f"Academic Year {acad_year}" if lang == "en" else f"Année académique {acad_year}",
                    order=5,
                ),
            ],
            presenter_notes=(
                (
                    "Mr. Chairman of the Jury, Honorable Jury Members.\n"
                    f'I have the honor to submit for your review our research protocol entitled: "{title_str}".\n'
                    f"This work is conducted within the Department of {dept_name} under the supervision of {dir_name}."
                )
                if lang == "en"
                else (
                    "Monsieur le Président du Jury, Honorables membres du jury.\n"
                    f"J'ai l'honneur de soumettre à votre appréciation notre protocole intitulé : « {title_str} ».\n"
                    f"Ce travail est conduit au sein du Département de {dept_name} sous la direction de {dir_name}."
                )
            ),
            is_validated=True,
        )

        axes = get_plan_axes(lang)
        s3 = Slide(
            id="slide-3",
            slide_index=3,
            slide_type="plan",
            title="PRESENTATION OUTLINE" if lang == "en" else "PLAN DU DIAPORAMA",
            subtitle="Research Protocol Summary" if lang == "en" else "Sommaire du Protocole de Recherche",
            breadcrumb="EXECUTIVE OUTLINE" if lang == "en" else "SOMMAIRE EXÉCUTIF",
            layout_mode="plan_6pills",
            background_image_path=TPL_PLAN,
            elements=[
                SlideElement(
                    id=f"el-3-pill-{idx+1}",
                    element_type="bullet_main",
                    content=p_txt,
                    order=idx + 1,
                )
                for idx, p_txt in enumerate(axes)
            ],
            presenter_notes=(
                ("Our presentation is structured around six main sections:\n" if lang == "en" else "Notre présentation s'articule autour de six axes majeurs :\n")
                + "\n".join(axes)
            ),
            is_validated=True,
        )
        return [s1, s2, s3]

    def _build_invariant_footer_slides(self, project: ProtocolProject) -> List[Slide]:
        lang = normalize_lang(getattr(project, "language", "fr"))
        cover = project.cover
        total_budget_str, budget_pills = self._extract_budget_pills(project)
        chrono_pills = self._extract_chronogram_pills(project)

        s_chrono = Slide(
            id="slide-chrono",
            slide_index=1,
            slide_type="chronogram",
            section_key="chap3_chronogramme",
            source_section_ids=["chap3_chronogramme"],
            title="Activity Timeline" if lang == "en" else "Chronogramme des activités",
            subtitle="Projected Gantt Chart" if lang == "en" else "Diagramme de Gantt prévisionnel",
            breadcrumb="5. TIMELINE" if lang == "en" else "5. CHRONOGRAMME",
            layout_mode="chronogram_7pills",
            background_image_path=TPL_CHRONOGRAM,
            elements=[
                SlideElement(
                    id=f"el-chrono-{idx + 1}",
                    element_type="pill",
                    content=pill_txt,
                    order=idx + 1,
                )
                for idx, pill_txt in enumerate(chrono_pills)
            ],
            presenter_notes=self._extract_chronogram_notes(project),
            locked_as_is=False,
            is_validated=True,
        )

        s_budget = Slide(
            id="slide-budget",
            slide_index=1,
            slide_type="budget",
            section_key="chap3_budget",
            source_section_ids=["chap3_budget"],
            title="Estimated Budget" if lang == "en" else "Budget prévisionnel",
            subtitle=total_budget_str,
            breadcrumb="6. ESTIMATED BUDGET" if lang == "en" else "6. BUDGET PRÉVISIONNEL",
            layout_mode="radial_budget",
            background_image_path=TPL_BUDGET,
            elements=[
                SlideElement(
                    id=f"el-budget-{idx+1}",
                    element_type="stat_callout" if idx == 0 else "bullet_main",
                    content=pill_txt,
                    order=idx + 1,
                )
                for idx, pill_txt in enumerate(budget_pills)
            ],
            presenter_notes=(
                (
                    f"The overall estimated budget for this study amounts to {total_budget_str}. "
                    "It covers consumables, ethical/administrative fees, field logistics, and biostatistical analysis."
                )
                if lang == "en"
                else (
                    f"Le budget prévisionnel global de l'étude s'élève à {total_budget_str}. "
                    "Il couvre l'ensemble des consommables, autorisations, logistique de collecte et analyses biostatistiques."
                )
            ),
            is_validated=True,
        )

        cand = (cover.author_name or ("Medical Candidate" if lang == "en" else "Impétrant(e) FMSB")).strip()
        contact = " • ".join(x for x in (cover.student_email, cover.student_phone) if x)
        if lang == "en":
            fac_lbl = (getattr(cover, "faculty_en", "") or getattr(cover, "faculty_fr", "") or "Faculty of Medicine and Biomedical Sciences").strip()
            uni_lbl = (getattr(cover, "university_en", "") or getattr(cover, "university_fr", "") or "The University of Yaoundé I").strip()
        else:
            fac_lbl = (getattr(cover, "faculty_fr", "") or "Faculté de Médecine et des Sciences Biomédicales").strip()
            uni_lbl = (getattr(cover, "university_fr", "") or "Université de Yaoundé I").strip()
        medallion_msg = (
            "QUESTIONS & DISCUSSIONS\n\n"
            f"{cand}\n"
            f"{fac_lbl}\n"
            f"{uni_lbl}"
            + (f"\n{contact}" if contact else "")
        )

        s_thanks = Slide(
            id="slide-thanks",
            slide_index=1,
            slide_type="acknowledgements",
            section_key="cover_page",
            source_section_ids=["cover_page"],
            title="Thank you for your attention!" if lang == "en" else "Merci pour votre attention !",
            subtitle="Merci pour votre aimable attention !" if lang == "en" else "Thank you for your kind attention!",
            breadcrumb="CLOSING" if lang == "en" else "CLÔTURE SOUTENANCE",
            layout_mode="acknowledgements",
            background_image_path=TPL_THANKS,
            elements=[
                SlideElement(
                    id="el-thanks-medallion",
                    element_type="bullet_main",
                    content=medallion_msg,
                    order=1,
                )
            ],
            presenter_notes=(
                (
                    "Mr. Chairman of the Jury, Honorable Jury Members, dear teachers.\n"
                    "Thank you very much for your kind attention. I now welcome your feedback and questions."
                )
                if lang == "en"
                else (
                    "Monsieur le Président du Jury, Honorables membres du jury, chers maîtres.\n"
                    "Je vous remercie vivement de votre bienveillante attention et me tiens à votre disposition pour vos observations et questions."
                )
            ),
            is_validated=True,
        )
        return [s_chrono, s_budget, s_thanks]

    def _build_deterministic_block_slides(
        self,
        project: ProtocolProject,
        spec: PptxBlockSpec,
        start_slide_num: int,
    ) -> List[Slide]:
        """Extract real protocol content for a content block & paginate via physical layout rules."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        slides_out: List[Slide] = []

        if spec.block_id == "objective_general":
            obj_gen, _ = self._extract_objectives(project)
            slides_out.append(
                Slide(
                    id=f"slide-{spec.block_id}",
                    slide_index=start_slide_num,
                    slide_type=spec.slide_type,
                    section_key="chap1_objectifs",
                    source_section_ids=["chap1_objectifs"],
                    title=spec.title,
                    breadcrumb=spec.breadcrumb,
                    layout_mode="standard",
                    background_image_path=spec.template_path,
                    elements=[
                        SlideElement(
                            id="el-obj-gen-1",
                            element_type="bullet_main",
                            content=obj_gen,
                            order=1,
                        )
                    ],
                    presenter_notes=(
                        f"The general objective of our study is: {obj_gen}"
                        if lang == "en"
                        else f"L'objectif général de notre étude est le suivant : {obj_gen}"
                    ),
                    is_validated=True,
                )
            )
            return slides_out

        if spec.block_id == "objective_specific":
            _, specs = self._extract_objectives(project)
            raw_els = [
                SlideElement(id=f"el-os-{i}", element_type="bullet_main", content=s, order=i)
                for i, s in enumerate(specs, start=1)
            ]
            groups = split_elements_to_fit_slides(raw_els, max_lines=CAMES_MAX_LINES_PER_SLIDE, font_pt=float(CAMES_MIN_BODY_PT))
            n_grp = len(groups)
            for gi, grp in enumerate(groups, start=1):
                t = spec.title if n_grp == 1 else f"{spec.title} ({gi}/{n_grp})"
                slides_out.append(
                    Slide(
                        id=f"slide-{spec.block_id}-{gi}",
                        slide_index=start_slide_num + gi - 1,
                        slide_type=spec.slide_type,
                        section_key="chap1_objectifs",
                        source_section_ids=["chap1_objectifs"],
                        title=t,
                        breadcrumb=spec.breadcrumb,
                        layout_mode="standard",
                        background_image_path=spec.template_path,
                        elements=grp,
                        presenter_notes=self._build_presenter_notes(t, [e.content for e in grp], lang=lang),
                        is_validated=True,
                    )
                )
            return slides_out

        if spec.block_id == "meth_procedure":
            steps = self._extract_procedure_steps(project)
            step_els = [
                SlideElement(
                    id=f"el-proc-{i}",
                    element_type="procedure_step",
                    title=st_title,
                    content=st_desc,
                    order=i,
                )
                for i, (st_title, st_desc) in enumerate(steps, start=1)
            ]
            slides_out.append(
                Slide(
                    id=f"slide-{spec.block_id}",
                    slide_index=start_slide_num,
                    slide_type=spec.slide_type,
                    section_key="chap3_outils_procedure",
                    source_section_ids=["chap3_outils_procedure"],
                    title=spec.title,
                    breadcrumb=spec.breadcrumb,
                    layout_mode="procedure_flow" if len(step_els) >= 2 else "standard",
                    background_image_path=spec.template_path,
                    elements=step_els,
                    presenter_notes=self._build_presenter_notes(
                        spec.title,
                        [f"{e.title}: {e.content}" if lang == "en" else f"{e.title} : {e.content}" for e in step_els],
                        lang=lang,
                    ),
                    is_validated=True,
                )
            )
            return slides_out

        # Other dynamic blocks (introduction, meth_type_site_duree, meth_population, meth_variables, meth_analyse_ethique)
        if spec.block_id == "meth_variables":
            bullets = self._extract_variables_bullets(project)
        else:
            combined_md = self._get_combined_section_markdown(project, spec.source_section_ids)
            bullets = self._extract_bullets_from_markdown(combined_md, max_items=10)
            if not bullets:
                sec_names = ", ".join(self._section_title_for(s, lang=lang) for s in spec.source_section_ids)
                bullets = [
                    f'Section(s) "{sec_names}" not yet drafted in Module 1.'
                    if lang == "en"
                    else f"Section(s) « {sec_names} » non encore rédigée(s) dans le Module 1."
                ]

        raw_els = [
            SlideElement(id=f"el-{spec.block_id}-{i}", element_type="bullet_main", content=b, order=i)
            for i, b in enumerate(bullets, start=1)
        ]

        # Attach an approved protocol figure if relevant & present
        has_fig = False
        if spec.supports_media:
            figs = self._find_approved_figures(project, preferred_section_ids=spec.media_section_ids)
            if figs:
                best_fig = figs[0]
                raw_els.append(
                    SlideElement(
                        id=f"el-{spec.block_id}-fig",
                        element_type="media",
                        title=best_fig.caption or best_fig.title,
                        content=best_fig.title,
                        icon_or_badge=best_fig.asset_id,
                        image_path=best_fig.local_path,
                        order=len(raw_els) + 1,
                    )
                )
                has_fig = True

        groups = split_elements_to_fit_slides(
            raw_els,
            max_lines=CAMES_MAX_LINES_PER_SLIDE,
            font_pt=float(CAMES_MIN_BODY_PT),
            has_media=has_fig,
        )[: spec.max_slides]

        n_grp = len(groups)
        for gi, grp in enumerate(groups, start=1):
            t = spec.title if n_grp == 1 else f"{spec.title} ({gi}/{n_grp})"
            has_slide_media = any(e.element_type == "media" for e in grp)
            slides_out.append(
                Slide(
                    id=f"slide-{spec.block_id}-{gi}",
                    slide_index=start_slide_num + gi - 1,
                    slide_type=spec.slide_type,
                    section_key=spec.source_section_ids[0] if spec.source_section_ids else None,
                    source_section_ids=list(spec.source_section_ids),
                    title=t,
                    breadcrumb=spec.breadcrumb,
                    layout_mode="split_media" if has_slide_media else "standard",
                    background_image_path=spec.template_path,
                    elements=grp,
                    presenter_notes=self._build_presenter_notes(
                        t, [e.content for e in grp if e.element_type != "media"], lang=lang
                    ),
                    is_validated=True,
                )
            )
        return slides_out

    def enforce_opening_order(self, deck: PresentationDeck) -> bool:
        """Guarantee that Welcome sits at slide 1, Cover at slide 2, and Plan at slide 3."""
        slides = list(deck.slides or [])
        if not slides:
            return False
        welcomes = [s for s in slides if s.slide_type == "welcome"]
        covers = [s for s in slides if s.slide_type == "cover"]
        plans = [s for s in slides if s.slide_type == "plan"]
        others = [s for s in slides if s.slide_type not in ("welcome", "cover", "plan")]
        ordered = welcomes + covers + plans + others
        changed = [s.id for s in ordered] != [s.id for s in slides]
        for idx, s in enumerate(ordered, start=1):
            if s.slide_index != idx:
                changed = True
            s.slide_index = idx
        deck.slides = ordered
        return changed

    def ensure_fresh_valid_deck(
        self,
        project: ProtocolProject,
        deck: Optional[PresentationDeck],
    ) -> Tuple[PresentationDeck, bool]:
        """Auto-migrate legacy cached decks (< v3.1.0 or legacy locked chronogram) and enforce Welcome-first ordering."""
        if deck is None or (deck.version or "") != CURRENT_DECK_VERSION:
            rebuilt = self.build_default_deck_from_protocol(project)
            return rebuilt, True

        migrated_chrono = False
        for s in deck.slides:
            if s.slide_type == "chronogram" and (
                s.locked_as_is
                or "Diapositive..Chronogramme" in (s.background_image_path or "")
                or (not s.elements and s.layout_mode in ("full_image_asis", "chronogram_7pills", "", None))
            ):
                s.background_image_path = TPL_CHRONOGRAM
                s.layout_mode = "chronogram_7pills"
                s.locked_as_is = False
                if not s.elements:
                    chrono_pills = self._extract_chronogram_pills(project)
                    s.elements = [
                        SlideElement(
                            id=f"el-chrono-{idx + 1}",
                            element_type="pill",
                            content=pill_txt,
                            order=idx + 1,
                        )
                        for idx, pill_txt in enumerate(chrono_pills)
                    ]
                if not (s.presenter_notes or "").strip():
                    s.presenter_notes = self._extract_chronogram_notes(project)
                migrated_chrono = True

        # Always keep the budget slide synchronized with the authoritative budget (Modules 1 & 2)
        synced_budget = False
        budget_slide = next((s for s in deck.slides if s.slide_type == "budget"), None)
        if budget_slide is not None:
            expected_total, expected_pills = self._extract_budget_pills(project)
            current_pills = [e.content for e in (budget_slide.elements or [])]
            if (
                expected_pills
                and (
                    (budget_slide.subtitle or "").strip() != expected_total
                    or current_pills != expected_pills
                )
            ):
                lang = normalize_lang(getattr(project, "language", "fr"))
                budget_slide.subtitle = expected_total
                budget_slide.elements = [
                    SlideElement(
                        id=f"el-budget-{idx+1}",
                        element_type="stat_callout" if idx == 0 else "bullet_main",
                        content=pill_txt,
                        order=idx + 1,
                    )
                    for idx, pill_txt in enumerate(expected_pills)
                ]
                budget_slide.presenter_notes = (
                    (
                        f"The overall estimated budget for this study amounts to {expected_total}. "
                        "It covers consumables, ethical/administrative fees, field logistics, and biostatistical analysis."
                    )
                    if lang == "en"
                    else (
                        f"Le budget prévisionnel global de l'étude s'élève à {expected_total}. "
                        "Il couvre l'ensemble des consommables, autorisations, logistique de collecte et analyses biostatistiques."
                    )
                )
                synced_budget = True

        order_changed = self.enforce_opening_order(deck)
        if synced_budget:
            try:
                compute_deck_layout(deck, project=project)
            except Exception:
                pass
            migrated_chrono = True
        compute_deck_layout(deck, project=project)
        return deck, (order_changed or migrated_chrono)

    # =========================================================================
    # PUBLIC ENTRYPOINT 1: DETERMINISTIC BUILD FROM REAL PROTOCOL DATA
    # =========================================================================
    def build_default_deck_from_protocol(self, project: ProtocolProject) -> PresentationDeck:
        """Build the presentation deck directly from REAL project sections, variables, budget & figures."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        localized_blocks = get_localized_pptx_blocks(lang)
        default_title = "FMSB Research Protocol" if lang == "en" else "Protocole de Recherche FMSB"
        deck = PresentationDeck(
            project_id=project.project_id,
            title=project.cover.title or project.name or default_title,
            max_slides=None,
            theme_palette="fmsb_burgundy",
            version=CURRENT_DECK_VERSION,
            decision_notes=(
                "Deterministic build from live protocol data (no 15-slide limit)."
                if lang == "en"
                else "Génération déterministe depuis les données réelles du protocole (sans plafond de 15 slides)."
            ),
            models_used={"extraction": "deterministic_protocol_parser"},
        )

        slides: List[Slide] = []
        slides.extend(self._build_invariant_header_slides(project))

        for spec in localized_blocks:
            if spec.block_id in ("welcome", "cover", "plan", "chronogram", "budget", "acknowledgements"):
                continue
            slides.extend(self._build_deterministic_block_slides(project, spec, start_slide_num=len(slides) + 1))

        slides.extend(self._build_invariant_footer_slides(project))
        deck.slides = slides
        deck.current_slide_index = 1
        self.enforce_opening_order(deck)
        compute_deck_layout(deck, project=project)

        deck.queue_items = [
            QueueDeckItem(
                id=f"queue-{s.slide_index}",
                section_id=s.section_key or s.slide_type,
                section_title=s.title,
                element_type=s.layout_mode,
                text_content=" | ".join(e.content for e in s.elements[:3]) or s.title,
                suggested_template=s.background_image_path or TPL_METHODOLOGY,
                suggested_slide_index=s.slide_index,
                presenter_cue=s.presenter_notes[:180],
            )
            for s in deck.slides
        ]
        return deck

    # =========================================================================
    # PUBLIC ENTRYPOINT 2: 3-ROLE AI FULL DECK ORCHESTRATION
    # =========================================================================
    def _ai_draft_and_audit_block(
        self,
        project: ProtocolProject,
        spec: PptxBlockSpec,
        source_text: str,
        user_instruction: str = "",
        models_used: Optional[Dict[str, str]] = None,
    ) -> Tuple[List[SlideElement], str, str]:
        """Stage 1 (primary reasoning model) + Stage 2 (audit/tagging) for one block."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        lang_directive = get_language_prompt_directive(lang)
        title_ctx = project.cover.title or project.name

        if lang == "en":
            inst_clause = f"\nUser specific instruction: {user_instruction}\n" if user_instruction else ""
            draft_prompt = (
                f"{lang_directive}\n\n"
                f"PROTOCOL: {title_ctx}\n"
                f"DEFENSE BLOCK: {spec.title} ({spec.breadcrumb})\n"
                f"CAMES GUIDELINES: {spec.ai_guidance}\n"
                f"{inst_clause}\n"
                "REAL PROTOCOL SOURCE TEXT (do not invent anything outside this content):\n"
                f"{source_text[:6500]}\n\n"
                "Task: Faithfully summarize into short CAMES bullets (<= 18 words each, preserving exact numbers) "
                "and write a natural oral presenter speech for the jury (75 to 120 words, in academic English)."
            )
            draft_sys = (
                "You are an expert FMSB/CAMES medical scientific rapporteur. "
                "You summarize only facts present in the source text in concise academic English."
            )
        else:
            inst_clause = f"\nConsigne spécifique de l'utilisateur : {user_instruction}\n" if user_instruction else ""
            draft_prompt = (
                f"{lang_directive}\n\n"
                f"PROTOCOLE : {title_ctx}\n"
                f"BLOC DE SOUTENANCE : {spec.title} ({spec.breadcrumb})\n"
                f"DIRECTIVES CAMES : {spec.ai_guidance}\n"
                f"{inst_clause}\n"
                "TEXTE SOURCE RÉEL DU PROTOCOLE (ne rien inventer en dehors de ce contenu) :\n"
                f"{source_text[:6500]}\n\n"
                "Consigne : Résume fidèlement en puces courtes (<= 18 mots par puce, chiffres exacts conservés) "
                "et rédige un discours oral naturel de présentation devant le jury (75 à 120 mots)."
            )
            draft_sys = (
                "Tu es un rapporteur scientifique FMSB/CAMES expert en soutenance médicale. "
                "Tu synthétises uniquement les faits présents dans le texte source, en français académique concis."
            )

        draft_res = self.router.generate_structured(
            role="drafting",
            contents=draft_prompt,
            schema=_DraftedBlockAI,
            system_instruction=draft_sys,
            temperature=0.2,
        )
        if models_used is not None:
            models_used["drafting"] = draft_res["model_used"]
        drafted: _DraftedBlockAI = draft_res["data"]

        # Stage 2: Audit & Element Tagging
        candidate_items: List[str] = []
        if spec.block_id == "meth_procedure" and drafted.procedure_steps:
            for st in drafted.procedure_steps:
                candidate_items.append(f"[STEP] {st.step_title}: {st.step_detail}")
        elif drafted.stat_cards and not drafted.bullets:
            for sc in drafted.stat_cards:
                candidate_items.append(f"[STAT] {sc.metric_value}: {sc.metric_detail}")
        else:
            for b in drafted.bullets:
                candidate_items.append(f"[BULLET] {b}")

        if not candidate_items:
            raise ValueError(
                f"AI summary for block {spec.block_id} produced no elements."
                if lang == "en"
                else f"Le résumé IA du bloc {spec.block_id} n'a produit aucun élément."
            )

        if lang == "en":
            audit_prompt = (
                f"{lang_directive}\n\n"
                f"BLOCK: {spec.title}\n"
                f"REAL SOURCE TEXT:\n{source_text[:4000]}\n\n"
                "SUMMARIZED ITEMS TO AUDIT & TAG:\n"
                + "\n".join(f"{i+1}. {c}" for i, c in enumerate(candidate_items))
                + "\n\nVerify that no figure, hospital, statistical test, or eligibility criterion was fabricated. "
                "Correct if needed, keep strictly in English, and tag each item: bullet_main, stat_callout, or procedure_step."
            )
            audit_sys = (
                "You are the FMSB/CAMES quality auditor. Guarantee strict factual fidelity to the protocol "
                "and tag each element for the slide layout orchestrator."
            )
        else:
            audit_prompt = (
                f"{lang_directive}\n\n"
                f"BLOC : {spec.title}\n"
                f"TEXTE SOURCE RÉEL :\n{source_text[:4000]}\n\n"
                "ÉLÉMENTS RÉSUMÉS À AUDITER ET ÉTIQUETER :\n"
                + "\n".join(f"{i+1}. {c}" for i, c in enumerate(candidate_items))
                + "\n\nVérifie qu'aucun chiffre, hôpital, test ou critère n'a été inventé. "
                "Corrige si besoin et étiquette chaque élément : bullet_main, stat_callout, ou procedure_step."
            )
            audit_sys = (
                "Tu es l'auditeur qualité FMSB/CAMES. Tu garantis la fidélité factuelle stricte au protocole "
                "et tu étiquettes chaque élément pour l'orchestrateur de mise en page."
            )

        audit_res = self.router.generate_structured(
            role="audit",
            contents=audit_prompt,
            schema=_AuditedBlockAI,
            system_instruction=audit_sys,
            temperature=0.1,
        )
        if models_used is not None:
            models_used["audit"] = audit_res["model_used"]
        audited: _AuditedBlockAI = audit_res["data"]

        elements: List[SlideElement] = []
        for idx, tag_el in enumerate(audited.elements, start=1):
            el_type = tag_el.element_type if tag_el.element_type in ("bullet_main", "stat_callout", "procedure_step") else "bullet_main"
            clean_txt = tag_el.refined_text.strip()
            clean_lbl = tag_el.stat_label.strip()
            if el_type == "procedure_step" and ":" in clean_txt and not clean_lbl:
                part_t, part_d = clean_txt.split(":", 1)
                clean_lbl = part_t.strip()
                clean_txt = part_d.strip()
            elements.append(
                SlideElement(
                    id=f"el-{spec.block_id}-ai-{idx}",
                    element_type=el_type,
                    title=clean_lbl or None,
                    sub_content=clean_lbl or None,
                    content=clean_txt,
                    order=idx,
                )
            )
        notes = drafted.presenter_speech.strip() or self._build_presenter_notes(
            spec.title, [e.content for e in elements], lang=lang
        )
        return elements, notes, spec.block_id

    def generate_full_deck_with_ai(self, project: ProtocolProject) -> PresentationDeck:
        """Orchestrate all 3 Gemini roles across real protocol blocks + Gemini Decision 1 layout."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        localized_blocks = get_localized_pptx_blocks(lang)
        models_used: Dict[str, str] = {}
        fallback_notes: List[str] = []

        # Collect approved figures for Gemini Decision 1
        approved_figs = self._find_approved_figures(project)
        fig_catalog = {img.asset_id: img for img in approved_figs}

        content_specs = [
            b
            for b in localized_blocks
            if b.block_id not in ("welcome", "cover", "plan", "chronogram", "budget", "acknowledgements")
        ]

        block_drafts: Dict[str, Tuple[List[SlideElement], str]] = {}

        # Stage 1 & 2 per dynamic block
        for spec in content_specs:
            if spec.block_id == "objective_general":
                obj_gen, _ = self._extract_objectives(project)
                source_txt = obj_gen
            elif spec.block_id == "objective_specific":
                _, os_list = self._extract_objectives(project)
                source_txt = "\n".join(os_list)
            elif spec.block_id == "meth_variables":
                v_bullets = self._extract_variables_bullets(project)
                source_txt = "\n".join(v_bullets) + "\n\n" + self._get_combined_section_markdown(project, spec.source_section_ids)
            else:
                source_txt = self._get_combined_section_markdown(project, spec.source_section_ids)

            if not source_txt.strip() or "non encore rédigée" in source_txt or "not yet drafted" in source_txt:
                det_slides = self._build_deterministic_block_slides(project, spec, 1)
                if det_slides:
                    block_drafts[spec.block_id] = (det_slides[0].elements, det_slides[0].presenter_notes)
                continue

            try:
                els, notes, _ = self._ai_draft_and_audit_block(
                    project=project,
                    spec=spec,
                    source_text=source_txt,
                    models_used=models_used,
                )
                block_drafts[spec.block_id] = (els, notes)
            except Exception as exc:
                logger.warning("AI drafting fallback on %s: %s", spec.block_id, exc)
                fallback_notes.append(f"{spec.block_id}: deterministic fallback ({exc})" if lang == "en" else f"{spec.block_id}: repli déterministe ({exc})")
                det_slides = self._build_deterministic_block_slides(project, spec, 1)
                merged_els: List[SlideElement] = []
                for ds in det_slides:
                    merged_els.extend(e for e in ds.elements if e.element_type != "media")
                block_drafts[spec.block_id] = (
                    merged_els,
                    det_slides[0].presenter_notes if det_slides else "",
                )

        # Stage 3: Gemini Decision 1 chooses optimal layout mode & figure placement
        directives_map: Dict[str, _LayoutDirectiveAI] = {}
        overall_decision_notes = (
            "CAMES slide layout optimized automatically."
            if lang == "en"
            else "Mise en page CAMES optimisée automatiquement."
        )
        try:
            queue_summary = [
                f"- {bid}: {len(els)} éléments (types={[e.element_type for e in els]})"
                for bid, (els, _) in block_drafts.items()
            ]
            figs_summary = (
                [
                    f"- asset_id={im.asset_id} | section={im.target_section_id} | titre={im.title}"
                    for im in approved_figs
                ]
                if approved_figs
                else ["Aucune figure approuvée disponible."]
            )
            dec_prompt = (
                f"{get_language_prompt_directive(lang)}\n\n"
                "Tu es Gemini Decision 1 (arbitre de mise en page PowerPoint CAMES 16:9).\n"
                "Règles : max 6 lignes par diapositive à 24pt, aucune limite de 15 diapositives.\n"
                "Choisis pour chaque bloc : standard, split_media (si figure approuvée pertinente), "
                "procedure_flow (si étapes séquentielles <= 4), ou stat_grid (si indicateurs chiffrés).\n\n"
                "BLOCS EN FILE D'ATTENTE :\n" + "\n".join(queue_summary) + "\n\n"
                "FIGURES APPROUVÉES DISPONIBLES :\n" + "\n".join(figs_summary)
            )
            dec_res = self.router.generate_structured(
                role="layout_decision",
                contents=dec_prompt,
                schema=_DecisionLayoutPlanAI,
                system_instruction="Décide la mise en page optimale sans jamais tronquer les objectifs ni la méthodologie.",
                temperature=0.1,
            )
            models_used["layout_decision"] = dec_res["model_used"]
            plan: _DecisionLayoutPlanAI = dec_res["data"]
            directives_map = {d.block_id: d for d in plan.directives}
            if plan.overall_decision_notes:
                overall_decision_notes = plan.overall_decision_notes
        except Exception as exc:
            logger.warning("Gemini Decision 1 layout fallback: %s", exc)
            models_used["layout_decision"] = "deterministic_layout_fallback"
            fallback_notes.append(f"Gemini Decision 1 repli déterministe ({exc})")

        # Assemble slides with physical anti-overflow pagination
        slides: List[Slide] = []
        slides.extend(self._build_invariant_header_slides(project))

        used_fig_ids: set[str] = set()
        for spec in content_specs:
            els, base_notes = block_drafts.get(spec.block_id, ([], ""))
            directive = directives_map.get(spec.block_id)
            pref_layout = directive.preferred_layout if directive else spec.layout_mode
            if spec.block_id in ("objective_general", "objective_specific"):
                pref_layout = "standard"

            # Attach requested or matching figure for split_media
            fig_el: Optional[SlideElement] = None
            if spec.supports_media and approved_figs:
                req_id = directive.attach_figure_asset_id if directive else ""
                chosen_fig = fig_catalog.get(req_id) if req_id in fig_catalog and req_id not in used_fig_ids else None
                if chosen_fig is None and pref_layout == "split_media":
                    avail = [f for f in approved_figs if f.asset_id not in used_fig_ids]
                    if avail:
                        chosen_fig = avail[0]
                if chosen_fig:
                    used_fig_ids.add(chosen_fig.asset_id)
                    fig_el = SlideElement(
                        id=f"el-{spec.block_id}-fig",
                        element_type="media",
                        title=chosen_fig.caption or chosen_fig.title,
                        content=chosen_fig.title,
                        icon_or_badge=chosen_fig.asset_id,
                        image_path=chosen_fig.local_path,
                        order=len(els) + 1,
                    )
                    pref_layout = "split_media"

            if pref_layout == "procedure_flow" and len([e for e in els if e.element_type != "media"]) >= 2:
                slides.append(
                    Slide(
                        id=f"slide-{spec.block_id}-1",
                        slide_index=len(slides) + 1,
                        slide_type=spec.slide_type,
                        section_key=spec.source_section_ids[0] if spec.source_section_ids else None,
                        source_section_ids=list(spec.source_section_ids),
                        title=spec.title,
                        breadcrumb=spec.breadcrumb,
                        layout_mode="procedure_flow",
                        background_image_path=spec.template_path,
                        elements=els[:4],
                        presenter_notes=base_notes,
                        is_validated=True,
                    )
                )
                continue

            if pref_layout == "stat_grid" and 2 <= len([e for e in els if e.element_type != "media"]) <= 4:
                slides.append(
                    Slide(
                        id=f"slide-{spec.block_id}-1",
                        slide_index=len(slides) + 1,
                        slide_type=spec.slide_type,
                        section_key=spec.source_section_ids[0] if spec.source_section_ids else None,
                        source_section_ids=list(spec.source_section_ids),
                        title=spec.title,
                        breadcrumb=spec.breadcrumb,
                        layout_mode="stat_grid",
                        background_image_path=spec.template_path,
                        elements=els[:4],
                        presenter_notes=base_notes,
                        is_validated=True,
                    )
                )
                continue

            working_els = list(els)
            if fig_el:
                working_els.append(fig_el)

            groups = split_elements_to_fit_slides(
                working_els,
                max_lines=CAMES_MAX_LINES_PER_SLIDE,
                font_pt=float(CAMES_MIN_BODY_PT),
                has_media=bool(fig_el),
            )[: spec.max_slides]

            n_grp = len(groups)
            for gi, grp in enumerate(groups, start=1):
                t = spec.title if n_grp == 1 else f"{spec.title} ({gi}/{n_grp})"
                has_m = any(e.element_type == "media" for e in grp)
                slides.append(
                    Slide(
                        id=f"slide-{spec.block_id}-{gi}",
                        slide_index=len(slides) + 1,
                        slide_type=spec.slide_type,
                        section_key=spec.source_section_ids[0] if spec.source_section_ids else None,
                        source_section_ids=list(spec.source_section_ids),
                        title=t,
                        breadcrumb=spec.breadcrumb,
                        layout_mode="split_media" if has_m else "standard",
                        background_image_path=spec.template_path,
                        elements=grp,
                        presenter_notes=base_notes if gi == 1 else self._build_presenter_notes(
                            t, [e.content for e in grp if e.element_type != "media"], lang=lang
                        ),
                        is_validated=True,
                    )
                )

        slides.extend(self._build_invariant_footer_slides(project))

        if fallback_notes:
            overall_decision_notes += " | Notes : " + "; ".join(fallback_notes[:4])

        default_title = "FMSB Research Protocol" if lang == "en" else "Protocole de Recherche FMSB"
        deck = PresentationDeck(
            project_id=project.project_id,
            title=project.cover.title or project.name or default_title,
            max_slides=None,
            slides=slides,
            current_slide_index=1,
            theme_palette="fmsb_burgundy",
            version=CURRENT_DECK_VERSION,
            decision_notes=overall_decision_notes,
            models_used=models_used,
        )
        self.enforce_opening_order(deck)
        compute_deck_layout(deck, project=project)
        deck.queue_items = [
            QueueDeckItem(
                id=f"queue-{s.slide_index}",
                section_id=s.section_key or s.slide_type,
                section_title=s.title,
                element_type=s.layout_mode,
                text_content=" | ".join(e.content for e in s.elements[:3]) or s.title,
                suggested_template=s.background_image_path or TPL_METHODOLOGY,
                suggested_slide_index=s.slide_index,
                presenter_cue=s.presenter_notes[:180],
            )
            for s in deck.slides
        ]
        return deck

    # =========================================================================
    # PUBLIC ENTRYPOINT 3: SINGLE SLIDE AI REGENERATION WITH INSTRUCTION
    # =========================================================================
    def generate_slide_ai_summary(
        self,
        project: ProtocolProject,
        deck: PresentationDeck,
        slide_index: int,
        user_instruction: str = "",
        preferred_layout: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Regenerate a single slide via Gemini reasoning + audit models & update geometry."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        localized_blocks = get_localized_pptx_blocks(lang)
        target = next((s for s in deck.slides if s.slide_index == slide_index), None)
        if not target:
            raise ValueError(
                f"Slide {slide_index} not found." if lang == "en" else f"Diapositive {slide_index} introuvable."
            )

        if target.locked_as_is:
            compute_deck_layout(deck, project=project)
            return {
                "status": "locked",
                "slide": target,
                "deck": deck,
                "model_used": "locked_as_is",
                "models_used": {"slide": "locked_as_is"},
            }

        sec_ids = list(target.source_section_ids or ([] if not target.section_key else [target.section_key]))
        source_txt = self._get_combined_section_markdown(project, sec_ids)
        if not source_txt.strip() and target.section_key == "chap1_variables":
            v_bullets = self._extract_variables_bullets(project)
            if v_bullets and not any(("non encore rédigée" in b.lower() or "not yet drafted" in b.lower()) for b in v_bullets):
                source_txt = "\n".join(v_bullets)
        if target.slide_type == "chronogram":
            chrono_pills = self._extract_chronogram_pills(project)
            parts: List[str] = []
            if chrono_pills:
                hdr = "Timeline steps (Period: Activity):\n" if lang == "en" else "Étapes du chronogramme (Période : Activité) :\n"
                parts.append(hdr + "\n".join(chrono_pills))
            if source_txt.strip():
                parts.append(source_txt)
            source_txt = "\n\n".join(parts)
        if not source_txt.strip() and target.slide_type == "budget":
            b_total, b_pills = self._extract_budget_pills(project)
            if b_pills:
                lbl = "Total budget:" if lang == "en" else "Budget total :"
                source_txt = f"{lbl} {b_total}\n" + "\n".join(b_pills)

        if not source_txt.strip() and not (user_instruction or "").strip():
            compute_deck_layout(deck, project=project)
            return {
                "status": "missing_source",
                "message": (
                    "This section has not yet been drafted in Module 1. Draft it first or provide an explicit instruction."
                    if lang == "en"
                    else "Cette section n'est pas encore rédigée dans le Module 1. Rédigez-la d'abord ou donnez une consigne explicite."
                ),
                "slide": target,
                "deck": deck,
                "model_used": "none",
                "models_used": {},
            }

        if not source_txt.strip():
            source_txt = (
                f"Exact protocol title: {project.cover.title or project.name}\nTarget slide: {target.title}"
                if lang == "en"
                else f"Sujet exact du protocole : {project.cover.title or project.name}\nSection visée : {target.title}"
            )

        matched_spec = next(
            (b for b in localized_blocks if b.slide_type == target.slide_type),
            next((b for b in localized_blocks if b.block_id == "meth_type_site_duree"), PPTX_BLOCK_BY_ID["meth_type_site_duree"]),
        )
        # Try to refine block match by section_key
        for b in localized_blocks:
            if target.section_key and target.section_key in b.source_section_ids and b.slide_type == target.slide_type:
                matched_spec = b
                break

        models_used: Dict[str, str] = {}
        els, notes, _ = self._ai_draft_and_audit_block(
            project=project,
            spec=matched_spec,
            source_text=source_txt,
            user_instruction=user_instruction,
            models_used=models_used,
        )

        # Preserve attached media element if split_media requested or previously present
        media_els = [e for e in target.elements if e.element_type == "media"]
        if preferred_layout:
            target.layout_mode = preferred_layout
        elif any(e.element_type == "procedure_step" for e in els) and any(w in (user_instruction or "").lower() for w in ("schéma", "schema", "diagram", "flow")):
            target.layout_mode = "procedure_flow"

        sep = ": " if lang == "en" else " : "
        if target.slide_type == "chronogram" and (target.layout_mode in ("chronogram_7pills", "", None)):
            target.elements = [
                SlideElement(
                    id=el.id,
                    element_type="pill",
                    content=(f"{el.title}{sep}{el.content}" if el.title and ":" not in el.content else el.content),
                    order=i + 1,
                )
                for i, el in enumerate(els[:7])
            ]
        elif target.layout_mode == "split_media" and media_els:
            target.elements = [*els[:4], media_els[0]]
        elif target.layout_mode == "procedure_flow":
            target.elements = els[:4]
        else:
            target.elements = els[:7] if target.slide_type == "chronogram" else els[:5]

        target.presenter_notes = notes
        compute_deck_layout(deck, project=project)

        return {
            "status": "ok",
            "slide": target,
            "deck": deck,
            "model_used": models_used.get("drafting", "gemini"),
            "models_used": models_used,
        }

    # =========================================================================
    # PUBLIC ENTRYPOINT 4: CAMES / PROTOCOL FIDELITY AUDIT
    # =========================================================================
    def audit_presentation_deck(
        self,
        project: ProtocolProject,
        deck: PresentationDeck,
    ) -> PresentationAuditReport:
        """Audit full deck deterministically for CAMES rules + optional AI scientific review."""
        lang = normalize_lang(getattr(project, "language", "fr"))
        compute_deck_layout(deck, project=project)
        issues: List[PresentationAuditIssue] = []
        score = 100

        # 1. Check required slide types
        present_types = {s.slide_type for s in deck.slides}
        if lang == "en":
            required_types = [
                ("welcome", "Welcome"),
                ("cover", "Official Cover Page"),
                ("plan", "Presentation Outline"),
                ("introduction", "Introduction"),
                ("objective_general", "General Objective"),
                ("objective_specific", "Specific Objectives"),
                ("methodology", "Methodology"),
                ("chronogram", "Timeline"),
                ("budget", "Estimated Budget"),
                ("acknowledgements", "Acknowledgements"),
            ]
        else:
            required_types = [
                ("welcome", "Accueil"),
                ("cover", "Page de garde officielle"),
                ("plan", "Plan du diaporama"),
                ("introduction", "Introduction"),
                ("objective_general", "Objectif général"),
                ("objective_specific", "Objectifs spécifiques"),
                ("methodology", "Méthodologie"),
                ("chronogram", "Chronogramme"),
                ("budget", "Budget prévisionnel"),
                ("acknowledgements", "Remerciements"),
            ]
        for req_t, label in required_types:
            if req_t not in present_types:
                score -= 10
                issues.append(
                    PresentationAuditIssue(
                        slide_index=1,
                        severity="error",
                        category="missing_content",
                        message=(
                            f"Missing required slide: {label}."
                            if lang == "en"
                            else f"Diapositive obligatoire manquante : {label}."
                        ),
                        suggestion=(
                            'Click "Reset from Protocol" or add this slide type.'
                            if lang == "en"
                            else "Utilisez « Réinitialiser » ou ajoutez ce type de diapositive."
                        ),
                    )
                )

        # 2. Check per-slide readability, overflow & empty markers
        total_words = 0
        for s in deck.slides:
            total_words += len(re.findall(r"\S+", s.presenter_notes or ""))
            for w_msg in s.cames_warnings:
                score -= 4
                issues.append(
                    PresentationAuditIssue(
                        slide_index=s.slide_index,
                        severity="warning",
                        category="overflow",
                        message=w_msg,
                        suggestion=(
                            "Trim text or regenerate in CAMES telegraphic style (<= 6 lines)."
                            if lang == "en"
                            else "Allégez la diapositive ou régénérez-la en style télégraphique (<= 6 lignes)."
                        ),
                    )
                )
            for el in s.elements:
                low_el = (el.content or "").lower()
                if "non encore rédigée" in low_el or "not yet drafted" in low_el:
                    score -= 6
                    issues.append(
                        PresentationAuditIssue(
                            slide_index=s.slide_index,
                            severity="warning",
                            category="missing_content",
                            message=(
                                "This slide contains an undrafted section placeholder."
                                if lang == "en"
                                else "Cette diapositive contient un marqueur de section non rédigée."
                            ),
                            suggestion=(
                                "Draft the source section in Module 1 first, then regenerate."
                                if lang == "en"
                                else "Rédigez d'abord la section dans le Module 1 puis régénérez."
                            ),
                        )
                    )
            if s.slide_type in ("introduction", "objective_general", "objective_specific", "methodology"):
                body_boxes = [b for b in s.render_boxes if b.role in ("bullet_card",)]
                for bb in body_boxes:
                    if bb.font_size_pt < CAMES_MIN_BODY_PT:
                        score -= 3
                        issues.append(
                            PresentationAuditIssue(
                                slide_index=s.slide_index,
                                severity="warning",
                                category="font_size",
                                message=(
                                    f"Font size {bb.font_size_pt} pt < {CAMES_MIN_BODY_PT} pt (CAMES standard)."
                                    if lang == "en"
                                    else f"Taille de police {bb.font_size_pt} pt < {CAMES_MIN_BODY_PT} pt (norme CAMES)."
                                ),
                                suggestion=(
                                    "Reduce word count or switch to standard layout."
                                    if lang == "en"
                                    else "Réduisez le nombre de mots ou passez en mise en page standard."
                                ),
                            )
                        )
                        break

        # 3. Check total budget coherence
        expected_total, _ = self._extract_budget_pills(project)
        budget_slide = next((s for s in deck.slides if s.slide_type == "budget"), None)
        if budget_slide and expected_total in ("À budgétiser", "To be budgeted"):
            issues.append(
                PresentationAuditIssue(
                    slide_index=budget_slide.slide_index,
                    severity="warning",
                    category="missing_content",
                    message=(
                        "Estimated budget has not been filled in Module 1 yet."
                        if lang == "en"
                        else "Le budget prévisionnel n'est pas encore renseigné dans le protocole (Module 1)."
                    ),
                    suggestion=(
                        'Complete the Budget section in Module 1, then click "Reset".'
                        if lang == "en"
                        else "Complétez la section Budget dans le Module 1 puis réinitialisez."
                    ),
                )
            )
            score -= 4
        elif budget_slide and expected_total not in ("À budgétiser", "To be budgeted"):
            if (budget_slide.subtitle or "").strip() != expected_total:
                score -= 8
                issues.append(
                    PresentationAuditIssue(
                        slide_index=budget_slide.slide_index,
                        severity="error",
                        category="budget",
                        message=(
                            f"Displayed total ({budget_slide.subtitle}) differs from protocol budget ({expected_total})."
                            if lang == "en"
                            else f"Total affiché ({budget_slide.subtitle}) différent du budget du protocole ({expected_total})."
                        ),
                        suggestion=(
                            'Click "Reset" to resynchronize with the actual budget.'
                            if lang == "en"
                            else "Cliquez sur « Réinitialiser » pour resynchroniser le budget réel."
                        ),
                    )
                )

        # 4. Check CAMES oral duration (10 to 15 min at 130 wpm)
        est_sec = int(round((total_words / CAMES_IDEAL_WPM) * 60)) if total_words else 0
        if est_sec < CAMES_MIN_DURATION_SEC:
            score -= 5
            issues.append(
                PresentationAuditIssue(
                    slide_index=1,
                    severity="info",
                    category="duration",
                    message=(
                        f"Estimated oral duration too short: {est_sec // 60} min {est_sec % 60:02d} s (CAMES target: 10–15 min)."
                        if lang == "en"
                        else f"Durée orale estimée trop courte : {est_sec // 60} min {est_sec % 60:02d} s (cible CAMES : 10–15 min)."
                    ),
                    suggestion=(
                        'Expand speaker notes or run "Generate All with AI".'
                        if lang == "en"
                        else "Enrichissez les notes du présentateur ou lancez « Générer tout par IA »."
                    ),
                )
            )
        elif est_sec > CAMES_MAX_DURATION_SEC:
            score -= 5
            issues.append(
                PresentationAuditIssue(
                    slide_index=1,
                    severity="warning",
                    category="duration",
                    message=(
                        f"Estimated oral duration high: {est_sec // 60} min {est_sec % 60:02d} s (max recommended: 15 min)."
                        if lang == "en"
                        else f"Durée orale estimée élevée : {est_sec // 60} min {est_sec % 60:02d} s (max recommandé : 15 min)."
                    ),
                    suggestion=(
                        "Condense presenter notes on secondary slides."
                        if lang == "en"
                        else "Condensez les notes sur les diapositives secondaires."
                    ),
                )
            )

        # 5. Best-effort executive AI review
        model_used = "deterministic_cames_auditor"
        summary = (
            f"CAMES Audit: {len(deck.slides)} slides, estimated oral duration {est_sec // 60} min {est_sec % 60:02d} s, "
            f"{len(issues)} issue(s) noted."
            if lang == "en"
            else f"Audit CAMES : {len(deck.slides)} diapositives, durée orale estimée {est_sec // 60} min {est_sec % 60:02d} s, "
            f"{len(issues)} point(s) d'attention relevé(s)."
        )
        try:
            slides_brief = "\n".join(
                f"{s.slide_index}. [{s.slide_type}] {s.title} -> "
                + " ; ".join(e.content[:65] for e in s.elements[:3])
                for s in deck.slides
            )
            ai_res = self.router.generate_structured(
                role="audit",
                contents=(
                    f"{get_language_prompt_directive(lang)}\n\n"
                    f"Protocol title: {project.cover.title}\n"
                    f"Actual budget total: {expected_total}\n"
                    f"Current deck ({len(deck.slides)} slides):\n{slides_brief}\n\n"
                    + (
                        "Provide a concise 2–3 sentence executive assessment of scientific quality and clarity for the CAMES jury in English."
                        if lang == "en"
                        else "Fais un bilan synthétique (2-3 phrases) de la qualité scientifique et de la clarté pour le jury CAMES."
                    )
                ),
                schema=_AuditReportAI,
                system_instruction=(
                    "You are an FMSB/CAMES thesis defense jury member. Respond factually and concisely in English."
                    if lang == "en"
                    else "Tu es membre d'un jury FMSB/CAMES. Réponse factuelle et concise."
                ),
                temperature=0.1,
            )
            model_used = ai_res["model_used"]
            ai_data: _AuditReportAI = ai_res["data"]
            if ai_data.summary:
                summary = ai_data.summary
            for ew in ai_data.extra_warnings[:3]:
                issues.append(
                    PresentationAuditIssue(
                        slide_index=1,
                        severity="info",
                        category="fidelity",
                        message=ew,
                    )
                )
        except Exception as exc:
            logger.info("Optional AI audit summary skipped (%s); deterministic audit returned.", exc)

        final_score = max(0, min(100, score))
        has_errors = any(i.severity == "error" for i in issues)
        report = PresentationAuditReport(
            score=final_score,
            total_slides=len(deck.slides),
            estimated_duration_sec=est_sec,
            cames_compliant=(final_score >= 80 and not has_errors),
            model_used=model_used,
            summary=summary,
            issues=issues,
        )
        deck.last_audit = report
        return report


def refresh_budget_slide_if_present(
    project: ProtocolProject,
    deck: Optional[PresentationDeck] = None,
) -> bool:
    """Keep an existing PowerPoint deck's 'budget' slide strictly synchronized with project.budget / chap3_budget."""
    loaded_from_storage = False
    if deck is None:
        try:
            from app.services.storage import project_storage

            deck = project_storage.get_presentation(project.project_id)
            loaded_from_storage = True
        except Exception:
            deck = None

    if not deck or not getattr(deck, "slides", None):
        return False

    budget_slide = next((s for s in deck.slides if getattr(s, "slide_type", "") == "budget"), None)
    if budget_slide is None:
        return False

    orch = PresentationOrchestrator()
    total_str, pills = orch._extract_budget_pills(project)
    lang = normalize_lang(getattr(project, "language", "fr"))
    budget_slide.subtitle = total_str
    budget_slide.elements = [
        SlideElement(
            id=f"el-budget-{idx+1}",
            element_type="stat_callout" if idx == 0 else "bullet_main",
            content=pill_txt,
            order=idx + 1,
        )
        for idx, pill_txt in enumerate(pills)
    ]
    budget_slide.presenter_notes = (
        (
            f"The overall estimated budget for this study amounts to {total_str}. "
            "It covers consumables, ethical/administrative fees, field logistics, and biostatistical analysis."
        )
        if lang == "en"
        else (
            f"Le budget prévisionnel global de l'étude s'élève à {total_str}. "
            "Il couvre l'ensemble des consommables, autorisations, logistique de collecte et analyses biostatistiques."
        )
    )
    try:
        compute_deck_layout(deck, project=project)
    except Exception:
        pass

    if loaded_from_storage:
        try:
            from app.services.storage import project_storage

            project_storage.save_presentation(deck)
        except Exception:
            pass
    return True
