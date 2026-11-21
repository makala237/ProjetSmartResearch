import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.data.i18n import normalize_lang
from app.schemas.protocol import ImageAsset, ProtocolProject, ReferenceItem
from app.services.gemini_client import gemini_router
from app.services.pipeline_tracker import emit_pipeline_step
from app.services.vancouver import format_vancouver_citation, refresh_vancouver_references

logger = logging.getLogger(__name__)

AFRICAN_PUBMED_JOURNALS_CLAUSE = (
    '("Pan Afr Med J"[Journal] OR "Health Sci Dis"[Journal] OR "Afr Health Sci"[Journal] '
    'OR "East Afr Med J"[Journal] OR "S Afr Med J"[Journal] OR "West Afr J Med"[Journal] '
    'OR "Mali Med"[Journal] OR "Dakar Med"[Journal] OR "Med Trop Sante Int"[Journal] '
    'OR "Med Sante Trop"[Journal] OR "Bull Soc Pathol Exot"[Journal])'
)

AFRICAN_PUBMED_GEO_CLAUSE = (
    '("Cameroon"[MeSH] OR "Cameroon"[Title/Abstract] OR "Africa, Central"[MeSH] '
    'OR "Africa, Western"[MeSH] OR "Africa South of the Sahara"[MeSH] OR "Sub-Saharan Africa"[Title/Abstract])'
)

AFRICAN_OPENALEX_COUNTRIES = "CM|SN|CI|CD|BF|ML|BJ|TG|GA|CG|TD|CF|NE|GN|MG|GH|NG|KE|ZA|UG|TZ|ET|RW"

_CAMEROON_FMSB_KEYWORDS = (
    "hsd-fmsb.org",
    "health sci dis",
    "health sciences and disease",
    "yaound",
    "fmsb",
    "minsante.gov.cm",
    "cameroon",
    "cameroun",
    "douala",
    "garoua",
    "buea",
    "bafoussam",
)

_AFRICA_CAMES_KEYWORDS = (
    "pan afr med j",
    "panafrican-med-journal",
    "ajol.info",
    "afr health sci",
    "african health sciences",
    "mali med",
    "dakar med",
    "ramur",
    "cames",
    "afro.who.int",
    "west afr j med",
    "east afr j med",
    "east afr med j",
    "s afr med j",
    "med trop",
    "med sante trop",
    "bull soc pathol exot",
    "afrique",
    "africa",
    "sub-saharan",
    "subsaharienne",
    "senegal",
    "sénégal",
    "dakar",
    "abidjan",
    "cote d'ivoire",
    "côte d'ivoire",
    "burkina",
    "ouagadougou",
    "bamako",
    "mali",
    "benin",
    "bénin",
    "togo",
    "gabon",
    "congo",
    "kinshasa",
    "nigeria",
    "ghana",
    "kenya",
    "uganda",
    "south africa",
)


def normalize_search_scope(scope: Optional[str]) -> str:
    """Normalize search_scope parameter to global, hybrid_africa, or africa_only."""
    val = (scope or "").strip().lower()
    if val in ("global", "africa_only", "hybrid_africa"):
        return val
    return "hybrid_africa"


def classify_reference_regional_scope(
    ref: ReferenceItem, force_min_africa: bool = False
) -> Tuple[str, Optional[str]]:
    """Classify a bibliographic reference into cameroun_fmsb, afrique_cames, or international."""
    haystack = " ".join(
        [
            ref.journal or "",
            ref.url or "",
            ref.title or "",
            (ref.abstract or "")[:400],
        ]
    ).lower()

    if any(kw in haystack for kw in _CAMEROON_FMSB_KEYWORDS):
        return "cameroun_fmsb", "Cameroun / FMSB"
    if any(kw in haystack for kw in _AFRICA_CAMES_KEYWORDS):
        return "afrique_cames", "Afrique / CAMES"
    if force_min_africa or ref.regional_scope in ("cameroun_fmsb", "afrique_cames"):
        if ref.regional_scope == "cameroun_fmsb":
            return "cameroun_fmsb", "Cameroun / FMSB"
        return "afrique_cames", "Afrique / CAMES"
    return "international", None


def tag_reference_scope(ref: ReferenceItem, force_min_africa: bool = False) -> ReferenceItem:
    """Populate regional_scope and african_source_label on a ReferenceItem."""
    scope, label = classify_reference_regional_scope(ref, force_min_africa=force_min_africa)
    ref.regional_scope = scope
    ref.african_source_label = label
    return ref


class SearchPlan(BaseModel):
    pubmed_queries: List[str] = Field(
        description="2 à 3 requêtes booléennes en anglais optimisées pour PubMed/Europe PMC"
    )
    openalex_queries: List[str] = Field(
        default_factory=list,
        description="2 requêtes simples en mots-clés anglais (sans balises [Mesh] ni parenthèses complexes) pour OpenAlex"
    )
    local_epidemiology_query: str = Field(
        description="Requête en français/anglais ciblant l'épidémiologie au Cameroun et en Afrique subsaharienne"
    )
    target_sites: List[str] = Field(
        description="Liste des plateformes/sites institutionnels à interroger (ex. PubMed, Europe PMC, OpenAlex, OMS, MINSANTE)"
    )


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def plan_research_queries(project: ProtocolProject, custom_query: Optional[str] = None) -> Tuple[SearchPlan, str]:
    """Use Gemini (research role: 3.8-flash -> fallback) to build targeted biomedical queries."""
    topic = custom_query or project.research_idea or project.cover.title or project.name
    prompt = f"""
Tu es un expert en recherche documentaire biomédicale (PubMed/MeSH, Europe PMC, OpenAlex).
Sujet du protocole : {topic}
Type d'étude pressenti : {project.study_type or "Non précisé"}

Génère un plan de recherche structuré pour trouver :
1. Des articles scientifiques internationaux récents (méthodologie, revue de la littérature, données mondiales).
2. Des études africaines et camerounaises sur ce sujet.
3. Les sites institutionnels pertinents à consulter.
"""
    res = gemini_router.generate_structured(
        role="research",
        contents=prompt,
        schema=SearchPlan,
        temperature=0.2,
    )
    return res["data"], res["model_used"]


def search_pubmed(query: str, max_results: int = 5) -> List[ReferenceItem]:
    """Query NCBI PubMed E-utilities (esearch + efetch) and parse verified metadata."""
    settings = get_settings()
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    params: Dict[str, Any] = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "sort": "relevance",
    }
    if settings.ncbi_api_key:
        params["api_key"] = settings.ncbi_api_key

    refs: List[ReferenceItem] = []
    with httpx.Client(timeout=15.0) as client:
        r = client.get(f"{base}/esearch.fcgi", params=params)
        r.raise_for_status()
        id_list = r.json().get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return refs

        fetch_params: Dict[str, Any] = {
            "db": "pubmed",
            "id": ",".join(id_list),
            "retmode": "xml",
        }
        if settings.ncbi_api_key:
            fetch_params["api_key"] = settings.ncbi_api_key

        fr = client.get(f"{base}/efetch.fcgi", params=fetch_params)
        fr.raise_for_status()

    root = ET.fromstring(fr.text)
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//MedlineCitation/PMID") or ""
        title = "".join(article.find(".//ArticleTitle").itertext()) if article.find(".//ArticleTitle") is not None else ""
        journal = (
            article.findtext(".//Journal/ISOAbbreviation")
            or article.findtext(".//Journal/Title")
            or ""
        )
        year_txt = (
            article.findtext(".//JournalIssue/PubDate/Year")
            or article.findtext(".//JournalIssue/PubDate/MedlineDate")
            or ""
        )
        year_match = re.search(r"(19|20)\d{2}", year_txt)
        year = int(year_match.group(0)) if year_match else None

        volume = article.findtext(".//JournalIssue/Volume") or ""
        issue = article.findtext(".//JournalIssue/Issue") or ""
        pages = article.findtext(".//Pagination/MedlinePgn") or ""

        authors: List[str] = []
        for au in article.findall(".//AuthorList/Author"):
            last = au.findtext("LastName") or ""
            init = au.findtext("Initials") or ""
            if last:
                authors.append(f"{last} {init}".strip())

        abstract_parts = []
        for ab in article.findall(".//Abstract/AbstractText"):
            lbl = ab.attrib.get("Label")
            txt = "".join(ab.itertext()).strip()
            if txt:
                abstract_parts.append(f"{lbl}: {txt}" if lbl else txt)

        doi = ""
        pmcid = ""
        for aid in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if aid.attrib.get("IdType") == "doi" and aid.text:
                doi = aid.text.strip()
            elif aid.attrib.get("IdType") == "pmc" and aid.text:
                pmcid = aid.text.strip()

        if title:
            ref = ReferenceItem(
                pmid=pmid or None,
                pmcid=pmcid or None,
                doi=doi or None,
                source_platform="pubmed",
                authors=authors,
                title=title.strip(),
                journal=journal.strip(),
                year=year,
                volume=volume,
                issue=issue,
                pages=pages,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                abstract="\n".join(abstract_parts),
                verified=True,
            )
            ref.vancouver_citation = format_vancouver_citation(ref)
            tag_reference_scope(ref)
            refs.append(ref)
    return refs


def search_europepmc(query: str, max_results: int = 5) -> List[ReferenceItem]:
    """Query Europe PMC open REST API (no API key required)."""
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": max_results,
    }
    refs: List[ReferenceItem] = []
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        results = r.json().get("resultList", {}).get("result", [])

    for item in results:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        author_list = []
        for au in item.get("authorList", {}).get("author", []):
            last = au.get("lastName") or ""
            init = au.get("initials") or ""
            if last:
                author_list.append(f"{last} {init}".strip())
        if not author_list and item.get("authorString"):
            author_list = [a.strip() for a in item["authorString"].split(",") if a.strip()]

        pmid = item.get("pmid")
        pmcid = item.get("pmcid")
        doi = item.get("doi")
        year_val = item.get("pubYear")
        year = int(year_val) if year_val and str(year_val).isdigit() else None

        ref = ReferenceItem(
            pmid=pmid,
            pmcid=pmcid,
            doi=doi,
            source_platform="europepmc",
            authors=author_list,
            title=title,
            journal=item.get("journalInfo", {}).get("journal", {}).get("isoabbreviation")
            or item.get("journalTitle")
            or "",
            year=year,
            volume=str(item.get("journalVolume") or ""),
            issue=str(item.get("issue") or ""),
            pages=str(item.get("pageInfo") or ""),
            url=f"https://europepmc.org/article/MED/{pmid}" if pmid else (f"https://doi.org/{doi}" if doi else ""),
            abstract=re.sub(r"<[^>]+>", "", item.get("abstractText") or "").strip(),
            verified=True,
        )
        ref.vancouver_citation = format_vancouver_citation(ref)
        tag_reference_scope(ref)
        refs.append(ref)
    return refs


def _clean_plain_query(query: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]", "", query)
    cleaned = re.sub(r"[()\"*]+", " ", cleaned)
    cleaned = re.sub(r"\b(AND|OR|NOT)\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def search_openalex(query: str, max_results: int = 5, africa_only: bool = False) -> List[ReferenceItem]:
    """Query OpenAlex works API (optionally filtered to African institutional affiliations)."""
    settings = get_settings()
    url = "https://api.openalex.org/works"
    plain_q = _clean_plain_query(query)
    if not plain_q:
        return []
    params: Dict[str, Any] = {
        "search": plain_q,
        "per-page": max_results,
        "sort": "relevance_score:desc",
    }
    if africa_only:
        params["filter"] = f"institutions.country_code:{AFRICAN_OPENALEX_COUNTRIES}"
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key
    elif settings.openalex_email:
        params["mailto"] = settings.openalex_email

    refs: List[ReferenceItem] = []
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url, params=params)
        if r.status_code in (401, 403, 429):
            logger.warning("OpenAlex skipped due to HTTP %s", r.status_code)
            return refs
        r.raise_for_status()
        works = r.json().get("results", [])

    for w in works:
        title = (w.get("title") or "").strip()
        if not title:
            continue
        authors = []
        for auth in w.get("authorships", []):
            name = auth.get("author", {}).get("display_name")
            if name:
                authors.append(name)
        doi_url = w.get("doi") or ""
        doi = doi_url.replace("https://doi.org/", "").strip() if doi_url else None
        source_name = (
            (w.get("primary_location") or {}).get("source") or {}
        ).get("display_name") or ""
        biblio = w.get("biblio") or {}
        pages = ""
        if biblio.get("first_page"):
            pages = (
                f"{biblio['first_page']}-{biblio['last_page']}"
                if biblio.get("last_page")
                else str(biblio["first_page"])
            )

        # Reconstruct abstract from OpenAlex inverted index if present
        abstract = ""
        inv = w.get("abstract_inverted_index")
        if isinstance(inv, dict) and inv:
            pos_words: Dict[int, str] = {}
            for word, positions in inv.items():
                for pos in positions:
                    pos_words[pos] = word
            abstract = " ".join(pos_words[i] for i in sorted(pos_words))

        ref = ReferenceItem(
            doi=doi,
            source_platform="openalex",
            authors=authors,
            title=title,
            journal=source_name,
            year=w.get("publication_year"),
            volume=str(biblio.get("volume") or ""),
            issue=str(biblio.get("issue") or ""),
            pages=pages,
            url=doi_url or w.get("id") or "",
            abstract=abstract,
            verified=True,
        )
        ref.vancouver_citation = format_vancouver_citation(ref)
        tag_reference_scope(ref, force_min_africa=africa_only)
        refs.append(ref)
    return refs


def search_pubmed_african(query: str, max_results: int = 4) -> List[ReferenceItem]:
    """Query PubMed targeting African/CAMES journals and Sub-Saharan/Cameroon geography."""
    plain_q = _clean_plain_query(query)
    if not plain_q:
        return []
    targeted = f"({plain_q}) AND ({AFRICAN_PUBMED_JOURNALS_CLAUSE} OR {AFRICAN_PUBMED_GEO_CLAUSE})"
    refs = search_pubmed(targeted, max_results=max_results)
    if not refs:
        fallback_q = f"({plain_q}) AND (Africa[Title/Abstract] OR Cameroon[Title/Abstract])"
        refs = search_pubmed(fallback_q, max_results=max_results)
    for ref in refs:
        tag_reference_scope(ref, force_min_africa=True)
    return refs


def search_europepmc_african(query: str, max_results: int = 4) -> List[ReferenceItem]:
    """Query Europe PMC targeting African journals (PAMJ, HSD, AHS) and African affiliations."""
    plain_q = _clean_plain_query(query)
    if not plain_q:
        return []
    targeted = (
        f'({plain_q}) AND (JOURNAL:"Pan African Medical Journal" OR '
        'JOURNAL:"Health Sciences and Disease" OR JOURNAL:"African Health Sciences" OR '
        'AFF:"Cameroon" OR AFF:"Africa" OR "Sub-Saharan Africa")'
    )
    refs = search_europepmc(targeted, max_results=max_results)
    for ref in refs:
        tag_reference_scope(ref, force_min_africa=True)
    return refs


def search_web_institutional(
    query: str, lang: str = "fr", africa_focus: bool = False
) -> Tuple[str, List[ReferenceItem], str]:
    """Use Gemini Google Search Grounding for WHO / MINSANTE / Cameroon / African journals data."""
    if africa_focus:
        if normalize_lang(lang) == "en":
            prompt = f"""
Search specifically African and Cameroonian biomedical portals and medical journals on:
{query}

Prioritize authoritative African/CAMES sources:
- Health Sciences and Disease (hsd-fmsb.org, FMSB Yaoundé I)
- The Pan African Medical Journal (panafrican-med-journal.com)
- African Journals Online (ajol.info)
- WHO Regional Office for Africa (afro.who.int)
- Cameroon Ministry of Public Health (minsante.gov.cm)
- CAMES / DICAMES, Médecine d'Afrique Noire, Mali Médical, Dakar Médical

Provide a concise factual summary of epidemiological data in Africa and Cameroon in English, citing the sources consulted.
"""
        else:
            prompt = f"""
Recherche en priorité sur les portails institutionnels et revues médicales africaines et camerounaises à propos de :
{query}

Cible explicitement ces sources de référence CAMES / Afrique :
- Health Sciences and Disease (hsd-fmsb.org, FMSB Yaoundé I)
- The Pan African Medical Journal (panafrican-med-journal.com)
- African Journals Online (ajol.info)
- OMS Bureau Régional Afrique (afro.who.int)
- Ministère de la Santé Publique du Cameroun (minsante.gov.cm)
- CAMES / DICAMES, Médecine d'Afrique Noire, Mali Médical, Dakar Médical

Résume de manière factuelle les données épidémiologiques (Cameroun, Afrique subsaharienne) en citant les sources consultées.
"""
    elif normalize_lang(lang) == "en":
        prompt = f"""
Search for reliable epidemiological figures and institutional guidelines (WHO, Cameroon Ministry of Public Health, CDC, African medical journals) on:
{query}

Provide a concise factual summary of recent statistics (Global, Africa, Cameroon) in English, citing the sources consulted.
"""
    else:
        prompt = f"""
Recherche des données épidémiologiques et recommandations institutionnelles fiables (OMS/WHO, Ministère de la Santé du Cameroun, CDC, revues africaines) sur :
{query}

Résume de manière factuelle les chiffres récents (Monde, Afrique, Cameroun) en citant les sources consultées.
"""
    res = gemini_router.generate_with_search(
        role="research",
        contents=prompt,
        temperature=0.1,
    )
    web_refs: List[ReferenceItem] = []
    seen_urls = set()
    for src in res["sources"]:
        u = src["url"]
        if u in seen_urls:
            continue
        seen_urls.add(u)
        ref = ReferenceItem(
            source_platform="web",
            authors=["Institutional / Web Source" if normalize_lang(lang) == "en" else "Source institutionnelle / Web"],
            title=src["title"],
            url=u,
            abstract=res["text"][:500],
            verified=True,
        )
        ref.vancouver_citation = format_vancouver_citation(ref)
        tag_reference_scope(ref, force_min_africa=False)
        web_refs.append(ref)
    return res["text"], web_refs, res["model_used"]


def fetch_europepmc_open_access_figures(ref: ReferenceItem) -> List[ImageAsset]:
    """Extract Open Access figure captions & EuropePMC image links when PMCID is available."""
    if not ref.pmcid:
        return []
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{ref.pmcid}/fullTextXML"
    assets: List[ImageAsset] = []
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.get(url)
            if r.status_code != 200:
                return []
        root = ET.fromstring(r.text)
        for idx, fig in enumerate(root.findall(".//fig"), start=1):
            label = (fig.findtext("label") or f"Figure {idx}").strip()
            cap_el = fig.find("caption")
            caption = "".join(cap_el.itertext()).strip() if cap_el is not None else ""
            graphic = fig.find(".//graphic")
            href = ""
            if graphic is not None:
                for k, v in graphic.attrib.items():
                    if k.endswith("href"):
                        href = v
                        break
            if caption:
                clean_href = re.sub(r"\.(jpg|jpeg|png|gif|webp)\.(?:jpg|jpeg)$", r".\1", (href or "").strip(), flags=re.IGNORECASE)
                if clean_href and not re.search(r"\.(jpg|jpeg|png|gif|webp|tif|tiff)$", clean_href, flags=re.IGNORECASE):
                    clean_href = f"{clean_href}.jpg"
                assets.append(
                    ImageAsset(
                        target_section_id="chap2_rappel_connaissances",
                        title=f"{label} ({ref.pmcid})",
                        caption=caption[:400],
                        source_url=f"https://pmc.ncbi.nlm.nih.gov/articles/{ref.pmcid}/"
                        if not clean_href
                        else f"https://europepmc.org/articles/{ref.pmcid}/bin/{clean_href}",
                        source_ref_id=ref.ref_id,
                        license_info="Open Access (Europe PMC / PMC)",
                        approved=False,
                    )
                )
            if len(assets) >= 2:
                break
    except Exception as exc:
        logger.debug("OA figure extraction skipped for %s: %s", ref.pmcid, exc)
    return assets


def run_research_pipeline(
    project: ProtocolProject,
    custom_query: Optional[str] = None,
    max_per_source: int = 4,
    include_web_grounding: bool = True,
    search_scope: str = "hybrid_africa",
) -> Dict[str, Any]:
    """Execute full multi-source literature search and update project references & images."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    scope = normalize_search_scope(search_scope)
    emit_pipeline_step(
        "plan",
        "Formulation des requêtes biomédicales...",
        "Formulating biomedical search queries...",
        progress_pct=15,
    )
    plan, planner_model = plan_research_queries(project, custom_query=custom_query)

    collected: List[ReferenceItem] = []
    source_errors: List[str] = []

    oa_queries = plan.openalex_queries[:2] if plan.openalex_queries else plan.pubmed_queries[:2]

    if scope in ("global", "hybrid_africa"):
        emit_pipeline_step(
            "pubmed",
            "Interrogation de PubMed & Europe PMC...",
            "Querying PubMed & Europe PMC...",
            detail=" | ".join(plan.pubmed_queries[:2]),
            progress_pct=35,
        )
        for q in plan.pubmed_queries[:2]:
            try:
                collected.extend(search_pubmed(q, max_results=max_per_source))
            except Exception as exc:
                source_errors.append(f"PubMed ({q}): {exc}")

            try:
                collected.extend(search_europepmc(q, max_results=max_per_source))
            except Exception as exc:
                source_errors.append(f"EuropePMC ({q}): {exc}")

        emit_pipeline_step(
            "openalex",
            "Collecte sur OpenAlex...",
            "Searching OpenAlex...",
            progress_pct=55,
        )
        for oq in oa_queries:
            try:
                collected.extend(search_openalex(oq, max_results=max_per_source, africa_only=False))
            except Exception as exc:
                source_errors.append(f"OpenAlex ({oq}): {exc}")

    if scope in ("hybrid_africa", "africa_only"):
        emit_pipeline_step(
            "africa_cames",
            "Recherche ciblée revues africaines & CAMES (HSD, PAMJ, AJOL, OpenAlex Afrique)...",
            "Searching African & CAMES journals (HSD, PAMJ, AJOL, OpenAlex Africa)...",
            progress_pct=68,
        )
        african_queries = oa_queries[:1] if scope == "hybrid_africa" else oa_queries[:2]
        for aq in african_queries:
            if not aq:
                continue
            try:
                collected.extend(search_pubmed_african(aq, max_results=max_per_source))
            except Exception as exc:
                source_errors.append(f"PubMed Africa ({aq}): {exc}")
            try:
                collected.extend(search_europepmc_african(aq, max_results=max_per_source))
            except Exception as exc:
                source_errors.append(f"EuropePMC Africa ({aq}): {exc}")
            try:
                collected.extend(search_openalex(aq, max_results=max_per_source, africa_only=True))
            except Exception as exc:
                source_errors.append(f"OpenAlex Africa ({aq}): {exc}")

    web_summary = ""
    if include_web_grounding and plan.local_epidemiology_query:
        emit_pipeline_step(
            "web_grounding",
            "Recherche Web institutionnelle (OMS / MINSANTE)...",
            "Institutional web grounding (WHO / MINSANTE)...",
            progress_pct=78,
        )
        try:
            web_summary, web_refs, _ = search_web_institutional(
                plan.local_epidemiology_query,
                lang=lang,
                africa_focus=scope in ("hybrid_africa", "africa_only"),
            )
            collected.extend(web_refs[:3])
        except Exception as exc:
            source_errors.append(f"Web Grounding: {exc}")

    emit_pipeline_step(
        "dedup_vancouver",
        "Déduplication, formatage Vancouver & figures Open Access...",
        "Deduplicating, Vancouver formatting & Open Access figures...",
        progress_pct=92,
    )

    # Deduplicate against existing project references
    existing_pmids = {r.pmid for r in project.references if r.pmid}
    existing_dois = {r.doi.lower() for r in project.references if r.doi}
    existing_titles = {_normalize_title(r.title) for r in project.references}

    added_refs: List[ReferenceItem] = []
    added_images: List[ImageAsset] = []

    for ref in collected:
        tag_reference_scope(ref, force_min_africa=False)
        norm_t = _normalize_title(ref.title)
        if ref.pmid and ref.pmid in existing_pmids:
            continue
        if ref.doi and ref.doi.lower() in existing_dois:
            continue
        if norm_t in existing_titles:
            continue

        if ref.pmid:
            existing_pmids.add(ref.pmid)
        if ref.doi:
            existing_dois.add(ref.doi.lower())
        existing_titles.add(norm_t)

        project.references.append(ref)
        added_refs.append(ref)

        # Try extracting Open Access figures when PMCID exists
        if ref.pmcid and len(added_images) < 4:
            figs = fetch_europepmc_open_access_figures(ref)
            for fig in figs:
                project.images.append(fig)
                added_images.append(fig)

    refresh_vancouver_references(project)
    african_refs_added = sum(
        1 for r in added_refs if r.regional_scope in ("cameroun_fmsb", "afrique_cames")
    )

    return {
        "planner_model": planner_model,
        "plan": plan.model_dump(),
        "search_scope": scope,
        "web_summary": web_summary,
        "added_references_count": len(added_refs),
        "african_references_count": african_refs_added,
        "total_references_count": len(project.references),
        "added_images_count": len(added_images),
        "source_errors": source_errors,
    }
