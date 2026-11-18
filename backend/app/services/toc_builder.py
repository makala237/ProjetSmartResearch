from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt, RGBColor
from pydantic import BaseModel

from app.schemas.protocol import FormattingRules, ProtocolProject
from app.services.page_layout_engine import (
    DocumentPaginationPlan,
    FMSB_CHAPTER_TITLES,
    _build_layout_blocks,
    clean_heading_text,
    int_to_roman as _int_to_roman,
    is_duplicate_heading,
    normalize_for_dedup as _normalize_for_dedup,
    plan_document_pagination,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class TocEntry(BaseModel):
    level: int  # 1, 2, or 3
    title: str
    page_label: str
    bookmark_name: str
    section_id: str = ""


def extract_project_toc_entries(
    project: ProtocolProject,
    pagination_plan: Optional[DocumentPaginationPlan] = None,
    max_depth: Optional[int] = None,
) -> List[TocEntry]:
    """Build the ordered FMSB TOC hierarchy (calibrated to depth=2 for CAMES 1-page summary)."""
    depth = max_depth if max_depth is not None else int(getattr(project.formatting, "toc_depth", 2) or 2)
    allowed_levels = {1, 2} if depth <= 2 else {1, 2, 3}
    plan = pagination_plan or plan_document_pagination(project, use_gemini_decision=False)
    all_blocks, _ = _build_layout_blocks(project)

    entries: List[TocEntry] = []
    bm_idx = 1
    seen_l3_per_sec: Dict[str, set[str]] = {}

    for blk in all_blocks:
        if blk.toc_level not in allowed_levels or not blk.title.strip():
            continue
        if blk.toc_level == 3:
            sec_seen = seen_l3_per_sec.setdefault(blk.section_id, set())
            key = _normalize_for_dedup(blk.title)
            if not key or key in sec_seen or len(blk.title) > 120:
                continue
            sec_seen.add(key)

        h_key = f"{blk.section_id}::{blk.title}"
        pg_lbl = (
            plan.heading_page_map.get(h_key)
            or plan.section_start_page.get(blk.section_id)
            or ("i" if blk.stream == "roman" else "1")
        )
        entries.append(
            TocEntry(
                level=blk.toc_level,
                title=blk.title,
                page_label=pg_lbl,
                bookmark_name=f"_TocFMSB_{bm_idx}",
                section_id=blk.section_id,
            )
        )
        bm_idx += 1

    return entries


def _render_toc_markdown_and_html(entries: List[TocEntry]) -> Tuple[str, str]:
    """Render TocEntry list to clean Markdown pipe table and aligned HTML table."""
    md_lines = [
        "| Rubrique / Section du Protocole | Page |",
        "| :--- | ---: |",
    ]
    html_rows: List[str] = []

    for e in entries:
        indent_prefix = "" if e.level == 1 else ("\u00a0\u00a0\u00a0\u00a0" if e.level == 2 else "\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0")
        md_title = f"**{e.title}**" if e.level == 1 else f"{indent_prefix}{e.title}"
        md_lines.append(f"| {md_title} | {e.page_label} |")

        pad_px = 4 if e.level == 1 else (20 if e.level == 2 else 36)
        weight_css = "700" if e.level == 1 else "400"
        color_css = "#0f172a" if e.level == 1 else ("#1e293b" if e.level == 2 else "#475569")
        html_rows.append(
            f'<tr>'
            f'<td style="text-align:left;padding:4px 8px 4px {pad_px}px;font-weight:{weight_css};color:{color_css};border-bottom:1px dotted #cbd5e1;">'
            f"{escape(e.title)}</td>"
            f'<td style="text-align:right;padding:4px 6px;font-weight:{weight_css};color:{color_css};white-space:nowrap;border-bottom:1px dotted #cbd5e1;">'
            f"{escape(e.page_label)}</td>"
            f"</tr>"
        )

    md_lines.append("")
    md_lines.append(
        "*Note : Sommaire synthétique CAMES calibré sur les Grands titres & Sous-titres principaux (tenue sur 1 page A4), synchronisé avec Microsoft Word.*"
    )

    html_out = (
        '<table style="width:100%;border-collapse:collapse;text-align:left;font-size:10.5pt;">'
        '<thead><tr>'
        '<th style="text-align:left;padding:5px 4px;border-bottom:2px solid #6C2826;color:#6C2826;">Rubrique / Section du Protocole</th>'
        '<th style="text-align:right;padding:5px 6px;border-bottom:2px solid #6C2826;color:#6C2826;">Page</th>'
        "</tr></thead>"
        f"<tbody>{''.join(html_rows)}</tbody>"
        "</table>"
        '<p style="text-align:left;font-size:9pt;font-style:italic;color:#475569;margin-top:6px;">'
        "Sommaire exécutif conforme CAMES / FMSB (1 page) — synchronisation dynamique Word (.docx) et PDF."
        "</p>"
    )
    return "\n".join(md_lines), html_out


def generate_clean_toc_section_content(
    project: ProtocolProject,
    pagination_plan: Optional[DocumentPaginationPlan] = None,
) -> Tuple[str, str, List[TocEntry]]:
    """Generate clean Markdown and left-aligned HTML table representations of the FMSB TOC."""
    entries = extract_project_toc_entries(project, pagination_plan=pagination_plan)
    md_out, html_out = _render_toc_markdown_and_html(entries)
    return md_out, html_out, entries


def extract_word_toc_entries_from_docx(
    docx_path: Path,
    seq_filter: Optional[str] = None,
) -> List[TocEntry]:
    """Read freshly recalculated TOC or caption-list (\\c "Tableau" / \\c "Figure") entries from a Word-updated .docx."""
    if not docx_path.exists():
        return []
    try:
        doc = Document(str(docx_path))
    except Exception:
        return []

    in_toc_field = False
    fld_depth = 0
    toc_result_started = False
    nested_in_instr = False
    collected: List[TocEntry] = []

    for p in doc.paragraphs:
        xml_str = p._element.xml
        if not in_toc_field:
            if "TOC" in xml_str and "instrText" in xml_str:
                if seq_filter is None:
                    if "\\c" not in xml_str:
                        in_toc_field = True
                    else:
                        continue
                else:
                    if f'"{seq_filter}"' in xml_str or f"\\c {seq_filter}" in xml_str:
                        in_toc_field = True
                    else:
                        continue
            else:
                continue

        buf: List[str] = []
        done_outer = False
        for node in p._element.iter():
            local = node.tag.split("}")[-1]
            if local == "fldChar":
                ftype = node.get(qn("w:fldCharType"))
                if ftype == "begin":
                    fld_depth += 1
                    if fld_depth >= 2:
                        nested_in_instr = True
                elif ftype == "separate":
                    if fld_depth == 1:
                        toc_result_started = True
                    elif fld_depth >= 2:
                        nested_in_instr = False
                elif ftype == "end":
                    fld_depth = max(0, fld_depth - 1)
                    if fld_depth < 2:
                        nested_in_instr = False
                    if toc_result_started and fld_depth == 0:
                        done_outer = True
            elif toc_result_started and not nested_in_instr:
                if local == "t" and node.text:
                    buf.append(node.text)
                elif local == "tab":
                    buf.append("\t")

        raw_line = "".join(buf).strip()
        if raw_line:
            parts = [seg.strip() for seg in raw_line.split("\t") if seg.strip()]
            title = ""
            pg_lbl = ""
            if len(parts) >= 2 and re.match(r"^(?:[ivxlcdm]+|\d+)$", parts[-1], flags=re.IGNORECASE):
                title = clean_heading_text(" ".join(parts[:-1]))
                pg_lbl = parts[-1]
            else:
                m_tail = re.match(r"^(.*?)\s+([ivxlcdm]+|\d+)$", raw_line, flags=re.IGNORECASE)
                if m_tail:
                    title = clean_heading_text(m_tail.group(1))
                    pg_lbl = m_tail.group(2)

            if title and pg_lbl:
                if re.match(r"^[ivxlcdm]+$", pg_lbl, flags=re.IGNORECASE) and not pg_lbl.isdigit():
                    pg_lbl = pg_lbl.lower()
                pPr = p._element.find(qn("w:pPr"))
                pStyle_el = pPr.find(qn("w:pStyle")) if pPr is not None else None
                style_id = (pStyle_el.get(qn("w:val")) if pStyle_el is not None else "") or getattr(p.style, "style_id", "") or ""
                if any(k in style_id.upper() for k in ("TM1", "TOC1")):
                    lvl = 1
                elif any(k in style_id.upper() for k in ("TM3", "TOC3")):
                    lvl = 3
                else:
                    lvl = 2
                collected.append(
                    TocEntry(
                        level=lvl,
                        title=title,
                        page_label=pg_lbl,
                        bookmark_name=f"_TocWord_{len(collected) + 1}",
                    )
                )

        if done_outer:
            break

    return collected


def sync_toc_from_exported_docx(project: ProtocolProject, docx_path: Path) -> bool:
    """Update project.sections['toc'], ['list_of_tables'], and ['list_of_figures'] from Word COM's recalculated fields."""
    changed = False
    word_entries = extract_word_toc_entries_from_docx(docx_path, seq_filter=None)
    max_depth = max(1, min(3, int(getattr(project.formatting, "toc_depth", 2) or 2)))
    filtered_entries = [e for e in word_entries if e.level <= max_depth]
    if len(filtered_entries) >= 3:
        toc_sec = project.sections.get("toc")
        if toc_sec is not None:
            md_out, html_out = _render_toc_markdown_and_html(filtered_entries)
            toc_sec.content_markdown = md_out
            toc_sec.content_html = html_out
            changed = True

    from app.services.list_builder import (
        CaptionListEntry,
        _render_caption_list_markdown_and_html,
        clean_caption_title,
    )

    for seq_label, sec_id in (("Tableau", "list_of_tables"), ("Figure", "list_of_figures")):
        raw_caption_entries = extract_word_toc_entries_from_docx(docx_path, seq_filter=seq_label)
        target_sec = project.sections.get(sec_id)
        if raw_caption_entries and target_sec is not None:
            caption_entries: List[CaptionListEntry] = []
            for idx, item in enumerate(raw_caption_entries, start=1):
                clean_t = clean_caption_title(item.title, seq_label) or item.title
                disp = f"Tableau {idx} : {clean_t}" if seq_label == "Tableau" else f"Figure {idx}. {clean_t}"
                caption_entries.append(
                    CaptionListEntry(
                        seq_label=seq_label,
                        number=idx,
                        title=clean_t,
                        display_label=disp,
                        page_label=item.page_label,
                    )
                )
            md_c, html_c = _render_caption_list_markdown_and_html(seq_label, caption_entries)
            target_sec.content_markdown = md_c
            target_sec.content_html = html_c
            target_sec.custom_tables = []
            changed = True

    return changed


def _upsert_toc_style(
    styles_el,
    style_id: str,
    style_name: str,
    font_family: str,
    size_pt: int,
    bold: bool,
    left_dxa: int,
    space_before_dxa: int,
    space_after_dxa: int,
    right_tab_dxa: int,
) -> None:
    """Create or replace a Word TOC paragraph style (TM1/TM2/TM3/Tabledesillustrations)."""
    for existing in list(styles_el.findall(qn("w:style"))):
        if existing.get(qn("w:styleId")) == style_id:
            styles_el.remove(existing)

    half_pts = size_pt * 2
    bold_xml = "<w:b/><w:bCs/>" if bold else ""
    safe_font = escape(font_family or "Times New Roman")
    ind_xml = f'<w:ind w:left="{left_dxa}"/>' if left_dxa > 0 else '<w:ind w:left="0"/>'

    style_xml = (
        f'<w:style {nsdecls("w")} w:type="paragraph" w:styleId="{style_id}">'
        f'  <w:name w:val="{style_name}"/>'
        f'  <w:basedOn w:val="Normal"/>'
        f'  <w:autoRedefine/>'
        f"  <w:pPr>"
        f'    <w:tabs><w:tab w:val="right" w:leader="dot" w:pos="{right_tab_dxa}"/></w:tabs>'
        f'    <w:spacing w:before="{space_before_dxa}" w:after="{space_after_dxa}" w:line="252" w:lineRule="auto"/>'
        f"    {ind_xml}"
        f'    <w:jc w:val="left"/>'
        f"  </w:pPr>"
        f"  <w:rPr>"
        f'    <w:rFonts w:ascii="{safe_font}" w:hAnsi="{safe_font}" w:cs="{safe_font}"/>'
        f"    {bold_xml}"
        f'    <w:sz w:val="{half_pts}"/>'
        f'    <w:szCs w:val="{half_pts}"/>'
        f'    <w:color w:val="000000"/>'
        f"  </w:rPr>"
        f"</w:style>"
    )
    styles_el.append(parse_xml(style_xml))


def configure_word_toc_styles(doc: Document, fmt: FormattingRules) -> None:
    """Configure Word built-in French TOC styles (TM1-3, Tabledesillustrations) and updateFields."""
    usable_cm = max(12.0, 21.0 - 2.0 * float(fmt.margin_cm or 2.5))
    right_tab_dxa = int(round(usable_cm * 566.929))  # 1 cm ≈ 566.929 dxa (twips)
    base_pt = int(fmt.body_font_size_pt or 12)
    font_family = fmt.font_family or "Times New Roman"

    styles_el = doc.styles.element
    _upsert_toc_style(
        styles_el,
        style_id="TM1",
        style_name="toc 1",
        font_family=font_family,
        size_pt=base_pt,
        bold=True,
        left_dxa=0,
        space_before_dxa=100,
        space_after_dxa=35,
        right_tab_dxa=right_tab_dxa,
    )
    _upsert_toc_style(
        styles_el,
        style_id="TM2",
        style_name="toc 2",
        font_family=font_family,
        size_pt=base_pt,
        bold=False,
        left_dxa=340,
        space_before_dxa=25,
        space_after_dxa=25,
        right_tab_dxa=right_tab_dxa,
    )
    _upsert_toc_style(
        styles_el,
        style_id="TM3",
        style_name="toc 3",
        font_family=font_family,
        size_pt=max(10, base_pt - 1),
        bold=False,
        left_dxa=680,
        space_before_dxa=15,
        space_after_dxa=15,
        right_tab_dxa=right_tab_dxa,
    )
    _upsert_toc_style(
        styles_el,
        style_id="Tabledesillustrations",
        style_name="table of figures",
        font_family=font_family,
        size_pt=base_pt,
        bold=False,
        left_dxa=0,
        space_before_dxa=30,
        space_after_dxa=30,
        right_tab_dxa=right_tab_dxa,
    )

    # Tell Word to prompt / refresh fields on open
    settings_el = doc.settings.element
    for existing in list(settings_el.findall(qn("w:updateFields"))):
        settings_el.remove(existing)
    settings_el.append(parse_xml(f'<w:updateFields {nsdecls("w")} w:val="true"/>'))


def _apply_toc_entry_font(run, font_family: str, size_pt: int, bold: bool) -> None:
    run.font.name = font_family
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.font.color.rgb = RGBColor(0, 0, 0)
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), font_family)
    rFonts.set(qn("w:hAnsi"), font_family)
    rFonts.set(qn("w:cs"), font_family)


def insert_word_automatic_toc(
    doc: Document,
    anchor_p,
    project: ProtocolProject,
    fmt: FormattingRules,
    pagination_plan: Optional[DocumentPaginationPlan] = None,
) -> Tuple[Any, List[TocEntry]]:
    """Insert a true native Word TOC field (TM1/TM2/TM3 + dot leaders) after anchor_p."""
    font_family = fmt.font_family or "Times New Roman"
    base_pt = int(fmt.body_font_size_pt or 12)
    usable_cm = max(12.0, 21.0 - 2.0 * float(fmt.margin_cm or 2.5))
    right_tab_dxa = int(round(usable_cm * 566.929))

    # Subtle note paragraph (left-aligned, never justified)
    note_el = OxmlElement("w:p")
    anchor_p._p.addnext(note_el)
    note_p = doc.paragraphs[0].__class__(note_el, anchor_p._parent)
    note_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    note_p.paragraph_format.space_before = Pt(0)
    note_p.paragraph_format.space_after = Pt(6)
    r_note = note_p.add_run(
        "Table des matières générée automatiquement (actualisation automatique ou touche F9 sous Word)."
    )
    _apply_toc_entry_font(r_note, font_family, size_pt=9, bold=False)
    r_note.italic = True
    r_note.font.color.rgb = RGBColor(100, 116, 139)

    entries = extract_project_toc_entries(project, pagination_plan=pagination_plan)
    cursor_p = note_p
    toc_depth = max(1, min(3, int(getattr(fmt, "toc_depth", 2) or 2)))
    instr_code = f' TOC \\o "1-{toc_depth}" \\h \\z \\u '

    if not entries:
        empty_el = OxmlElement("w:p")
        cursor_p._p.addnext(empty_el)
        empty_p = doc.paragraphs[0].__class__(empty_el, cursor_p._parent)
        empty_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r = empty_p.add_run()
        r._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>'))
        r._r.append(
            parse_xml(
                f'<w:instrText {nsdecls("w")} xml:space="preserve">{instr_code}</w:instrText>'
            )
        )
        r._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>'))
        r._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>'))
        return empty_p, entries

    style_by_level = {1: "TM1", 2: "TM2", 3: "TM3"}
    indent_by_level = {1: 0, 2: 280, 3: 560}
    # Clean, elegant size calibrated for 1 single A4 page
    size_by_level = {1: min(base_pt, 11), 2: max(9.5, min(base_pt - 1, 10.5)), 3: max(9, base_pt - 2)}

    for idx, entry in enumerate(entries):
        p_el = OxmlElement("w:p")
        cursor_p._p.addnext(p_el)
        p_obj = doc.paragraphs[0].__class__(p_el, cursor_p._parent)

        style_id = style_by_level.get(entry.level, "TM2")
        left_dxa = indent_by_level.get(entry.level, 280)
        sz_pt = size_by_level.get(entry.level, base_pt)
        is_bold = entry.level == 1

        pPr = parse_xml(
            f'<w:pPr {nsdecls("w")}>'
            f'  <w:pStyle w:val="{style_id}"/>'
            f'  <w:tabs><w:tab w:val="right" w:leader="dot" w:pos="{right_tab_dxa}"/></w:tabs>'
            f'  <w:spacing w:before="{32 if is_bold else 10}" w:after="10" w:line="240" w:lineRule="auto"/>'
            f'  <w:ind w:left="{left_dxa}"/>'
            f'  <w:jc w:val="left"/>'
            f"</w:pPr>"
        )
        p_el.append(pPr)

        if idx == 0:
            r_begin = p_obj.add_run()
            r_begin._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>'))
            r_begin._r.append(
                parse_xml(
                    f'<w:instrText {nsdecls("w")} xml:space="preserve">{instr_code}</w:instrText>'
                )
            )
            r_begin._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>'))

        r_title = p_obj.add_run(entry.title)
        _apply_toc_entry_font(r_title, font_family, size_pt=sz_pt, bold=is_bold)

        r_tab = p_obj.add_run()
        _apply_toc_entry_font(r_tab, font_family, size_pt=sz_pt, bold=is_bold)
        r_tab._r.append(parse_xml(f'<w:tab {nsdecls("w")}/>'))

        r_pg = p_obj.add_run(entry.page_label)
        _apply_toc_entry_font(r_pg, font_family, size_pt=sz_pt, bold=is_bold)

        if idx == len(entries) - 1:
            r_end = p_obj.add_run()
            r_end._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>'))

        cursor_p = p_obj

    return cursor_p, entries
