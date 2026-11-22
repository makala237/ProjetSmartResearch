from concurrent.futures import ThreadPoolExecutor, as_completed
import contextvars
import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Literal, Optional
import httpx
from pydantic import BaseModel, Field

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import get_localized_canvas, normalize_lang
from app.schemas.protocol import ArticleSegment, ProtocolProject, ReferenceItem
from app.services.gemini_client import gemini_router
from app.services.pipeline_tracker import emit_pipeline_step

logger = logging.getLogger(__name__)

ALLOWED_TARGET_SECTIONS = {
    s.id for s in FMSB_SECTIONS if s.id not in ("cover_page", "references", "annexes", "annexe_fiche_collecte")
}


class ExtractedSegmentDTO(BaseModel):
    target_section_ids: List[str] = Field(
        description="IDs des sections FMSB ciblées (ex: intro_contexte, chap1_justification, chap2_rappel_connaissances, chap2_etat_connaissances, chap3_type_site_duree, chap3_population_echantillon, chap3_outils_procedure, chap3_analyse_donnees)"
    )
    source_chunk_ids: List[int] = Field(
        default_factory=list,
        description="Numéros [C0, C1...] des blocs de l'article d'où provient ce segment",
    )
    summary: str = Field(
        description="Synthèse concise (1-2 phrases max) en français médical de l'information extraite"
    )
    key_quote_or_data: str = Field(
        description="Chiffres exacts (prévalence, OR/IC95%, taille échantillon) ou détail méthodologique court"
    )
    epidemiology_scope: Optional[Literal["monde", "afrique", "cameroun", "other"]] = None
    relevance_note: str = Field(
        description="Courte note (1 phrase) expliquant l'utilité pour la section FMSB"
    )


class ArticleSegmentationResult(BaseModel):
    segments: List[ExtractedSegmentDTO] = Field(default_factory=list)


def _fetch_open_access_chunks(pmcid: str, max_chunks: int = 8) -> List[str]:
    """Deterministically split Europe PMC fullTextXML into labeled section chunks in Python."""
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(url)
            if r.status_code != 200:
                return []
        root = ET.fromstring(r.text)
        body = root.find(".//body")
        if body is None:
            return []
        chunks: List[str] = []
        for sec in body.findall("./sec"):
            title = (sec.findtext("title") or "Section").strip()
            text = " ".join("".join(p.itertext()).strip() for p in sec.findall(".//p"))
            if text:
                chunks.append(f"[{title}] {text[:1200]}")
            if len(chunks) >= max_chunks:
                break
        return chunks
    except Exception as exc:
        logger.debug("Could not fetch fullTextXML for %s: %s", pmcid, exc)
        return []


def segment_article(project: ProtocolProject, ref: ReferenceItem) -> Dict[str, object]:
    """Fast hybrid segmentation: Python splits article into indexed chunks; Gemini classifies."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    chunks: List[str] = []
    if ref.abstract.strip():
        chunks.append(f"[Abstract] {ref.abstract.strip()[:1600]}")
    if ref.pmcid:
        chunks.extend(_fetch_open_access_chunks(ref.pmcid, max_chunks=6))

    if not chunks:
        return {"model_used": None, "segments": []}

    indexed_chunks = "\n".join(f"[C{idx}] {txt}" for idx, txt in enumerate(chunks))
    sections_catalog = "\n".join(
        f"- {s.id} ({s.chapter_title} -> {s.title})"
        for s in get_localized_canvas(lang)
        if s.id in ALLOWED_TARGET_SECTIONS
    )

    if lang == "en":
        prompt = f"""
You are classifying a scientific article for a CAMES/FMSB medical research protocol.
Topic: {project.research_idea or project.cover.title or project.name}

Available protocol sections:
{sections_catalog}

Article chunks (Ref #{ref.order_index or '?'} - {ref.title}, {ref.year or ''}):
{indexed_chunks}

Speed & rigor rules:
- Extract 1 to 3 key segments citing their `source_chunk_ids` ([C0], [C1]...).
- Write `summary`, `key_quote_or_data`, and `relevance_note` concisely in academic medical ENGLISH, 100% faithful to numbers in the source text.
- Indicate epidemiological scope ('monde', 'afrique', 'cameroun', or 'other').
"""
    else:
        prompt = f"""
Tu classes un article scientifique pour un protocole FMSB.
Sujet : {project.research_idea or project.cover.title or project.name}

Sections FMSB disponibles :
{sections_catalog}

Blocs de l'article (Réf #{ref.order_index or '?'} - {ref.title}, {ref.year or ''}) :
{indexed_chunks}

Consignes de rapidité et rigueur :
- Extrais 1 à 3 segments clés en citant leurs `source_chunk_ids` ([C0], [C1]...).
- Garde `summary`, `key_quote_or_data` et `relevance_note` très concis (1-2 phrases max) en français médical et 100% fidèles aux chiffres du texte.
- Indique la portée épidémiologique ('monde', 'afrique', 'cameroun', 'other').
"""
    res = gemini_router.generate_structured(
        role="segmentation",
        contents=prompt,
        schema=ArticleSegmentationResult,
        temperature=0.1,
    )
    dto: ArticleSegmentationResult = res["data"]

    created_segments: List[ArticleSegment] = []
    for seg in dto.segments:
        valid_targets = [tid for tid in seg.target_section_ids if tid in ALLOWED_TARGET_SECTIONS]
        if not valid_targets:
            valid_targets = ["chap2_etat_connaissances"]
        created_segments.append(
            ArticleSegment(
                ref_id=ref.ref_id,
                target_section_ids=valid_targets,
                summary=seg.summary,
                key_quote_or_data=seg.key_quote_or_data,
                epidemiology_scope=seg.epidemiology_scope,
                relevance_note=seg.relevance_note,
                approved=True,
            )
        )
    return {
        "model_used": res["model_used"],
        "segments": created_segments,
    }


def segment_project_articles(
    project: ProtocolProject,
    limit: int = 8,
    only_unsegmented: bool = True,
) -> Dict[str, object]:
    """Segment retrieved articles concurrently via configured Gemini reasoning chain."""
    already_segmented_ref_ids = {s.ref_id for s in project.article_segments}
    targets = [
        r
        for r in project.references
        if (not only_unsegmented or r.ref_id not in already_segmented_ref_ids)
        and (r.abstract.strip() or r.pmcid)
    ][:limit]

    models_used = set()
    added_count = 0
    errors: List[str] = []

    if not targets:
        return {
            "processed_articles": 0,
            "added_segments": 0,
            "total_segments": len(project.article_segments),
            "models_used": [],
            "errors": [],
        }

    emit_pipeline_step(
        "prepare",
        f"Préparation de {len(targets)} article(s) à segmenter...",
        f"Preparing {len(targets)} article(s) for segmentation...",
        progress_pct=15,
    )

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {}
        for ref in targets:
            ctx = contextvars.copy_context()
            fut = executor.submit(ctx.run, segment_article, project, ref)
            future_map[fut] = ref
        completed_cnt = 0
        for future in as_completed(future_map):
            ref = future_map[future]
            completed_cnt += 1
            pct = min(92, 20 + int((completed_cnt / max(len(targets), 1)) * 72))
            emit_pipeline_step(
                "segmenting",
                "Extraction & classement des segments scientifiques...",
                "Extracting & classifying scientific segments...",
                detail=f"{completed_cnt}/{len(targets)} articles",
                progress_pct=pct,
            )
            try:
                out = future.result()
                if out["model_used"]:
                    models_used.add(out["model_used"])
                for seg in out["segments"]:
                    project.article_segments.append(seg)
                    added_count += 1
            except Exception as exc:
                errors.append(f"Ref {ref.ref_id}: {exc}")

    return {
        "processed_articles": len(targets),
        "added_segments": added_count,
        "total_segments": len(project.article_segments),
        "models_used": sorted(models_used),
        "errors": errors,
    }
