"""Internal Vancouver (ICMJE / NLM / FMSB) reference generator and Gemini Decision 1 validator."""

from datetime import datetime, timezone
from html import escape, unescape
import re
from typing import Any, Dict, List, Optional, Tuple

from app.data.i18n import get_project_canvas
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
    """Normalize spacing, italics, 'et al.', NLM page ranges, and terminal period."""
    s = _strip_leading_number(text)
    if not s:
        return ""
    # Strict NLM/Vancouver uses NO italics (strip Markdown *...* or _..._)
    s = re.sub(r"\*+([^*]+)\*+", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Protect URLs before tightening punctuation spacing
    urls: List[str] = []

    def _stash_url(match: re.Match) -> str:
        # Never keep a trailing period attached to a URL
        urls.append(match.group(0).rstrip(".,;)"))
        return f"__URL_{len(urls) - 1}__"

    s = _URL_RE.sub(_stash_url, s)
    s = re.sub(r"\s+([;,.])", r"\1", s)
    # Normalize spelled-out publication months after 4-digit year (e.g. '2024 January;' -> '2024 Jan;')
    s = re.sub(
        r"\b((?:19|20)\d{2})\s+([A-Za-zÀ-ÖØ-öø-ÿ]{3,10}\.?)(?=\s|\d|;)",
        lambda m: f"{m.group(1)} {_NLM_MONTH_MAP.get(m.group(2).lower(), m.group(2))}",
        s,
    )
    s = re.sub(r"([12]\d{3})\s*;\s*", r"\1;", s)
    s = re.sub(r"(\w|\))\s*:\s*(\d)", r"\1:\2", s)
    s = re.sub(r"\bet\s+al\b(?!\.)", "et al.", s, flags=re.IGNORECASE)
    # Collapse accidental double periods like 'et al..'
    s = re.sub(r"\.{2,}", ".", s)
    # Repair truncated trailing page ranges like '215-' or '215–.'
    s = re.sub(r"(:\s*[A-Za-z]?\d+)\s*[-–]+\.?$", r"\1.", s)
    # Apply NLM page-range elision to numeric ranges after ':' (e.g. ':284-287' -> ':284-7')
    s = re.sub(
        r":([A-Za-z]?\d+\s*[-–]\s*[A-Za-z]?\d+)",
        lambda m: ":" + _abbreviate_page_range_nlm(m.group(1)),
        s,
    )
    s = s.rstrip(" ,;")
    if s and s[-1] not in ".!?":
        if not s.endswith("__"):
            s += "."
    for idx, url in enumerate(urls):
        s = s.replace(f"__URL_{idx}__", url)
    return s.strip()


_CORPORATE_AUTHOR_RE = re.compile(
    r"\b(organization|organisation|who|oms|minsante|minist[eè]re|ministry|cdc|inserm|unaids|onusida|"
    r"unicef|unesco|nih|nlm|nice|has|haute\s+autorit[eé]|institut|institute|committee|comit[eé]|"
    r"consortium|collaborat|group|groupe|society|soci[eé]t[eé]|association|college|coll[eè]ge|"
    r"university|universit[eé]|academy|acad[eé]mie|foundation|fondation|center|centre|network|r[eé]seau|"
    r"working\s+party|task\s+force|guidelines?|department|d[eé]partement|fmsb|cames)\b",
    flags=re.IGNORECASE,
)


def _abbreviate_page_range_nlm(pages: str) -> str:
    """Apply official NLM Citing Medicine page-range elision (e.g. 284-287 -> 284-7, 112-119 -> 112-19)."""
    raw = (pages or "").strip().rstrip("-–.")
    if not raw:
        return ""
    m = re.fullmatch(r"([A-Za-z]?)(\d+)\s*[-–]\s*([A-Za-z]?)(\d+)", raw)
    if not m:
        return raw
    p1, s1, p2, s2 = m.group(1), m.group(2), m.group(3), m.group(4)
    if p1.upper() != p2.upper():
        return f"{p1}{s1}-{p2}{s2}"
    if len(s1) != len(s2) or int(s2) <= int(s1):
        return f"{p1}{s1}-{s2}"
    # Find first differing digit
    diff_idx = 0
    while diff_idx < len(s1) and s1[diff_idx] == s2[diff_idx]:
        diff_idx += 1
    # Keep last two digits if penultimate digit is '1' (teens 10-19 rule in NLM/Chicago)
    if diff_idx == len(s1) - 1 and len(s1) >= 2 and s1[-2] == "1":
        diff_idx = len(s1) - 2
    return f"{p1}{s1}-{s2[diff_idx:]}"


_NLM_MONTH_MAP: Dict[str, str] = {
    "january": "Jan", "janvier": "Jan", "jan.": "Jan",
    "february": "Feb", "février": "Feb", "fevrier": "Feb", "feb.": "Feb", "févr.": "Feb",
    "march": "Mar", "mars": "Mar", "mar.": "Mar",
    "april": "Apr", "avril": "Apr", "apr.": "Apr", "avr.": "Apr",
    "may": "May", "mai": "May",
    "june": "Jun", "juin": "Jun", "jun.": "Jun",
    "july": "Jul", "juillet": "Jul", "jul.": "Jul", "juil.": "Jul",
    "august": "Aug", "août": "Aug", "aout": "Aug", "aug.": "Aug",
    "september": "Sep", "septembre": "Sep", "sept.": "Sep", "sep.": "Sep",
    "october": "Oct", "octobre": "Oct", "oct.": "Oct",
    "november": "Nov", "novembre": "Nov", "nov.": "Nov",
    "december": "Dec", "décembre": "Dec", "decembre": "Dec", "dec.": "Dec", "déc.": "Dec",
}

_FR_MONTHS_SHORT = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sep", "oct", "nov", "déc"]
_EN_MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Official NLM Catalog / Index Medicus abbreviations for top global, African & FMSB/CAMES journals
_NLM_JOURNAL_ABBREV_MAP: Dict[str, str] = {
    "new england journal of medicine": "N Engl J Med",
    "the new england journal of medicine": "N Engl J Med",
    "the lancet": "Lancet",
    "lancet global health": "Lancet Glob Health",
    "the lancet global health": "Lancet Glob Health",
    "lancet infectious diseases": "Lancet Infect Dis",
    "the lancet infectious diseases": "Lancet Infect Dis",
    "lancet hiv": "Lancet HIV",
    "the lancet hiv": "Lancet HIV",
    "lancet public health": "Lancet Public Health",
    "journal of the american medical association": "JAMA",
    "british medical journal": "BMJ",
    "bmj global health": "BMJ Glob Health",
    "bmj open": "BMJ Open",
    "nature medicine": "Nat Med",
    "pan african medical journal": "Pan Afr Med J",
    "the pan african medical journal": "Pan Afr Med J",
    "health sciences and disease": "Health Sci Dis",
    "health sciences and diseases": "Health Sci Dis",
    "plos one": "PLoS One",
    "plos medicine": "PLoS Med",
    "plos neglected tropical diseases": "PLoS Negl Trop Dis",
    "bmc public health": "BMC Public Health",
    "bmc infectious diseases": "BMC Infect Dis",
    "bmc health services research": "BMC Health Serv Res",
    "bmc medicine": "BMC Med",
    "bmc pregnancy and childbirth": "BMC Pregnancy Childbirth",
    "bmc pediatrics": "BMC Pediatr",
    "bulletin of the world health organization": "Bull World Health Organ",
    "weekly epidemiological record": "Wkly Epidemiol Rec",
    "tropical medicine & international health": "Trop Med Int Health",
    "tropical medicine and international health": "Trop Med Int Health",
    "transactions of the royal society of tropical medicine and hygiene": "Trans R Soc Trop Med Hyg",
    "american journal of tropical medicine and hygiene": "Am J Trop Med Hyg",
    "clinical infectious diseases": "Clin Infect Dis",
    "journal of infectious diseases": "J Infect Dis",
    "the journal of infectious diseases": "J Infect Dis",
    "journal of acquired immune deficiency syndromes": "J Acquir Immune Defic Syndr",
    "journal of the international aids society": "J Int AIDS Soc",
    "aids care": "AIDS Care",
    "international journal of tuberculosis and lung disease": "Int J Tuberc Lung Dis",
    "european respiratory journal": "Eur Respir J",
    "journal of clinical oncology": "J Clin Oncol",
    "annals of internal medicine": "Ann Intern Med",
    "american journal of public health": "Am J Public Health",
    "international journal of epidemiology": "Int J Epidemiol",
    "european journal of epidemiology": "Eur J Epidemiol",
    "journal of clinical epidemiology": "J Clin Epidemiol",
    "cochrane database of systematic reviews": "Cochrane Database Syst Rev",
    "medecine et maladies infectieuses": "Med Mal Infect",
    "médecine et maladies infectieuses": "Med Mal Infect",
    "medecine tropicale": "Med Trop",
    "médecine tropicale": "Med Trop",
    "medecine et sante tropicales": "Med Sante Trop",
    "médecine et santé tropicales": "Med Sante Trop",
    "revue d'epidemiologie et de sante publique": "Rev Epidemiol Sante Publique",
    "revue d'épidémiologie et de santé publique": "Rev Epidemiol Sante Publique",
    "sante publique": "Sante Publique",
    "santé publique": "Sante Publique",
    "bulletin de la societe de pathologie exotique": "Bull Soc Pathol Exot",
    "bulletin de la société de pathologie exotique": "Bull Soc Pathol Exot",
    "african health sciences": "Afr Health Sci",
    "african journal of reproductive health": "Afr J Reprod Health",
    "east african medical journal": "East Afr Med J",
    "west african journal of medicine": "West Afr J Med",
    "south african medical journal": "S Afr Med J",
    "revue africaine de chirurgie et specialites": "Rev Afr Chir Spec",
    "revue africaine de chirurgie et spécialités": "Rev Afr Chir Spec",
    "journal of global health": "J Glob Health",
    "global health action": "Glob Health Action",
    "malaria journal": "Malar J",
    "emerging infectious diseases": "Emerg Infect Dis",
    "frontiers in public health": "Front Public Health",
    "frontiers in medicine": "Front Med (Lausanne)",
    "scientific reports": "Sci Rep",
}

_PRESERVE_UPPER_TOKENS = frozenset({
    "HIV", "VIH", "AIDS", "SIDA", "WHO", "OMS", "PVVIH", "PLHIV", "TARV", "ARV", "ART",
    "CD4", "PCR", "DNA", "ADN", "RNA", "ARN", "TB", "MDR-TB", "XDR-TB", "COVID-19",
    "SARS-CoV-2", "SARS", "MERS", "HBV", "HCV", "VHB", "VHC", "HPV", "HTA", "IMC", "BMI",
    "ECG", "EEG", "IRM", "MRI", "TDM", "CT", "FMSB", "CAMES", "CHUY", "HGY", "HCY",
    "HGOPY", "HGD", "HLD", "MINSANTE", "CDC", "NIH", "NLM", "INSERM", "UNAIDS", "ONUSIDA",
    "UNICEF", "CAMPHIA", "DHS", "EDS", "PRISMA", "CONSORT", "STROBE", "SPSS", "STATA",
    "USA", "UK", "EU", "UN", "FCFA", "ICU", "US", "CI", "IC", "OR", "RR", "HR", "IQR",
})

_PROPER_NOUN_CASE_MAP: Dict[str, str] = {
    "cameroon": "Cameroon", "cameroun": "Cameroun", "yaounde": "Yaoundé", "yaoundé": "Yaoundé",
    "douala": "Douala", "africa": "Africa", "afrique": "Afrique", "sub-saharan": "Sub-Saharan",
    "subsaharienne": "subsaharienne", "europe": "Europe", "france": "France", "paris": "Paris",
    "geneva": "Geneva", "genève": "Genève", "buea": "Buea", "bamenda": "Bamenda",
    "garoua": "Garoua", "maroua": "Maroua", "bafoussam": "Bafoussam", "ebolowa": "Ebolowa",
}


def _to_nlm_sentence_case(title: str) -> str:
    """Fix ALL-CAPS scraped article titles into clean NLM sentence case while preserving acronyms & proper nouns."""
    t = re.sub(r"\*+([^*]+)\*+", r"\1", (title or "").strip()).rstrip(".")
    if not t:
        return ""
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return t
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    # Only transform when title is predominantly ALL-CAPS (> 72% uppercase) so valid mixed-case titles stay untouched
    if upper_ratio <= 0.72:
        return t
    words = t.split()
    out: List[str] = []
    for idx, w in enumerate(words):
        core = w.strip(".,:;!?()[]{}\"'")
        core_up = core.upper()
        if core_up in _PRESERVE_UPPER_TOKENS or re.fullmatch(r"[A-Z]{2,5}\d+[A-Z0-9\-]*", core_up):
            out.append(w)
        elif core.lower() in _PROPER_NOUN_CASE_MAP:
            repl = _PROPER_NOUN_CASE_MAP[core.lower()]
            out.append(w.replace(core, repl))
        else:
            lw = w.lower()
            if idx == 0 and lw:
                lw = lw[0].upper() + lw[1:]
            out.append(lw)
    return " ".join(out)


def _format_cited_date_nlm(lang: str = "fr") -> str:
    """Return NLM/FMSB access date stamp: '[cité le 26 nov 2026]' (FR) or '[cited 2026 Nov 26]' (EN)."""
    now = datetime.now(timezone.utc)
    if (lang or "fr").lower().startswith("en"):
        mon = _EN_MONTHS_SHORT[now.month - 1]
        return f"[cited {now.year} {mon} {now.day}]"
    mon = _FR_MONTHS_SHORT[now.month - 1]
    return f"[cité le {now.day} {mon} {now.year}]"


def _clean_journal_nlm(journal: str) -> str:
    """Format NLM journal title: upright (no italics), official NLM abbreviation, and no internal periods."""
    j = (journal or "").strip()
    if not j:
        return ""
    j = re.sub(r"\*+([^*]+)\*+", r"\1", j)
    # Remove periods after abbreviated words (e.g., 'N. Engl. J. Med.' -> 'N Engl J Med')
    j = re.sub(r"\.(?=\s|$)", "", j)
    j = re.sub(r"\s+", " ", j).strip(" .")
    mapped = _NLM_JOURNAL_ABBREV_MAP.get(j.lower())
    return mapped if mapped else j


def _normalize_single_author_vancouver(raw_author: str) -> str:
    """Normalize an individual author to strict NLM 'Surname Initials' while preserving corporate authors."""
    a = re.sub(r"^\s*(?:and|et|&)\s+", "", (raw_author or "").strip(), flags=re.IGNORECASE)
    a = re.sub(r"\s+", " ", a).strip(" ,.;")
    if not a or a.lower() in {"anonymous", "anonyme", "unknown", "inconnu", "auteurs non précisés"}:
        return ""
    if _CORPORATE_AUTHOR_RE.search(a) or len(a.split()) >= 5:
        return a
    # Format 1: "Surname, First Middle" or "Surname, J. A."
    if "," in a:
        surname, given = [p.strip() for p in a.split(",", 1)]
        initials = "".join(tok[0].upper() for tok in re.split(r"[\s.\-]+", given) if tok and tok[0].isalpha())
        return f"{surname} {initials}".strip() if initials else surname
    tokens = a.split()
    if len(tokens) == 1:
        return tokens[0]
    # Format 2: Already "Surname AB" or "Surname A.B." (last token(s) are initials)
    if re.fullmatch(r"(?:[A-Z]\.?){1,4}", tokens[-1]) and not re.fullmatch(r"(?:[A-Z]\.?){1,4}", tokens[0]):
        surname_parts = []
        init_parts = []
        for tok in tokens:
            if re.fullmatch(r"(?:[A-Z]\.?){1,4}", tok) and surname_parts:
                init_parts.append(tok.replace(".", ""))
            else:
                surname_parts.append(tok)
        if surname_parts and init_parts:
            return f"{' '.join(surname_parts)} {''.join(init_parts)}"
    # Format 3: "First Middle Surname" (e.g., "John A. Smith" -> "Smith JA")
    if len(tokens) in (2, 3) and not tokens[-1].endswith(".") and len(tokens[-1]) >= 2:
        surname = tokens[-1]
        given_tokens = tokens[:-1]
        # Only flip if first token looks like a given name or initial (not all-caps surname)
        if not tokens[0].isupper() or len(tokens[0].rstrip(".")) == 1:
            initials = "".join(
                t[0].upper()
                for t in re.split(r"[\s.\-]+", " ".join(given_tokens))
                if t and t[0].isalpha()
            )
            if initials:
                return f"{surname} {initials}"
    return a.replace(".", "")


def _format_authors_vancouver(authors: List[str]) -> str:
    """Format authors per NLM Citing Medicine: Surname Initials, max 6 then ', et al.'; empty if no author."""
    cleaned = [a.strip().rstrip(".") for a in (authors or []) if a and a.strip()]
    if not cleaned:
        return ""
    had_et_al = any(a.lower() in {"et al", "et al."} for a in cleaned)
    filtered = [
        _normalize_single_author_vancouver(a)
        for a in cleaned
        if a.lower() not in {"et al", "et al."}
    ]
    filtered = [a for a in filtered if a]
    if not filtered:
        return ""
    if len(filtered) > 6 or had_et_al:
        return ", ".join(filtered[:6]) + ", et al."
    return ", ".join(filtered)


def format_vancouver_citation(ref: ReferenceItem, lang: str = "fr") -> str:
    """Deterministically format a ReferenceItem according to full NLM / ICMJE / FMSB Vancouver rules."""
    is_en = (lang or "fr").lower().startswith("en")
    authors_str = _format_authors_vancouver(ref.authors)
    title_str = _to_nlm_sentence_case(ref.title or "")
    journal_str = _clean_journal_nlm(ref.journal or "")

    if ref.source_platform == "web" and not journal_str:
        parts = []
        if authors_str:
            parts.append(f"{authors_str.rstrip('.')}.")
        if title_str:
            t_internet = title_str if "[internet]" in title_str.lower() else f"{title_str} [Internet]"
            parts.append(f"{t_internet}.")
        cited_stamp = _format_cited_date_nlm(lang)
        if ref.year:
            parts.append(f"{ref.year} {cited_stamp}.")
        else:
            parts.append(f"{cited_stamp}.")
        if ref.url:
            clean_url = ref.url.strip().rstrip(".,;)")
            avail_label = "Available from:" if is_en else "Disponible sur :"
            parts.append(f"{avail_label} {clean_url}")
        return _normalize_vancouver_punctuation(" ".join(parts).strip())

    pub_parts = []
    if ref.year:
        pub_parts.append(str(ref.year))
    elided_pages = _abbreviate_page_range_nlm(ref.pages or "")
    if ref.volume:
        vol_issue = ref.volume.strip()
        if ref.issue:
            vol_issue += f"({ref.issue.strip()})"
        if elided_pages:
            vol_issue += f":{elided_pages}"
        pub_parts.append(vol_issue)
    elif elided_pages:
        pub_parts.append(f":{elided_pages}")

    pub_block = ";".join(pub_parts) if len(pub_parts) > 1 else "".join(pub_parts)

    segments: List[str] = []
    if authors_str:
        segments.append(f"{authors_str}.")
    if title_str:
        segments.append(f"{title_str}.")
    if journal_str:
        segments.append(f"{journal_str}.")
    if pub_block:
        segments.append(f"{pub_block}.")
    if ref.doi:
        clean_doi = re.sub(r"^(?:doi\s*:\s*|https?://doi\.org/)", "", ref.doi.strip(), flags=re.IGNORECASE).rstrip(".")
        if clean_doi:
            segments.append(f"doi:{clean_doi}.")
    if ref.pmid:
        segments.append(f"PMID:{ref.pmid.strip()}.")

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
    # Strict NLM/ICMJE bracket style: no spaces after commas (e.g. [1,4,6], [1–3], [1,3–5])
    return "[" + ",".join(tokens) + "]"


def _remap_citations_in_text(text: str, old_to_new: Dict[int, int]) -> str:
    """Remap and NLM-normalize all [n] / [a, b] / [a-b] citations without touching years or links."""
    if not text:
        return text

    def _repl(match: re.Match) -> str:
        nums = _expand_citation_group(match.group(1))
        if not nums:
            return match.group(0)
        remapped = [old_to_new.get(n, n) for n in nums] if old_to_new else nums
        formatted = _format_citation_numbers(remapped)
        return formatted if formatted else match.group(0)

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
    for spec in get_project_canvas(project):
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


def _extract_ordered_citations_from_section(
    sec: SectionState,
    section_images: Optional[List[Any]] = None,
) -> List[int]:
    """Return citation indices referenced in sec prose, tables, diagrams & figure captions in appearance order."""
    chunks: List[str] = []
    txt = _section_text_for_citation_scan(sec)
    if txt:
        chunks.append(txt)
    for ct in getattr(sec, "custom_tables", None) or []:
        if getattr(ct, "title", None):
            chunks.append(str(ct.title))
        for row in getattr(ct, "rows", None) or []:
            chunks.extend(str(c) for c in row if c)
    for dg in getattr(sec, "custom_diagrams", None) or []:
        if getattr(dg, "title", None):
            chunks.append(str(dg.title))
        if getattr(dg, "caption", None):
            chunks.append(str(dg.caption))
    for img in section_images or []:
        if getattr(img, "caption", None):
            chunks.append(str(img.caption))
    if not chunks:
        return []
    combined = "\n".join(chunks)
    seen: set[int] = set()
    ordered: List[int] = []
    for match in _CITATION_GROUP_RE.finditer(combined):
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
    imgs_by_sec: Dict[str, List[Any]] = {}
    for img in getattr(project, "images", None) or []:
        if getattr(img, "section_id", None):
            imgs_by_sec.setdefault(img.section_id, []).append(img)

    cited_old_order: List[int] = []
    seen_old: set[int] = set()
    for sid in _ordered_body_section_ids(project):
        sec = project.sections.get(sid)
        if sec is None:
            continue
        for old_idx in _extract_ordered_citations_from_section(sec, imgs_by_sec.get(sid)):
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

    lang = getattr(project, "language", "fr")
    project.references = cited_refs + uncited_refs
    for ref in project.references:
        if not ref.vancouver_citation:
            ref.vancouver_citation = format_vancouver_citation(ref, lang=lang)
        else:
            ref.vancouver_citation = _normalize_vancouver_punctuation(ref.vancouver_citation)

    remapped_indices = {o: n for o, n in old_to_new.items() if o != n}
    new_idx_to_ref_id = {r.order_index: r.ref_id for r in cited_refs if r.order_index}

    for sid in _ordered_body_section_ids(project):
        sec = project.sections.get(sid)
        if sec is None:
            continue
        if sec.content_markdown:
            sec.content_markdown = _remap_citations_in_text(sec.content_markdown, old_to_new)
        if sec.content_html:
            sec.content_html = _remap_citations_in_html(sec.content_html, old_to_new)
        if remapped_indices:
            for ct in getattr(sec, "custom_tables", None) or []:
                if getattr(ct, "title", None):
                    ct.title = _remap_citations_in_text(ct.title, old_to_new)
                if getattr(ct, "rows", None):
                    ct.rows = [
                        [_remap_citations_in_text(str(c), old_to_new) for c in row]
                        for row in ct.rows
                    ]
            for dg in getattr(sec, "custom_diagrams", None) or []:
                if getattr(dg, "title", None):
                    dg.title = _remap_citations_in_text(dg.title, old_to_new)
                if getattr(dg, "caption", None):
                    dg.caption = _remap_citations_in_text(dg.caption, old_to_new)
            for img in imgs_by_sec.get(sid, []):
                if getattr(img, "caption", None):
                    img.caption = _remap_citations_in_text(img.caption, old_to_new)
        sec_new_nums = _extract_ordered_citations_from_section(sec, imgs_by_sec.get(sid))
        sec.cited_ref_ids = [
            new_idx_to_ref_id[n] for n in sec_new_nums if n in new_idx_to_ref_id
        ]

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
