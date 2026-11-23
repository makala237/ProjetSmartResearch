"""Unified Budget Prévisionnel Engine (Single Source of Truth across Modules 1, 2 & 4).

Handles:
1. Structured AI generation of FMSB/CAMES provisional budgets (with deterministic Python arithmetic).
2. Universal extraction of BudgetItem rows from chap3_budget (custom_tables, Markdown tables, or bulleted text).
3. Authoritative resolution & synchronization across:
   - Module 1 (chap3_budget section Markdown)
   - Module 2 (project.budget -> "Éditer les tableaux et annexes" + Word/PDF export)
   - Module 4 (PowerPoint "Budget prévisionnel" slide)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.data.i18n import normalize_lang
from app.schemas.protocol import BudgetItem, ProtocolProject, SectionState
from app.services.gemini_client import gemini_router

logger = logging.getLogger(__name__)


class BudgetItemDraft(BaseModel):
    category: str = Field(default="Matériel & Logistique")
    designation: str
    quantity: float = 1.0
    unit_cost_fcfa: float = 0.0


class StructuredBudgetOut(BaseModel):
    intro_paragraph: str = ""
    items: List[BudgetItemDraft] = Field(default_factory=list)
    include_contingency_10pct: bool = True
    closing_note: str = ""
    assistant_commentary: str = ""


_TOTAL_ROW_RE = re.compile(
    r"^\s*(sous[-\s]?total|sub[-\s]?total|total(\s+g[eé]n[eé]ral|\s+estim[eé]|\s+global|\s+budget)?|grand\s+total|montant\s+total)\b",
    flags=re.IGNORECASE,
)

_HEADER_CELL_RE = re.compile(
    r"^\s*(n[°o]?|#|cat[eé]gorie|rubrique|poste|d[eé]signation|libell[eé]|item|description|"
    r"quantit[eé]|qt[eé]|qty|unit[eé]|prix\s*unitaire|pu|unit\s*(cost|price)|"
    r"prix\s*total|montant|total(\s*\(fcfa\))?|fcfa|co[uû]t)\b",
    flags=re.IGNORECASE,
)

_CONTINGENCY_RE = re.compile(r"impr[eé]vu|contingenc", flags=re.IGNORECASE)


def format_fcfa(amount: float) -> str:
    """Format a FCFA amount with clean thousands spaces (e.g. '450 000 FCFA')."""
    val = int(round(max(0.0, float(amount or 0.0))))
    return f"{val:,} FCFA".replace(",", " ")


def parse_fcfa_number(raw: Any) -> float:
    """Robustly parse FCFA amounts & quantities like '150 000 FCFA', '150.000', '150,000', '25k'."""
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw or "").strip()
    if not s:
        return 0.0
    # Strip currency tokens & non-breaking spaces
    s_clean = re.sub(r"\b(f\.?\s*cfa|fcfa|xaf|cfa)\b", "", s, flags=re.IGNORECASE)
    s_clean = s_clean.replace("\u00a0", " ").replace("\u202f", " ").strip()

    # Handle shorthand like "150k" / "1.5M"
    m_short = re.search(r"(\d+(?:[.,]\d+)?)\s*([kKmM])\b", s_clean)
    if m_short:
        num = float(m_short.group(1).replace(",", "."))
        mult = 1_000.0 if m_short.group(2).lower() == "k" else 1_000_000.0
        return num * mult

    # Extract first numeric token allowing spaces/dots/commas as thousand separators
    m_num = re.search(r"\d[\d\s.,]*", s_clean)
    if not m_num:
        return 0.0
    token = m_num.group(0).strip().rstrip("., ")
    # Remove internal spaces (French thousands separator)
    compact = re.sub(r"\s+", "", token)
    # If pattern is like 150.000 or 1.250.000 (3 digits after dot/comma) -> thousands separator
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", compact):
        compact = compact.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", compact):
        compact = compact.replace(",", "")
    else:
        # Otherwise treat comma as decimal separator if single comma with <=2 decimals
        if compact.count(",") == 1 and "." not in compact:
            parts = compact.split(",")
            compact = "".join(parts) if len(parts[1]) == 3 else ".".join(parts)
        else:
            compact = compact.replace(",", "")
    try:
        return float(compact)
    except ValueError:
        digits = re.sub(r"[^\d]", "", compact)
        return float(digits) if digits else 0.0


def normalize_budget_items(items: List[BudgetItem]) -> List[BudgetItem]:
    """Clean, deduplicate, drop total rows, and deterministically recompute FCFA totals."""
    normalized: List[BudgetItem] = []
    seen: set[str] = set()
    for raw in items or []:
        des = re.sub(r"[*_`]+", "", (raw.designation or "").strip()).strip()
        if not des or _TOTAL_ROW_RE.match(des) or _HEADER_CELL_RE.fullmatch(des):
            continue
        cat = re.sub(r"[*_`]+", "", (raw.category or "").strip()).strip() or "Matériel & Logistique"
        qty = float(raw.quantity or 0.0)
        pu = float(raw.unit_cost_fcfa or 0.0)
        tot = float(raw.total_fcfa or 0.0)

        if qty <= 0:
            qty = 1.0
        if pu > 0:
            tot = round(qty * pu)
        elif tot > 0:
            pu = round(tot / qty, 2)
            tot = round(tot)
        else:
            # Skip zero-value placeholder lines
            continue

        key = f"{cat.lower()}::{des.lower()}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            BudgetItem(
                category=cat,
                designation=des,
                quantity=qty,
                unit_cost_fcfa=pu,
                total_fcfa=tot,
            )
        )
    return normalized


def compute_budget_total(items: List[BudgetItem]) -> float:
    """Compute exact grand total in FCFA from normalized BudgetItems."""
    total = 0.0
    for b in items or []:
        line = b.total_fcfa if (b.total_fcfa and b.total_fcfa > 0) else (b.quantity * b.unit_cost_fcfa)
        total += max(0.0, float(line or 0.0))
    return round(total)


def _extract_items_from_table_grid(
    headers: List[str],
    rows: List[List[str]],
    default_category: str = "Matériel & Logistique",
) -> List[BudgetItem]:
    """Parse any 2-, 3-, 4-, or 5-column budget table cleanly using header inference."""
    norm_headers = [re.sub(r"[*_`]+", "", (h or "").strip()).lower() for h in (headers or [])]
    cat_idx: Optional[int] = None
    des_idx: Optional[int] = None
    qty_idx: Optional[int] = None
    pu_idx: Optional[int] = None
    tot_idx: Optional[int] = None

    for i, h in enumerate(norm_headers):
        if any(k in h for k in ("catégorie", "categorie", "category", "rubrique")) and cat_idx is None:
            cat_idx = i
        elif any(k in h for k in ("désignation", "designation", "libellé", "libelle", "poste", "item", "description")) and des_idx is None:
            des_idx = i
        elif any(k in h for k in ("quantité", "quantite", "qté", "qte", "qty")) and qty_idx is None:
            qty_idx = i
        elif any(k in h for k in ("unitaire", "unit price", "unit cost", "p.u", "pu")) and pu_idx is None:
            pu_idx = i
        elif any(k in h for k in ("total", "montant", "amount", "coût", "cout")) and tot_idx is None:
            tot_idx = i

    extracted: List[BudgetItem] = []
    current_cat = default_category

    for raw_row in rows or []:
        cols = [re.sub(r"[*_`]+", "", str(c or "").strip()).strip() for c in raw_row]
        if not any(cols):
            continue

        # Skip separator rows (---) or repeated header rows
        if all(re.fullmatch(r"[-:\s]+", c) for c in cols if c):
            continue
        first_non_empty = next((c for c in cols if c), "")
        if _TOTAL_ROW_RE.match(first_non_empty):
            continue
        if any(_TOTAL_ROW_RE.match(c) for c in cols[:2] if c):
            continue

        # Check if row is a header repeat
        if len(cols) >= 2 and _HEADER_CELL_RE.match(cols[0]) and _HEADER_CELL_RE.match(cols[1]):
            continue

        # Determine designation & category
        n = len(cols)
        if des_idx is not None and des_idx < n:
            des = cols[des_idx]
            cat = cols[cat_idx] if (cat_idx is not None and cat_idx < n and cols[cat_idx]) else current_cat
        elif cat_idx is not None and cat_idx == 0 and n >= 3:
            cat = cols[0] or current_cat
            des = cols[1]
        elif n >= 5:
            # Common 5-column layout: [Catégorie, Désignation, Qté, PU, Total]
            cat = cols[0] or current_cat
            des = cols[1]
        else:
            # Skip leading numeric index column like "1" or "1."
            if n >= 3 and re.fullmatch(r"\d+\.?", cols[0]):
                des = cols[1]
                cat = current_cat
            else:
                des = cols[0]
                cat = current_cat

        if cat and not re.fullmatch(r"\d+(?:[.,]\d+)?", cat):
            current_cat = cat

        if not des or _TOTAL_ROW_RE.match(des):
            continue

        # Extract qty, unit_cost, total
        qty = parse_fcfa_number(cols[qty_idx]) if (qty_idx is not None and qty_idx < n) else 0.0
        pu = parse_fcfa_number(cols[pu_idx]) if (pu_idx is not None and pu_idx < n) else 0.0
        tot = parse_fcfa_number(cols[tot_idx]) if (tot_idx is not None and tot_idx < n) else 0.0

        # Heuristic fallback when headers didn't specify numeric columns
        if qty == 0.0 and pu == 0.0 and tot == 0.0:
            start_col = 2 if n >= 5 else 1
            nums = [parse_fcfa_number(c) for c in cols[start_col:]]
            non_zero = [v for v in nums if v > 0]
            if len(non_zero) >= 3:
                # Could be [qty, pu, tot] OR [pu, qty, tot]
                a, b, c_val = non_zero[0], non_zero[1], non_zero[-1]
                if abs(a * b - c_val) <= max(10.0, c_val * 0.05):
                    if a <= 500 and b >= a:
                        qty, pu, tot = a, b, c_val
                    else:
                        pu, qty, tot = a, b, c_val
                else:
                    qty, pu, tot = a, b, c_val
            elif len(non_zero) == 2:
                a, b = non_zero[0], non_zero[1]
                if a <= 500 and b >= 500:
                    qty, pu, tot = a, b, a * b
                else:
                    qty, pu, tot = 1.0, b, b
            elif len(non_zero) == 1:
                qty, pu, tot = 1.0, non_zero[0], non_zero[0]

        if qty <= 0:
            qty = 1.0
        if tot <= 0 and pu > 0:
            tot = qty * pu
        if pu <= 0 and tot > 0:
            pu = tot / qty
        if tot <= 0:
            continue

        extracted.append(
            BudgetItem(
                category=current_cat,
                designation=des,
                quantity=qty,
                unit_cost_fcfa=pu,
                total_fcfa=tot,
            )
        )

    return normalize_budget_items(extracted)


def extract_budget_items_from_markdown(markdown_text: str) -> List[BudgetItem]:
    """Extract BudgetItem rows from Markdown pipe tables or bulleted/numbered FCFA lines."""
    raw = (markdown_text or "").strip()
    if not raw:
        return []

    # 1. Parse all Markdown pipe tables first
    lines = raw.splitlines()
    table_items: List[BudgetItem] = []
    idx = 0
    while idx < len(lines):
        if "|" in lines[idx] and lines[idx].strip().startswith("|"):
            block: List[List[str]] = []
            while idx < len(lines) and "|" in lines[idx]:
                ln = lines[idx].strip()
                if not ln:
                    break
                cols = [c.strip() for c in ln.strip("|").split("|")]
                block.append(cols)
                idx += 1
            if len(block) >= 2:
                headers = block[0]
                data_rows = [
                    r for r in block[1:]
                    if not all(re.fullmatch(r"[-:\s]*", c) for c in r)
                ]
                table_items.extend(_extract_items_from_table_grid(headers, data_rows))
        else:
            idx += 1

    if table_items:
        return normalize_budget_items(table_items)

    # 2. Fallback: bulleted/numbered lines with explicit FCFA amounts
    bullet_items: List[BudgetItem] = []
    current_cat = "Matériel & Logistique"
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = re.sub(r"^#+\s*", "", stripped).strip()
            if heading and "budget" not in heading.lower():
                current_cat = heading[:60]
            continue
        if _TOTAL_ROW_RE.search(stripped):
            continue
        # Match lines like "- Impression et reliure : 75 000 FCFA" or "* Réactifs (10 x 5 000) = 50 000 FCFA"
        m_line = re.match(
            r"^(?:[-*•+]|\d+[.)])\s*(.+?)\s*[:=–—-]\s*([\d\s.,]+)\s*(?:f\.?\s*cfa|fcfa|xaf)\b",
            stripped,
            flags=re.IGNORECASE,
        )
        if m_line:
            des = re.sub(r"[*_`]+", "", m_line.group(1)).strip()
            amt = parse_fcfa_number(m_line.group(2))
            if des and amt > 0 and not _TOTAL_ROW_RE.match(des):
                bullet_items.append(
                    BudgetItem(
                        category=current_cat,
                        designation=des,
                        quantity=1.0,
                        unit_cost_fcfa=amt,
                        total_fcfa=amt,
                    )
                )

    return normalize_budget_items(bullet_items)


def extract_budget_items_from_section(section: Optional[SectionState]) -> List[BudgetItem]:
    """Extract BudgetItems from a chap3_budget SectionState (Markdown tables, custom_tables, or bullets)."""
    if section is None:
        return []

    # 1. Try Markdown content first (primary output of Module 1 drafting & user edits)
    md_items = extract_budget_items_from_markdown(section.content_markdown or "")
    if md_items:
        return md_items

    # 2. Try custom_tables attached to the section
    tbl_items: List[BudgetItem] = []
    for ct in getattr(section, "custom_tables", []) or []:
        tbl_items.extend(_extract_items_from_table_grid(ct.headers or [], ct.rows or []))
    if tbl_items:
        return normalize_budget_items(tbl_items)

    return []


def strip_markdown_budget_tables(markdown_text: str) -> str:
    """Remove inline Markdown pipe tables from chap3_budget prose so DOCX doesn't duplicate the structured table."""
    if not markdown_text:
        return ""
    out_lines: List[str] = []
    for ln in markdown_text.splitlines():
        s = ln.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            continue
        out_lines.append(ln)
    cleaned = "\n".join(out_lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def render_budget_markdown(
    items: List[BudgetItem],
    intro_paragraph: str = "",
    closing_note: str = "",
    lang: str = "fr",
) -> str:
    """Render a clean CAMES/FMSB Markdown budget section with exact Python-computed totals."""
    norm = normalize_budget_items(items)
    grand_total = compute_budget_total(norm)
    is_en = normalize_lang(lang) == "en"

    parts: List[str] = []
    if intro_paragraph.strip():
        parts.append(intro_paragraph.strip())
    else:
        parts.append(
            "The provisional budget below details the material, logistical, and operational resources "
            "required to conduct this study. Financing will be covered by the candidate's own funds."
            if is_en
            else "Le budget prévisionnel ci-dessous détaille l'ensemble des ressources matérielles, logistiques et "
            "opérationnelles nécessaires à la réalisation de cette étude. Le financement sera assuré sur fonds propres du candidat."
        )

    if norm:
        if is_en:
            header = "| Category | Item / Description | Quantity | Unit Price (FCFA) | Total (FCFA) |"
            sep = "| :--- | :--- | :---: | :---: | :---: |"
            total_lbl = "**GRAND TOTAL**"
        else:
            header = "| Catégorie | Désignation | Quantité | Prix unitaire (FCFA) | Prix total (FCFA) |"
            sep = "| :--- | :--- | :---: | :---: | :---: |"
            total_lbl = "**TOTAL GÉNÉRAL**"

        rows = [header, sep]
        for b in norm:
            qty_str = f"{int(b.quantity)}" if float(b.quantity).is_integer() else f"{b.quantity:g}"
            pu_str = f"{int(round(b.unit_cost_fcfa)):,}".replace(",", " ")
            tot_str = f"{int(round(b.total_fcfa)):,}".replace(",", " ")
            rows.append(f"| {b.category} | {b.designation} | {qty_str} | {pu_str} | {tot_str} |")
        rows.append(f"| {total_lbl} | | | | **{format_fcfa(grand_total)}** |")
        parts.append("\n".join(rows))

    if closing_note.strip():
        parts.append(closing_note.strip())
    elif norm:
        parts.append(
            f"**Total estimated cost:** {format_fcfa(grand_total)} (funded by the candidate's own resources)."
            if is_en
            else f"**Coût global estimé de l'étude :** {format_fcfa(grand_total)}, entièrement pris en charge sur fonds propres du candidat."
        )

    return "\n\n".join(p for p in parts if p).strip()


def sync_budget_from_section(project: ProtocolProject, overwrite: bool = False) -> int:
    """Extract BudgetItems from chap3_budget and synchronize them into project.budget.

    - overwrite=True: replaces project.budget with the extracted items (when non-empty),
      keeping Module 2 ("Éditer les tableaux et annexes") and Module 4 (PPTX) strictly aligned.
    - overwrite=False: populates project.budget if empty, or appends missing designations.
    Returns the number of budget items synced/added.
    """
    bud_sec = project.sections.get("chap3_budget")
    extracted = extract_budget_items_from_section(bud_sec)
    if not extracted:
        return 0

    if overwrite or not project.budget:
        project.budget = extracted
        return len(extracted)

    existing_keys = {b.designation.strip().lower() for b in project.budget if b.designation.strip()}
    added = 0
    for item in extracted:
        if item.designation.strip().lower() not in existing_keys:
            project.budget.append(item)
            existing_keys.add(item.designation.strip().lower())
            added += 1
    project.budget = normalize_budget_items(project.budget)
    return added


def sync_section_from_budget(project: ProtocolProject) -> None:
    """When project.budget is edited in Module 2, keep chap3_budget's Markdown table coherent."""
    project.budget = normalize_budget_items(project.budget)
    bud_sec = project.sections.get("chap3_budget")
    if bud_sec is None or not project.budget:
        return

    lang = normalize_lang(getattr(project, "language", "fr"))
    # Preserve existing non-table prose paragraphs if any
    existing_prose = strip_markdown_budget_tables(bud_sec.content_markdown or "")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", existing_prose) if p.strip()]
    intro = paragraphs[0] if paragraphs else ""
    closing = paragraphs[-1] if len(paragraphs) > 1 else ""
    # Avoid repeating an outdated total sentence in closing
    if closing and re.search(r"co[uû]t\s+global|total\s+estimated|fcfa", closing, flags=re.IGNORECASE):
        closing = ""

    from app.services.rich_formatter import markdown_to_clean_html

    new_md = render_budget_markdown(project.budget, intro_paragraph=intro, closing_note=closing, lang=lang)
    bud_sec.content_markdown = new_md
    bud_sec.content_html = markdown_to_clean_html(new_md)
    # Remove any stale LLM custom_tables on chap3_budget so Word/PDF always renders project.budget
    bud_sec.custom_tables = []


def resolve_authoritative_budget(project: ProtocolProject) -> Tuple[List[BudgetItem], float]:
    """Single source of truth for Module 2 (Word/PDF) & Module 4 (PowerPoint) budget data."""
    if project.budget:
        norm = normalize_budget_items(project.budget)
        if norm:
            return norm, compute_budget_total(norm)

    bud_sec = project.sections.get("chap3_budget")
    extracted = extract_budget_items_from_section(bud_sec)
    if extracted:
        return extracted, compute_budget_total(extracted)

    return [], 0.0


def generate_structured_budget_with_ai(
    project: ProtocolProject,
    user_instruction: str = "",
) -> Dict[str, Any]:
    """Generate a realistic, CAMES/FMSB-compliant structured budget in FCFA and sync Modules 1, 2 & 4."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    is_en = lang == "en"
    cover = project.cover

    # Gather real protocol context (sample size, procedures, sites)
    pop_excerpt = (project.sections.get("chap3_population").content_markdown if project.sections.get("chap3_population") else "")[:700]
    proc_excerpt = (project.sections.get("chap3_procedure").content_markdown if project.sections.get("chap3_procedure") else "")[:700]
    sites_txt = ", ".join(s.strip() for s in (getattr(cover, "hospital_sites", None) or []) if s.strip())

    existing_budget_hint = ""
    if project.budget:
        lines = [f"- {b.category} | {b.designation} | qty={b.quantity} | pu={b.unit_cost_fcfa}" for b in project.budget[:20]]
        existing_budget_hint = "EXISTING BUDGET ITEMS (update/refine if relevant):\n" + "\n".join(lines)

    prompt = f"""You are a senior FMSB/CAMES medical thesis advisor in Cameroon (Yaoundé).
Generate a realistic, detailed, well-categorized provisional budget in FCFA for this medical thesis protocol.

STUDY CONTEXT:
- Title: {getattr(cover, "protocol_title", "") or project.name}
- Study type: {project.study_type or 'Étude transversale analytique'}
- Faculty / Department: {cover.faculty_fr or cover.faculty_en} / {cover.department_fr or cover.department_en}
- Study site(s): {sites_txt or 'Hôpital universitaire / Cameroun'}
- Population & Sampling excerpt: {pop_excerpt or 'Non encore précisé'}
- Data Collection / Procedures excerpt: {proc_excerpt or 'Non encore précisé'}
- User instruction: {user_instruction or 'Générer un budget prévisionnel réaliste et complet en FCFA.'}
{existing_budget_hint}

STRICT REQUIREMENTS:
1. Language: {'English' if is_en else 'French (academic FMSB medical style)'}.
2. Provide 6 to 10 realistic budget items grouped into clear categories (e.g. {'Stationery & Printing, Data Collection & Logistics, Ethical Clearance & Admin, Data Analysis, Communication & Transport' if is_en else 'Fournitures & Secrétariat, Collecte des données & Logistique, Clairance éthique & Frais administratifs, Analyse statistique & Informatique, Transport & Communication'}).
3. Use realistic Cameroon / Yaoundé academic pricing in FCFA (integers, e.g., ethical clearance 25 000–50 000 FCFA, printing/binding 30 000–80 000 FCFA, internet/transport 20 000–60 000 FCFA, reagents/consumables only if laboratory/clinical tests are needed).
4. Do NOT include a 'Total' or 'Grand Total' row inside `items` (totals and 10% contingency are computed automatically in Python).
5. `intro_paragraph`: 2–3 formal academic sentences introducing the budget and stating the financing source ({'candidate own funds' if is_en else 'fonds propres du candidat'}).
6. `closing_note`: 1 short formal sentence on budget realism and funding responsibility.
"""

    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=StructuredBudgetOut,
        temperature=0.2,
    )
    dto: StructuredBudgetOut = res["data"]

    raw_items = [
        BudgetItem(
            category=it.category or ("Logistics & Materials" if is_en else "Matériel & Logistique"),
            designation=it.designation,
            quantity=it.quantity if it.quantity and it.quantity > 0 else 1.0,
            unit_cost_fcfa=it.unit_cost_fcfa if it.unit_cost_fcfa and it.unit_cost_fcfa > 0 else 0.0,
            total_fcfa=round((it.quantity or 1.0) * (it.unit_cost_fcfa or 0.0)),
        )
        for it in (dto.items or [])
    ]
    norm_items = normalize_budget_items(raw_items)

    # Deterministically add 10% contingency if requested and not already present
    has_contingency = any(_CONTINGENCY_RE.search(b.designation) for b in norm_items)
    if dto.include_contingency_10pct and norm_items and not has_contingency:
        subtotal = compute_budget_total(norm_items)
        contingency = round(subtotal * 0.10)
        if contingency > 0:
            norm_items.append(
                BudgetItem(
                    category="Imprévus" if not is_en else "Contingency",
                    designation="Imprévus (10 %)" if not is_en else "Contingency (10%)",
                    quantity=1.0,
                    unit_cost_fcfa=contingency,
                    total_fcfa=contingency,
                )
            )

    grand_total = compute_budget_total(norm_items)
    content_md = render_budget_markdown(
        norm_items,
        intro_paragraph=dto.intro_paragraph,
        closing_note=dto.closing_note,
        lang=lang,
    )

    # Synchronize directly into project.budget (Module 2 & Module 4 source of truth)
    project.budget = norm_items

    return {
        "model_used": res["model_used"],
        "items": norm_items,
        "grand_total_fcfa": grand_total,
        "content_markdown": content_md,
        "assistant_commentary": dto.assistant_commentary
        or (
            f"Budget prévisionnel structuré généré ({len(norm_items)} postes, total exact : {format_fcfa(grand_total)}) "
            "et synchronisé automatiquement avec le Module 2 (Word/PDF) et le Module 4 (PowerPoint)."
            if not is_en
            else f"Structured budget generated ({len(norm_items)} items, exact total: {format_fcfa(grand_total)}) "
            "and automatically synced to Module 2 (Word/PDF) and Module 4 (PowerPoint)."
        ),
    }
