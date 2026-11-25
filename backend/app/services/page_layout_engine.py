"""Physical A4 Page Boundary Engine & 'Gemini Decision 1' Pagination Orchestrator.

Models Microsoft Word A4 pagination rules (21.0 x 29.7 cm, configurable margins,
Times New Roman line metrics, keep_with_next, keep_together, paragraph line-splitting),
enforces FMSB structural page boundaries, computes >= 12pt minimum legible figure
dimensions, and optionally adjudicates borderline splits via Gemini Decision 1.
"""

from html.parser import HTMLParser
import html as html_lib
import math
from pathlib import Path
import re
import textwrap
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import get_chapter_titles, get_doc_labels, get_localized_canvas, get_project_canvas, normalize_lang
from app.schemas.protocol import FormattingRules, ProtocolProject, StepStatus
from app.services.annexes_generator import build_annexes_pack
from app.services.diagram_renderer import compute_minimum_legible_figure_size_cm
from app.services.gemini_client import GEMINI_DECISION_1_LABEL, gemini_router
from app.services.list_builder import clean_caption_title, collect_deduplicated_abbreviations

A4_WIDTH_CM = 21.0
A4_HEIGHT_CM = 29.7
PT_PER_CM = 72.0 / 2.54  # 28.3464567 pt/cm
# Microsoft Word Times New Roman 12pt single line spacing advance factor (~1.11x em)
TNR_SINGLE_LINE_FACTOR = 1.08
# Minimum height (~2 lines at 12pt x 1.5) required before Word allows orphan split
MIN_WIDOW_ORPHAN_PT = 44.0

FMSB_CHAPTER_TITLES: Dict[str, str] = {
    "chapitre_1": "CHAPITRE I : INTRODUCTION",
    "chapitre_2": "CHAPITRE II : REVUE DE LA LITTÉRATURE",
    "chapitre_3": "CHAPITRE III : MÉTHODOLOGIE",
    "references": "CHAPITRE IV : RÉFÉRENCES",
    "annexes": "CHAPITRE V : ANNEXES",
}


class LayoutBlock(BaseModel):
    block_id: str
    kind: str  # front_matter | grand_title_frame | heading_2 | heading_3 | paragraph | bullet | table_with_caption | figure_with_caption | annexe_start | references_list
    section_id: str
    title: str
    toc_level: int = 0  # 0 = none, 1 = chapter/front, 2 = section, 3 = subsection
    stream: str = "arabic"  # "roman" | "arabic"
    estimated_height_pt: float
    page_break_before: bool = False
    page_break_after: bool = False
    keep_with_next: bool = False
    keep_together: bool = False
    isolated_full_page: bool = False
    vertical_align: str = "top"  # "top" | "center"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PagePlan(BaseModel):
    page_index: int
    page_label: str
    stream: str
    usable_height_pt: float
    used_height_pt: float
    remaining_height_pt: float
    block_ids: List[str] = Field(default_factory=list)
    summary_titles: List[str] = Field(default_factory=list)
    rule_notes: List[str] = Field(default_factory=list)


class DocumentPaginationPlan(BaseModel):
    page_size: str = "A4"
    margin_cm: float
    usable_width_cm: float
    usable_height_cm: float
    usable_width_pt: float
    usable_height_pt: float
    total_roman_pages: int
    total_arabic_pages: int
    model_used: str
    decision_notes: str
    section_start_page: Dict[str, str] = Field(default_factory=dict)
    heading_page_map: Dict[str, str] = Field(default_factory=dict)
    table_page_map: Dict[str, str] = Field(default_factory=dict)
    figure_page_map: Dict[str, str] = Field(default_factory=dict)
    figure_dimensions_cm: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    break_before_block_ids: List[str] = Field(default_factory=list)
    pages: List[PagePlan] = Field(default_factory=list)


def int_to_roman(num: int) -> str:
    """Convert a positive integer into lowercase Roman numerals for front matter."""
    if num <= 0:
        return "i"
    pairs = [
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ]
    out: List[str] = []
    rem = num
    for val, sym in pairs:
        while rem >= val:
            out.append(sym)
            rem -= val
    return "".join(out)


def _strip_accents_lower(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def clean_heading_text(raw: str) -> str:
    """Normalize a heading string and remove Markdown markers or literal dot leaders."""
    s = re.sub(r"\s+", " ", html_lib.unescape(raw or ""))
    s = re.sub(r"^[•*]\s*", "", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"^#{1,6}\s*", "", s.strip())
    s = s.replace("**", "").replace("__", "").strip()
    s = re.sub(
        r"\s*\.{3,}\s*(?:dynamic\s*)?(?:[ivxlcdm]+|\d+)?\s*$",
        "",
        s,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", s).strip()


def normalize_for_dedup(text: str) -> str:
    s = _strip_accents_lower(clean_heading_text(text))
    s = re.sub(r"^(?:chapitre|chapter)\s+[ivx1-9]+\s*[:.\-–]*\s*", "", s)
    s = re.sub(r"^[ivx]+(?:\.\d+)+\s*[:.)\-–]*\s*", "", s)
    s = re.sub(r"^[ivx]+\.\s+", "", s)
    s = re.sub(r"^\d+(?:[–\-]\d+)?(?:\.\d+)*\s*[:.)\-–]*\s*", "", s)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(?:et|de|du|des|la|le|les|l|d|a|the|of|and|for|in)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_duplicate_heading(candidate: str, references: List[str]) -> bool:
    """Return True if candidate repeats a chapter banner or section heading."""
    cand_raw = _strip_accents_lower(clean_heading_text(candidate))
    if not cand_raw:
        return True

    redundant_prefixes = (
        "table des matieres",
        "table de matieres",
        "sommaire",
        "liste des tableaux",
        "liste des figures",
        "abreviations, acronymes",
    )
    if any(cand_raw.startswith(p) for p in redundant_prefixes):
        return True

    if re.match(r"^(?:chapitre|chapter)\s+[ivx1-9]+\b", cand_raw):
        return True
    cand_no_num = re.sub(r"^(?:\d+|[ivx]+)(?:\.\d+)*\s*[:.)\-–]*\s*", "", cand_raw).strip()
    legacy_redundant_titles = (
        "introduction",
        "introduction generale",
        "references",
        "bibliographie",
        "annexes",
        "problematique",
        "problem statement",
        "revue de la litterature",
        "literature review",
        "methodologie",
        "methodology",
        "justification de l'etude",
        "contexte & epidemiologie",
        "interet de la recherche",
        "liste des variables",
    )
    if cand_raw in legacy_redundant_titles or cand_no_num in legacy_redundant_titles:
        return True

    norm_cand = normalize_for_dedup(candidate)
    if not norm_cand:
        return True

    for ref in references:
        if not ref:
            continue
        norm_ref = normalize_for_dedup(ref)
        if not norm_ref:
            continue
        if norm_cand == norm_ref:
            return True
        if len(norm_cand) >= 8 and len(norm_ref) >= 8:
            if norm_cand in norm_ref or norm_ref in norm_cand:
                return True
    return False


class _BlockExtractorHTMLParser(HTMLParser):
    """Lightweight HTML parser extracting ordered paragraphs, headings, bullets, and tables."""

    def __init__(self) -> None:
        super().__init__()
        self.items: List[Tuple[str, Any]] = []  # ("heading", level, txt) | ("para", is_bullet, txt) | ("table", headers, rows)
        self._in_table = False
        self._in_tr = False
        self._in_cell = False
        self._is_th = False
        self._current_rows: List[Tuple[bool, List[str]]] = []
        self._current_tr_is_header = False
        self._current_tr: List[str] = []
        self._cell_buf: List[str] = []
        self._heading_level = 0
        self._in_li = False
        self._buf: List[str] = []

    def _flush_text_block(self, kind: str = "para") -> None:
        txt = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if not txt:
            return
        if kind == "heading" and self._heading_level > 0:
            self.items.append(("heading", (self._heading_level, txt)))
        else:
            self.items.append(("para", (self._in_li, txt)))

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t == "table":
            self._flush_text_block()
            self._in_table = True
            self._current_rows = []
            return
        if self._in_table:
            if t == "tr":
                self._in_tr = True
                self._current_tr = []
                self._current_tr_is_header = False
            elif t in ("td", "th"):
                self._in_cell = True
                self._is_th = t == "th"
                if self._is_th:
                    self._current_tr_is_header = True
                self._cell_buf = []
            elif t == "br" and self._in_cell:
                self._cell_buf.append(" ")
            return

        if t in ("h1", "h2", "h3", "h4"):
            self._flush_text_block()
            self._heading_level = int(t[1])
        elif t in ("p", "div", "blockquote"):
            self._flush_text_block()
        elif t == "li":
            self._flush_text_block()
            self._in_li = True
        elif t == "br":
            self._buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "table" and self._in_table:
            self._in_table = False
            if self._current_rows:
                headers = self._current_rows[0][1]
                data_rows = [r[1] for r in self._current_rows[1:]]
                self.items.append(("table", (headers, data_rows)))
            self._current_rows = []
            return
        if self._in_table:
            if t in ("td", "th") and self._in_cell:
                self._in_cell = False
                self._current_tr.append(re.sub(r"\s+", " ", "".join(self._cell_buf)).strip())
                self._cell_buf = []
            elif t == "tr" and self._in_tr:
                self._in_tr = False
                if any(c for c in self._current_tr):
                    self._current_rows.append((self._current_tr_is_header, self._current_tr))
                self._current_tr = []
            return

        if t in ("h1", "h2", "h3", "h4"):
            self._flush_text_block("heading")
            self._heading_level = 0
        elif t in ("p", "div", "blockquote"):
            self._flush_text_block()
        elif t == "li":
            self._flush_text_block()
            self._in_li = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)
        elif not self._in_table:
            self._buf.append(data)


def _parse_markdown_items(md_str: str) -> List[Tuple[str, Any]]:
    """Parse Markdown prose, headings, bullets, and pipe tables into ordered layout items."""
    lines = (md_str or "").splitlines()
    items: List[Tuple[str, Any]] = []
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue

        # Detect Markdown pipe table
        if (
            stripped.startswith("|")
            and stripped.endswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1])
        ):
            headers = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows: List[List[str]] = []
            while i < len(lines):
                r_line = lines[i].strip()
                if not (r_line.startswith("|") and r_line.endswith("|")):
                    break
                rows.append([c.strip() for c in r_line.strip("|").split("|")])
                i += 1
            items.append(("table", (headers, rows)))
            continue

        m_h = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if m_h:
            lvl = len(m_h.group(1))
            items.append(("heading", (lvl, clean_heading_text(m_h.group(2)))))
            i += 1
            continue

        m_bold_h = re.match(r"^\*\*(\d+(?:\.\d+)+[^*]{2,95})\*\*$", stripped)
        if m_bold_h:
            items.append(("heading", (3, clean_heading_text(m_bold_h.group(1)))))
            i += 1
            continue

        is_bullet = bool(re.match(r"^(?:[-*•]|\d+[.)])\s+", stripped))
        plain = re.sub(r"^(?:[-*•])\s+", "", stripped)
        plain = plain.replace("**", "").replace("__", "").strip()
        if plain:
            items.append(("para", (is_bullet, plain)))
        i += 1
    return items


def _extract_ordered_rich_items(html_str: str, md_str: str) -> List[Tuple[str, Any]]:
    raw_html = (html_str or "").strip()
    raw_md = (md_str or "").strip()
    has_md_pipe_table = bool(re.search(r"(?m)^\s*\|[\s:\-|]+\|\s*$", raw_md))

    if has_md_pipe_table and "<table" not in raw_html.lower():
        return _parse_markdown_items(raw_md)
    if raw_html:
        parser = _BlockExtractorHTMLParser()
        parser.feed(raw_html)
        parser._flush_text_block()
        if parser.items:
            return parser.items
    if raw_md:
        return _parse_markdown_items(raw_md)
    return []


def _measure_paragraph_height_pt(
    text: str,
    font_pt: float,
    line_spacing: float,
    usable_width_pt: float,
    space_before_pt: float = 0.0,
    space_after_pt: float = 6.0,
    is_bullet: bool = False,
) -> float:
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    line_h = font_pt * TNR_SINGLE_LINE_FACTOR * line_spacing
    if not clean:
        return round(space_before_pt + line_h + space_after_pt, 2)
    eff_width = usable_width_pt * (0.90 if is_bullet else 0.96)
    chars_per_line = max(32, int(eff_width / max(3.2, font_pt * 0.398)))
    total_lines = 0
    for seg in (text or "").splitlines():
        seg_clean = seg.strip()
        if not seg_clean:
            continue
        wrapped = textwrap.wrap(seg_clean, width=chars_per_line, break_long_words=True)
        total_lines += max(1, len(wrapped))
    total_lines = max(1, total_lines)
    return round(space_before_pt + total_lines * line_h + space_after_pt, 2)


def _measure_table_height_pt(
    headers: List[str],
    rows: List[List[str]],
    body_font_pt: float,
    usable_width_pt: float,
    has_caption: bool = True,
) -> float:
    n_cols = max(1, len(headers) if headers else (len(rows[0]) if rows else 2))
    col_w_pt = max(48.0, (usable_width_pt - 10.0) / n_cols)
    hdr_font = max(9.0, body_font_pt - 1.0)
    row_font = max(9.0, body_font_pt - 2.0)

    hdr_cpl = max(6, int((col_w_pt - 6.0) / (hdr_font * 0.41)))
    hdr_lines = max(
        [len(textwrap.wrap(str(h or ""), width=hdr_cpl)) or 1 for h in (headers or [""])],
        default=1,
    )
    hdr_h = hdr_lines * (hdr_font * TNR_SINGLE_LINE_FACTOR * 1.12) + 8.0

    row_cpl = max(6, int((col_w_pt - 6.0) / (row_font * 0.40)))
    data_h = 0.0
    for row in rows:
        max_cell_lines = 1
        for cell_val in row[:n_cols]:
            w_lines = textwrap.wrap(str(cell_val or ""), width=row_cpl)
            max_cell_lines = max(max_cell_lines, len(w_lines) or 1)
        data_h += max_cell_lines * (row_font * TNR_SINGLE_LINE_FACTOR * 1.12) + 6.0

    cap_h = 26.0 if has_caption else 0.0
    spacer_h = 12.0
    return round(cap_h + hdr_h + data_h + spacer_h, 2)


def _build_layout_blocks(
    project: ProtocolProject,
) -> Tuple[List[LayoutBlock], Dict[str, Dict[str, Any]]]:
    """Decompose the ProtocolProject into ordered physical A4 layout blocks."""
    from app.services.media_transport import (
        get_effective_section_tables,
        synchronize_module1_to_module2_tables,
        transport_and_repair_project_images,
    )

    transport_and_repair_project_images(project)
    synchronize_module1_to_module2_tables(project)

    fmt = project.formatting
    margin_cm = float(fmt.margin_cm or 2.5)
    usable_width_cm = max(8.0, A4_WIDTH_CM - 2.0 * margin_cm)
    usable_height_cm = max(10.0, A4_HEIGHT_CM - 2.0 * margin_cm)
    usable_width_pt = usable_width_cm * PT_PER_CM
    usable_height_pt = usable_height_cm * PT_PER_CM
    body_pt = float(fmt.body_font_size_pt or 12)
    spacing = float(fmt.line_spacing or 1.5)

    blocks: List[LayoutBlock] = []
    fig_dims: Dict[str, Dict[str, Any]] = {}
    blk_counter = 0

    def next_id(prefix: str) -> str:
        nonlocal blk_counter
        blk_counter += 1
        return f"{prefix}_{blk_counter:04d}"

    # 1. Roman Front Matter
    # Cover page is p. i (isolated cover sheet)
    blocks.append(
        LayoutBlock(
            block_id=next_id("cover"),
            kind="front_matter",
            section_id="cover",
            title="Page de garde",
            toc_level=0,
            stream="roman",
            estimated_height_pt=usable_height_pt,
            isolated_full_page=True,
            page_break_after=True,
        )
    )

    lang = normalize_lang(getattr(project, "language", "fr"))
    chap_titles = get_chapter_titles(lang)
    doc_labels = get_doc_labels(lang)
    localized_canvas = get_project_canvas(project)

    # TOC starts on a fresh blank page (p. ii)
    # Calibrated for CAMES executive summary (Niveaux 1 et 2 = exactement 1 page A4)
    if fmt.auto_toc:
        toc_depth = max(1, min(3, int(getattr(fmt, "toc_depth", 2) or 2)))
        heading_count = 10 + len([s for s in localized_canvas if s.chapter_id not in ("preliminaires", "references", "annexes")]) + len(build_annexes_pack(project))
        if toc_depth >= 3:
            for s_item in localized_canvas:
                if s_item.chapter_id in ("preliminaires", "references", "annexes"):
                    continue
                s_obj = project.sections.get(s_item.id)
                if s_obj:
                    heading_count += sum(
                        1
                        for k, _ in _extract_ordered_rich_items(s_obj.content_html, s_obj.content_markdown)
                        if k == "heading"
                    )
            toc_h = max(180.0, 52.0 + heading_count * 14.5)
        else:
            # 1-page compact executive TOC
            toc_h = min(usable_height_pt * 0.92, max(180.0, 40.0 + heading_count * 13.0))

        blocks.append(
            LayoutBlock(
                block_id=next_id("toc"),
                kind="front_matter",
                section_id="toc",
                title=doc_labels["anchor_toc"],
                toc_level=0,
                stream="roman",
                estimated_height_pt=round(toc_h, 2),
                page_break_before=True,
            )
        )

    # Abbreviations (measured as ONE unified deduplicated alphabetical 2-column table, without page numbers)
    if fmt.auto_abbreviations_table:
        unified_abbrs = collect_deduplicated_abbreviations(project)
        abbr_h = 46.0
        if unified_abbrs:
            abbr_rows = [[a.acronym, a.full_form] for a in unified_abbrs]
            abbr_h += _measure_table_height_pt(
                doc_labels["abbr_headers"].split("|"),
                abbr_rows,
                body_pt,
                usable_width_pt,
                has_caption=False,
            )
        blocks.append(
            LayoutBlock(
                block_id=next_id("abbr"),
                kind="front_matter",
                section_id="abbreviations",
                title=doc_labels["anchor_abbreviations"],
                toc_level=1,
                stream="roman",
                estimated_height_pt=round(max(120.0, abbr_h), 2),
                page_break_before=True,
            )
        )

    # Count numbered tables & figures across body chapters, references & annexes (never front-matter!)
    total_tables = 0
    total_figures = len(
        [
            im
            for im in project.images
            if im.approved and im.target_section_id not in ("cover_page", "toc", "abbreviations", "list_of_tables", "list_of_figures", "preliminaires")
        ]
    )
    for spec_cnt in localized_canvas:
        if spec_cnt.id in ("cover_page", "toc", "abbreviations", "list_of_tables", "list_of_figures", "preliminaires"):
            continue
        sec_cnt = project.sections.get(spec_cnt.id)
        total_tables += len(get_effective_section_tables(project, spec_cnt.id))
        if sec_cnt is not None:
            total_figures += len([d for d in (getattr(sec_cnt, "custom_diagrams", None) or []) if d.nodes])

    if fmt.auto_list_of_tables:
        lot_h = max(140.0, 52.0 + max(1, total_tables) * 22.0)
        blocks.append(
            LayoutBlock(
                block_id=next_id("lot"),
                kind="front_matter",
                section_id="list_of_tables",
                title=doc_labels["list_of_tables_title"],
                toc_level=1,
                stream="roman",
                estimated_height_pt=round(lot_h, 2),
                page_break_before=True,
            )
        )

    if fmt.auto_list_of_figures:
        lof_h = max(140.0, 52.0 + max(1, total_figures) * 22.0)
        blocks.append(
            LayoutBlock(
                block_id=next_id("lof"),
                kind="front_matter",
                section_id="list_of_figures",
                title=doc_labels["list_of_figures_title"],
                toc_level=1,
                stream="roman",
                estimated_height_pt=round(lof_h, 2),
                page_break_before=True,
            )
        )

    # 2. Arabic Stream: Chapters -> Sections -> References -> Annexes
    approved_images_by_section: Dict[str, List[Any]] = {}
    for img in project.images:
        if img.approved:
            approved_images_by_section.setdefault(img.target_section_id, []).append(img)

    seen_chapters: set[str] = set()
    table_idx = 1
    figure_idx = 1
    diag_dir = get_settings().storage_dir / "tmp_diagrams" / project.project_id

    for spec in localized_canvas:
        if spec.chapter_id in ("preliminaires", "references", "annexes"):
            continue

        if spec.chapter_id not in seen_chapters:
            seen_chapters.add(spec.chapter_id)
            ch_title = chap_titles.get(spec.chapter_id, spec.chapter_title.upper())
            # Grand framed chapter title sits ALONE, centered on its own page; body starts next page
            blocks.append(
                LayoutBlock(
                    block_id=next_id("chap"),
                    kind="grand_title_frame",
                    section_id=spec.chapter_id,
                    title=ch_title,
                    toc_level=1,
                    stream="arabic",
                    estimated_height_pt=usable_height_pt,
                    page_break_before=True,
                    page_break_after=True,
                    isolated_full_page=True,
                    vertical_align="center",
                )
            )

        # Level-2 section heading flows continuously after previous section (no artificial +1 page!)
        blocks.append(
            LayoutBlock(
                block_id=next_id("sec"),
                kind="heading_2",
                section_id=spec.id,
                title=spec.title,
                toc_level=2,
                stream="arabic",
                estimated_height_pt=_measure_paragraph_height_pt(
                    spec.title, body_pt + 1.0, spacing, usable_width_pt, space_after_pt=6.0
                ),
                keep_with_next=True,
            )
        )

        sec_state = project.sections.get(spec.id)
        ch_title_ref = chap_titles.get(spec.chapter_id, spec.chapter_title.upper())
        rich_items = (
            _extract_ordered_rich_items(sec_state.content_html, sec_state.content_markdown)
            if sec_state
            else []
        )

        pending_table_caption: Optional[str] = None
        if not rich_items:
            blocks.append(
                LayoutBlock(
                    block_id=next_id("placeholder"),
                    kind="paragraph",
                    section_id=spec.id,
                    title="",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=_measure_paragraph_height_pt(
                        "[Section en attente]", body_pt, spacing, usable_width_pt
                    ),
                )
            )
        else:
            for item_kind, payload in rich_items:
                if item_kind == "heading":
                    _, raw_h = payload
                    clean_h = clean_heading_text(raw_h)
                    if not clean_h or is_duplicate_heading(
                        clean_h, [spec.title, spec.chapter_title, ch_title_ref]
                    ):
                        continue
                    blocks.append(
                        LayoutBlock(
                            block_id=next_id("sub"),
                            kind="heading_3",
                            section_id=spec.id,
                            title=clean_h,
                            toc_level=3,
                            stream="arabic",
                            estimated_height_pt=_measure_paragraph_height_pt(
                                clean_h, body_pt, spacing, usable_width_pt, space_after_pt=6.0
                            ),
                            keep_with_next=True,
                        )
                    )
                elif item_kind == "para":
                    is_bullet, txt = payload
                    # Keep literal "Tableau N :" caption attached to its immediate table
                    if re.match(r"^(?:Tableau|Table)\s+\d+\b", txt.strip(), flags=re.IGNORECASE):
                        pending_table_caption = txt.strip()
                        continue
                    blocks.append(
                        LayoutBlock(
                            block_id=next_id("para"),
                            kind="bullet" if is_bullet else "paragraph",
                            section_id=spec.id,
                            title="",
                            toc_level=0,
                            stream="arabic",
                            estimated_height_pt=_measure_paragraph_height_pt(
                                txt, body_pt, spacing, usable_width_pt, is_bullet=is_bullet
                            ),
                        )
                    )
                elif item_kind == "table":
                    headers, rows = payload
                    tbl_title = clean_caption_title(pending_table_caption or "", "Tableau")
                    pending_table_caption = None
                    tbl_h = _measure_table_height_pt(headers, rows, body_pt, usable_width_pt, has_caption=False)
                    blocks.append(
                        LayoutBlock(
                            block_id=next_id("tbl"),
                            kind="table_inline",
                            section_id=spec.id,
                            title=tbl_title,
                            toc_level=0,
                            stream="arabic",
                            estimated_height_pt=tbl_h,
                            keep_together=tbl_h <= usable_height_pt * 0.92,
                        )
                    )

        if pending_table_caption:
            blocks.append(
                LayoutBlock(
                    block_id=next_id("para"),
                    kind="paragraph",
                    section_id=spec.id,
                    title="",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=_measure_paragraph_height_pt(
                        pending_table_caption, body_pt, spacing, usable_width_pt
                    ),
                )
            )

        # Unified effective tables attached to section (custom OR non-suppressed structured, zero duplicate)
        for tbl in get_effective_section_tables(project, spec.id):
            clean_ct_title = clean_caption_title(tbl.title, "Tableau") or tbl.title
            ct_h = _measure_table_height_pt(tbl.headers, tbl.rows, body_pt, usable_width_pt, has_caption=True)
            blocks.append(
                LayoutBlock(
                    block_id=next_id("ctbl"),
                    kind="table_with_caption",
                    section_id=spec.id,
                    title=f"Tableau {table_idx} : {clean_ct_title}",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=ct_h,
                    keep_together=ct_h <= usable_height_pt * 0.92,
                )
            )
            table_idx += 1

        # Approved figures (>= 12pt minimum legibility + caption locked together)
        for img in approved_images_by_section.get(spec.id, []):
            img_path = Path(img.local_path) if getattr(img, "local_path", None) else None
            if not img_path or not img_path.exists():
                from app.services.media_transport import restore_image_from_blob

                restored = restore_image_from_blob(project.project_id, img)
                if restored is not None:
                    img_path = restored
            sz = compute_minimum_legible_figure_size_cm(
                diagram=None,
                image_path=img_path if (img_path and img_path.exists()) else None,
                rules=fmt,
                min_font_pt=12.0,
            )
            fig_key = getattr(img, "image_id", None) or getattr(img, "asset_id", None) or f"img_{figure_idx}"
            fig_dims[fig_key] = sz
            fig_h_pt = float(sz["height_cm"]) * PT_PER_CM + 36.0
            clean_img_title = clean_caption_title(img.title, "Figure") or img.title
            blocks.append(
                LayoutBlock(
                    block_id=next_id("figimg"),
                    kind="figure_with_caption",
                    section_id=spec.id,
                    title=f"Figure {figure_idx} : {clean_img_title}",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=round(fig_h_pt, 2),
                    keep_together=True,
                    metadata={"figure_key": fig_key, **sz},
                )
            )
            figure_idx += 1

        # Structured diagrams (>= 12pt minimum legibility + caption locked together)
        if sec_state and getattr(sec_state, "custom_diagrams", None):
            for dg in sec_state.custom_diagrams:
                if not dg.nodes:
                    continue
                png_candidate = diag_dir / f"{dg.diagram_id}.png"
                sz = compute_minimum_legible_figure_size_cm(
                    diagram=dg,
                    image_path=png_candidate if png_candidate.exists() else None,
                    rules=fmt,
                    min_font_pt=12.0,
                )
                fig_dims[dg.diagram_id] = sz
                fig_h_pt = float(sz["height_cm"]) * PT_PER_CM + 36.0
                clean_dg_title = clean_caption_title(dg.title, "Figure") or dg.title
                blocks.append(
                    LayoutBlock(
                        block_id=next_id("figdg"),
                        kind="figure_with_caption",
                        section_id=spec.id,
                        title=f"Figure {figure_idx} : {clean_dg_title}",
                        toc_level=0,
                        stream="arabic",
                        estimated_height_pt=round(fig_h_pt, 2),
                        keep_together=True,
                        metadata={"figure_key": dg.diagram_id, **sz},
                    )
                )
                figure_idx += 1

    # 3. References (grand framed title centered on its own page -> bibliography starts next page)
    ref_bib_title = (
        "IV. Bibliography (Vancouver Style)"
        if lang == "en"
        else "IV. Références bibliographiques (Système Vancouver)"
    )
    blocks.append(
        LayoutBlock(
            block_id=next_id("chap"),
            kind="grand_title_frame",
            section_id="references",
            title=chap_titles["references"],
            toc_level=1,
            stream="arabic",
            estimated_height_pt=usable_height_pt,
            page_break_before=True,
            page_break_after=True,
            isolated_full_page=True,
            vertical_align="center",
        )
    )
    blocks.append(
        LayoutBlock(
            block_id=next_id("sec"),
            kind="heading_2",
            section_id="references",
            title=ref_bib_title,
            toc_level=2,
            stream="arabic",
            estimated_height_pt=_measure_paragraph_height_pt(
                ref_bib_title, body_pt + 1.0, spacing, usable_width_pt
            ),
            keep_with_next=True,
        )
    )
    ref_sec = project.sections.get("references")
    if project.references:
        for r_idx, ref in enumerate(project.references, start=1):
            base_line = ref.vancouver_citation or f"{', '.join(ref.authors)}. {ref.title}. {ref.journal}."
            line = f"{r_idx}. {base_line}"
            blocks.append(
                LayoutBlock(
                    block_id=next_id("ref"),
                    kind="references_list",
                    section_id="references",
                    title="",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=_measure_paragraph_height_pt(
                        line, body_pt, spacing, max(120.0, usable_width_pt - 24.0)
                    ),
                )
            )
    elif ref_sec and (ref_sec.content_html.strip() or ref_sec.content_markdown.strip()):
        ref_items = _extract_ordered_rich_items(ref_sec.content_html, ref_sec.content_markdown)
        for rk, rpayload in ref_items:
            if rk == "para":
                _, rtxt = rpayload
                blocks.append(
                    LayoutBlock(
                        block_id=next_id("refp"),
                        kind="references_list",
                        section_id="references",
                        title="",
                        toc_level=0,
                        stream="arabic",
                        estimated_height_pt=_measure_paragraph_height_pt(rtxt, body_pt, spacing, usable_width_pt),
                    )
                )
    else:
        raw_lines = [ln.strip() for ln in (ref_sec.content_markdown if ref_sec else "").splitlines() if ln.strip()]
        if raw_lines:
            for ln in raw_lines:
                blocks.append(
                    LayoutBlock(
                        block_id=next_id("refln"),
                        kind="references_list",
                        section_id="references",
                        title="",
                        toc_level=0,
                        stream="arabic",
                        estimated_height_pt=_measure_paragraph_height_pt(ln, body_pt, spacing, usable_width_pt),
                    )
                )
        else:
            blocks.append(
                LayoutBlock(
                    block_id=next_id("refempty"),
                    kind="paragraph",
                    section_id="references",
                    title="",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=26.0,
                )
            )
    if ref_sec and getattr(ref_sec, "custom_tables", None):
        for ct in ref_sec.custom_tables:
            if not ct.headers and not ct.rows:
                continue
            clean_ct_title = clean_caption_title(ct.title, doc_labels["table_seq"]) or ct.title
            ct_h = _measure_table_height_pt(ct.headers, ct.rows, body_pt, usable_width_pt, has_caption=True)
            blocks.append(
                LayoutBlock(
                    block_id=next_id("rctbl"),
                    kind="table_with_caption",
                    section_id="references",
                    title=f"{doc_labels['table_seq']} {table_idx} : {clean_ct_title}",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=ct_h,
                    keep_together=ct_h <= usable_height_pt * 0.92,
                )
            )
            table_idx += 1

    # 4. Annexes (grand framed title centered on its own page -> each Annexe starts on a fresh page)
    blocks.append(
        LayoutBlock(
            block_id=next_id("chap"),
            kind="grand_title_frame",
            section_id="annexes",
            title=chap_titles["annexes"],
            toc_level=1,
            stream="arabic",
            estimated_height_pt=usable_height_pt,
            page_break_before=True,
            page_break_after=True,
            isolated_full_page=True,
            vertical_align="center",
        )
    )
    annexes = build_annexes_pack(project)
    for idx_ann, ann in enumerate(annexes):
        blocks.append(
            LayoutBlock(
                block_id=next_id("ann"),
                kind="annexe_start",
                section_id="annexes",
                title=ann["title"],
                toc_level=2,
                stream="arabic",
                estimated_height_pt=_measure_paragraph_height_pt(ann["title"], 13.0, spacing, usable_width_pt),
                page_break_before=(idx_ann > 0),  # First annex already follows grand title page break
                keep_with_next=True,
            )
        )
        if ann.get("kind") == "data_collection":
            blocks.append(
                LayoutBlock(
                    block_id=next_id("annhdr"),
                    kind="table_inline",
                    section_id="annexes",
                    title="En-tête Fiche de Collecte",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=135.0,
                    keep_together=True,
                )
            )
            for b_title, vars_list in ann.get("grouped_variables", {}).items():
                rows = [
                    [
                        f"{v.get('code', '')}. {v.get('name', '')}".strip(". "),
                        v.get("modalities", "") + (f"\n▸ {v.get('sub_field_label', '')}" if v.get("sub_field_label") else ""),
                    ]
                    for v in vars_list
                ]
                th = _measure_table_height_pt([b_title, "Contrôle"], rows, max(9.5, body_pt - 1.5), usable_width_pt, has_caption=False)
                blocks.append(
                    LayoutBlock(
                        block_id=next_id("anntbl"),
                        kind="table_inline",
                        section_id="annexes",
                        title=b_title,
                        toc_level=0,
                        stream="arabic",
                        estimated_height_pt=th,
                        keep_together=th <= usable_height_pt * 0.92,
                    )
                )
        else:
            for p_txt in ann.get("paragraphs", []):
                blocks.append(
                    LayoutBlock(
                        block_id=next_id("annp"),
                        kind="paragraph",
                        section_id="annexes",
                        title="",
                        toc_level=0,
                        stream="arabic",
                        estimated_height_pt=_measure_paragraph_height_pt(p_txt, body_pt, spacing, usable_width_pt),
                    )
                )

    ann_sec = project.sections.get("annexes")
    if ann_sec and getattr(ann_sec, "custom_tables", None):
        for ct in ann_sec.custom_tables:
            if not ct.headers and not ct.rows:
                continue
            clean_ct_title = clean_caption_title(ct.title, "Tableau") or ct.title
            ct_h = _measure_table_height_pt(ct.headers, ct.rows, body_pt, usable_width_pt, has_caption=True)
            blocks.append(
                LayoutBlock(
                    block_id=next_id("annctbl"),
                    kind="table_with_caption",
                    section_id="annexes",
                    title=f"Tableau {table_idx} : {clean_ct_title}",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=ct_h,
                    keep_together=ct_h <= usable_height_pt * 0.92,
                )
            )
            table_idx += 1

    if ann_sec and getattr(ann_sec, "custom_diagrams", None):
        for dg in ann_sec.custom_diagrams:
            if not dg.nodes:
                continue
            png_candidate = diag_dir / f"{dg.diagram_id}.png"
            sz = compute_minimum_legible_figure_size_cm(
                diagram=dg,
                image_path=png_candidate if png_candidate.exists() else None,
                rules=fmt,
                min_font_pt=12.0,
            )
            fig_dims[dg.diagram_id] = sz
            fig_h_pt = float(sz["height_cm"]) * PT_PER_CM + 36.0
            clean_dg_title = clean_caption_title(dg.title, "Figure") or dg.title
            blocks.append(
                LayoutBlock(
                    block_id=next_id("annfigdg"),
                    kind="figure_with_caption",
                    section_id="annexes",
                    title=f"Figure {figure_idx} : {clean_dg_title}",
                    toc_level=0,
                    stream="arabic",
                    estimated_height_pt=round(fig_h_pt, 2),
                    keep_together=True,
                    metadata={"figure_key": dg.diagram_id, **sz},
                )
            )
            figure_idx += 1

    return blocks, fig_dims


def _simulate_stream(
    stream_blocks: List[LayoutBlock],
    usable_height_pt: float,
    stream: str,
    force_break_ids: Optional[set[str]] = None,
) -> Tuple[List[PagePlan], List[Dict[str, Any]]]:
    """Pack ordered blocks onto physical A4 pages following Word vertical flow rules."""
    forced = force_break_ids or set()
    pages: List[PagePlan] = []
    borderline: List[Dict[str, Any]] = []

    cur_idx = 1
    used_h = 0.0
    cur_blocks: List[str] = []
    cur_titles: List[str] = []
    cur_notes: List[str] = []

    def label_for(n: int) -> str:
        return int_to_roman(n) if stream == "roman" else str(n)

    def flush_page() -> None:
        nonlocal cur_idx, used_h, cur_blocks, cur_titles, cur_notes
        if not cur_blocks and used_h == 0.0:
            return
        pages.append(
            PagePlan(
                page_index=cur_idx,
                page_label=label_for(cur_idx),
                stream=stream,
                usable_height_pt=round(usable_height_pt, 2),
                used_height_pt=round(min(usable_height_pt, used_h), 2),
                remaining_height_pt=round(max(0.0, usable_height_pt - used_h), 2),
                block_ids=cur_blocks,
                summary_titles=cur_titles,
                rule_notes=cur_notes,
            )
        )
        cur_idx += 1
        used_h = 0.0
        cur_blocks = []
        cur_titles = []
        cur_notes = []

    n = len(stream_blocks)
    for i, blk in enumerate(stream_blocks):
        if (blk.page_break_before or blk.block_id in forced) and (used_h > 0.0 or cur_blocks):
            flush_page()

        if blk.isolated_full_page:
            cur_blocks.append(blk.block_id)
            if blk.title:
                cur_titles.append(blk.title)
            if blk.kind == "grand_title_frame":
                cur_notes.append(f"Grand titre encadré centré seul sur page ({blk.title})")
            used_h = usable_height_pt
            flush_page()
            continue

        # Lookahead keep_with_next requirement (e.g. heading must fit with first 2 lines or following kept table/fig)
        required_h = blk.estimated_height_pt
        if blk.keep_with_next and i + 1 < n:
            nxt = stream_blocks[i + 1]
            if not nxt.page_break_before and not nxt.isolated_full_page:
                if nxt.keep_together:
                    required_h += nxt.estimated_height_pt
                else:
                    required_h += min(nxt.estimated_height_pt, MIN_WIDOW_ORPHAN_PT)

        remaining = usable_height_pt - used_h
        is_kept_unit = blk.keep_with_next or blk.keep_together

        if is_kept_unit:
            if used_h > 0.0 and required_h > remaining:
                flush_page()
                cur_notes.append(f"keep_with_next/keep_together: {blk.title or blk.kind}")
            elif used_h > 0.0 and abs(remaining - required_h) <= 35.0 and blk.title:
                borderline.append(
                    {
                        "block_id": blk.block_id,
                        "kind": blk.kind,
                        "title": blk.title[:90],
                        "page_label": label_for(cur_idx),
                        "required_pt": round(required_h, 1),
                        "remaining_pt": round(remaining, 1),
                    }
                )

            cur_blocks.append(blk.block_id)
            if blk.title:
                cur_titles.append(blk.title)
            used_h += blk.estimated_height_pt
            # Multi-page single kept item fallback (e.g. giant bibliography table)
            while used_h > usable_height_pt + 1.0:
                overflow = used_h - usable_height_pt
                flush_page()
                used_h = overflow
                cur_blocks.append(blk.block_id)
        else:
            # Multi-line prose / multi-page front-matter / references: split across page boundary when height exceeds remaining
            if used_h > 0.0 and blk.estimated_height_pt > remaining and remaining < MIN_WIDOW_ORPHAN_PT:
                flush_page()
                remaining = usable_height_pt

            if blk.estimated_height_pt > remaining:
                cur_blocks.append(blk.block_id)
                if blk.title:
                    cur_titles.append(blk.title)
                spill = blk.estimated_height_pt - remaining
                used_h = usable_height_pt
                flush_page()
                while spill > usable_height_pt + 1.0:
                    cur_blocks.append(blk.block_id)
                    used_h = usable_height_pt
                    flush_page()
                    spill -= usable_height_pt
                if spill > 0.5:
                    cur_blocks.append(blk.block_id)
                    used_h = spill
            else:
                cur_blocks.append(blk.block_id)
                if blk.title:
                    cur_titles.append(blk.title)
                used_h += blk.estimated_height_pt

        if blk.page_break_after and (used_h > 0.0 or cur_blocks):
            flush_page()

    flush_page()
    return pages, borderline


def plan_document_pagination(
    project: ProtocolProject,
    use_gemini_decision: bool = False,
) -> DocumentPaginationPlan:
    """Compute physical A4 pagination for Roman front-matter and Arabic body/back-matter.

    Optionally consults Gemini Decision 1 on borderline page-split candidates.
    """
    fmt = project.formatting
    margin_cm = float(fmt.margin_cm or 2.5)
    usable_width_cm = round(max(8.0, A4_WIDTH_CM - 2.0 * margin_cm), 2)
    usable_height_cm = round(max(10.0, A4_HEIGHT_CM - 2.0 * margin_cm), 2)
    usable_width_pt = round(usable_width_cm * PT_PER_CM, 2)
    usable_height_pt = round(usable_height_cm * PT_PER_CM, 2)

    all_blocks, fig_dims = _build_layout_blocks(project)
    roman_blocks = [b for b in all_blocks if b.stream == "roman"]
    arabic_blocks = [b for b in all_blocks if b.stream == "arabic"]

    roman_pages, _ = _simulate_stream(roman_blocks, usable_height_pt, stream="roman")
    arabic_pages, borderline = _simulate_stream(arabic_blocks, usable_height_pt, stream="arabic")

    model_used = "deterministic-a4-solver"
    decision_notes = "Allocation géométrique A4 exacte appliquée."
    force_breaks: set[str] = set()

    if use_gemini_decision:
        payload = {
            "page_format": "A4",
            "margin_cm": margin_cm,
            "usable_width_pt": usable_width_pt,
            "usable_height_pt": usable_height_pt,
            "total_roman_pages": len(roman_pages),
            "total_arabic_pages": len(arabic_pages),
            "borderline_candidates": borderline[:18],
            "figures_legibility_min_pt": 12.0,
            "figures_checked": len(fig_dims),
        }
        decision, model_used = gemini_router.decide_page_layout(payload)
        decision_notes = decision.get("notes") or decision_notes
        valid_ids = {b.block_id for b in arabic_blocks}
        force_breaks = {
            bid for bid in (decision.get("move_to_next_page_block_ids") or []) if bid in valid_ids
        }
        if force_breaks:
            arabic_pages, _ = _simulate_stream(
                arabic_blocks,
                usable_height_pt,
                stream="arabic",
                force_break_ids=force_breaks,
            )

    # Map every block_id to the first page where it starts
    block_by_id = {b.block_id: b for b in all_blocks}
    block_start_page: Dict[str, str] = {}
    all_pages = roman_pages + arabic_pages
    for pg in all_pages:
        for bid in pg.block_ids:
            if bid not in block_start_page:
                block_start_page[bid] = pg.page_label

    section_start_page: Dict[str, str] = {}
    heading_page_map: Dict[str, str] = {}
    table_page_map: Dict[str, str] = {}
    figure_page_map: Dict[str, str] = {}

    for blk in all_blocks:
        p_lbl = block_start_page.get(blk.block_id)
        if not p_lbl:
            continue
        if blk.kind in ("front_matter", "grand_title_frame", "heading_2", "annexe_start"):
            if blk.section_id and blk.section_id not in section_start_page:
                section_start_page[blk.section_id] = p_lbl
        if blk.toc_level > 0 and blk.title:
            h_key = f"{blk.section_id}::{blk.title}"
            if h_key not in heading_page_map:
                heading_page_map[h_key] = p_lbl
        if blk.kind == "table_with_caption" and blk.title:
            table_page_map[blk.title] = p_lbl
        if blk.kind == "figure_with_caption" and blk.title:
            figure_page_map[blk.title] = p_lbl

    return DocumentPaginationPlan(
        page_size=fmt.page_size or "A4",
        margin_cm=margin_cm,
        usable_width_cm=usable_width_cm,
        usable_height_cm=usable_height_cm,
        usable_width_pt=usable_width_pt,
        usable_height_pt=usable_height_pt,
        total_roman_pages=len(roman_pages),
        total_arabic_pages=len(arabic_pages),
        model_used=model_used,
        decision_notes=decision_notes,
        section_start_page=section_start_page,
        heading_page_map=heading_page_map,
        table_page_map=table_page_map,
        figure_page_map=figure_page_map,
        figure_dimensions_cm=fig_dims,
        break_before_block_ids=sorted(force_breaks),
        pages=all_pages,
    )
