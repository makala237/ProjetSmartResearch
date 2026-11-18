import copy
import math
import re
from xml.sax.saxutils import escape

from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt, RGBColor

from app.schemas.protocol import FormattingRules, ProtocolProject, SupervisorInfo

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
        txt = re.sub(r"^d[ée]partement\s+(?:de\s+la\s+|de\s+l'|du\s+|des\s+|d'|de\s+)?", "", txt, flags=re.IGNORECASE)
        return (txt.strip() or "SANTE PUBLIQUE").upper()
    txt = re.sub(r"^departe?ment\s+(?:of\s+)?", "", txt, flags=re.IGNORECASE)
    return (txt.strip() or "PUBLIC HEALTH").upper()


def _rebuild_bilingual_header_table(doc, project: ProtocolProject) -> None:
    """Condense side headers on 2 lines, stretch laterally, and enlarge centered UY1/FMSB logo."""
    if not doc.tables:
        return
    t0 = doc.tables[0]
    if len(t0.rows) < 1 or len(t0.rows[0].cells) < 3:
        return

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

    dept_fr = _strip_dept_prefix(project.cover.department_fr, is_french=True)
    dept_en = _strip_dept_prefix(project.cover.department_en, is_french=False)

    def _write_side_cell(cell, lines: list[tuple[str, float, bool, bool]]) -> None:
        cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
        for p_el in list(cell._tc.findall(qn("w:p"))):
            cell._tc.remove(p_el)
        for idx, (text, sz, is_bold, is_italic) in enumerate(lines):
            p = cell.add_paragraph() if idx > 0 else cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = 1.0
            r = p.add_run(text)
            _apply_cover_run_font(r, "Times New Roman", size_pt=sz, bold=is_bold, italic=is_italic)

    left_lines = [
        ("REPUBLIQUE DU CAMEROUN", 9.0, True, False),
        ("Paix – Travail – Patrie", 8.5, False, True),
        ("************", 8.0, False, False),
        ("MINISTERE DE L’ENSEIGNEMENT", 8.5, True, False),
        ("SUPERIEUR", 8.5, True, False),
        ("**************", 8.0, False, False),
        ("UNIVERSITE DE YAOUNDE I", 8.5, True, False),
        ("**************", 8.0, False, False),
        ("FACULTE DE MEDECINE ET DES", 8.0, True, False),
        ("SCIENCES BIOMEDICALES", 8.0, True, False),
        ("**************", 8.0, False, False),
        ("DEPARTEMENT DE", 8.5, True, False),
        (dept_fr, 8.5, True, False),
    ]
    right_lines = [
        ("REPUBLIC OF CAMEROON", 9.0, True, False),
        ("Peace – Work – Fatherland", 8.5, False, True),
        ("************", 8.0, False, False),
        ("MINISTRY OF HIGHER", 8.5, True, False),
        ("EDUCATION", 8.5, True, False),
        ("************", 8.0, False, False),
        ("THE UNIVERSITY OF YAOUNDE I", 8.5, True, False),
        ("************", 8.0, False, False),
        ("FACULTY OF MEDICINE AND", 8.0, True, False),
        ("BIOMEDICAL SCIENCES", 8.0, True, False),
        ("************", 8.0, False, False),
        ("DEPARTMENT OF", 8.5, True, False),
        (dept_en, 8.5, True, False),
    ]
    _write_side_cell(t0.rows[0].cells[0], left_lines)
    _write_side_cell(t0.rows[0].cells[2], right_lines)

    # Center cell: enlarge and center the official UY1 logo inline cleanly
    center_cell = t0.rows[0].cells[1]
    center_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    graphic_el = None
    for g in center_cell._tc.iter(f"{{{A_NS}}}graphic"):
        graphic_el = copy.deepcopy(g)
        break

    if graphic_el is not None:
        logo_cx = 1020000  # ~2.83 cm (up from 1.30 cm)
        logo_cy = 1277000  # ~3.55 cm (up from 1.62 cm)
        for ext in graphic_el.iter(f"{{{A_NS}}}ext"):
            ext.set("cx", str(logo_cx))
            ext.set("cy", str(logo_cy))

        for p_el in list(center_cell._tc.findall(qn("w:p"))):
            center_cell._tc.remove(p_el)

        p_logo = center_cell.add_paragraph()
        p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_logo.paragraph_format.space_before = Pt(0)
        p_logo.paragraph_format.space_after = Pt(0)
        run_logo = p_logo.add_run()
        inline_drawing = parse_xml(
            f'<w:drawing xmlns:w="{W_NS}" xmlns:wp="{WP_NS}">'
            f'  <wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'    <wp:extent cx="{logo_cx}" cy="{logo_cy}"/>'
            f'    <wp:effectExtent l="19050" t="0" r="19050" b="19050"/>'
            f'    <wp:docPr id="{_next_box_id()}" name="LogoUY1"/>'
            f'    <wp:cNvGraphicFramePr/>'
            f'  </wp:inline>'
            f'</w:drawing>'
        )
        inline_el = inline_drawing.find(f"{{{WP_NS}}}inline")
        if inline_el is not None:
            inline_el.append(graphic_el)
        run_logo._r.append(inline_drawing)


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

    p_el = OxmlElement("w:p")
    anchor_el.addprevious(p_el)
    p_obj = doc.paragraphs[0].__class__(p_el, parent_container)
    p_obj.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_obj.paragraph_format.space_before = Pt(space_before_pt)
    p_obj.paragraph_format.space_after = Pt(space_after_pt)

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
        f"              <a:spAutoFit/>"
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

    # 2. Condense lateral headers & enlarge UY1/FMSB logo in center
    _rebuild_bilingual_header_table(doc, project)

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

    # 3. Title Box (wide, single-instance rounded/styled DrawingML inline box, 18pt, dynamic height)
    title_text = (cover.title or project.name or "TITRE DU PROTOCOLE DE RECHERCHE").strip()
    chars_per_line = max(30, int((usable_width_cm - 0.8) / 0.32))
    est_lines = sum(
        max(1, math.ceil(len(part.strip()) / chars_per_line))
        for part in (title_text.splitlines() or [title_text])
    )
    title_box_h_cm = max(2.2, min(8.2, est_lines * 0.82 + 0.85))
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
        space_before_pt=14,
        space_after_pt=12,
    )

    # 4. Exact FMSB thesis protocol line just below title box + Student identity
    degree = (cover.degree_label or "Doctorat en Médecine Générale").strip()
    add_cover_para(
        f"Protocole de thèse de {degree} rédigé par :",
        size_pt=12,
        italic=False,
        space_before=6,
        space_after=10,
    )

    author = (cover.author_name or "Noms & Prénoms de l'étudiant").strip()
    add_cover_para(author, size_pt=13, bold=True, space_before=0, space_after=3)

    matricule = (cover.matricule or "—").strip()
    add_cover_para(f"Matricule : {matricule}", size_pt=12, bold=False, space_before=0, space_after=16)

    # 5. Supervision table (Directeur | Co-directeur(s))
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
            r_empty = p_empty.add_run("À renseigner")
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

    _render_supervisor_blocks(sup_tbl.cell(0, 0), "Directeur", dir_list)
    _render_supervisor_blocks(sup_tbl.cell(0, 1), "Co-directeur(s)", codir_list)
    toc_el.addprevious(sup_tbl._tbl)

    # 6. Academic year pushed toward the bottom of Page 1 inside a small matching bordered frame
    extra_codir_lines = max(0, len(codir_list) - 1) * 3
    approx_used_pt = (
        130  # header table
        + (title_box_h_cm * 28.35)
        + 105  # degree + student + matricule
        + (95 + extra_codir_lines * 18)  # supervision table
        + 40  # academic year box
    )
    available_pt = (29.7 - 2.0 * (fmt.margin_cm or 2.5)) * 28.35
    bottom_push_pt = max(14, min(140, int(available_pt - approx_used_pt - 28)))

    acad = (cover.academic_year or "2025-2026").strip()
    _create_inline_box_paragraph(
        anchor_el=toc_el,
        parent_container=toc_anchor_p._parent,
        doc=doc,
        text=f"Année académique {acad}",
        width_cm=7.4,
        height_cm=1.05,
        font_family="Times New Roman",
        size_pt=12,
        fmt=fmt,
        space_before_pt=bottom_push_pt,
        space_after_pt=0,
    )
