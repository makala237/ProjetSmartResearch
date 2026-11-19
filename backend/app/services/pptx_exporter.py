"""High-Precision Native PowerPoint (.pptx) Compiler.

Renders every slide directly from the physical 16:9 RenderPlan (slide.render_boxes)
computed by slide_layout_engine.py, ensuring 1:1 fidelity with the web studio,
borderless transparent editable foreground textboxes per bullet, proportional figures,
native vector procedure flows, and strict FMSB/CAMES cover rules.
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image as PILImage
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from app.data.pptx_canvas import TPL_CHRONOGRAM, TPL_COVER_BG, TPL_LOGO
from app.schemas.presentation import LayoutBox, PresentationDeck, Slide
from app.schemas.protocol import ProtocolProject
from app.services.slide_layout_engine import (
    SLIDE_HEIGHT_IN,
    SLIDE_WIDTH_IN,
    compute_deck_layout,
    compute_fit_contain_rect,
)

logger = logging.getLogger(__name__)
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _hex_to_rgb(hex_color: str, fallback: Tuple[int, int, int] = (30, 41, 59)) -> RGBColor:
    clean = (hex_color or "").strip().lstrip("#")
    if len(clean) == 6:
        try:
            return RGBColor(int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))
        except ValueError:
            pass
    return RGBColor(*fallback)


def _to_align(align_str: str) -> PP_ALIGN:
    if align_str == "center":
        return PP_ALIGN.CENTER
    if align_str == "right":
        return PP_ALIGN.RIGHT
    return PP_ALIGN.LEFT


def _to_anchor(valign_str: str) -> MSO_ANCHOR:
    if valign_str == "top":
        return MSO_ANCHOR.TOP
    if valign_str == "bottom":
        return MSO_ANCHOR.BOTTOM
    return MSO_ANCHOR.MIDDLE


def _resolve_asset_path(rel_or_abs: Optional[str]) -> Optional[Path]:
    if not rel_or_abs:
        return None
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p
    return p if p.exists() else None


def _add_picture_fit_contain(
    pptx_slide,
    img_path: Path,
    left_in: float,
    top_in: float,
    width_in: float,
    height_in: float,
) -> None:
    """Insert picture preserving exact native aspect ratio inside bounding box."""
    try:
        with PILImage.open(img_path) as im:
            iw, ih = im.size
        dl, dt, dw, dh = compute_fit_contain_rect(iw, ih, left_in, top_in, width_in, height_in)
        pptx_slide.shapes.add_picture(
            str(img_path.resolve()),
            Inches(dl),
            Inches(dt),
            width=Inches(dw),
            height=Inches(dh),
        )
    except Exception as exc:
        logger.warning("Failed to insert fit-contain picture %s: %s", img_path, exc)


def _add_borderless_textbox(
    pptx_slide,
    box: LayoutBox,
    text_override: Optional[str] = None,
    font_name: str = "Arial",
):
    """Create a borderless, transparent, 100% editable textbox placed in the foreground."""
    left_in = box.x_pct * SLIDE_WIDTH_IN
    top_in = box.y_pct * SLIDE_HEIGHT_IN
    width_in = max(0.3, box.w_pct * SLIDE_WIDTH_IN)
    height_in = max(0.25, box.h_pct * SLIDE_HEIGHT_IN)

    tx = pptx_slide.shapes.add_textbox(
        Inches(left_in), Inches(top_in), Inches(width_in), Inches(height_in)
    )
    tf = tx.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = _to_anchor(box.valign)
    tf.margin_left = Inches(0.06)
    tf.margin_right = Inches(0.06)
    tf.margin_top = Inches(0.04)
    tf.margin_bottom = Inches(0.04)

    content = box.text if text_override is None else text_override
    lines = (content or "").split("\n")
    color = _hex_to_rgb(box.color_hex)
    align = _to_align(box.align)

    for idx, ln in enumerate(lines):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.text = ln
        p.font.name = font_name
        p.font.size = Pt(box.font_size_pt)
        p.font.bold = box.bold
        p.font.italic = box.italic
        p.font.color.rgb = color
        p.alignment = align
    return tx, tf


def _add_card_backdrop(
    pptx_slide,
    box: LayoutBox,
    corner_shape: MSO_SHAPE = MSO_SHAPE.ROUNDED_RECTANGLE,
    border_width_pt: float = 1.25,
) -> None:
    """Draw a decorative rounded backdrop shape *behind* the editable foreground textbox."""
    if not box.bg_hex and not box.border_hex:
        return
    shape = pptx_slide.shapes.add_shape(
        corner_shape,
        Inches(box.x_pct * SLIDE_WIDTH_IN),
        Inches(box.y_pct * SLIDE_HEIGHT_IN),
        Inches(box.w_pct * SLIDE_WIDTH_IN),
        Inches(box.h_pct * SLIDE_HEIGHT_IN),
    )
    if box.bg_hex:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _hex_to_rgb(box.bg_hex, (255, 255, 255))
    else:
        shape.fill.background()

    if box.border_hex:
        shape.line.color.rgb = _hex_to_rgb(box.border_hex, (203, 213, 225))
        shape.line.width = Pt(border_width_pt)
    else:
        shape.line.fill.background()


def _render_welcome_slide(pptx_slide, slide: Slide) -> None:
    bg_path = _resolve_asset_path(slide.background_image_path or TPL_COVER_BG)
    if bg_path:
        pptx_slide.shapes.add_picture(
            str(bg_path.resolve()), 0, 0, Inches(SLIDE_WIDTH_IN), Inches(SLIDE_HEIGHT_IN)
        )
    card = slide.render_boxes[0] if slide.render_boxes else None
    if not card:
        return
    _add_card_backdrop(pptx_slide, card, border_width_pt=2.5)
    # Editable foreground textbox inside welcome card
    tx = pptx_slide.shapes.add_textbox(
        Inches(card.x_pct * SLIDE_WIDTH_IN + 0.25),
        Inches(card.y_pct * SLIDE_HEIGHT_IN + 0.15),
        Inches(card.w_pct * SLIDE_WIDTH_IN - 0.5),
        Inches(card.h_pct * SLIDE_HEIGHT_IN - 0.3),
    )
    tf = tx.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE

    p0 = tf.paragraphs[0]
    p0.text = card.text
    p0.font.name = "Arial"
    p0.font.size = Pt(22)
    p0.font.bold = True
    p0.font.color.rgb = RGBColor(0, 75, 135)
    p0.alignment = PP_ALIGN.CENTER
    p0.space_after = Pt(14)

    p1 = tf.add_paragraph()
    p1.text = card.sub_text
    p1.font.name = "Arial"
    p1.font.size = Pt(26)
    p1.font.bold = True
    p1.font.color.rgb = RGBColor(108, 40, 38)
    p1.alignment = PP_ALIGN.CENTER


def _render_cover_slide(pptx_slide, slide: Slide) -> None:
    bg_path = _resolve_asset_path(slide.background_image_path or TPL_COVER_BG)
    if bg_path:
        # Leave pure white strip on top (y=0..0.255) for official bilingual UY1/FMSB headers & emblem
        pptx_slide.shapes.add_picture(
            str(bg_path.resolve()),
            Inches(0),
            Inches(SLIDE_HEIGHT_IN * 0.255),
            Inches(SLIDE_WIDTH_IN),
            Inches(SLIDE_HEIGHT_IN * 0.745),
        )

    for box in slide.render_boxes:
        if box.role == "cover_logo":
            logo_p = _resolve_asset_path(box.image_path or TPL_LOGO)
            if logo_p:
                _add_picture_fit_contain(
                    pptx_slide,
                    logo_p,
                    box.x_pct * SLIDE_WIDTH_IN,
                    box.y_pct * SLIDE_HEIGHT_IN,
                    box.w_pct * SLIDE_WIDTH_IN,
                    box.h_pct * SLIDE_HEIGHT_IN,
                )
            continue

        if box.role in ("cover_header_fr", "cover_header_en"):
            tx = pptx_slide.shapes.add_textbox(
                Inches(box.x_pct * SLIDE_WIDTH_IN),
                Inches(box.y_pct * SLIDE_HEIGHT_IN),
                Inches(box.w_pct * SLIDE_WIDTH_IN),
                Inches(box.h_pct * SLIDE_HEIGHT_IN),
            )
            tf = tx.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.TOP
            tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Inches(0.02)
            for idx, ln in enumerate(box.text.splitlines()):
                p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
                p.text = ln
                p.font.name = "Times New Roman"
                p.font.size = Pt(9.5 if idx == 0 else 8.5)
                p.font.bold = (idx != 1 and "*" not in ln)
                p.font.italic = (idx == 1)
                p.font.color.rgb = RGBColor(15, 23, 42)
                p.alignment = PP_ALIGN.CENTER
            continue

        if box.role == "cover_title":
            _add_card_backdrop(pptx_slide, box, border_width_pt=2.0)
            _add_borderless_textbox(pptx_slide, box, font_name="Times New Roman")
            continue

        if box.role == "cover_candidate":
            tx = pptx_slide.shapes.add_textbox(
                Inches(box.x_pct * SLIDE_WIDTH_IN),
                Inches(box.y_pct * SLIDE_HEIGHT_IN),
                Inches(box.w_pct * SLIDE_WIDTH_IN),
                Inches(box.h_pct * SLIDE_HEIGHT_IN),
            )
            tf = tx.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            lines = box.text.splitlines()
            for idx, ln in enumerate(lines):
                p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
                p.text = ln
                p.font.name = "Times New Roman"
                p.font.size = Pt(14 if idx == 1 else 12)
                p.font.bold = (idx == 1)
                p.font.italic = (idx == 0)
                p.font.color.rgb = RGBColor(108, 40, 38) if idx == 0 else RGBColor(15, 23, 42)
                p.alignment = PP_ALIGN.CENTER
            continue

        if box.role in ("cover_director", "cover_codirector"):
            _add_card_backdrop(pptx_slide, box, border_width_pt=1.0)
            tx = pptx_slide.shapes.add_textbox(
                Inches(box.x_pct * SLIDE_WIDTH_IN),
                Inches(box.y_pct * SLIDE_HEIGHT_IN),
                Inches(box.w_pct * SLIDE_WIDTH_IN),
                Inches(box.h_pct * SLIDE_HEIGHT_IN),
            )
            tf = tx.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.TOP
            tf.margin_top = Inches(0.05)

            p_head = tf.paragraphs[0]
            p_head.text = box.text
            p_head.font.name = "Times New Roman"
            p_head.font.size = Pt(box.font_size_pt)
            p_head.font.bold = True
            p_head.font.color.rgb = RGBColor(0, 75, 135)
            p_head.alignment = PP_ALIGN.CENTER
            p_head.space_after = Pt(4)

            for blk in (box.sub_text or "").split("\n\n"):
                blk_lines = [l.strip() for l in blk.splitlines() if l.strip()]
                for bi, ln in enumerate(blk_lines):
                    p = tf.add_paragraph()
                    p.text = ln
                    p.font.name = "Times New Roman"
                    p.font.size = Pt(box.font_size_pt if bi == 0 else max(9.5, box.font_size_pt - 1.5))
                    p.font.bold = (bi == 0)
                    p.font.color.rgb = RGBColor(15, 23, 42)
                    p.alignment = PP_ALIGN.CENTER
                    if bi == len(blk_lines) - 1:
                        p.space_after = Pt(4)
            continue

        if box.role == "cover_year":
            _add_card_backdrop(pptx_slide, box, corner_shape=MSO_SHAPE.ROUNDED_RECTANGLE, border_width_pt=1.5)
            _add_borderless_textbox(pptx_slide, box, font_name="Times New Roman")


def _render_plan_slide(pptx_slide, slide: Slide) -> None:
    bg_path = _resolve_asset_path(slide.background_image_path)
    if bg_path:
        pptx_slide.shapes.add_picture(
            str(bg_path.resolve()), 0, 0, Inches(SLIDE_WIDTH_IN), Inches(SLIDE_HEIGHT_IN)
        )
    for box in slide.render_boxes:
        _add_borderless_textbox(pptx_slide, box, font_name="Arial")


def _render_chronogram_slide(pptx_slide, slide: Slide) -> None:
    """Deposit the chronogram slide image as-is with exact proportional scaling (never stretched)."""
    bg_path = _resolve_asset_path(slide.background_image_path or TPL_CHRONOGRAM)
    if bg_path:
        _add_picture_fit_contain(
            pptx_slide, bg_path, 0.0, 0.0, SLIDE_WIDTH_IN, SLIDE_HEIGHT_IN
        )


def _render_budget_slide(pptx_slide, slide: Slide) -> None:
    bg_path = _resolve_asset_path(slide.background_image_path)
    if bg_path:
        pptx_slide.shapes.add_picture(
            str(bg_path.resolve()), 0, 0, Inches(SLIDE_WIDTH_IN), Inches(SLIDE_HEIGHT_IN)
        )
    for box in slide.render_boxes:
        _add_borderless_textbox(pptx_slide, box, font_name="Arial")


def _render_acknowledgements_slide(pptx_slide, slide: Slide) -> None:
    bg_path = _resolve_asset_path(slide.background_image_path)
    if bg_path:
        pptx_slide.shapes.add_picture(
            str(bg_path.resolve()), 0, 0, Inches(SLIDE_WIDTH_IN), Inches(SLIDE_HEIGHT_IN)
        )
    for box in slide.render_boxes:
        _add_borderless_textbox(pptx_slide, box, font_name="Arial")


def _render_content_slide(pptx_slide, slide: Slide) -> None:
    bg_path = _resolve_asset_path(slide.background_image_path)
    if bg_path:
        pptx_slide.shapes.add_picture(
            str(bg_path.resolve()), 0, 0, Inches(SLIDE_WIDTH_IN), Inches(SLIDE_HEIGHT_IN)
        )

    proc_boxes = [b for b in slide.render_boxes if b.role == "procedure_step"]
    if proc_boxes:
        n_steps = len(proc_boxes)
        for idx, pbox in enumerate(proc_boxes):
            left_in = pbox.x_pct * SLIDE_WIDTH_IN
            top_in = pbox.y_pct * SLIDE_HEIGHT_IN
            w_in = pbox.w_pct * SLIDE_WIDTH_IN
            h_in = pbox.h_pct * SLIDE_HEIGHT_IN

            # Card background shape (white with blue border)
            card = pptx_slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(left_in),
                Inches(top_in),
                Inches(w_in),
                Inches(h_in),
            )
            card.fill.solid()
            card.fill.fore_color.rgb = RGBColor(248, 250, 252)
            card.line.color.rgb = RGBColor(0, 75, 135)
            card.line.width = Pt(1.75)

            # Top header ribbon inside each procedure box
            hdr = pptx_slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(left_in),
                Inches(top_in),
                Inches(w_in),
                Inches(0.68),
            )
            hdr.fill.solid()
            hdr.fill.fore_color.rgb = RGBColor(0, 75, 135)
            hdr.line.fill.background()

            # Foreground editable textbox for step title
            tx_h = pptx_slide.shapes.add_textbox(
                Inches(left_in + 0.08), Inches(top_in + 0.04), Inches(w_in - 0.16), Inches(0.60)
            )
            tf_h = tx_h.text_frame
            tf_h.word_wrap = True
            tf_h.vertical_anchor = MSO_ANCHOR.MIDDLE
            ph = tf_h.paragraphs[0]
            ph.text = pbox.text
            ph.font.name = "Arial"
            ph.font.size = Pt(16)
            ph.font.bold = True
            ph.font.color.rgb = RGBColor(255, 255, 255)
            ph.alignment = PP_ALIGN.CENTER

            # Foreground editable textbox for step description
            tx_b = pptx_slide.shapes.add_textbox(
                Inches(left_in + 0.12), Inches(top_in + 0.78), Inches(w_in - 0.24), Inches(h_in - 0.90)
            )
            tf_b = tx_b.text_frame
            tf_b.word_wrap = True
            tf_b.vertical_anchor = MSO_ANCHOR.MIDDLE
            pb = tf_b.paragraphs[0]
            pb.text = pbox.sub_text
            pb.font.name = "Arial"
            pb.font.size = Pt(pbox.font_size_pt)
            pb.font.color.rgb = RGBColor(30, 41, 59)
            pb.alignment = PP_ALIGN.CENTER

            # Vector connecting arrow between steps
            if idx < n_steps - 1:
                next_left = proc_boxes[idx + 1].x_pct * SLIDE_WIDTH_IN
                gap = max(0.25, next_left - (left_in + w_in))
                arrow_w = gap * 0.72
                arrow_left = left_in + w_in + (gap - arrow_w) / 2.0
                arrow_top = top_in + h_in / 2.0 - 0.22
                arr = pptx_slide.shapes.add_shape(
                    MSO_SHAPE.RIGHT_ARROW,
                    Inches(arrow_left),
                    Inches(arrow_top),
                    Inches(arrow_w),
                    Inches(0.44),
                )
                arr.fill.solid()
                arr.fill.fore_color.rgb = RGBColor(108, 40, 38)
                arr.line.fill.background()

    for box in slide.render_boxes:
        if box.role == "procedure_step":
            continue

        if box.role == "media":
            _add_card_backdrop(pptx_slide, box, border_width_pt=1.0)
            fig_p = _resolve_asset_path(box.image_path)
            if fig_p:
                _add_picture_fit_contain(
                    pptx_slide,
                    fig_p,
                    box.x_pct * SLIDE_WIDTH_IN + 0.08,
                    box.y_pct * SLIDE_HEIGHT_IN + 0.08,
                    box.w_pct * SLIDE_WIDTH_IN - 0.16,
                    box.h_pct * SLIDE_HEIGHT_IN - 0.16,
                )
            continue

        if box.role == "stat_card":
            _add_card_backdrop(pptx_slide, box, border_width_pt=1.5)
            tx = pptx_slide.shapes.add_textbox(
                Inches(box.x_pct * SLIDE_WIDTH_IN + 0.12),
                Inches(box.y_pct * SLIDE_HEIGHT_IN + 0.12),
                Inches(box.w_pct * SLIDE_WIDTH_IN - 0.24),
                Inches(box.h_pct * SLIDE_HEIGHT_IN - 0.24),
            )
            tf = tx.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            p0 = tf.paragraphs[0]
            p0.text = box.text
            p0.font.name = "Arial"
            p0.font.size = Pt(28)
            p0.font.bold = True
            p0.font.color.rgb = RGBColor(108, 40, 38)
            p0.alignment = PP_ALIGN.CENTER
            p0.space_after = Pt(6)
            p1 = tf.add_paragraph()
            p1.text = box.sub_text
            p1.font.name = "Arial"
            p1.font.size = Pt(box.font_size_pt)
            p1.font.color.rgb = RGBColor(30, 41, 59)
            p1.alignment = PP_ALIGN.CENTER
            continue

        if box.role == "bullet_card":
            # Optional subtle card backdrop drawn behind the borderless foreground textbox
            if slide.slide_type == "objective_general":
                _add_card_backdrop(pptx_slide, box, border_width_pt=1.75)
                _add_borderless_textbox(pptx_slide, box, text_override=box.text, font_name="Arial")
            else:
                _add_card_backdrop(pptx_slide, box, border_width_pt=0.75)
                bullet_txt = box.text if box.text.lstrip().startswith("•") else f"• {box.text}"
                _add_borderless_textbox(pptx_slide, box, text_override=bullet_txt, font_name="Arial")
            continue

        # title, breadcrumb, slide_number, media_caption -> strictly borderless foreground textboxes
        _add_borderless_textbox(pptx_slide, box, font_name="Arial")


def compile_presentation_pptx(
    deck: PresentationDeck,
    output_path: Path,
    project: Optional[ProtocolProject] = None,
) -> Path:
    """Compile a native 16:9 PowerPoint (.pptx) from the physical RenderPlan."""
    compute_deck_layout(deck, project=project)

    prs = Presentation()
    prs.slide_width = Inches(SLIDE_WIDTH_IN)
    prs.slide_height = Inches(SLIDE_HEIGHT_IN)
    blank_layout = prs.slide_layouts[6]

    for slide_data in deck.slides:
        pptx_slide = prs.slides.add_slide(blank_layout)
        stype = slide_data.slide_type

        if stype == "welcome":
            _render_welcome_slide(pptx_slide, slide_data)
        elif stype == "cover":
            _render_cover_slide(pptx_slide, slide_data)
        elif stype == "plan":
            _render_plan_slide(pptx_slide, slide_data)
        elif stype == "chronogram" or slide_data.locked_as_is:
            _render_chronogram_slide(pptx_slide, slide_data)
        elif stype == "budget":
            _render_budget_slide(pptx_slide, slide_data)
        elif stype == "acknowledgements":
            _render_acknowledgements_slide(pptx_slide, slide_data)
        else:
            _render_content_slide(pptx_slide, slide_data)

        if slide_data.presenter_notes:
            try:
                notes_slide = pptx_slide.notes_slide
                notes_slide.notes_text_frame.text = slide_data.presenter_notes
            except Exception:
                pass

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path.resolve()))
    logger.info("Compiled %d-slide PPTX -> %s", len(deck.slides), output_path)
    return output_path
