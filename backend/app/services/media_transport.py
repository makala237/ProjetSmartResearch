from __future__ import annotations

import hashlib
import io
import logging
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image

from app.core.config import get_settings
from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import ImageAsset, ProtocolProject, SectionTable

logger = logging.getLogger(__name__)

_FRONT_MATTER_IDS = {
    "cover_page",
    "toc",
    "abbreviations",
    "list_of_tables",
    "list_of_figures",
    "preliminaires",
}

_STRUCTURED_TABLE_SECTIONS = {
    "chap1_variables": ("__structured_variables__", "Liste opérationnelle des variables d'étude"),
    "chap2_etat_connaissances": ("__structured_synthesis__", "Synthèse des études antérieures"),
    "chap3_chronogramme": ("__structured_timeline__", "Chronogramme des activités"),
    "chap3_budget": ("__structured_budget__", "Budget prévisionnel de l'étude"),
}


def _clean_caption_title(raw_title: str, prefix: str = "Figure") -> str:
    txt = (raw_title or "").replace("\xa0", " ").strip()
    if not txt:
        return ""
    if prefix.lower() == "tableau":
        txt = re.sub(
            r"^\s*(?:Tableaux?|Tables?)\b(?:\s*(?:n[°o]\s*)?\d+(?:\.\d+)*\s*[:.\-–—]*|\s*[:.\-–—]+)\s*",
            "",
            txt,
            flags=re.IGNORECASE,
        )
    else:
        txt = re.sub(
            r"^\s*(?:Figures?|Figs?\.?|Sch[eé]mas?)\b(?:\s*(?:n[°o]\s*)?\d+(?:\.\d+)*\s*[:.\-–—]*|\s*[:.\-–—]+)\s*",
            "",
            txt,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\s+", " ", txt).strip()


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def clean_remote_image_url(url: str) -> str:
    """Fix common URL defects such as doubled extensions (.jpg.jpg, .png.png)."""
    u = (url or "").strip()
    if not u:
        return ""
    u = re.sub(r"\.(jpg|jpeg|png|gif|webp)\.(?:jpg|jpeg)$", r".\1", u, flags=re.IGNORECASE)
    return u


def _extract_pmcid_and_href(url_or_title: str) -> Tuple[Optional[str], str]:
    txt = (url_or_title or "").replace("\xa0", " ")
    m_pmc = re.search(r"\b(PMC\d+)\b", txt, flags=re.IGNORECASE)
    pmcid = m_pmc.group(1).upper() if m_pmc else None

    href = ""
    if "/bin/" in txt:
        href = txt.rsplit("/bin/", 1)[-1].split("?")[0].strip()
        href = clean_remote_image_url(href)
    return pmcid, href


def _extract_figure_index(title: str) -> int:
    m = re.search(r"\bfig(?:ure)?[\s\xa0]*(\d+)\b", title or "", flags=re.IGNORECASE)
    if m:
        try:
            return max(1, int(m.group(1)))
        except ValueError:
            pass
    return 1


def download_pmc_supplementary_zip(pmcid: str, force_refresh: bool = False) -> Optional[Path]:
    """Download & cache official Europe PMC REST supplementaryFiles ZIP archive."""
    clean_id = (pmcid or "").strip().upper()
    if not re.match(r"^PMC\d+$", clean_id):
        return None

    cache_dir = get_settings().storage_dir / "cache" / "pmc_zips"
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f"{clean_id}.zip"

    if zip_path.exists() and not force_refresh and zipfile.is_zipfile(zip_path):
        return zip_path

    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{clean_id}/supplementaryFiles"
    try:
        with httpx.Client(
            timeout=25.0,
            follow_redirects=True,
            headers={"User-Agent": "SmartResearchFMSB/1.0 (Academic Protocol Assistant)"},
        ) as client:
            resp = client.get(url)
        if resp.status_code == 200 and len(resp.content) > 200:
            buf = io.BytesIO(resp.content)
            if zipfile.is_zipfile(buf):
                zip_path.write_bytes(resp.content)
                return zip_path
    except Exception as exc:
        logger.debug("PMC supplementaryFiles download failed for %s: %s", clean_id, exc)
    return None


def extract_figure_from_pmc_zip(
    pmcid: str,
    filename_hint: str = "",
    figure_index: int = 1,
) -> Optional[Tuple[bytes, str]]:
    """Extract an image figure from an article's cached EBI supplementaryFiles ZIP."""
    zip_path = download_pmc_supplementary_zip(pmcid)
    if not zip_path:
        return None

    clean_hint = clean_remote_image_url(filename_hint).lower()
    hint_stem = Path(clean_hint).stem if clean_hint else ""
    img_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff"}

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            candidates = [
                info
                for info in zf.infolist()
                if not info.is_dir()
                and Path(info.filename).suffix.lower() in img_exts
                and info.file_size >= 1500
            ]
            if not candidates:
                return None

            # Prefer non-GIF full-resolution images (.jpg/.jpeg/.png/.webp) and larger byte size
            def _quality_key(info: zipfile.ZipInfo) -> Tuple[int, int]:
                ext = Path(info.filename).suffix.lower()
                is_gif = 1 if ext == ".gif" else 0
                return (is_gif, -info.file_size)

            ranked = sorted(candidates, key=_quality_key)
            chosen = None
            if clean_hint:
                # Pass 1: exact filename match
                for info in ranked:
                    if Path(info.filename).name.lower() == clean_hint:
                        chosen = info
                        break
            if chosen is None and hint_stem:
                # Pass 2: stem match preferring full-res JPG/PNG over GIF thumbnail
                for info in ranked:
                    if Path(info.filename).stem.lower() == hint_stem:
                        chosen = info
                        break
            if chosen is None and hint_stem:
                m_g = re.search(r"(g0*\d+|fig\d+)", hint_stem, flags=re.IGNORECASE)
                if m_g:
                    token = m_g.group(1).lower()
                    for info in ranked:
                        if token in Path(info.filename).name.lower():
                            chosen = info
                            break
            if chosen is None:
                # Filter out small GIF thumbnails if non-GIF files exist
                hi_res = [i for i in candidates if Path(i.filename).suffix.lower() != ".gif"] or candidates

                def sort_key(info: zipfile.ZipInfo) -> Tuple[int, str, int]:
                    name = Path(info.filename).name.lower()
                    is_fig = 0 if re.search(r"g\d+|fig", name) else 1
                    return (is_fig, name, -info.file_size)

                ordered = sorted(hi_res, key=sort_key)
                idx = min(max(0, figure_index - 1), len(ordered) - 1)
                chosen = ordered[idx]

            raw = zf.read(chosen.filename)
            ext = Path(chosen.filename).suffix.lower().lstrip(".") or "jpg"
            return raw, ext
    except Exception as exc:
        logger.debug("PMC ZIP figure extraction error for %s: %s", pmcid, exc)
        return None


def save_materialized_image_bytes(
    project_id: str,
    raw_bytes: bytes,
    preferred_asset_id: Optional[str] = None,
    min_dim: int = 80,
) -> Optional[Dict[str, Any]]:
    """Validate via Pillow, normalize to high-res JPG/PNG, persist locally, and compute A4 layout width."""
    if not raw_bytes or len(raw_bytes) < 500:
        return None
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            im.load()
            w, h = im.size
            if w < min_dim or h < min_dim:
                return None
            orig_fmt = (im.format or "PNG").upper()
            asset_id = preferred_asset_id or f"img_{uuid.uuid4().hex[:10]}"
            upload_dir = get_settings().storage_dir / "uploads" / project_id
            upload_dir.mkdir(parents=True, exist_ok=True)

            # Always normalize exotic modes / GIF / TIFF to clean PNG or RGB JPEG for python-docx stability
            if orig_fmt in ("PNG",) and im.mode in ("RGBA", "RGB", "L"):
                out_ext = "png"
                dest = upload_dir / f"{asset_id}.png"
                im.save(dest, format="PNG", optimize=True)
            else:
                out_ext = "jpg"
                dest = upload_dir / f"{asset_id}.jpg"
                rgb = im.convert("RGB")
                rgb.save(dest, format="JPEG", quality=92, optimize=True)

            # Optimal A4 display width balancing legibility and vertical fit (usable width ~16 cm, target height <= 12 cm)
            aspect = h / max(w, 1)
            if aspect > 1.15:
                rec_w_cm = round(min(11.5, max(8.5, 11.5 / aspect)), 1)
            elif aspect > 0.75:
                rec_w_cm = 13.0
            else:
                rec_w_cm = 14.5

            sha1 = hashlib.sha1(dest.read_bytes()).hexdigest()[:12]
            return {
                "asset_id": asset_id,
                "local_path": str(dest),
                "source_url": f"/api/projects/{project_id}/images/{asset_id}/file",
                "width": w,
                "height": h,
                "ext": out_ext,
                "sha1": sha1,
                "recommended_width_cm": rec_w_cm,
            }
    except Exception as exc:
        logger.debug("Pillow normalization skipped for image bytes: %s", exc)
        return None


def download_and_materialize_pmc_figure(
    project_id: str,
    pmcid: str,
    href: str = "",
    figure_index: int = 1,
    preferred_asset_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Download an Open Access figure from Europe PMC supplementaryFiles ZIP and persist locally."""
    extracted = extract_figure_from_pmc_zip(pmcid, filename_hint=href, figure_index=figure_index)
    if not extracted:
        return None
    raw_bytes, _ = extracted
    return save_materialized_image_bytes(
        project_id=project_id,
        raw_bytes=raw_bytes,
        preferred_asset_id=preferred_asset_id,
    )


def _derive_french_short_title(img: ImageAsset, pmcid: Optional[str] = None) -> str:
    """Ensure image titles are informative and clean for LISTE DES FIGURES and Word captions."""
    cleaned = _clean_caption_title(img.title, "Figure")
    is_only_pmc = bool(re.fullmatch(r"\(?PMC\d+\)?", cleaned or "", flags=re.IGNORECASE))
    if cleaned and not is_only_pmc and len(cleaned) >= 6:
        return cleaned[:130]

    cap = re.sub(r"\s+", " ", (img.caption or "").replace("\xa0", " ")).strip()
    if cap:
        # Take first meaningful sentence/clause before parenthetical enumeration overload
        first_clause = re.split(r"(?<=[.!?;])\s+|\s+\(\d+\s*:", cap)[0].strip(" .:-")
        if len(first_clause) > 105:
            first_clause = first_clause[:102].rsplit(" ", 1)[0] + "..."
        if first_clause:
            suffix = f" ({pmcid})" if pmcid and pmcid.lower() not in first_clause.lower() else ""
            return f"{first_clause}{suffix}"
    return cleaned or img.title or "Figure scientifique"


def _select_semantic_anchor(project: ProtocolProject, img: ImageAsset) -> str:
    """Pick the best paragraph anchor in the target section so the image sits beside matching text."""
    if img.anchor_hint and img.anchor_hint.strip():
        return img.anchor_hint.strip()
    sec = project.sections.get(img.target_section_id)
    if not sec or not (sec.content_markdown or "").strip():
        return ""

    paragraphs = [
        p.strip()
        for p in re.split(r"\n{2,}", sec.content_markdown)
        if len(p.strip()) >= 25 and not p.strip().startswith("#")
    ]
    if not paragraphs:
        return ""

    # Keyword overlap between caption/title and section paragraphs
    tokens = {
        w
        for w in re.findall(r"[a-zA-ZÀ-ÿ]{5,}", f"{img.title} {img.caption}".lower())
        if w not in {"figure", "clinical", "study", "diagram", "presentation", "drawn", "powerpoint"}
    }
    best_para = ""
    best_score = 0
    for para in paragraphs:
        low = para.lower()
        score = sum(1 for tok in tokens if tok[:5] in low)
        if score > best_score:
            best_score = score
            best_para = para
    chosen = best_para if best_score > 0 else paragraphs[min(1, len(paragraphs) - 1)]
    clean_snippet = re.sub(r"[*_`#>\[\]]+", "", chosen).strip()
    return clean_snippet[:65].rsplit(" ", 1)[0] if len(clean_snippet) > 65 else clean_snippet


def _wikimedia_fallback_bytes(query_text: str) -> Optional[bytes]:
    q_words = [
        w
        for w in re.findall(r"[a-zA-Z]{4,}", query_text or "")
        if w.lower() not in {"figure", "powerpoint", "presentation", "drawn", "created", "flowchart"}
    ]
    query = " ".join(q_words[:4]) or "human anatomy medical diagram"
    try:
        with httpx.Client(
            timeout=12.0,
            follow_redirects=True,
            headers={"User-Agent": "SmartResearchFMSB/1.0 (Academic Protocol Assistant)"},
        ) as client:
            r = client.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f"{query} filetype:bitmap",
                    "gsrnamespace": "6",
                    "gsrlimit": "4",
                    "prop": "imageinfo",
                    "iiprop": "url|mime|size",
                    "iiurlwidth": "1000",
                    "format": "json",
                },
            )
            if r.status_code != 200:
                return None
            pages = (r.json().get("query") or {}).get("pages") or {}
            for page in pages.values():
                infos = page.get("imageinfo") or []
                if not infos:
                    continue
                info = infos[0]
                if int(info.get("width", 0)) < 220 or int(info.get("height", 0)) < 180:
                    continue
                img_url = info.get("thumburl") or info.get("url")
                if not img_url:
                    continue
                ir = client.get(img_url)
                if ir.status_code == 200 and len(ir.content) > 1000:
                    return ir.content
    except Exception as exc:
        logger.debug("Wikimedia fallback image fetch skipped: %s", exc)
    return None


def materialize_image_asset(project: ProtocolProject, img: ImageAsset) -> bool:
    """Guarantee that an ImageAsset from Module 1 / Antigravity / Europe PMC is materialized on disk."""
    local_ok = bool(img.local_path and Path(img.local_path).exists() and Path(img.local_path).stat().st_size > 500)
    pmcid, href = _extract_pmcid_and_href(f"{img.source_url} {img.title}")
    if not pmcid and img.source_ref_id:
        ref = next((r for r in project.references if r.ref_id == img.source_ref_id), None)
        if ref and ref.pmcid:
            pmcid = ref.pmcid.upper()

    if local_ok:
        # Upgrade low-res thumbnail (e.g. 100x96 GIF) if full-res PMC version is available
        try:
            with Image.open(img.local_path) as _cur:
                cur_w, cur_h = _cur.size
        except Exception:
            cur_w, cur_h = 999, 999
        if pmcid and (cur_w < 200 or cur_h < 200):
            fig_idx = _extract_figure_index(img.title)
            upgraded = download_and_materialize_pmc_figure(
                project_id=project.project_id,
                pmcid=pmcid,
                href=href,
                figure_index=fig_idx,
                preferred_asset_id=img.asset_id,
            )
            if upgraded and upgraded["width"] > cur_w:
                img.local_path = upgraded["local_path"]
                img.source_url = upgraded["source_url"]
                img.width_cm = upgraded["recommended_width_cm"]

        # Preserve existing approved/excluded flag if already materialized via local upload URL
        expected_url = f"/api/projects/{project.project_id}/images/{img.asset_id}/file"
        if not img.source_url or img.source_url.startswith("http"):
            img.source_url = expected_url
            img.approved = True
        try:
            with Image.open(img.local_path) as im:
                w, h = im.size
            aspect = h / max(w, 1)
            if aspect > 1.15:
                img.width_cm = round(min(11.5, max(8.5, 11.5 / aspect)), 1)
            elif aspect > 0.75:
                img.width_cm = 13.0
            else:
                img.width_cm = 14.5
        except Exception:
            pass
        img.title = _derive_french_short_title(img, pmcid)
        if not img.anchor_hint:
            img.anchor_hint = _select_semantic_anchor(project, img)
        return True

    # Attempt 1: Europe PMC official REST supplementaryFiles ZIP
    saved_meta: Optional[Dict[str, Any]] = None
    if pmcid:
        fig_idx = _extract_figure_index(img.title)
        saved_meta = download_and_materialize_pmc_figure(
            project_id=project.project_id,
            pmcid=pmcid,
            href=href,
            figure_index=fig_idx,
            preferred_asset_id=img.asset_id,
        )

    # Attempt 2: Direct cleaned remote HTTP fetch
    if saved_meta is None and img.source_url and img.source_url.startswith("http"):
        cleaned_url = clean_remote_image_url(img.source_url)
        try:
            with httpx.Client(
                timeout=12.0,
                follow_redirects=True,
                headers={"User-Agent": "SmartResearchFMSB/1.0 (Academic Protocol Assistant)"},
            ) as client:
                resp = client.get(cleaned_url)
                ctype = (resp.headers.get("content-type") or "").lower()
                if resp.status_code == 200 and ("image/" in ctype or not ctype.startswith("text/")):
                    saved_meta = save_materialized_image_bytes(
                        project.project_id,
                        resp.content,
                        preferred_asset_id=img.asset_id,
                    )
        except Exception as exc:
            logger.debug("Direct image fetch failed (%s): %s", cleaned_url, exc)

    # Attempt 3: Wikimedia Open Access medical diagram fallback so pipeline figures never vanish
    if saved_meta is None:
        wm_bytes = _wikimedia_fallback_bytes(f"{img.title} {img.caption} {project.cover.title}")
        if wm_bytes:
            saved_meta = save_materialized_image_bytes(
                project.project_id,
                wm_bytes,
                preferred_asset_id=img.asset_id,
            )
            if saved_meta:
                img.license_info = img.license_info or "Wikimedia Commons (Open Access)"

    if saved_meta is None:
        return False

    img.asset_id = saved_meta["asset_id"]
    img.local_path = saved_meta["local_path"]
    img.source_url = saved_meta["source_url"]
    img.width_cm = saved_meta["recommended_width_cm"]
    img.approved = True
    img.title = _derive_french_short_title(img, pmcid)
    if not img.anchor_hint:
        img.anchor_hint = _select_semantic_anchor(project, img)
    return True


def transport_and_repair_project_images(project: ProtocolProject) -> Dict[str, Any]:
    """Transport, materialize, deduplicate, and size all downloaded/captured images for Module 2."""
    valid_images: List[ImageAsset] = []
    seen_hashes: set[str] = set()
    seen_keys: set[str] = set()
    materialized = 0
    already_local = 0
    removed_broken = 0

    for img in project.images:
        was_local = bool(img.local_path and Path(img.local_path).exists())
        ok = materialize_image_asset(project, img)
        if not ok:
            removed_broken += 1
            continue

        file_hash = ""
        if img.local_path and Path(img.local_path).exists():
            file_hash = hashlib.sha1(Path(img.local_path).read_bytes()).hexdigest()[:12]
        sec_hash_key = f"{img.target_section_id}::{file_hash}"
        title_key = f"{img.target_section_id}::{_normalize_key(img.title)}"

        if (file_hash and sec_hash_key in seen_hashes) or (
            title_key.split("::", 1)[1] and title_key in seen_keys
        ):
            continue

        if file_hash:
            seen_hashes.add(sec_hash_key)
        seen_keys.add(title_key)

        if was_local:
            already_local += 1
        else:
            materialized += 1
        valid_images.append(img)

    project.images = valid_images
    return {
        "total_images": len(valid_images),
        "materialized_from_remote": materialized,
        "already_local": already_local,
        "removed_broken": removed_broken,
    }


def build_structured_table_for_section(
    project: ProtocolProject,
    section_id: str,
) -> Optional[SectionTable]:
    """Build the virtual SectionTable representation of a global structured array if active and non-empty."""
    if section_id == "chap1_variables" and project.variables:
        return SectionTable(
            table_id="__structured_variables__",
            title="Liste opérationnelle des variables d'étude",
            headers=["Variable", "Rôle", "Définition", "Échelle", "Outil"],
            rows=[
                [v.name, v.role, v.definition or "—", v.measurement_scale or "—", v.collection_tool or "—"]
                for v in project.variables
            ],
        )

    if section_id == "chap2_etat_connaissances" and project.literature_synthesis:
        return SectionTable(
            table_id="__structured_synthesis__",
            title="Synthèse des études antérieures",
            headers=["Auteur(s), Pays (Année)", "Devis", "Population", "Résultats principaux", "Limites"],
            rows=[
                [s.authors_country_year, s.study_design, s.population, s.main_results, s.limits or "—"]
                for s in project.literature_synthesis
            ],
        )

    if section_id == "chap3_chronogramme" and project.timeline_grid.rows:
        periods = project.timeline_grid.periods
        rows = [
            [r.activity] + ["■" if p in set(r.active_periods) else "" for p in periods]
            for r in project.timeline_grid.rows
        ]
        return SectionTable(
            table_id="__structured_timeline__",
            title="Chronogramme des activités",
            headers=["Activité"] + periods,
            rows=rows,
        )

    if section_id == "chap3_budget" and project.budget:
        budget_rows: List[List[str]] = []
        grand_total = 0.0
        for b in project.budget:
            line_total = b.total_fcfa if b.total_fcfa > 0 else b.quantity * b.unit_cost_fcfa
            grand_total += line_total
            budget_rows.append(
                [
                    b.designation,
                    f"{int(b.unit_cost_fcfa):,}".replace(",", " "),
                    f"{b.quantity:g}",
                    f"{int(line_total):,}".replace(",", " "),
                ]
            )
        budget_rows.append(["TOTAL GÉNÉRAL", "", "", f"{int(grand_total):,} FCFA".replace(",", " ")])
        return SectionTable(
            table_id="__structured_budget__",
            title="Budget prévisionnel de l'étude",
            headers=["Désignation", "Prix unitaire (FCFA)", "Quantité", "Prix total (FCFA)"],
            rows=budget_rows,
        )

    return None


def get_effective_section_tables(
    project: ProtocolProject,
    section_id: str,
) -> List[SectionTable]:
    """Single canonical list of tables for a section shared by Module 1, Module 2, list_builder,
    page_layout_engine, and docx_template. Eliminates all structured/custom duplicates.
    """
    sec = project.sections.get(section_id)
    custom = [ct for ct in (getattr(sec, "custom_tables", None) or []) if ct.headers or ct.rows]

    # When an authored custom_table exists in a section, it is the canonical source of truth
    # so we NEVER render a duplicate second table (Tableau 2 + 3, Tableau 5 + 6, Tableau 12 + 13).
    if custom:
        return custom

    # If the user explicitly suppressed/deleted the structured table in Module 1, keep it suppressed
    if sec and getattr(sec, "suppress_structured_table", False):
        return []

    structured = build_structured_table_for_section(project, section_id)
    if structured is not None:
        return [structured]
    return []


def clear_structured_table_for_section(project: ProtocolProject, section_id: str) -> bool:
    """Suppress and clear the backing structured table for a section when deleted in Module 1.
    Note: for `chap1_variables`, we set `sec.suppress_structured_table = True` while keeping
    `project.variables` safe if needed by Module 3 CRF, OR clear non-CRF lists cleanly.
    """
    cleared = False
    sec = project.sections.get(section_id)
    if sec is not None and not getattr(sec, "suppress_structured_table", False):
        sec.suppress_structured_table = True
        cleared = True

    if section_id == "chap2_etat_connaissances" and project.literature_synthesis:
        project.literature_synthesis = []
        cleared = True
    elif section_id == "chap3_chronogramme" and project.timeline_grid.rows:
        project.timeline_grid.rows = []
        cleared = True
    elif section_id == "chap3_budget" and project.budget:
        project.budget = []
        cleared = True
    return cleared


def _sync_redundant_structured_on_custom_presence(project: ProtocolProject) -> List[str]:
    """Where a custom_table already defines a section's table AND a duplicate global copy exists,
    keep only the clean custom_table or remove overlapping clones so deletion in Module 1 cannot
    resurrect a phantom table in Module 2.
    """
    cleaned_sections: List[str] = []
    # Deduplicate intra-section tables & remove subset duplicates (e.g., identical eligibility tables)
    for sid, sec in project.sections.items():
        if sid in _FRONT_MATTER_IDS or not sec.custom_tables:
            continue
        unique_tables: List[SectionTable] = []
        seen_titles: set[str] = set()
        for ct in sec.custom_tables:
            if not ct.headers and not ct.rows:
                continue
            ct.title = _clean_caption_title(ct.title, "Tableau") or ct.title
            tkey = _normalize_key(ct.title)
            if tkey and tkey in seen_titles:
                continue
            seen_titles.add(tkey)
            unique_tables.append(ct)
        if len(unique_tables) != len(sec.custom_tables):
            cleaned_sections.append(sid)
        sec.custom_tables = unique_tables
    return cleaned_sections


def purge_cames_forbidden_and_redundant_tables(project: ProtocolProject) -> Dict[str, Any]:
    """Clean up legacy forbidden CAMES tables and duplicate structured/custom tables."""
    spec_by_id = {s.id: s for s in FMSB_SECTIONS}
    removed_tables: List[Dict[str, str]] = []

    for sid, sec in project.sections.items():
        if sid in _FRONT_MATTER_IDS:
            if sec.custom_tables:
                sec.custom_tables = []
            continue
        spec = spec_by_id.get(sid)
        if spec and spec.constraints.table_policy == "forbidden" and sec.custom_tables:
            for t in sec.custom_tables:
                removed_tables.append({"section_id": sid, "table_id": t.table_id, "title": t.title})
            sec.custom_tables = []

    # Remove redundant subset eligibility table in chap3_population_echantillon if >= 2 eligibility tables exist
    pop_sec = project.sections.get("chap3_population_echantillon")
    if pop_sec and len(pop_sec.custom_tables) > 1:
        elig_tables = [t for t in pop_sec.custom_tables if "ligibilit" in t.title.lower()]
        if len(elig_tables) > 1:
            # Keep the richer eligibility table (most rows), remove shorter duplicate
            best_elig = max(elig_tables, key=lambda t: len(t.rows))
            drop_ids = {t.table_id for t in elig_tables if t.table_id != best_elig.table_id}
            for t in pop_sec.custom_tables:
                if t.table_id in drop_ids:
                    removed_tables.append(
                        {"section_id": "chap3_population_echantillon", "table_id": t.table_id, "title": t.title}
                    )
            pop_sec.custom_tables = [t for t in pop_sec.custom_tables if t.table_id not in drop_ids]

    # When a structured section already has a custom_table, avoid duplicate backing list mismatches:
    # clear redundant duplicate copy on chap2_etat_connaissances and chap3_budget and chap1_variables
    for sid in ("chap1_variables", "chap2_etat_connaissances", "chap3_budget"):
        sec = project.sections.get(sid)
        if sec and sec.custom_tables:
            if clear_structured_table_for_section(project, sid):
                removed_tables.append(
                    {"section_id": sid, "table_id": "__structured_duplicate__", "title": _STRUCTURED_TABLE_SECTIONS[sid][1]}
                )

    return {
        "removed_count": len(removed_tables),
        "removed_tables": removed_tables,
    }


def synchronize_module1_to_module2_tables(
    project: ProtocolProject,
    purge_forbidden_cames: bool = False,
) -> Dict[str, Any]:
    """Synchronize tables between Module 1 and Module 2 with single-source-of-truth guarantees."""
    dedup_secs = _sync_redundant_structured_on_custom_presence(project)
    purge_report = {"removed_count": 0, "removed_tables": []}
    if purge_forbidden_cames:
        purge_report = purge_cames_forbidden_and_redundant_tables(project)

    # Also ensure chap2_etat_connaissances / chap3_budget / chap1_variables don't harbor silent duplicates
    suppressed_structured: List[str] = []
    for sid in _STRUCTURED_TABLE_SECTIONS:
        sec = project.sections.get(sid)
        if sec and sec.custom_tables:
            # If the section uses custom_tables, keep them canonical and avoid dual-table rendering
            suppressed_structured.append(sid)

    return {
        "deduplicated_sections": dedup_secs,
        "suppressed_duplicate_structured_sections": suppressed_structured,
        **purge_report,
    }
