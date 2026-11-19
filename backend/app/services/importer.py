import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal

from docx import Document
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import EntryMode, ProtocolProject, ReferenceItem, StepStatus
from app.services.drafting import deterministic_section_checks
from app.services.figure_extractor import extract_images_from_uploaded_document
from app.services.gemini_client import gemini_router
from app.services.vancouver import refresh_vancouver_references

HEADING_PATTERN = re.compile(
    r"^("
    r"CHAPITRE\s+[IVX0-9]+|"
    r"INTRODUCTION(\s+GENERALE)?|"
    r"PROBLEMATIQUE|"
    r"REVUE\s+DE\s+LA\s+LITTERATURE|"
    r"METHODOLOGIE|MATERIELS?\s+ET\s+METHODES?|"
    r"REFERENCES|ANNEXES?|"
    r"ABREVIATIONS|SOMMAIRE|TABLE\s+DES\s+MATIERES|"
    r"LISTE\s+DES\s+(TABLEAUX|FIGURES|ANNEXES)|"
    r"(\d+\.){1,3}\s+[A-ZÉÈÊÀÂÎÔÙÇ]"
    r")",
    re.IGNORECASE,
)


@dataclass
class DocBlock:
    block_id: int
    heading: str
    paragraphs: List[str] = field(default_factory=list)

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

    def push_block(heading: str, initial: str = "") -> DocBlock:
        blk = DocBlock(block_id=len(blocks), heading=heading[:120])
        if initial:
            blk.paragraphs.append(initial)
        blocks.append(blk)
        return blk

    if lower.endswith(".docx"):
        doc = Document(io.BytesIO(content_bytes))
        current = push_block("Page de garde / En-tête")
        current_chars = 0

        # Iterate body elements in natural document order (paragraphs + tables)
        w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        para_map = {p._element: p for p in doc.paragraphs}
        tbl_map = {t._element: t for t in doc.tables}

        for child in doc.element.body:
            tag = child.tag.split("}")[-1]
            if tag == "p" and child in para_map:
                p = para_map[child]
                txt = p.text.strip()
                if not txt:
                    continue
                sname = p.style.name if p.style else ""
                # Skip dense TOC dot-leader lines to keep index clean
                if "......" in txt or "TOC" in sname:
                    continue
                if _is_heading_paragraph(txt, sname) or current_chars > 3500:
                    current = push_block(txt, txt)
                    current_chars = len(txt)
                else:
                    current.paragraphs.append(txt)
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
        return [b for b in blocks if b.paragraphs]

    if lower.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content_bytes))
        current = push_block("Début du document PDF")
        for page_idx, page in enumerate(reader.pages, start=1):
            txt = (page.extract_text() or "").strip()
            if not txt:
                continue
            lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
            first_line = lines[0] if lines else f"Page {page_idx}"
            if len(current.full_text) > 2200 or _is_heading_paragraph(first_line, ""):
                current = push_block(f"Page {page_idx}: {first_line}", txt)
            else:
                current.paragraphs.append(txt)
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


def analyze_and_import_protocol(
    project: ProtocolProject,
    filename: str,
    content_bytes: bytes,
) -> Dict[str, object]:
    """Fast hybrid import: Python splits structural blocks; gemini-3.1-flash-lite maps block IDs only."""
    blocks = split_upload_into_blocks(filename, content_bytes)
    if not blocks:
        raise ValueError("Le document importé semble vide ou illisible (PDF scanné sans texte).")

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
        for s in FMSB_SECTIONS
    )

    prompt = f"""
Tu es un expert méthodologiste FMSB. Un étudiant importe son protocole ({filename}, {len(blocks)} blocs structurels).
Voici les 21 sections du canevas FMSB :
{sections_list}

Voici l'index complet des blocs extraits du document :
{chr(10).join(catalog_lines)}

Tâches (NE RECOPIE PAS le texte des blocs, renvoie UNIQUEMENT les numéros de blocs) :
1. Identifie le titre et le type d'étude si visibles dans les premiers blocs.
2. Pour chaque section FMSB trouvée, liste les `block_ids` (entiers, ex: [4, 5]) correspondants.
3. Évalue son statut (`in_progress` ou `needs_revision`) et écris une `diagnostic_note` courte (1 phrase).
"""
    # Use fast segmentation chain (gemini-3.1-flash-lite) because output is tiny JSON indices!
    res = gemini_router.generate_structured(
        role="segmentation",
        contents=prompt,
        schema=ImportedProtocolIndexAnalysis,
        temperature=0.1,
    )
    analysis: ImportedProtocolIndexAnalysis = res["data"]

    project.entry_mode = EntryMode.EXISTING_PROTOCOL
    if not project.topic_locked and analysis.detected_title and (not project.cover.title or project.cover.title == project.name):
        project.cover.title = analysis.detected_title
    if analysis.detected_study_type and not project.study_type:
        project.study_type = analysis.detected_study_type

    mapped_count = 0
    for item in analysis.mapped_sections:
        if item.section_id not in project.sections:
            continue
        valid_blocks = [
            blocks_by_id[bid].full_text
            for bid in item.block_ids
            if bid in blocks_by_id and blocks_by_id[bid].full_text
        ]
        if not valid_blocks:
            continue
        reassembled_text = "\n\n".join(valid_blocks)
        sec = project.sections[item.section_id]
        sec.content_markdown = reassembled_text
        sec.status = StepStatus(item.status)
        det_notes = deterministic_section_checks(project, item.section_id, reassembled_text)
        sec.audit_notes = det_notes + ([item.diagnostic_note] if item.diagnostic_note else [])
        mapped_count += 1

        if item.section_id == "references":
            parsed_refs = _parse_imported_references(reassembled_text)
            if parsed_refs:
                existing_titles = {r.vancouver_citation.lower() for r in project.references}
                for pr in parsed_refs:
                    if pr.vancouver_citation.lower() not in existing_titles:
                        project.references.append(pr)
                        existing_titles.add(pr.vancouver_citation.lower())
                refresh_vancouver_references(project)

    # Extract ALL embedded images/figures from the user-imported document
    extracted_imgs = extract_images_from_uploaded_document(project, filename, content_bytes)

    return {
        "model_used": res["model_used"],
        "filename": filename,
        "extracted_chars": total_chars,
        "total_blocks": len(blocks),
        "extracted_references_count": len(project.references),
        "extracted_images_count": len(extracted_imgs),
        "global_summary": analysis.global_summary,
        "mapped_sections_count": mapped_count,
    }
