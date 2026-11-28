"""Deterministic formatter for academic Markdown, headings, lists, and math formulas.

Converts LLM Markdown + LaTeX math ($...$, $$...$$) into:
1. Clean HTML (zero raw `#` or `$` / `$$`) for TipTap RichTextEditor & Module 2 live preview.
2. Word-compatible runs & native Office MathML (<m:oMath>) for `.docx` export.
"""

from html import escape
import re
from typing import Dict, List, Tuple


LATEX_SYMBOLS: Dict[str, str] = {
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\theta": "θ",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\pi": "π",
    r"\rho": "ρ",
    r"\sigma": "σ",
    r"\chi": "χ",
    r"\omega": "ω",
    r"\Delta": "Δ",
    r"\Sigma": "Σ",
    r"\Omega": "Ω",
    r"\cdot": "·",
    r"\times": "×",
    r"\div": "÷",
    r"\pm": "±",
    r"\mp": "∓",
    r"\leq": "≤",
    r"\le": "≤",
    r"\geq": "≥",
    r"\ge": "≥",
    r"\neq": "≠",
    r"\ne": "≠",
    r"\approx": "≈",
    r"\equiv": "≡",
    r"\infty": "∞",
    r"\sum": "∑",
    r"\prod": "∏",
    r"\sqrt": "√",
    r"\rightarrow": "→",
    r"\leftarrow": "←",
    r"\leftrightarrow": "↔",
    r"\Rightarrow": "⇒",
    r"\in": "∈",
    r"\notin": "∉",
    r"\subset": "⊂",
    r"\cup": "∪",
    r"\cap": "∩",
    r"\forall": "∀",
    r"\exists": "∃",
    r"\percent": "%",
    r"\%": "%",
    r"\,": " ",
    r"\;": " ",
    r"\quad": "  ",
}


def _find_matching_brace(s: str, start: int) -> int:
    """Return index of matching '}' for '{' at `start`, or -1."""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _clean_latex_tokens(expr: str) -> str:
    """Normalize common LaTeX spacing, comma braces `{,}`, and Greek/operator symbols."""
    out = expr.strip()
    out = out.replace("{,}", ",")
    out = re.sub(r"\\text\{([^}]*)\}", r"\1", out)
    out = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", out)
    out = re.sub(r"\\mathbf\{([^}]*)\}", r"\1", out)
    # Replace longest keys first to avoid partial prefix matches (e.g. \le vs \leq)
    for k in sorted(LATEX_SYMBOLS.keys(), key=len, reverse=True):
        out = out.replace(k, LATEX_SYMBOLS[k])
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _latex_to_html_inline(expr: str) -> str:
    """Convert a LaTeX math expression into clean HTML with stacked fractions and sub/sup."""
    s = _clean_latex_tokens(expr)

    # Handle \frac{num}{den} recursively (inner-to-outer or outer-to-inner)
    while r"\frac" in s:
        idx = s.find(r"\frac")
        b1 = s.find("{", idx)
        if b1 == -1:
            break
        e1 = _find_matching_brace(s, b1)
        if e1 == -1:
            break
        b2 = s.find("{", e1 + 1)
        if b2 == -1:
            break
        e2 = _find_matching_brace(s, b2)
        if e2 == -1:
            break
        num_html = _latex_to_html_inline(s[b1 + 1 : e1])
        den_html = _latex_to_html_inline(s[b2 + 1 : e2])
        frac_html = (
            '<span style="display:inline-flex;flex-direction:column;align-items:center;'
            'vertical-align:middle;margin:0 3px;line-height:1.15;">'
            f'<span style="border-bottom:1px solid currentColor;padding:0 4px;">{num_html}</span>'
            f'<span style="padding:0 4px;">{den_html}</span>'
            "</span>"
        )
        s = s[:idx] + frac_html + s[e2 + 1 :]

    # Protect already-built HTML tags while converting ^{...}, ^x, _{...}, _x
    placeholders: List[str] = []

    def _stash(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return f"@@HTMLTAG{len(placeholders) - 1}@@"

    s = re.sub(r"<span\b[^>]*>.*?</span>", _stash, s)
    # Replace ^ and _ with <sup> and <sub>
    s = re.sub(r"\^\{([^}]+)\}", lambda m: f"<sup>{escape(m.group(1))}</sup>", s)
    s = re.sub(r"\^([A-Za-z0-9α-ωΑ-Ω])", lambda m: f"<sup>{escape(m.group(1))}</sup>", s)
    s = re.sub(r"_\{([^}]+)\}", lambda m: f"<sub>{escape(m.group(1))}</sub>", s)
    s = re.sub(r"_([A-Za-z0-9α-ωΑ-Ω])", lambda m: f"<sub>{escape(m.group(1))}</sub>", s)

    # Restore stashed spans
    for idx_p, tag in enumerate(placeholders):
        s = s.replace(f"@@HTMLTAG{idx_p}@@", tag)

    return s


def latex_to_plain_math(expr: str) -> str:
    """Convert LaTeX math into clean Unicode academic notation (for Word fallback / plain text)."""
    s = _clean_latex_tokens(expr)
    while r"\frac" in s:
        idx = s.find(r"\frac")
        b1 = s.find("{", idx)
        if b1 == -1:
            break
        e1 = _find_matching_brace(s, b1)
        if e1 == -1:
            break
        b2 = s.find("{", e1 + 1)
        if b2 == -1:
            break
        e2 = _find_matching_brace(s, b2)
        if e2 == -1:
            break
        num = latex_to_plain_math(s[b1 + 1 : e1])
        den = latex_to_plain_math(s[b2 + 1 : e2])
        if any(op in num for op in (" ", "+", "-", "·", "×")):
            num = f"({num})"
        if any(op in den for op in (" ", "+", "-", "·", "×")):
            den = f"({den})"
        s = s[:idx] + f"{num} / {den}" + s[e2 + 1 :]
    s = re.sub(r"\^\{([^}]+)\}", r"^(\1)", s)
    s = re.sub(r"_\{([^}]+)\}", r"_(\1)", s)
    s = s.replace("^(2)", "²").replace("^2", "²").replace("^(3)", "³").replace("^3", "³")
    return s


def _inline_markdown_to_html(text: str) -> str:
    """Convert inline math ($...$), **bold**, *italic* into clean HTML."""
    math_tokens: List[str] = []

    def _stash_math(m: re.Match) -> str:
        inner = m.group(1)
        rendered = _latex_to_html_inline(inner)
        math_tokens.append(f'<span class="math-inline" style="font-style:italic;">{rendered}</span>')
        return f"@@MATH{len(math_tokens) - 1}@@"

    # Stash inline $...$ (single dollar, not $$)
    processed = re.sub(r"(?<!\$)\$(?!\$)([^$\n]+?)(?<!\$)\$(?!\$)", _stash_math, text)

    # Escape HTML safely before applying **bold** / *italic*
    processed = escape(processed)
    processed = re.sub(r"\*\*([^*\n]+?)\*\*", r"<strong>\1</strong>", processed)
    processed = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", processed)

    for i, tok in enumerate(math_tokens):
        processed = processed.replace(f"@@MATH{i}@@", tok)
    return processed


def strip_ascii_diagrams(text: str) -> str:
    """Remove accidental ASCII box diagrams (+---+, | ... |) if a model still emits them."""
    lines = text.splitlines()
    cleaned: List[str] = []
    in_ascii_block = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("```"):
            in_ascii_block = not in_ascii_block
            continue
        if in_ascii_block:
            continue
        if re.match(r"^\+[-+=]+\+$", s):
            continue
        # Only strip ASCII box-drawing lines (e.g. |   Box   | without inner cell separators),
        # NEVER strip real Markdown pipe tables (which have 2+ cells separated by `|`).
        if s.startswith("|") and s.endswith("|") and len(s) > 4:
            inner_cells = [c.strip() for c in s[1:-1].split("|")]
            if len(inner_cells) < 2:
                continue
        if s in ("v", "^", "|"):
            continue
        cleaned.append(ln)
    return "\n".join(cleaned)


_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?$")


def _split_pipe_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


def _is_pipe_table_line(line: str) -> bool:
    s = line.strip()
    if not s or "|" not in s:
        return False
    if _TABLE_SEP_RE.match(s):
        return True
    cells = _split_pipe_row(s)
    # Must have at least 2 non-empty cells (or 3+ columns) and not be a prose sentence with an isolated pipe
    non_empty = [c for c in cells if c]
    if len(cells) >= 3 and len(non_empty) >= 2:
        return True
    if len(cells) == 2 and len(non_empty) == 2:
        # Allow any explicit Markdown pipe row `| col1 | col2 |` regardless of length;
        # for bare `col1 | col2` without outer pipes, allow up to 600 chars.
        if (s.startswith("|") and s.endswith("|")) or len(s) <= 600:
            return True
    return False


def extract_markdown_tables_to_structured(
    markdown_text: str,
    fallback_title: str = "Tableau récapitulatif",
) -> Tuple[str, List[Dict[str, object]]]:
    """Deterministically extract any raw Markdown pipe tables from prose into structured table dicts.

    Returns `(cleaned_markdown, extracted_tables)` where each table dict has:
      {"title": str, "anchor_hint": str, "headers": List[str], "rows": List[List[str]]}
    """
    raw = (markdown_text or "")
    if "|" not in raw:
        return raw, []

    lines = raw.splitlines()
    out_lines: List[str] = []
    extracted: List[Dict[str, object]] = []
    last_heading = ""
    last_para = ""

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()

        if _is_pipe_table_line(s):
            block: List[str] = []
            j = i
            while j < n and _is_pipe_table_line(lines[j]):
                block.append(lines[j].strip())
                j += 1

            # Require at least 2 non-separator rows (1 header + >= 1 data row)
            data_rows = [b for b in block if not _TABLE_SEP_RE.match(b)]
            if len(data_rows) >= 2:
                headers = _split_pipe_row(data_rows[0])
                col_count = len(headers)
                if col_count >= 2:
                    parsed_rows: List[List[str]] = []
                    for row_line in data_rows[1:]:
                        cells = _split_pipe_row(row_line)
                        if len(cells) == col_count:
                            parsed_rows.append(cells)
                        elif len(cells) < col_count:
                            # Right-align 2-cell summary rows like "SOUS-TOTAL | 3 796 000" into last column
                            if len(cells) == 2 and col_count >= 3:
                                parsed_rows.append([cells[0]] + [""] * (col_count - 2) + [cells[1]])
                            else:
                                parsed_rows.append(cells + [""] * (col_count - len(cells)))
                        else:
                            parsed_rows.append(cells[: col_count - 1] + [" | ".join(cells[col_count - 1 :])])

                    clean_heading = re.sub(r"^#{1,6}\s+", "", last_heading).strip()
                    clean_heading = re.sub(r"^\*+|\*+$", "", clean_heading).strip()
                    tbl_title = clean_heading or fallback_title
                    if len(extracted) > 0 and any(str(t["title"]).lower() == tbl_title.lower() for t in extracted):
                        tbl_title = f"{tbl_title} ({len(extracted) + 1})"

                    extracted.append(
                        {
                            "title": tbl_title,
                            "anchor_hint": (last_para or clean_heading)[:140],
                            "headers": headers,
                            "rows": parsed_rows,
                        }
                    )
                    i = j
                    continue

        # Track preceding heading & paragraph for title/anchor_hint inference
        if s:
            if re.match(r"^(?:#{1,6}\s+|[A-Z]\.\s+|[IVX]+\.\s+|Tableau\s)", s) and len(s) <= 120:
                last_heading = s
            elif len(s) >= 20:
                last_para = s
        out_lines.append(line)
        i += 1

    cleaned_md = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    return cleaned_md, extracted


def _render_pipe_table_html(block_lines: List[str]) -> str:
    """Fallback HTML <table> renderer if raw pipe table lines reach markdown_to_clean_html directly."""
    data_rows = [b for b in block_lines if not _TABLE_SEP_RE.match(b)]
    if len(data_rows) < 2:
        return "".join(f"<p>{_inline_markdown_to_html(b)}</p>" for b in block_lines)
    headers = _split_pipe_row(data_rows[0])
    col_count = len(headers)
    th_html = "".join(
        f'<th style="background:#6C2826;color:#FFFFFF;padding:6px 8px;border:1px solid #cbd5e1;text-align:left;font-weight:700;">'
        f'{_inline_markdown_to_html(h)}</th>'
        for h in headers
    )
    body_rows_html: List[str] = []
    for r_idx, r_line in enumerate(data_rows[1:]):
        cells = _split_pipe_row(r_line)
        if len(cells) < col_count:
            if len(cells) == 2 and col_count >= 3:
                cells = [cells[0]] + [""] * (col_count - 2) + [cells[1]]
            else:
                cells = cells + [""] * (col_count - len(cells))
        elif len(cells) > col_count:
            cells = cells[: col_count - 1] + [" | ".join(cells[col_count - 1 :])]
        bg = "#F8FAFC" if r_idx % 2 == 1 else "#FFFFFF"
        tds = "".join(
            f'<td style="padding:6px 8px;border:1px solid #cbd5e1;background:{bg};">{_inline_markdown_to_html(c)}</td>'
            for c in cells
        )
        body_rows_html.append(f"<tr>{tds}</tr>")
    return (
        '<table style="width:100%;border-collapse:collapse;margin:10px 0;font-size:13px;">'
        f"<thead><tr>{th_html}</tr></thead>"
        f"<tbody>{''.join(body_rows_html)}</tbody></table>"
    )


def markdown_to_clean_html(markdown_text: str) -> str:
    """Deterministically convert Markdown + LaTeX math into clean TipTap/HTML (zero `#` or `$`)."""
    raw = strip_ascii_diagrams(markdown_text or "").strip()
    if not raw:
        return ""

    # First extract display math blocks $$...$$ (single or multi-line)
    block_maths: List[str] = []

    def _stash_block_math(m: re.Match) -> str:
        rendered = _latex_to_html_inline(m.group(1))
        block_maths.append(
            f'<p style="text-align:center;font-style:italic;margin:8px 0;">{rendered}</p>'
        )
        return f"\n\n@@BLOCKMATH{len(block_maths) - 1}@@\n\n"

    raw = re.sub(r"\$\$([\s\S]+?)\$\$", _stash_block_math, raw)

    lines = raw.splitlines()
    html_parts: List[str] = []
    in_ul = False
    in_ol = False
    para_buf: List[str] = []
    tbl_buf: List[str] = []

    def _flush_table() -> None:
        nonlocal tbl_buf
        if tbl_buf:
            html_parts.append(_render_pipe_table_html(tbl_buf))
            tbl_buf = []

    def _flush_para() -> None:
        nonlocal para_buf
        if para_buf:
            joined = " ".join(p.strip() for p in para_buf if p.strip())
            if joined:
                html_parts.append(f"<p>{_inline_markdown_to_html(joined)}</p>")
            para_buf = []

    def _close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            html_parts.append("</ul>")
            in_ul = False
        if in_ol:
            html_parts.append("</ol>")
            in_ol = False

    for line in lines:
        s = line.strip()
        if not s:
            _flush_table()
            _flush_para()
            _close_lists()
            continue

        if _is_pipe_table_line(s):
            _flush_para()
            _close_lists()
            tbl_buf.append(s)
            continue
        else:
            _flush_table()

        if s.startswith("@@BLOCKMATH") and s.endswith("@@"):
            _flush_para()
            _close_lists()
            idx = int(s.replace("@@BLOCKMATH", "").replace("@@", ""))
            html_parts.append(block_maths[idx])
            continue

        # Headings: #, ##, ###, #### -> clean <h3> or <h4> without '#'
        m_head = re.match(r"^(#{1,6})\s+(.+)$", s)
        if m_head:
            _flush_para()
            _close_lists()
            level = len(m_head.group(1))
            tag = "h3" if level <= 3 else "h4"
            html_parts.append(f"<{tag}><strong>{_inline_markdown_to_html(m_head.group(2))}</strong></{tag}>")
            continue

        # Unordered list (- , * , • )
        m_ul = re.match(r"^(?:[-*•])\s+(.+)$", s)
        if m_ul:
            _flush_para()
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            if not in_ul:
                html_parts.append("<ul>")
                in_ul = True
            html_parts.append(f"<li>{_inline_markdown_to_html(m_ul.group(1))}</li>")
            continue

        # Ordered list (1. , 2. )
        m_ol = re.match(r"^\d+\.\s+(.+)$", s)
        if m_ol:
            _flush_para()
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if not in_ol:
                html_parts.append("<ol>")
                in_ol = True
            html_parts.append(f"<li>{_inline_markdown_to_html(m_ol.group(1))}</li>")
            continue

        _close_lists()
        para_buf.append(s)

    _flush_table()
    _flush_para()
    _close_lists()
    return "\n".join(html_parts)


def split_math_segments(text: str) -> List[Tuple[str, str]]:
    """Split text into [('text', ...), ('block_math', ...), ('inline_math', ...)] for Word rendering."""
    pattern = re.compile(r"(\$\$[\s\S]+?\$\$|(?<!\$)\$(?!\$)[^$\n]+?(?<!\$)\$(?!\$))")
    segments: List[Tuple[str, str]] = []
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            segments.append(("text", text[pos : m.start()]))
        tok = m.group(0)
        if tok.startswith("$$"):
            segments.append(("block_math", tok[2:-2].strip()))
        else:
            segments.append(("inline_math", tok[1:-1].strip()))
        pos = m.end()
    if pos < len(text):
        segments.append(("text", text[pos:]))
    return segments
