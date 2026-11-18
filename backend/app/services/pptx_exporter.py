import logging
from pathlib import Path
from typing import Any, Optional
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

from app.schemas.presentation import PresentationDeck, Slide, SlideElement
from app.services.slide_templates import (
    COLORS,
    SLIDE_WIDTH_IN,
    SLIDE_HEIGHT_IN,
)

logger = logging.getLogger(__name__)

# User template directories & files
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = PROJECT_ROOT / "tamplates"
BASE_PPTX = PROJECT_ROOT / "tamplatePowerpointProtocole.pptx"

TPL_COVER_BG = TEMPLATE_DIR / "tamplate image de fond première et deuxième diapositive.png"
TPL_PLAN = TEMPLATE_DIR / "tamplate diapositive Plan.png"
TPL_INTRO = TEMPLATE_DIR / "tamplate diapositive introduction.png"
TPL_OBJ_GEN = TEMPLATE_DIR / "tamplate diapositive objectif générale.png"
TPL_OBJ_SPEC = TEMPLATE_DIR / "tamplate objectifs spécifiques.png"
TPL_METHODOLOGY = TEMPLATE_DIR / "tamplate diapositive  méthodologie.png"
TPL_CHRONOGRAM = TEMPLATE_DIR / "Diapositive..Chronogramme des activités 2026–2027 (1).png"
TPL_BUDGET = TEMPLATE_DIR / "Tamplate diapositive budget.png"
TPL_THANKS = TEMPLATE_DIR / "Merci pour votre attention !.png"
TPL_LOGO = TEMPLATE_DIR / "fmsb_uy1_logo.png"


def _to_rgb(color_tuple) -> RGBColor:
    return RGBColor(color_tuple[0], color_tuple[1], color_tuple[2])


def _add_borderless_textbox(
    slide,
    left_in: float,
    top_in: float,
    width_in: float,
    height_in: float,
    text: str = "",
    font_size_pt: int = 24,
    bold: bool = False,
    italic: bool = False,
    color_rgb: RGBColor = _to_rgb(COLORS["navy_slate"]),
    align: PP_ALIGN = PP_ALIGN.LEFT,
    word_wrap: bool = True,
):
    """Add a borderless, transparent, strictly editable textbox placed in the foreground."""
    tx_box = slide.shapes.add_textbox(
        Inches(left_in), Inches(top_in), Inches(width_in), Inches(height_in)
    )
    tf = tx_box.text_frame
    tf.word_wrap = word_wrap
    tf.margin_left = Inches(0.05)
    tf.margin_right = Inches(0.05)
    tf.margin_top = Inches(0.05)
    tf.margin_bottom = Inches(0.05)

    p = tf.paragraphs[0]
    p.text = text
    p.font.name = "Arial"
    p.font.size = Pt(font_size_pt)
    p.font.bold = bold
    p.font.italic = italic
    p.font.color.rgb = color_rgb
    p.alignment = align
    return tx_box, tf


def _add_full_background(slide, image_path: Path):
    """Add background image stretched cleanly across 16:9 widescreen dimensions."""
    if image_path.exists():
        try:
            slide.shapes.add_picture(
                str(image_path.resolve()),
                Inches(0),
                Inches(0),
                width=Inches(SLIDE_WIDTH_IN),
                height=Inches(SLIDE_HEIGHT_IN),
            )
            return True
        except Exception as e:
            logger.warning(f"Could not add background {image_path}: {e}")
    return False


# =========================================================================
# 1. WELCOME SLIDE (Bienvenue)
# =========================================================================
def _render_welcome_slide(prs, pptx_slide, slide: Slide):
    _add_full_background(pptx_slide, TPL_COVER_BG)

    # Rounded banner capsule for welcome
    banner = pptx_slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(1.5),
        Inches(2.6),
        Inches(10.333),
        Inches(2.2),
    )
    banner.fill.solid()
    banner.fill.fore_color.rgb = RGBColor(255, 255, 255)
    banner.line.color.rgb = RGBColor(0, 75, 135)
    banner.line.width = Pt(2.5)

    tf = banner.text_frame
    tf.word_wrap = True
    p0 = tf.paragraphs[0]
    p0.text = "BIENVENUE À LA SOUTENANCE DU PROTOCOLE DE RECHERCHE"
    p0.font.name = "Arial"
    p0.font.size = Pt(22)
    p0.font.bold = True
    p0.font.color.rgb = RGBColor(0, 75, 135)
    p0.alignment = PP_ALIGN.CENTER

    cand_txt = slide.subtitle or "DE L'IMPÉTRANT(E) : CANDIDAT EN MÉDECINE"
    p1 = tf.add_paragraph()
    p1.text = cand_txt
    p1.font.name = "Arial"
    p1.font.size = Pt(26)
    p1.font.bold = True
    p1.font.color.rgb = RGBColor(108, 40, 38)
    p1.alignment = PP_ALIGN.CENTER


# =========================================================================
# 2. COVER SLIDE (Strict FMSB / CAMES Official Page de Garde)
# =========================================================================
def _render_cover_slide(prs, pptx_slide, slide: Slide):
    # Medical background on bottom section
    if TPL_COVER_BG.exists():
        try:
            pptx_slide.shapes.add_picture(
                str(TPL_COVER_BG.resolve()),
                Inches(0),
                Inches(1.8),
                width=Inches(SLIDE_WIDTH_IN),
                height=Inches(5.7),
            )
        except Exception:
            pass

    # Header Left (Cameroon FR)
    _add_borderless_textbox(
        pptx_slide,
        left_in=0.6,
        top_in=0.15,
        width_in=4.6,
        height_in=1.6,
        text="RÉPUBLIQUE DU CAMEROUN\nPaix - Travail - Patrie\n----------\nUNIVERSITÉ DE YAOUNDÉ I\n----------\nFACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES",
        font_size_pt=10,
        bold=True,
        color_rgb=RGBColor(15, 23, 42),
        align=PP_ALIGN.CENTER,
    )

    # University Emblem in center
    if TPL_LOGO.exists():
        try:
            pptx_slide.shapes.add_picture(
                str(TPL_LOGO.resolve()),
                Inches(5.9),
                Inches(0.1),
                width=Inches(1.5),
                height=Inches(1.7),
            )
        except Exception:
            pass

    # Header Right (Cameroon EN)
    _add_borderless_textbox(
        pptx_slide,
        left_in=8.1,
        top_in=0.15,
        width_in=4.6,
        height_in=1.6,
        text="REPUBLIC OF CAMEROON\nPeace - Work - Fatherland\n----------\nTHE UNIVERSITY OF YAOUNDÉ I\n----------\nFACULTY OF MEDICINE AND BIOMEDICAL SCIENCES",
        font_size_pt=10,
        bold=True,
        color_rgb=RGBColor(15, 23, 42),
        align=PP_ALIGN.CENTER,
    )

    # Extract elements
    dept_text = "DÉPARTEMENT DE CHIRURGIE ET SPÉCIALITÉS"
    author_text = "Rédigé et présenté par : MBIANDA TCHOUAFA BRICE KEVIN    |    Matricule : 2026-FMSB-042"
    directors_text = slide.subtitle or "Directeur de thèse : Prof. Angwafo III Fru\nCo-directeur(s) : Dr. Mvogo Jean"
    year_text = "Année académique 2025 - 2026"

    for el in slide.elements:
        if el.element_type == "department":
            dept_text = el.content
        elif el.element_type == "author":
            author_text = el.content
        elif el.element_type == "directors":
            directors_text = el.content
        elif el.element_type == "year":
            year_text = el.content

    # Department Banner
    dept_box = pptx_slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(1.2),
        Inches(1.85),
        Inches(10.933),
        Inches(0.45),
    )
    dept_box.fill.solid()
    dept_box.fill.fore_color.rgb = RGBColor(108, 40, 38)
    dept_box.line.fill.background()
    p = dept_box.text_frame.paragraphs[0]
    p.text = dept_text
    p.font.name = "Arial"
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = RGBColor(255, 255, 255)
    p.alignment = PP_ALIGN.CENTER

    # Title Banner Box
    title_box = pptx_slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(1.2),
        Inches(2.45),
        Inches(10.933),
        Inches(1.5),
    )
    title_box.fill.solid()
    title_box.fill.fore_color.rgb = RGBColor(255, 255, 255)
    title_box.line.color.rgb = RGBColor(0, 75, 135)
    title_box.line.width = Pt(2.0)
    p = title_box.text_frame.paragraphs[0]
    p.text = (slide.title or "TITRE DU PROTOCOLE DE RECHERCHE").upper()
    p.font.name = "Arial"
    p.font.size = Pt(18)
    p.font.bold = True
    p.font.color.rgb = RGBColor(0, 75, 135)
    p.alignment = PP_ALIGN.CENTER

    # Degree Mention
    _add_borderless_textbox(
        pptx_slide,
        left_in=1.2,
        top_in=4.1,
        width_in=10.933,
        height_in=0.5,
        text="Protocole de Thèse rédigé en vue de l'obtention du Diplôme de Docteur en Médecine",
        font_size_pt=12,
        bold=True,
        italic=True,
        color_rgb=RGBColor(108, 40, 38),
        align=PP_ALIGN.CENTER,
    )

    # Author & Matricule
    _add_borderless_textbox(
        pptx_slide,
        left_in=1.2,
        top_in=4.65,
        width_in=10.933,
        height_in=0.5,
        text=author_text.replace("\n", "    |    "),
        font_size_pt=13,
        bold=True,
        color_rgb=RGBColor(15, 23, 42),
        align=PP_ALIGN.CENTER,
    )

    # Directors: Director left, Co-director right
    dir_parts = directors_text.split("Co-directeur(s) :")
    dir_left = dir_parts[0].strip()
    dir_right = ("Co-directeur(s) :\n" + dir_parts[1].strip()) if len(dir_parts) > 1 else ""

    _add_borderless_textbox(
        pptx_slide,
        left_in=1.2,
        top_in=5.35,
        width_in=5.0,
        height_in=0.9,
        text=dir_left,
        font_size_pt=12,
        bold=True,
        color_rgb=RGBColor(15, 23, 42),
        align=PP_ALIGN.CENTER,
    )
    if dir_right:
        _add_borderless_textbox(
            pptx_slide,
            left_in=7.133,
            top_in=5.35,
            width_in=5.0,
            height_in=0.9,
            text=dir_right,
            font_size_pt=12,
            bold=True,
            color_rgb=RGBColor(15, 23, 42),
            align=PP_ALIGN.CENTER,
        )

    # Academic Year
    yr_box = pptx_slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(4.66),
        Inches(6.65),
        Inches(4.0),
        Inches(0.45),
    )
    yr_box.fill.solid()
    yr_box.fill.fore_color.rgb = RGBColor(241, 245, 249)
    yr_box.line.color.rgb = RGBColor(203, 213, 225)
    p = yr_box.text_frame.paragraphs[0]
    p.text = year_text
    p.font.name = "Arial"
    p.font.size = Pt(12)
    p.font.bold = True
    p.font.color.rgb = RGBColor(71, 85, 105)
    p.alignment = PP_ALIGN.CENTER


# =========================================================================
# 3. PLAN SLIDE (6 Puces - Template Plan)
# =========================================================================
def _render_plan_slide(prs, pptx_slide, slide: Slide):
    _add_full_background(pptx_slide, TPL_PLAN)

    # Title in left flask
    _add_borderless_textbox(
        pptx_slide,
        left_in=0.8,
        top_in=2.7,
        width_in=4.0,
        height_in=1.8,
        text="PLAN DU DIAPORAMA\nSommaire du Protocole",
        font_size_pt=22,
        bold=True,
        color_rgb=RGBColor(0, 75, 135),
        align=PP_ALIGN.CENTER,
    )

    # 6 Right Pills
    default_plan = [
        "1. INTRODUCTION & CONTEXTE",
        "2. OBJECTIF GÉNÉRAL",
        "3. OBJECTIFS SPÉCIFIQUES",
        "4. MÉTHODOLOGIE DE L'ÉTUDE",
        "5. CHRONOGRAMME DES ACTIVITÉS",
        "6. BUDGET PRÉVISIONNEL",
    ]
    pills = [el.content for el in slide.elements] or default_plan

    for i, txt in enumerate(pills[:6]):
        top_pos = 0.85 + i * 1.0
        _add_borderless_textbox(
            pptx_slide,
            left_in=5.0,
            top_in=top_pos,
            width_in=7.4,
            height_in=0.65,
            text=txt,
            font_size_pt=15,
            bold=True,
            color_rgb=RGBColor(15, 23, 42),
            align=PP_ALIGN.LEFT,
        )


# =========================================================================
# 4 & 5. INTRODUCTION SLIDES (Template Introduction)
# =========================================================================
def _render_intro_slide(prs, pptx_slide, slide: Slide):
    _add_full_background(pptx_slide, TPL_INTRO)

    # Top Capsule Title
    _add_borderless_textbox(
        pptx_slide,
        left_in=2.2,
        top_in=0.35,
        width_in=9.0,
        height_in=0.85,
        text=slide.title,
        font_size_pt=22,
        bold=True,
        color_rgb=RGBColor(0, 75, 135),
    )

    # Bullets in white body
    bullets_text = "\n\n".join(f"• {el.content}" for el in slide.elements)
    _add_borderless_textbox(
        pptx_slide,
        left_in=1.0,
        top_in=2.0,
        width_in=11.33,
        height_in=4.6,
        text=bullets_text,
        font_size_pt=18,
        color_rgb=RGBColor(30, 41, 59),
    )


# =========================================================================
# 6. OBJECTIF GÉNÉRAL (Template Objectif Général)
# =========================================================================
def _render_obj_gen_slide(prs, pptx_slide, slide: Slide):
    _add_full_background(pptx_slide, TPL_OBJ_GEN)

    # Top Capsule Title
    _add_borderless_textbox(
        pptx_slide,
        left_in=2.2,
        top_in=0.35,
        width_in=9.0,
        height_in=0.85,
        text="OBJECTIF GÉNÉRAL",
        font_size_pt=24,
        bold=True,
        color_rgb=RGBColor(0, 75, 135),
    )

    # Central Objective Box
    obj_text = slide.elements[0].content if slide.elements else slide.subtitle
    _add_borderless_textbox(
        pptx_slide,
        left_in=1.2,
        top_in=2.8,
        width_in=11.0,
        height_in=3.5,
        text=obj_text,
        font_size_pt=24,
        bold=True,
        color_rgb=RGBColor(15, 23, 42),
        align=PP_ALIGN.CENTER,
    )


# =========================================================================
# 7 & 8. OBJECTIFS SPÉCIFIQUES (Template Objectifs Spécifiques)
# =========================================================================
def _render_obj_spec_slide(prs, pptx_slide, slide: Slide):
    _add_full_background(pptx_slide, TPL_OBJ_SPEC)

    # Top Capsule Title
    _add_borderless_textbox(
        pptx_slide,
        left_in=2.2,
        top_in=0.35,
        width_in=9.0,
        height_in=0.85,
        text=slide.title,
        font_size_pt=22,
        bold=True,
        color_rgb=RGBColor(0, 75, 135),
    )

    # OS Bullets
    bullets_text = "\n\n".join(f"• {el.content}" for el in slide.elements)
    _add_borderless_textbox(
        pptx_slide,
        left_in=1.0,
        top_in=2.0,
        width_in=11.33,
        height_in=4.6,
        text=bullets_text,
        font_size_pt=18,
        color_rgb=RGBColor(30, 41, 59),
    )


# =========================================================================
# 9 to 14. MÉTHODOLOGIE (Template Méthodologie)
# =========================================================================
def _render_methodology_slide(prs, pptx_slide, slide: Slide):
    _add_full_background(pptx_slide, TPL_METHODOLOGY)

    # Top Capsule Title
    _add_borderless_textbox(
        pptx_slide,
        left_in=2.2,
        top_in=0.35,
        width_in=9.0,
        height_in=0.85,
        text=slide.title,
        font_size_pt=22,
        bold=True,
        color_rgb=RGBColor(0, 75, 135),
    )

    # Methodology content
    bullets_text = "\n\n".join(
        el.content if el.content.startswith(("•", " ", "-")) else f"• {el.content}"
        for el in slide.elements
    )
    _add_borderless_textbox(
        pptx_slide,
        left_in=1.0,
        top_in=2.0,
        width_in=11.33,
        height_in=4.6,
        text=bullets_text,
        font_size_pt=18,
        color_rgb=RGBColor(30, 41, 59),
    )


# =========================================================================
# 15. CHRONOGRAMME (Déposée telle quelle sans texte !)
# =========================================================================
def _render_chronogram_slide(prs, pptx_slide, slide: Slide):
    _add_full_background(pptx_slide, TPL_CHRONOGRAM)
    # Strictly deposited as-is without extra text overlaid!


# =========================================================================
# 16. BUDGET PRÉVISIONNEL (Template Budget Radial)
# =========================================================================
def _render_budget_slide(prs, pptx_slide, slide: Slide):
    _add_full_background(pptx_slide, TPL_BUDGET)

    # Total in central pill
    total_text = slide.subtitle or "1 250 000 FCFA"
    _add_borderless_textbox(
        pptx_slide,
        left_in=5.7,
        top_in=4.4,
        width_in=2.0,
        height_in=0.5,
        text=total_text,
        font_size_pt=16,
        bold=True,
        color_rgb=RGBColor(0, 75, 135),
        align=PP_ALIGN.CENTER,
    )

    # 10 Pills around (5 left, 5 right)
    default_pills = [
        "1. Matériel & consommables : 350 000",
        "2. Transport & logistique : 200 000",
        "3. Clairance éthique : 100 000",
        "4. Analyse statistique : 150 000",
        "5. Reprographie & reliure : 120 000",
        "6. Enquêtes & fiches CRF : 80 000",
        "7. Communication & téléphone : 50 000",
        "8. Restitution des résultats : 70 000",
        "9. Imprévus (10%) : 130 000",
        "10. Financement : Fonds propres",
    ]
    pills = [el.content for el in slide.elements] or default_pills

    # Left 5 pills
    for i, txt in enumerate(pills[:5]):
        top_pos = 1.1 + i * 1.2
        _add_borderless_textbox(
            pptx_slide,
            left_in=1.0,
            top_in=top_pos,
            width_in=4.2,
            height_in=0.65,
            text=txt,
            font_size_pt=11,
            bold=True,
            color_rgb=RGBColor(15, 23, 42),
        )

    # Right 5 pills
    for i, txt in enumerate(pills[5:10]):
        top_pos = 1.1 + i * 1.2
        _add_borderless_textbox(
            pptx_slide,
            left_in=8.2,
            top_in=top_pos,
            width_in=4.2,
            height_in=0.65,
            text=txt,
            font_size_pt=11,
            bold=True,
            color_rgb=RGBColor(15, 23, 42),
        )


# =========================================================================
# 17. REMERCIEMENTS & CLÔTURE (Template Merci)
# =========================================================================
def _render_acknowledgements_slide(prs, pptx_slide, slide: Slide):
    _add_full_background(pptx_slide, TPL_THANKS)

    _add_borderless_textbox(
        pptx_slide,
        left_in=8.1,
        top_in=2.8,
        width_in=4.2,
        height_in=2.2,
        text="MERCI POUR VOTRE AIMABLE ATTENTION\n\n« Sapientia Collativa Cognitio »\nFaculté de Médecine et des Sciences Biomédicales\nUniversité de Yaoundé I",
        font_size_pt=14,
        bold=True,
        color_rgb=RGBColor(0, 75, 135),
        align=PP_ALIGN.CENTER,
    )


# =========================================================================
# MAIN PPTX COMPILER
# =========================================================================
def compile_presentation_pptx(deck: PresentationDeck, output_path: Path) -> Path:
    """Generate a high-grade native PowerPoint (.pptx) deck mapped directly to user templates."""
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_WIDTH_IN)
    prs.slide_height = Inches(SLIDE_HEIGHT_IN)
    blank_layout = prs.slide_layouts[6]

    for slide_data in deck.slides:
        pptx_slide = prs.slides.add_slide(blank_layout)

        # Dispatch by slide type
        stype = slide_data.slide_type
        if stype == "welcome":
            _render_welcome_slide(prs, pptx_slide, slide_data)
        elif stype == "cover":
            _render_cover_slide(prs, pptx_slide, slide_data)
        elif stype == "plan":
            _render_plan_slide(prs, pptx_slide, slide_data)
        elif stype == "introduction":
            _render_intro_slide(prs, pptx_slide, slide_data)
        elif stype == "objective_general":
            _render_obj_gen_slide(prs, pptx_slide, slide_data)
        elif stype == "objective_specific":
            _render_obj_spec_slide(prs, pptx_slide, slide_data)
        elif stype == "methodology":
            _render_methodology_slide(prs, pptx_slide, slide_data)
        elif stype == "chronogram":
            _render_chronogram_slide(prs, pptx_slide, slide_data)
        elif stype == "budget":
            _render_budget_slide(prs, pptx_slide, slide_data)
        elif stype == "acknowledgements":
            _render_acknowledgements_slide(prs, pptx_slide, slide_data)
        else:
            # Fallback to methodology template or intro
            _render_methodology_slide(prs, pptx_slide, slide_data)

        # Inject Speaker Notes
        if slide_data.presenter_notes:
            try:
                notes_slide = pptx_slide.notes_slide
                text_frame = notes_slide.notes_text_frame
                text_frame.text = slide_data.presenter_notes
            except Exception:
                pass

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path.resolve()))
    logger.info(f"Successfully compiled PPTX ({len(deck.slides)} slides) to {output_path}")
    return output_path
