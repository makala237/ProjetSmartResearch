from __future__ import annotations

import hashlib
import io
import logging
import re
from typing import Any, Dict, List, Optional
import uuid
import zipfile

import httpx
from PIL import Image
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.core.config import get_settings
from app.schemas.protocol import ImageAsset, ProtocolProject, ReferenceItem
from app.services.gemini_client import gemini_router
from app.services.literature import fetch_europepmc_open_access_figures, search_europepmc

logger = logging.getLogger(__name__)


class KnowledgeRecallImageNeeds(BaseModel):
    needs_image: bool = Field(
        default=True,
        description="True si une figure médicale/anatomique/physiopathologique enrichit la section Rappel des connaissances",
    )
    image_type: str = Field(
        default="Schéma anatomique / physiopathologique",
        description="Type de figure approprié (ex: Schéma anatomique, Algorithme diagnostique, Classification)",
    )
    search_query_en: str = Field(
        default="",
        description="Requête courte en anglais (3-5 mots-clés) pour trouver cette figure médicale en Open Access",
    )
    target_caption_fr: str = Field(
        default="",
        description="Titre / légende académique en français pour la figure recherchée",
    )
    rationale: str = Field(
        default="",
        description="Justification brève de Gemini 3.1 expliquant le choix de ce type de figure",
    )


def _save_valid_image_bytes(
    project_id: str,
    raw_bytes: bytes,
    ext_hint: str = "png",
    min_dim: int = 80,
) -> Optional[Dict[str, Any]]:
    """Validate image bytes via Pillow, filter tiny icons (<80px), and persist locally."""
    if not raw_bytes or len(raw_bytes) < 500:
        return None
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            w, h = im.size
            if w < min_dim or h < min_dim:
                return None
            fmt = (im.format or ext_hint or "PNG").lower()
            if fmt not in ("png", "jpeg", "jpg", "webp"):
                out_buf = io.BytesIO()
                im.convert("RGB").save(out_buf, format="PNG")
                raw_bytes = out_buf.getvalue()
                ext = "png"
            else:
                ext = "jpg" if fmt == "jpeg" else fmt

        digest = hashlib.sha1(raw_bytes).hexdigest()[:12]
        asset_id = f"img_{uuid.uuid4().hex[:10]}"
        upload_dir = get_settings().storage_dir / "uploads" / project_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        fpath = upload_dir / f"{asset_id}.{ext}"
        fpath.write_bytes(raw_bytes)
        return {
            "asset_id": asset_id,
            "local_path": str(fpath),
            "source_url": f"/api/projects/{project_id}/images/{asset_id}/file",
            "width": w,
            "height": h,
            "sha1": digest,
        }
    except Exception as exc:
        logger.debug("Invalid image bytes skipped: %s", exc)
        return None


def _guess_section_for_caption(caption_hint: str) -> str:
    low = (caption_hint or "").lower()
    if any(k in low for k in ("conceptuel", "cadre", "déterminant", "modèle")):
        return "chap1_cadre_theorique"
    if any(k in low for k in ("flux", "collecte", "recrutement", "procédure", "algorithme")):
        return "chap3_outils_procedure"
    if any(k in low for k in ("étude", "compar", "forest", "résultat")):
        return "chap2_etat_connaissances"
    return "chap2_rappel_connaissances"


def extract_images_from_uploaded_document(
    project: ProtocolProject,
    filename: str,
    content_bytes: bytes,
) -> List[ImageAsset]:
    """Extract ALL valid embedded images/figures from an imported .docx or .pdf document."""
    lower = filename.lower()
    extracted: List[ImageAsset] = []
    seen_hashes: set[str] = set()

    if lower.endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
                media_names = sorted(
                    n for n in zf.namelist()
                    if n.startswith("word/media/") and not n.endswith("/")
                )
                for idx, mname in enumerate(media_names, start=1):
                    raw = zf.read(mname)
                    ext_hint = mname.rsplit(".", 1)[-1] if "." in mname else "png"
                    saved = _save_valid_image_bytes(project.project_id, raw, ext_hint=ext_hint)
                    if not saved or saved["sha1"] in seen_hashes:
                        continue
                    seen_hashes.add(saved["sha1"])
                    asset = ImageAsset(
                        asset_id=saved["asset_id"],
                        title=f"Figure extraite {len(extracted) + 1} ({filename})",
                        source_url=saved["source_url"],
                        local_path=saved["local_path"],
                        caption=f"Image extraite automatiquement du document importé ({mname.split('/')[-1]}, {saved['width']}×{saved['height']} px).",
                        target_section_id="chap2_rappel_connaissances",
                        license_info="Document importé par l'utilisateur",
                        approved=True,
                    )
                    project.images.append(asset)
                    extracted.append(asset)
        except Exception as exc:
            logger.warning("DOCX image extraction error (%s): %s", filename, exc)

    elif lower.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content_bytes))
            for page_idx, page in enumerate(reader.pages, start=1):
                page_txt = (page.extract_text() or "").strip()
                cap_m = re.search(r"(?:Figure|Fig\.?)\s*\d+[^\n]{0,140}", page_txt, re.IGNORECASE)
                cap_hint = cap_m.group(0).strip() if cap_m else ""
                for img_file in getattr(page, "images", []) or []:
                    raw = getattr(img_file, "data", b"")
                    iname = getattr(img_file, "name", f"p{page_idx}.png")
                    ext_hint = iname.rsplit(".", 1)[-1] if "." in iname else "png"
                    saved = _save_valid_image_bytes(project.project_id, raw, ext_hint=ext_hint)
                    if not saved or saved["sha1"] in seen_hashes:
                        continue
                    seen_hashes.add(saved["sha1"])
                    asset = ImageAsset(
                        asset_id=saved["asset_id"],
                        title=cap_hint[:100] or f"Figure extraite p.{page_idx} ({filename})",
                        source_url=saved["source_url"],
                        local_path=saved["local_path"],
                        caption=cap_hint or f"Image extraite du PDF importé (page {page_idx}, {saved['width']}×{saved['height']} px).",
                        target_section_id=_guess_section_for_caption(cap_hint),
                        license_info="Document PDF importé par l'utilisateur",
                        approved=True,
                    )
                    project.images.append(asset)
                    extracted.append(asset)
        except Exception as exc:
            logger.warning("PDF image extraction error (%s): %s", filename, exc)

    return extracted


def analyze_knowledge_recall_image_needs(
    project: ProtocolProject,
    section_text: str,
) -> Dict[str, Any]:
    """Use Gemini 3.1 (`segmentation` role -> gemini-3.1-flash-lite) to analyze
    '1. Rappel des connaissances (1/3)' and decide what type of figure is appropriate.
    """
    topic = project.cover.title or project.research_idea or project.name
    prompt = f"""
Tu es Gemini 3.1. Analyse le contenu de la section « 1. Rappel des connaissances (1/3) » d'un protocole médical FMSB.
Thème du protocole : {topic}

Contenu actuel de la section (ou résumé du sujet si vide) :
{(section_text or '(Section en cours de création — baser le besoin sur le thème médical)')[:2800]}

Détermine précisément :
1. Quel type de figure scientifique serait le plus approprié pour enrichir ce Rappel des connaissances (ex. schéma anatomique, mécanisme physiopathologique, classification clinique, arbre diagnostique) ;
2. Une requête courte en anglais (`search_query_en`, 3 à 5 mots-clés médicaux précis) pour rechercher cette figure en Open Access / Wikimedia ;
3. La légende en français (`target_caption_fr`).
"""
    try:
        res = gemini_router.generate_structured(
            role="segmentation",
            contents=prompt,
            schema=KnowledgeRecallImageNeeds,
            temperature=0.1,
        )
        dto: KnowledgeRecallImageNeeds = res["data"]
        return {
            "needs": dto.model_dump(),
            "model_used": res["model_used"],
        }
    except Exception as exc:
        logger.warning("Gemini 3.1 image needs analysis fallback (%s)", exc)
        return {
            "needs": KnowledgeRecallImageNeeds(
                needs_image=True,
                image_type="Schéma anatomique / physiopathologique",
                search_query_en=topic[:60],
                target_caption_fr=f"Illustration clinique liée à : {topic[:80]}",
                rationale="Analyse par défaut basée sur le thème médical.",
            ).model_dump(),
            "model_used": "gemini-3.1-fallback",
        }


def _search_wikimedia_medical_images(query_en: str, limit: int = 2) -> List[Dict[str, str]]:
    """Search Wikimedia Commons for open-access medical/anatomical diagrams."""
    url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{query_en} filetype:bitmap",
        "gsrnamespace": "6",
        "gsrlimit": str(limit + 2),
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
        "iiurlwidth": "1000",
        "format": "json",
    }
    headers = {"User-Agent": "SmartResearchFMSB/1.0 (Academic Protocol Assistant)"}
    results: List[Dict[str, str]] = []
    try:
        with httpx.Client(timeout=12.0, headers=headers) as client:
            r = client.get(url, params=params)
            if r.status_code != 200:
                return results
            pages = (r.json().get("query") or {}).get("pages") or {}
            for page in pages.values():
                infos = page.get("imageinfo") or []
                if not infos:
                    continue
                info = infos[0]
                mime = (info.get("mime") or "").lower()
                if not any(m in mime for m in ("image/png", "image/jpeg")):
                    continue
                if int(info.get("width", 0)) < 250 or int(info.get("height", 0)) < 200:
                    continue
                img_url = info.get("thumburl") or info.get("url") or ""
                if not img_url:
                    continue
                title = (page.get("title") or "").replace("File:", "").rsplit(".", 1)[0]
                results.append(
                    {
                        "title": title[:120],
                        "url": img_url,
                        "license": "Wikimedia Commons (Open Access)",
                    }
                )
                if len(results) >= limit:
                    break
    except Exception as exc:
        logger.debug("Wikimedia medical image search skipped: %s", exc)
    return results


def search_rappel_connaissances_images_with_antigravity(
    project: ProtocolProject,
    section_text: str = "",
) -> Dict[str, Any]:
    """Phase 3 Strict Web Image Chain (ONLY for `chap2_rappel_connaissances`):
    1. Gemini 3.1 analyzes text and determines appropriate image type & query.
    2. Gemini Decision 1 evaluates the needs and decides to dispatch Antigravity.
    3. Antigravity searches Europe PMC Open Access (<fig>) + Wikimedia Commons and downloads locally.
    """
    # Step 1: Gemini 3.1 determines image needs
    analysis = analyze_knowledge_recall_image_needs(project, section_text)
    needs = analysis["needs"]
    gemini_31_model = analysis["model_used"]

    if not needs.get("needs_image", True):
        return {
            "gemini_31_model": gemini_31_model,
            "decision_model": "Non sollicité (Gemini 3.1 juge aucune image nécessaire)",
            "antigravity_called": False,
            "added_images_count": 0,
            "needs": needs,
        }

    # Step 2: Transmit Gemini 3.1 needs to Gemini Decision 1
    decision_model = "Gemini Decision 1 (models/gemini-decision-1)"
    call_ag = True
    try:
        dres = gemini_router.run_decision_1(
            input_text=(
                f"Section cible : chap2_rappel_connaissances (1. Rappel des connaissances)\n"
                f"Analyse Gemini 3.1 ({gemini_31_model}) :\n"
                f"- Type d'image approprié : {needs.get('image_type')}\n"
                f"- Requête suggérée : {needs.get('search_query_en')}\n"
                f"- Légende visée : {needs.get('target_caption_fr')}\n"
                f"- Justification : {needs.get('rationale')}"
            ),
            questions={
                "dispatch_antigravity_rappel_image": {
                    "type": "noul",
                    "instructions": (
                        "Faut-il transmettre ces besoins à l'agent Antigravity pour rechercher et "
                        "télécharger des figures scientifiques pour le Rappel des connaissances ?"
                    ),
                    "criteria": {
                        "true_description": "Les besoins identifiés par Gemini 3.1 justifient une recherche de figure par Antigravity.",
                        "false_description": "Aucune recherche web d'image nécessaire.",
                    },
                }
            },
        )
        score = dres.get("scores", {}).get("dispatch_antigravity_rappel_image", 1.0)
        call_ag = score >= 0.45
    except Exception as exc:
        logger.warning("Decision 1 image dispatch fallback (%s)", exc)
        decision_model = "Gemini Decision 1 (repli-deterministe)"
        call_ag = True

    if not call_ag:
        return {
            "gemini_31_model": gemini_31_model,
            "decision_model": decision_model,
            "antigravity_called": False,
            "added_images_count": 0,
            "needs": needs,
        }

    # Step 3: Antigravity searches Open Access figures using Gemini 3.1's exact query
    q_en = str(needs.get("search_query_en") or project.cover.title or project.name).strip()
    try:
        ag_res = gemini_router.run_antigravity_agent(
            prompt=(
                f"Pour la section 'Rappel des connaissances' d'un protocole médical, Gemini 3.1 recommande : "
                f"{needs.get('image_type')} ({q_en}). "
                "Donne en 1 ligne anglaise de 3 mots-clés les meilleurs termes de recherche pour trouver ce schéma anatomique/clinique."
            ),
            agent_model="antigravity-preview-latest",
            timeout_sec=20.0,
        )
        refined = (ag_res.get("output_text") or "").strip().splitlines()[0].strip(' "\'.')
        if 3 <= len(refined) <= 70:
            q_en = refined
    except Exception as exc:
        logger.debug("Antigravity image query refinement skipped: %s", exc)

    added_assets: List[ImageAsset] = []
    existing_urls = {img.source_url for img in project.images if img.source_url}

    from app.services.media_transport import materialize_image_asset

    # 3a. Search Europe PMC Open Access full-text <fig> and materialize directly via supplementaryFiles ZIP
    try:
        epmc_refs: List[ReferenceItem] = search_europepmc(q_en, max_results=4)
        for ref in epmc_refs:
            if not ref.pmcid:
                continue
            figs = fetch_europepmc_open_access_figures(ref)
            for fig in figs:
                if fig.source_url in existing_urls:
                    continue
                fig.target_section_id = "chap2_rappel_connaissances"
                fig.approved = True
                if materialize_image_asset(project, fig):
                    project.images.append(fig)
                    existing_urls.add(fig.source_url)
                    added_assets.append(fig)
                if len(added_assets) >= 2:
                    break
            if len(added_assets) >= 2:
                break
    except Exception as exc:
        logger.debug("EuropePMC OA image fetch error: %s", exc)

    # 3b. Search Wikimedia Commons medical/anatomical Open Access diagrams if < 2 found
    if len(added_assets) < 2:
        for wm in _search_wikimedia_medical_images(q_en, limit=2 - len(added_assets)):
            try:
                with httpx.Client(
                    timeout=10.0,
                    follow_redirects=True,
                    headers={"User-Agent": "SmartResearchFMSB/1.0"},
                ) as cl:
                    ir = cl.get(wm["url"])
                    if ir.status_code != 200:
                        continue
                    saved = _save_valid_image_bytes(project.project_id, ir.content)
                    if not saved or saved["source_url"] in existing_urls:
                        continue
                    asset = ImageAsset(
                        asset_id=saved["asset_id"],
                        title=needs.get("target_caption_fr") or wm["title"],
                        source_url=saved["source_url"],
                        local_path=saved["local_path"],
                        caption=f"{needs.get('image_type', 'Figure')} — {wm['title']}",
                        target_section_id="chap2_rappel_connaissances",
                        license_info=wm["license"],
                        approved=True,
                    )
                    project.images.append(asset)
                    existing_urls.add(asset.source_url)
                    added_assets.append(asset)
            except Exception:
                continue

    return {
        "gemini_31_model": gemini_31_model,
        "decision_model": decision_model,
        "antigravity_called": True,
        "refined_query": q_en,
        "added_images_count": len(added_assets),
        "needs": needs,
    }
