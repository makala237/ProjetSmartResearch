"""Internal Vancouver (ICMJE / NLM / FMSB) reference generator and Gemini Decision 1 validator."""

from html import escape, unescape
import re
from typing import Any, Dict, List, Optional, Tuple

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import ProtocolProject, ReferenceItem, SectionState, StepStatus
from app.services.gemini_client import GEMINI_DECISION_1_LABEL, gemini_router

_SKIP_HEADING_EXACT = {
    "references",
    "références",
    "references bibliographiques",
    "références bibliographiques",
    "bibliographie",
    "bibliographie (système vancouver)",
    "bibliographie (systeme vancouver)",
    "references (vancouver)",
    "aucune référence bibliographique enregistrée.",
    "aucune référence enregistrée pour le moment.",
    "aucune référence n'est encore citée dans les sections rédigées du protocole.",
    "aucune référence n'est encore citée dans les sections rédigées du protocole",
    "no references have been cited in the drafted sections yet.",
    "no references have been cited in the drafted sections yet",
}

_NON_BODY_SECTION_IDS = frozenset(
    {
        "cover_page",
        "toc",
        "abbreviations",
        "list_of_tables",
        "list_of_figures",
        "references",
    }
)
_CITATION_GROUP_RE = re.compile(r"\[\s*(\d+(?:\s*(?:[-–,]\s*\d+))*)\s*\](?!\s*[\(:])")
_HTML_TAG_SPLIT_RE = re.compile(r"(<[^>]+>)")

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_DOI_RE = re.compile(r"(?:doi\s*:\s*|https?://doi\.org/)(10\.\d{4,9}/[^\s,;]+)", re.IGNORECASE)
_PMID_RE = re.compile(r"PMID\s*:\s*(\d+)", re.IGNORECASE)
_JOURNAL_META_RE = re.compile(
    r"(?P<journal>[^.;]+?)\.?\s*(?P<year>(?:19|20)\d{2})\s*;\s*(?P<vol>[A-Za-z0-9\-]+)?(?:\((?P<issue>[^)]+)\))?\s*(?::\s*(?P<pages>[A-Za-z0-9\-–]+))?\.?$"
)
_BOOK_YEAR_RE = re.compile(r":\s*([^;]+?)\s*;\s*((?:19|20)\d{2})\b")
_ANY_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def _strip_leading_number(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^\s*(?:\[\d+\]|\d+\s*[.)\]])\s*", "", cleaned)
    cleaned = re.sub(r"^\s*[-•*]\s+", "", cleaned)
    return cleaned.strip()


def _normalize_vancouver_punctuation(text: str) -> str:
    """Normalize spacing, 'et al.', incomplete hyphenated page ranges, and terminal period."""
    s = _strip_leading_number(text)
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    # Protect URLs before tightening punctuation spacing
    urls: List[str] = []

    def _stash_url(match: re.Match) -> str:
        urls.append(match.group(0))
        return f"__URL_{len(urls) - 1}__"

    s = _URL_RE.sub(_stash_url, s)
    s = re.sub(r"\s+([;,.])", r"\1", s)
    s = re.sub(r"([12]\d{3})\s*;\s*", r"\1;", s)
    s = re.sub(r"(\w|\))\s*:\s*(\d)", r"\1:\2", s)
    s = re.sub(r"\bet\s+al\b(?!\.)", "et al.", s, flags=re.IGNORECASE)
    # Repair truncated trailing page ranges like '215-' or '215–.'
    s = re.sub(r"(:\s*\d+)\s*[-–]+\.?$", r"\1.", s)
    s = s.rstrip(" ,;")
    if s and s[-1] not in ".!?":
        if not s.endswith("__"):
            s += "."
    for idx, url in enumerate(urls):
        s = s.replace(f"__URL_{idx}__", url)
    return s.strip()


def _format_authors_vancouver(authors: List[str]) -> str:
    cleaned = [a.strip().rstrip(".") for a in authors if a and a.strip()]
    if not cleaned:
        return "Auteurs non précisés"
    # Remove any trailing 'et al' inside list if already truncated upstream
    filtered = [a for a in cleaned if a.lower() not in {"et al", "et al."}]
    had_et_al = len(filtered) < len(cleaned)
    if len(filtered) > 6:
        return ", ".join(filtered[:6]) + ", et al."
    if had_et_al and filtered:
        return ", ".join(filtered[:6]) + ", et al."
    return ", ".join(filtered)


def format_vancouver_citation(ref: ReferenceItem) -> str:
    """Deterministically format a ReferenceItem according to Vancouver ICMJE style."""
    if ref.source_platform == "web" and not ref.journal:
        parts = []
        if ref.authors:
            parts.append(_format_authors_vancouver(ref.authors) + ".")
        parts.append(ref.title.rstrip(".") + ".")
        if ref.year:
            parts.append(f"{ref.year}.")
        if ref.url:
            parts.append(f"Disponible sur : {ref.url}")
        return _normalize_vancouver_punctuation(" ".join(parts).strip())

    authors_str = _format_authors_vancouver(ref.authors)
    title_str = ref.title.strip().rstrip(".")
    journal_str = ref.journal.strip().rstrip(".")

    pub_parts = []
    if ref.year:
        pub_parts.append(str(ref.year))
    if ref.volume:
        vol_issue = ref.volume
        if ref.issue:
            vol_issue += f"({ref.issue})"
        if ref.pages:
            vol_issue += f":{ref.pages}"
        pub_parts.append(vol_issue)
    elif ref.pages:
        pub_parts.append(f":{ref.pages}")

    pub_block = ";".join(pub_parts) if len(pub_parts) > 1 else "".join(pub_parts)

    segments = [f"{authors_str}.", f"{title_str}."]
    if journal_str:
        segments.append(f"{journal_str}.")
    if pub_block:
        segments.append(f"{pub_block}.")
    if ref.doi:
        segments.append(f"doi:{ref.doi}.")
    if ref.pmid:
        segments.append(f"PMID:{ref.pmid}.")

    return _normalize_vancouver_punctuation(" ".join(segments).strip())


def parse_raw_vancouver_line(raw_line: str, order_index: int) -> ReferenceItem:
    """Parse a raw academic reference string (article, book, guideline, or URL) into ReferenceItem."""
    normalized = _normalize_vancouver_punctuation(raw_line)
    work = normalized

    doi_match = _DOI_RE.search(work)
    doi_val = doi_match.group(1).rstrip(".") if doi_match else None
    pmid_match = _PMID_RE.search(work)
    pmid_val = pmid_match.group(1) if pmid_match else None
    url_match = _URL_RE.search(work)
    url_val = url_match.group(0).rstrip(".") if url_match else ""

    # Strip trailing doi/pmid tokens for core field splitting
    core = _DOI_RE.sub("", work)
    core = _PMID_RE.sub("", core).strip().rstrip(".")

    authors_list: List[str] = []
    title_val = core
    journal_val = ""
    year_val: Optional[int] = None
    volume_val = ""
    issue_val = ""
    pages_val = ""

    # Split authors.rest carefully (keep ', et al.' intact)
    first_sep = re.search(r"(?<!et\sal)\.\s+", core)
    rest = ""
    if first_sep:
        raw_authors = core[: first_sep.start()].strip()
        rest = core[first_sep.end() :].strip()
        if raw_authors:
            authors_list = [a.strip() for a in raw_authors.split(",") if a.strip()]
    else:
        rest = core

    # Check journal pattern Year;Vol(Issue):Pages in remainder
    m_journal = _JOURNAL_META_RE.search(rest)
    if m_journal and "." in rest:
        before_year = rest[: m_journal.start()].strip()
        journal_val = (m_journal.group("journal") or "").strip().rstrip(".")
        if before_year:
            # Usually "Title. Journal" -> split on last sentence boundary before journal
            title_val = before_year.rstrip(".")
        else:
            # Regex swallowed title + journal if single dot before year
            if "." in rest[: m_journal.start("year")]:
                t_part, j_part = rest[: m_journal.start("year")].rsplit(".", 1)
                title_val = t_part.strip()
                journal_val = j_part.strip()
        try:
            year_val = int(m_journal.group("year"))
        except (TypeError, ValueError):
            pass
        volume_val = (m_journal.group("vol") or "").strip()
        issue_val = (m_journal.group("issue") or "").strip()
        pages_val = (m_journal.group("pages") or "").strip().rstrip("-–")
    else:
        # Book / Monograph / Report pattern
        if rest:
            m_book = _BOOK_YEAR_RE.search(rest)
            if m_book:
                try:
                    year_val = int(m_book.group(2))
                except (TypeError, ValueError):
                    pass
                title_val = rest.split(".", 1)[0].strip()
                journal_val = rest.split(".", 1)[1].strip() if "." in rest else m_book.group(1).strip()
            else:
                m_yr = _ANY_YEAR_RE.search(rest)
                if m_yr:
                    try:
                        year_val = int(m_yr.group(1))
                    except (TypeError, ValueError):
                        pass
                title_val = rest.split(".", 1)[0].strip() or core

    return ReferenceItem(
        order_index=order_index,
        pmid=pmid_val,
        doi=doi_val,
        source_platform="web" if (url_val and not journal_val) else "manual",
        authors=authors_list,
        title=title_val[:260] or normalized[:180],
        journal=journal_val[:180],
        year=year_val,
        volume=volume_val,
        issue=issue_val,
        pages=pages_val,
        url=url_val,
        vancouver_citation=normalized,
        verified=False,
    )


def _extract_lines_from_references_section(sec: Optional[SectionState]) -> List[str]:
    """Extract raw candidate reference entries from HTML (<ol>/<li>/<p>) or Markdown."""
    if sec is None:
        return []
    candidates: List[str] = []

    raw_md = (sec.content_markdown or "").strip()
    if raw_md:
        for line in raw_md.splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#") or set(clean) <= {"-", "=", "*"}:
                continue
            clean = re.sub(r"^\*\*|\*\*$", "", clean).strip()
            if clean.lower().rstrip(":.") in _SKIP_HEADING_EXACT:
                continue
            if len(clean) < 12:
                continue
            candidates.append(clean)
        if candidates:
            return candidates

    raw_html = (sec.content_html or "").strip()
    if raw_html:
        blocks = re.findall(r"<(?:li|p)[^>]*>(.*?)</(?:li|p)>", raw_html, flags=re.IGNORECASE | re.DOTALL)
        if not blocks:
            blocks = raw_html.splitlines()
        for blk in blocks:
            txt = unescape(re.sub(r"<[^>]+>", " ", blk))
            txt = re.sub(r"\s+", " ", txt).strip()
            if not txt or txt.lower().rstrip(":.") in _SKIP_HEADING_EXACT or len(txt) < 12:
                continue
            candidates.append(txt)
    return candidates


def _dedup_key(ref: ReferenceItem) -> str:
    """Build a normalized deduplication fingerprint for a ReferenceItem."""
    if ref.doi:
        return f"doi:{ref.doi.lower().strip()}"
    if ref.pmid:
        return f"pmid:{ref.pmid.strip()}"
    cit = _strip_leading_number(ref.vancouver_citation or "")
    if not cit:
        cit = f"{' '.join(ref.authors)} {ref.title} {ref.year or ''}"
    alnum = re.sub(r"[^a-z0-9]+", "", cit.lower())
    return alnum[:90]


def _expand_citation_group(inner: str) -> List[int]:
    """Expand a citation bracket body like '1, 3-5' into ordered integer indices, ignoring years (>500)."""
    nums: List[int] = []
    for part in re.split(r"\s*,\s*", (inner or "").strip()):
        if not part:
            continue
        m_range = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m_range:
            a, b = int(m_range.group(1)), int(m_range.group(2))
            if 1 <= a <= b <= 500 and (b - a) <= 40:
                nums.extend(range(a, b + 1))
        elif part.isdigit():
            val = int(part)
            if 1 <= val <= 500:
                nums.append(val)
    return nums


def _format_citation_numbers(nums: List[int]) -> str:
    """Format remapped citation indices with sorting and consecutive range collapsing (>=3)."""
    unique = sorted(set(n for n in nums if n >= 1))
    if not unique:
        return ""
    tokens: List[str] = []
    start = unique[0]
    prev = unique[0]
    for cur in unique[1:]:
        if cur == prev + 1:
            prev = cur
        else:
            if prev - start >= 2:
                tokens.append(f"{start}–{prev}")
            elif prev > start:
                tokens.extend([str(start), str(prev)])
            else:
                tokens.append(str(start))
            start = cur
            prev = cur
    if prev - start >= 2:
        tokens.append(f"{start}–{prev}")
    elif prev > start:
        tokens.extend([str(start), str(prev)])
    else:
        tokens.append(str(start))
    return "[" + ", ".join(tokens) + "]"


def _remap_citations_in_text(text: str, old_to_new: Dict[int, int]) -> str:
    """Remap all [n] / [a, b] / [a-b] citations in plain/markdown text without touching years or links."""
    if not text or not old_to_new:
        return text

    def _repl(match: re.Match) -> str:
        nums = _expand_citation_group(match.group(1))
        if not nums:
            return match.group(0)
        remapped = [old_to_new.get(n, n) for n in nums]
        if remapped == nums:
            return match.group(0)
        return _format_citation_numbers(remapped)

    return _CITATION_GROUP_RE.sub(_repl, text)


def _remap_citations_in_html(html: str, old_to_new: Dict[int, int]) -> str:
    """Remap citations only inside HTML text nodes, preserving tags and attributes intact."""
    if not html or not old_to_new:
        return html
    parts = _HTML_TAG_SPLIT_RE.split(html)
    out: List[str] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            out.append(part)
        else:
            out.append(_remap_citations_in_text(part, old_to_new))
    return "".join(out)


def _ordered_body_section_ids(project: ProtocolProject) -> List[str]:
    """Return all protocol body section IDs in canonical CAMES appearance order."""
    ordered: List[str] = []
    seen: set[str] = set()
    for spec in FMSB_SECTIONS:
        if spec.id not in _NON_BODY_SECTION_IDS and spec.id in project.sections:
            ordered.append(spec.id)
            seen.add(spec.id)
    for sid in project.sections:
        if sid not in _NON_BODY_SECTION_IDS and sid not in seen:
            ordered.append(sid)
            seen.add(sid)
    return ordered


def _section_text_for_citation_scan(sec: SectionState) -> str:
    md = (sec.content_markdown or "").strip()
    if md:
        return md
    html = (sec.content_html or "").strip()
    if html:
        return unescape(re.sub(r"<[^>]+>", " ", html))
    return ""


def _extract_ordered_citations_from_section(sec: SectionState) -> List[int]:
    """Return citation indices referenced in sec in exact left-to-right appearance order."""
    txt = _section_text_for_citation_scan(sec)
    if not txt:
        return []
    seen: set[int] = set()
    ordered: List[int] = []
    for match in _CITATION_GROUP_RE.finditer(txt):
        for n in _expand_citation_group(match.group(1)):
            if n not in seen:
                seen.add(n)
                ordered.append(n)
    return ordered


def _assign_stable_pool_indices(refs: List[ReferenceItem]) -> Dict[int, ReferenceItem]:
    """Ensure every reference in refs has a unique positive order_index and return index->ref map."""
    by_idx: Dict[int, ReferenceItem] = {}
    missing: List[ReferenceItem] = []
    for ref in refs:
        idx = getattr(ref, "order_index", None)
        if isinstance(idx, int) and idx > 0 and idx not in by_idx:
            by_idx[idx] = ref
        else:
            missing.append(ref)
    next_idx = max(by_idx.keys(), default=0) + 1
    for ref in missing:
        ref.order_index = next_idx
        by_idx[next_idx] = ref
        next_idx += 1
    return by_idx


def _empty_cited_references_message(lang: str) -> str:
    return (
        "No references have been cited in the drafted sections yet."
        if (lang or "fr").lower().startswith("en")
        else "Aucune référence n'est encore citée dans les sections rédigées du protocole."
    )


def get_cited_references_in_order(project: ProtocolProject) -> List[ReferenceItem]:
    """Return ONLY the references cited in body sections, sorted strictly by first appearance."""
    refs = list(project.references or [])
    if not refs:
        return []
    by_idx = _assign_stable_pool_indices(refs)
    seen_ref_ids: set[str] = set()
    cited: List[ReferenceItem] = []
    for sid in _ordered_body_section_ids(project):
        sec = project.sections.get(sid)
        if sec is None:
            continue
        for idx in _extract_ordered_citations_from_section(sec):
            ref = by_idx.get(idx)
            if ref is not None and ref.ref_id not in seen_ref_ids:
                seen_ref_ids.add(ref.ref_id)
                cited.append(ref)
    return cited


def renumber_and_sync_vancouver_references(project: ProtocolProject) -> Dict[str, Any]:
    """Renumber cited references 1..K by first appearance across sections, remap [n], and sync references section."""
    refs = list(project.references or [])
    if not refs:
        ref_sec = project.sections.get("references")
        if ref_sec is not None:
            empty_msg = _empty_cited_references_message(getattr(project, "language", "fr"))
            ref_sec.content_markdown = empty_msg
            ref_sec.content_html = f"<p><em>{escape(empty_msg)}</em></p>"
        return {
            "cited_count": 0,
            "total_pool_count": 0,
            "uncited_count": 0,
            "remapped_count": 0,
            "mapping": {},
            "cited_refs": [],
        }

    by_old_idx = _assign_stable_pool_indices(refs)

    cited_old_order: List[int] = []
    seen_old: set[int] = set()
    for sid in _ordered_body_section_ids(project):
        sec = project.sections.get(sid)
        if sec is None:
            continue
        for old_idx in _extract_ordered_citations_from_section(sec):
            if old_idx in by_old_idx and old_idx not in seen_old:
                seen_old.add(old_idx)
                cited_old_order.append(old_idx)

    cited_refs: List[ReferenceItem] = [by_old_idx[i] for i in cited_old_order]
    cited_ref_id_set = {r.ref_id for r in cited_refs}
    uncited_refs: List[ReferenceItem] = [r for r in refs if r.ref_id not in cited_ref_id_set]

    old_to_new: Dict[int, int] = {}
    for new_idx, old_idx in enumerate(cited_old_order, start=1):
        old_to_new[old_idx] = new_idx
        by_old_idx[old_idx].order_index = new_idx
    for offset, ref in enumerate(uncited_refs, start=len(cited_refs) + 1):
        prev_idx = getattr(ref, "order_index", None)
        if isinstance(prev_idx, int) and prev_idx > 0 and prev_idx not in old_to_new:
            old_to_new[prev_idx] = offset
        ref.order_index = offset

    project.references = cited_refs + uncited_refs
    for ref in project.references:
        if not ref.vancouver_citation:
            ref.vancouver_citation = format_vancouver_citation(ref)
        else:
            ref.vancouver_citation = _normalize_vancouver_punctuation(ref.vancouver_citation)

    remapped_indices = {o: n for o, n in old_to_new.items() if o != n}
    new_idx_to_ref_id = {r.order_index: r.ref_id for r in cited_refs if r.order_index}

    for sid in _ordered_body_section_ids(project):
        sec = project.sections.get(sid)
        if sec is None:
            continue
        if remapped_indices:
            if sec.content_markdown:
                sec.content_markdown = _remap_citations_in_text(sec.content_markdown, old_to_new)
            if sec.content_html:
                sec.content_html = _remap_citations_in_html(sec.content_html, old_to_new)
        sec_new_nums = _extract_ordered_citations_from_section(sec)
        sec.cited_ref_ids = [
            new_idx_to_ref_id[n] for n in sec_new_nums if n in new_idx_to_ref_id
        ]

    lang = getattr(project, "language", "fr")
    ref_sec = project.sections.get("references")
    if ref_sec is None:
        ref_sec = SectionState(section_id="references", status=StepStatus.IN_PROGRESS)
        project.sections["references"] = ref_sec

    if cited_refs:
        ref_sec.content_markdown = render_vancouver_markdown_list(cited_refs)
        ref_sec.content_html = render_vancouver_html_list(cited_refs)
        if ref_sec.status == StepStatus.PENDING:
            ref_sec.status = StepStatus.IN_PROGRESS
    else:
        empty_msg = _empty_cited_references_message(lang)
        ref_sec.content_markdown = empty_msg
        ref_sec.content_html = f"<p><em>{escape(empty_msg)}</em></p>"

    return {
        "cited_count": len(cited_refs),
        "total_pool_count": len(project.references),
        "uncited_count": len(uncited_refs),
        "remapped_count": len(remapped_indices),
        "mapping": old_to_new,
        "cited_refs": cited_refs,
    }


def extract_all_project_references(project: ProtocolProject) -> List[ReferenceItem]:
    """Merge and deduplicate references from project.references and section 'references' without clobbering existing indices."""
    deduped: List[ReferenceItem] = []
    seen_keys: set[str] = set()

    for ref in project.references:
        cloned = ref.model_copy(deep=True)
        if not cloned.vancouver_citation:
            cloned.vancouver_citation = format_vancouver_citation(cloned)
        else:
            cloned.vancouver_citation = _normalize_vancouver_punctuation(cloned.vancouver_citation)
        key = _dedup_key(cloned)
        if key and key not in seen_keys:
            seen_keys.add(key)
            deduped.append(cloned)

    used_indices = {
        r.order_index
        for r in deduped
        if isinstance(getattr(r, "order_index", None), int) and r.order_index > 0
    }
    next_idx = max(used_indices, default=0) + 1
    for ref in deduped:
        if not (isinstance(getattr(ref, "order_index", None), int) and ref.order_index > 0):
            ref.order_index = next_idx
            used_indices.add(next_idx)
            next_idx += 1

    raw_lines = _extract_lines_from_references_section(project.sections.get("references"))
    for raw in raw_lines:
        parsed = parse_raw_vancouver_line(raw, order_index=next_idx)
        key = _dedup_key(parsed)
        if key and key not in seen_keys:
            seen_keys.add(key)
            deduped.append(parsed)
            next_idx += 1

    return deduped


def render_vancouver_markdown_list(refs: List[ReferenceItem]) -> str:
    """Render references as a strict numbered Markdown list (1. ... 2. ...)."""
    if not refs:
        return ""
    lines: List[str] = []
    for idx, ref in enumerate(refs, start=1):
        txt = _strip_leading_number(ref.vancouver_citation or format_vancouver_citation(ref))
        lines.append(f"{idx}. {txt}")
    return "\n".join(lines)


def render_vancouver_html_list(refs: List[ReferenceItem]) -> str:
    """Render references as a strict ordered HTML <ol> list in Times New Roman 12pt."""
    if not refs:
        return ""
    items: List[str] = []
    for ref in refs:
        txt = _strip_leading_number(ref.vancouver_citation or format_vancouver_citation(ref))
        items.append(f'<li style="margin-bottom:6pt;">{escape(txt)}</li>')
    return (
        '<ol style="list-style-type:decimal;margin:0;padding-left:24px;font-family:\'Times New Roman\',serif;'
        'font-size:12pt;line-height:1.5;text-align:justify;">'
        + "".join(items)
        + "</ol>"
    )


def build_and_validate_vancouver_references(
    project: ProtocolProject,
    use_gemini_decision: bool = True,
    mark_validated: bool = False,
) -> Dict[str, Any]:
    """Reorder cited references by first appearance, deduplicate, audit with Gemini Decision 1, and render cited-only list."""
    project.references = extract_all_project_references(project)
    if not project.references:
        renumber_and_sync_vancouver_references(project)
        return {
            "references_count": 0,
            "total_pool_count": 0,
            "uncited_count": 0,
            "remapped_count": 0,
            "corrected_count": 0,
            "decision_model": f"{GEMINI_DECISION_1_LABEL} (vancouver-empty)",
            "decision_summary": "Aucune référence bibliographique détectée dans le projet.",
            "references": [],
        }

    renum_report = renumber_and_sync_vancouver_references(project)
    cited_refs: List[ReferenceItem] = renum_report["cited_refs"]
    uncited_refs = project.references[len(cited_refs) :]

    corrected_count = 0
    decision_model = f"{GEMINI_DECISION_1_LABEL} (deterministic-vancouver)"
    decision_summary = (
        f"{len(cited_refs)} référence(s) citée(s) classée(s) par ordre d'apparition "
        f"(sur un réservoir de {len(project.references)} référence(s))."
    )

    if use_gemini_decision and cited_refs:
        payload = [
            {
                "index": idx,
                "raw": ref.vancouver_citation,
                "authors": ref.authors[:6],
                "title": ref.title,
                "journal": ref.journal,
                "year": ref.year,
                "volume": ref.volume,
                "issue": ref.issue,
                "pages": ref.pages,
            }
            for idx, ref in enumerate(cited_refs, start=1)
        ]
        decision_res, decision_model = gemini_router.validate_vancouver_references(payload)
        decision_summary = decision_res.get("summary") or decision_summary
        validated_rows = decision_res.get("references") or []
        by_idx: Dict[int, Dict[str, Any]] = {}
        for row in validated_rows:
            if isinstance(row, dict) and row.get("index") is not None:
                try:
                    by_idx[int(row["index"])] = row
                except (TypeError, ValueError):
                    continue

        for idx, ref in enumerate(cited_refs, start=1):
            before = ref.vancouver_citation
            row = by_idx.get(idx)
            if row and isinstance(row.get("vancouver_citation"), str):
                candidate = _normalize_vancouver_punctuation(row["vancouver_citation"])
                # Guard against accidental empty or truncated LLM output
                if len(candidate) >= max(18, int(len(before) * 0.45)):
                    ref.vancouver_citation = candidate
                    if candidate != before or bool(row.get("was_corrected")):
                        corrected_count += 1
            ref.verified = True
    else:
        for ref in cited_refs:
            ref.vancouver_citation = _normalize_vancouver_punctuation(ref.vancouver_citation)

    project.references = cited_refs + uncited_refs
    ref_sec = project.sections.get("references")
    if ref_sec is None:
        ref_sec = SectionState(section_id="references", status=StepStatus.IN_PROGRESS)
        project.sections["references"] = ref_sec

    if cited_refs:
        ref_sec.content_markdown = render_vancouver_markdown_list(cited_refs)
        ref_sec.content_html = render_vancouver_html_list(cited_refs)
        ref_sec.audit_notes = [
            f"Liste Vancouver ({len(cited_refs)} réf. citées / {len(project.references)} au total) validée : {decision_summary}"
        ]
    else:
        empty_msg = _empty_cited_references_message(getattr(project, "language", "fr"))
        ref_sec.content_markdown = empty_msg
        ref_sec.content_html = f"<p><em>{escape(empty_msg)}</em></p>"
        ref_sec.audit_notes = [decision_summary]

    if mark_validated and cited_refs:
        ref_sec.status = StepStatus.VALIDATED
    elif ref_sec.status == StepStatus.PENDING:
        ref_sec.status = StepStatus.IN_PROGRESS

    return {
        "references_count": len(cited_refs),
        "total_pool_count": len(project.references),
        "uncited_count": len(uncited_refs),
        "remapped_count": renum_report["remapped_count"],
        "corrected_count": corrected_count,
        "decision_model": decision_model,
        "decision_summary": decision_summary,
        "references": [r.model_dump() for r in cited_refs],
    }


def refresh_vancouver_references(project: ProtocolProject) -> ProtocolProject:
    """Ensure every reference has an order_index, clean Vancouver text, and sync cited appearance order."""
    for idx, ref in enumerate(project.references, start=1):
        if not (isinstance(getattr(ref, "order_index", None), int) and ref.order_index > 0):
            ref.order_index = idx
        if not ref.vancouver_citation or not ref.verified:
            ref.vancouver_citation = format_vancouver_citation(ref)
        else:
            ref.vancouver_citation = _normalize_vancouver_punctuation(ref.vancouver_citation)
    renumber_and_sync_vancouver_references(project)
    return project
