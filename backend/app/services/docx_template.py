import copy
from html.parser import HTMLParser
import io
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt, RGBColor
import httpx
from PIL import Image as PILImage

from app.core.config import WORKSPACE_DIR, get_settings
from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import FormattingRules, ProtocolProject, StepStatus
from app.services.annexes_generator import build_annexes_pack
from app.services.cover_builder import build_fmsb_cover_page
from app.services.crf_builder import get_crf_checkbox_png_bytes
from app.services.diagram_renderer import (
    compute_minimum_legible_figure_size_cm,
    render_diagram_png,
)
from app.services.humanizer import (
    apply_human_author_metadata_docx,
    sanitize_llm_french_typography,
)
from app.services.list_builder import (
    build_unified_abbreviations_list,
    build_unified_tables_and_figures_lists,
    clean_caption_title,
    insert_word_automatic_caption_list,
)
from app.services.page_layout_engine import (
    FMSB_CHAPTER_TITLES,
    is_duplicate_heading,
)
from app.services.rich_formatter import latex_to_plain_math, markdown_to_clean_html, split_math_segments
from app.services.toc_builder import (
    configure_word_toc_styles,
    insert_word_automatic_toc,
)
from app.services.vancouver import build_and_validate_vancouver_references

SOURCE_CANVAS_DOCX = WORKSPACE_DIR / "2025_Canevas_protocole.docx"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FMSB_BORDEAUX = "6C2826"
FMSB_GANTT_FILL = "8B3A38"


class _SimpleHTMLRunsParser(HTMLParser):
    """Parse TipTap HTML subset (<p>, <h3>, <h4>, <strong>, <em>, <u>, <sup>, <sub>, <li>, <table>)."""

    def __init__(self) -> None:
        super().__init__()
        # Each item is ("para", (is_bullet, heading_level, is_center, runs))
        # or ("table", (headers, data_rows))
        self.items: List[Tuple[str, Any]] = []
        self._current_runs: List[Tuple[str, bool, bool, bool, bool, bool]] = []
        self._bold = False
        self._italic = False
        self._underline = False
        self._sup = False
        self._sub = False
        self._in_li = False
        self._heading_level = 0
        self._in_center = False
        # Table state
        self._in_table = False
        self._in_cell = False
        self._current_row_is_header = False
        self._current_row: List[str] = []
        self._cell_chunks: List[str] = []
        self._table_rows: List[Tuple[bool, List[str]]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        attr_dict = dict(attrs)
        if t == "table":
            self._flush_para(
                is_bullet=self._in_li,
                heading_level=self._heading_level,
                is_center=self._in_center,
            )
            self._in_table = True
            self._table_rows = []
            return
        if self._in_table:
            if t == "tr":
                self._current_row = []
                self._current_row_is_header = False
            elif t in ("th", "td"):
                self._in_cell = True
                self._cell_chunks = []
                if t == "th":
                    self._current_row_is_header = True
            elif t == "br" and self._in_cell:
                self._cell_chunks.append(" ")
            return

        if t in ("strong", "b"):
            self._bold = True
        elif t in ("em", "i"):
            self._italic = True
        elif t == "u":
            self._underline = True
        elif t == "sup":
            self._sup = True
        elif t == "sub":
            self._sub = True
        elif t == "li":
            self._in_li = True
            self._flush_para()
        elif t in ("h1", "h2", "h3", "h4"):
            self._flush_para()
            self._heading_level = int(t[1])
        elif t in ("p", "div"):
            style_val = (attr_dict.get("style") or "").lower()
            if "flex-direction:column" in style_val:
                # Stacked HTML fraction: separate numerator and denominator cleanly with " / "
                return
            self._flush_para()
            self._in_center = "text-align:center" in style_val
        elif t == "br":
            self._current_runs.append(
                ("\n", self._bold or bool(self._heading_level), self._italic, self._underline, self._sup, self._sub)
            )

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if self._in_table:
            if t in ("th", "td") and self._in_cell:
                cell_txt = " ".join("".join(self._cell_chunks).split())
                self._current_row.append(cell_txt)
                self._cell_chunks = []
                self._in_cell = False
            elif t in ("p", "div") and self._in_cell:
                if self._cell_chunks and not self._cell_chunks[-1].endswith(" "):
                    self._cell_chunks.append(" ")
            elif t == "tr" and self._current_row:
                self._table_rows.append((self._current_row_is_header, self._current_row))
                self._current_row = []
            elif t == "table":
                self._flush_table()
                self._in_table = False
            return

        if t in ("strong", "b"):
            self._bold = False
        elif t in ("em", "i"):
            self._italic = False
        elif t == "u":
            self._underline = False
        elif t == "sup":
            self._sup = False
        elif t == "sub":
            self._sub = False
        elif t == "li":
            self._flush_para(is_bullet=True)
            self._in_li = False
        elif t in ("h1", "h2", "h3", "h4"):
            self._flush_para(heading_level=self._heading_level)
            self._heading_level = 0
        elif t in ("p", "div"):
            self._flush_para(is_bullet=self._in_li, is_center=self._in_center)
            self._in_center = False

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self._in_cell:
            self._cell_chunks.append(data)
            return
        self._current_runs.append(
            (data, self._bold or bool(self._heading_level), self._italic, self._underline, self._sup, self._sub)
        )

    def _flush_para(self, is_bullet: bool = False, heading_level: int = 0, is_center: bool = False) -> None:
        text_check = "".join(r[0] for r in self._current_runs).strip()
        if text_check:
            self.items.append(("para", (is_bullet, heading_level, is_center, self._current_runs)))
        self._current_runs = []

    def _flush_table(self) -> None:
        if self._in_cell:
            self._current_row.append(" ".join("".join(self._cell_chunks).split()))
            self._cell_chunks = []
            self._in_cell = False
        if self._current_row:
            self._table_rows.append((self._current_row_is_header, self._current_row))
            self._current_row = []
        if not self._table_rows:
            return
        max_cols = max(len(r) for _, r in self._table_rows)
        if max_cols == 0:
            self._table_rows = []
            return
        padded = [r + [""] * (max_cols - len(r)) for _, r in self._table_rows]
        headers = padded[0]
        data_rows = padded[1:]
        self.items.append(("table", (headers, data_rows)))
        self._table_rows = []

    def finalize(self) -> List[Tuple[str, Any]]:
        if self._in_table:
            self._flush_table()
            self._in_table = False
        self._flush_para(is_bullet=self._in_li, heading_level=self._heading_level, is_center=self._in_center)
        return self.items


def _strip_md_inline(text: str) -> str:
    """Remove simple inline Markdown emphasis wrappers inside table cells or labels."""
    clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    clean = re.sub(r"__([^_]+)__", r"\1", clean)
    clean = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", clean)
    return clean.strip()


def _parse_pipe_row(line: str) -> List[str]:
    """Split a Markdown pipe-table row into trimmed cell strings."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [_strip_md_inline(c) for c in stripped.split("|")]


def _markdown_with_tables_to_html(markdown_text: str) -> str:
    """Convert Markdown text containing pipe tables into clean HTML with <table> tags."""
    if not markdown_text or not markdown_text.strip():
        return ""
    lines = markdown_text.splitlines()
    html_blocks: List[str] = []
    md_buf: List[str] = []
    i = 0

    def _flush_md() -> None:
        if md_buf:
            chunk = "\n".join(md_buf).strip()
            if chunk:
                html_blocks.append(markdown_to_clean_html(chunk))
            md_buf.clear()

    sep_re = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")
    while i < len(lines):
        ln = lines[i]
        if (
            ln.strip().startswith("|")
            and ln.strip().endswith("|")
            and i + 1 < len(lines)
            and sep_re.match(lines[i + 1])
        ):
            _flush_md()
            headers = _parse_pipe_row(ln)
            i += 2
            rows: List[List[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                rows.append(_parse_pipe_row(lines[i]))
                i += 1
            th_html = "".join(f"<th>{h}</th>" for h in headers)
            tr_html = "".join(
                "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                for r in rows
            )
            html_blocks.append(f"<table><thead><tr>{th_html}</tr></thead><tbody>{tr_html}</tbody></table>")
            continue
        md_buf.append(ln)
        i += 1

    _flush_md()
    return "\n".join(html_blocks)


def _extract_abbreviations_from_markdown(md_str: str) -> Tuple[List[Tuple[str, str]], str]:
    """Extract '- **SIGLE** : Signification' lines and return (pairs, remaining_markdown)."""
    if not md_str or not md_str.strip():
        return [], ""
    pairs: List[Tuple[str, str]] = []
    remaining: List[str] = []
    abbr_re = re.compile(
        r"^(?:[-*•]\s+)?(?:\*\*|__)?([A-Za-z0-9À-ÖØ-öø-ÿ/+.-]{2,25}(?:\s+[A-Za-z0-9À-ÖØ-öø-ÿ/+.-]{1,10})?)(?:\*\*|__)?\s*[:–—-]\s+(.+)$"
    )
    for raw_line in md_str.splitlines():
        s = raw_line.strip()
        if not s:
            remaining.append("")
            continue
        if re.match(r"^#{1,6}\s+liste\s+des\s+abr[ée]viations", s, flags=re.IGNORECASE):
            continue
        m = abbr_re.match(s)
        if m:
            lhs = _strip_md_inline(m.group(1))
            rhs = _strip_md_inline(m.group(2))
            has_bullet_or_bold = s.startswith(("-", "*", "•", "**", "__"))
            has_upper_or_digit = any(ch.isupper() or ch.isdigit() for ch in lhs)
            if has_bullet_or_bold and has_upper_or_digit and len(lhs) <= 24:
                pairs.append((lhs, rhs))
                continue
        remaining.append(raw_line)
    return pairs, "\n".join(remaining).strip()


def _replace_text_in_element(element: ET.Element, mapping: Dict[str, str]) -> None:
    # 1. Fast pass on individual <w:t> nodes
    for t_node in element.iter(f"{{{W_NS}}}t"):
        if not t_node.text:
            continue
        new_text = t_node.text
        for old, new in mapping.items():
            if old in new_text:
                new_text = new_text.replace(old, new)
        t_node.text = new_text

    # 2. Paragraph-level pass for keys split across multiple <w:t> runs
    for p_node in element.iter(f"{{{W_NS}}}p"):
        if p_node.find(f".//{{{W_NS}}}txbxContent") is not None:
            continue
        t_nodes = [t for t in p_node.iter(f"{{{W_NS}}}t") if t.text is not None]
        if len(t_nodes) <= 1:
            continue
        combined = "".join(t.text or "" for t in t_nodes)
        updated = combined
        for old, new in mapping.items():
            if old in updated:
                updated = updated.replace(old, new)
        if updated != combined:
            t_nodes[0].text = updated
            for extra_t in t_nodes[1:]:
                extra_t.text = ""


def _force_cover_title_size(element: ET.Element, target_pt: int = 18) -> None:
    """Enforce exact font size (default 18 pt = 36 half-points) on the cover title box."""
    half_pts = str(max(12, target_pt) * 2)
    for p_node in element.iter(f"{{{W_NS}}}p"):
        combined = "".join(t.text or "" for t in p_node.iter(f"{{{W_NS}}}t"))
        if "{{TITLE}}" not in combined and "18 à 24" not in combined:
            continue
        pPr = p_node.find(f"{{{W_NS}}}pPr")
        if pPr is not None:
            rPr = pPr.find(f"{{{W_NS}}}rPr")
            if rPr is not None:
                for tag_name in ("sz", "szCs"):
                    el = rPr.find(f"{{{W_NS}}}{tag_name}")
                    if el is None:
                        el = OxmlElement(f"w:{tag_name}")
                        rPr.append(el)
                    el.set(f"{{{W_NS}}}val", half_pts)
        for r_node in p_node.iter(f"{{{W_NS}}}r"):
            rPr = r_node.find(f"{{{W_NS}}}rPr")
            if rPr is None:
                rPr = OxmlElement("w:rPr")
                r_node.insert(0, rPr)
            for tag_name in ("sz", "szCs"):
                el = rPr.find(f"{{{W_NS}}}{tag_name}")
                if el is None:
                    el = OxmlElement(f"w:{tag_name}")
                    rPr.append(el)
                el.set(f"{{{W_NS}}}val", half_pts)


def _apply_template_border_styles(doc: Document, fmt: FormattingRules) -> None:
    """Algorithmically apply border shape, corner radius, thickness, and color across cover & chapters."""
    color_hex = (fmt.border_color or FMSB_BORDEAUX).lstrip("#").upper()
    if len(color_hex) != 6:
        color_hex = FMSB_BORDEAUX

    border_pt = float(getattr(fmt, "box_border_width_pt", 2.5) or 2.5)
    border_pt = max(0.75, min(8.0, border_pt))
    raw_corner = getattr(fmt, "box_corner_radius_pct", None)
    corner_pct = int(18 if raw_corner is None else raw_corner)
    corner_pct = max(0, min(50, corner_pct))
    adj_fmla = f"val {corner_pct * 1000}"
    arc_vml = f"{int(round(corner_pct * 655.35))}f"
    ln_w_emu = str(int(round(border_pt * 12700)))
    page_border_sz = str(max(4, min(48, int(round(border_pt * 8)))))

    # Apply cover page border style (w:pgBorders on sectPr)
    for section in doc.sections:
        sectPr = section._sectPr
        pgBorders = sectPr.find(qn("w:pgBorders"))
        if pgBorders is not None:
            for side in ("top", "left", "bottom", "right"):
                el = pgBorders.find(qn(f"w:{side}"))
                if el is not None:
                    if fmt.cover_border_style == "none":
                        el.set(qn("w:val"), "none")
                        el.set(qn("w:sz"), "0")
                    elif fmt.cover_border_style == "single":
                        el.set(qn("w:val"), "single")
                        el.set(qn("w:sz"), page_border_sz)
                        el.set(qn("w:color"), color_hex)
                    else:
                        el.set(qn("w:val"), "double")
                        el.set(qn("w:sz"), page_border_sz)
                        el.set(qn("w:color"), color_hex)

    # Apply DrawingML & VML chapter box styles (only <wps:wsp> shapes, never logo <pic:pic>)
    A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
    V_NS = "urn:schemas-microsoft-com:vml"
    WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
    body = doc.element.body

    for wsp in body.iter(f"{{{WPS_NS}}}wsp"):
        spPr = wsp.find(f"{{{WPS_NS}}}spPr")
        if spPr is None:
            continue
        prstGeom = spPr.find(f"{{{A_NS}}}prstGeom")
        if prstGeom is not None:
            if corner_pct > 0:
                prstGeom.set("prst", "roundRect")
                avLst = prstGeom.find(f"{{{A_NS}}}avLst")
                if avLst is None:
                    avLst = OxmlElement("a:avLst")
                    prstGeom.append(avLst)
                for old_gd in list(avLst):
                    avLst.remove(old_gd)
                gd = OxmlElement("a:gd")
                gd.set("name", "adj")
                gd.set("fmla", adj_fmla)
                avLst.append(gd)
            else:
                prstGeom.set("prst", "rect")
        ln = spPr.find(f"{{{A_NS}}}ln")
        if ln is None:
            ln = OxmlElement("a:ln")
            spPr.append(ln)
        effective_ln = str(int(round(min(border_pt, 1.5) * 12700))) if fmt.chapter_box_style == "minimal" else ln_w_emu
        ln.set("w", effective_ln)
        if corner_pct > 0:
            ln.attrib.pop("cmpd", None)
            for m in list(ln.findall(f"{{{A_NS}}}miter")):
                ln.remove(m)
            if ln.find(f"{{{A_NS}}}round") is None:
                ln.append(parse_xml(f'<a:round xmlns:a="{A_NS}"/>'))
        for fill_tag in ("solidFill", "noFill", "gradFill"):
            for existing_fill in list(ln.findall(f"{{{A_NS}}}{fill_tag}")):
                ln.remove(existing_fill)
        solidFill = parse_xml(
            f'<a:solidFill xmlns:a="{A_NS}"><a:srgbClr val="{color_hex}"/></a:solidFill>'
        )
        ln.insert(0, solidFill)

    for v_shape in list(body.iter(f"{{{V_NS}}}rect")) + list(body.iter(f"{{{V_NS}}}roundrect")):
        if corner_pct > 0:
            v_shape.tag = f"{{{V_NS}}}roundrect"
            v_shape.set("arcsize", arc_vml)
        else:
            v_shape.tag = f"{{{V_NS}}}rect"
            if "arcsize" in v_shape.attrib:
                del v_shape.attrib["arcsize"]
        effective_vml_pt = min(border_pt, 1.5) if fmt.chapter_box_style == "minimal" else border_pt
        v_shape.set("stroked", "t")
        v_shape.set("strokecolor", f"#{color_hex}")
        v_shape.set("strokeweight", f"{effective_vml_pt:.1f}pt")


def _center_grand_chapter_box(p_obj, fmt: FormattingRules) -> None:
    """Isolate and vertically + horizontally center a grand framed chapter title on its A4 page."""
    WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    V_NS = "urn:schemas-microsoft-com:vml"

    # Strip any legacy inline page breaks inside the banner paragraph so we never double-break
    for br in list(p_obj._element.iter(f"{{{W_NS}}}br")):
        if br.get(f"{{{W_NS}}}type") == "page":
            parent = br.getparent()
            if parent is not None:
                parent.remove(br)

    p_obj.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_obj.paragraph_format.space_before = Pt(0)
    p_obj.paragraph_format.space_after = Pt(0)
    p_obj.paragraph_format.keep_together = True

    margin_cm = float(getattr(fmt, "margin_cm", 2.5) or 2.5)
    usable_w_cm = max(10.0, 21.0 - 2.0 * margin_cm)
    usable_h_cm = max(12.0, 29.7 - 2.0 * margin_cm)
    usable_w_emu = int(round(usable_w_cm * 360000))
    usable_w_pt = usable_w_cm * 28.3464567
    usable_h_pt = usable_h_cm * 28.3464567

    for anchor in p_obj._element.iter(f"{{{WP_NS}}}anchor"):
        ext = anchor.find(f"{{{WP_NS}}}extent")
        if ext is not None:
            try:
                cx = int(ext.get("cx") or "0")
                if cx > usable_w_emu - 180000:
                    ext.set("cx", str(usable_w_emu - 180000))
            except ValueError:
                pass

        for axis, rel_val in (("positionH", "margin"), ("positionV", "margin")):
            pos_el = anchor.find(f"{{{WP_NS}}}{axis}")
            if pos_el is None:
                pos_el = OxmlElement(f"wp:{axis}")
                anchor.insert(0, pos_el)
            pos_el.set("relativeFrom", rel_val)
            for child in list(pos_el):
                pos_el.remove(child)
            align_el = OxmlElement("wp:align")
            align_el.text = "center"
            pos_el.append(align_el)

    # Also update VML fallback shapes so alternate renderers center both horizontally & vertically
    for v_el in list(p_obj._element.iter(f"{{{V_NS}}}rect")) + list(p_obj._element.iter(f"{{{V_NS}}}roundrect")):
        style = v_el.get("style") or ""
        w_m = re.search(r"width:([0-9.]+)pt", style)
        h_m = re.search(r"height:([0-9.]+)pt", style)
        box_w_pt = float(w_m.group(1)) if w_m else 400.0
        box_h_pt = float(h_m.group(1)) if h_m else 93.0
        left_pt = max(0.0, (usable_w_pt - box_w_pt) / 2.0)
        top_pt = max(0.0, (usable_h_pt - box_h_pt) / 2.0)
        style = re.sub(r"margin-left:[^;]+", f"margin-left:{left_pt:.1f}pt", style)
        style = re.sub(r"margin-top:[^;]+", f"margin-top:{top_pt:.1f}pt", style)
        style = re.sub(r"mso-position-horizontal:[^;]+", "mso-position-horizontal:center", style)
        style = re.sub(r"mso-position-horizontal-relative:[^;]+", "mso-position-horizontal-relative:margin", style)
        style = re.sub(r"mso-position-vertical:[^;]+", "mso-position-vertical:center", style)
        style = re.sub(r"mso-position-vertical-relative:[^;]+", "mso-position-vertical-relative:margin", style)
        v_el.set("style", style)


def ensure_master_template() -> Path:
    """Create a clean, parameterized FMSB master template from 2025_Canevas_protocole.docx."""
    settings = get_settings()
    target_path = settings.templates_dir / "fmsb_master_template.docx"
    if not SOURCE_CANVAS_DOCX.exists():
        raise FileNotFoundError(f"Fichier source introuvable : {SOURCE_CANVAS_DOCX}")

    shutil.copyfile(SOURCE_CANVAS_DOCX, target_path)
    doc = Document(str(target_path))

    if len(doc.tables) >= 1:
        t0 = doc.tables[0]
        for row in t0.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    if "DEPARTEMENT DE SANTE PUBLIQUE" in p.text:
                        for run in p.runs:
                            run.text = run.text.replace(
                                "DEPARTEMENT DE SANTE PUBLIQUE",
                                "DEPARTEMENT DE {{DEPARTMENT_FR}}",
                            )
                    if "DEPARTEMENT OF PUBLIC HEALTH" in p.text:
                        for run in p.runs:
                            run.text = run.text.replace(
                                "DEPARTEMENT OF PUBLIC HEALTH",
                                "DEPARTMENT OF {{DEPARTMENT_EN}}",
                            )

    if len(doc.tables) >= 2:
        t1 = doc.tables[1]
        if len(t1.rows) >= 1 and len(t1.rows[0].cells) >= 3:
            dir_cell = t1.rows[0].cells[0]
            dir_cell.text = ""
            p_lbl = dir_cell.paragraphs[0]
            p_lbl.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r_lbl = p_lbl.add_run("Directeur")
            r_lbl.bold = True
            p_val = dir_cell.add_paragraph("{{DIRECTOR}}")
            p_val.alignment = WD_ALIGN_PARAGRAPH.CENTER

            codir_cell = t1.rows[0].cells[2]
            codir_cell.text = ""
            p_clbl = codir_cell.paragraphs[0]
            p_clbl.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r_clbl = p_clbl.add_run("Co-directeur(s)")
            r_clbl.bold = True
            p_cval = codir_cell.add_paragraph("{{CO_DIRECTORS}}")
            p_cval.alignment = WD_ALIGN_PARAGRAPH.CENTER

    body = doc.element.body
    body_children = list(body)
    keep_markers = {
        "TABLE DE MATIERES",
        "ABREVIATIONS, ACRONYMES & SIGLES",
        "INTRODUCTION",
        "Chapitre I",
        "Chapitre II",
        "REFERENCES",
        "ANNEXES",
    }

    to_remove = []
    past_cover = False
    for elem in body_children:
        tag = elem.tag.split("}")[-1]
        if tag == "sectPr":
            continue
        full_text = "".join(t.text for t in elem.iter(f"{{{W_NS}}}t") if t.text).strip()

        if "Année académique" in full_text:
            _replace_text_in_element(elem, {"2025-2026": "{{ACADEMIC_YEAR}}"})
            past_cover = True
            continue

        if not past_cover:
            _force_cover_title_size(elem, 18)
            _replace_text_in_element(
                elem,
                {
                    "18 à 24 …": "{{TITLE}}",
                    "Protocole rédigé en vue de l’obtention du … par\xa0:": "Protocole rédigé en vue de l’obtention du {{DEGREE_LABEL}} par :",
                    "Protocole rédigé en vue de l’obtention du … par :": "Protocole rédigé en vue de l’obtention du {{DEGREE_LABEL}} par :",
                    "Noms & prénoms,": "{{AUTHOR_NAME}}",
                    "Mat\xa0N°": "Matricule : {{MATRICULE}}",
                    "Mat N°": "Matricule : {{MATRICULE}}",
                },
            )
            continue

        if "METHODLOGIE" in full_text:
            _replace_text_in_element(
                elem,
                {
                    "Chapitre II\xa0: METHODLOGIE": "Chapitre III : METHODOLOGIE",
                    "Chapitre II : METHODLOGIE": "Chapitre III : METHODOLOGIE",
                },
            )

        if not any(m in full_text for m in keep_markers):
            to_remove.append(elem)

    for elem in to_remove:
        body.remove(elem)

    _strip_header_page_numbers(doc)
    doc.save(str(target_path))
    return target_path


def _apply_run_font(
    run,
    fmt: FormattingRules,
    size_pt: Optional[int] = None,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    color_rgb: Optional[RGBColor] = None,
) -> None:
    run.font.name = fmt.font_family
    run.font.size = Pt(size_pt or fmt.body_font_size_pt)
    run.bold = bold
    run.italic = italic
    run.underline = underline
    if color_rgb is not None:
        run.font.color.rgb = color_rgb
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), fmt.font_family)
    rFonts.set(qn("w:hAnsi"), fmt.font_family)
    rFonts.set(qn("w:cs"), fmt.font_family)


def _apply_paragraph_rules(p, fmt: FormattingRules, align_override: Optional[WD_ALIGN_PARAGRAPH] = None) -> None:
    align_map = {
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
    }
    p.alignment = align_override or align_map.get(fmt.text_align, WD_ALIGN_PARAGRAPH.JUSTIFY)
    p.paragraph_format.line_spacing = fmt.line_spacing
    p.paragraph_format.space_after = Pt(6)


def _insert_field(paragraph, instruction: str, cached_text: Optional[str] = None) -> None:
    run = paragraph.add_run()
    fldChar1 = parse_xml(r'<w:fldChar %s w:fldCharType="begin"/>' % nsdecls("w"))
    instrText = parse_xml(r'<w:instrText %s xml:space="preserve"> %s </w:instrText>' % (nsdecls("w"), instruction))
    fldChar2 = parse_xml(r'<w:fldChar %s w:fldCharType="separate"/>' % nsdecls("w"))
    fldChar3 = parse_xml(r'<w:fldChar %s w:fldCharType="end"/>' % nsdecls("w"))
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)
    if cached_text:
        t_el = OxmlElement("w:t")
        t_el.text = cached_text
        run._r.append(t_el)
    run._r.append(fldChar3)


def _set_paragraph_outline(
    p_obj,
    level_zero_based: int,
    invisible_toc_label: Optional[str] = None,
) -> None:
    """Attach OOXML <w:outlineLvl> to include paragraph in Word automatic TOC."""
    pPr = p_obj._p.get_or_add_pPr()
    for existing in list(pPr.findall(qn("w:outlineLvl"))):
        pPr.remove(existing)
    ol = OxmlElement("w:outlineLvl")
    ol.set(qn("w:val"), str(max(0, min(8, int(level_zero_based)))))
    rpr = pPr.find(qn("w:rPr"))
    if rpr is not None:
        rpr.addprevious(ol)
    else:
        pPr.append(ol)

    if invisible_toc_label:
        # Remove any stale hidden TOC label runs then append a 1pt white run
        p_obj.add_run()
        r = p_obj.runs[-1]
        r.text = invisible_toc_label
        r.font.size = Pt(1)
        r.font.color.rgb = RGBColor(255, 255, 255)
    else:
        p_obj.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_obj.paragraph_format.keep_with_next = True


def _insert_caption_with_seq(
    doc: Document,
    anchor_p,
    seq_label: str,
    num_val: int,
    separator: str,
    title_text: str,
    fmt: FormattingRules,
    size_pt: int = 11,
    bold: bool = True,
    italic: bool = False,
    align: WD_ALIGN_PARAGRAPH = WD_ALIGN_PARAGRAPH.LEFT,
    keep_with_next_override: Optional[bool] = None,
):
    """Insert a numbered caption containing a Word SEQ field for List of Tables / Figures."""
    new_p = OxmlElement("w:p")
    anchor_p._p.addnext(new_p)
    p_obj = doc.paragraphs[0].__class__(new_p, anchor_p._parent)
    p_obj.alignment = align
    p_obj.paragraph_format.line_spacing = fmt.line_spacing
    p_obj.paragraph_format.space_before = Pt(4)
    p_obj.paragraph_format.space_after = Pt(4)
    p_obj.paragraph_format.keep_together = True
    p_obj.paragraph_format.keep_with_next = (
        keep_with_next_override if keep_with_next_override is not None else (seq_label == "Tableau")
    )

    r_prefix = p_obj.add_run(f"{seq_label} ")
    _apply_run_font(r_prefix, fmt, size_pt=size_pt, bold=bold, italic=italic)

    _insert_field(p_obj, f"SEQ {seq_label} \\* ARABIC", cached_text=str(num_val))
    _apply_run_font(p_obj.runs[-1], fmt, size_pt=size_pt, bold=bold, italic=italic)

    clean_title = clean_caption_title(title_text or "", seq_label) or (title_text or "")

    r_title = p_obj.add_run(f"{separator}{clean_title}")
    _apply_run_font(r_title, fmt, size_pt=size_pt, bold=bold, italic=italic)
    return p_obj


def _set_cell_shading(cell, fill_hex: str) -> None:
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
    cell._tc.get_or_add_tcPr().append(shading)


def _set_table_borders(tbl) -> None:
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        "</w:tblBorders>"
    )
    tbl._tbl.tblPr.append(borders)


def _insert_table_after(
    doc: Document,
    anchor_p,
    headers: List[str],
    rows: List[List[str]],
    fmt: FormattingRules,
    colored_cells: Optional[Dict[Tuple[int, int], str]] = None,
):
    """Create a styled Word table and move it directly after anchor_p."""
    safe_headers = headers if headers else ["Colonne 1"]
    tbl = doc.add_table(rows=1 + len(rows), cols=len(safe_headers))
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(tbl)

    # Keep title/caption glued to table start (Header row + Row 0). Only lock ALL rows
    # together for genuinely compact tables; locking 5..12 dense rows causes Word to break
    # the keepWithNext chain BEFORE Row 0, stranding the title alone at the bottom of a page.
    anchor_txt = "".join(t.text for t in anchor_p._p.iter(qn("w:t")) if t.text).strip()
    if anchor_txt and (
        re.match(r"^(?:Tableau|Table)\s+\d+", anchor_txt, flags=re.IGNORECASE)
        or len(anchor_txt) <= 180
        or anchor_p.paragraph_format.keep_with_next
    ):
        anchor_p.paragraph_format.keep_together = True
        anchor_p.paragraph_format.keep_with_next = True

    total_chars = sum(len(str(c or "")) for r in rows for c in r)
    keep_all_rows = len(rows) <= 4 and total_chars <= 380
    hdr_trPr = tbl.rows[0]._tr.get_or_add_trPr()
    hdr_trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
    hdr_trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
    for ci, h_txt in enumerate(safe_headers):
        cell = tbl.rows[0].cells[ci]
        _set_cell_shading(cell, FMSB_BORDEAUX)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(h_txt)
        _apply_run_font(run, fmt, size_pt=max(9, fmt.body_font_size_pt - 1), bold=True, color_rgb=RGBColor(255, 255, 255))

    # Data rows
    for ri, row_vals in enumerate(rows):
        row_obj = tbl.rows[ri + 1]
        row_trPr = row_obj._tr.get_or_add_trPr()
        row_trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        for ci in range(len(safe_headers)):
            val = row_vals[ci] if ci < len(row_vals) else ""
            cell = row_obj.cells[ci]
            if colored_cells and (ri, ci) in colored_cells:
                _set_cell_shading(cell, colored_cells[(ri, ci)])
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_after = Pt(2)
            if keep_all_rows and ri < len(rows) - 1:
                p.paragraph_format.keep_with_next = True
            run = p.add_run(val)
            _apply_run_font(run, fmt, size_pt=max(9, fmt.body_font_size_pt - 2))

    # Move table XML right after anchor_p
    anchor_p._p.addnext(tbl._tbl)
    # Create a compact trailing spacer paragraph to return a valid cursor without inflating height
    spacer = OxmlElement("w:p")
    tbl._tbl.addnext(spacer)
    spacer_p = doc.paragraphs[0].__class__(spacer, anchor_p._parent)
    spacer_p.paragraph_format.space_before = Pt(0)
    spacer_p.paragraph_format.space_after = Pt(4)
    spacer_p.paragraph_format.line_spacing = 1.0
    return spacer_p


def _set_cell_margins(cell, top: int = 60, bottom: int = 60, left: int = 100, right: int = 100) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    for old in list(tcPr.findall(qn("w:tcMar"))):
        tcPr.remove(old)
    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top}" w:type="dxa"/>'
        f'<w:left w:w="{left}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'<w:right w:w="{right}" w:type="dxa"/>'
        "</w:tcMar>"
    )
    tcPr.append(tcMar)


def _set_cell_borders(cell, color: str = "475569", sz: str = "6") -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    for old in list(tcPr.findall(qn("w:tcBorders"))):
        tcPr.remove(old)
    tcBorders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:left w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:bottom w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:right w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        "</w:tcBorders>"
    )
    tcPr.append(tcBorders)


def _set_table_borderless(tbl) -> None:
    tblPr = tbl._tbl.tblPr
    for old in list(tblPr.findall(qn("w:tblBorders"))):
        tblPr.remove(old)
    tblPr.append(
        parse_xml(
            f'<w:tblBorders {nsdecls("w")}>'
            '<w:top w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:left w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:bottom w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:insideH w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            "</w:tblBorders>"
        )
    )


def _ensure_tc_ends_with_p(cell) -> None:
    """OOXML requires every <w:tc> to end with a <w:p> element after nested <w:tbl>."""
    if len(cell._tc) == 0 or not cell._tc[-1].tag.endswith("}p"):
        trailing_p = parse_xml(
            f'<w:p {nsdecls("w")}>'
            '<w:pPr><w:spacing w:before="0" w:after="0" w:line="40" w:lineRule="exact"/></w:pPr>'
            "</w:p>"
        )
        cell._tc.append(trailing_p)


def _append_inline_checkbox(
    p,
    label_text: str,
    cb_png_bytes: bytes,
    fmt: FormattingRules,
    size_pt: float = 9.5,
) -> None:
    r_img = p.add_run()
    r_img.add_picture(io.BytesIO(cb_png_bytes), width=Cm(0.36), height=Cm(0.36))
    r_lbl = p.add_run(f" {label_text}    ")
    _apply_run_font(r_lbl, fmt, size_pt=size_pt, bold=False, color_rgb=RGBColor(30, 41, 59))


def _append_fill_box_in_cell(
    doc: Document,
    cell,
    fmt: FormattingRules,
    width_cm: float = 4.4,
    height_cm: float = 0.56,
    unit_label: str = "",
    prefix_label: str = "",
) -> None:
    if prefix_label:
        p_pre = cell.add_paragraph() if (len(cell.paragraphs) > 1 or cell.paragraphs[0].text or len(cell.paragraphs[0].runs) > 0) else cell.paragraphs[0]
        p_pre.paragraph_format.space_before = Pt(3)
        p_pre.paragraph_format.space_after = Pt(2)
        rp = p_pre.add_run(f"▸ {prefix_label} :")
        _apply_run_font(rp, fmt, size_pt=8.5, italic=True, color_rgb=RGBColor(71, 85, 105))

    cols = 2 if unit_label else 1
    sub_tbl = doc.add_table(rows=1, cols=cols)
    sub_tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    sub_tbl.autofit = False
    _set_table_borderless(sub_tbl)
    row = sub_tbl.rows[0]
    row.height = Cm(height_cm)
    trPr = row._tr.get_or_add_trPr()
    trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

    box_cell = row.cells[0]
    box_cell.width = Cm(width_cm)
    _set_cell_shading(box_cell, "F8FAFC")
    _set_cell_borders(box_cell, color="475569", sz="8")
    _set_cell_margins(box_cell, top=40, bottom=40, left=80, right=80)
    bp = box_cell.paragraphs[0]
    bp.paragraph_format.space_before = Pt(0)
    bp.paragraph_format.space_after = Pt(0)
    br = bp.add_run(" ")
    _apply_run_font(br, fmt, size_pt=9.5, color_rgb=RGBColor(100, 116, 139))

    if unit_label:
        u_cell = row.cells[1]
        u_cell.width = Cm(3.2)
        _set_cell_margins(u_cell, top=40, bottom=40, left=100, right=40)
        up = u_cell.paragraphs[0]
        up.paragraph_format.space_before = Pt(0)
        up.paragraph_format.space_after = Pt(0)
        ur = up.add_run(unit_label)
        _apply_run_font(ur, fmt, size_pt=9.5, bold=True, italic=True, color_rgb=RGBColor(71, 85, 105))

    # Remove empty initial placeholder paragraph if completely unused before inserting sub_tbl
    cell._tc.append(sub_tbl._tbl)
    _ensure_tc_ends_with_p(cell)


def _append_segmented_boxes_in_cell(
    doc: Document,
    cell,
    segments: List[str],
    fmt: FormattingRules,
    box_w_cm: float = 0.44,
    height_cm: float = 0.50,
) -> None:
    sub_tbl = doc.add_table(rows=1, cols=len(segments))
    sub_tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    sub_tbl.autofit = False
    _set_table_borderless(sub_tbl)
    row = sub_tbl.rows[0]
    row.height = Cm(height_cm)
    trPr = row._tr.get_or_add_trPr()
    trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

    for idx, seg in enumerate(segments):
        sc = row.cells[idx]
        p = sc.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        if seg in ("/", "-"):
            sc.width = Cm(0.28)
            _set_cell_margins(sc, top=20, bottom=20, left=10, right=10)
            r = p.add_run(seg)
            _apply_run_font(r, fmt, size_pt=9.5, bold=True, color_rgb=RGBColor(71, 85, 105))
        else:
            sc.width = Cm(box_w_cm)
            _set_cell_shading(sc, "F8FAFC")
            _set_cell_borders(sc, color="475569", sz="8")
            _set_cell_margins(sc, top=30, bottom=30, left=20, right=20)
            is_fixed = seg.isdigit()
            r = p.add_run(seg if seg.strip() else " ")
            _apply_run_font(
                r,
                fmt,
                size_pt=8.5,
                bold=is_fixed,
                color_rgb=RGBColor(30, 41, 59) if is_fixed else RGBColor(148, 163, 184),
            )

    cell._tc.append(sub_tbl._tbl)
    _ensure_tc_ends_with_p(cell)


def _render_crf_form_in_docx(
    doc: Document,
    anchor_p,
    ann: Dict[str, Any],
    fmt: FormattingRules,
):
    """
    Render Annexe 1 (Fiche de collecte des données) as a graphical clinical form
    with drawn square checkboxes (for dichotomous / choice fields) and drawn
    bordered rectangular fill-in / segmented boxes (for numbers, dates & text).
    """
    cb_png = get_crf_checkbox_png_bytes(checked=False)
    cursor = anchor_p

    # 1. Official FMSB CRF Header Box
    hdr_tbl = doc.add_table(rows=4, cols=2)
    hdr_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr_tbl.autofit = False
    hdr_tbl._tbl.tblPr.append(
        parse_xml(
            f'<w:tblBorders {nsdecls("w")}>'
            '<w:top w:val="single" w:sz="12" w:space="0" w:color="6C2826"/>'
            '<w:left w:val="single" w:sz="12" w:space="0" w:color="6C2826"/>'
            '<w:bottom w:val="single" w:sz="12" w:space="0" w:color="6C2826"/>'
            '<w:right w:val="single" w:sz="12" w:space="0" w:color="6C2826"/>'
            '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
            '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
            "</w:tblBorders>"
        )
    )
    for r in hdr_tbl.rows:
        r._tr.get_or_add_trPr().append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

    # Banner row
    banner_cell = hdr_tbl.rows[0].cells[0].merge(hdr_tbl.rows[0].cells[1])
    banner_cell.width = Cm(16.0)
    _set_cell_shading(banner_cell, "FDF8F8")
    _set_cell_margins(banner_cell, top=90, bottom=90, left=120, right=120)
    bp = banner_cell.paragraphs[0]
    bp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    bp.paragraph_format.space_before = Pt(0)
    bp.paragraph_format.space_after = Pt(2)
    r_btitle = bp.add_run("FICHE DE COLLECTE DES DONNÉES — ANONYME ET CONFIDENTIELLE\n")
    _apply_run_font(r_btitle, fmt, size_pt=10.5, bold=True, color_rgb=RGBColor(108, 40, 38))
    study_title = str(ann.get("study_title") or "").strip()
    if study_title:
        r_st = bp.add_run(f"Étude : {study_title}\n")
        _apply_run_font(r_st, fmt, size_pt=9.0, bold=False, color_rgb=RGBColor(30, 41, 59))
    r_inst = bp.add_run(
        "Consigne : Cocher la case dessinée correspondante (☐) ou inscrire lisiblement la valeur dans le cadre prévu."
    )
    _apply_run_font(r_inst, fmt, size_pt=8.5, italic=True, color_rgb=RGBColor(100, 116, 139))

    # Row 1: Code participant | Date de collecte
    c_code = hdr_tbl.rows[1].cells[0]
    c_code.width = Cm(8.0)
    _set_cell_margins(c_code, top=70, bottom=70, left=100, right=100)
    p_code = c_code.paragraphs[0]
    p_code.paragraph_format.space_before = Pt(0)
    p_code.paragraph_format.space_after = Pt(3)
    rc = p_code.add_run("Code participant :")
    _apply_run_font(rc, fmt, size_pt=9.5, bold=True, color_rgb=RGBColor(108, 40, 38))
    _append_segmented_boxes_in_cell(doc, c_code, [" ", " ", " ", "-", " ", " ", " "], fmt, box_w_cm=0.45)

    c_date = hdr_tbl.rows[1].cells[1]
    c_date.width = Cm(8.0)
    _set_cell_margins(c_date, top=70, bottom=70, left=100, right=100)
    p_date = c_date.paragraphs[0]
    p_date.paragraph_format.space_before = Pt(0)
    p_date.paragraph_format.space_after = Pt(3)
    rd = p_date.add_run("Date de collecte :")
    _apply_run_font(rd, fmt, size_pt=9.5, bold=True, color_rgb=RGBColor(108, 40, 38))
    _append_segmented_boxes_in_cell(doc, c_date, ["J", "J", "/", "M", "M", "/", "2", "0", "A", "A"], fmt, box_w_cm=0.42)

    # Row 2: Hospital sites with drawn checkboxes + Autre fill-in
    c_hosp = hdr_tbl.rows[2].cells[0].merge(hdr_tbl.rows[2].cells[1])
    c_hosp.width = Cm(16.0)
    _set_cell_margins(c_hosp, top=70, bottom=70, left=100, right=100)
    p_hosp = c_hosp.paragraphs[0]
    p_hosp.paragraph_format.space_before = Pt(0)
    p_hosp.paragraph_format.space_after = Pt(2)
    rh = p_hosp.add_run("Hôpital / Site d'inclusion :   ")
    _apply_run_font(rh, fmt, size_pt=9.5, bold=True, color_rgb=RGBColor(108, 40, 38))
    for site in ann.get("hospital_sites") or ["HGOPY", "HCBY", "HGY"]:
        _append_inline_checkbox(p_hosp, str(site), cb_png, fmt, size_pt=9.5)
    _append_inline_checkbox(p_hosp, "Autre (préciser) : _______________", cb_png, fmt, size_pt=9.5)

    # Row 3: Investigator
    c_inv = hdr_tbl.rows[3].cells[0].merge(hdr_tbl.rows[3].cells[1])
    c_inv.width = Cm(16.0)
    _set_cell_margins(c_inv, top=60, bottom=60, left=100, right=100)
    p_inv = c_inv.paragraphs[0]
    p_inv.paragraph_format.space_before = Pt(0)
    p_inv.paragraph_format.space_after = Pt(0)
    ri1 = p_inv.add_run("Nom de l'enquêteur : ")
    _apply_run_font(ri1, fmt, size_pt=9.5, bold=True, color_rgb=RGBColor(108, 40, 38))
    ri2 = p_inv.add_run(str(ann.get("author_label") or ""))
    _apply_run_font(ri2, fmt, size_pt=9.5, color_rgb=RGBColor(30, 41, 59))

    cursor._p.addnext(hdr_tbl._tbl)
    spacer = OxmlElement("w:p")
    hdr_tbl._tbl.addnext(spacer)
    cursor = doc.paragraphs[0].__class__(spacer, cursor._parent)
    cursor.paragraph_format.space_before = Pt(0)
    cursor.paragraph_format.space_after = Pt(4)

    # 2. Group CRF fields by section in order
    grouped_sections: List[Tuple[str, List[Dict[str, Any]]]] = []
    sec_map: Dict[str, List[Dict[str, Any]]] = {}
    for f in ann.get("crf_fields") or []:
        st = str(f.get("section_title") or "SECTION A : VARIABLES COLLECTÉES")
        if st not in sec_map:
            sec_map[st] = []
            grouped_sections.append((st, sec_map[st]))
        sec_map[st].append(f)

    # 3. Render one clean 2-column form table per section
    for sec_title, items in grouped_sections:
        tbl = doc.add_table(rows=1 + len(items), cols=2)
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        tbl.autofit = False
        _set_table_borders(tbl)

        # Header row (section bar)
        hdr_row = tbl.rows[0]
        hdr_trPr = hdr_row._tr.get_or_add_trPr()
        hdr_trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
        hdr_trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        sec_cell = hdr_row.cells[0].merge(hdr_row.cells[1])
        sec_cell.width = Cm(16.0)
        _set_cell_shading(sec_cell, FMSB_BORDEAUX)
        _set_cell_margins(sec_cell, top=75, bottom=75, left=110, right=110)
        sp = sec_cell.paragraphs[0]
        sp.paragraph_format.space_before = Pt(0)
        sp.paragraph_format.space_after = Pt(0)
        sp.paragraph_format.keep_with_next = True
        sr = sp.add_run(sec_title)
        _apply_run_font(sr, fmt, size_pt=10.0, bold=True, color_rgb=RGBColor(255, 255, 255))

        for r_idx, field in enumerate(items):
            row = tbl.rows[r_idx + 1]
            row._tr.get_or_add_trPr().append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
            bg_hex = "FFFFFF" if r_idx % 2 == 0 else "F8FAFC"

            # Left cell: Code + Question label
            q_cell = row.cells[0]
            q_cell.width = Cm(7.0)
            _set_cell_shading(q_cell, bg_hex)
            _set_cell_margins(q_cell, top=70, bottom=70, left=100, right=100)
            qp = q_cell.paragraphs[0]
            qp.paragraph_format.space_before = Pt(0)
            qp.paragraph_format.space_after = Pt(0)
            code = str(field.get("code") or "").strip()
            if code:
                rc = qp.add_run(f"{code}. ")
                _apply_run_font(rc, fmt, size_pt=9.5, bold=True, color_rgb=RGBColor(108, 40, 38))
            ql = qp.add_run(str(field.get("label") or ""))
            _apply_run_font(ql, fmt, size_pt=9.5, bold=False, color_rgb=RGBColor(15, 23, 42))

            # Right cell: Drawn graphical form widgets
            a_cell = row.cells[1]
            a_cell.width = Cm(9.0)
            _set_cell_shading(a_cell, bg_hex)
            _set_cell_margins(a_cell, top=70, bottom=70, left=100, right=100)

            ftype = str(field.get("field_type") or "text")
            opts = [str(o) for o in (field.get("options") or []) if str(o).strip()]
            unit = str(field.get("unit") or "").strip()
            sub_lbl = str(field.get("sub_field_label") or "").strip()

            ap = a_cell.paragraphs[0]
            ap.paragraph_format.space_before = Pt(0)
            ap.paragraph_format.space_after = Pt(2)

            if ftype == "dichotomous":
                for opt in opts or ["Oui", "Non"]:
                    _append_inline_checkbox(ap, opt, cb_png, fmt, size_pt=9.5)
                if sub_lbl:
                    _append_fill_box_in_cell(doc, a_cell, fmt, width_cm=5.2, height_cm=0.50, prefix_label=sub_lbl)
            elif ftype in ("single_choice", "multi_choice"):
                for opt in opts or ["Option 1", "Option 2"]:
                    # Put long option lists cleanly line by line or compact inline
                    if len(opts) >= 3 and sum(len(x) for x in opts) > 55:
                        p_opt = ap if (not ap.text and len(ap.runs) == 0) else a_cell.add_paragraph()
                        p_opt.paragraph_format.space_before = Pt(0)
                        p_opt.paragraph_format.space_after = Pt(2)
                        _append_inline_checkbox(p_opt, opt, cb_png, fmt, size_pt=9.0)
                    else:
                        _append_inline_checkbox(ap, opt, cb_png, fmt, size_pt=9.0)
                if sub_lbl:
                    _append_fill_box_in_cell(doc, a_cell, fmt, width_cm=5.2, height_cm=0.50, prefix_label=sub_lbl)
            elif ftype == "date":
                ap.paragraph_format.space_after = Pt(2)
                r_dt = ap.add_run("Date (JJ / MM / AAAA) :")
                _apply_run_font(r_dt, fmt, size_pt=8.0, italic=True, color_rgb=RGBColor(100, 116, 139))
                _append_segmented_boxes_in_cell(
                    doc, a_cell, ["J", "J", "/", "M", "M", "/", "2", "0", "A", "A"], fmt, box_w_cm=0.42
                )
            elif ftype == "number":
                ap.paragraph_format.space_after = Pt(0)
                r_lbl = ap.add_run("Valeur à inscrire :")
                _apply_run_font(r_lbl, fmt, size_pt=8.0, italic=True, color_rgb=RGBColor(100, 116, 139))
                _append_fill_box_in_cell(doc, a_cell, fmt, width_cm=4.2, height_cm=0.54, unit_label=unit)
                if sub_lbl:
                    _append_fill_box_in_cell(doc, a_cell, fmt, width_cm=4.6, height_cm=0.48, prefix_label=sub_lbl)
            else:
                r_lbl = ap.add_run("Réponse :")
                _apply_run_font(r_lbl, fmt, size_pt=8.0, italic=True, color_rgb=RGBColor(100, 116, 139))
                _append_fill_box_in_cell(doc, a_cell, fmt, width_cm=7.6, height_cm=0.58, unit_label=unit)
                if sub_lbl:
                    _append_fill_box_in_cell(doc, a_cell, fmt, width_cm=6.0, height_cm=0.48, prefix_label=sub_lbl)

        cursor._p.addnext(tbl._tbl)
        sp_el = OxmlElement("w:p")
        tbl._tbl.addnext(sp_el)
        cursor = doc.paragraphs[0].__class__(sp_el, cursor._parent)
        cursor.paragraph_format.space_before = Pt(0)
        cursor.paragraph_format.space_after = Pt(4)

    return cursor


_SECTPR_ORDER = [
    "headerReference",
    "footerReference",
    "footnotePr",
    "endnotePr",
    "type",
    "pgSz",
    "pgMar",
    "paperSrc",
    "pgBorders",
    "lnNumType",
    "pgNumType",
    "cols",
    "formProt",
    "vAlign",
    "noEndnote",
    "titlePg",
    "textDirection",
    "bidi",
    "rtlGutter",
    "docGrid",
    "printerSettings",
]


def _set_sect_child(sect_pr, local_name: str, new_el) -> None:
    """Replace or insert an OOXML child element inside <w:sectPr> in schema order."""
    tag_full = qn(f"w:{local_name}")
    for existing in list(sect_pr.findall(tag_full)):
        sect_pr.remove(existing)
    if new_el is None:
        return
    target_rank = _SECTPR_ORDER.index(local_name) if local_name in _SECTPR_ORDER else len(_SECTPR_ORDER)
    insert_idx = len(sect_pr)
    for idx, child in enumerate(list(sect_pr)):
        child_local = child.tag.split("}")[-1]
        if child_local in _SECTPR_ORDER and _SECTPR_ORDER.index(child_local) > target_rank:
            insert_idx = idx
            break
    sect_pr.insert(insert_idx, new_el)


def _strip_header_page_numbers(doc: Document) -> None:
    """Remove all page numbers and residual header content so only bottom footer numbering stays."""
    for sec in doc.sections:
        for hdr in (sec.header, sec.first_page_header, sec.even_page_header):
            try:
                hdr.is_linked_to_previous = False
            except Exception:
                pass
            hdr_el = hdr._element
            for child in list(hdr_el):
                hdr_el.remove(child)
            hdr_el.append(OxmlElement("w:p"))
        sect_pr = sec._sectPr
        for href in list(sect_pr.findall(qn("w:headerReference"))):
            sect_pr.remove(href)
    # Belt-and-suspenders: strip any headerReference inside body sectPr elements
    for sect_pr in doc.element.body.iter(qn("w:sectPr")):
        for href in list(sect_pr.findall(qn("w:headerReference"))):
            sect_pr.remove(href)


def _configure_fmsb_pagination(doc: Document, intro_anchor_p, fmt: FormattingRules) -> None:
    """Split front matter (lowerRoman, hidden on cover) from body (Arabic starting at 1)."""
    if not doc.sections:
        return
    _strip_header_page_numbers(doc)
    base_sec = doc.sections[0]
    base_sec.different_first_page_header_footer = True

    footer = base_sec.footer
    fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    fp.text = ""
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fp.paragraph_format.space_before = Pt(0)
    fp.paragraph_format.space_after = Pt(0)
    _insert_field(fp, "PAGE")
    for run in fp.runs:
        _apply_run_font(run, fmt, size_pt=10)

    sect2_pr = base_sec._sectPr
    if intro_anchor_p is not None:
        intro_anchor_p.paragraph_format.page_break_before = False
        break_p = OxmlElement("w:p")
        intro_anchor_p._p.addprevious(break_p)
        pPr = OxmlElement("w:pPr")
        spacing = parse_xml(f'<w:spacing {nsdecls("w")} w:before="0" w:after="0"/>')
        pPr.append(spacing)

        sect1_pr = copy.deepcopy(sect2_pr)
        _set_sect_child(sect1_pr, "type", parse_xml(f'<w:type {nsdecls("w")} w:val="nextPage"/>'))
        _set_sect_child(
            sect1_pr,
            "pgNumType",
            parse_xml(f'<w:pgNumType {nsdecls("w")} w:fmt="lowerRoman" w:start="1"/>'),
        )
        pPr.append(sect1_pr)
        break_p.append(pPr)

        # Body section: remove cover border & titlePg so Introduction starts cleanly as page 1
        _set_sect_child(sect2_pr, "pgBorders", None)
        _set_sect_child(sect2_pr, "titlePg", None)
        _set_sect_child(sect2_pr, "type", parse_xml(f'<w:type {nsdecls("w")} w:val="nextPage"/>'))
        _set_sect_child(
            sect2_pr,
            "pgNumType",
            parse_xml(f'<w:pgNumType {nsdecls("w")} w:fmt="decimal" w:start="1"/>'),
        )
    else:
        _set_sect_child(
            sect2_pr,
            "pgNumType",
            parse_xml(f'<w:pgNumType {nsdecls("w")} w:fmt="decimal" w:start="1"/>'),
        )
    _strip_header_page_numbers(doc)


def compile_protocol_docx(project: ProtocolProject, output_path: Path) -> Path:
    """Deterministically compile a complete FMSB ProtocolProject into .docx."""
    from app.services.media_transport import (
        materialize_image_asset,
        synchronize_module1_to_module2_tables,
        transport_and_repair_project_images,
    )

    # Transport & materialize all downloaded/captured figures and unify Module 1 <-> Module 2 tables
    transport_and_repair_project_images(project)
    synchronize_module1_to_module2_tables(project)

    # Unify & deduplicate Abbreviations, Vancouver References, Tables List, and Figures List before Word compilation
    _, _, unified_abbrs = build_unified_abbreviations_list(project, mark_validated=False)
    build_and_validate_vancouver_references(project, use_gemini_decision=False, mark_validated=False)
    table_entries, figure_entries, pagination_plan = build_unified_tables_and_figures_lists(
        project, mark_validated=False, use_gemini_decision=True
    )

    master_path = ensure_master_template()
    doc = Document(str(master_path))
    fmt = project.formatting

    for section in doc.sections:
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(fmt.margin_cm)
        section.bottom_margin = Cm(fmt.margin_cm)
        section.left_margin = Cm(fmt.margin_cm)
        section.right_margin = Cm(fmt.margin_cm)

    cover = project.cover
    replacements = {
        "{{TITLE}}": cover.title or project.name,
        "{{DEGREE_LABEL}}": cover.degree_label or "Doctorat en Médecine",
        "{{DEPARTMENT_FR}}": cover.department_fr or "SANTE PUBLIQUE",
        "{{DEPARTMENT_EN}}": cover.department_en or "PUBLIC HEALTH",
        "{{AUTHOR_NAME}}": cover.author_name or "Noms & Prénoms",
        "{{MATRICULE}}": cover.matricule or "—",
        "{{DIRECTOR}}": cover.director or "À renseigner",
        "{{CO_DIRECTORS}}": "\n".join(cover.co_directors) if cover.co_directors else "À renseigner",
        "{{ACADEMIC_YEAR}}": cover.academic_year or "2025-2026",
    }

    _force_cover_title_size(doc.element.body, fmt.cover_title_max_pt or 18)
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                _force_cover_title_size(cell._tc, fmt.cover_title_max_pt or 18)
                _replace_text_in_element(cell._tc, replacements)
    _replace_text_in_element(
        doc.element.body,
        {
            **replacements,
            "Chapitre II\xa0: METHODLOGIE": "Chapitre III : METHODOLOGIE",
            "Chapitre II : METHODLOGIE": "Chapitre III : METHODOLOGIE",
            "METHODLOGIE": "METHODOLOGIE",
        },
    )
    _apply_template_border_styles(doc, fmt)
    configure_word_toc_styles(doc, fmt)

    chapter_map = [
        ("TABLE DE MATIERES", "toc"),
        ("ABREVIATIONS, ACRONYMES & SIGLES", "abbreviations"),
        ("INTRODUCTION", "introduction"),
        ("METHODOLOGIE", "chapitre_3"),
        ("METHODLOGIE", "chapitre_3"),
        ("Chapitre III", "chapitre_3"),
        ("REVUE DE LA LITTERATURE", "chapitre_2"),
        ("Chapitre II", "chapitre_2"),
        ("Chapitre I", "chapitre_1"),
        ("REFERENCES", "references"),
        ("ANNEXES", "annexes"),
    ]

    anchors = {}
    for p in doc.paragraphs:
        full_text = "".join(t.text for t in p._element.iter(f"{{{W_NS}}}t") if t.text)
        for marker, key in chapter_map:
            if marker in full_text and key not in anchors:
                anchors[key] = p
                break

    # Fix split-run "Chapitre II" -> "Chapitre III" inside chapitre_3 banner if Word split runs
    if "chapitre_3" in anchors:
        for t in anchors["chapitre_3"]._element.iter(f"{{{W_NS}}}t"):
            if t.text and "II" in t.text and "III" not in t.text:
                t.text = t.text.replace("II", "III")

    # Force clean page breaks before front-matter and major chapter boxes, and center grand titles on their own page
    for pb_key in ("toc", "abbreviations", "introduction", "chapitre_1", "chapitre_2", "chapitre_3", "references", "annexes"):
        if pb_key in anchors:
            anchors[pb_key].paragraph_format.page_break_before = True
    for ch_key in ("introduction", "chapitre_1", "chapitre_2", "chapitre_3", "references", "annexes"):
        if ch_key in anchors:
            _center_grand_chapter_box(anchors[ch_key], fmt)

    build_fmsb_cover_page(doc, project, fmt, anchors.get("toc"))

    def insert_page_break_after(anchor_p):
        new_p = OxmlElement("w:p")
        anchor_p._p.addnext(new_p)
        p_obj = doc.paragraphs[0].__class__(new_p, anchor_p._parent)
        run = p_obj.add_run()
        br = OxmlElement("w:br")
        br.set(qn("w:type"), "page")
        run._r.append(br)
        return p_obj

    def insert_after(
        anchor_p,
        text: str = "",
        bold: bool = False,
        italic: bool = False,
        size_pt: Optional[int] = None,
        align_override: Optional[WD_ALIGN_PARAGRAPH] = None,
    ):
        lines = [ln for ln in text.split("\n")] if text and "\n" in text else [text]
        cursor_p = anchor_p
        for line in lines:
            clean_line = line.strip("\r")
            if len(lines) > 1 and not clean_line.strip():
                continue
            new_p = OxmlElement("w:p")
            cursor_p._p.addnext(new_p)
            p_obj = doc.paragraphs[0].__class__(new_p, cursor_p._parent)
            _apply_paragraph_rules(p_obj, fmt, align_override=align_override)
            if clean_line:
                run = p_obj.add_run(clean_line)
                _apply_run_font(run, fmt, size_pt=size_pt, bold=bold, italic=italic)
            cursor_p = p_obj
        return cursor_p

    def insert_rich_content(
        anchor_p,
        html_str: str,
        markdown_str: str,
        emit_placeholder: bool = True,
        skip_duplicate_headings: Optional[List[str]] = None,
    ):
        cursor = anchor_p
        inserted_paras = []
        raw_html = html_str.strip() if html_str and html_str.strip() else ""
        raw_md = markdown_str.strip() if markdown_str and markdown_str.strip() else ""
        if raw_html:
            raw_html, _ = sanitize_llm_french_typography(raw_html)
        if raw_md:
            raw_md, _ = sanitize_llm_french_typography(raw_md)
        has_md_pipe_table = bool(re.search(r"(?m)^\s*\|[\s:\-|]+\|\s*$", raw_md))

        if has_md_pipe_table and "<table" not in raw_html.lower():
            effective_html = _markdown_with_tables_to_html(raw_md)
        elif raw_html:
            effective_html = raw_html
        elif raw_md:
            effective_html = _markdown_with_tables_to_html(raw_md)
        else:
            effective_html = ""

        if effective_html:
            parser = _SimpleHTMLRunsParser()
            parser.feed(effective_html)
            parsed_items = parser.finalize()
            for kind, payload in parsed_items:
                if kind == "table":
                    headers, data_rows = payload
                    cursor = _insert_table_after(doc, cursor, headers, data_rows, fmt)
                    continue

                is_bullet, heading_level, is_center, runs = payload
                is_heading = heading_level > 0
                if is_heading and skip_duplicate_headings:
                    heading_txt = "".join(r[0] for r in runs).strip()
                    if is_duplicate_heading(heading_txt, skip_duplicate_headings):
                        continue

                split_groups: List[List[Tuple[str, bool, bool, bool, bool, bool]]] = [[]]
                for txt, b, it, u, sup, sub in runs:
                    parts = txt.split("\n")
                    for idx_part, part in enumerate(parts):
                        if part:
                            split_groups[-1].append((part, b, it, u, sup, sub))
                        if idx_part < len(parts) - 1:
                            split_groups.append([])
                for grp in split_groups:
                    if not any(r[0].strip() for r in grp):
                        continue
                    new_p = OxmlElement("w:p")
                    cursor._p.addnext(new_p)
                    p_obj = doc.paragraphs[0].__class__(new_p, cursor._parent)
                    align_ov = (
                        WD_ALIGN_PARAGRAPH.CENTER
                        if is_center
                        else (WD_ALIGN_PARAGRAPH.LEFT if is_heading else None)
                    )
                    _apply_paragraph_rules(p_obj, fmt, align_override=align_ov)
                    if is_heading and int(getattr(fmt, "toc_depth", 2) or 2) >= 3:
                        _set_paragraph_outline(p_obj, level_zero_based=2)
                    if is_bullet:
                        r_bul = p_obj.add_run("• ")
                        _apply_run_font(r_bul, fmt)
                    sz_override = (
                        fmt.body_font_size_pt + 1
                        if heading_level in (1, 2)
                        else (fmt.body_font_size_pt if is_heading else None)
                    )
                    for txt, b, it, u, sup, sub in grp:
                        # Fallback clean any residual $...$ math expressions
                        if "$" in txt:
                            for seg_kind, seg_val in split_math_segments(txt):
                                out_val = latex_to_plain_math(seg_val) if seg_kind != "text" else seg_val
                                r = p_obj.add_run(out_val)
                                _apply_run_font(
                                    r,
                                    fmt,
                                    size_pt=sz_override,
                                    bold=b or is_heading,
                                    italic=(it or seg_kind != "text"),
                                    underline=u,
                                )
                                r.font.superscript = sup
                                r.font.subscript = sub
                        else:
                            r = p_obj.add_run(txt)
                            _apply_run_font(
                                r,
                                fmt,
                                size_pt=sz_override,
                                bold=b or is_heading,
                                italic=it,
                                underline=u,
                            )
                            r.font.superscript = sup
                            r.font.subscript = sub
                    cursor = p_obj
                    inserted_paras.append(p_obj)
            return cursor, inserted_paras

        if emit_placeholder:
            cursor = insert_after(cursor, "[Section en attente de rédaction / validation]", italic=True)
            inserted_paras.append(cursor)
        return cursor, inserted_paras

    # Track table & figure numbering sequentially
    table_counter = 1
    figure_counter = 1

    def render_custom_tables(sec_state, base_cursor, section_paras=None):
        """Render any CustomTable items attached to a section and return updated cursor."""
        nonlocal table_counter
        cursor_out = base_cursor
        if not sec_state or not getattr(sec_state, "custom_tables", None):
            return cursor_out
        paras = section_paras or []
        for ct in sec_state.custom_tables:
            if not ct.headers and not ct.rows:
                continue
            target_c = cursor_out
            advance_main = True
            clean_hint = (ct.anchor_hint or "").strip().lower()
            if clean_hint:
                for sp in paras:
                    if clean_hint in (sp.text or "").lower():
                        target_c = sp
                        advance_main = False
                        break
            c_after = _insert_caption_with_seq(
                doc,
                target_c,
                seq_label="Tableau",
                num_val=table_counter,
                separator=" : ",
                title_text=ct.title,
                fmt=fmt,
                size_pt=11,
                bold=True,
                align=WD_ALIGN_PARAGRAPH.LEFT,
            )
            table_counter += 1
            c_after = _insert_table_after(doc, c_after, ct.headers, ct.rows, fmt)
            if advance_main:
                cursor_out = c_after
        return cursor_out

    def render_custom_diagrams(sec_state, base_cursor, section_paras=None):
        """Render any CustomDiagram items attached to a section (>= 12pt legibility) and return cursor."""
        nonlocal figure_counter
        cursor_out = base_cursor
        if not sec_state or not getattr(sec_state, "custom_diagrams", None):
            return cursor_out
        paras = section_paras or []
        diag_dir = get_settings().storage_dir / "tmp_diagrams" / project.project_id
        for dg in sec_state.custom_diagrams:
            if not dg.nodes:
                continue
            target_c = cursor_out
            advance_main = True
            clean_hint = (dg.anchor_hint or "").strip().lower()
            if clean_hint:
                for sp in paras:
                    if clean_hint in (sp.text or "").lower():
                        target_c = sp
                        advance_main = False
                        break
            c_dg = target_c
            png_path = diag_dir / f"{dg.diagram_id}.png"
            clean_dg_title = clean_caption_title(dg.title, "Figure") or dg.title
            try:
                render_diagram_png(dg, png_path)
                fig_sz = compute_minimum_legible_figure_size_cm(
                    diagram=dg,
                    image_path=png_path,
                    rules=fmt,
                    min_font_pt=12.0,
                )
                p_dg = insert_after(c_dg, "", align_override=WD_ALIGN_PARAGRAPH.CENTER)
                p_dg.paragraph_format.keep_with_next = True
                p_dg.paragraph_format.keep_together = True
                run_dg = p_dg.add_run()
                run_dg.add_picture(str(png_path), width=Cm(float(fig_sz["width_cm"])))
                c_dg = p_dg
            except Exception:
                c_dg = insert_after(
                    c_dg,
                    f"[Schéma Figure {figure_counter} : {clean_dg_title}]",
                    italic=True,
                    size_pt=10,
                    align_override=WD_ALIGN_PARAGRAPH.CENTER,
                )
                c_dg.paragraph_format.keep_with_next = True
            c_dg = _insert_caption_with_seq(
                doc,
                c_dg,
                seq_label="Figure",
                num_val=figure_counter,
                separator=". ",
                title_text=clean_dg_title,
                fmt=fmt,
                size_pt=9,
                bold=True,
                italic=False,
                align=WD_ALIGN_PARAGRAPH.CENTER,
                keep_with_next_override=bool(dg.caption and dg.caption.strip()),
            )
            if dg.caption and dg.caption.strip():
                c_dg = insert_after(
                    c_dg,
                    dg.caption.strip(),
                    italic=True,
                    size_pt=8,
                    align_override=WD_ALIGN_PARAGRAPH.CENTER,
                )
            if advance_main:
                cursor_out = c_dg
            figure_counter += 1
        return cursor_out

    # Front matter: TOC -> Abbreviations -> List of Tables -> List of Figures (FMSB order)
    if "toc" in anchors:
        cursor = anchors["toc"]
        toc_sec = project.sections.get("toc")
        if fmt.auto_toc:
            cursor, _ = insert_word_automatic_toc(
                doc, cursor, project, fmt, pagination_plan=pagination_plan
            )
        elif toc_sec and (toc_sec.content_html.strip() or toc_sec.content_markdown.strip()):
            clean_toc_md = re.sub(
                r"(?im)^#{1,6}\s+table\s+des\s+mati[èe]res[^\n]*\n*",
                "",
                toc_sec.content_markdown or "",
            ).strip()
            clean_toc_html = re.sub(
                r"(?is)<h[1-4][^>]*>\s*(?:<strong>)?\s*table\s+des\s+mati[èe]res.*?</h[1-4]>",
                "",
                toc_sec.content_html or "",
            ).strip()
            if clean_toc_html or clean_toc_md:
                cursor, _ = insert_rich_content(
                    cursor,
                    clean_toc_html,
                    clean_toc_md,
                    emit_placeholder=False,
                )

    # Abbreviations table (SINGLE unified deduplicated 2-column A-Z table, NO SEQ Tableau caption, NO page numbers),
    # then paginated List of Tables & List of Figures (each starting on a new page)
    if "abbreviations" in anchors:
        cursor = anchors["abbreviations"]
        if fmt.auto_abbreviations_table:
            _set_paragraph_outline(cursor, 0)

        if unified_abbrs and fmt.auto_abbreviations_table:
            rows = [[a.acronym, a.full_form] for a in unified_abbrs]
            cursor = _insert_table_after(
                doc,
                cursor,
                ["Sigle / Acronyme", "Signification complète"],
                rows,
                fmt,
            )
        else:
            cursor = insert_after(
                cursor,
                "Aucune abréviation enregistrée.",
                italic=True,
                align_override=WD_ALIGN_PARAGRAPH.LEFT,
            )

        if fmt.auto_list_of_tables:
            cursor = insert_after(cursor, "LISTE DES TABLEAUX", bold=True, size_pt=14, align_override=WD_ALIGN_PARAGRAPH.LEFT)
            cursor.paragraph_format.page_break_before = True
            _set_paragraph_outline(cursor, 0)
            cursor = insert_word_automatic_caption_list(doc, cursor, "Tableau", table_entries, fmt)

        if fmt.auto_list_of_figures:
            cursor = insert_after(cursor, "LISTE DES FIGURES", bold=True, size_pt=14, align_override=WD_ALIGN_PARAGRAPH.LEFT)
            cursor.paragraph_format.page_break_before = True
            _set_paragraph_outline(cursor, 0)
            cursor = insert_word_automatic_caption_list(doc, cursor, "Figure", figure_entries, fmt)

    # Chapters
    sections_by_chapter: Dict[str, List] = {}
    for spec in FMSB_SECTIONS:
        if spec.chapter_id in ("preliminaires", "references", "annexes"):
            continue
        sections_by_chapter.setdefault(spec.chapter_id, []).append(spec)

    approved_images_by_section: Dict[str, List] = {}
    for img in project.images:
        if img.approved:
            approved_images_by_section.setdefault(img.target_section_id, []).append(img)

    for chapter_id, specs in sections_by_chapter.items():
        if chapter_id not in anchors:
            continue
        ch_toc_label = FMSB_CHAPTER_TITLES.get(chapter_id, chapter_id.upper())
        _set_paragraph_outline(anchors[chapter_id], 0, invisible_toc_label=ch_toc_label)
        # Keep the framed grand chapter title alone on its own full page
        cursor = insert_page_break_after(anchors[chapter_id])
        for spec in specs:
            sec_state = project.sections.get(spec.id)
            cursor = insert_after(
                cursor,
                spec.title,
                bold=True,
                size_pt=fmt.body_font_size_pt + 1,
                align_override=WD_ALIGN_PARAGRAPH.LEFT,
            )
            _set_paragraph_outline(cursor, 1)
            cursor, section_paras = insert_rich_content(
                cursor,
                sec_state.content_html if sec_state else "",
                sec_state.content_markdown if sec_state else "",
                skip_duplicate_headings=[spec.title, spec.chapter_title, ch_toc_label],
            )

            def _resolve_hint(hint: str, fallback_cursor):
                clean = (hint or "").strip().lower()
                if clean:
                    for sp in section_paras:
                        if clean in (sp.text or "").lower():
                            return sp, False
                return fallback_cursor, True

            # Render custom section tables (manual or AI-suggested)
            cursor = render_custom_tables(sec_state, cursor, section_paras)

            # Render structured tables belonging to this section ONLY when no custom table replaces it
            # and the user has not deleted/suppressed the section's structured table in Module 1.
            _has_custom_tbl = bool(
                sec_state
                and any(t.headers or t.rows for t in (getattr(sec_state, "custom_tables", None) or []))
            )
            _suppressed_structured = bool(
                sec_state and getattr(sec_state, "suppress_structured_table", False)
            )

            if spec.id == "chap1_variables" and project.variables and not _has_custom_tbl and not _suppressed_structured:
                cursor = _insert_caption_with_seq(
                    doc,
                    cursor,
                    seq_label="Tableau",
                    num_val=table_counter,
                    separator=" : ",
                    title_text="Liste opérationnelle des variables d'étude",
                    fmt=fmt,
                    size_pt=11,
                    bold=True,
                    align=WD_ALIGN_PARAGRAPH.LEFT,
                )
                table_counter += 1
                var_rows = [
                    [v.name, v.role, v.definition or "—", v.measurement_scale or "—", v.collection_tool or "—"]
                    for v in project.variables
                ]
                cursor = _insert_table_after(
                    doc,
                    cursor,
                    ["Variable", "Rôle", "Définition", "Échelle", "Outil"],
                    var_rows,
                    fmt,
                )

            if spec.id == "chap2_etat_connaissances" and project.literature_synthesis and not _has_custom_tbl and not _suppressed_structured:
                cursor = _insert_caption_with_seq(
                    doc,
                    cursor,
                    seq_label="Tableau",
                    num_val=table_counter,
                    separator=" : ",
                    title_text="Synthèse des études antérieures",
                    fmt=fmt,
                    size_pt=11,
                    bold=True,
                    align=WD_ALIGN_PARAGRAPH.LEFT,
                )
                table_counter += 1
                syn_rows = [
                    [s.authors_country_year, s.study_design, s.population, s.main_results, s.limits or "—"]
                    for s in project.literature_synthesis
                ]
                cursor = _insert_table_after(
                    doc,
                    cursor,
                    ["Auteur(s), Pays (Année)", "Devis", "Population", "Résultats principaux", "Limites"],
                    syn_rows,
                    fmt,
                )

            if spec.id == "chap3_chronogramme" and project.timeline_grid.rows and not _has_custom_tbl and not _suppressed_structured:
                cursor = _insert_caption_with_seq(
                    doc,
                    cursor,
                    seq_label="Tableau",
                    num_val=table_counter,
                    separator=" : ",
                    title_text="Chronogramme des activités",
                    fmt=fmt,
                    size_pt=11,
                    bold=True,
                    align=WD_ALIGN_PARAGRAPH.LEFT,
                )
                table_counter += 1
                periods = project.timeline_grid.periods
                headers = ["Activité"] + periods
                gantt_rows: List[List[str]] = []
                colored: Dict[Tuple[int, int], str] = {}
                for ri, row in enumerate(project.timeline_grid.rows):
                    vals = [row.activity]
                    active_set = set(row.active_periods)
                    for pi, per in enumerate(periods, start=1):
                        if per in active_set:
                            vals.append("■")
                            colored[(ri, pi)] = FMSB_GANTT_FILL
                        else:
                            vals.append("")
                    gantt_rows.append(vals)
                cursor = _insert_table_after(doc, cursor, headers, gantt_rows, fmt, colored_cells=colored)

            if spec.id == "chap3_budget" and project.budget and not _has_custom_tbl and not _suppressed_structured:
                cursor = _insert_caption_with_seq(
                    doc,
                    cursor,
                    seq_label="Tableau",
                    num_val=table_counter,
                    separator=" : ",
                    title_text="Budget prévisionnel de l'étude",
                    fmt=fmt,
                    size_pt=11,
                    bold=True,
                    align=WD_ALIGN_PARAGRAPH.LEFT,
                )
                table_counter += 1
                budget_rows: List[List[str]] = []
                grand_total = 0.0
                for b in project.budget:
                    line_total = b.total_fcfa if b.total_fcfa > 0 else b.quantity * b.unit_cost_fcfa
                    grand_total += line_total
                    budget_rows.append(
                        [
                            b.designation,
                            f"{int(b.unit_cost_fcfa):,}".replace(",", " "),
                            f"{b.quantity:g}",
                            f"{int(line_total):,}".replace(",", " "),
                        ]
                    )
                budget_rows.append(["TOTAL GÉNÉRAL", "", "", f"{int(grand_total):,} FCFA".replace(",", " ")])
                cursor = _insert_table_after(
                    doc,
                    cursor,
                    ["Désignation", "Prix unitaire (FCFA)", "Quantité", "Prix total (FCFA)"],
                    budget_rows,
                    fmt,
                    colored_cells={(len(budget_rows) - 1, c): "EFE6E6" for c in range(4)},
                )

            # Render centered approved figures (>= 12pt minimum legibility) + 9pt centered caption locked together
            for img in approved_images_by_section.get(spec.id, []):
                if not getattr(img, "local_path", None) or not Path(img.local_path).exists():
                    materialize_image_asset(project, img)
                target_c, advance_main = _resolve_hint(getattr(img, "anchor_hint", ""), cursor)
                c_img = target_c
                img_inserted = False
                if getattr(img, "local_path", None) and Path(img.local_path).exists():
                    try:
                        img_sz = compute_minimum_legible_figure_size_cm(
                            diagram=None,
                            image_path=Path(img.local_path),
                            rules=fmt,
                            min_font_pt=12.0,
                        )
                        target_w_cm = min(
                            float(img_sz["usable_width_cm"]),
                            max(9.0, float(img.width_cm or img_sz["width_cm"] or 13.0)),
                        )
                        # Clamp height to <= 11.5 cm so image + caption stay together on A4
                        from PIL import Image as _PILImage

                        with _PILImage.open(img.local_path) as _im:
                            pw, ph = _im.size
                        aspect = ph / max(pw, 1)
                        if target_w_cm * aspect > 11.5:
                            target_w_cm = round(max(7.5, 11.5 / aspect), 2)

                        p_img = insert_after(c_img, "", align_override=WD_ALIGN_PARAGRAPH.CENTER)
                        p_img.paragraph_format.space_before = Pt(8)
                        p_img.paragraph_format.space_after = Pt(3)
                        p_img.paragraph_format.keep_with_next = True
                        p_img.paragraph_format.keep_together = True
                        run_img = p_img.add_run()
                        run_img.add_picture(str(img.local_path), width=Cm(target_w_cm))
                        c_img = p_img
                        img_inserted = True
                    except Exception:
                        img_inserted = False
                if not img_inserted and img.source_url and img.source_url.lower().endswith((".png", ".jpg", ".jpeg")):
                    try:
                        with httpx.Client(timeout=6.0) as client:
                            r = client.get(img.source_url)
                            if r.status_code == 200 and len(r.content) > 500:
                                usable_w = max(8.0, 21.0 - 2.0 * float(fmt.margin_cm or 2.5))
                                p_img = insert_after(c_img, "", align_override=WD_ALIGN_PARAGRAPH.CENTER)
                                p_img.paragraph_format.keep_with_next = True
                                p_img.paragraph_format.keep_together = True
                                run_img = p_img.add_run()
                                run_img.add_picture(io.BytesIO(r.content), width=Cm(min(usable_w, max(12.0, img.width_cm))))
                                c_img = p_img
                                img_inserted = True
                    except Exception:
                        img_inserted = False

                if not img_inserted:
                    c_img = insert_after(
                        c_img,
                        f"[Emplacement Figure {figure_counter} : {img.title} — Source : {img.source_url or 'Locale'}]",
                        italic=True,
                        size_pt=10,
                        align_override=WD_ALIGN_PARAGRAPH.CENTER,
                    )
                    c_img.paragraph_format.keep_with_next = True

                clean_img_title = clean_caption_title(img.title, "Figure") or img.title
                c_img = _insert_caption_with_seq(
                    doc,
                    c_img,
                    seq_label="Figure",
                    num_val=figure_counter,
                    separator=". ",
                    title_text=clean_img_title,
                    fmt=fmt,
                    size_pt=9,
                    bold=True,
                    italic=False,
                    align=WD_ALIGN_PARAGRAPH.CENTER,
                    keep_with_next_override=bool(img.caption and img.caption.strip()),
                )
                if img.caption and img.caption.strip():
                    c_img = insert_after(
                        c_img,
                        img.caption.strip(),
                        italic=True,
                        size_pt=8,
                        align_override=WD_ALIGN_PARAGRAPH.CENTER,
                    )
                if advance_main:
                    cursor = c_img
                figure_counter += 1

            # Render structured vector diagrams (high-DPI PNG sized for >= 12pt minimum character legibility)
            cursor = render_custom_diagrams(sec_state, cursor, section_paras)

    # Vancouver References (grand title alone on its page -> content starts next page)
    if "references" in anchors:
        _set_paragraph_outline(
            anchors["references"],
            0,
            invisible_toc_label=FMSB_CHAPTER_TITLES["references"],
        )
        cursor = insert_page_break_after(anchors["references"])
        cursor = insert_after(
            cursor,
            "Bibliographie (Système Vancouver)",
            bold=True,
            size_pt=fmt.body_font_size_pt + 1,
            align_override=WD_ALIGN_PARAGRAPH.LEFT,
        )
        _set_paragraph_outline(cursor, 1)
        ref_sec = project.sections.get("references")
        ref_paras = []
        if project.references:
            for idx, ref in enumerate(project.references, start=1):
                line = (
                    ref.vancouver_citation
                    or f"{', '.join(ref.authors)}. {ref.title}. {ref.journal}. {ref.year or ''}."
                )
                line = re.sub(r"^\s*(?:\[\d+\]|\d+\s*[.)\]])\s*", "", line).strip()
                p_ref = insert_after(
                    cursor,
                    f"{idx}. {line}",
                    align_override=WD_ALIGN_PARAGRAPH.JUSTIFY
                    if fmt.text_align == "justify"
                    else WD_ALIGN_PARAGRAPH.LEFT,
                )
                p_ref.paragraph_format.left_indent = Cm(0.85)
                p_ref.paragraph_format.first_line_indent = Cm(-0.85)
                p_ref.paragraph_format.space_before = Pt(0)
                p_ref.paragraph_format.space_after = Pt(4)
                p_ref.paragraph_format.keep_together = True
                cursor = p_ref
                ref_paras.append(cursor)
        elif ref_sec and (ref_sec.content_html.strip() or ref_sec.content_markdown.strip()):
            cursor, ref_paras = insert_rich_content(
                cursor,
                ref_sec.content_html,
                ref_sec.content_markdown,
                skip_duplicate_headings=["Bibliographie (Système Vancouver)", "REFERENCES", "Références"],
            )
        elif not (ref_sec and getattr(ref_sec, "custom_tables", None)):
            insert_after(cursor, "Aucune référence bibliographique enregistrée.", italic=True, align_override=WD_ALIGN_PARAGRAPH.LEFT)
        cursor = render_custom_tables(ref_sec, cursor, ref_paras)

    # FMSB Annexes Pack (grand title alone on its page; each Annexe starts on a new page)
    if "annexes" in anchors:
        _set_paragraph_outline(
            anchors["annexes"],
            0,
            invisible_toc_label=FMSB_CHAPTER_TITLES["annexes"],
        )
        cursor = insert_page_break_after(anchors["annexes"])
        ann_sec = project.sections.get("annexes")
        annexes = build_annexes_pack(project)
        left_prefixes = (
            "à monsieur",
            "objet :",
            "pièces jointes",
            "fait à",
            "signature",
            "nom :",
            "noms :",
            "téléphone",
            "email",
            "investigateur",
            "directeur",
            "1.",
            "2.",
            "3.",
            "4.",
            "5.",
            "6.",
            "•",
            "- ",
        )
        for idx_ann, ann in enumerate(annexes):
            cursor = insert_after(cursor, ann["title"], bold=True, size_pt=13, align_override=WD_ALIGN_PARAGRAPH.LEFT)
            if idx_ann > 0:
                cursor.paragraph_format.page_break_before = True
            _set_paragraph_outline(cursor, 1)
            if ann.get("kind") == "data_collection":
                cursor = _render_crf_form_in_docx(doc, cursor, ann, fmt)
            else:
                for p_txt in ann["paragraphs"]:
                    stripped = p_txt.strip()
                    lower_s = stripped.lower()
                    is_short_or_form = (
                        len(stripped) < 75
                        or "..." in stripped
                        or "___" in stripped
                        or lower_s.startswith(left_prefixes)
                    )
                    align = WD_ALIGN_PARAGRAPH.LEFT if is_short_or_form else WD_ALIGN_PARAGRAPH.JUSTIFY
                    cursor = insert_after(cursor, p_txt, align_override=align)
        cursor = render_custom_tables(ann_sec, cursor)
        cursor = render_custom_diagrams(ann_sec, cursor)

    _configure_fmsb_pagination(doc, anchors.get("introduction"), fmt)
    apply_human_author_metadata_docx(doc, project)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path
