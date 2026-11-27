import copy
import math
from pathlib import Path
import re
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape

from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image as PILImage

from app.data.i18n import get_doc_labels, normalize_lang
from app.schemas.protocol import CoverMetadata, FormattingRules, ProtocolProject, SupervisorInfo

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

FMSB_BORDEAUX = "6C2826"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"

_BOX_ID_COUNTER = 500


def _next_box_id() -> int:
    global _BOX_ID_COUNTER
    _BOX_ID_COUNTER += 1
    return _BOX_ID_COUNTER


def _cm_to_dxa(cm_val: float) -> int:
    return int(round(cm_val * 566.929))


def _cm_to_emu(cm_val: float) -> int:
    return int(round(cm_val * 360000))


def _apply_cover_run_font(
    run,
    font_family: str,
    size_pt: float,
    bold: bool = False,
    italic: bool = False,
    color_rgb: RGBColor = RGBColor(0, 0, 0),
) -> None:
    run.font.name = font_family
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = color_rgb


def _set_cell_margins(
    cell,
    top_dxa: int = 80,
    bottom_dxa: int = 80,
    left_dxa: int = 120,
    right_dxa: int = 120,
) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    for existing in list(tcPr.findall(qn("w:tcMar"))):
        tcPr.remove(existing)
    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top_dxa}" w:type="dxa"/>'
        f'<w:left w:w="{left_dxa}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom_dxa}" w:type="dxa"/>'
        f'<w:right w:w="{right_dxa}" w:type="dxa"/>'
        "</w:tcMar>"
    )
    tcPr.append(tcMar)


def _set_cell_width_dxa(cell, width_cm: float) -> None:
    dxa = _cm_to_dxa(width_cm)
    tcPr = cell._tc.get_or_add_tcPr()
    for existing in list(tcPr.findall(qn("w:tcW"))):
        tcPr.remove(existing)
    tcPr.append(parse_xml(f'<w:tcW {nsdecls("w")} w:w="{dxa}" w:type="dxa"/>'))


def _clear_table_borders(tbl) -> None:
    tblPr = tbl._tbl.tblPr
    for existing in list(tblPr.findall(qn("w:tblBorders"))):
        tblPr.remove(existing)
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        '<w:top w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:left w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:bottom w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:insideH w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        "</w:tblBorders>"
    )
    tblPr.append(borders)


def _strip_dept_prefix(raw: str, is_french: bool) -> str:
    txt = (raw or "").strip()
    if is_french:
        txt = re.sub(
            r"^(?:d[ée]partement\s+)?(?:de\s+la\s+|de\s+l['’]\s*|du\s+|des\s+|d['’]\s*|de\s+)?",
            "",
            txt,
            flags=re.IGNORECASE,
        )
        txt = re.sub(r"^d[ée]partement\s+", "", txt, flags=re.IGNORECASE)
        return (txt.strip() or "SANTE PUBLIQUE").upper()
    txt = re.sub(r"^(?:departe?ment\s+)?(?:of\s+)?", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"^departe?ment\s+", "", txt, flags=re.IGNORECASE)
    return (txt.strip() or "PUBLIC HEALTH").upper()


def format_department_full_line(raw: str, is_french: bool) -> str:
    """Format a standalone full-width department heading without duplicating prepositions."""
    txt = re.sub(r"\s+", " ", (raw or "").strip())
    if is_french:
        if not txt:
            return "DÉPARTEMENT DE SANTÉ PUBLIQUE"
        if re.match(
            r"^d[ée]partement\s+(?:de\s+la\s+|de\s+l['’]\s*|du\s+|des\s+|d['’]\s*|de\s+)",
            txt,
            flags=re.IGNORECASE,
        ):
            return re.sub(r"^d[ée]partement\b", "DÉPARTEMENT", txt, flags=re.IGNORECASE).upper()
        txt = re.sub(r"^d[ée]partement\s*", "", txt, flags=re.IGNORECASE).strip()
        if not txt:
            return "DÉPARTEMENT DE SANTÉ PUBLIQUE"
        if re.match(
            r"^(?:de\s+la\s+|de\s+l['’]\s*|du\s+|des\s+|d['’]\s*|de\s+)",
            txt,
            flags=re.IGNORECASE,
        ):
            return f"DÉPARTEMENT {txt}".upper()
        if re.match(r"^[AEIOUYÀÂÄÉÈÊËÎÏÔÖÙÛÜŸ]", txt, flags=re.IGNORECASE):
            return f"DÉPARTEMENT D'{txt}".upper()
        return f"DÉPARTEMENT DE {txt}".upper()

    if not txt:
        return "DEPARTMENT OF PUBLIC HEALTH"
    if re.match(r"^departe?ment\s+of\b", txt, flags=re.IGNORECASE):
        return re.sub(r"^departe?ment\b", "DEPARTMENT", txt, flags=re.IGNORECASE).upper()
    txt = re.sub(r"^departe?ment\s*", "", txt, flags=re.IGNORECASE).strip()
    if not txt:
        return "DEPARTMENT OF PUBLIC HEALTH"
    if re.match(r"^of\s+", txt, flags=re.IGNORECASE):
        return f"DEPARTMENT {txt}".upper()
    return f"DEPARTMENT OF {txt}".upper()


def format_contact_tier_lines(po_box: str, email: str) -> Tuple[List[str], List[str]]:
    """Format optional PO Box & Email tier lines in French and English; empty lists if both blank."""
    clean_bp = (po_box or "").strip()
    clean_bp = re.sub(r"^(?:b\.?\s*p\.?|p\.?\s*o\.?\s*box)\s*:?\s*", "", clean_bp, flags=re.IGNORECASE).strip()

    clean_mail = (email or "").strip()
    clean_mail = re.sub(r"^(?:courriel|e-?mail|mail)\s*:?\s*", "", clean_mail, flags=re.IGNORECASE).strip()

    fr_lines: List[str] = []
    en_lines: List[str] = []
    if clean_bp:
        clean_bp_en = re.sub(r"\bcameroun\b", "Cameroon", clean_bp, flags=re.IGNORECASE)
        fr_lines.append(f"B.P. : {clean_bp}")
        en_lines.append(f"P.O. Box: {clean_bp_en}")
    if clean_mail:
        fr_lines.append(f"Courriel : {clean_mail}")
        en_lines.append(f"E-mail: {clean_mail}")
    return fr_lines, en_lines


def build_cover_header_lines(
    cover: CoverMetadata,
    include_department: bool = True,
) -> Tuple[List[Tuple[str, float, bool, bool]], List[Tuple[str, float, bool, bool]]]:
    """Build bilingual header lines for Word cover page with editable tiers 3-5 & optional contact tiers."""
    dept_fr = _strip_dept_prefix(cover.department_fr, is_french=True)
    dept_en = _strip_dept_prefix(cover.department_en, is_french=False)

    uni_fr = (getattr(cover, "university_fr", "") or "UNIVERSITÉ DE YAOUNDÉ I").strip().upper()
    uni_en = (getattr(cover, "university_en", "") or "THE UNIVERSITY OF YAOUNDÉ I").strip().upper()
    fac_fr = (getattr(cover, "faculty_fr", "") or "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES").strip().upper()
    fac_en = (getattr(cover, "faculty_en", "") or "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES").strip().upper()

    uni_fr_contacts, uni_en_contacts = format_contact_tier_lines(
        getattr(cover, "university_po_box", ""),
        getattr(cover, "university_email", ""),
    )
    fac_fr_contacts, fac_en_contacts = format_contact_tier_lines(
        getattr(cover, "faculty_po_box", ""),
        getattr(cover, "faculty_email", ""),
    )

    has_extra_tiers = bool(uni_fr_contacts or fac_fr_contacts)
    sz_head = 8.5 if has_extra_tiers else 9.0
    sz_main = 8.0 if has_extra_tiers else 8.5
    sz_fac = 7.5 if has_extra_tiers else 8.0
    sz_star = 7.0 if has_extra_tiers else 8.0
    sz_contact = 7.2 if has_extra_tiers else 7.5

    left_lines: List[Tuple[str, float, bool, bool]] = [
        ("RÉPUBLIQUE DU CAMEROUN", sz_head, True, False),
        ("Paix – Travail – Patrie", sz_main, False, True),
        ("************", sz_star, False, False),
        ("MINISTÈRE DE L'ENSEIGNEMENT SUPÉRIEUR", sz_main, True, False),
        ("**************", sz_star, False, False),
        (uni_fr, sz_main, True, False),
    ]
    for c_line in uni_fr_contacts:
        left_lines.append((c_line, sz_contact, False, False))
    left_lines.extend(
        [
            ("**************", sz_star, False, False),
            (fac_fr, sz_fac, True, False),
            ("**************", sz_star, False, False),
        ]
    )
    if include_department:
        left_lines.extend(
            [
                ("DÉPARTEMENT DE", sz_main, True, False),
                (dept_fr, sz_main, True, False),
            ]
        )
        if fac_fr_contacts:
            left_lines.append(("**************", sz_star, False, False))
    for c_line in fac_fr_contacts:
        left_lines.append((c_line, sz_contact, False, False))

    right_lines: List[Tuple[str, float, bool, bool]] = [
        ("REPUBLIC OF CAMEROON", sz_head, True, False),
        ("Peace – Work – Fatherland", sz_main, False, True),
        ("************", sz_star, False, False),
        ("MINISTRY OF HIGHER EDUCATION", sz_main, True, False),
        ("************", sz_star, False, False),
        (uni_en, sz_main, True, False),
    ]
    for c_line in uni_en_contacts:
        right_lines.append((c_line, sz_contact, False, False))
    right_lines.extend(
        [
            ("************", sz_star, False, False),
            (fac_en, sz_fac, True, False),
            ("************", sz_star, False, False),
        ]
    )
    if include_department:
        right_lines.extend(
            [
                ("DEPARTMENT OF", sz_main, True, False),
                (dept_en, sz_main, True, False),
            ]
        )
        if fac_en_contacts:
            right_lines.append(("************", sz_star, False, False))
    for c_line in fac_en_contacts:
        right_lines.append((c_line, sz_contact, False, False))

    return left_lines, right_lines


def _resolve_cover_logo_path(path_str: Optional[str]) -> Optional[Path]:
    if not path_str or not path_str.strip():
        return None
    p = Path(path_str.strip())
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p
    return p if p.exists() and p.is_file() else None


def _add_fitted_logo_paragraph(
    cell,
    img_path: Path,
    max_w_cm: float,
    max_h_cm: float,
    space_after_pt: float = 0.0,
) -> None:
    try:
        with PILImage.open(img_path) as im:
            iw, ih = im.size
        if iw <= 0 or ih <= 0:
            raise ValueError("Invalid image size")
        ratio = iw / ih
        if (max_w_cm / max_h_cm) >= ratio:
            draw_h = max_h_cm
            draw_w = draw_h * ratio
        else:
            draw_w = max_w_cm
            draw_h = draw_w / ratio
    except Exception:
        draw_w = min(max_w_cm, 2.2)
        draw_h = min(max_h_cm, 2.2)

    p_logo = cell.add_paragraph()
    p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_logo.paragraph_format.space_before = Pt(0)
    p_logo.paragraph_format.space_after = Pt(space_after_pt)
    p_logo.paragraph_format.line_spacing = 1.0
    run_logo = p_logo.add_run()
    run_logo.add_picture(str(img_path.resolve()), width=Cm(draw_w), height=Cm(draw_h))


def _append_inline_graphic_el(
    cell,
    graphic_el,
    logo_cx_emu: int,
    logo_cy_emu: int,
    space_after_pt: float = 0.0,
) -> None:
    g_copy = copy.deepcopy(graphic_el)
    for ext in g_copy.iter(f"{{{A_NS}}}ext"):
        ext.set("cx", str(logo_cx_emu))
        ext.set("cy", str(logo_cy_emu))

    p_logo = cell.add_paragraph()
    p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_logo.paragraph_format.space_before = Pt(0)
    p_logo.paragraph_format.space_after = Pt(space_after_pt)
    run_logo = p_logo.add_run()
    inline_drawing = parse_xml(
        f'<w:drawing xmlns:w="{W_NS}" xmlns:wp="{WP_NS}">'
        f'  <wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'    <wp:extent cx="{logo_cx_emu}" cy="{logo_cy_emu}"/>'
        f'    <wp:effectExtent l="19050" t="0" r="19050" b="19050"/>'
        f'    <wp:docPr id="{_next_box_id()}" name="LogoUniversite"/>'
        f'    <wp:cNvGraphicFramePr/>'
        f'  </wp:inline>'
        f'</w:drawing>'
    )
    inline_el = inline_drawing.find(f"{{{WP_NS}}}inline")
    if inline_el is not None:
        inline_el.append(g_copy)
    run_logo._r.append(inline_drawing)


def _rebuild_bilingual_header_table(
    doc,
    project: ProtocolProject,
    include_department: bool = True,
) -> int:
    """Build dynamic multi-university bilingual header tiers and 1 or 2 stacked center logos. Returns extra line count."""
    if not doc.tables:
        return 0
    t0 = doc.tables[0]
    if len(t0.rows) < 1 or len(t0.rows[0].cells) < 3:
        return 0

    t0.alignment = WD_TABLE_ALIGNMENT.CENTER
    t0.autofit = False
    tblPr = t0._tbl.tblPr
    total_w_cm = 17.7
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is not None:
        tblW.set(qn("w:w"), str(_cm_to_dxa(total_w_cm)))
        tblW.set(qn("w:type"), "dxa")
    else:
        tblPr.insert(1, parse_xml(f'<w:tblW {nsdecls("w")} w:w="{_cm_to_dxa(total_w_cm)}" w:type="dxa"/>'))
    tblInd = tblPr.find(qn("w:tblInd"))
    if tblInd is not None:
        tblInd.set(qn("w:w"), "0")
        tblInd.set(qn("w:type"), "dxa")

    col_widths_cm = [6.4, 4.9, 6.4]
    for ci, w_cm in enumerate(col_widths_cm):
        if ci < len(t0.columns):
            t0.columns[ci].width = Cm(w_cm)
        cell = t0.rows[0].cells[ci]
        cell.width = Cm(w_cm)
        _set_cell_width_dxa(cell, w_cm)
        _set_cell_margins(cell, top_dxa=20, bottom_dxa=20, left_dxa=35, right_dxa=35)

    def _write_side_cell(cell, lines: List[Tuple[str, float, bool, bool]]) -> None:
        cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
        for p_el in list(cell._tc.findall(qn("w:p"))):
            cell._tc.remove(p_el)
        for text, sz, is_bold, is_italic in lines:
            p = cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0.5 if len(lines) > 12 else 1.0)
            p.paragraph_format.line_spacing = 1.0
            r = p.add_run(text)
            _apply_cover_run_font(r, "Times New Roman", size_pt=sz, bold=is_bold, italic=is_italic)

    left_lines, right_lines = build_cover_header_lines(
        project.cover,
        include_department=include_department,
    )
    _write_side_cell(t0.rows[0].cells[0], left_lines)
    _write_side_cell(t0.rows[0].cells[2], right_lines)

    # Center cell: stack 1 or 2 logos cleanly (University on top/center, Faculty optionally below)
    center_cell = t0.rows[0].cells[1]
    center_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    graphic_el = None
    for g in center_cell._tc.iter(f"{{{A_NS}}}graphic"):
        graphic_el = copy.deepcopy(g)
        break

    for p_el in list(center_cell._tc.findall(qn("w:p"))):
        center_cell._tc.remove(p_el)

    custom_uni_logo = _resolve_cover_logo_path(getattr(project.cover, "university_logo_path", ""))
    custom_fac_logo = _resolve_cover_logo_path(getattr(project.cover, "faculty_logo_path", ""))
    default_uni_logo = _resolve_cover_logo_path("tamplates/fmsb_uy1_logo.png")

    has_two_logos = custom_fac_logo is not None
    if has_two_logos:
        if custom_uni_logo is not None:
            _add_fitted_logo_paragraph(center_cell, custom_uni_logo, max_w_cm=2.3, max_h_cm=2.0, space_after_pt=4.0)
        elif graphic_el is not None:
            _append_inline_graphic_el(center_cell, graphic_el, logo_cx_emu=612000, logo_cy_emu=766000, space_after_pt=4.0)
        elif default_uni_logo is not None:
            _add_fitted_logo_paragraph(center_cell, default_uni_logo, max_w_cm=2.3, max_h_cm=2.0, space_after_pt=4.0)
        _add_fitted_logo_paragraph(center_cell, custom_fac_logo, max_w_cm=2.3, max_h_cm=2.0, space_after_pt=0.0)
    else:
        if custom_uni_logo is not None:
            _add_fitted_logo_paragraph(center_cell, custom_uni_logo, max_w_cm=2.9, max_h_cm=3.55, space_after_pt=0.0)
        elif graphic_el is not None:
            _append_inline_graphic_el(center_cell, graphic_el, logo_cx_emu=1020000, logo_cy_emu=1277000, space_after_pt=0.0)
        elif default_uni_logo is not None:
            _add_fitted_logo_paragraph(center_cell, default_uni_logo, max_w_cm=2.9, max_h_cm=3.55, space_after_pt=0.0)

    if len(center_cell.paragraphs) == 0:
        center_cell.add_paragraph()

    return max(0, max(len(left_lines), len(right_lines)) - 12)


def _create_inline_box_paragraph(
    anchor_el,
    parent_container,
    doc,
    text: str,
    width_cm: float,
    height_cm: float,
    font_family: str,
    size_pt: int,
    fmt: FormattingRules,
    space_before_pt: int = 0,
    space_after_pt: int = 6,
    center_in_table: bool = False,
):
    """Create a centered, single-instance DrawingML bordered box (supports rounded corners & thickness)."""
    color_hex = (fmt.border_color or FMSB_BORDEAUX).lstrip("#").upper()
    if len(color_hex) != 6:
        color_hex = FMSB_BORDEAUX

    border_pt = float(getattr(fmt, "box_border_width_pt", 2.5) or 2.5)
    border_pt = max(0.75, min(8.0, border_pt))
    if fmt.chapter_box_style == "minimal":
        border_pt = min(border_pt, 1.5)
    ln_w_emu = int(round(border_pt * 12700))

    raw_corner = getattr(fmt, "box_corner_radius_pct", None)
    corner_pct = int(18 if raw_corner is None else raw_corner)
    corner_pct = max(0, min(50, corner_pct))
    is_rounded = corner_pct > 0
    geom_xml = (
        f'<a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val {corner_pct * 1000}"/></a:avLst></a:prstGeom>'
        if is_rounded
        else '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
    )

    cx = _cm_to_emu(width_cm)
    cy = _cm_to_emu(height_cm)
    half_pts = str(size_pt * 2)
    safe_text = escape((text or "").strip())
    safe_font = escape(font_family or "Times New Roman")
    box_id = _next_box_id()

    if center_in_table:
        # Place inline DrawingML shape inside a 1x1 centered borderless table at x=0 of the cell
        # so LibreOffice Writer never shifts the inner textbox relative to the rounded border.
        if space_before_pt > 0:
            spacer_el = OxmlElement("w:p")
            anchor_el.addprevious(spacer_el)
            spacer_p = doc.paragraphs[0].__class__(spacer_el, parent_container)
            spacer_p.paragraph_format.space_before = Pt(space_before_pt)
            spacer_p.paragraph_format.space_after = Pt(0)
            spacer_p.paragraph_format.line_spacing = 1.0
            spacer_r = spacer_p.add_run(" ")
            spacer_r.font.size = Pt(1)

        box_tbl = doc.add_table(rows=1, cols=1)
        box_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        box_tbl.autofit = False
        _clear_table_borders(box_tbl)
        cell_w = Cm(width_cm)
        box_tbl.columns[0].width = cell_w
        cell = box_tbl.cell(0, 0)
        cell.width = cell_w
        _set_cell_margins(cell, top_dxa=0, bottom_dxa=0, left_dxa=0, right_dxa=0)
        anchor_el.addprevious(box_tbl._tbl)

        p_obj = cell.paragraphs[0]
        p_obj.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_obj.paragraph_format.space_before = Pt(0)
        p_obj.paragraph_format.space_after = Pt(space_after_pt)
        p_el = p_obj._p
        autofit_xml = "<a:noAutofit/>"
    else:
        p_el = OxmlElement("w:p")
        anchor_el.addprevious(p_el)
        p_obj = doc.paragraphs[0].__class__(p_el, parent_container)
        p_obj.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_obj.paragraph_format.space_before = Pt(space_before_pt)
        p_obj.paragraph_format.space_after = Pt(space_after_pt)
        autofit_xml = "<a:spAutoFit/>"

    shape_run_xml = (
        f'<w:r xmlns:w="{W_NS}" xmlns:wp="{WP_NS}" xmlns:a="{A_NS}" xmlns:wps="{WPS_NS}">'
        f"  <w:drawing>"
        f'    <wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'      <wp:extent cx="{cx}" cy="{cy}"/>'
        f'      <wp:effectExtent l="19050" t="19050" r="19050" b="19050"/>'
        f'      <wp:docPr id="{box_id}" name="FMSBBox{box_id}"/>'
        f"      <wp:cNvGraphicFramePr/>"
        f"      <a:graphic>"
        f'        <a:graphicData uri="{WPS_NS}">'
        f"          <wps:wsp>"
        f"            <wps:cNvSpPr/>"
        f"            <wps:spPr>"
        f'              <a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f"              {geom_xml}"
        f'              <a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>'
        f'              <a:ln w="{ln_w_emu}"><a:solidFill><a:srgbClr val="{color_hex}"/></a:solidFill><a:round/></a:ln>'
        f"            </wps:spPr>"
        f"            <wps:txbx>"
        f"              <w:txbxContent>"
        f"                <w:p>"
        f'                  <w:pPr><w:jc w:val="center"/><w:spacing w:before="0" w:after="0" w:line="276" w:lineRule="auto"/></w:pPr>'
        f"                  <w:r>"
        f'                    <w:rPr><w:rFonts w:ascii="{safe_font}" w:hAnsi="{safe_font}" w:cs="{safe_font}"/><w:b/><w:sz w:val="{half_pts}"/><w:szCs w:val="{half_pts}"/><w:color w:val="000000"/></w:rPr>'
        f'                    <w:t xml:space="preserve">{safe_text}</w:t>'
        f"                  </w:r>"
        f"                </w:p>"
        f"              </w:txbxContent>"
        f"            </wps:txbx>"
        f'            <wps:bodyPr wrap="square" lIns="144000" tIns="72000" rIns="144000" bIns="72000" anchor="ctr" upright="1">'
        f"              {autofit_xml}"
        f"            </wps:bodyPr>"
        f"          </wps:wsp>"
        f"        </a:graphicData>"
        f"      </a:graphic>"
        f"    </wp:inline>"
        f"  </w:drawing>"
        f"</w:r>"
    )
    p_el.append(parse_xml(shape_run_xml))
    return p_obj


def build_fmsb_cover_page(
    doc,
    project: ProtocolProject,
    fmt: FormattingRules,
    toc_anchor_p,
) -> None:
    """Algorithmically build a single-page, rounded-frame, condensed-header FMSB cover page."""
    if not doc.tables or toc_anchor_p is None:
        return

    body = doc.element.body
    header_tbl_el = doc.tables[0]._tbl
    toc_el = toc_anchor_p._p

    # 1. Remove legacy paragraphs / floating boxes / supervision table between header and TOC
    children = list(body)
    try:
        start_idx = children.index(header_tbl_el)
        end_idx = children.index(toc_el)
    except ValueError:
        return

    for el in children[start_idx + 1 : end_idx]:
        body.remove(el)

    # 2. Condense lateral headers & enlarge UY1/FMSB logo(s) in center
    lang = normalize_lang(getattr(project, "language", "fr"))
    labels = get_doc_labels(lang)
    dept_above_title = (
        getattr(fmt, "cover_department_layout", "header_columns") == "above_title"
    )
    extra_header_lines = _rebuild_bilingual_header_table(
        doc,
        project,
        include_department=not dept_above_title,
    )

    cover = project.cover
    font_family = fmt.font_family or "Times New Roman"
    usable_width_cm = max(13.0, 21.0 - 2.0 * (fmt.margin_cm or 2.5) - 0.3)

    title_pt = (
        min(18, fmt.cover_title_max_pt or 18)
        if (fmt.cover_title_max_pt or 18) >= 18
        else (fmt.cover_title_max_pt or 18)
    )
    title_pt = max(14, title_pt)

    def add_cover_para(
        text: str = "",
        size_pt: int = 12,
        bold: bool = False,
        italic: bool = False,
        space_before: int = 0,
        space_after: int = 6,
        line_spacing: float = 1.15,
    ):
        p_el = OxmlElement("w:p")
        toc_el.addprevious(p_el)
        p_obj = doc.paragraphs[0].__class__(p_el, toc_anchor_p._parent)
        p_obj.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_obj.paragraph_format.space_before = Pt(space_before)
        p_obj.paragraph_format.space_after = Pt(space_after)
        p_obj.paragraph_format.line_spacing = line_spacing
        if text:
            run = p_obj.add_run(text)
            _apply_cover_run_font(run, font_family, size_pt=size_pt, bold=bold, italic=italic)
        return p_obj

    if dept_above_title:
        dept_heading = format_department_full_line(
            cover.department_en if lang == "en" else cover.department_fr,
            is_french=(lang != "en"),
        )
        add_cover_para(
            dept_heading,
            size_pt=12,
            bold=True,
            space_before=12,
            space_after=6,
            line_spacing=1.15,
        )

    # 3. Title Box (wide, single-instance rounded/styled DrawingML inline box, dynamic font & height so Page 1 never overflows)
    title_text = (cover.title or project.name or "TITRE DU PROTOCOLE DE RECHERCHE").strip()
    if len(title_text) > 175:
        title_pt = min(title_pt, 14)
    elif len(title_text) > 130:
        title_pt = min(title_pt, 15)
    char_w_cm = 0.27 if title_pt <= 14 else (0.29 if title_pt <= 15 else 0.32)
    chars_per_line = max(32, int((usable_width_cm - 0.8) / char_w_cm))
    est_lines = sum(
        max(1, math.ceil(len(part.strip()) / chars_per_line))
        for part in (title_text.splitlines() or [title_text])
    )
    line_h_cm = 0.66 if title_pt <= 14 else (0.72 if title_pt <= 15 else 0.80)
    title_box_h_cm = max(2.2, min(6.2, est_lines * line_h_cm + 0.72))
    is_compact_cover = est_lines >= 5 or has_extra_tiers or len(cover.co_directors_info or []) >= 2
    _create_inline_box_paragraph(
        anchor_el=toc_el,
        parent_container=toc_anchor_p._parent,
        doc=doc,
        text=title_text,
        width_cm=usable_width_cm,
        height_cm=title_box_h_cm,
        font_family=font_family,
        size_pt=title_pt,
        fmt=fmt,
        space_before_pt=4 if (dept_above_title or is_compact_cover) else 12,
        space_after_pt=6 if is_compact_cover else 10,
    )

    # 4. Exact FMSB thesis protocol line just below title box + Student identity
    degree = (cover.degree_label or ("Medical Doctor Thesis" if lang == "en" else "Doctorat en Médecine Générale")).strip()
    add_cover_para(
        labels["cover_written_by"].format(degree=degree),
        size_pt=12,
        italic=False,
        space_before=4 if is_compact_cover else 6,
        space_after=6 if is_compact_cover else 10,
    )

    author = (cover.author_name or ("Candidate Full Name" if lang == "en" else "Noms & Prénoms de l'étudiant")).strip()
    add_cover_para(author, size_pt=13, bold=True, space_before=0, space_after=2)

    matricule = (cover.matricule or "—").strip()
    add_cover_para(
        labels["cover_reg_no"].format(matricule=matricule),
        size_pt=12,
        bold=False,
        space_before=0,
        space_after=8 if is_compact_cover else 14,
    )

    # 5. Supervision table (Directeur / Supervisor | Co-directeur(s) / Co-supervisor(s))
    sup_tbl = doc.add_table(rows=1, cols=2)
    sup_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    sup_tbl.autofit = False
    _clear_table_borders(sup_tbl)
    half_w = Cm(usable_width_cm / 2.0)
    for col_idx in (0, 1):
        sup_tbl.columns[col_idx].width = half_w
        sup_tbl.cell(0, col_idx).width = half_w

    def _render_supervisor_blocks(c, heading: str, supervisors: list[SupervisorInfo]) -> None:
        _set_cell_margins(c, top_dxa=40, bottom_dxa=40, left_dxa=100, right_dxa=100)
        p_h = c.paragraphs[0]
        p_h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_h.paragraph_format.space_before = Pt(0)
        p_h.paragraph_format.space_after = Pt(5)
        r_h = p_h.add_run(heading)
        _apply_cover_run_font(r_h, "Times New Roman", size_pt=12, bold=True)

        valid_sups = [s for s in supervisors if s.has_content()]
        if not valid_sups:
            p_empty = c.add_paragraph()
            p_empty.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_empty.paragraph_format.space_before = Pt(0)
            p_empty.paragraph_format.space_after = Pt(2)
            r_empty = p_empty.add_run(labels["cover_to_complete"])
            _apply_cover_run_font(r_empty, "Times New Roman", size_pt=12, bold=False)
            return

        for idx, sup in enumerate(valid_sups):
            first_line = True
            for field_val, size_pt, is_bold in (
                (sup.name, 14, True),
                (sup.rank, 12, False),
                (sup.specialty, 12, False),
            ):
                clean_val = (field_val or "").strip()
                if not clean_val:
                    continue
                p_v = c.add_paragraph()
                p_v.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_v.paragraph_format.space_before = Pt(12 if (idx > 0 and first_line) else 0)
                p_v.paragraph_format.space_after = Pt(2)
                p_v.paragraph_format.line_spacing = 1.1
                r_v = p_v.add_run(clean_val)
                _apply_cover_run_font(r_v, "Times New Roman", size_pt=size_pt, bold=is_bold)
                first_line = False

    dir_list = [cover.director_info] if cover.director_info.has_content() else []
    codir_list = [c for c in cover.co_directors_info if c.has_content()]

    _render_supervisor_blocks(sup_tbl.cell(0, 0), labels["cover_supervisor"], dir_list)
    _render_supervisor_blocks(sup_tbl.cell(0, 1), labels["cover_cosupervisor"], codir_list)
    toc_el.addprevious(sup_tbl._tbl)

    # 6. Academic year anchored cleanly at the bottom of Page 1 (never overflowing to Page 2 in LibreOffice/Word)
    extra_codir_lines = max(0, len(codir_list) - 1) * 3
    approx_used_pt = (
        185 + (extra_header_lines * 11)  # bilingual header table + logos
        + (30 if dept_above_title else 0)  # optional full-width department line
        + (title_box_h_cm * 28.35) + 18  # title box + margins
        + (88 if is_compact_cover else 108)  # degree + student + matricule
        + (125 + extra_codir_lines * 20)  # supervision table (accounts for 2-line prof names + rank + specialty)
        + 48  # academic year box + page border safety padding
    )
    available_pt = (29.7 - 2.0 * (fmt.margin_cm or 2.5)) * 28.35
    bottom_push_pt = max(14, min(145, int(available_pt - approx_used_pt + 22)))

    acad = (cover.academic_year or "2025-2026").strip()
    _create_inline_box_paragraph(
        anchor_el=toc_el,
        parent_container=toc_anchor_p._parent,
        doc=doc,
        text=labels["cover_academic_year"].format(year=acad),
        width_cm=8.2,
        height_cm=0.95,
        font_family="Times New Roman",
        size_pt=12,
        fmt=fmt,
        space_before_pt=bottom_push_pt,
        space_after_pt=0,
        center_in_table=True,
    )
