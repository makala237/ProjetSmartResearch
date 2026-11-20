"""Physical 16:9 Slide Geometry & Typography Engine.

Single source of truth for both the React studio canvas (x_pct/y_pct)
and the native python-pptx compiler (Inches), calibrated pixel-for-pixel
against the 9 graphic PNG templates in tamplates/ and CAMES readability rules.
"""

import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image as PILImage

from app.data.pptx_canvas import (
    CAMES_MAX_LINES_PER_SLIDE,
    CAMES_MIN_BODY_PT,
    CAMES_MIN_TITLE_PT,
    PLAN_6_AXES,
    TPL_BUDGET,
    TPL_CHRONOGRAM,
    TPL_COVER_BG,
    TPL_INTRO,
    TPL_LOGO,
    TPL_METHODOLOGY,
    TPL_OBJ_GEN,
    TPL_OBJ_SPEC,
    TPL_PLAN,
    TPL_THANKS,
)
from app.schemas.presentation import LayoutBox, PresentationDeck, Slide, SlideElement
from app.schemas.protocol import ProtocolProject, SupervisorInfo
from app.services.cover_builder import (
    _resolve_cover_logo_path,
    _strip_dept_prefix,
    format_contact_tier_lines,
)

SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5

# Normalized (x_pct, y_pct, w_pct, h_pct) calibrated on tamplate diapositive Plan.png
PLAN_PILL_RECTS: List[Tuple[float, float, float, float]] = [
    (0.302, 0.123, 0.535, 0.068),
    (0.334, 0.262, 0.555, 0.068),
    (0.364, 0.394, 0.555, 0.068),
    (0.364, 0.530, 0.555, 0.068),
    (0.336, 0.660, 0.555, 0.068),
    (0.302, 0.787, 0.535, 0.068),
]

# Normalized (x_pct, y_pct, w_pct, h_pct) calibrated on Tamplate diapositive budget.png
BUDGET_LEFT_PILL_RECTS: List[Tuple[float, float, float, float]] = [
    (0.155, 0.181, 0.241, 0.080),
    (0.097, 0.341, 0.225, 0.079),
    (0.085, 0.508, 0.216, 0.080),
    (0.091, 0.678, 0.234, 0.079),
    (0.155, 0.838, 0.241, 0.079),
]
BUDGET_RIGHT_PILL_RECTS: List[Tuple[float, float, float, float]] = [
    (0.602, 0.181, 0.241, 0.080),
    (0.679, 0.341, 0.226, 0.079),
    (0.697, 0.508, 0.218, 0.080),
    (0.673, 0.678, 0.235, 0.079),
    (0.602, 0.838, 0.242, 0.079),
]
BUDGET_TOTAL_RECT: Tuple[float, float, float, float] = (0.436, 0.535, 0.126, 0.063)

# Coordinates relative to Tamplate diapositive chronogramme des activités .png (1345 x 1169)
CHRONOGRAM_IMAGE_SIZE: Tuple[int, int] = (1345, 1169)

# 7 white pill boxes on the left (top Pill 1 = green/latest -> bottom Pill 7 = yellow/earliest)
CHRONOGRAM_PILL_RECTS: List[Tuple[float, float, float, float]] = [
    (0.078, 0.234, 0.351, 0.065),  # Pill 1 (top, green dot)
    (0.071, 0.341, 0.338, 0.064),  # Pill 2 (cyan dot)
    (0.065, 0.445, 0.317, 0.064),  # Pill 3 (blue dot)
    (0.059, 0.549, 0.310, 0.065),  # Pill 4 (purple dot)
    (0.060, 0.655, 0.318, 0.065),  # Pill 5 (pink dot)
    (0.065, 0.761, 0.333, 0.064),  # Pill 6 (orange dot)
    (0.115, 0.865, 0.344, 0.061),  # Pill 7 (bottom, yellow dot)
]

# 7 colored 3D staircase steps on the right (top Step 1 = green -> bottom Step 7 = yellow)
CHRONOGRAM_STEP_RECTS: List[Tuple[float, float, float, float]] = [
    (0.570, 0.326, 0.175, 0.048),  # Step 1 (top green)
    (0.515, 0.388, 0.170, 0.048),  # Step 2 (cyan)
    (0.465, 0.462, 0.175, 0.048),  # Step 3 (blue)
    (0.455, 0.546, 0.175, 0.048),  # Step 4 (purple)
    (0.458, 0.630, 0.180, 0.048),  # Step 5 (pink)
    (0.505, 0.710, 0.185, 0.048),  # Step 6 (orange)
    (0.558, 0.785, 0.185, 0.048),  # Step 7 (bottom yellow)
]

# Safe zones for the 4 icon-header content templates
TITLE_CAPSULE_RECT: Tuple[float, float, float, float] = (0.300, 0.038, 0.495, 0.170)
BODY_STANDARD_RECT: Tuple[float, float, float, float] = (0.065, 0.255, 0.820, 0.625)
BODY_SPLIT_LEFT_RECT: Tuple[float, float, float, float] = (0.055, 0.255, 0.495, 0.625)
BODY_SPLIT_MEDIA_RECT: Tuple[float, float, float, float] = (0.570, 0.255, 0.355, 0.530)
BODY_SPLIT_CAPTION_RECT: Tuple[float, float, float, float] = (0.570, 0.795, 0.355, 0.080)
PROCEDURE_AREA_RECT: Tuple[float, float, float, float] = (0.055, 0.295, 0.855, 0.480)
STAT_GRID_RECT: Tuple[float, float, float, float] = (0.065, 0.265, 0.820, 0.610)
OBJ_GEN_FOCAL_RECT: Tuple[float, float, float, float] = (0.085, 0.295, 0.830, 0.500)


def estimate_text_lines(text: str, width_in: float, font_pt: float) -> int:
    """Estimate wrapped line count for Arial/Calibri text inside width_in at font_pt."""
    clean = (text or "").strip()
    if not clean:
        return 1
    avg_char_pt = max(4.0, float(font_pt) * 0.52)
    chars_per_line = max(12, int((width_in * 72.0 - 8.0) / avg_char_pt))
    total = 0
    for para in clean.splitlines() or [clean]:
        p_str = para.strip()
        if not p_str:
            total += 1
        else:
            total += max(1, math.ceil(len(p_str) / chars_per_line))
    return max(1, total)


def compute_fit_contain_rect(
    img_w_px: int,
    img_h_px: int,
    box_left_in: float = 0.0,
    box_top_in: float = 0.0,
    box_w_in: float = SLIDE_WIDTH_IN,
    box_h_in: float = SLIDE_HEIGHT_IN,
) -> Tuple[float, float, float, float]:
    """Center an image proportionally (fit-contain) inside box without aspect distortion."""
    if img_w_px <= 0 or img_h_px <= 0 or box_w_in <= 0 or box_h_in <= 0:
        return (box_left_in, box_top_in, box_w_in, box_h_in)
    img_ratio = img_w_px / img_h_px
    box_ratio = box_w_in / box_h_in
    if img_ratio >= box_ratio:
        draw_w = box_w_in
        draw_h = draw_w / img_ratio
    else:
        draw_h = box_h_in
        draw_w = draw_h * img_ratio
    draw_left = box_left_in + (box_w_in - draw_w) / 2.0
    draw_top = box_top_in + (box_h_in - draw_h) / 2.0
    return (round(draw_left, 4), round(draw_top, 4), round(draw_w, 4), round(draw_h, 4))


def _fit_title_font_pt(title: str, width_in: float, height_in: float, max_pt: float = 27.0, min_pt: float = 21.0) -> float:
    """Pick the largest legible title size that fits inside 1-2 lines of the header capsule."""
    pt = max_pt
    while pt > min_pt:
        lines = estimate_text_lines(title, width_in, pt)
        line_h_in = (pt * 1.22) / 72.0
        if lines <= 2 and (lines * line_h_in) <= (height_in * 0.90):
            return round(pt, 1)
        pt -= 1.0
    return min_pt


def split_elements_to_fit_slides(
    elements: List[SlideElement],
    max_lines: int = CAMES_MAX_LINES_PER_SLIDE,
    font_pt: float = float(CAMES_MIN_BODY_PT),
    has_media: bool = False,
) -> List[List[SlideElement]]:
    """Pack text elements across as many slides as needed so each stays <= max_lines & physical height."""
    media_els = [el for el in elements if el.element_type == "media"]
    text_els = [el for el in elements if el.element_type != "media"]

    if not text_els:
        return [elements] if elements else [[]]

    body_w_pct = BODY_SPLIT_LEFT_RECT[2] if (has_media or media_els) else BODY_STANDARD_RECT[2]
    body_h_pct = BODY_SPLIT_LEFT_RECT[3] if (has_media or media_els) else BODY_STANDARD_RECT[3]
    avail_w_in = body_w_pct * SLIDE_WIDTH_IN - 0.35
    avail_h_in = body_h_pct * SLIDE_HEIGHT_IN

    pages: List[List[SlideElement]] = []
    current_group: List[SlideElement] = []
    current_lines = 0
    current_h_in = 0.0

    for el in text_els:
        el_lines = estimate_text_lines(f"• {el.content}", avail_w_in, font_pt)
        el_h_in = max(0.55, el_lines * (font_pt * 1.32 / 72.0) + 0.24)
        would_exceed_lines = (current_lines + el_lines) > max_lines
        would_exceed_height = (current_h_in + el_h_in + 0.14) > avail_h_in

        if current_group and (would_exceed_lines or would_exceed_height):
            pages.append(current_group)
            current_group = []
            current_lines = 0
            current_h_in = 0.0

        current_group.append(el)
        current_lines += el_lines
        current_h_in += el_h_in + 0.14

    if current_group:
        pages.append(current_group)

    if media_els and pages:
        # Attach media figure cleanly to the first slide of the group
        pages[0] = [*pages[0], *media_els]

    return pages


def _format_supervisors_lines(supervisors: List[SupervisorInfo]) -> str:
    """Format supervisor blocks consistently with cover_builder.py (Name, Rank, Specialty)."""
    blocks: List[str] = []
    for sup in supervisors:
        lines = sup.to_display_lines()
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "À renseigner"


def _compute_welcome_boxes(slide: Slide, project: Optional[ProtocolProject]) -> List[LayoutBox]:
    cand = (project.cover.author_name if project else "") or ""
    subtitle_txt = slide.subtitle or (
        f"DE L'IMPÉTRANT(E) : {cand.upper()}" if cand else "DE L'IMPÉTRANT(E)"
    )
    headline = (slide.title or "BIENVENUE À LA SOUTENANCE DU PROTOCOLE DE RECHERCHE").strip()
    return [
        LayoutBox(
            box_id="welcome-card",
            role="welcome_card",
            text=headline,
            sub_text=subtitle_txt,
            x_pct=0.10,
            y_pct=0.30,
            w_pct=0.80,
            h_pct=0.38,
            font_size_pt=24.0,
            bold=True,
            color_hex="#004B87",
            align="center",
            valign="middle",
            bg_hex="#FFFFFF",
            border_hex="#004B87",
        )
    ]


def _compute_cover_boxes(slide: Slide, project: Optional[ProtocolProject]) -> List[LayoutBox]:
    """Compute strict bilingual multi-university cover boxes matching cover_builder.py rules."""
    cover = project.cover if project else None

    raw_dept_fr = (
        cover.department_fr
        if cover and cover.department_fr
        else next((el.content for el in slide.elements if el.element_type == "department"), "SANTE PUBLIQUE")
    )
    raw_dept_en = cover.department_en if cover and cover.department_en else raw_dept_fr
    dept_fr = _strip_dept_prefix(raw_dept_fr, is_french=True)
    dept_en = _strip_dept_prefix(raw_dept_en, is_french=False)

    uni_fr = (getattr(cover, "university_fr", "") or "UNIVERSITÉ DE YAOUNDÉ I").strip().upper()
    uni_en = (getattr(cover, "university_en", "") or "THE UNIVERSITY OF YAOUNDÉ I").strip().upper()
    fac_fr = (getattr(cover, "faculty_fr", "") or "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES").strip().upper()
    fac_en = (getattr(cover, "faculty_en", "") or "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES").strip().upper()

    uni_fr_contacts, uni_en_contacts = (
        format_contact_tier_lines(
            getattr(cover, "university_po_box", ""),
            getattr(cover, "university_email", ""),
        )
        if cover
        else ([], [])
    )
    fac_fr_contacts, fac_en_contacts = (
        format_contact_tier_lines(
            getattr(cover, "faculty_po_box", ""),
            getattr(cover, "faculty_email", ""),
        )
        if cover
        else ([], [])
    )

    fr_parts = [
        "RÉPUBLIQUE DU CAMEROUN",
        "Paix – Travail – Patrie",
        "************",
        "MINISTÈRE DE L'ENSEIGNEMENT SUPÉRIEUR",
        "************",
        uni_fr,
        *uni_fr_contacts,
        "************",
        fac_fr,
        "************",
        f"DÉPARTEMENT DE {dept_fr}",
    ]
    if fac_fr_contacts:
        fr_parts.extend(["************", *fac_fr_contacts])

    en_parts = [
        "REPUBLIC OF CAMEROON",
        "Peace – Work – Fatherland",
        "************",
        "MINISTRY OF HIGHER EDUCATION",
        "************",
        uni_en,
        *uni_en_contacts,
        "************",
        fac_en,
        "************",
        f"DEPARTMENT OF {dept_en}",
    ]
    if fac_en_contacts:
        en_parts.extend(["************", *fac_en_contacts])

    header_fr_text = "\n".join(fr_parts)
    header_en_text = "\n".join(en_parts)

    has_extra_tiers = bool(uni_fr_contacts or fac_fr_contacts)
    header_strip_h = 0.285 if has_extra_tiers else 0.255
    header_box_h = round(header_strip_h - 0.020, 4)
    header_font_pt = 7.6 if has_extra_tiers else 9.0

    title_text = (
        (cover.title if cover and cover.title else slide.title)
        or "TITRE DU PROTOCOLE DE RECHERCHE"
    ).strip()

    # Dynamic title box sizing (16-21 pt bold)
    title_w_in = 0.86 * SLIDE_WIDTH_IN - 0.30
    title_pt = 21.0
    while title_pt > 16.0:
        t_lines = estimate_text_lines(title_text, title_w_in, title_pt)
        if t_lines <= 3:
            break
        title_pt -= 1.5
    t_lines = estimate_text_lines(title_text, title_w_in, title_pt)
    max_title_h = 0.215 if has_extra_tiers else 0.235
    dyn_title_h = max(0.145, min(max_title_h, (t_lines * (title_pt * 1.25 / 72.0) + 0.36) / SLIDE_HEIGHT_IN))
    title_top = header_strip_h + 0.015
    title_bottom = title_top + dyn_title_h

    degree_label = (cover.degree_label if cover and cover.degree_label else "Doctorat en Médecine Générale").strip()
    author_name = (cover.author_name if cover and cover.author_name else "À renseigner").strip()
    matricule = (cover.matricule if cover and cover.matricule else "—").strip()

    cand_top = title_bottom + 0.014
    cand_h = 0.128 if has_extra_tiers else 0.132
    cand_text = (
        f"Protocole de thèse de {degree_label} rédigé par :\n"
        f"{author_name}\n"
        f"Matricule : {matricule}"
    )

    sup_top = cand_top + cand_h + 0.010
    sup_h = max(0.145, 0.900 - sup_top)

    if cover:
        dir_sups = [cover.director_info] if cover.director_info.has_content() else []
        codir_sups = [c for c in cover.co_directors_info if c.has_content()]
        dir_body = _format_supervisors_lines(dir_sups)
        codir_body = _format_supervisors_lines(codir_sups)
        codir_font = 10.5 if len(codir_sups) >= 2 else 12.5
        dir_font = 12.5
    else:
        raw_dir = next((el.content for el in slide.elements if el.element_type == "directors"), slide.subtitle or "")
        parts = raw_dir.split("Co-directeur")
        dir_body = parts[0].replace("Directeur :", "").replace("Directeur de thèse :", "").strip() or "À renseigner"
        codir_body = (
            parts[1].lstrip("(s) :").strip() if len(parts) > 1 else "À renseigner"
        )
        codir_font = 11.5
        dir_font = 12.0

    acad_year = (cover.academic_year if cover and cover.academic_year else "2025-2026").strip()
    year_text = f"Année académique {acad_year}"

    custom_uni_logo = _resolve_cover_logo_path(getattr(cover, "university_logo_path", "")) if cover else None
    custom_fac_logo = _resolve_cover_logo_path(getattr(cover, "faculty_logo_path", "")) if cover else None
    uni_logo_path = str(custom_uni_logo) if custom_uni_logo else TPL_LOGO

    logo_boxes: List[LayoutBox] = []
    if custom_fac_logo is not None:
        slot_h = round((header_strip_h - 0.032) / 2.0, 4)
        logo_boxes.append(
            LayoutBox(
                box_id="cover-logo",
                role="cover_logo",
                image_path=uni_logo_path,
                x_pct=0.448,
                y_pct=0.012,
                w_pct=0.104,
                h_pct=slot_h,
                align="center",
                valign="middle",
            )
        )
        logo_boxes.append(
            LayoutBox(
                box_id="cover-faculty-logo",
                role="cover_faculty_logo",
                image_path=str(custom_fac_logo),
                x_pct=0.448,
                y_pct=round(0.012 + slot_h + 0.008, 4),
                w_pct=0.104,
                h_pct=slot_h,
                align="center",
                valign="middle",
            )
        )
    else:
        logo_boxes.append(
            LayoutBox(
                box_id="cover-logo",
                role="cover_logo",
                image_path=uni_logo_path,
                x_pct=0.448,
                y_pct=0.015,
                w_pct=0.104,
                h_pct=round(header_strip_h - 0.027, 4),
                align="center",
                valign="middle",
            )
        )

    return [
        LayoutBox(
            box_id="cover-header-fr",
            role="cover_header_fr",
            text=header_fr_text,
            x_pct=0.03,
            y_pct=0.012,
            w_pct=0.38,
            h_pct=header_box_h,
            font_size_pt=header_font_pt,
            bold=True,
            color_hex="#0F172A",
            align="center",
            valign="top",
        ),
        *logo_boxes,
        LayoutBox(
            box_id="cover-header-en",
            role="cover_header_en",
            text=header_en_text,
            x_pct=0.59,
            y_pct=0.012,
            w_pct=0.38,
            h_pct=header_box_h,
            font_size_pt=header_font_pt,
            bold=True,
            color_hex="#0F172A",
            align="center",
            valign="top",
        ),
        LayoutBox(
            box_id="cover-title-box",
            role="cover_title",
            text=title_text,
            x_pct=0.07,
            y_pct=round(title_top, 4),
            w_pct=0.86,
            h_pct=round(dyn_title_h, 4),
            font_size_pt=round(title_pt, 1),
            bold=True,
            color_hex="#004B87",
            align="center",
            valign="middle",
            bg_hex="#FFFFFF",
            border_hex="#6C2826",
        ),
        LayoutBox(
            box_id="cover-candidate",
            role="cover_candidate",
            text=cand_text,
            x_pct=0.10,
            y_pct=round(cand_top, 4),
            w_pct=0.80,
            h_pct=round(cand_h, 4),
            font_size_pt=13.0,
            bold=True,
            color_hex="#0F172A",
            align="center",
            valign="middle",
        ),
        LayoutBox(
            box_id="cover-director",
            role="cover_director",
            text="Directeur",
            sub_text=dir_body,
            x_pct=0.05,
            y_pct=round(sup_top, 4),
            w_pct=0.43,
            h_pct=round(sup_h, 4),
            font_size_pt=dir_font,
            bold=False,
            color_hex="#0F172A",
            align="center",
            valign="top",
            bg_hex="#FFFFFF",
            border_hex="#CBD5E1",
        ),
        LayoutBox(
            box_id="cover-codirector",
            role="cover_codirector",
            text="Co-directeur(s)",
            sub_text=codir_body,
            x_pct=0.52,
            y_pct=round(sup_top, 4),
            w_pct=0.43,
            h_pct=round(sup_h, 4),
            font_size_pt=codir_font,
            bold=False,
            color_hex="#0F172A",
            align="center",
            valign="top",
            bg_hex="#FFFFFF",
            border_hex="#CBD5E1",
        ),
        LayoutBox(
            box_id="cover-year",
            role="cover_year",
            text=year_text,
            x_pct=0.34,
            y_pct=0.915,
            w_pct=0.32,
            h_pct=0.062,
            font_size_pt=12.0,
            bold=True,
            color_hex="#0F172A",
            align="center",
            valign="middle",
            bg_hex="#FFFFFF",
            border_hex="#6C2826",
        ),
    ]


def _compute_plan_boxes(slide: Slide) -> List[LayoutBox]:
    boxes: List[LayoutBox] = [
        LayoutBox(
            box_id="plan-flask",
            role="plan_flask_title",
            text="PLAN\ndu protocole",
            x_pct=0.032,
            y_pct=0.405,
            w_pct=0.130,
            h_pct=0.165,
            font_size_pt=16.5,
            bold=True,
            color_hex="#004B87",
            align="center",
            valign="middle",
        )
    ]
    elements = [el for el in slide.elements if el.element_type != "media"]
    for idx, rect in enumerate(PLAN_PILL_RECTS):
        el = elements[idx] if idx < len(elements) else None
        text_val = el.content if el else PLAN_6_AXES[idx]
        boxes.append(
            LayoutBox(
                box_id=f"plan-pill-{idx+1}",
                role="plan_pill",
                element_id=el.id if el else None,
                text=text_val,
                x_pct=rect[0],
                y_pct=rect[1],
                w_pct=rect[2],
                h_pct=rect[3],
                font_size_pt=16.0,
                bold=True,
                color_hex="#0F172A",
                align="left",
                valign="middle",
                step_index=idx + 1,
            )
        )
    return boxes


_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE_SIZE_CACHE: Dict[str, Tuple[int, int]] = {}


def _get_template_dimensions(rel_path: Optional[str]) -> Tuple[int, int]:
    """Return actual (w, h) px dimensions for a template image, cached."""
    key = rel_path or TPL_CHRONOGRAM
    if key in _TEMPLATE_SIZE_CACHE:
        return _TEMPLATE_SIZE_CACHE[key]
    try:
        p = Path(key)
        if not p.is_absolute():
            p = _WORKSPACE_ROOT / p
        if p.exists():
            with PILImage.open(p) as im:
                _TEMPLATE_SIZE_CACHE[key] = im.size
                return im.size
    except Exception:
        pass
    return CHRONOGRAM_IMAGE_SIZE


def _extract_chronogram_step_label(pill_text: str) -> str:
    """Extract concise period/date label (before ':') to display on the colored staircase step."""
    clean = (pill_text or "").strip()
    if not clean:
        return ""
    if ":" in clean:
        prefix = clean.split(":", 1)[0].strip()
        return prefix[:22]
    words = clean.split()
    return " ".join(words[:2])[:22] if len(words) > 2 else clean[:22]


def _compute_chronogram_boxes(slide: Slide) -> List[LayoutBox]:
    """Compute fit-contain projected boxes for the 7 left pills and 7 colored staircase steps."""
    img_w, img_h = _get_template_dimensions(slide.background_image_path or TPL_CHRONOGRAM)
    dl, dt, dw, dh = compute_fit_contain_rect(
        img_w, img_h, 0.0, 0.0, SLIDE_WIDTH_IN, SLIDE_HEIGHT_IN
    )
    off_x = dl / SLIDE_WIDTH_IN
    off_y = dt / SLIDE_HEIGHT_IN
    sc_w = dw / SLIDE_WIDTH_IN
    sc_h = dh / SLIDE_HEIGHT_IN

    boxes: List[LayoutBox] = []
    pills = [el for el in slide.elements if el.element_type != "media"]
    for idx in range(min(7, len(pills))):
        el = pills[idx]
        txt = (el.content or "").strip()
        px_x, px_y, px_w, px_h = CHRONOGRAM_PILL_RECTS[idx]
        f_pt = 9.5 if len(txt) <= 44 else 8.5
        boxes.append(
            LayoutBox(
                box_id=f"chrono-pill-{idx + 1}",
                role="chronogram_pill",
                element_id=el.id,
                text=txt,
                x_pct=round(off_x + px_x * sc_w, 4),
                y_pct=round(off_y + px_y * sc_h, 4),
                w_pct=round(px_w * sc_w, 4),
                h_pct=round(px_h * sc_h, 4),
                font_size_pt=f_pt,
                bold=True,
                color_hex="#002060",
                align="center",
                valign="middle",
                step_index=idx + 1,
            )
        )

        sx, sy, sw, sh = CHRONOGRAM_STEP_RECTS[idx]
        step_label = _extract_chronogram_step_label(txt)
        boxes.append(
            LayoutBox(
                box_id=f"chrono-step-{idx + 1}",
                role="chronogram_step",
                element_id=el.id,
                text=step_label,
                x_pct=round(off_x + sx * sc_w, 4),
                y_pct=round(off_y + sy * sc_h, 4),
                w_pct=round(sw * sc_w, 4),
                h_pct=round(sh * sc_h, 4),
                font_size_pt=10.0 if len(step_label) <= 14 else 8.5,
                bold=True,
                color_hex="#FFFFFF",
                align="center",
                valign="middle",
                step_index=idx + 1,
            )
        )
    return boxes


def _compute_budget_boxes(slide: Slide) -> List[LayoutBox]:
    total_text = (slide.subtitle or "0 FCFA").strip()
    total_font = 14.5 if len(total_text) <= 16 else 12.5
    boxes: List[LayoutBox] = [
        LayoutBox(
            box_id="budget-total",
            role="budget_total",
            text=total_text,
            x_pct=BUDGET_TOTAL_RECT[0],
            y_pct=BUDGET_TOTAL_RECT[1],
            w_pct=BUDGET_TOTAL_RECT[2],
            h_pct=BUDGET_TOTAL_RECT[3],
            font_size_pt=total_font,
            bold=True,
            color_hex="#004B87",
            align="center",
            valign="middle",
        )
    ]

    pills = [el for el in slide.elements if el.element_type != "media"]
    all_rects = [*BUDGET_LEFT_PILL_RECTS, *BUDGET_RIGHT_PILL_RECTS]
    for idx, rect in enumerate(all_rects):
        el = pills[idx] if idx < len(pills) else None
        txt = el.content if el else ""
        if not txt and not el:
            continue
        f_pt = 11.5 if len(txt) <= 44 else 10.0
        boxes.append(
            LayoutBox(
                box_id=f"budget-pill-{idx+1}",
                role="budget_pill",
                element_id=el.id if el else None,
                text=txt,
                x_pct=rect[0],
                y_pct=rect[1],
                w_pct=rect[2],
                h_pct=rect[3],
                font_size_pt=f_pt,
                bold=True,
                color_hex="#0F172A",
                align="center",
                valign="middle",
                step_index=idx + 1,
            )
        )
    return boxes


def _compute_acknowledgements_boxes(slide: Slide, project: Optional[ProtocolProject]) -> List[LayoutBox]:
    cand = (project.cover.author_name if project else "") or ""
    contact_parts = []
    if project and project.cover.student_email:
        contact_parts.append(project.cover.student_email)
    if project and project.cover.student_phone:
        contact_parts.append(project.cover.student_phone)
    contact_line = " • ".join(contact_parts)

    fac_lbl = (
        (project.cover.faculty_fr if project and getattr(project.cover, "faculty_fr", "") else "")
        or "Faculté de Médecine et des Sciences Biomédicales"
    ).strip()
    uni_lbl = (
        (project.cover.university_fr if project and getattr(project.cover, "university_fr", "") else "")
        or "Université de Yaoundé I"
    ).strip()

    medallion_text = (
        slide.elements[0].content
        if slide.elements and slide.elements[0].content.strip()
        else (
            "QUESTIONS & DISCUSSIONS\n\n"
            f"{cand or 'Impétrant(e) FMSB'}\n"
            f"{fac_lbl}\n"
            f"{uni_lbl}"
            + (f"\n{contact_line}" if contact_line else "")
        )
    )
    en_sub = slide.subtitle or "Thank you for your kind attention!"
    # Keep left subtitle clean if someone stored French institution block in subtitle
    if "Université" in en_sub or "Sapientia" in en_sub:
        en_sub = "Thank you for your kind attention!"

    return [
        LayoutBox(
            box_id="thanks-en",
            role="thanks_en",
            text=en_sub,
            x_pct=0.055,
            y_pct=0.700,
            w_pct=0.455,
            h_pct=0.085,
            font_size_pt=20.0,
            bold=False,
            italic=True,
            color_hex="#004B87",
            align="center",
            valign="middle",
        ),
        LayoutBox(
            box_id="thanks-medallion",
            role="thanks_medallion",
            element_id=slide.elements[0].id if slide.elements else None,
            text=medallion_text,
            x_pct=0.625,
            y_pct=0.285,
            w_pct=0.255,
            h_pct=0.370,
            font_size_pt=14.0,
            bold=True,
            color_hex="#004B87",
            align="center",
            valign="middle",
        ),
    ]


def _compute_content_boxes(
    slide: Slide,
    total_slides: int,
) -> Tuple[List[LayoutBox], int, List[str]]:
    """Compute independent LayoutBoxes for content slides & check CAMES line/height limits."""
    warnings: List[str] = []
    boxes: List[LayoutBox] = []

    # 1. Header Capsule Title
    cap_w_in = TITLE_CAPSULE_RECT[2] * SLIDE_WIDTH_IN
    cap_h_in = TITLE_CAPSULE_RECT[3] * SLIDE_HEIGHT_IN
    title_pt = _fit_title_font_pt(slide.title or "Sans titre", cap_w_in, cap_h_in, max_pt=26.0, min_pt=21.0)
    boxes.append(
        LayoutBox(
            box_id="slide-title",
            role="title",
            text=slide.title or "Sans titre",
            x_pct=TITLE_CAPSULE_RECT[0],
            y_pct=TITLE_CAPSULE_RECT[1],
            w_pct=TITLE_CAPSULE_RECT[2],
            h_pct=TITLE_CAPSULE_RECT[3],
            font_size_pt=title_pt,
            bold=True,
            color_hex="#004B87",
            align="left",
            valign="middle",
        )
    )

    # 2. Footer Breadcrumb + Progress Slide Indexer
    if slide.breadcrumb:
        boxes.append(
            LayoutBox(
                box_id="slide-breadcrumb",
                role="breadcrumb",
                text=slide.breadcrumb,
                x_pct=0.060,
                y_pct=0.915,
                w_pct=0.580,
                h_pct=0.050,
                font_size_pt=11.0,
                bold=True,
                color_hex="#475569",
                align="left",
                valign="middle",
            )
        )
    boxes.append(
        LayoutBox(
            box_id="slide-number",
            role="slide_number",
            text=f"{slide.slide_index:02d} / {max(1, total_slides):02d}",
            x_pct=0.660,
            y_pct=0.915,
            w_pct=0.140,
            h_pct=0.050,
            font_size_pt=13.0,
            bold=True,
            color_hex="#004B87",
            align="right",
            valign="middle",
        )
    )

    media_els = [el for el in slide.elements if el.element_type == "media"]
    text_els = [el for el in slide.elements if el.element_type != "media"]

    mode = slide.layout_mode or "standard"
    if media_els and mode == "standard":
        mode = "split_media"

    # 3A. Objective General focal card
    if slide.slide_type == "objective_general" and mode == "standard":
        obj_el = text_els[0] if text_els else None
        obj_txt = (obj_el.content if obj_el else slide.subtitle) or ""
        w_in = OBJ_GEN_FOCAL_RECT[2] * SLIDE_WIDTH_IN - 0.5
        lines = estimate_text_lines(obj_txt, w_in, 26.0)
        f_pt = 26.0 if lines <= 4 else 24.0
        lines = estimate_text_lines(obj_txt, w_in, f_pt)
        boxes.append(
            LayoutBox(
                box_id="obj-gen-focal",
                role="bullet_card",
                element_id=obj_el.id if obj_el else None,
                text=obj_txt,
                x_pct=OBJ_GEN_FOCAL_RECT[0],
                y_pct=OBJ_GEN_FOCAL_RECT[1],
                w_pct=OBJ_GEN_FOCAL_RECT[2],
                h_pct=OBJ_GEN_FOCAL_RECT[3],
                font_size_pt=f_pt,
                bold=True,
                color_hex="#0F172A",
                align="center",
                valign="middle",
                bg_hex="#F8FAFC",
                border_hex="#004B87",
            )
        )
        return boxes, lines, warnings

    # 3B. Procedure Flow horizontal vector diagram (3-4 steps with arrows)
    if mode == "procedure_flow":
        steps = text_els[:4] if text_els else []
        n = max(1, len(steps))
        ax, ay, aw, ah = PROCEDURE_AREA_RECT
        gap_pct = 0.042 if n > 1 else 0.0
        box_w = (aw - (n - 1) * gap_pct) / n
        for idx, el in enumerate(steps):
            bx = ax + idx * (box_w + gap_pct)
            step_title = el.title or f"Étape {idx + 1}"
            f_pt = 17.5 if len(el.content) <= 95 else 15.5
            boxes.append(
                LayoutBox(
                    box_id=f"proc-step-{idx+1}",
                    role="procedure_step",
                    element_id=el.id,
                    text=step_title,
                    sub_text=el.content,
                    x_pct=round(bx, 4),
                    y_pct=ay,
                    w_pct=round(box_w, 4),
                    h_pct=ah,
                    font_size_pt=f_pt,
                    bold=False,
                    color_hex="#0F172A",
                    align="center",
                    valign="middle",
                    bg_hex="#F8FAFC",
                    border_hex="#004B87",
                    step_index=idx + 1,
                )
            )
        return boxes, n, warnings

    # 3C. Stat Grid (2x2 or 3-column Bento cards)
    if mode == "stat_grid" and text_els:
        cards = text_els[:4]
        n = len(cards)
        gx, gy, gw, gh = STAT_GRID_RECT
        if n <= 3:
            gap = 0.025
            cw = (gw - (n - 1) * gap) / n
            for idx, el in enumerate(cards):
                boxes.append(
                    LayoutBox(
                        box_id=f"stat-card-{idx+1}",
                        role="stat_card",
                        element_id=el.id,
                        text=el.sub_content or el.title or f"Donnée {idx+1}",
                        sub_text=el.content,
                        x_pct=round(gx + idx * (cw + gap), 4),
                        y_pct=gy + 0.06,
                        w_pct=round(cw, 4),
                        h_pct=gh - 0.12,
                        font_size_pt=24.0,
                        bold=True,
                        color_hex="#0F172A",
                        align="center",
                        valign="middle",
                        bg_hex="#F8FAFC",
                        border_hex="#6C2826",
                    )
                )
        else:
            gap_x, gap_y = 0.03, 0.035
            cw = (gw - gap_x) / 2.0
            ch = (gh - gap_y) / 2.0
            for idx, el in enumerate(cards):
                col = idx % 2
                row = idx // 2
                boxes.append(
                    LayoutBox(
                        box_id=f"stat-card-{idx+1}",
                        role="stat_card",
                        element_id=el.id,
                        text=el.sub_content or el.title or f"Indicateur {idx+1}",
                        sub_text=el.content,
                        x_pct=round(gx + col * (cw + gap_x), 4),
                        y_pct=round(gy + row * (ch + gap_y), 4),
                        w_pct=round(cw, 4),
                        h_pct=round(ch, 4),
                        font_size_pt=22.0,
                        bold=True,
                        color_hex="#0F172A",
                        align="center",
                        valign="middle",
                        bg_hex="#F8FAFC",
                        border_hex="#6C2826",
                    )
                )
        return boxes, len(cards), warnings

    # 3D. Standard stacked bullets OR Split Media (left bullets + right figure)
    body_rect = BODY_SPLIT_LEFT_RECT if mode == "split_media" else BODY_STANDARD_RECT
    bx, by, bw, bh = body_rect
    w_in = bw * SLIDE_WIDTH_IN - 0.35

    # Choose >= 24 pt when possible; only gently step down to 22 pt on tight split slides
    base_pt = float(CAMES_MIN_BODY_PT)
    item_lines = [estimate_text_lines(f"• {el.content}", w_in, base_pt) for el in text_els]
    total_lines = sum(item_lines)
    if total_lines > CAMES_MAX_LINES_PER_SLIDE:
        warnings.append(
            f"Densité CAMES : {total_lines} lignes estimées (recommandé <= {CAMES_MAX_LINES_PER_SLIDE} lignes à {int(base_pt)} pt)."
        )
        # Gentle fallback to 22 pt if overflowing physical height
        line_h_pct = (base_pt * 1.32 / 72.0) / SLIDE_HEIGHT_IN
        needed_h = sum(max(0.085, l * line_h_pct + 0.025) for l in item_lines)
        if needed_h > bh:
            base_pt = 22.0
            item_lines = [estimate_text_lines(f"• {el.content}", w_in, base_pt) for el in text_els]
            total_lines = sum(item_lines)

    n = len(text_els)
    if n > 0:
        gap_pct = 0.018
        total_gap = gap_pct * max(0, n - 1)
        avail_bh = max(0.20, bh - total_gap)
        weights = [max(1.0, float(l)) for l in item_lines]
        w_sum = sum(weights)
        curr_y = by
        for idx, (el, wt) in enumerate(zip(text_els, weights)):
            item_h = avail_bh * (wt / w_sum)
            boxes.append(
                LayoutBox(
                    box_id=f"bullet-{idx+1}",
                    role="bullet_card",
                    element_id=el.id,
                    text=el.content,
                    x_pct=bx,
                    y_pct=round(curr_y, 4),
                    w_pct=bw,
                    h_pct=round(item_h, 4),
                    font_size_pt=base_pt,
                    bold=False,
                    color_hex="#1E293B",
                    align="left",
                    valign="middle",
                    bg_hex="#FFFFFF",
                    border_hex="#E2E8F0",
                )
            )
            curr_y += item_h + gap_pct

    if mode == "split_media" and media_els:
        m = media_els[0]
        boxes.append(
            LayoutBox(
                box_id="split-media-img",
                role="media",
                element_id=m.id,
                text=m.title or "Figure scientifique",
                x_pct=BODY_SPLIT_MEDIA_RECT[0],
                y_pct=BODY_SPLIT_MEDIA_RECT[1],
                w_pct=BODY_SPLIT_MEDIA_RECT[2],
                h_pct=BODY_SPLIT_MEDIA_RECT[3],
                image_path=m.image_path,
                align="center",
                valign="middle",
                bg_hex="#FFFFFF",
                border_hex="#CBD5E1",
            )
        )
        if m.title or m.content:
            boxes.append(
                LayoutBox(
                    box_id="split-media-caption",
                    role="media_caption",
                    text=m.title or m.content,
                    x_pct=BODY_SPLIT_CAPTION_RECT[0],
                    y_pct=BODY_SPLIT_CAPTION_RECT[1],
                    w_pct=BODY_SPLIT_CAPTION_RECT[2],
                    h_pct=BODY_SPLIT_CAPTION_RECT[3],
                    font_size_pt=12.0,
                    italic=True,
                    color_hex="#475569",
                    align="center",
                    valign="top",
                )
            )

    return boxes, total_lines, warnings


def compute_slide_layout(
    slide: Slide,
    total_slides: int,
    project: Optional[ProtocolProject] = None,
) -> Slide:
    """Populate slide.render_boxes, estimated_lines and cames_warnings using physical 16:9 metrics."""
    stype = slide.slide_type

    if stype == "welcome":
        slide.background_image_path = slide.background_image_path or TPL_COVER_BG
        slide.layout_mode = "welcome"
        slide.render_boxes = _compute_welcome_boxes(slide, project)
        slide.estimated_lines = 2
        slide.cames_warnings = []
    elif stype == "cover":
        slide.background_image_path = slide.background_image_path or TPL_COVER_BG
        slide.layout_mode = "cover_fmsb"
        slide.render_boxes = _compute_cover_boxes(slide, project)
        slide.estimated_lines = 10
        slide.cames_warnings = []
    elif stype == "plan":
        slide.background_image_path = slide.background_image_path or TPL_PLAN
        slide.layout_mode = "plan_6pills"
        slide.render_boxes = _compute_plan_boxes(slide)
        slide.estimated_lines = 6
        slide.cames_warnings = []
    elif stype == "chronogram":
        slide.background_image_path = slide.background_image_path or TPL_CHRONOGRAM
        slide.locked_as_is = False
        if slide.layout_mode in ("chronogram_7pills", "full_image_asis", "", None):
            slide.layout_mode = "chronogram_7pills"
            slide.render_boxes = _compute_chronogram_boxes(slide)
            slide.estimated_lines = min(7, len(slide.elements))
            slide.cames_warnings = []
        else:
            boxes, est_lines, warns = _compute_content_boxes(slide, total_slides=total_slides)
            slide.render_boxes = boxes
            slide.estimated_lines = est_lines
            slide.cames_warnings = warns
    elif stype == "budget":
        slide.background_image_path = slide.background_image_path or TPL_BUDGET
        slide.layout_mode = "radial_budget"
        slide.render_boxes = _compute_budget_boxes(slide)
        slide.estimated_lines = min(10, len(slide.elements))
        slide.cames_warnings = []
    elif stype == "acknowledgements":
        slide.background_image_path = slide.background_image_path or TPL_THANKS
        slide.layout_mode = "acknowledgements"
        slide.render_boxes = _compute_acknowledgements_boxes(slide, project)
        slide.estimated_lines = 4
        slide.cames_warnings = []
    else:
        if not slide.background_image_path:
            if stype == "introduction":
                slide.background_image_path = TPL_INTRO
            elif stype == "objective_general":
                slide.background_image_path = TPL_OBJ_GEN
            elif stype == "objective_specific":
                slide.background_image_path = TPL_OBJ_SPEC
            else:
                slide.background_image_path = TPL_METHODOLOGY
        boxes, est_lines, warns = _compute_content_boxes(slide, total_slides=total_slides)
        slide.render_boxes = boxes
        slide.estimated_lines = est_lines
        slide.cames_warnings = warns

    return slide


def compute_deck_layout(
    deck: PresentationDeck,
    project: Optional[ProtocolProject] = None,
) -> PresentationDeck:
    """Re-index slides cleanly 1..N and recompute layout geometry for every slide in the deck."""
    total = len(deck.slides)
    for idx, sl in enumerate(deck.slides, start=1):
        sl.slide_index = idx
        compute_slide_layout(sl, total_slides=total, project=project)
    return deck
