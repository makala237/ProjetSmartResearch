from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import get_language_prompt_directive, get_localized_section_spec, normalize_lang
from app.schemas.protocol import (
    ArticleSegment,
    ImageAsset,
    ProtocolProject,
    ReferenceItem,
)
from app.services.figure_extractor import (
    search_rappel_connaissances_images_with_antigravity,
)
from app.services.gemini_client import gemini_router
from app.services.literature import (
    _normalize_title,
    fetch_europepmc_open_access_figures,
    search_europepmc,
    search_openalex,
    search_pubmed,
)
from app.services.pipeline_tracker import emit_pipeline_step
from app.services.vancouver import (
    format_vancouver_citation,
    refresh_vancouver_references,
    render_vancouver_html_list,
    render_vancouver_markdown_list,
)
from app.services.working_memory import get_upstream_and_current_memory

logger = logging.getLogger(__name__)

_SKIP_MEMORY_SECTIONS = {
    "cover_page",
    "toc",
    "abbreviations",
    "list_of_tables",
    "list_of_figures",
    "preliminaires",
    "references",
}


class ExistingTextGapAnalysis(BaseModel):
    needs_external_info: bool = Field(
        default=False,
        description="True uniquement si le texte existant manque d'informations scientifiques, statistiques ou références externes introuvables dans le contenu actuel",
    )
    needs_images: bool = Field(
        default=False,
        description="True si une figure/image scientifique issue de la littérature est utile ou demandée pour cette section",
    )
    missing_focus: str = Field(
        default="",
        description="Précision courte des données ou références manquantes à faire rechercher par Antigravity (en anglais/français)",
    )
    reasoning: str = Field(
        default="",
        description="Courte justification clinique/méthodologique de l'analyse du texte existant",
    )


class AntigravitySectionQueryPlan(BaseModel):
    pubmed_query: str = Field(
        description="Requête booléenne courte et précise en anglais pour PubMed/Europe PMC ciblant exactement cette section et ce thème"
    )
    openalex_query: str = Field(
        description="Requête simple en mots-clés anglais (sans balises MeSH) pour OpenAlex"
    )
    section_focus_note: str = Field(
        default="",
        description="Brève note méthodologique d'Antigravity sur les points clés à capturer dans la littérature pour cette section",
    )


def _get_section_spec(section_id: str, lang: str = "fr"):
    try:
        return get_localized_section_spec(section_id, lang)
    except ValueError:
        return None


def _count_words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def build_cross_section_memory(
    project: ProtocolProject,
    current_section_id: str,
) -> Tuple[str, int]:
    """Read the persistent Working Memory file (`memoire_travail.md`) for upstream + current section."""
    return get_upstream_and_current_memory(project, current_section_id)


def check_if_existing_text_needs_enrichment(
    project: ProtocolProject,
    section_id: str,
    existing_text: str,
    user_instruction: Optional[str],
    cross_section_memory: str,
) -> Tuple[ExistingTextGapAnalysis, str]:
    """Step 1 when initial text exists: Gemini evaluates if existing draft suffices
    or if new external literature / images missing from content should be requested via Decision 1 -> Antigravity.
    """
    lang = normalize_lang(getattr(project, "language", "fr"))
    spec = _get_section_spec(section_id, lang)
    instr = (user_instruction or "").strip()
    cames_reqs = spec.cames_concepts_required if spec else []
    min_refs = spec.constraints.min_references if spec else None
    fig_pol = spec.constraints.figure_policy if spec else "none"

    # Fast deterministic cue check when student explicitly demands external search / figures (FR + EN)
    explicit_search = bool(
        re.search(
            r"\b(recherch|article|pubm?ed|littérature|literature|bibliograph|référence|reference|sourc|donnée|data|statistiq|épidémiolog|epidemiolog|image|figure|illustration|capture|search|find)\b",
            instr,
            re.IGNORECASE,
        )
    )
    explicit_images = bool(
        re.search(
            r"\b(image|figure|photo|illustration|iconograph|radiograph|échograph|diagram|chart|schema)\b",
            instr,
            re.IGNORECASE,
        )
    )

    prompt = f"""
Tu es Gemini Flash (analyseur de brouillon CAMES / FMSB).
Une action est engagée sur une section qui CONTIENT DÉJÀ DU TEXTE.
Règle : tu dois raisonner EN PRIORITÉ sur le texte existant ci-dessous. Tu ne demandes de nouvelles informations externes (qui seront transmises à Gemini Decision 1 -> Antigravity) QUE si le contenu actuel manque réellement de concepts CAMES obligatoires, de références minimales ({min_refs or 0} attendues) ou de figures ({fig_pol}) introuvables dans le contenu actuel.

LANGUE DU PROTOCOLE : {'Anglais' if lang == 'en' else 'Français'}
SECTION CIBLE : {spec.chapter_title + ' — ' + spec.title if spec else section_id}
ATTENDUS FMSB : {spec.guidance_points if spec else []}
CONCEPTS CAMES OBLIGATOIRES : {cames_reqs}
THÈME DU PROTOCOLE : {project.cover.title or project.research_idea or project.name}

TEXTE EXISTANT DANS LA SECTION :
{existing_text[:3000]}

CONSIGNE DE L'ÉTUDIANT : {instr or '(Aucune consigne supplémentaire — vérifier complétude par rapport aux objectifs FMSB)'}

NOMBRE DE RÉFÉRENCES DÉJÀ DISPONIBLES DANS LE PROJET : {len(project.references)}
MÉMOIRE DES AUTRES SECTIONS :
{cross_section_memory[:1200]}

Réponds en JSON structuré (`needs_external_info`, `needs_images`, `missing_focus`, `reasoning`).
"""
    try:
        res = gemini_router.generate_structured(
            role="audit",
            contents=prompt,
            schema=ExistingTextGapAnalysis,
            temperature=0.0,
        )
        dto: ExistingTextGapAnalysis = res["data"]
        if explicit_search:
            dto.needs_external_info = True
            if not dto.missing_focus:
                dto.missing_focus = instr
        if explicit_images:
            dto.needs_images = True
        return dto, res["model_used"]
    except Exception as exc:
        logger.warning("Existing text gap evaluation fallback (%s)", exc)
        return (
            ExistingTextGapAnalysis(
                needs_external_info=explicit_search,
                needs_images=explicit_images,
                missing_focus=instr if explicit_search else "",
                reasoning="Priority reasoning preserved on existing content." if lang == "en" else "Raisonnement prioritaire conservé sur le texte existant.",
            ),
            "gemini-flash-deterministic-check",
        )


def build_section_internal_library_context(
    project: ProtocolProject,
    section_id: str,
) -> Dict[str, Any]:
    """Assemble the secondary working memory (`internal_library`) available for a specific section."""
    sec = project.sections.get(section_id)
    entries = list(getattr(sec, "internal_library", []) or []) if sec else []
    if not entries:
        return {
            "entries_count": 0,
            "total_words": 0,
            "citable_ref_ids": [],
            "tables_count": 0,
            "images_count": 0,
            "context_markdown": "",
        }

    ref_by_id = {r.ref_id: r for r in project.references}
    citable_ref_ids: List[str] = []
    seen_refs: set[str] = set()
    blocks: List[str] = []
    total_words = 0
    tables_count = 0
    images_count = 0

    for idx, entry in enumerate(entries, start=1):
        txt = (entry.content_markdown or "").strip()
        total_words += _count_words(txt)
        tables_count += len(entry.tables_markdown or [])
        images_count += len(entry.image_asset_ids or [])

        if entry.citable and entry.ref_id and entry.ref_id in ref_by_id:
            ref_obj = ref_by_id[entry.ref_id]
            if entry.ref_id not in seen_refs:
                seen_refs.add(entry.ref_id)
                citable_ref_ids.append(entry.ref_id)
            tag = (
                f"[Source interne citable #{idx} — Réf [{ref_obj.order_index or '?'}] "
                f"({entry.source_kind}) — {entry.source_filename}]"
            )
            cit_line = f"Citation Vancouver : {ref_obj.vancouver_citation or entry.vancouver_citation}"
        elif entry.citable and entry.vancouver_citation:
            tag = f"[Source interne citable #{idx} ({entry.source_kind}) — {entry.source_filename}]"
            cit_line = f"Citation Vancouver : {entry.vancouver_citation}"
        else:
            tag = (
                f"[Brouillon interne non référencé #{idx} — {entry.source_filename} "
                "— EXPLOITER LE CONTENU MAIS NE PAS INVENTER DE CITATION [n]]"
            )
            cit_line = ""

        tables_snip = (
            "\nTableaux extraits :\n" + "\n\n".join((entry.tables_markdown or [])[:2])
            if entry.tables_markdown
            else ""
        )
        block = f"{tag}\n{cit_line}\n{txt[:2200]}{tables_snip}".strip()
        blocks.append(block)

    return {
        "entries_count": len(entries),
        "total_words": total_words,
        "citable_ref_ids": citable_ref_ids,
        "tables_count": tables_count,
        "images_count": images_count,
        "context_markdown": "\n\n---\n\n".join(blocks),
    }


def route_section_with_gemini_decision_1(
    project: ProtocolProject,
    section_id: str,
    action: str,
    initial_text_present: bool,
    gap_analysis: Optional[ExistingTextGapAnalysis],
    user_instruction: Optional[str],
    force_antigravity: bool = False,
    force_external_research: bool = False,
    internal_lib: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Use Gemini Decision 1 (`models/gemini-decision-1`) as the central tri-engine router
    inspecting the section's secondary working memory (`internal_library`) FIRST before deciding
    whether to mobilize external bibliographic platforms (PubMed/EuropePMC/OpenAlex).
    """
    allow_web_images = section_id == "chap2_rappel_connaissances"
    lib_info = internal_lib or build_section_internal_library_context(project, section_id)
    has_internal = int(lib_info.get("entries_count", 0) or 0) > 0

    if force_antigravity or force_external_research:
        return {
            "decision_model": "Gemini Decision 1 (enrichissement externe demandé)",
            "call_antigravity_research": True,
            "call_antigravity_images": allow_web_images and force_antigravity,
            "internal_library_sufficient": False,
            "used_internal_library_only": False,
            "can_enrich_externally": False,
            "scores": {
                "call_antigravity_research": 1.0,
                "call_antigravity_images": 1.0 if (allow_web_images and force_antigravity) else 0.0,
                "internal_library_sufficient": 0.0,
            },
            "notes": (
                "Enrichissement externe explicite vers PubMed / Europe PMC / OpenAlex demandé par l'utilisateur."
                if force_external_research and not force_antigravity
                else (
                    "Mission directe confiée à Antigravity (références + images Rappel des connaissances)."
                    if allow_web_images
                    else "Mission directe confiée à Antigravity (références Vancouver uniquement)."
                )
            ),
        }

    # If initial text exists and Gemini Flash concluded no missing external data or images, do not solicit Decision 1 unnecessarily
    if (
        initial_text_present
        and gap_analysis is not None
        and not gap_analysis.needs_external_info
        and not gap_analysis.needs_images
    ):
        return {
            "decision_model": "Non sollicité (texte initial suffisant)",
            "call_antigravity_research": False,
            "call_antigravity_images": False,
            "internal_library_sufficient": bool(has_internal),
            "used_internal_library_only": False,
            "can_enrich_externally": has_internal,
            "scores": {},
            "notes": gap_analysis.reasoning or "Texte existant suffisant : Gemini Flash raisonne directement dessus.",
        }

    spec = _get_section_spec(section_id)
    sec_title = f"{spec.chapter_title} — {spec.title}" if spec else section_id
    topic = project.cover.title or project.research_idea or project.name

    # Preliminary/structural generated sections should not call literature APIs unless requested
    if section_id in ("toc", "abbreviations", "list_of_tables", "list_of_figures"):
        return {
            "decision_model": "Non requis (section liminaire auto-calculée)",
            "call_antigravity_research": False,
            "call_antigravity_images": False,
            "internal_library_sufficient": False,
            "used_internal_library_only": False,
            "can_enrich_externally": False,
            "scores": {},
            "notes": "Section liminaire gérée par le moteur de listes A4.",
        }

    min_words = int(spec.constraints.min_words or 120) if spec and spec.constraints else 120
    min_refs = int(spec.constraints.min_references or 0) if spec and spec.constraints else 0
    existing_section_refs = len(
        [
            seg
            for seg in project.article_segments
            if seg.approved and section_id in seg.target_section_ids
        ]
    )
    internal_words = int(lib_info.get("total_words", 0) or 0)
    internal_citable_count = len(lib_info.get("citable_ref_ids", []) or [])
    det_internal_ok = bool(
        has_internal
        and internal_words >= max(60, int(min_words * 0.35))
        and (min_refs == 0 or (internal_citable_count + existing_section_refs) >= min_refs)
    )

    context_input = (
        f"ROUTAGE PROTOCOLE FMSB (BIBLIOTHÈQUE INTERNE D'ABORD)\n"
        f"Action engagée : {action}\n"
        f"Section : {sec_title} ({section_id})\n"
        f"Normes OMS/CAMES attendues : min_words={min_words}, min_references={min_refs}\n"
        f"Thème : {topic}\n"
        f"Texte initial présent dans la section : {'OUI' if initial_text_present else 'NON (section vide)'}\n"
        f"Bibliothèque interne (mémoire secondaire de la section) : {lib_info['entries_count']} entrée(s), "
        f"{internal_words} mots, {internal_citable_count} source(s) citable(s) Vancouver, "
        f"{lib_info['tables_count']} tableau(x), {lib_info['images_count']} figure(s).\n"
        f"Extraits bibliothèque interne :\n{lib_info['context_markdown'][:1800] or '(Vide)'}\n"
        f"Analyse manques (Gemini Flash) : {gap_analysis.model_dump() if gap_analysis else 'Section vide — évaluer la bibliothèque interne puis les bases externes si insuffisant'}\n"
        f"Consigne étudiant : {user_instruction or 'Standard'}\n"
        f"Nombre de références globales du projet : {len(project.references)}"
    )

    questions = {
        "internal_library_sufficient": {
            "type": "noul",
            "instructions": (
                "La bibliothèque interne de cette section contient-elle déjà des connaissances pertinentes "
                "et suffisantes pour rédiger une section conforme aux normes OMS/CAMES (volume de mots, "
                "objectifs attendus, références requises) sans appeler les bases externes ?"
            ),
            "criteria": {
                "true_description": "La bibliothèque interne couvre de manière pertinente les besoins de la section selon les normes OMS/CAMES.",
                "false_description": "La bibliothèque interne est vide, hors-sujet ou insuffisante (mots ou références manquants).",
            },
        },
        "call_antigravity_research": {
            "type": "noul",
            "instructions": (
                "Faut-il lancer une recherche bibliographique externe sur PubMed/EuropePMC/OpenAlex "
                "pour compléter cette section ?"
            ),
            "criteria": {
                "true_description": "La bibliothèque interne est vide ou insuffisante pour atteindre les normes OMS/CAMES.",
                "false_description": "La bibliothèque interne suffit pour la rédaction actuelle sans appel externe.",
            },
        },
        "call_antigravity_images": {
            "type": "noul",
            "instructions": (
                "Faut-il charger l'agent Antigravity de rechercher/capturer une figure ou illustration scientifique "
                "pour appuyer cette section (ex. Revue de la littérature, Contexte clinique, Méthodologie) ?"
            ),
            "criteria": {
                "true_description": "Une figure issue des articles scientifiques enrichirait utilement cette section ou est demandée.",
                "false_description": "Aucune capture d'image externe nécessaire pour cette action.",
            },
        },
    }

    decision_model = "Gemini Decision 1 (models/gemini-decision-1)"
    used_internal_only = False
    internal_sufficient = det_internal_ok
    call_research = (not initial_text_present and not det_internal_ok) or bool(
        gap_analysis and gap_analysis.needs_external_info and not det_internal_ok
    )
    call_images = allow_web_images and (
        bool(gap_analysis and gap_analysis.needs_images) or (not initial_text_present and lib_info["images_count"] == 0)
    )
    scores: Dict[str, float] = {}

    try:
        dres = gemini_router.run_decision_1(input_text=context_input, questions=questions)
        scores = dres.get("scores", {})
        d_internal_ok = scores.get("internal_library_sufficient", 0.5 if det_internal_ok else 0.0) >= 0.50
        internal_sufficient = bool(det_internal_ok and d_internal_ok)
        if internal_sufficient:
            call_research = False
            used_internal_only = True
        elif not initial_text_present:
            call_research = True
        else:
            call_research = call_research or (scores.get("call_antigravity_research", 0.0) >= 0.55)
        call_images = allow_web_images and (
            call_images or (scores.get("call_antigravity_images", 0.0) >= 0.65)
        )
        notes = (
            f"Gemini Decision 1 ({dres.get('total_tokens', 0)} tokens) → "
            + (
                "Bibliothèque interne suffisante (rédaction locale uniquement)."
                if used_internal_only
                else f"Antigravity recherche={'OUI' if call_research else 'NON'}, images={'OUI' if call_images else 'NON'}."
            )
        )
    except Exception as exc:
        logger.warning("Gemini Decision 1 routing deterministic fallback (%s)", exc)
        decision_model = "Gemini Decision 1 (routage-deterministe)"
        if det_internal_ok:
            call_research = False
            used_internal_only = True
            notes = "Routage bibliothèque interne d'abord : contenu interne suffisant selon normes CAMES/OMS."
        else:
            notes = "Routage bibliothèque interne insuffisante : complément bibliographique externe déclenché."

    return {
        "decision_model": decision_model,
        "call_antigravity_research": call_research,
        "call_antigravity_images": call_images,
        "internal_library_sufficient": internal_sufficient,
        "used_internal_library_only": used_internal_only,
        "can_enrich_externally": has_internal and not call_research,
        "scores": scores,
        "notes": notes,
    }


def _download_figure_locally(
    project_id: str,
    asset: ImageAsset,
) -> ImageAsset:
    """Attempt to download an Open Access figure image locally into storage/uploads/{project_id}."""
    url = (asset.source_url or "").strip()
    if not url or not any(ext in url.lower() for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", "/bin/")):
        return asset
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            r = client.get(url)
            ctype = (r.headers.get("content-type") or "").lower()
            if r.status_code == 200 and len(r.content) > 1500 and ("image" in ctype or url.lower().endswith((".jpg", ".png", ".jpeg"))):
                ext = ".png" if "png" in ctype or url.lower().endswith(".png") else ".jpg"
                upload_dir = get_settings().storage_dir / "uploads" / project_id
                upload_dir.mkdir(parents=True, exist_ok=True)
                fname = f"{asset.asset_id}{ext}"
                fpath = upload_dir / fname
                fpath.write_bytes(r.content)
                asset.local_path = str(fpath)
                asset.source_url = f"/api/projects/{project_id}/images/{asset.asset_id}/file"
    except Exception as exc:
        logger.debug("Image capture download skipped (%s): %s", url, exc)
    return asset


def execute_antigravity_section_mission(
    project: ProtocolProject,
    section_id: str,
    focus_query: str = "",
    capture_images: bool = False,
) -> Dict[str, Any]:
    """Run Agent Antigravity for a specific protocol section:
    1. Formulate targeted biomedical search queries (Antigravity Interactions API),
    2. Query PubMed, Europe PMC & OpenAlex,
    3. Automatically add & annotate references in Vancouver format + create section ArticleSegments,
    4. Extract/capture Open Access figures for this section when requested.
    """
    lang = normalize_lang(getattr(project, "language", "fr"))
    spec = _get_section_spec(section_id, lang)
    sec_title = f"{spec.chapter_title} — {spec.title}" if spec else section_id
    topic = project.cover.title or project.research_idea or project.name

    emit_pipeline_step(
        "antigravity_plan",
        "Formulation des requêtes ciblées pour la section...",
        "Formulating section-targeted literature queries...",
        progress_pct=48,
    )

    # 1. Ask Antigravity Agent to synthesize tight section-specific search terms
    ag_note = ""
    ag_agent_used = "antigravity-preview-latest"
    pubmed_q = f"{topic} {focus_query}".strip()
    openalex_q = pubmed_q

    try:
        ag_prompt = (
            f"Pour un protocole médical FMSB sur le thème : '{topic}'\n"
            f"Section concernée : '{sec_title}'\n"
            f"Besoin spécifique : '{focus_query or 'Données scientifiques, épidémiologie, méthodologie et justifications'}'\n\n"
            "Réponds STRICTEMENT en JSON valide sans texte autour :\n"
            '{"pubmed_query": "requête booléenne anglaise concise (max 6 mots-clés)", '
            '"openalex_query": "3 à 5 mots-clés simples en anglais", '
            '"section_focus_note": "1 phrase courte sur les preuves à rechercher"}'
        )
        ag_res = gemini_router.run_antigravity_agent(
            prompt=ag_prompt,
            agent_model="antigravity-preview-latest",
            timeout_sec=25.0,
        )
        ag_agent_used = ag_res.get("agent_used") or ag_agent_used
        raw_out = (ag_res.get("output_text") or "").strip()
        m_json = re.search(r"\{.*\}", raw_out, flags=re.DOTALL)
        if m_json:
            import json

            parsed = json.loads(m_json.group(0))
            if parsed.get("pubmed_query"):
                pubmed_q = str(parsed["pubmed_query"]).strip()
            if parsed.get("openalex_query"):
                openalex_q = str(parsed["openalex_query"]).strip()
            ag_note = str(parsed.get("section_focus_note") or "").strip()
    except Exception as exc:
        logger.warning("Antigravity query planning fallback (%s)", exc)
        # Clean plain fallback query
        words = [w for w in re.split(r"\W+", f"{topic} {focus_query}") if len(w) > 3]
        pubmed_q = " ".join(words[:5]) or topic
        openalex_q = pubmed_q

    emit_pipeline_step(
        "antigravity_search",
        "Recherche PubMed, Europe PMC & OpenAlex...",
        "Searching PubMed, Europe PMC & OpenAlex...",
        detail=pubmed_q[:90],
        progress_pct=58,
    )

    # 2. Fetch from biomedical platforms (bounded to keep interactive latency snappy)
    collected: List[ReferenceItem] = []
    source_errors: List[str] = []

    for fn_name, fn, q_arg in (
        ("PubMed", search_pubmed, pubmed_q),
        ("EuropePMC", search_europepmc, pubmed_q),
        ("OpenAlex", search_openalex, openalex_q),
    ):
        try:
            collected.extend(fn(q_arg, max_results=3))
        except Exception as exc:
            source_errors.append(f"{fn_name}: {exc}")

    # 3. Deduplicate, add references, annotate Vancouver & create section-linked ArticleSegments
    existing_pmids = {r.pmid for r in project.references if r.pmid}
    existing_dois = {r.doi.lower() for r in project.references if r.doi}
    existing_titles = {_normalize_title(r.title) for r in project.references}

    added_refs: List[ReferenceItem] = []
    added_segments: List[ArticleSegment] = []

    for ref in collected:
        norm_t = _normalize_title(ref.title)
        matched_existing: Optional[ReferenceItem] = None

        if ref.pmid and ref.pmid in existing_pmids:
            matched_existing = next((r for r in project.references if r.pmid == ref.pmid), None)
        elif ref.doi and ref.doi.lower() in existing_dois:
            matched_existing = next((r for r in project.references if r.doi and r.doi.lower() == ref.doi.lower()), None)
        elif norm_t in existing_titles:
            matched_existing = next((r for r in project.references if _normalize_title(r.title) == norm_t), None)

        target_ref = matched_existing
        if target_ref is None:
            ref.vancouver_citation = format_vancouver_citation(ref)
            if ref.pmid:
                existing_pmids.add(ref.pmid)
            if ref.doi:
                existing_dois.add(ref.doi.lower())
            existing_titles.add(norm_t)
            project.references.append(ref)
            added_refs.append(ref)
            target_ref = ref

        # Ensure an approved ArticleSegment connects this reference to `section_id`
        already_seg = next(
            (s for s in project.article_segments if s.ref_id == target_ref.ref_id and section_id in s.target_section_ids),
            None,
        )
        if already_seg is None:
            seg = ArticleSegment(
                ref_id=target_ref.ref_id,
                target_section_ids=[section_id],
                summary=(target_ref.abstract or target_ref.title)[:420],
                key_quote_or_data=(target_ref.abstract or "")[:240],
                epidemiology_scope="Cameroun / Afrique"
                if any(k in (target_ref.title + " " + target_ref.abstract).lower() for k in ("cameroon", "cameroun", "africa", "afrique"))
                else "International",
                approved=True,
            )
            project.article_segments.append(seg)
            added_segments.append(seg)

    # 4. Web image search by Antigravity is STRICTLY restricted to `chap2_rappel_connaissances`
    image_search_report: Optional[Dict[str, Any]] = None
    added_images_count = 0
    if capture_images and section_id == "chap2_rappel_connaissances":
        emit_pipeline_step(
            "antigravity_images",
            "Recherche & capture de figures scientifiques Open Access...",
            "Searching & capturing Open Access scientific figures...",
            progress_pct=66,
        )
        rappel_sec = project.sections.get("chap2_rappel_connaissances")
        rappel_txt = (rappel_sec.content_markdown or "") if rappel_sec else ""
        image_search_report = search_rappel_connaissances_images_with_antigravity(
            project, section_text=rappel_txt
        )
        added_images_count = int(image_search_report.get("added_images_count", 0) or 0)

    # Refresh Vancouver indices & update project.sections["references"]
    if project.references:
        refresh_vancouver_references(project)
        ref_sec = project.sections.get("references")
        if ref_sec is not None:
            ref_sec.content_markdown = render_vancouver_markdown_list(project.references)
            ref_sec.content_html = render_vancouver_html_list(project.references)

    return {
        "agent_used": ag_agent_used,
        "pubmed_query": pubmed_q,
        "openalex_query": openalex_q,
        "section_focus_note": ag_note,
        "added_references_count": len(added_refs),
        "added_segments_count": len(added_segments),
        "added_images_count": added_images_count,
        "image_search_report": image_search_report,
        "total_references_count": len(project.references),
        "source_errors": source_errors,
    }


def orchestrate_section_action(
    project: ProtocolProject,
    section_id: str,
    action: str = "draft",
    user_instruction: Optional[str] = None,
    force_antigravity: bool = False,
    force_external_research: bool = False,
) -> Dict[str, Any]:
    """Master Tri-Engine Orchestrator for any section action (`draft`, `audit`, `fix`, `antigravity`)."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    current_sec = project.sections.get(section_id)
    existing_text = (current_sec.content_markdown or "").strip() if current_sec else ""
    initial_text_present = _count_words(existing_text) >= 15

    # Step 1: Cross-section memory + Secondary working memory (per-section Internal Library)
    emit_pipeline_step(
        "memory",
        "Lecture des mémoires de travail (primaire & bibliothèque interne)...",
        "Reading primary working memory & section internal library...",
        progress_pct=12,
    )
    cross_section_memory, memory_sections_count = build_cross_section_memory(project, section_id)
    internal_lib = build_section_internal_library_context(project, section_id)

    # Step 2: Check initial text & evaluate gap if present
    gap_analysis: Optional[ExistingTextGapAnalysis] = None
    gap_model = ""
    if initial_text_present and not force_antigravity:
        emit_pipeline_step(
            "gap_check",
            "Analyse du brouillon existant & écarts FMSB...",
            "Evaluating existing draft & FMSB requirements...",
            progress_pct=25,
        )
        gap_analysis, gap_model = check_if_existing_text_needs_enrichment(
            project=project,
            section_id=section_id,
            existing_text=existing_text,
            user_instruction=user_instruction,
            cross_section_memory=cross_section_memory,
        )

    # Step 3: Route via Gemini Decision 1 (Inspects Internal Library FIRST)
    emit_pipeline_step(
        "decision_route",
        "Arbitrage Gemini Decision 1 (bibliothèque interne d'abord)...",
        "Gemini Decision 1 routing (internal library first)...",
        progress_pct=38,
    )
    routing = route_section_with_gemini_decision_1(
        project=project,
        section_id=section_id,
        action=action,
        initial_text_present=initial_text_present,
        gap_analysis=gap_analysis,
        user_instruction=user_instruction,
        force_antigravity=force_antigravity,
        force_external_research=force_external_research,
        internal_lib=internal_lib,
    )

    # Step 4: Mobilize Antigravity if Decision 1 triggers research or image capture
    antigravity_report: Optional[Dict[str, Any]] = None
    antigravity_called = bool(routing["call_antigravity_research"] or routing["call_antigravity_images"])
    if antigravity_called:
        focus = (
            (gap_analysis.missing_focus if gap_analysis else "")
            or (user_instruction or "")
        ).strip()
        antigravity_report = execute_antigravity_section_mission(
            project=project,
            section_id=section_id,
            focus_query=focus,
            capture_images=bool(routing["call_antigravity_images"]),
        )

    if lang == "en":
        routing_summary = (
            f"Cross-section memory ({memory_sections_count} sections). "
            + (
                "Existing draft detected: priority reasoning on existing text. "
                if initial_text_present
                else "Empty section: literature retrieval triggered. "
            )
            + routing["notes"]
            + (
                f" Literature agent: +{antigravity_report['added_references_count']} Vancouver ref(s), +{antigravity_report['added_images_count']} figure(s)."
                if antigravity_report
                else ""
            )
        ).strip()
    else:
        routing_summary = (
            f"Mémoire transversale ({memory_sections_count} sections). "
            + (
                "Texte initial détecté : raisonnement prioritaire sur le contenu. "
                if initial_text_present
                else "Section initialement vide : Gemini Decision 1 a sollicité Antigravity. "
            )
            + routing["notes"]
            + (
                f" Antigravity : +{antigravity_report['added_references_count']} réf. Vancouver, +{antigravity_report['added_images_count']} image(s)."
                if antigravity_report
                else ""
            )
        ).strip()

    trace = {
        "action": action,
        "initial_text_present": initial_text_present,
        "memory_sections_count": memory_sections_count,
        "gap_model": gap_model or None,
        "gap_needs_external_info": bool(gap_analysis.needs_external_info) if gap_analysis else (not initial_text_present),
        "decision_model": routing["decision_model"],
        "decision_scores": routing.get("scores", {}),
        "antigravity_called": antigravity_called,
        "added_references_count": antigravity_report["added_references_count"] if antigravity_report else 0,
        "added_images_count": antigravity_report["added_images_count"] if antigravity_report else 0,
        "internal_library_entries_used": internal_lib["entries_count"],
        "used_internal_library_only": bool(routing.get("used_internal_library_only", False)),
        "can_enrich_externally": bool(routing.get("can_enrich_externally", False)),
        "routing_summary": routing_summary,
    }

    return {
        "orchestration_trace": trace,
        "cross_section_memory": cross_section_memory,
        "internal_library_context": internal_lib["context_markdown"],
        "used_internal_library_only": bool(routing.get("used_internal_library_only", False)),
        "internal_library_entries_used": internal_lib["entries_count"],
        "can_enrich_externally": bool(routing.get("can_enrich_externally", False)),
        "antigravity_report": antigravity_report,
    }
