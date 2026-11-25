from __future__ import annotations

from html.parser import HTMLParser
import re
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt, RGBColor
from pydantic import BaseModel

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import get_doc_labels, get_localized_canvas, get_project_canvas, normalize_lang
from app.schemas.protocol import (
    AbbreviationItem,
    FormattingRules,
    ProtocolProject,
    SectionDiagram,
    SectionTable,
    StepStatus,
)

# Normalized keys never treated as real acronyms (table headers, Roman numerals, stop-tokens)
_ABBR_STOP_KEYS = {
    "",
    "N",
    "NO",
    "NR",
    "NB",
    "VS",
    "ET",
    "OU",
    "DE",
    "DU",
    "DES",
    "LA",
    "LE",
    "LES",
    "UN",
    "UNE",
    "EN",
    "AU",
    "AUX",
    "PAR",
    "SUR",
    "SOUS",
    "AVEC",
    "SANS",
    "POUR",
    "DANS",
    "OUI",
    "NON",
    "NA",
    "ND",
    "NP",
    "SIGLE",
    "SIGLES",
    "ACRONYME",
    "ACRONYMES",
    "ABREVIATION",
    "ABREVIATIONS",
    "SIGNIFICATION",
    "SIGNIFICATIONS",
    "DEFINITION",
    "DEFINITIONS",
    "SENS",
    "TERME",
    "TERMES",
    "DOMAINE",
    "CONTEXTE",
    "PRECISION",
    "PRECISIONS",
    "CODE",
    "CODES",
    "ITEM",
    "TOTAL",
    "NOTE",
    "NOTES",
    "PAGE",
    "PAGES",
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "XI",
    "XII",
    "FIG",
    "TAB",
    "CF",
    "EX",
    "VOIR",
    "ANNEXE",
    "ANNEXES",
    "SECTION",
    "CHAPITRE",
    "INTRODUCTION",
    "METHODOLOGIE",
    "METHODE",
    "RESULTATS",
    "OBJECTIF",
    "OBJECTIFS",
    "REFERENCES",
    "RESUME",
}

_FRONT_MATTER_SECTION_IDS = {
    "cover_page",
    "toc",
    "abbreviations",
    "list_of_tables",
    "list_of_figures",
}


class CaptionListEntry(BaseModel):
    seq_label: str  # "Tableau" or "Figure"
    number: int
    title: str
    display_label: str
    page_label: str
    section_id: str = ""


def clean_caption_title(title: str, seq_label: str = "Tableau") -> str:
    """Strip redundant 'Tableau N :' / 'Figure N.' prefixes without ever truncating words like 'des'.

    Important: Roman numerals are ONLY matched when immediately followed by an explicit delimiter
    (':', '.', '-', '–', '—') so 'Tableau des abréviations' is never clipped by 'd'.
    """
    raw = re.sub(r"\s+", " ", (title or "")).strip()
    raw = raw.replace("**", "").replace("__", "").strip()
    if not raw:
        return ""

    # Match explicit hierarchical/Arabic numbering (e.g. '2.1', '12') OR Roman numbering + mandatory delimiter
    prefix_re = re.compile(
        rf"^\s*(?:{re.escape(seq_label)}|Tableaux?|Tables?|Figures?|Fig\.?)\s*"
        r"(?:n[°o]\s*)?"
        r"(?:\d+(?:\.\d+)*\s*[:.\-–—]*\s*|[IVXLCDM]{1,6}(?:\.\d+)*\s*[:.\-–—]+\s*|[:.\-–—]+\s*)",
        flags=re.IGNORECASE,
    )
    cleaned = prefix_re.sub("", raw, count=1).strip()
    cleaned = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[IVXLCDM]{1,4})\s*[:\-–—]+\s*", "", cleaned).strip()
    return cleaned or raw


def _normalize_abbr_key(acronym: str) -> str:
    import unicodedata

    norm = unicodedata.normalize("NFKD", acronym or "")
    ascii_only = "".join(ch for ch in norm if not unicodedata.combining(ch))
    key = re.sub(r"[^A-Z0-9]", "", ascii_only.upper())
    return "UY1" if key == "UYI" else key


def _normalize_dedup_key(text: str) -> str:
    import unicodedata

    norm = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(ch for ch in norm if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", ascii_only).strip().lower()
    s = re.sub(r"^chapitre\s+[ivx1-9]+\s*[:.\-–]*\s*", "", s)
    s = re.sub(r"^\d+(?:[–\-]\d+)?(?:\.\d+)*\s*[:.)\-–]*\s*", "", s)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(?:et|de|du|des|la|le|les|l|d|a)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class _HtmlAbbrParser(HTMLParser):
    """Extract abbreviation pairs from HTML <table> rows and <li>/<p> items."""

    def __init__(self) -> None:
        super().__init__()
        self.pairs: List[Tuple[str, str]] = []
        self._in_tr = False
        self._in_cell = False
        self._cell_is_hdr = False
        self._row_cells: List[Tuple[bool, str]] = []
        self._cell_buf: List[str] = []
        self._in_block = False
        self._block_buf: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t == "tr":
            self._in_tr = True
            self._row_cells = []
        elif t in ("th", "td") and self._in_tr:
            self._in_cell = True
            self._cell_is_hdr = t == "th"
            self._cell_buf = []
        elif t in ("li", "p") and not self._in_tr:
            self._in_block = True
            self._block_buf = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("th", "td") and self._in_cell:
            txt = " ".join("".join(self._cell_buf).split())
            self._row_cells.append((self._cell_is_hdr, txt))
            self._in_cell = False
        elif t == "tr" and self._in_tr:
            if len(self._row_cells) >= 2 and not all(h for h, _ in self._row_cells):
                ac = self._row_cells[0][1].strip()
                ff = self._row_cells[1][1].strip()
                if ac and ff:
                    self.pairs.append((ac, ff))
            self._in_tr = False
        elif t in ("li", "p") and self._in_block:
            line = " ".join("".join(self._block_buf).split())
            m = re.match(r"^[•*\-–]?\s*([A-Za-z0-9À-ÖØ-öø-ÿ/\-+.]{2,20})\s*[:\-–=]\s*(.{3,})$", line)
            if m:
                self.pairs.append((m.group(1).strip(), m.group(2).strip()))
            self._in_block = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)
        elif self._in_block:
            self._block_buf.append(data)


def _clean_abbr_text(val: str) -> str:
    txt = re.sub(r"\*\*|\*|__|_", "", val or "")
    txt = re.sub(r"\s+", " ", txt).strip(" \t\r\n:;-–—")
    return txt


def _is_valid_acronym_candidate(ac: str, ff: str) -> bool:
    if not ac or not ff:
        return False
    if len(ac) < 2 or len(ac) > 20 or len(ff) < 3:
        return False
    key = _normalize_abbr_key(ac)
    if key in _ABBR_STOP_KEYS or len(key) < 2:
        return False
    # Ignore pure numbers or years or percentages
    if re.fullmatch(r"\d+(?:[.,]\d+)?%?", ac.strip()):
        return False
    if "page" in ff.lower() and len(ff) <= 12:
        return False
    # Must contain at least one letter in acronym
    if not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", ac):
        return False
    # Ignore normal lowercase sentence prefixes masquerading as acronyms (only short uppercase/mixed tokens)
    letters = [c for c in ac if c.isalpha()]
    upper_count = sum(1 for c in letters if c.isupper())
    if letters and upper_count == 0 and "/" not in ac:
        return False
    return True


def _merge_pair(
    store: Dict[str, Tuple[str, str]],
    acronym: str,
    full_form: str,
    precision: str = "",
) -> None:
    ac = _clean_abbr_text(acronym)
    if ac.upper() == "UYI":
        ac = "UY1"
    ff = _clean_abbr_text(full_form)
    prec = _clean_abbr_text(precision)

    # Reject table header phrases
    ac_norm = _normalize_abbr_key(ac)
    if ac_norm in _ABBR_STOP_KEYS or _normalize_abbr_key(ff) in _ABBR_STOP_KEYS:
        return
    if any(
        hdr in ac.lower()
        for hdr in ("sigle", "abréviation", "abreviation", "acronyme", "tableau ", "page ")
    ):
        return

    if not _is_valid_acronym_candidate(ac, ff):
        return

    # Combine succinct bilingual precision when column 1 is English and column 2 gives French translation
    if prec and ac_norm == "AMH" and "müllérienne" in prec.lower() and "müllérienne" not in ff.lower():
        ff = f"{ff} ({prec})"

    existing = store.get(ac_norm)
    if existing is None:
        store[ac_norm] = (ac, ff)
        return

    prev_ac, prev_ff = existing
    # Prefer acronym with cleaner uppercase formatting, and longer/richer French full_form
    best_ac = ac if sum(c.isupper() for c in ac) >= sum(c.isupper() for c in prev_ac) else prev_ac
    prev_has_french = any(ch in prev_ff for ch in "éèêàâùûôîïçÉÈÊÀÂ")
    new_has_french = any(ch in ff for ch in "éèêàâùûôîïçÉÈÊÀÂ")
    if (new_has_french and not prev_has_french and len(ff) >= 6) or (
        len(ff) > len(prev_ff) + 3 and "non précisé" not in ff.lower()
    ):
        best_ff = ff
    else:
        best_ff = prev_ff
    store[ac_norm] = (best_ac, best_ff)


# Curated medical, epidemiological, statistical & FMSB/CAMES acronyms (FR / EN)
# Used to resolve bare acronyms that appear in protocol prose without an inline parenthetical definition.
_KNOWN_MEDICAL_ABBREVIATIONS: Dict[str, Tuple[str, str]] = {
    "VIH": ("Virus de l'Immunodéficience Humaine", "Human Immunodeficiency Virus"),
    "HIV": ("Virus de l'Immunodéficience Humaine", "Human Immunodeficiency Virus"),
    "SIDA": ("Syndrome d'Immunodéficience Acquise", "Acquired Immunodeficiency Syndrome"),
    "AIDS": ("Syndrome d'Immunodéficience Acquise", "Acquired Immunodeficiency Syndrome"),
    "PVVIH": ("Personnes Vivant avec le VIH", "People Living with HIV"),
    "PLHIV": ("Personnes Vivant avec le VIH", "People Living with HIV"),
    "TARV": ("Traitement Antirétroviral", "Antiretroviral Therapy"),
    "ARV": ("Antirétroviral", "Antiretroviral"),
    "ART": ("Traitement Antirétroviral", "Antiretroviral Therapy"),
    "OMS": ("Organisation Mondiale de la Santé", "World Health Organization"),
    "WHO": ("Organisation Mondiale de la Santé", "World Health Organization"),
    "FMSB": ("Faculté de Médecine et des Sciences Biomédicales", "Faculty of Medicine and Biomedical Sciences"),
    "UY1": ("Université de Yaoundé I", "University of Yaoundé I"),
    "CAMES": ("Conseil Africain et Malgache pour l'Enseignement Supérieur", "African and Malagasy Council for Higher Education"),
    "CIER": ("Comité Institutionnel d'Éthique de la Recherche", "Institutional Research Ethics Committee"),
    "CIERFMSB": ("Comité Institutionnel d'Éthique et de la Recherche de la FMSB", "Institutional Research Ethics Committee of FMSB"),
    "CNERSH": ("Comité National d'Éthique de la Recherche pour la Santé Humaine", "National Ethics Committee for Human Health Research"),
    "MINSANTE": ("Ministère de la Santé Publique", "Ministry of Public Health"),
    "CHUY": ("Centre Hospitalier et Universitaire de Yaoundé", "Yaoundé University Teaching Hospital"),
    "HGY": ("Hôpital Général de Yaoundé", "Yaoundé General Hospital"),
    "HCY": ("Hôpital Central de Yaoundé", "Yaoundé Central Hospital"),
    "HGOPY": ("Hôpital Gynéco-Obstétrique et Pédiatrique de Yaoundé", "Yaoundé Gynaeco-Obstetric and Pediatric Hospital"),
    "HGD": ("Hôpital Général de Douala", "Douala General Hospital"),
    "HLD": ("Hôpital Laquintinie de Douala", "Laquintinie Hospital of Douala"),
    "CRF": ("Fiche de Collecte des Données (Case Report Form)", "Case Report Form"),
    "IMC": ("Indice de Masse Corporelle", "Body Mass Index"),
    "BMI": ("Indice de Masse Corporelle (Body Mass Index)", "Body Mass Index"),
    "HTA": ("Hypertension Artérielle", "Arterial Hypertension"),
    "DT2": ("Diabète de Type 2", "Type 2 Diabetes Mellitus"),
    "ECG": ("Électrocardiogramme", "Electrocardiogram"),
    "TDM": ("Tomodensitométrie", "Computed Tomography"),
    "IRM": ("Imagerie par Résonance Magnétique", "Magnetic Resonance Imaging"),
    "NFS": ("Numération Formule Sanguine", "Complete Blood Count"),
    "CRP": ("Protéine C-Réactive", "C-Reactive Protein"),
    "PCR": ("Réaction en Chaîne par Polymérase", "Polymerase Chain Reaction"),
    "CD4": ("Lymphocytes T CD4+", "CD4+ T Lymphocytes"),
    "IC": ("Intervalle de Confiance", "Confidence Interval"),
    "CI": ("Intervalle de Confiance (Confidence Interval)", "Confidence Interval"),
    "OR": ("Rapport des Cotes (Odds Ratio)", "Odds Ratio"),
    "ORA": ("Odds Ratio Ajusté", "Adjusted Odds Ratio"),
    "AOR": ("Odds Ratio Ajusté (Adjusted Odds Ratio)", "Adjusted Odds Ratio"),
    "RR": ("Risque Relatif", "Relative Risk"),
    "HR": ("Rapport de Risque (Hazard Ratio)", "Hazard Ratio"),
    "ET": ("Écart-Type", "Standard Deviation"),
    "SD": ("Écart-Type (Standard Deviation)", "Standard Deviation"),
    "IQR": ("Écart Interquartile (Interquartile Range)", "Interquartile Range"),
    "EIQ": ("Écart Interquartile", "Interquartile Range"),
    "SPSS": ("Statistical Package for the Social Sciences", "Statistical Package for the Social Sciences"),
    "ANOVA": ("Analyse de la Variance", "Analysis of Variance"),
    "ROC": ("Receiver Operating Characteristic", "Receiver Operating Characteristic"),
    "AUC": ("Aire Sous la Courbe (Area Under the Curve)", "Area Under the Curve"),
    "CONSORT": ("Consolidated Standards of Reporting Trials", "Consolidated Standards of Reporting Trials"),
    "PRISMA": ("Preferred Reporting Items for Systematic Reviews and Meta-Analyses", "Preferred Reporting Items for Systematic Reviews and Meta-Analyses"),
    "STROBE": ("Strengthening the Reporting of Observational Studies in Epidemiology", "Strengthening the Reporting of Observational Studies in Epidemiology"),
    "FCFA": ("Franc de la Coopération Financière en Afrique Centrale", "Central African CFA Franc"),
    "IST": ("Infection Sexuellement Transmissible", "Sexually Transmitted Infection"),
    "VHB": ("Virus de l'Hépatite B", "Hepatitis B Virus"),
    "VHC": ("Virus de l'Hépatite C", "Hepatitis C Virus"),
    "HBA1C": ("Hémoglobine Glyquée", "Glycated Hemoglobin"),
    "DFG": ("Débit de Filtration Glomérulaire", "Glomerular Filtration Rate"),
    "IRC": ("Insuffisance Rénale Chronique", "Chronic Kidney Disease"),
    "IRA": ("Insuffisance Rénale Aiguë", "Acute Kidney Injury"),
    "AVC": ("Accident Vasculaire Cérébral", "Cerebrovascular Accident / Stroke"),
    "IDM": ("Infarctus du Myocarde", "Myocardial Infarction"),
    "BPCO": ("Bronchopneumopathie Chronique Obstructive", "Chronic Obstructive Pulmonary Disease"),
    "GCS": ("Score de Glasgow (Glasgow Coma Scale)", "Glasgow Coma Scale"),
    "EVA": ("Échelle Visuelle Analogique", "Visual Analog Scale"),
    "IA": ("Intelligence Artificielle", "Artificial Intelligence"),
    "AI": ("Intelligence Artificielle (Artificial Intelligence)", "Artificial Intelligence"),
    "LLM": ("Grand Modèle de Langage (Large Language Model)", "Large Language Model"),
}

_LEADING_ARTICLES_RE = re.compile(
    r"^(?:l['’]|d['’]|le|la|les|un|une|des|du|de|au|aux|en|et|ou|par|pour|sur|dans|chez|avec|the|a|an|of|for|in|by)\s+",
    re.IGNORECASE,
)


def _extract_inline_acronyms_from_text(text: str, lang: str = "fr") -> List[Tuple[str, str]]:
    """Detect explicit 'Full Meaning (SIGLE)', 'SIGLE (Full Meaning)', and known bare medical acronyms."""
    results: List[Tuple[str, str]] = []
    if not text:
        return results
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\*\*|\*|__", "", plain)
    plain = re.sub(r"\s+", " ", plain)

    # Pattern 1: "personnes vivant avec le VIH (PVVIH)" OR "Faculté de Médecine ... (FMSB)"
    # Accepts BOTH capitalized and lowercase French/English multi-word definitions (2..10 words)
    p_def_then_sigle = re.compile(
        r"\b([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'’\-]+){1,9})\s*\(([A-Z][A-Za-z0-9\-/]{1,11})\)"
    )
    for m in p_def_then_sigle.finditer(plain):
        raw_phrase = m.group(1).strip()
        sigle = m.group(2).strip()
        sigle_letters = re.sub(r"[^A-Z]", "", sigle.upper())
        if len(sigle_letters) < 2:
            continue
        # Trim sentence-prefix words until we align with the first letter of the acronym
        words = [w for w in raw_phrase.split() if w]
        start_idx = None
        for idx_w, w in enumerate(words):
            clean_w = re.sub(r"^(?:[ldLD]['’])", "", w)
            if clean_w and clean_w[0].upper() == sigle_letters[0]:
                start_idx = idx_w
                break
        if start_idx is None:
            continue
        trimmed_phrase = " ".join(words[start_idx:]).strip()
        while _LEADING_ARTICLES_RE.match(trimmed_phrase):
            nxt = _LEADING_ARTICLES_RE.sub("", trimmed_phrase, count=1).strip()
            if len(nxt.split()) >= 2:
                trimmed_phrase = nxt
            else:
                break
        if len(trimmed_phrase) >= 5 and len(trimmed_phrase.split()) >= 2:
            pretty = trimmed_phrase[0].upper() + trimmed_phrase[1:]
            results.append((sigle, pretty))

    # Pattern 2: "WHO (World Health Organization)"
    p_sigle_then_def = re.compile(
        r"\b([A-Z][A-Z0-9\-/]{1,10})\s*\(([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'’\-]+){1,9})\)"
    )
    for m in p_sigle_then_def.finditer(plain):
        sigle = m.group(1).strip()
        phrase = m.group(2).strip()
        words = [w for w in re.split(r"[\s'’\-]+", phrase) if w]
        sigle_letters = re.sub(r"[^A-Z]", "", sigle)
        if len(words) >= 2 and len(sigle_letters) >= 2 and any(w[0].upper() == sigle_letters[0] for w in words[:3]):
            pretty = phrase[0].upper() + phrase[1:]
            results.append((sigle, pretty))

    # Pattern 3: Known bare medical/statistical/FMSB acronyms appearing as standalone tokens
    is_en = normalize_lang(lang) == "en"
    for tok in set(re.findall(r"\b([A-Z][A-Za-z0-9\-]{1,11})\b", plain)):
        norm_k = _normalize_abbr_key(tok)
        if norm_k in _KNOWN_MEDICAL_ABBREVIATIONS:
            fr_def, en_def = _KNOWN_MEDICAL_ABBREVIATIONS[norm_k]
            results.append((tok, en_def if is_en else fr_def))

    return results


def collect_deduplicated_abbreviations(project: ProtocolProject) -> List[AbbreviationItem]:
    """Consolidate all project abbreviations from structured data, custom tables, HTML/MD & body text."""
    merged: Dict[str, Tuple[str, str]] = {}

    # 1. Existing structured project.abbreviations
    for item in project.abbreviations:
        _merge_pair(merged, item.acronym, item.full_form)

    # 2. Authored abbreviations section: Markdown bullets, pipe tables, HTML, AND custom_tables
    abbr_sec = project.sections.get("abbreviations")
    if abbr_sec is not None:
        # 2a. Custom tables previously attached to 'abbreviations'
        for ct in getattr(abbr_sec, "custom_tables", None) or []:
            for row in ct.rows:
                if not row or len(row) < 2:
                    continue
                prec = row[2] if len(row) >= 3 else ""
                _merge_pair(merged, str(row[0]), str(row[1]), precision=str(prec))

        # 2b. Markdown content (bullets + pipe tables)
        md = abbr_sec.content_markdown or ""
        for ln in md.splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            # Pipe table row
            if s.startswith("|") and s.endswith("|"):
                if re.match(r"^\|\s*[:\-]+\s*\|", s):
                    continue
                cells = [c.strip() for c in s.strip("|").split("|")]
                if len(cells) >= 2:
                    _merge_pair(merged, cells[0], cells[1], precision=cells[2] if len(cells) >= 3 else "")
                continue
            # Bullet or key: value line
            m_b = re.match(
                r"^(?:[-*•]\s*)?(?:\*\*)?\s*([A-Za-z0-9À-ÖØ-öø-ÿ/\-+.]{2,20})\s*(?:\*\*)?\s*[:\-–=]\s*(.{3,})$",
                s,
            )
            if m_b:
                _merge_pair(merged, m_b.group(1), m_b.group(2))

        # 2c. HTML tables/lists inside abbreviations section
        if abbr_sec.content_html:
            hp = _HtmlAbbrParser()
            try:
                hp.feed(abbr_sec.content_html)
                for ac, ff in hp.pairs:
                    _merge_pair(merged, ac, ff)
            except Exception:
                pass

    # 3. Scan cover metadata, body sections, custom tables & diagrams for explicit & known acronyms
    proj_lang = getattr(project, "language", "fr") or "fr"
    cover_blob = " ".join(
        [
            project.cover.title or "",
            project.cover.faculty_fr or "",
            project.cover.university_fr or "",
            " ".join(project.cover.host_institutions or []),
            " ".join(project.cover.ethics_committees or []),
        ]
    )
    for ac, ff in _extract_inline_acronyms_from_text(cover_blob, lang=proj_lang):
        if _normalize_abbr_key(ac) not in merged:
            _merge_pair(merged, ac, ff)

    for spec in get_project_canvas(project):
        if spec.id in _FRONT_MATTER_SECTION_IDS:
            continue
        sec = project.sections.get(spec.id)
        if not sec:
            continue
        for ac, ff in _extract_inline_acronyms_from_text(sec.content_markdown, lang=proj_lang):
            if _normalize_abbr_key(ac) not in merged:
                _merge_pair(merged, ac, ff)
        for ct in getattr(sec, "custom_tables", None) or []:
            for row in ct.rows:
                for cell in row:
                    for ac, ff in _extract_inline_acronyms_from_text(str(cell), lang=proj_lang):
                        if _normalize_abbr_key(ac) not in merged:
                            _merge_pair(merged, ac, ff)
        for dg in getattr(sec, "custom_diagrams", None) or []:
            dg_blob = " ".join(
                [dg.title or "", dg.caption or ""]
                + [f"{n.title} {' '.join(n.items or [])}" for n in (dg.nodes or [])]
            )
            for ac, ff in _extract_inline_acronyms_from_text(dg_blob, lang=proj_lang):
                if _normalize_abbr_key(ac) not in merged:
                    _merge_pair(merged, ac, ff)

    sorted_items = [
        AbbreviationItem(acronym=ac, full_form=ff)
        for _, (ac, ff) in sorted(merged.items(), key=lambda kv: kv[1][0].upper())
    ]
    return sorted_items


def _render_abbreviations_markdown_and_html(
    items: List[AbbreviationItem],
    lang: str = "fr",
) -> Tuple[str, str]:
    """Render a SINGLE unified 2-column A-Z table (without page column)."""
    labels = get_doc_labels(lang)
    col_abbr, col_meaning = labels["abbr_headers"].split("|")
    if not items:
        empty_txt = labels["no_abbreviations"]
        md = f"### {labels['anchor_abbreviations']}\n\n{empty_txt}"
        html = f'<p style="text-align:left;font-style:italic;color:#64748b;">{escape(empty_txt)}</p>'
        return md, html

    md_lines = [
        f"| {col_abbr} | {col_meaning} |",
        "| :--- | :--- |",
    ]
    html_rows: List[str] = []
    for item in items:
        ac = item.acronym.replace("|", "/")
        ff = item.full_form.replace("|", "/")
        md_lines.append(f"| **{ac}** | {ff} |")
        html_rows.append(
            "<tr>"
            '<td style="text-align:left;padding:6px 10px;font-weight:700;color:#6C2826;width:24%;'
            'border-bottom:1px solid #e2e8f0;vertical-align:top;">'
            f"{escape(item.acronym)}</td>"
            '<td style="text-align:left;padding:6px 10px;color:#0f172a;width:76%;'
            'border-bottom:1px solid #e2e8f0;vertical-align:top;">'
            f"{escape(item.full_form)}</td>"
            "</tr>"
        )

    html = (
        '<table style="width:100%;border-collapse:collapse;text-align:left;font-size:11pt;margin-top:8px;">'
        "<thead><tr>"
        f'<th style="text-align:left;padding:8px 10px;background-color:#6C2826;color:#ffffff;width:24%;">{escape(col_abbr)}</th>'
        f'<th style="text-align:left;padding:8px 10px;background-color:#6C2826;color:#ffffff;width:76%;">{escape(col_meaning)}</th>'
        "</tr></thead>"
        f"<tbody>{''.join(html_rows)}</tbody>"
        "</table>"
    )
    return "\n".join(md_lines), html


def build_unified_abbreviations_list(
    project: ProtocolProject,
    mark_validated: bool = True,
) -> Tuple[str, str, List[AbbreviationItem]]:
    """Unify & deduplicate ALL project abbreviations into ONE clean alphabetical table (no duplicates, no page column)."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    items = collect_deduplicated_abbreviations(project)
    project.abbreviations = items

    md_out, html_out = _render_abbreviations_markdown_and_html(items, lang=lang)
    abbr_sec = project.sections.get("abbreviations")
    if abbr_sec is not None:
        # Eliminate redundant separate custom tables & stray diagrams from abbreviations
        abbr_sec.custom_tables = []
        abbr_sec.custom_diagrams = []
        abbr_sec.content_markdown = md_out
        abbr_sec.content_html = html_out
        if mark_validated:
            abbr_sec.status = StepStatus.VALIDATED
        abbr_sec.audit_notes = []

    return md_out, html_out, items


def deduplicate_project_media_and_tables(project: ProtocolProject) -> None:
    """Clean stray front-matter tables/diagrams, transport images locally, and synchronize Module 1 -> Module 2 tables."""
    from app.services.media_transport import (
        synchronize_module1_to_module2_tables,
        transport_and_repair_project_images,
    )

    transport_and_repair_project_images(project)
    synchronize_module1_to_module2_tables(project)

    for fm_id in ("cover_page", "toc", "abbreviations", "list_of_tables", "list_of_figures"):
        sec = project.sections.get(fm_id)
        if sec is not None:
            sec.custom_tables = []
            sec.custom_diagrams = []

    # Remove stray front-matter diagram from preliminares if any exists
    if "preliminaires" in project.sections:
        psec = project.sections["preliminaires"]
        psec.custom_tables = []
        psec.custom_diagrams = []

    for spec in get_project_canvas(project):
        if spec.id in _FRONT_MATTER_SECTION_IDS:
            continue
        sec = project.sections.get(spec.id)
        if sec is None:
            continue

        # Deduplicate custom tables within section by cleaned normalized title
        if getattr(sec, "custom_tables", None):
            dedup_tables: List[SectionTable] = []
            seen_tbl_keys: set[str] = set()
            for ct in sec.custom_tables:
                if not ct.headers and not ct.rows:
                    continue
                cleaned_title = clean_caption_title(ct.title, "Tableau") or ct.title
                tkey = _normalize_dedup_key(cleaned_title) or ct.table_id
                if tkey in seen_tbl_keys:
                    continue
                seen_tbl_keys.add(tkey)
                ct.title = cleaned_title
                dedup_tables.append(ct)
            sec.custom_tables = dedup_tables

        # Deduplicate custom diagrams within section by cleaned normalized title
        if getattr(sec, "custom_diagrams", None):
            dedup_diags: List[SectionDiagram] = []
            seen_dg_keys: set[str] = set()
            for dg in sec.custom_diagrams:
                if not dg.nodes:
                    continue
                cleaned_title = clean_caption_title(dg.title, "Figure") or dg.title
                dkey = _normalize_dedup_key(cleaned_title) or dg.diagram_id
                if dkey in seen_dg_keys:
                    continue
                seen_dg_keys.add(dkey)
                dg.title = cleaned_title
                dedup_diags.append(dg)
            sec.custom_diagrams = dedup_diags

    # Clean approved image titles (preserve all distinct user-uploaded figures; never silently unapprove!)
    seen_img_titles: Dict[str, int] = {}
    for img in project.images:
        if img.approved:
            img.title = clean_caption_title(img.title, "Figure") or img.title
            ikey = f"{img.target_section_id}::{_normalize_dedup_key(img.title)}"
            count = seen_img_titles.get(ikey, 0) + 1
            seen_img_titles[ikey] = count
            if count > 1 and not re.search(r"\(\d+\)$", img.title):
                img.title = f"{img.title} ({count})"


def _render_caption_list_markdown_and_html(
    seq_label: str,
    entries: List[CaptionListEntry],
    lang: str = "fr",
) -> Tuple[str, str]:
    is_en = normalize_lang(lang) == "en"
    is_tbl = seq_label.lower() in ("tableau", "table")
    col_title = (
        ("No. & Table Title" if is_tbl else "No. & Figure Title")
        if is_en
        else (f"N° & Titre du {seq_label.lower()}" if is_tbl else "N° & Titre de la figure")
    )
    empty_msg = (
        ("No numbered tables are currently present in this protocol." if is_tbl else "No numbered figures or conceptual diagrams are currently present in this protocol.")
        if is_en
        else (
            "Aucun tableau numéroté n'est encore présent dans le protocole."
            if is_tbl
            else "Aucune figure ou schéma vectoriel n'est encore présent dans le protocole."
        )
    )
    if not entries:
        md = f"{empty_msg}"
        html = f'<p style="text-align:left;font-style:italic;color:#64748b;">{escape(empty_msg)}</p>'
        return md, html

    md_lines = [
        f"| {col_title} | Page |",
        "| :--- | ---: |",
    ]
    html_rows: List[str] = []
    for e in entries:
        safe_lbl = e.display_label.replace("|", "/")
        md_lines.append(f"| {safe_lbl} | {e.page_label} |")
        html_rows.append(
            "<tr>"
            '<td style="text-align:left;padding:5px 8px;color:#0f172a;border-bottom:1px dotted #cbd5e1;">'
            f"{escape(e.display_label)}</td>"
            '<td style="text-align:right;padding:5px 6px;font-weight:700;color:#6C2826;white-space:nowrap;'
            'border-bottom:1px dotted #cbd5e1;">'
            f"{escape(e.page_label)}</td>"
            "</tr>"
        )

    md_lines.append("")
    md_lines.append(
        "*Note: Deduplicated sequential numbering and A4 pagination automatically synchronized with Word.*"
        if is_en
        else "*Note : numérotation séquentielle sans doublon et pagination A4 synchronisée automatiquement avec Word.*"
    )
    html = (
        '<table style="width:100%;border-collapse:collapse;text-align:left;font-size:11pt;margin-top:8px;">'
        "<thead><tr>"
        f'<th style="text-align:left;padding:6px 8px;border-bottom:2px solid #6C2826;color:#6C2826;">{escape(col_title)}</th>'
        '<th style="text-align:right;padding:6px 6px;border-bottom:2px solid #6C2826;color:#6C2826;">Page</th>'
        "</tr></thead>"
        f"<tbody>{''.join(html_rows)}</tbody>"
        "</table>"
    )
    return "\n".join(md_lines), html


def extract_project_tables_and_figures_entries(
    project: ProtocolProject,
    pagination_plan: Optional[Any] = None,
    use_gemini_decision: bool = True,
) -> Tuple[List[CaptionListEntry], List[CaptionListEntry], Any]:
    """Extract ordered, deduplicated Table and Figure entries paired with their exact A4 pages."""
    from app.services.page_layout_engine import _build_layout_blocks, plan_document_pagination

    lang = normalize_lang(getattr(project, "language", "fr"))
    labels = get_doc_labels(lang)
    tbl_seq = labels["table_seq"]
    fig_seq = labels["figure_seq"]

    deduplicate_project_media_and_tables(project)
    plan = pagination_plan or plan_document_pagination(project, use_gemini_decision=use_gemini_decision)
    all_blocks, _ = _build_layout_blocks(project)

    block_start_page: Dict[str, str] = {}
    for pg in plan.pages:
        for bid in pg.block_ids:
            if bid not in block_start_page:
                block_start_page[bid] = pg.page_label

    table_entries: List[CaptionListEntry] = []
    figure_entries: List[CaptionListEntry] = []
    seen_tbl_titles: set[str] = set()
    seen_fig_titles: set[str] = set()

    for blk in all_blocks:
        if blk.kind == "table_with_caption" and blk.title.strip():
            raw_title = clean_caption_title(blk.title, tbl_seq)
            dedup_k = f"{blk.section_id}::{_normalize_dedup_key(raw_title)}"
            if not raw_title or dedup_k in seen_tbl_titles:
                continue
            seen_tbl_titles.add(dedup_k)
            num = len(table_entries) + 1
            pg_lbl = (
                block_start_page.get(blk.block_id)
                or plan.table_page_map.get(blk.title)
                or plan.section_start_page.get(blk.section_id)
                or "1"
            )
            table_entries.append(
                CaptionListEntry(
                    seq_label=tbl_seq,
                    number=num,
                    title=raw_title,
                    display_label=f"{tbl_seq} {num} : {raw_title}",
                    page_label=pg_lbl,
                    section_id=blk.section_id,
                )
            )
        elif blk.kind == "figure_with_caption" and blk.title.strip():
            raw_title = clean_caption_title(blk.title, fig_seq)
            dedup_k = f"{blk.section_id}::{_normalize_dedup_key(raw_title)}"
            if not raw_title or dedup_k in seen_fig_titles:
                continue
            seen_fig_titles.add(dedup_k)
            num = len(figure_entries) + 1
            pg_lbl = (
                block_start_page.get(blk.block_id)
                or plan.figure_page_map.get(blk.title)
                or plan.section_start_page.get(blk.section_id)
                or "1"
            )
            figure_entries.append(
                CaptionListEntry(
                    seq_label=fig_seq,
                    number=num,
                    title=raw_title,
                    display_label=f"{fig_seq} {num}. {raw_title}",
                    page_label=pg_lbl,
                    section_id=blk.section_id,
                )
            )

    return table_entries, figure_entries, plan


def build_unified_tables_and_figures_lists(
    project: ProtocolProject,
    pagination_plan: Optional[Any] = None,
    mark_validated: bool = True,
    use_gemini_decision: bool = True,
) -> Tuple[List[CaptionListEntry], List[CaptionListEntry], Any]:
    """Update sections['list_of_tables'] & sections['list_of_figures'] with clean deduplicated paginated lists."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    labels = get_doc_labels(lang)
    table_entries, figure_entries, plan = extract_project_tables_and_figures_entries(
        project,
        pagination_plan=pagination_plan,
        use_gemini_decision=use_gemini_decision,
    )

    lot_md, lot_html = _render_caption_list_markdown_and_html(labels["table_seq"], table_entries, lang=lang)
    lot_sec = project.sections.get("list_of_tables")
    if lot_sec is not None:
        lot_sec.custom_tables = []
        lot_sec.custom_diagrams = []
        lot_sec.content_markdown = lot_md
        lot_sec.content_html = lot_html
        if mark_validated:
            lot_sec.status = StepStatus.VALIDATED
        lot_sec.audit_notes = []

    lof_md, lof_html = _render_caption_list_markdown_and_html(labels["figure_seq"], figure_entries, lang=lang)
    lof_sec = project.sections.get("list_of_figures")
    if lof_sec is not None:
        lof_sec.custom_tables = []
        lof_sec.custom_diagrams = []
        lof_sec.content_markdown = lof_md
        lof_sec.content_html = lof_html
        if mark_validated:
            lof_sec.status = StepStatus.VALIDATED
        lof_sec.audit_notes = []

    return table_entries, figure_entries, plan


def _apply_list_entry_font(run, font_family: str, size_pt: int) -> None:
    run.font.name = font_family
    run.font.size = Pt(size_pt)
    run.bold = False
    run.font.color.rgb = RGBColor(0, 0, 0)
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), font_family)
    rFonts.set(qn("w:hAnsi"), font_family)
    rFonts.set(qn("w:cs"), font_family)


def insert_word_automatic_caption_list(
    doc: Document,
    anchor_p: Any,
    seq_label: str,
    entries: List[CaptionListEntry],
    fmt: FormattingRules,
    lang: str = "fr",
) -> Any:
    """Insert a native Word TOC \\h \\z \\c <seq_label> field pre-populated with left-aligned dot-leader entries."""
    norm_lang = normalize_lang(lang)
    font_family = fmt.font_family or "Times New Roman"
    base_pt = int(fmt.body_font_size_pt or 12)
    usable_cm = max(12.0, 21.0 - 2.0 * float(fmt.margin_cm or 2.5))
    right_tab_dxa = int(round(usable_cm * 566.929))

    note_el = OxmlElement("w:p")
    anchor_p._p.addnext(note_el)
    note_p = doc.paragraphs[0].__class__(note_el, anchor_p._parent)
    note_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    note_p.paragraph_format.space_before = Pt(0)
    note_p.paragraph_format.space_after = Pt(6)
    r_note = note_p.add_run(
        "Automatically generated list (updates automatically or press F9 in Word)."
        if norm_lang == "en"
        else "Liste générée automatiquement (actualisation automatique ou touche F9 sous Word)."
    )
    _apply_list_entry_font(r_note, font_family, size_pt=9)
    r_note.italic = True
    r_note.font.color.rgb = RGBColor(100, 116, 139)

    cursor_p = note_p
    instr = f' TOC \\h \\z \\c "{seq_label}" '

    if not entries:
        empty_el = OxmlElement("w:p")
        cursor_p._p.addnext(empty_el)
        empty_p = doc.paragraphs[0].__class__(empty_el, cursor_p._parent)
        empty_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r = empty_p.add_run()
        r._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>'))
        r._r.append(
            parse_xml(f'<w:instrText {nsdecls("w")} xml:space="preserve">{instr}</w:instrText>')
        )
        r._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>'))
        is_table = seq_label.strip().lower() in ("tableau", "table")
        if norm_lang == "en":
            empty_msg = (
                "No numbered table in the protocol."
                if is_table
                else "No numbered figure in the protocol."
            )
        else:
            empty_msg = (
                "Aucun tableau numéroté dans le protocole."
                if is_table
                else "Aucune figure numérotée dans le protocole."
            )
        r_msg = empty_p.add_run(empty_msg)
        _apply_list_entry_font(r_msg, font_family, size_pt=base_pt)
        r_msg.italic = True
        r_end = empty_p.add_run()
        r_end._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>'))
        return empty_p

    for idx, entry in enumerate(entries):
        p_el = OxmlElement("w:p")
        cursor_p._p.addnext(p_el)
        p_obj = doc.paragraphs[0].__class__(p_el, cursor_p._parent)

        pPr = parse_xml(
            f'<w:pPr {nsdecls("w")}>'
            '  <w:pStyle w:val="Tabledesillustrations"/>'
            f'  <w:tabs><w:tab w:val="right" w:leader="dot" w:pos="{right_tab_dxa}"/></w:tabs>'
            '  <w:spacing w:before="30" w:after="30" w:line="252" w:lineRule="auto"/>'
            '  <w:ind w:left="0"/>'
            '  <w:jc w:val="left"/>'
            "</w:pPr>"
        )
        p_el.append(pPr)

        if idx == 0:
            r_begin = p_obj.add_run()
            r_begin._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>'))
            r_begin._r.append(
                parse_xml(f'<w:instrText {nsdecls("w")} xml:space="preserve">{instr}</w:instrText>')
            )
            r_begin._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>'))

        r_label = p_obj.add_run(entry.display_label)
        _apply_list_entry_font(r_label, font_family, size_pt=base_pt)

        r_tab = p_obj.add_run()
        _apply_list_entry_font(r_tab, font_family, size_pt=base_pt)
        r_tab._r.append(parse_xml(f'<w:tab {nsdecls("w")}/>'))

        r_pg = p_obj.add_run(entry.page_label)
        _apply_list_entry_font(r_pg, font_family, size_pt=base_pt)

        if idx == len(entries) - 1:
            r_end = p_obj.add_run()
            r_end._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>'))

        cursor_p = p_obj

    return cursor_p


def generate_all_lists_and_toc(
    project: ProtocolProject,
    use_gemini_decision: bool = True,
) -> Dict[str, Any]:
    """Master tool: build deduplicated Abbreviations, Vancouver References, List of Tables, List of Figures & A4 TOC together."""
    from app.services.page_layout_engine import plan_document_pagination
    from app.services.toc_builder import generate_clean_toc_section_content
    from app.services.vancouver import build_and_validate_vancouver_references

    # 1. Consolidate & deduplicate abbreviations (clears multiple tables & stray custom_tables)
    _, _, abbr_items = build_unified_abbreviations_list(project, mark_validated=True)

    # 1b. Deduplicate, validate via Gemini Decision 1 & format Vancouver references as numbered list
    ref_report = build_and_validate_vancouver_references(
        project, use_gemini_decision=use_gemini_decision, mark_validated=False
    )

    # 2. Deduplicate & sanitize section tables/figures before A4 pagination pass
    deduplicate_project_media_and_tables(project)

    # 3. Compute exact A4 pagination (+ optional Gemini Decision 1)
    plan = plan_document_pagination(project, use_gemini_decision=use_gemini_decision)

    # 4. Build List of Tables & List of Figures paired with exact A4 page numbers
    table_entries, figure_entries, plan = build_unified_tables_and_figures_lists(
        project, pagination_plan=plan, mark_validated=True
    )

    # 5. Build clean 3-level FMSB Table of Contents paired with exact A4 page numbers
    toc_md, toc_html, toc_entries = generate_clean_toc_section_content(
        project, pagination_plan=plan
    )
    toc_sec = project.sections.get("toc")
    if toc_sec is not None:
        toc_sec.custom_tables = []
        toc_sec.custom_diagrams = []
        toc_sec.content_markdown = toc_md
        toc_sec.content_html = toc_html
        toc_sec.status = StepStatus.VALIDATED
        toc_sec.audit_notes = []

    return {
        "abbreviations_count": len(abbr_items),
        "references_count": ref_report.get("references_count", len(project.references)),
        "tables_count": len(table_entries),
        "figures_count": len(figure_entries),
        "entries_count": len(toc_entries),
        "toc_entries": toc_entries,
        "table_entries": table_entries,
        "figure_entries": figure_entries,
        "abbreviations": abbr_items,
        "plan": plan,
    }
