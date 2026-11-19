import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.data.fmsb_canvas import FMSB_SECTIONS
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
    def _section_title_for(self, section_id: str) -> str:
        for spec in FMSB_SECTIONS:
            if spec.id == section_id:
                return spec.title
        return section_id

    def _get_combined_section_markdown(
        self,
        project: ProtocolProject,
        section_ids: List[str],
    ) -> str:
        chunks: List[str] = []
        for sid in section_ids:
            sec = project.sections.get(sid)
            if sec and (sec.content_markdown or "").strip():
                chunks.append(f"## {self._section_title_for(sid)}\n{sec.content_markdown.strip()}")
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
            if ln.startswith("#") or re.match(r"^(CHAPITRE\b|\d+\.\d+(?:\.\d+)*\.?\s+)", ln, re.IGNORECASE):
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
        sec = project.sections.get("chap1_objectifs")
        raw = (sec.content_markdown if sec else "") or ""
        if not raw.strip():
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
            if "objectif général" in low or "objectif general" in low:
                mode = "general"
                continue
            if "objectifs spécifiques" in low or "objectifs specifiques" in low:
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
                item = re.sub(r"^([-*•]|\d+[.)]|OS\s*\d+\s*:?)\s*", "", clean_ln, flags=re.IGNORECASE).strip()
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

        formatted_specs = [
            f"OS {idx} : {txt}" for idx, txt in enumerate(obj_spec_lines, start=1)
        ] or ["Aucun objectif spécifique structuré détecté dans « Objectifs »."]

        return obj_general, formatted_specs

    def _extract_budget_pills(self, project: ProtocolProject) -> Tuple[str, List[str]]:
        """Compute real total FCFA and <= 10 concise pill labels from project.budget / chap3_budget."""
        items = list(project.budget or [])

        # Fallback: parse Markdown table in chap3_budget if structured list empty
        if not items:
            sec = project.sections.get("chap3_budget")
            raw = (sec.content_markdown if sec else "") or ""
            for ln in raw.splitlines():
                if not ln.strip().startswith("|") or "---" in ln:
                    continue
                cols = [c.strip() for c in ln.strip().strip("|").split("|")]
                if len(cols) >= 2:
                    label = _clean_text_for_display(cols[0])
                    if label.lower() in ("rubrique", "désignation", "libellé", "total", "total général"):
                        continue
                    # Find numeric value in trailing columns
                    amt = 0.0
                    for c in reversed(cols[1:]):
                        nums = re.findall(r"\d+", c.replace(" ", "").replace("\u202f", ""))
                        if nums:
                            try:
                                amt = float("".join(nums))
                                break
                            except ValueError:
                                pass
                    if label and amt > 0:
                        from app.schemas.protocol import BudgetItem

                        items.append(BudgetItem(designation=label, quantity=1, unit_cost_fcfa=amt, total_fcfa=amt))

        if not items:
            return "À budgétiser", []

        total_amount = sum(
            (b.total_fcfa if (b.total_fcfa and b.total_fcfa > 0) else (b.quantity * b.unit_cost_fcfa))
            for b in items
        )
        total_str = _format_fcfa(total_amount)

        # Sort / preserve order; consolidate extras if > 9 items so financing fits cleanly as 10th pill
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
            pills.append(f"Autres postes ({len(ordered) - 8})\n{_format_fcfa(rest_sum)}")
            pills.append("Financement :\nFonds propres du candidat")
            return total_str, pills

        pills = [
            f"{_shorten_label(b.designation, 32)}\n{_format_fcfa(b.total_fcfa if b.total_fcfa > 0 else b.quantity * b.unit_cost_fcfa)}"
            for b in ordered
        ]
        if len(pills) < 10:
            pills.append("Financement :\nFonds propres du candidat")
        return total_str, pills[:10]

    def _extract_variables_bullets(self, project: ProtocolProject) -> List[str]:
        """Group real study variables by category; ignore generic CRF seed variables when section is unwritten."""
        sec = project.sections.get("chap1_variables")
        raw = (sec.content_markdown if sec else "") or ""
        var_names = {(v.name or "").strip() for v in (project.variables or []) if (v.name or "").strip()}
        is_generic_seed_only = bool(var_names) and var_names.issubset(GENERIC_SEED_VARIABLE_NAMES)

        if project.variables and (raw.strip() or not is_generic_seed_only):
            by_cat: Dict[str, List[str]] = {}
            for v in project.variables:
                raw_cat = (v.category or "Variables d'étude").strip()
                cat = re.sub(r"^SECTION\s+[A-Z0-9]+\s*:\s*", "", raw_cat, flags=re.IGNORECASE).strip() or raw_cat
                cat_fmt = cat[:1].upper() + cat[1:].lower() if cat.isupper() else cat
                by_cat.setdefault(cat_fmt, [])
                clean_vname = re.sub(r"^[A-Z]\d+(?:\.\d+)*\.?\s*", "", (v.name or "").strip()).split("—")[0].strip()
                if clean_vname and clean_vname not in by_cat[cat_fmt]:
                    by_cat[cat_fmt].append(clean_vname)
            bullets: List[str] = []
            for cat, names in by_cat.items():
                preview = ", ".join(names[:4])
                suffix = f" (+{len(names) - 4} autres)" if len(names) > 4 else ""
                bullets.append(f"{cat} ({len(names)}) : {preview}{suffix}.")
            return bullets

        extracted = self._extract_bullets_from_markdown(raw, max_items=6)
        return extracted or [
            "Section « Liste des variables » non encore rédigée dans le protocole (Module 1)."
        ]

    def _extract_procedure_steps(self, project: ProtocolProject) -> List[Tuple[str, str]]:
        """Extract 3-4 sequential workflow steps from chap3_outils_procedure or its diagrams."""
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
                ("1. Préparation", "Section « Outils & Procédure » non encore rédigée dans le Module 1."),
            ]
        steps = []
        for idx, b in enumerate(bullets[:4], start=1):
            if ":" in b[:45]:
                t, rest = b.split(":", 1)
                clean_t = _truncate_words(t, 24)
                steps.append((f"{idx}. {clean_t}", _truncate_words(rest.strip(), 100)))
            else:
                steps.append((f"Étape {idx}", _truncate_words(b, 100)))
        return steps

    def _extract_chronogram_notes(self, project: ProtocolProject) -> str:
        if project.timeline_grid and project.timeline_grid.rows:
            lines = ["Chronogramme prévisionnel des activités du protocole :"]
            for r in project.timeline_grid.rows:
                periods = ", ".join(r.active_periods) if r.active_periods else "Planifié"
                lines.append(f"• {r.activity} ({periods})")
            return "\n".join(lines)
        sec = project.sections.get("chap3_chronogramme")
        if sec and (sec.content_markdown or "").strip():
            bullets = self._extract_bullets_from_markdown(sec.content_markdown, max_items=6)
            if bullets:
                return "Chronogramme des activités :\n" + "\n".join(f"• {b}" for b in bullets)
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

    def _build_presenter_notes(self, block_title: str, bullets: List[str]) -> str:
        if not bullets:
            return f"Présentation de la diapositive : {block_title}."
        intro = f"Pour cette diapositive consacrée à « {block_title} », nous mettons en avant les points suivants :"
        pts = "\n".join(f"- {b}" for b in bullets[:4])
        return f"{intro}\n{pts}"

    # =========================================================================
    # BLOCK BUILDERS (SHARED BY DETERMINISTIC & AI FLOWS)
    # =========================================================================
    def _build_invariant_header_slides(self, project: ProtocolProject) -> List[Slide]:
        cover = project.cover
        cand_name = (cover.author_name or "Impétrant(e) en Médecine").strip()
        cand_mat = (cover.matricule or "—").strip()
        dept_name = (cover.department_fr or "SANTE PUBLIQUE").strip()
        title_str = (cover.title or project.name or "Titre du protocole de recherche").strip()
        dir_name = (cover.director or "À renseigner").strip()
        co_dir_name = ", ".join(cover.co_directors) if cover.co_directors else "À renseigner"
        acad_year = (cover.academic_year or "2025-2026").strip()
        degree_lbl = (cover.degree_label or "Doctorat en Médecine Générale").strip()

        s1 = Slide(
            id="slide-1",
            slide_index=1,
            slide_type="welcome",
            section_key="cover_page",
            source_section_ids=["cover_page"],
            title="BIENVENUE À LA SOUTENANCE DU PROTOCOLE DE RECHERCHE",
            subtitle=f"DE L'IMPÉTRANT(E) : {cand_name.upper()}",
            breadcrumb="ACCUEIL SOUTENANCE",
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
                "Monsieur le Président du Jury, Honorables membres du jury, chers maîtres, chers collègues.\n"
                f"Soyez les bienvenus à la séance de soutenance de protocole de recherche de {cand_name}."
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
            subtitle=f"Directeur : {dir_name}\nCo-directeur(s) : {co_dir_name}",
            breadcrumb="PAGE DE GARDE OFFICIELLE",
            layout_mode="cover_fmsb",
            background_image_path=TPL_COVER_BG,
            elements=[
                SlideElement(id="el-2-dept", element_type="department", content=dept_name.upper(), order=1),
                SlideElement(
                    id="el-2-degree",
                    element_type="degree",
                    content=f"Protocole de thèse de {degree_lbl} rédigé par :",
                    order=2,
                ),
                SlideElement(
                    id="el-2-author",
                    element_type="author",
                    content=f"{cand_name}\nMatricule : {cand_mat}",
                    order=3,
                ),
                SlideElement(
                    id="el-2-directors",
                    element_type="directors",
                    content=f"Directeur : {dir_name}\nCo-directeur(s) : {co_dir_name}",
                    order=4,
                ),
                SlideElement(
                    id="el-2-year",
                    element_type="year",
                    content=f"Année académique {acad_year}",
                    order=5,
                ),
            ],
            presenter_notes=(
                "Monsieur le Président du Jury, Honorables membres du jury.\n"
                f"J'ai l'honneur de soumettre à votre appréciation notre protocole intitulé : « {title_str} ».\n"
                f"Ce travail est conduit au sein du Département de {dept_name} sous la direction de {dir_name}."
            ),
            is_validated=True,
        )

        s3 = Slide(
            id="slide-3",
            slide_index=3,
            slide_type="plan",
            title="PLAN DU DIAPORAMA",
            subtitle="Sommaire du Protocole de Recherche",
            breadcrumb="SOMMAIRE EXÉCUTIF",
            layout_mode="plan_6pills",
            background_image_path=TPL_PLAN,
            elements=[
                SlideElement(
                    id=f"el-3-pill-{idx+1}",
                    element_type="bullet_main",
                    content=p_txt,
                    order=idx + 1,
                )
                for idx, p_txt in enumerate(PLAN_6_AXES)
            ],
            presenter_notes=(
                "Notre présentation s'articule autour de six axes majeurs :\n"
                + "\n".join(PLAN_6_AXES)
            ),
            is_validated=True,
        )
        return [s1, s2, s3]

    def _build_invariant_footer_slides(self, project: ProtocolProject) -> List[Slide]:
        cover = project.cover
        total_budget_str, budget_pills = self._extract_budget_pills(project)

        s_chrono = Slide(
            id="slide-chrono",
            slide_index=1,
            slide_type="chronogram",
            section_key="chap3_chronogramme",
            source_section_ids=["chap3_chronogramme"],
            title="Chronogramme des activités",
            subtitle="Diagramme de Gantt prévisionnel",
            breadcrumb="5. CHRONOGRAMME",
            layout_mode="full_image_asis",
            background_image_path=TPL_CHRONOGRAM,
            elements=[],
            presenter_notes=self._extract_chronogram_notes(project),
            locked_as_is=True,
            is_validated=True,
        )

        s_budget = Slide(
            id="slide-budget",
            slide_index=1,
            slide_type="budget",
            section_key="chap3_budget",
            source_section_ids=["chap3_budget"],
            title="Budget prévisionnel",
            subtitle=total_budget_str,
            breadcrumb="6. BUDGET PRÉVISIONNEL",
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
                f"Le budget prévisionnel global de l'étude s'élève à {total_budget_str}. "
                "Il couvre l'ensemble des consommables, autorisations, logistique de collecte et analyses biostatistiques."
            ),
            is_validated=True,
        )

        cand = (cover.author_name or "Impétrant(e) FMSB").strip()
        contact = " • ".join(x for x in (cover.student_email, cover.student_phone) if x)
        medallion_msg = (
            "QUESTIONS & DISCUSSIONS\n\n"
            f"{cand}\n"
            "Faculté de Médecine et des Sciences Biomédicales\n"
            "Université de Yaoundé I"
            + (f"\n{contact}" if contact else "")
        )

        s_thanks = Slide(
            id="slide-thanks",
            slide_index=1,
            slide_type="acknowledgements",
            section_key="cover_page",
            source_section_ids=["cover_page"],
            title="Merci pour votre attention !",
            subtitle="Thank you for your kind attention!",
            breadcrumb="CLÔTURE SOUTENANCE",
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
                "Monsieur le Président du Jury, Honorables membres du jury, chers maîtres.\n"
                "Je vous remercie vivement de votre bienveillante attention et me tiens à votre disposition pour vos observations et questions."
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
                    title="Objectif général",
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
                    presenter_notes=f"L'objectif général de notre étude est le suivant : {obj_gen}",
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
                t = "Objectifs spécifiques" if n_grp == 1 else f"Objectifs spécifiques ({gi}/{n_grp})"
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
                        presenter_notes=self._build_presenter_notes(t, [e.content for e in grp]),
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
                        spec.title, [f"{e.title} : {e.content}" for e in step_els]
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
                sec_names = ", ".join(self._section_title_for(s) for s in spec.source_section_ids)
                bullets = [f"Section(s) « {sec_names} » non encore rédigée(s) dans le Module 1."]

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
                        t, [e.content for e in grp if e.element_type != "media"]
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
        """Auto-migrate legacy cached decks (< v3.1.0) and enforce Welcome-first ordering."""
        if deck is None or (deck.version or "") != CURRENT_DECK_VERSION:
            rebuilt = self.build_default_deck_from_protocol(project)
            return rebuilt, True
        order_changed = self.enforce_opening_order(deck)
        compute_deck_layout(deck, project=project)
        return deck, order_changed

    # =========================================================================
    # PUBLIC ENTRYPOINT 1: DETERMINISTIC BUILD FROM REAL PROTOCOL DATA
    # =========================================================================
    def build_default_deck_from_protocol(self, project: ProtocolProject) -> PresentationDeck:
        """Build the presentation deck directly from REAL project sections, variables, budget & figures."""
        deck = PresentationDeck(
            project_id=project.project_id,
            title=project.cover.title or project.name or "Protocole de Recherche FMSB",
            max_slides=None,
            theme_palette="fmsb_burgundy",
            version=CURRENT_DECK_VERSION,
            decision_notes="Génération déterministe depuis les données réelles du protocole (sans plafond de 15 slides).",
            models_used={"extraction": "deterministic_protocol_parser"},
        )

        slides: List[Slide] = []
        slides.extend(self._build_invariant_header_slides(project))

        for spec in PPTX_CANVAS_BLOCKS:
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
        """Stage 1 (Gemini 3.5 Flash Lite) + Stage 2 (Gemini 3.1 Flash Lite) for one block."""
        title_ctx = project.cover.title or project.name
        inst_clause = f"\nConsigne spécifique de l'utilisateur : {user_instruction}\n" if user_instruction else ""

        draft_prompt = (
            f"PROTOCOLE : {title_ctx}\n"
            f"BLOC DE SOUTENANCE : {spec.title} ({spec.breadcrumb})\n"
            f"DIRECTIVES CAMES : {spec.ai_guidance}\n"
            f"{inst_clause}\n"
            "TEXTE SOURCE RÉEL DU PROTOCOLE (ne rien inventer en dehors de ce contenu) :\n"
            f"{source_text[:6500]}\n\n"
            "Consigne : Résume fidèlement en puces courtes (<= 18 mots par puce, chiffres exacts conservés) "
            "et rédige un discours oral naturel de présentation devant le jury (75 à 120 mots)."
        )
        draft_res = self.router.generate_structured(
            role="drafting",
            contents=draft_prompt,
            schema=_DraftedBlockAI,
            system_instruction=(
                "Tu es un rapporteur scientifique FMSB/CAMES expert en soutenance médicale. "
                "Tu synthétises uniquement les faits présents dans le texte source, en français académique concis."
            ),
            temperature=0.2,
        )
        if models_used is not None:
            models_used["drafting"] = draft_res["model_used"]
        drafted: _DraftedBlockAI = draft_res["data"]

        # Stage 2: Audit & Element Tagging (role="audit" -> Gemini 3.1 Flash Lite)
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
            raise ValueError(f"Le résumé IA du bloc {spec.block_id} n'a produit aucun élément.")

        audit_prompt = (
            f"BLOC : {spec.title}\n"
            f"TEXTE SOURCE RÉEL :\n{source_text[:4000]}\n\n"
            "ÉLÉMENTS RÉSUMÉS À AUDITER ET ÉTIQUETER :\n"
            + "\n".join(f"{i+1}. {c}" for i, c in enumerate(candidate_items))
            + "\n\nVérifie qu'aucun chiffre, hôpital, test ou critère n'a été inventé. "
            "Corrige si besoin et étiquette chaque élément : bullet_main, stat_callout, ou procedure_step."
        )
        audit_res = self.router.generate_structured(
            role="audit",
            contents=audit_prompt,
            schema=_AuditedBlockAI,
            system_instruction=(
                "Tu es l'auditeur qualité FMSB/CAMES. Tu garantis la fidélité factuelle stricte au protocole "
                "et tu étiquettes chaque élément pour l'orchestrateur de mise en page."
            ),
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
            spec.title, [e.content for e in elements]
        )
        return elements, notes, spec.block_id

    def generate_full_deck_with_ai(self, project: ProtocolProject) -> PresentationDeck:
        """Orchestrate all 3 Gemini roles across real protocol blocks + Gemini Decision 1 layout."""
        models_used: Dict[str, str] = {}
        fallback_notes: List[str] = []

        # Collect approved figures for Gemini Decision 1
        approved_figs = self._find_approved_figures(project)
        fig_catalog = {img.asset_id: img for img in approved_figs}

        content_specs = [
            b
            for b in PPTX_CANVAS_BLOCKS
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

            if not source_txt.strip() or "non encore rédigée" in source_txt:
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
                fallback_notes.append(f"{spec.block_id}: repli déterministe ({exc})")
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
        overall_decision_notes = "Arbitrage spatial Gemini Decision 1 appliqué."
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
                            t, [e.content for e in grp if e.element_type != "media"]
                        ),
                        is_validated=True,
                    )
                )

        slides.extend(self._build_invariant_footer_slides(project))

        if fallback_notes:
            overall_decision_notes += " | Notes : " + "; ".join(fallback_notes[:4])

        deck = PresentationDeck(
            project_id=project.project_id,
            title=project.cover.title or project.name or "Protocole de Recherche FMSB",
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
        """Regenerate a single slide via Gemini 3.5 Flash Lite + Gemini 3.1 Flash Lite & update geometry."""
        target = next((s for s in deck.slides if s.slide_index == slide_index), None)
        if not target:
            raise ValueError(f"Diapositive {slide_index} introuvable.")

        if target.slide_type == "chronogram" or target.locked_as_is:
            compute_deck_layout(deck, project=project)
            return {
                "status": "locked",
                "slide": target,
                "deck": deck,
                "model_used": "locked_as_is",
                "models_used": {"chronogram": "locked_as_is"},
            }

        sec_ids = list(target.source_section_ids or ([] if not target.section_key else [target.section_key]))
        source_txt = self._get_combined_section_markdown(project, sec_ids)
        if not source_txt.strip() and target.section_key == "chap1_variables":
            v_bullets = self._extract_variables_bullets(project)
            if v_bullets and not any("non encore rédigée" in b.lower() for b in v_bullets):
                source_txt = "\n".join(v_bullets)
        if not source_txt.strip() and target.slide_type == "budget":
            b_total, b_pills = self._extract_budget_pills(project)
            if b_pills:
                source_txt = f"Budget total : {b_total}\n" + "\n".join(b_pills)

        if not source_txt.strip() and not (user_instruction or "").strip():
            compute_deck_layout(deck, project=project)
            return {
                "status": "missing_source",
                "message": "Cette section n'est pas encore rédigée dans le Module 1. Rédigez-la d'abord ou donnez une consigne explicite.",
                "slide": target,
                "deck": deck,
                "model_used": "none",
                "models_used": {},
            }

        if not source_txt.strip():
            source_txt = f"Sujet exact du protocole : {project.cover.title or project.name}\nSection visée : {target.title}"

        matched_spec = next(
            (b for b in PPTX_CANVAS_BLOCKS if b.slide_type == target.slide_type),
            PPTX_BLOCK_BY_ID["meth_type_site_duree"],
        )
        # Try to refine block match by section_key
        for b in PPTX_CANVAS_BLOCKS:
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
        elif any(e.element_type == "procedure_step" for e in els) and "schéma" in (user_instruction or "").lower():
            target.layout_mode = "procedure_flow"

        if target.layout_mode == "split_media" and media_els:
            target.elements = [*els[:4], media_els[0]]
        elif target.layout_mode == "procedure_flow":
            target.elements = els[:4]
        else:
            target.elements = els[:5]

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
        compute_deck_layout(deck, project=project)
        issues: List[PresentationAuditIssue] = []
        score = 100

        # 1. Check required slide types
        present_types = {s.slide_type for s in deck.slides}
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
                        message=f"Diapositive obligatoire manquante : {label}.",
                        suggestion="Utilisez « Réinitialiser » ou ajoutez ce type de diapositive.",
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
                        suggestion="Allégez la diapositive ou régénérez-la en style télégraphique (<= 6 lignes).",
                    )
                )
            for el in s.elements:
                if "non encore rédigée" in (el.content or "").lower():
                    score -= 6
                    issues.append(
                        PresentationAuditIssue(
                            slide_index=s.slide_index,
                            severity="warning",
                            category="missing_content",
                            message="Cette diapositive contient un marqueur de section non rédigée.",
                            suggestion="Rédigez d'abord la section dans le Module 1 puis régénérez.",
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
                                message=f"Taille de police {bb.font_size_pt} pt < {CAMES_MIN_BODY_PT} pt (norme CAMES).",
                                suggestion="Réduisez le nombre de mots ou passez en mise en page standard.",
                            )
                        )
                        break

        # 3. Check total budget coherence
        expected_total, _ = self._extract_budget_pills(project)
        budget_slide = next((s for s in deck.slides if s.slide_type == "budget"), None)
        if budget_slide and expected_total == "À budgétiser":
            issues.append(
                PresentationAuditIssue(
                    slide_index=budget_slide.slide_index,
                    severity="warning",
                    category="missing_content",
                    message="Le budget prévisionnel n'est pas encore renseigné dans le protocole (Module 1).",
                    suggestion="Complétez la section Budget dans le Module 1 puis réinitialisez.",
                )
            )
            score -= 4
        elif budget_slide and expected_total != "À budgétiser":
            if (budget_slide.subtitle or "").strip() != expected_total:
                score -= 8
                issues.append(
                    PresentationAuditIssue(
                        slide_index=budget_slide.slide_index,
                        severity="error",
                        category="budget",
                        message=f"Total affiché ({budget_slide.subtitle}) différent du budget du protocole ({expected_total}).",
                        suggestion="Cliquez sur « Réinitialiser » pour resynchroniser le budget réel.",
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
                    message=f"Durée orale estimée trop courte : {est_sec // 60} min {est_sec % 60:02d} s (cible CAMES : 10–15 min).",
                    suggestion="Enrichissez les notes du présentateur ou lancez « Générer tout par IA ».",
                )
            )
        elif est_sec > CAMES_MAX_DURATION_SEC:
            score -= 5
            issues.append(
                PresentationAuditIssue(
                    slide_index=1,
                    severity="warning",
                    category="duration",
                    message=f"Durée orale estimée élevée : {est_sec // 60} min {est_sec % 60:02d} s (max recommandé : 15 min).",
                    suggestion="Condensez les notes sur les diapositives secondaires.",
                )
            )

        # 5. Best-effort Gemini 3.1 Flash Lite executive review
        model_used = "deterministic_cames_auditor"
        summary = (
            f"Audit CAMES : {len(deck.slides)} diapositives, durée orale estimée {est_sec // 60} min {est_sec % 60:02d} s, "
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
                    f"Titre protocole : {project.cover.title}\n"
                    f"Total budget réel : {expected_total}\n"
                    f"Diaporama actuel ({len(deck.slides)} slides) :\n{slides_brief}\n\n"
                    "Fais un bilan synthétique (2-3 phrases) de la qualité scientifique et de la clarté pour le jury CAMES."
                ),
                schema=_AuditReportAI,
                system_instruction="Tu es membre d'un jury FMSB/CAMES. Réponse factuelle et concise.",
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
