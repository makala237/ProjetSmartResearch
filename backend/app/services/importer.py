import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from docx import Document
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import get_localized_canvas, normalize_lang
from app.schemas.protocol import EntryMode, ProtocolProject, ReferenceItem, SectionState, StepStatus
from app.services.crf_builder import (
    extract_crf_annexe_block,
    extract_crf_fields_from_project,
    is_crf_annexe_content,
)
from app.services.drafting import deterministic_section_checks
from app.services.figure_extractor import extract_images_from_uploaded_document
from app.services.gemini_client import gemini_router
from app.services.pipeline_tracker import emit_pipeline_step
from app.services.rich_formatter import markdown_to_clean_html
from app.services.vancouver import refresh_vancouver_references

HEADING_PATTERN = re.compile(
    r"^("
    r"(?:CHAPITRE|CHAPTER)\s+[IVX0-9]+|"
    r"INTRODUCTION(\s+(?:GENERALE|GENERAL))?|"
    r"PROBLEMATIQUE|PROBLEM\s+STATEMENT|"
    r"REVUE\s+DE\s+LA\s+LITTERATURE|LITERATURE\s+REVIEW|"
    r"METHODOLOGIE|METHODOLOGY|MATERIELS?\s+ET\s+METHODES?|MATERIALS?\s+AND\s+METHODS?|"
    r"REFERENCES|ANNEXES?|APPENDICES|"
    r"ABREVIATIONS|ABBREVIATIONS|SOMMAIRE|TABLE\s+(?:DES\s+MATIERES|OF\s+CONTENTS)|"
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
            first_line = lines[0] if lines else f"Page {page_idx}"
            if len(current.full_text) > 2200 or _is_heading_paragraph(first_line, ""):
                current = push_block(f"Page {page_idx}: {first_line}", txt, page_num=page_idx)
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


def _parse_imported_references(raw_ref_text: str) -> List[ReferenceItem]:
    """Parse numbered or line-separated references from an imported protocol into ReferenceItem objects."""
    refs: List[ReferenceItem] = []
    # Split on newlines or numbered prefixes like "1. " / "[1] "
    lines = [ln.strip() for ln in raw_ref_text.splitlines() if ln.strip()]
    for line in lines:
        if line.upper() in ("REFERENCES", "RÉFÉRENCES", "BIBLIOGRAPHIE"):
            continue
        cleaned = re.sub(r"^\s*(?:\[\d+\]|\d+[.)])\s*", "", line).strip()
        if len(cleaned) < 15:
            continue
        year_match = re.search(r"\b(19|20)\d{2}\b", cleaned)
        year = int(year_match.group(0)) if year_match else None
        doi_match = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", cleaned)
        doi = doi_match.group(1).rstrip(".") if doi_match else None
        pmid_match = re.search(r"PMID\s*:?\s*(\d+)", cleaned, re.IGNORECASE)
        pmid = pmid_match.group(1) if pmid_match else None

        refs.append(
            ReferenceItem(
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
    return refs


def _fallback_map_blocks_to_sections(blocks: List[DocBlock]) -> ImportedProtocolIndexAnalysis:
    """Deterministic keyword mapping when AI segmentation is temporarily unavailable."""
    section_rules = [
        ("abbreviations", ("abréviation", "abreviation", "sigle", "acronyme")),
        ("references", ("référence", "reference", "bibliograph")),
        ("annexes", ("annexe", "consentement", "clairance", "questionnaire")),
        ("chap3_budget", ("budget", "financement", "coût", "cout")),
        ("chap3_chronogramme", ("chronogramme", "calendrier", "gantt")),
        ("chap3_ethique", ("éthique", "ethique", "confidentialit", "consentement")),
        ("chap3_analyse", ("analyse statistique", "plan d'analyse", "données", "logiciel")),
        ("chap3_outils_procedure", ("outil", "collecte", "procédure", "procedure")),
        ("chap3_echantillonnage", ("échantillon", "echantillon", "taille", "inclusion", "exclusion")),
        ("chap3_type_lieu_duree", ("type d'étude", "lieu d'étude", "site d'étude", "cadre de l'étude", "durée")),
        ("chap2_etat_connaissances", ("état des connaissances", "etat des connaissances", "épidémiolog", "revue de la littérature")),
        ("chap2_rappel_connaissances", ("rappel", "généralité", "generalite", "définition", "physiopatholog")),
        ("chap1_variables", ("variable", "définition opérationnelle")),
        ("chap1_objectifs_hypotheses", ("objectif", "hypothèse", "hypothese", "question de recherche")),
        ("chap1_justification", ("justification", "rationnel", "intérêt de l'étude")),
        ("intro_problematique", ("problématique", "problematique")),
        ("intro_contexte", ("introduction", "contexte")),
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


def analyze_and_import_protocol(
    project: ProtocolProject,
    filename: str,
    content_bytes: bytes,
) -> Dict[str, object]:
    """Fast hybrid import: Python splits structural blocks; Gemini maps block IDs only."""
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

    # Build a compact catalog of blocks (no full-text regurgitation!)
    # Cap catalog at ~140 blocks if a huge thesis has hundreds of micro-headings
    catalog_lines = [
        f"[B{b.block_id}] Titre: {b.heading} | Aperçu: {b.preview}"
        for b in blocks[:140]
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
Tu es un expert méthodologiste FMSB. Un étudiant importe son protocole ({filename}, {len(blocks)} blocs structurels).
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

    project.entry_mode = EntryMode.EXISTING_PROTOCOL
    if not project.topic_locked and analysis.detected_title and (not project.cover.title or project.cover.title == project.name):
        project.cover.title = analysis.detected_title
    if analysis.detected_study_type and not project.study_type:
        project.study_type = analysis.detected_study_type

    mapped_count = 0
    page_to_section: Dict[int, str] = {}
    elem_idx_to_section: Dict[int, str] = {}

    for item in analysis.mapped_sections:
        if item.section_id not in project.sections:
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

        if item.section_id == "annexes":
            before_crf, crf_block, after_crf, crf_title = extract_crf_annexe_block(reassembled_text)
            if crf_block and is_crf_annexe_content(crf_title, crf_block):
                if "annexe_fiche_collecte" not in project.sections:
                    project.sections["annexe_fiche_collecte"] = SectionState(section_id="annexe_fiche_collecte")
                crf_sec = project.sections["annexe_fiche_collecte"]
                crf_sec.content_markdown = crf_block
                crf_sec.content_html = markdown_to_clean_html(crf_block)
                crf_sec.status = StepStatus(item.status)
                # Keep non-CRF appendices in `annexes` when separating into dedicated CRF sub-section
                other_parts = [p for p in (before_crf, after_crf) if p and p.strip()]
                if other_parts:
                    cleaned_other = "\n\n---\n\n".join(other_parts)
                    sec.content_markdown = cleaned_other
                    sec.content_html = markdown_to_clean_html(cleaned_other)
                project.crf_fields = extract_crf_fields_from_project(project)

        if item.section_id == "references":
            parsed_refs = _parse_imported_references(reassembled_text)
            if parsed_refs:
                existing_titles = {r.vancouver_citation.lower() for r in project.references}
                for pr in parsed_refs:
                    if pr.vancouver_citation.lower() not in existing_titles:
                        project.references.append(pr)
                        existing_titles.add(pr.vancouver_citation.lower())
                refresh_vancouver_references(project)

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
    }
