import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from docx import Document
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import get_localized_canvas, normalize_lang
from app.schemas.protocol import (
    EntryMode,
    InternalLibraryEntry,
    InternalSourceDocument,
    ProtocolProject,
    ReferenceItem,
    SectionState,
    StepStatus,
)
from app.services.annexe_engine import extract_all_sub_annexes_from_blocks
from app.services.crf_builder import (
    extract_crf_annexe_block,
    extract_crf_fields_from_project,
    is_crf_annexe_content,
    sync_crf_to_project,
)
from app.services.crf_extractor import (
    compile_crf_fields_hybrid,
    detect_crf_boundaries_in_blocks,
    reconcile_crf_fields,
    slice_crf_from_text_or_blocks,
)
from app.services.drafting import deterministic_section_checks
from app.services.figure_extractor import extract_images_from_uploaded_document
from app.services.gemini_client import gemini_router
from app.services.pipeline_tracker import emit_pipeline_step
from app.services.rich_formatter import markdown_to_clean_html
from app.services.vancouver import (
    format_vancouver_citation,
    parse_raw_vancouver_line,
    refresh_vancouver_references,
)

HEADING_PATTERN = re.compile(
    r"^("
    r"(?:CHAPITRE|CHAPTER)\s+[IVX0-9]+|"
    r"INTRODUCTION(\s+(?:G[ÉE]N[ÉE]RALE|GENERAL))?|"
    r"PROBL[ÉE]MATIQUE|PROBLEM\s+STATEMENT|"
    r"REVUE\s+DE\s+LA\s+LITT[ÉE]RATURE|LITERATURE\s+REVIEW|"
    r"M[ÉE]THODOLOGIE|METHODOLOGY|MAT[ÉE]RIELS?\s+ET\s+M[ÉE]THODES?|MATERIALS?\s+AND\s+METHODS?|"
    r"(?:(?:[IVX]+|\d+)\s*[.\-–:]\s*)?(?:R[ÉE]F[ÉE]RENCES(?:\s+BIBLIOGRAPHIQUES)?|BIBLIOGRAPHIE|BIBLIOGRAPHY)|"
    r"ANNEXES?|APPENDICES|"
    r"ABR[ÉE]VIATIONS|ABBREVIATIONS|SOMMAIRE|TABLE\s+(?:DES\s+MATI[ÈE]RES|OF\s+CONTENTS)|"
    r"(?:LISTE\s+DES|LIST\s+OF)\s+(?:TABLEAUX|TABLES|FIGURES|ANNEXES|APPENDICES)|"
    r"(\d+\.){1,3}\s+[A-ZÉÈÊÀÂÎÔÙÇ]"
    r")",
    re.IGNORECASE,
)


@dataclass
class DocBlock:
    block_id: int
    heading: str
    paragraphs: List[str] = field(default_factory=list)
    page_numbers: List[int] = field(default_factory=list)
    elem_indices: List[int] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(self.paragraphs).strip()

    @property
    def preview(self) -> str:
        body = " ".join(self.paragraphs).replace("\n", " ")
        return re.sub(r"\s+", " ", body).strip()[:220]


class ImportedSectionIndexMapping(BaseModel):
    section_id: str
    block_ids: List[int] = Field(
        default_factory=list,
        description="Numéros [B0, B1...] des blocs appartenant à cette section FMSB",
    )
    status: Literal["pending", "in_progress", "needs_revision", "validated"] = "in_progress"
    diagnostic_note: str = Field(
        default="",
        description="Courte note diagnostique (1 phrase) sur la complétude ou les écarts FMSB",
    )


class ImportedProtocolIndexAnalysis(BaseModel):
    detected_title: str = ""
    detected_study_type: str = ""
    global_summary: str = Field(
        description="Résumé global court (2 phrases) de l'état d'avancement du document importé"
    )
    mapped_sections: List[ImportedSectionIndexMapping] = Field(default_factory=list)


def _is_heading_paragraph(text: str, style_name: str) -> bool:
    if len(text) > 160:
        return False
    if "Heading" in style_name or "Titre" in style_name:
        return True
    if HEADING_PATTERN.match(text):
        return True
    if text.isupper() and 4 <= len(text) <= 90:
        return True
    return False


def split_upload_into_blocks(filename: str, content_bytes: bytes) -> List[DocBlock]:
    """Deterministically segment .docx / .pdf / .txt into indexed structural blocks in Python."""
    lower = filename.lower()
    blocks: List[DocBlock] = []

    def push_block(
        heading: str,
        initial: str = "",
        page_num: Optional[int] = None,
        elem_idx: Optional[int] = None,
    ) -> DocBlock:
        blk = DocBlock(block_id=len(blocks), heading=heading[:120])
        if initial:
            blk.paragraphs.append(initial)
        if page_num is not None:
            blk.page_numbers.append(page_num)
        if elem_idx is not None:
            blk.elem_indices.append(elem_idx)
        blocks.append(blk)
        return blk

    if lower.endswith(".docx"):
        doc = Document(io.BytesIO(content_bytes))
        current = push_block("Page de garde / En-tête")
        current_chars = 0

        # Iterate body elements in natural document order (paragraphs + tables)
        para_map = {p._element: p for p in doc.paragraphs}
        tbl_map = {t._element: t for t in doc.tables}

        for elem_idx, child in enumerate(doc.element.body):
            tag = child.tag.split("}")[-1]
            if tag == "p" and child in para_map:
                p = para_map[child]
                txt = p.text.strip()
                if not txt:
                    if elem_idx not in current.elem_indices:
                        current.elem_indices.append(elem_idx)
                    continue
                sname = p.style.name if p.style else ""
                # Skip dense TOC dot-leader lines to keep index clean
                if "......" in txt or "TOC" in sname:
                    continue
                if _is_heading_paragraph(txt, sname) or current_chars > 3500:
                    current = push_block(txt, txt, elem_idx=elem_idx)
                    current_chars = len(txt)
                else:
                    current.paragraphs.append(txt)
                    current.elem_indices.append(elem_idx)
                    current_chars += len(txt)
            elif tag == "tbl" and child in tbl_map:
                tbl = tbl_map[child]
                rows_txt = []
                for row in tbl.rows[:25]:
                    cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                    line = " | ".join(c for c in cells if c)
                    if line:
                        rows_txt.append(line)
                if rows_txt:
                    tbl_block = "\n".join(rows_txt)
                    current.paragraphs.append(tbl_block)
                    current_chars += len(tbl_block)
                current.elem_indices.append(elem_idx)
        return [b for b in blocks if b.paragraphs]

    if lower.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content_bytes))
        current = push_block("Début du document PDF", page_num=1)
        for page_idx, page in enumerate(reader.pages, start=1):
            txt = (page.extract_text() or "").strip()
            if not txt:
                if page_idx not in current.page_numbers:
                    current.page_numbers.append(page_idx)
                continue
            lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
            non_page_lines = [ln for ln in lines[:5] if not ln.isdigit()]
            heading_candidate = non_page_lines[0] if non_page_lines else (lines[0] if lines else f"Page {page_idx}")
            matched_page_head = next(
                (
                    ln
                    for ln in non_page_lines[:4]
                    if _is_heading_paragraph(ln, "")
                    or re.match(
                        r"^(?:CHAPITRE|CHAPTER|ANNEXE|APPENDIX|NOTICE\s+D['’]INFORMATION|FICHE\s+D['’]INFORMATION|"
                        r"FORMULAIRE\s+DE\s+CONSENTEMENT|DEMANDE\s+(?:D['’]|DE\s+))",
                        ln,
                        re.IGNORECASE,
                    )
                ),
                None,
            )
            if len(current.full_text) > 2200 or matched_page_head is not None:
                lbl = matched_page_head or heading_candidate
                current = push_block(f"Page {page_idx}: {lbl}"[:120], txt, page_num=page_idx)
            else:
                current.paragraphs.append(txt)
                if page_idx not in current.page_numbers:
                    current.page_numbers.append(page_idx)
        return [b for b in blocks if b.paragraphs]

    if lower.endswith((".txt", ".md")):
        raw = content_bytes.decode("utf-8", errors="replace")
        chunks = [c.strip() for c in raw.split("\n\n") if c.strip()]
        for idx, ch in enumerate(chunks):
            push_block(ch.splitlines()[0][:80] or f"Bloc {idx}", ch)
        return blocks

    raise ValueError("Format non supporté. Utilisez un fichier .docx, .pdf ou .txt/.md.")


_REF_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:(?:CHAPITRE|CHAPTER)\s+[IVX0-9]+\s*[:.\-–]?\s*)?"
    r"(?:(?:[IVX]+|\d+)\s*[.\-–:]\s*)?"
    r"(?:R[ÉE]F[ÉE]RENCES(?:\s+BIBLIOGRAPHIQUES)?|BIBLIOGRAPHIE|BIBLIOGRAPHY)"
    r"(?:\s*\([^)]*\))?\s*[:.\-–]?\s*$",
    re.IGNORECASE,
)


def _parse_imported_references(raw_ref_text: str, start_order: int = 1) -> List[ReferenceItem]:
    """Parse numbered or line-separated references from an imported protocol into structured ReferenceItem objects."""
    refs: List[ReferenceItem] = []
    if not raw_ref_text or not raw_ref_text.strip():
        return refs

    # Split on newlines; also split inline numbered entries if a single block merged multiple refs
    raw_lines = [ln.strip() for ln in raw_ref_text.splitlines() if ln.strip()]
    expanded_lines: List[str] = []
    for ln in raw_lines:
        if _REF_HEADING_RE.match(ln):
            continue
        # Strip leading markdown heading markers
        ln_clean = re.sub(r"^#{1,6}\s+", "", ln).strip()
        if _REF_HEADING_RE.match(ln_clean):
            continue
        expanded_lines.append(ln_clean)

    next_order = max(1, start_order)
    for line in expanded_lines:
        cleaned = re.sub(r"^\s*(?:[-*•]\s*)?(?:\[\d+\]|\d+[.)])\s*", "", line).strip()
        if len(cleaned) < 18:
            continue
        # Skip obvious non-reference separator or page-number lines
        if re.match(r"^(?:page\s+\d+|[-_*=]{3,})$", cleaned, re.IGNORECASE):
            continue
        try:
            parsed = parse_raw_vancouver_line(cleaned, order_index=next_order)
            parsed.source_platform = "imported"
            if not parsed.vancouver_citation:
                parsed.vancouver_citation = format_vancouver_citation(parsed) or cleaned
            refs.append(parsed)
            next_order += 1
        except Exception:
            year_match = re.search(r"\b(19|20)\d{2}\b", cleaned)
            year = int(year_match.group(0)) if year_match else None
            doi_match = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", cleaned)
            doi = doi_match.group(1).rstrip(".") if doi_match else None
            pmid_match = re.search(r"PMID\s*:?\s*(\d+)", cleaned, re.IGNORECASE)
            pmid = pmid_match.group(1) if pmid_match else None
            refs.append(
                ReferenceItem(
                    order_index=next_order,
                    source_platform="imported",
                    authors=[],
                    title=cleaned[:250],
                    year=year,
                    doi=doi,
                    pmid=pmid,
                    vancouver_citation=cleaned,
                    verified=bool(doi or pmid),
                )
            )
            next_order += 1
    return refs


def _extract_reference_text_from_all_blocks(blocks: List[DocBlock]) -> str:
    """Deterministically locate reference/bibliography blocks anywhere in the imported document."""
    in_refs = False
    collected: List[str] = []
    for blk in blocks:
        hd = (blk.heading or "").strip()
        first_line = next((ln.strip() for ln in (blk.full_text or "").splitlines() if ln.strip()), "")
        is_ref_start = bool(_REF_HEADING_RE.match(hd) or _REF_HEADING_RE.match(first_line))
        is_annex_start = bool(
            re.match(
                r"^(?:#{1,6}\s*)?(?:(?:CHAPITRE|CHAPTER)\s+[IVX0-9]+\s*[:.\-–]?\s*)?(?:ANNEXES?|APPENDICES)\b",
                hd or first_line,
                re.IGNORECASE,
            )
        )
        if is_ref_start:
            in_refs = True
            if blk.full_text:
                collected.append(blk.full_text)
            continue
        if in_refs:
            if is_annex_start:
                break
            if blk.full_text:
                collected.append(blk.full_text)
    return "\n".join(collected).strip()


def _merge_imported_references_into_project(
    project: ProtocolProject,
    raw_ref_text: str,
) -> int:
    """Parse raw references text, merge deduplicated ReferenceItems into project.references, and sync section."""
    start_idx = max((r.order_index for r in (project.references or [])), default=0) + 1
    parsed_refs = _parse_imported_references(raw_ref_text, start_order=start_idx)
    if not parsed_refs:
        return 0

    existing_keys = {
        re.sub(r"[^a-z0-9]+", "", (r.vancouver_citation or r.title or "").lower())[:90]
        for r in (project.references or [])
    }
    added = 0
    for pr in parsed_refs:
        k = re.sub(r"[^a-z0-9]+", "", (pr.vancouver_citation or pr.title or "").lower())[:90]
        if k and k not in existing_keys:
            pr.order_index = len(project.references) + 1
            project.references.append(pr)
            existing_keys.add(k)
            added += 1

    # Sync references section while preserving imported items if body doesn't yet use [n] brackets
    refresh_vancouver_references(project)
    ref_sec = project.sections.get("references")
    if ref_sec is not None and added > 0:
        # If refresh_vancouver_references left fewer lines than imported (e.g. superscript/author-year citations),
        # render all project.references so the student sees their full imported bibliography immediately.
        cited_lines = [ln for ln in (ref_sec.content_markdown or "").splitlines() if re.match(r"^\d+\.\s+", ln.strip())]
        if len(cited_lines) < added:
            lines_md = ["### RÉFÉRENCES BIBLIOGRAPHIQUES (VANCOUVER)", ""]
            for idx_r, r in enumerate(project.references, start=1):
                r.order_index = idx_r
                cit = format_vancouver_citation(r, lang=getattr(project, "language", "fr") or "fr") or r.vancouver_citation or r.title
                lines_md.append(f"{idx_r}. {cit}")
            full_md = "\n".join(lines_md).strip()
            ref_sec.content_markdown = full_md
            ref_sec.content_html = markdown_to_clean_html(full_md)
    return added


def _fallback_map_blocks_to_sections(blocks: List[DocBlock]) -> ImportedProtocolIndexAnalysis:
    """Deterministic keyword mapping when AI segmentation is temporarily unavailable."""
    section_rules = [
        (
            "annexe_fiche_collecte",
            (
                "fiche de collecte",
                "fiche d'enquête",
                "fiche d'enquete",
                "formulaire de collecte",
                "formulaire d'enregistrement",
                "cahier d'observation",
                "grille de collecte",
            ),
        ),
        ("annexe_notice_information", ("notice d'information", "fiche d'information", "information sheet")),
        ("annexe_consentement", ("consentement éclairé", "formulaire de consentement", "consent form")),
        ("annexe_clairance_ethique", ("clairance éthique", "ethical clearance")),
        ("annexe_autorisation_recherche", ("autorisation de recherche", "demande d'autorisation")),
        ("annexe_confidentialite", ("déclaration de confidentialité", "confidentiality declaration")),
        ("annexe_engagement", ("lettre d'engagement", "commitment letter")),
        ("abbreviations", ("abréviation", "abreviation", "sigle", "acronyme")),
        ("references", ("référence", "reference", "bibliograph")),
        ("annexes", ("annexe", "appendix")),
        ("chap3_budget", ("budget", "financement", "coût", "cout")),
        ("chap3_chronogramme", ("chronogramme", "calendrier", "gantt")),
        ("chap3_formalites_admin", ("formalités administratives", "formalites administratives")),
        ("chap3_limites_biais", ("limites et biais", "biais de", "limites de l'étude")),
        ("chap3_ethique", ("éthique", "ethique", "confidentialit")),
        ("chap3_analyse_donnees", ("analyse des données", "analyse statistique", "plan d'analyse", "logiciel")),
        ("chap3_outils_procedure", ("outil", "matériel de collecte", "procédure de collecte", "procedure")),
        ("chap3_population_echantillon", ("échantillon", "echantillon", "taille minimale", "inclusion", "exclusion")),
        ("chap3_type_site_duree", ("type d'étude", "lieu d'étude", "cadre de l'étude", "période et durée")),
        ("chap2_etat_connaissances", ("état des connaissances", "état des publications", "revue de la littérature")),
        ("chap2_rappel_connaissances", ("rappel", "généralité", "generalite", "physiopatholog")),
        ("chap1_variables", ("variables de l'étude", "liste des variables", "variable")),
        ("chap1_definitions_operationnelles", ("définition des termes", "termes opérationnels")),
        ("chap1_objectifs", ("objectif général", "objectifs spécifiques", "objectifs")),
        ("chap1_hypotheses", ("hypothèse", "hypothese")),
        ("chap1_questions", ("question de recherche", "question de la recherche")),
        ("chap1_justification", ("justification", "rationnel")),
        ("intro_interet", ("problématique", "problematique", "intérêt de l'étude")),
        ("intro_contexte", ("introduction", "situation du problème", "contexte")),
    ]
    buckets: Dict[str, List[int]] = {}
    for blk in blocks:
        probe = f"{blk.heading} {blk.preview}".lower()
        if any(k in probe for k in ("république", "université", "faculté", "thème", "protocole de")) and blk.block_id <= 2:
            buckets.setdefault("cover_page", []).append(blk.block_id)
            continue
        matched_sid = "chap2_rappel_connaissances"
        for sid, keywords in section_rules:
            if any(kw in probe for kw in keywords):
                matched_sid = sid
                break
        buckets.setdefault(matched_sid, []).append(blk.block_id)

    mapped = [
        ImportedSectionIndexMapping(
            section_id=sid,
            block_ids=bids,
            status="in_progress",
            diagnostic_note="Section mappée automatiquement par analyse structurelle.",
        )
        for sid, bids in buckets.items()
    ]
    return ImportedProtocolIndexAnalysis(
        detected_title="",
        detected_study_type="",
        global_summary="Document analysé et classé automatiquement selon la structure CAMES/FMSB (mode secours déterministe).",
        mapped_sections=mapped,
    )


class SourceCitabilityMetadata(BaseModel):
    citable: bool = Field(
        default=False,
        description="True uniquement si l'en-tête/page de garde identifie une source citable (thèse, mémoire, article publié, rapport OMS/MINSANTE).",
    )
    source_kind: Literal["thesis", "article", "institutional_report", "unreferenced_draft"] = "unreferenced_draft"
    authors: List[str] = Field(default_factory=list, description="Liste des auteurs principaux format Vancouver si trouvés")
    title: str = Field(default="", description="Titre exact du document ou de l'article/thèse")
    year: Optional[int] = Field(default=None, description="Année de publication ou soutenance")
    institution_or_journal: str = Field(default="", description="Faculté/Université, revue scientifique ou institution (OMS, MINSANTE...)")
    city: str = Field(default="", description="Ville d'édition ou de soutenance (ex: Yaoundé)")
    doi: str = Field(default="", description="DOI ou identifiant si présent")
    vancouver_citation: str = Field(
        default="",
        description="Citation formatée strictement au style Vancouver si citable=True, sinon vide.",
    )


def _extract_markdown_tables(md: str) -> List[str]:
    """Extract Markdown pipe tables from a block of Markdown text."""
    tables: List[str] = []
    curr: List[str] = []
    for line in (md or "").splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 2:
            curr.append(line)
        else:
            if len(curr) >= 3 and any("---" in l or ":---" in l for l in curr[:3]):
                tables.append("\n".join(curr).strip())
            curr = []
    if len(curr) >= 3 and any("---" in l or ":---" in l for l in curr[:3]):
        tables.append("\n".join(curr).strip())
    return tables


def _fallback_detect_citability(filename: str, front_text: str) -> SourceCitabilityMetadata:
    """Deterministic fallback to detect whether an uploaded source has citable cover/header metadata."""
    low = (front_text or "").lower()
    year_m = re.search(r"\b(19\d{2}|20[0-3]\d)\b", front_text or "")
    year_val = int(year_m.group(1)) if year_m else None
    doi_m = re.search(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", front_text or "")
    doi_val = doi_m.group(1) if doi_m else ""

    lines = [l.strip(" #*") for l in (front_text or "").splitlines() if len(l.strip()) >= 8]
    guess_title = lines[0][:200] if lines else filename

    if any(k in low for k in ("thèse", "these", "mémoire", "memoire", "soutenu", "directeur de thèse", "faculté de médecine")):
        yr_part = f" {year_val}." if year_val else "."
        cit = f"Auteur non précisé. {guess_title} [Thèse/Mémoire]. Faculté / Université ;{yr_part}"
        return SourceCitabilityMetadata(
            citable=True,
            source_kind="thesis",
            title=guess_title,
            year=year_val,
            institution_or_journal="Thèse / Mémoire universitaire",
            vancouver_citation=cit,
        )
    if doi_val or any(k in low for k in ("pmid", "issn", "vol.", "journal", "revue ", "original article", "article original")):
        yr_part = f" {year_val}." if year_val else "."
        cit = f"Auteur non précisé. {guess_title}. Revue scientifique.{yr_part}" + (f" doi:{doi_val}" if doi_val else "")
        return SourceCitabilityMetadata(
            citable=True,
            source_kind="article",
            title=guess_title,
            year=year_val,
            doi=doi_val,
            institution_or_journal="Article scientifique",
            vancouver_citation=cit.strip(),
        )
    if any(k in low for k in ("organisation mondiale de la santé", "world health organization", "who ", "oms ", "minsante", "ministère de la santé", "unicef", "cdc")):
        yr_part = f" {year_val}." if year_val else "."
        cit = f"Institution sanitaire. {guess_title}. Rapport technique ;{yr_part}"
        return SourceCitabilityMetadata(
            citable=True,
            source_kind="institutional_report",
            title=guess_title,
            year=year_val,
            institution_or_journal="Rapport institutionnel",
            vancouver_citation=cit,
        )
    return SourceCitabilityMetadata(citable=False, source_kind="unreferenced_draft", title=guess_title)


def detect_document_citability(filename: str, blocks: List[DocBlock], lang: str = "fr") -> SourceCitabilityMetadata:
    """Inspect document header / cover page to decide if it is citable (Vancouver) or an unreferenced draft."""
    front_chunks = [b.full_text for b in blocks[:4] if b.full_text]
    front_text = "\n\n".join(front_chunks)[:3500]
    if not front_text.strip():
        return SourceCitabilityMetadata(citable=False, source_kind="unreferenced_draft", title=filename)

    prompt = f"""
Analyse uniquement l'en-tête / la page de garde / les premières lignes de ce document importé (`{filename}`) :

{front_text}

Détermine :
1. S'il s'agit d'une source académique ou officielle **citable** selon les normes Vancouver (ICMJE/CAMES) :
   - `thesis` (Thèse de médecine, Mémoire DES/Master, Université/Faculté, année/auteur visibles)
   - `article` (Article de revue scientifique, titre, auteurs, revue/DOI/PMID/année)
   - `institutional_report` (Rapport officiel OMS/WHO, MINSANTE, UNICEF, CDC, société savante)
2. S'il s'agit d'un simple brouillon personnel ou notes sans référence bibliographique exploitable → `citable = false`, `source_kind = "unreferenced_draft"`, `vancouver_citation = ""`.
3. Si `citable = true`, génère une citation Vancouver propre et fidèle aux éléments visibles (ex: "Dupont J. Titre [Thèse de Doctorat en Médecine]. Yaoundé : Université de Yaoundé I, FMSB ; 2024.") sans inventer d'auteurs ou DOI absents.
Langue cible du résumé/mention : {'anglais' if lang == 'en' else 'français'}.
"""
    try:
        res = gemini_router.generate_structured(
            role="audit",
            contents=prompt,
            schema=SourceCitabilityMetadata,
            temperature=0.0,
        )
        meta: SourceCitabilityMetadata = res["data"]
        if meta.citable and not meta.vancouver_citation.strip():
            title_s = meta.title.strip() or filename
            auth_s = ", ".join(meta.authors[:3]) or "Collectif"
            inst_s = meta.institution_or_journal.strip() or "Document académique"
            yr_s = f" {meta.year}." if meta.year else "."
            meta.vancouver_citation = f"{auth_s}. {title_s}. {inst_s};{yr_s}"
        if not meta.citable:
            meta.source_kind = "unreferenced_draft"
            meta.vancouver_citation = ""
        return meta
    except Exception:
        return _fallback_detect_citability(filename, front_text)


def remove_internal_source_from_project(project: ProtocolProject, source_id: str) -> bool:
    """Remove an internal source document and all its mapped section entries."""
    before_len = len(project.internal_sources)
    project.internal_sources = [s for s in project.internal_sources if s.source_id != source_id]
    removed_entries = 0
    for sec in project.sections.values():
        kept = [e for e in (getattr(sec, "internal_library", []) or []) if e.source_id != source_id]
        removed_entries += len(getattr(sec, "internal_library", []) or []) - len(kept)
        sec.internal_library = kept
    return len(project.internal_sources) < before_len or removed_entries > 0


def analyze_and_import_protocol(
    project: ProtocolProject,
    filename: str,
    content_bytes: bytes,
    import_mode: str = "skeleton",
) -> Dict[str, object]:
    """Fast hybrid import: Python splits structural blocks; Gemini maps block IDs only.
    Supports `import_mode`:
      - 'skeleton': populates/updates protocol sections directly (existing behavior).
      - 'internal_source': checks header/cover citability and stores extracted blocks,
        tables & figures in each section's secondary working memory (`internal_library`)
        WITHOUT overwriting any drafted protocol content.
    """
    resolved_mode = "internal_source" if (import_mode or "").strip().lower() == "internal_source" else "skeleton"
    lang = normalize_lang(getattr(project, "language", "fr"))
    emit_pipeline_step(
        "parse",
        "Lecture et découpage structurel du document...",
        "Parsing document structure and blocks...",
        detail=filename,
        progress_pct=20,
    )
    blocks = split_upload_into_blocks(filename, content_bytes)
    if not blocks:
        raise ValueError(
            "The uploaded document appears empty or unreadable (scanned PDF without text)."
            if lang == "en"
            else "Le document importé semble vide ou illisible (PDF scanné sans texte)."
        )

    total_chars = sum(len(b.full_text) for b in blocks)
    blocks_by_id = {b.block_id: b for b in blocks}

    # Build a compact catalog of blocks without truncating the tail (where annexes & CRF live!)
    if len(blocks) <= 140:
        catalog_blocks = blocks
    else:
        seen_bids = set()
        catalog_blocks = []
        for blk in list(blocks[:95]) + list(blocks[-45:]):
            if blk.block_id not in seen_bids:
                seen_bids.add(blk.block_id)
                catalog_blocks.append(blk)

    catalog_lines = [
        f"[B{b.block_id}] Titre: {b.heading} | Aperçu: {b.preview}"
        for b in catalog_blocks
    ]
    sections_list = "\n".join(
        f"- {s.id}: {s.chapter_title} — {s.title}"
        for s in get_localized_canvas(lang)
    )

    emit_pipeline_step(
        "map_sections",
        "Classification des blocs vers le canevas FMSB...",
        "Mapping extracted blocks onto protocol sections...",
        detail=f"{len(blocks)} blocs",
        progress_pct=52,
    )
    diag_lang_note = (
        "Write `global_summary` and each `diagnostic_note` in English."
        if lang == "en"
        else "Rédige `global_summary` et chaque `diagnostic_note` en français."
    )
    prompt = f"""
Tu es un expert méthodologiste FMSB. Un étudiant importe un document ({filename}, {len(blocks)} blocs structurels).
Voici les sections du canevas FMSB :
{sections_list}

Voici l'index complet des blocs extraits du document :
{chr(10).join(catalog_lines)}

Tâches (NE RECOPIE PAS le texte des blocs, renvoie UNIQUEMENT les numéros de blocs) :
1. Identifie le titre et le type d'étude si visibles dans les premiers blocs.
2. Pour chaque section FMSB trouvée, liste les `block_ids` (entiers, ex: [4, 5]) correspondants.
3. Évalue son statut (`in_progress` ou `needs_revision`) et écris une `diagnostic_note` courte (1 phrase).
4. {diag_lang_note}
"""
    try:
        res = gemini_router.generate_structured(
            role="segmentation",
            contents=prompt,
            schema=ImportedProtocolIndexAnalysis,
            temperature=0.1,
        )
        analysis: ImportedProtocolIndexAnalysis = res["data"]
        model_used = str(res["model_used"])
    except Exception:
        analysis = _fallback_map_blocks_to_sections(blocks)
        model_used = "deterministic-importer-fallback"

    if resolved_mode == "internal_source":
        emit_pipeline_step(
            "citability",
            "Analyse de l'en-tête / page de garde (source citable vs brouillon)...",
            "Inspecting cover/header for Vancouver citability...",
            progress_pct=68,
        )
        meta = detect_document_citability(filename, blocks, lang=lang)
        source_ref_id: Optional[str] = None
        if meta.citable and meta.vancouver_citation:
            existing_by_cit = {
                r.vancouver_citation.strip().lower(): r
                for r in project.references
                if r.vancouver_citation.strip()
            }
            key = meta.vancouver_citation.strip().lower()
            if key in existing_by_cit:
                source_ref_id = existing_by_cit[key].ref_id
            else:
                new_ref = ReferenceItem(
                    source_platform="internal_library",
                    authors=meta.authors,
                    title=meta.title or filename,
                    journal=meta.institution_or_journal,
                    year=meta.year,
                    doi=meta.doi or None,
                    vancouver_citation=meta.vancouver_citation,
                    verified=True,
                )
                project.references.append(new_ref)
                refresh_vancouver_references(project)
                source_ref_id = new_ref.ref_id

        source_doc = InternalSourceDocument(
            filename=filename,
            source_kind=meta.source_kind,
            citable=meta.citable,
            ref_id=source_ref_id,
            vancouver_citation=meta.vancouver_citation,
            authors=meta.authors,
            title=meta.title or filename,
            year=meta.year,
            institution_or_journal=meta.institution_or_journal,
        )

        page_to_section: Dict[int, str] = {}
        elem_idx_to_section: Dict[int, str] = {}
        prepared_sections: List[Tuple[str, str, List[str], str]] = []
        for item in analysis.mapped_sections:
            if item.section_id not in project.sections or item.section_id == "cover_page":
                continue
            valid_blocks: List[str] = []
            for bid in item.block_ids:
                blk = blocks_by_id.get(bid)
                if blk is None:
                    continue
                for pno in blk.page_numbers:
                    if pno not in page_to_section or page_to_section[pno] == "cover_page":
                        page_to_section[pno] = item.section_id
                for eidx in blk.elem_indices:
                    if eidx not in elem_idx_to_section or elem_idx_to_section[eidx] == "cover_page":
                        elem_idx_to_section[eidx] = item.section_id
                if blk.full_text:
                    valid_blocks.append(blk.full_text)
            if not valid_blocks:
                continue
            reassembled_text = "\n\n".join(valid_blocks)
            tables_md = _extract_markdown_tables(reassembled_text)
            prepared_sections.append((item.section_id, reassembled_text, tables_md, item.diagnostic_note or ""))

        # Dedicated deterministic CRF + Sub-Annexes boundary pass for internal_source documents
        crf_det = detect_crf_boundaries_in_blocks(blocks, lang=lang, use_ai=False)
        crf_span_ids_int: set[int] = set()
        if crf_det.found and crf_det.start_block_id is not None and crf_det.end_block_id is not None:
            id_to_pos_int = {b.block_id: idx for idx, b in enumerate(blocks)}
            sp_i = id_to_pos_int.get(crf_det.start_block_id)
            ep_i = id_to_pos_int.get(crf_det.end_block_id)
            if sp_i is not None and ep_i is not None and sp_i <= ep_i:
                crf_span_ids_int = {blocks[k].block_id for k in range(sp_i, ep_i + 1)}

        existing_prepared_sids = {sid for sid, _, _, _ in prepared_sections}
        if crf_det.found and "annexe_fiche_collecte" not in existing_prepared_sids:
            _, pure_crf, _, crf_det = slice_crf_from_text_or_blocks(
                blocks=blocks,
                lang=lang,
                use_ai=False,
                precomputed_detection=crf_det,
            )
            if pure_crf.strip():
                if "annexe_fiche_collecte" not in project.sections:
                    project.sections["annexe_fiche_collecte"] = SectionState(section_id="annexe_fiche_collecte")
                prepared_sections.append(
                    (
                        "annexe_fiche_collecte",
                        pure_crf,
                        _extract_markdown_tables(pure_crf),
                        "Fiche de collecte identifiée automatiquement (début → fin) pour la bibliothèque interne.",
                    )
                )
                existing_prepared_sids.add("annexe_fiche_collecte")

        sub_ann_int = extract_all_sub_annexes_from_blocks(
            blocks,
            excluded_block_ids=crf_span_ids_int,
            lang=lang,
        )
        detected_subs_int: List[str] = []
        for sub_sid, sub_data in sub_ann_int["extracted"].items():
            detected_subs_int.append(sub_sid)
            if sub_sid not in existing_prepared_sids:
                if sub_sid not in project.sections:
                    project.sections[sub_sid] = SectionState(section_id=sub_sid)
                sub_md = sub_data["content_markdown"]
                prepared_sections.append(
                    (
                        sub_sid,
                        sub_md,
                        _extract_markdown_tables(sub_md),
                        f"Sous-annexe « {sub_data['title']} » identifiée pour la bibliothèque interne.",
                    )
                )
                existing_prepared_sids.add(sub_sid)

        emit_pipeline_step(
            "extract_images",
            "Extraction des figures vers la bibliothèque interne...",
            "Extracting figures into internal section library...",
            progress_pct=82,
        )
        extracted_imgs = extract_images_from_uploaded_document(
            project,
            filename,
            content_bytes,
            section_context={
                "page_to_section": page_to_section,
                "elem_idx_to_section": elem_idx_to_section,
            },
        )
        imgs_by_sec: Dict[str, List[str]] = {}
        for img in extracted_imgs:
            imgs_by_sec.setdefault(img.target_section_id, []).append(img.asset_id)

        entries_added = 0
        for sid, text_md, tables_md, diag in prepared_sections:
            sec = project.sections[sid]
            entry = InternalLibraryEntry(
                source_id=source_doc.source_id,
                source_filename=filename,
                source_kind=meta.source_kind,
                citable=meta.citable,
                ref_id=source_ref_id,
                vancouver_citation=meta.vancouver_citation,
                content_markdown=text_md,
                tables_markdown=tables_md,
                image_asset_ids=imgs_by_sec.get(sid, []),
                diagnostic_note=diag,
            )
            sec.internal_library.append(entry)
            source_doc.mapped_sections.append(sid)
            entries_added += 1

        source_doc.extracted_images_count = len(extracted_imgs)
        project.internal_sources.append(source_doc)

        return {
            "model_used": model_used,
            "filename": filename,
            "import_mode": "internal_source",
            "extracted_chars": total_chars,
            "total_blocks": len(blocks),
            "extracted_references_count": len(project.references),
            "extracted_images_count": len(extracted_imgs),
            "extracted_images": [
                {
                    "asset_id": img.asset_id,
                    "title": img.title,
                    "target_section_id": img.target_section_id,
                    "source_url": img.source_url,
                    "caption": img.caption,
                    "anchor_hint": img.anchor_hint,
                }
                for img in extracted_imgs
            ],
            "global_summary": analysis.global_summary,
            "mapped_sections_count": entries_added,
            "internal_entries_added": entries_added,
            "citable": meta.citable,
            "vancouver_citation": meta.vancouver_citation,
            "source_document": source_doc.model_dump(mode="json"),
            "crf_detected": bool(crf_det.found or "annexe_fiche_collecte" in existing_prepared_sids or any(s == "annexe_fiche_collecte" for s, _, _, _ in prepared_sections)),
            "crf_detection": crf_det.model_dump(mode="json") if crf_det.found else None,
            "detected_sub_annexes": detected_subs_int,
        }

    project.entry_mode = EntryMode.EXISTING_PROTOCOL
    if not project.topic_locked and analysis.detected_title and (not project.cover.title or project.cover.title == project.name):
        project.cover.title = analysis.detected_title
    if analysis.detected_study_type and not project.study_type:
        project.study_type = analysis.detected_study_type

    # Dedicated Stage 1 CRF & Sub-Annexes boundary detection across ALL blocks before mapping annexes
    emit_pipeline_step(
        "crf_detect",
        "Identification chirurgicale des sous-annexes & de la fiche de collecte...",
        "Detecting exact Data Collection Form & sub-appendix boundaries...",
        progress_pct=70,
    )
    crf_det = detect_crf_boundaries_in_blocks(blocks, lang=lang, use_ai=True)
    crf_span_ids: set[int] = set()
    pure_crf_text = ""
    if crf_det.found and crf_det.start_block_id is not None and crf_det.end_block_id is not None:
        id_to_pos = {b.block_id: idx for idx, b in enumerate(blocks)}
        s_pos = id_to_pos.get(crf_det.start_block_id)
        e_pos = id_to_pos.get(crf_det.end_block_id)
        if s_pos is not None and e_pos is not None and s_pos <= e_pos:
            crf_span_ids = {blocks[k].block_id for k in range(s_pos, e_pos + 1)}
            _, pure_crf_text, _, crf_det = slice_crf_from_text_or_blocks(
                blocks=blocks,
                lang=lang,
                use_ai=False,
                precomputed_detection=crf_det,
            )

    sub_ann_res = extract_all_sub_annexes_from_blocks(
        blocks,
        excluded_block_ids=crf_span_ids,
        lang=lang,
    )
    sub_ann_claimed_ids: set[int] = set(sub_ann_res.get("claimed_block_ids") or set())
    all_claimed_annex_ids = crf_span_ids | sub_ann_claimed_ids

    mapped_count = 0
    mapped_section_ids: set[str] = set()
    page_to_section = {}
    elem_idx_to_section = {}

    for item in analysis.mapped_sections:
        if item.section_id not in project.sections:
            continue
        effective_bids = [
            bid
            for bid in item.block_ids
            if not (item.section_id == "annexes" and bid in all_claimed_annex_ids)
        ]
        valid_blocks = []
        for bid in effective_bids:
            blk = blocks_by_id.get(bid)
            if blk is None:
                continue
            for pno in blk.page_numbers:
                if pno not in page_to_section or page_to_section[pno] == "cover_page":
                    page_to_section[pno] = item.section_id
            for eidx in blk.elem_indices:
                if eidx not in elem_idx_to_section or elem_idx_to_section[eidx] == "cover_page":
                    elem_idx_to_section[eidx] = item.section_id
            if blk.full_text:
                valid_blocks.append(blk.full_text)
        if not valid_blocks:
            continue
        if item.section_id == "cover_page":
            continue
        reassembled_text = "\n\n".join(valid_blocks)
        sec = project.sections[item.section_id]
        sec.content_markdown = reassembled_text
        sec.content_html = markdown_to_clean_html(reassembled_text)
        sec.status = StepStatus(item.status)
        det_notes = deterministic_section_checks(project, item.section_id, reassembled_text)
        sec.audit_notes = det_notes + ([item.diagnostic_note] if item.diagnostic_note else [])
        mapped_count += 1
        mapped_section_ids.add(item.section_id)

        if item.section_id == "annexes" and not pure_crf_text:
            before_crf, crf_block, after_crf, crf_title = extract_crf_annexe_block(reassembled_text)
            if crf_block and is_crf_annexe_content(crf_title, crf_block):
                if "annexe_fiche_collecte" not in project.sections:
                    project.sections["annexe_fiche_collecte"] = SectionState(section_id="annexe_fiche_collecte")
                crf_sec = project.sections["annexe_fiche_collecte"]
                crf_sec.content_markdown = crf_block
                crf_sec.content_html = markdown_to_clean_html(crf_block)
                crf_sec.status = StepStatus(item.status)
                mapped_section_ids.add("annexe_fiche_collecte")
                # Keep non-CRF appendices in `annexes` when separating into dedicated CRF sub-section
                other_parts = [p for p in (before_crf, after_crf) if p and p.strip()]
                if other_parts:
                    cleaned_other = "\n\n---\n\n".join(other_parts)
                    sec.content_markdown = cleaned_other
                    sec.content_html = markdown_to_clean_html(cleaned_other)
                project.crf_fields = extract_crf_fields_from_project(project)

        if item.section_id == "references":
            _merge_imported_references_into_project(project, reassembled_text)

    # Deterministic fallback: if AI omitted or missed the References/Bibliographie block, extract it directly
    if "references" not in mapped_section_ids:
        fallback_ref_text = _extract_reference_text_from_all_blocks(blocks)
        if fallback_ref_text:
            added_fb = _merge_imported_references_into_project(project, fallback_ref_text)
            if added_fb > 0:
                mapped_section_ids.add("references")
                mapped_count += 1

    # Apply the surgical CRF extractor result into `annexe_fiche_collecte` + Masque de saisie
    crf_detected = False
    if pure_crf_text.strip():
        crf_detected = True
        for bid in crf_span_ids:
            blk = blocks_by_id.get(bid)
            if blk is None:
                continue
            for pno in blk.page_numbers:
                page_to_section[pno] = "annexe_fiche_collecte"
            for eidx in blk.elem_indices:
                elem_idx_to_section[eidx] = "annexe_fiche_collecte"

        if "annexe_fiche_collecte" not in project.sections:
            project.sections["annexe_fiche_collecte"] = SectionState(section_id="annexe_fiche_collecte")
        crf_sec = project.sections["annexe_fiche_collecte"]
        crf_sec.content_markdown = pure_crf_text
        crf_sec.content_html = markdown_to_clean_html(pure_crf_text)
        crf_sec.status = StepStatus.IN_PROGRESS
        if "annexe_fiche_collecte" not in mapped_section_ids:
            mapped_count += 1
            mapped_section_ids.add("annexe_fiche_collecte")

        compiled = compile_crf_fields_hybrid(project, pure_crf_text, use_ai=True)
        compiled_fields = compiled.get("fields") or []
        if compiled_fields:
            reconciled, _ = reconcile_crf_fields(project.crf_fields, compiled_fields)
            project.crf_fields = reconciled
        else:
            project.crf_fields = extract_crf_fields_from_project(project)
        sync_crf_to_project(project, sync_annexe1_markdown=False)
    else:
        crf_sec_existing = project.sections.get("annexe_fiche_collecte")
        if crf_sec_existing and (crf_sec_existing.content_markdown or "").strip():
            crf_detected = True
            compiled = compile_crf_fields_hybrid(project, crf_sec_existing.content_markdown, use_ai=True)
            if compiled.get("fields"):
                reconciled, _ = reconcile_crf_fields(project.crf_fields, compiled["fields"])
                project.crf_fields = reconciled
                sync_crf_to_project(project, sync_annexe1_markdown=False)
            elif not project.crf_fields:
                project.crf_fields = extract_crf_fields_from_project(project)

    # Populate dedicated individualized sub-annex sections extracted cleanly from the document
    detected_sub_annexes: List[str] = []
    if crf_detected:
        detected_sub_annexes.append("annexe_fiche_collecte")
    for sub_sid, sub_data in sub_ann_res["extracted"].items():
        sub_md = (sub_data.get("content_markdown") or "").strip()
        if not sub_md:
            continue
        if sub_sid not in project.sections:
            project.sections[sub_sid] = SectionState(section_id=sub_sid)
        sub_sec = project.sections[sub_sid]
        sub_sec.content_markdown = sub_md
        sub_sec.content_html = markdown_to_clean_html(sub_md)
        sub_sec.status = StepStatus.IN_PROGRESS
        sub_sec.audit_notes = [f"Sous-annexe extraite automatiquement ({sub_data.get('title', sub_sid)})."]
        if sub_sid not in mapped_section_ids:
            mapped_count += 1
            mapped_section_ids.add(sub_sid)
        if sub_sid not in detected_sub_annexes:
            detected_sub_annexes.append(sub_sid)
        for sbid in sub_data.get("block_ids") or []:
            blk = blocks_by_id.get(sbid)
            if blk is None:
                continue
            for pno in blk.page_numbers:
                page_to_section[pno] = sub_sid
            for eidx in blk.elem_indices:
                elem_idx_to_section[eidx] = sub_sid

    # Extract ALL valid embedded images/figures from the user-imported document with section context
    emit_pipeline_step(
        "extract_images",
        "Repérage & extraction des figures du document...",
        "Detecting & extracting embedded scientific figures...",
        progress_pct=82,
    )
    extracted_imgs = extract_images_from_uploaded_document(
        project,
        filename,
        content_bytes,
        section_context={
            "page_to_section": page_to_section,
            "elem_idx_to_section": elem_idx_to_section,
        },
    )

    return {
        "model_used": model_used,
        "filename": filename,
        "import_mode": "skeleton",
        "extracted_chars": total_chars,
        "total_blocks": len(blocks),
        "extracted_references_count": len(project.references),
        "extracted_images_count": len(extracted_imgs),
        "extracted_images": [
            {
                "asset_id": img.asset_id,
                "title": img.title,
                "target_section_id": img.target_section_id,
                "source_url": img.source_url,
                "caption": img.caption,
                "anchor_hint": img.anchor_hint,
            }
            for img in extracted_imgs
        ],
        "global_summary": analysis.global_summary,
        "mapped_sections_count": mapped_count,
        "crf_detected": crf_detected,
        "crf_fields_count": len(project.crf_fields),
        "crf_detection": crf_det.model_dump(mode="json") if crf_det.found else None,
        "detected_sub_annexes": detected_sub_annexes,
    }
