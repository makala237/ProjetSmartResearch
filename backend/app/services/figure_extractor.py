from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid
import zipfile

from docx import Document
import httpx
from PIL import Image
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.generic import ContentStream

from app.core.config import get_settings
from app.schemas.protocol import ImageAsset, ProtocolProject, ReferenceItem
from app.services.blob_storage import build_object_key, get_blob_storage
from app.services.gemini_client import gemini_router
from app.services.literature import fetch_europepmc_open_access_figures, search_europepmc

logger = logging.getLogger(__name__)

_FRONT_MATTER_SECTIONS = {
    "cover_page",
    "toc",
    "abbreviations",
    "list_of_tables",
    "list_of_figures",
    "preliminaires",
}

_COVER_KEYWORDS = (
    "république",
    "republic",
    "ministère",
    "ministry",
    "université",
    "university",
    "faculté",
    "faculty",
    "protocole",
    "thèse",
    "mémoire",
    "directeur",
    "matricule",
)

_CAPTION_RE = re.compile(
    r"(?:^|\b)[A-Za-zÀ-ÿ]?(?P<label>(?:Figure|Fig\.?|Sch[eé]ma|Graphique|Diagramme|Illustration)\s*\d*)\s*[:.\-–—]\s*(?P<body>[^\n]{3,160})",
    re.IGNORECASE,
)

_CAPTION_STANDALONE_RE = re.compile(
    r"^\s*[A-Za-zÀ-ÿ]?((?:Figure|Fig\.?|Sch[eé]ma|Graphique|Diagramme|Illustration)\s*\d+(?:\s*[:.\-–—]\s*[^\n]{2,160})?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


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


def _mirror_asset_to_blob(project_id: str, fpath: Path) -> None:
    try:
        blob = get_blob_storage()
        if blob.is_remote and fpath.exists():
            blob.upload_file(build_object_key("uploads", project_id, fpath.name), fpath)
    except Exception as exc:
        logger.debug("Remote blob mirror skipped for extracted image %s: %s", fpath.name, exc)


def _existing_project_image_hashes(project: ProtocolProject) -> set[str]:
    hashes: set[str] = set()
    for img in getattr(project, "images", []) or []:
        if not img.local_path:
            continue
        p = Path(img.local_path)
        if p.exists() and p.is_file():
            try:
                hashes.add(hashlib.sha1(p.read_bytes()).hexdigest()[:12])
            except Exception:
                pass
    return hashes


def _normalize_image_bytes(
    raw_or_pil: Any,
    ext_hint: str = "png",
    min_dim: int = 70,
) -> Optional[Tuple[bytes, str, int, int]]:
    """Validate and normalize image bytes or a PIL Image into web/Word-safe bytes."""
    try:
        if isinstance(raw_or_pil, Image.Image):
            im = raw_or_pil.copy()
            src_bytes: Optional[bytes] = None
        else:
            if not raw_or_pil or len(raw_or_pil) < 400:
                return None
            src_bytes = bytes(raw_or_pil)
            im = Image.open(io.BytesIO(src_bytes))

        with im:
            w, h = im.size
            if w < min_dim or h < min_dim:
                return None

            fmt = (im.format or ext_hint or "PNG").lower()
            is_web_safe_mode = im.mode in ("RGB", "RGBA", "L", "LA")
            if (
                src_bytes is not None
                and fmt in ("png", "jpeg", "jpg", "webp")
                and is_web_safe_mode
            ):
                out_bytes = src_bytes
                ext = "jpg" if fmt == "jpeg" else fmt
            else:
                has_alpha = im.mode in ("RGBA", "LA") or (
                    im.mode == "P" and "transparency" in im.info
                )
                converted = im.convert("RGBA" if has_alpha else "RGB")
                out_buf = io.BytesIO()
                converted.save(out_buf, format="PNG")
                out_bytes = out_buf.getvalue()
                ext = "png"

        return out_bytes, ext, w, h
    except Exception as exc:
        logger.debug("Invalid image skipped: %s", exc)
        return None


def _save_valid_image_bytes(
    project_id: str,
    raw_bytes: Any,
    ext_hint: str = "png",
    min_dim: int = 70,
    seen_hashes: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Validate image bytes/PIL via Pillow, filter tiny icons (<70px), deduplicate, and persist."""
    normalized = _normalize_image_bytes(raw_bytes, ext_hint=ext_hint, min_dim=min_dim)
    if not normalized:
        return None
    out_bytes, ext, w, h = normalized
    digest = hashlib.sha1(out_bytes).hexdigest()[:12]
    if seen_hashes is not None and digest in seen_hashes:
        return None

    asset_id = f"img_{uuid.uuid4().hex[:10]}"
    upload_dir = get_settings().storage_dir / "uploads" / project_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    fpath = upload_dir / f"{asset_id}.{ext}"
    fpath.write_bytes(out_bytes)
    _mirror_asset_to_blob(project_id, fpath)

    return {
        "asset_id": asset_id,
        "local_path": str(fpath),
        "source_url": f"/api/projects/{project_id}/images/{asset_id}/file",
        "width": w,
        "height": h,
        "sha1": digest,
    }


def _guess_section_for_caption(caption_hint: str) -> str:
    low = (caption_hint or "").lower()
    if any(k in low for k in ("conceptuel", "cadre", "déterminant", "modèle", "problème")):
        return "chap1_cadre_theorique"
    if any(k in low for k in ("flux", "collecte", "recrutement", "procédure", "algorithme", "échantillon")):
        return "chap3_outils_procedure"
    if any(k in low for k in ("étude", "compar", "forest", "résultat", "prévalence", "surveillance", "oms")):
        return "chap2_etat_connaissances"
    return "chap2_rappel_connaissances"


def _clean_extracted_caption(raw_cap: str) -> str:
    txt = re.sub(r"\s+", " ", (raw_cap or "").replace("\xa0", " ")).strip()
    # Fix OCR / merged prefix character such as "MFigure 6:"
    txt = re.sub(r"^[A-Za-z](?=(?:Figure|Fig\.?|Sch[eé]ma)\b)", "", txt, flags=re.IGNORECASE)
    return txt[:140].strip()


def _extract_captions_from_text(text: str) -> List[str]:
    """Extract ordered figure/diagram captions found inside a page or paragraph block."""
    if not text:
        return []
    found: List[str] = []
    seen: set[str] = set()
    for m in _CAPTION_RE.finditer(text):
        label = re.sub(r"\s+", " ", m.group("label")).strip()
        body = _clean_extracted_caption(m.group("body"))
        if not body or len(body) < 4:
            continue
        # Ignore prose references like "Figure 1 ci-dessous montre..." when body starts with verb phrases
        if re.match(r"^(?:ci[- ]dessous|ci[- ]dessus|suivante?)\s+(?:montre|illustre|pr[eé]sente)", body, re.IGNORECASE):
            continue
        candidate = _clean_extracted_caption(f"{label} : {body}")
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            found.append(candidate)

    if not found:
        for m in _CAPTION_STANDALONE_RE.finditer(text):
            candidate = _clean_extracted_caption(m.group(1))
            if candidate and candidate.lower() not in seen:
                seen.add(candidate.lower())
                found.append(candidate)
    return found


def _extract_anchor_sentence(context_text: str) -> str:
    """Return a clean non-caption prose sentence near an image to use as anchor_hint."""
    if not context_text:
        return ""
    lines = [
        ln.strip()
        for ln in context_text.splitlines()
        if ln.strip()
        and not re.match(r"^[A-Za-z]?(?:Figure|Fig\.?|Sch[eé]ma|Graphique)\s*\d*\s*[:.\-–—]", ln.strip(), re.IGNORECASE)
    ]
    joined = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if len(joined) < 18:
        return ""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", joined) if len(s.strip()) >= 18]
    if not sentences:
        return ""
    for sent in reversed(sentences):
        if re.search(r"\b(?:fig(?:ure)?|sch[eé]ma|illustr[eé]|ci[- ]dessous)\b", sent, re.IGNORECASE):
            return sent[:140]
    return sentences[-1][:140]


def _looks_like_cover_text(text: str) -> bool:
    low = (text or "").lower()
    hits = sum(1 for kw in _COVER_KEYWORDS if kw in low)
    return hits >= 2


def _extract_docx_images(
    project: ProtocolProject,
    filename: str,
    content_bytes: bytes,
    seen_hashes: set[str],
    elem_idx_to_section: Dict[int, str],
) -> List[ImageAsset]:
    """Walk DOCX body in reading order (excluding headers/footers & cover logos) to extract figures."""
    extracted: List[ImageAsset] = []
    seen_media_targets: set[str] = set()
    header_footer_targets: set[str] = set()

    # Identify media embedded inside headers/footers via ZIP rels
    try:
        with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
            for rel_name in zf.namelist():
                if not re.match(r"^word/_rels/(?:header|footer)\d*\.xml\.rels$", rel_name):
                    continue
                rel_xml = zf.read(rel_name).decode("utf-8", errors="ignore")
                for tgt in re.findall(r'Target="([^"]+)"', rel_xml):
                    norm_tgt = tgt.lstrip("./")
                    if norm_tgt.startswith("../"):
                        norm_tgt = norm_tgt.replace("../", "word/")
                    elif not norm_tgt.startswith("word/"):
                        norm_tgt = f"word/{norm_tgt}"
                    header_footer_targets.add(norm_tgt)
    except Exception:
        pass

    try:
        doc = Document(io.BytesIO(content_bytes))
    except Exception as exc:
        logger.warning("DOCX open failed during image extraction (%s): %s", filename, exc)
        return extracted

    R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
    V_NS = "urn:schemas-microsoft-com:vml"
    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    body_children = list(doc.element.body)
    block_texts: List[str] = []
    block_hits: List[Tuple[int, str, str, bytes]] = []

    for elem_idx, child in enumerate(body_children):
        txt = re.sub(
            r"\s+",
            " ",
            "".join(t.text for t in child.iter(f"{{{W_NS}}}t") if t.text),
        ).strip()
        block_texts.append(txt)

        rids_in_order: List[str] = []
        for el in child.iter():
            if el.tag == f"{{{A_NS}}}blip":
                rid = el.get(f"{{{R_NS}}}embed") or el.get(f"{{{R_NS}}}link")
                if rid:
                    rids_in_order.append(rid)
            elif el.tag == f"{{{V_NS}}}imagedata":
                rid = el.get(f"{{{R_NS}}}id")
                if rid:
                    rids_in_order.append(rid)

        for rid in rids_in_order:
            rel = doc.part.rels.get(rid)
            if rel is None or rel.is_external:
                continue
            try:
                part = rel.target_part
                blob = part.blob
                partname = str(getattr(part, "partname", "")).lstrip("/")
                if partname in header_footer_targets:
                    continue
                block_hits.append((elem_idx, partname, txt, blob))
            except Exception:
                continue

    for elem_idx, partname, own_txt, blob in block_hits:
        ext_hint = partname.rsplit(".", 1)[-1] if "." in partname else "png"
        norm = _normalize_image_bytes(blob, ext_hint=ext_hint, min_dim=70)
        if not norm:
            if partname:
                seen_media_targets.add(partname)
            continue
        _, _, w, h = norm

        next_txt = ""
        for look in range(elem_idx + 1, min(len(block_texts), elem_idx + 3)):
            if block_texts[look]:
                next_txt = block_texts[look]
                break
        prev_txt = ""
        for look in range(elem_idx - 1, max(-1, elem_idx - 3), -1):
            if block_texts[look]:
                prev_txt = block_texts[look]
                break

        caps = (
            _extract_captions_from_text(own_txt)
            or _extract_captions_from_text(next_txt)
            or _extract_captions_from_text(prev_txt)
        )
        caption_str = caps[0] if caps else ""

        mapped_sec = elem_idx_to_section.get(elem_idx, "")
        near_cover_ctx = " ".join(block_texts[max(0, elem_idx - 2) : min(len(block_texts), elem_idx + 3)])
        is_cover_logo = (
            not caption_str
            and max(w, h) <= 380
            and (
                mapped_sec == "cover_page"
                or elem_idx <= 12
                or _looks_like_cover_text(near_cover_ctx)
            )
        )
        if is_cover_logo:
            if partname:
                seen_media_targets.add(partname)
            continue

        saved = _save_valid_image_bytes(
            project.project_id,
            blob,
            ext_hint=ext_hint,
            min_dim=70,
            seen_hashes=seen_hashes,
        )
        if not saved:
            continue
        seen_hashes.add(saved["sha1"])
        if partname:
            seen_media_targets.add(partname)

        target_sec = (
            mapped_sec
            if mapped_sec and mapped_sec not in _FRONT_MATTER_SECTIONS
            else _guess_section_for_caption(caption_str or prev_txt)
        )
        anchor_hint = _extract_anchor_sentence(prev_txt or own_txt)
        asset = ImageAsset(
            asset_id=saved["asset_id"],
            title=(caption_str or f"Figure extraite {len(extracted) + 1} ({filename})")[:120],
            source_url=saved["source_url"],
            local_path=saved["local_path"],
            caption=caption_str
            or f"Image extraite du document ({partname.split('/')[-1] or 'figure'}, {saved['width']}×{saved['height']} px).",
            target_section_id=target_sec,
            anchor_hint=anchor_hint,
            license_info="Document Word importé",
            approved=True,
        )
        project.images.append(asset)
        extracted.append(asset)

    # Secondary safety sweep over word/media/* for grouped/SmartArt media omitted from body walk
    try:
        with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
            media_names = sorted(
                n for n in zf.namelist()
                if n.startswith("word/media/") and not n.endswith("/")
            )
            for mname in media_names:
                if mname in seen_media_targets or mname in header_footer_targets:
                    continue
                raw = zf.read(mname)
                ext_hint = mname.rsplit(".", 1)[-1] if "." in mname else "png"
                saved = _save_valid_image_bytes(
                    project.project_id,
                    raw,
                    ext_hint=ext_hint,
                    min_dim=120,
                    seen_hashes=seen_hashes,
                )
                if not saved:
                    continue
                seen_hashes.add(saved["sha1"])
                asset = ImageAsset(
                    asset_id=saved["asset_id"],
                    title=f"Figure extraite {len(extracted) + 1} ({filename})",
                    source_url=saved["source_url"],
                    local_path=saved["local_path"],
                    caption=f"Image extraite automatiquement du document importé ({mname.split('/')[-1]}, {saved['width']}×{saved['height']} px).",
                    target_section_id="chap2_rappel_connaissances",
                    license_info="Document Word importé",
                    approved=True,
                )
                project.images.append(asset)
                extracted.append(asset)
    except Exception as exc:
        logger.debug("DOCX media fallback sweep error (%s): %s", filename, exc)

    return extracted


def _parse_pdfimages_list(list_stdout: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in list_stdout.splitlines():
        s = line.strip()
        if not s or s.startswith("page") or s.startswith("---"):
            continue
        parts = s.split()
        if len(parts) < 11:
            continue
        try:
            page_no = int(parts[0])
            img_num = int(parts[1])
            img_type = parts[2].lower()
            width = int(parts[3])
            height = int(parts[4])
            # Columns: page num type width height color comp bpc enc interp [object ID (num gen)] x-ppi y-ppi size ratio
            if parts[10].lower() == "inline":
                obj_id = f"inline:{page_no}:{img_num}"
            elif len(parts) >= 12 and parts[11].isdigit():
                obj_id = f"{parts[10]}:{parts[11]}"
            else:
                obj_id = parts[10]
        except ValueError:
            continue
        rows.append(
            {
                "page": page_no,
                "num": img_num,
                "type": img_type,
                "width": width,
                "height": height,
                "obj_id": obj_id,
            }
        )
    return rows


def _filter_pdf_image_candidates(
    entries: List[Dict[str, Any]],
    page_texts: Dict[int, str],
    page_captions: Dict[int, List[str]],
) -> List[Dict[str, Any]]:
    """Filter smasks, tiny icons (<70px), repeated headers/watermarks (>=3 pages), and cover logos."""
    pages_by_obj: Dict[Any, set[int]] = {}
    for ent in entries:
        if ent.get("type", "image") == "smask":
            continue
        oid = ent.get("obj_key")
        if oid is not None:
            pages_by_obj.setdefault(oid, set()).add(int(ent["page"]))

    kept: List[Dict[str, Any]] = []
    for ent in entries:
        if ent.get("type", "image") == "smask":
            continue
        w = int(ent.get("width", 0))
        h = int(ent.get("height", 0))
        if w < 70 or h < 70:
            continue

        oid = ent.get("obj_key")
        if oid is not None and len(pages_by_obj.get(oid, set())) >= 3:
            continue

        page_no = int(ent["page"])
        p_txt = page_texts.get(page_no, "")
        p_caps = page_captions.get(page_no, [])
        if page_no == 1 and not p_caps and max(w, h) <= 360 and _looks_like_cover_text(p_txt):
            continue

        kept.append(ent)
    return kept


def _extract_pdf_via_poppler(
    project: ProtocolProject,
    filename: str,
    content_bytes: bytes,
    page_texts: Dict[int, str],
    page_captions: Dict[int, List[str]],
    page_to_section: Dict[int, str],
    seen_hashes: set[str],
) -> Optional[List[ImageAsset]]:
    pdfimages_exe = shutil.which("pdfimages")
    if not pdfimages_exe:
        return None

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        pdf_path = tmp_dir / "input.pdf"
        pdf_path.write_bytes(content_bytes)

        try:
            list_res = subprocess.run(
                [pdfimages_exe, "-list", str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if list_res.returncode != 0:
                return None
        except Exception as exc:
            logger.debug("pdfimages -list failed: %s", exc)
            return None

        raw_rows = _parse_pdfimages_list(list_res.stdout)
        entries = [
            {
                **r,
                "obj_key": r["obj_id"],
            }
            for r in raw_rows
        ]
        kept = _filter_pdf_image_candidates(entries, page_texts, page_captions)
        if not kept:
            return []

        prefix = tmp_dir / "img"
        try:
            ext_res = subprocess.run(
                [pdfimages_exe, "-png", "-p", str(pdf_path), str(prefix)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if ext_res.returncode != 0:
                return None
        except Exception as exc:
            logger.debug("pdfimages -png -p failed: %s", exc)
            return None

        file_by_key: Dict[Tuple[int, int], Path] = {}
        for fpath in sorted(tmp_dir.glob("img-*.png")):
            m = re.match(r"^img-(\d+)-(\d+)\.png$", fpath.name)
            if m:
                file_by_key[(int(m.group(1)), int(m.group(2)))] = fpath

        extracted: List[ImageAsset] = []
        caption_cursor_by_page: Dict[int, int] = {}

        for ent in kept:
            page_no = int(ent["page"])
            img_num = int(ent["num"])
            fpath = file_by_key.get((page_no, img_num))
            if not fpath or not fpath.exists():
                continue
            saved = _save_valid_image_bytes(
                project.project_id,
                fpath.read_bytes(),
                ext_hint="png",
                min_dim=70,
                seen_hashes=seen_hashes,
            )
            if not saved:
                continue
            seen_hashes.add(saved["sha1"])

            caps = page_captions.get(page_no, [])
            c_idx = caption_cursor_by_page.get(page_no, 0)
            cap = caps[c_idx] if c_idx < len(caps) else (caps[-1] if caps else "")
            caption_cursor_by_page[page_no] = c_idx + 1

            mapped_sec = page_to_section.get(page_no, "")
            target_sec = (
                mapped_sec
                if mapped_sec and mapped_sec not in _FRONT_MATTER_SECTIONS
                else _guess_section_for_caption(cap or page_texts.get(page_no, "")[:350])
            )
            anchor_hint = _extract_anchor_sentence(page_texts.get(page_no, ""))
            asset = ImageAsset(
                asset_id=saved["asset_id"],
                title=(cap or f"Figure extraite p.{page_no} ({filename})")[:120],
                source_url=saved["source_url"],
                local_path=saved["local_path"],
                caption=cap
                or f"Image extraite du PDF importé (page {page_no}, {saved['width']}×{saved['height']} px).",
                target_section_id=target_sec,
                anchor_hint=anchor_hint,
                license_info="Document PDF importé",
                approved=True,
            )
            project.images.append(asset)
            extracted.append(asset)

        return extracted


def _collect_page_drawn_image_xobjects(
    resources: Any,
    content_obj: Any,
    reader: PdfReader,
    visited_forms: Optional[set[int]] = None,
) -> List[Tuple[str, Optional[int], int, int]]:
    """Walk PDF content stream `Do` operators (including nested Form XObjects) on one page."""
    if visited_forms is None:
        visited_forms = set()
    hits: List[Tuple[str, Optional[int], int, int]] = []
    if not resources or content_obj is None:
        return hits

    xobjs = resources.get("/XObject")
    if hasattr(xobjs, "get_object"):
        xobjs = xobjs.get_object()
    if not xobjs:
        return hits

    try:
        cs = ContentStream(content_obj, reader)
    except Exception:
        return hits

    for operands, op in cs.operations:
        if op != b"Do" or not operands:
            continue
        name = str(operands[0])
        try:
            raw_ref = xobjs.raw_get(name) if hasattr(xobjs, "raw_get") else None
            obj = xobjs.get(name)
            if hasattr(obj, "get_object"):
                obj = obj.get_object()
            if not obj:
                continue
            sub = str(obj.get("/Subtype", ""))
            idnum = getattr(raw_ref, "idnum", None)
            if sub == "/Image":
                w = int(obj.get("/Width", 0) or 0)
                h = int(obj.get("/Height", 0) or 0)
                clean_name = name.lstrip("/")
                hits.append((clean_name, idnum, w, h))
            elif sub == "/Form":
                form_key = idnum or id(obj)
                if form_key in visited_forms:
                    continue
                visited_forms.add(form_key)
                sub_res = obj.get("/Resources") or resources
                if hasattr(sub_res, "get_object"):
                    sub_res = sub_res.get_object()
                hits.extend(
                    _collect_page_drawn_image_xobjects(
                        sub_res,
                        obj,
                        reader,
                        visited_forms=visited_forms,
                    )
                )
        except Exception:
            continue
    return hits


def _extract_pdf_images_pypdf_stream(
    project: ProtocolProject,
    filename: str,
    reader: PdfReader,
    page_texts: Dict[int, str],
    page_captions: Dict[int, List[str]],
    page_to_section: Dict[int, str],
    seen_hashes: set[str],
) -> List[ImageAsset]:
    """Pure-Python fallback that tracks actual `Do` operators per page (handles shared Resources)."""
    extracted: List[ImageAsset] = []
    entries: List[Dict[str, Any]] = []
    page_drawn: List[Tuple[int, Any, List[Tuple[str, Optional[int], int, int]]]] = []

    for page_idx, page in enumerate(reader.pages, start=1):
        res = page.get("/Resources")
        if hasattr(res, "get_object"):
            res = res.get_object()
        drawn = _collect_page_drawn_image_xobjects(res, page.get_contents(), reader)
        page_drawn.append((page_idx, page, drawn))

    has_any_drawn = any(drawn for _, _, drawn in page_drawn)

    for page_idx, page, drawn in page_drawn:
        if not drawn and has_any_drawn:
            # Skip text-only pages that merely inherit a shared PDF XObject Resource dictionary.
            continue

        page_imgs = list(getattr(page, "images", []) or [])
        if not drawn and not has_any_drawn:
            # Fallback only when content streams across the document yielded no Do image operators.
            for im in page_imgs:
                oid = getattr(getattr(im, "indirect_reference", None), "idnum", None)
                iname = getattr(im, "name", f"img_{page_idx}.png")
                try:
                    w, h = im.image.size
                except Exception:
                    w, h = (0, 0)
                drawn.append((Path(iname).stem, oid, w, h))

        if not drawn:
            continue

        page_img_by_id: Dict[int, Any] = {}
        page_img_by_stem: Dict[str, Any] = {}
        for im in page_imgs:
            oid = getattr(getattr(im, "indirect_reference", None), "idnum", None)
            if oid is not None:
                page_img_by_id[oid] = im
            stem = Path(getattr(im, "name", "")).stem
            if stem:
                page_img_by_stem[stem] = im

        for clean_name, idnum, w, h in drawn:
            im_file = page_img_by_id.get(idnum) if idnum is not None else page_img_by_stem.get(clean_name)
            if im_file is None:
                continue
            entries.append(
                {
                    "page": page_idx,
                    "type": "image",
                    "width": w or getattr(getattr(im_file, "image", None), "size", (0, 0))[0],
                    "height": h or getattr(getattr(im_file, "image", None), "size", (0, 0))[1],
                    "obj_key": idnum if idnum is not None else clean_name,
                    "im_file": im_file,
                }
            )

    kept = _filter_pdf_image_candidates(entries, page_texts, page_captions)
    caption_cursor_by_page: Dict[int, int] = {}

    for ent in kept:
        page_no = int(ent["page"])
        im_file = ent["im_file"]
        pil_or_bytes: Any = None
        try:
            pil_or_bytes = im_file.image
        except Exception:
            pil_or_bytes = getattr(im_file, "data", b"")

        iname = getattr(im_file, "name", f"p{page_no}.png")
        ext_hint = iname.rsplit(".", 1)[-1] if "." in iname else "png"
        saved = _save_valid_image_bytes(
            project.project_id,
            pil_or_bytes,
            ext_hint=ext_hint,
            min_dim=70,
            seen_hashes=seen_hashes,
        )
        if not saved and pil_or_bytes is not getattr(im_file, "data", b""):
            saved = _save_valid_image_bytes(
                project.project_id,
                getattr(im_file, "data", b""),
                ext_hint=ext_hint,
                min_dim=70,
                seen_hashes=seen_hashes,
            )
        if not saved:
            continue
        seen_hashes.add(saved["sha1"])

        caps = page_captions.get(page_no, [])
        c_idx = caption_cursor_by_page.get(page_no, 0)
        cap = caps[c_idx] if c_idx < len(caps) else (caps[-1] if caps else "")
        caption_cursor_by_page[page_no] = c_idx + 1

        mapped_sec = page_to_section.get(page_no, "")
        target_sec = (
            mapped_sec
            if mapped_sec and mapped_sec not in _FRONT_MATTER_SECTIONS
            else _guess_section_for_caption(cap or page_texts.get(page_no, "")[:350])
        )
        anchor_hint = _extract_anchor_sentence(page_texts.get(page_no, ""))
        asset = ImageAsset(
            asset_id=saved["asset_id"],
            title=(cap or f"Figure extraite p.{page_no} ({filename})")[:120],
            source_url=saved["source_url"],
            local_path=saved["local_path"],
            caption=cap
            or f"Image extraite du PDF importé (page {page_no}, {saved['width']}×{saved['height']} px).",
            target_section_id=target_sec,
            anchor_hint=anchor_hint,
            license_info="Document PDF importé",
            approved=True,
        )
        project.images.append(asset)
        extracted.append(asset)

    return extracted


def _extract_pdf_images(
    project: ProtocolProject,
    filename: str,
    content_bytes: bytes,
    seen_hashes: set[str],
    page_to_section: Dict[int, str],
) -> List[ImageAsset]:
    """Extract real scientific images per page using Poppler pdfimages with pure-Python stream fallback."""
    try:
        reader = PdfReader(io.BytesIO(content_bytes))
    except Exception as exc:
        logger.warning("PDF open failed during image extraction (%s): %s", filename, exc)
        return []

    page_texts: Dict[int, str] = {}
    page_captions: Dict[int, List[str]] = {}
    for page_idx, page in enumerate(reader.pages, start=1):
        txt = (page.extract_text() or "").strip()
        page_texts[page_idx] = txt
        page_captions[page_idx] = _extract_captions_from_text(txt)

    poppler_extracted = _extract_pdf_via_poppler(
        project=project,
        filename=filename,
        content_bytes=content_bytes,
        page_texts=page_texts,
        page_captions=page_captions,
        page_to_section=page_to_section,
        seen_hashes=seen_hashes,
    )
    if poppler_extracted is not None:
        return poppler_extracted

    return _extract_pdf_images_pypdf_stream(
        project=project,
        filename=filename,
        reader=reader,
        page_texts=page_texts,
        page_captions=page_captions,
        page_to_section=page_to_section,
        seen_hashes=seen_hashes,
    )


def extract_images_from_uploaded_document(
    project: ProtocolProject,
    filename: str,
    content_bytes: bytes,
    section_context: Optional[Dict[str, Any]] = None,
) -> List[ImageAsset]:
    """Extract valid embedded figures from an imported .docx or .pdf document with page/section context."""
    lower = filename.lower()
    seen_hashes = _existing_project_image_hashes(project)
    ctx = section_context or {}
    elem_idx_to_section: Dict[int, str] = ctx.get("elem_idx_to_section") or {}
    page_to_section: Dict[int, str] = ctx.get("page_to_section") or {}

    if lower.endswith(".docx"):
        return _extract_docx_images(
            project=project,
            filename=filename,
            content_bytes=content_bytes,
            seen_hashes=seen_hashes,
            elem_idx_to_section=elem_idx_to_section,
        )

    if lower.endswith(".pdf"):
        return _extract_pdf_images(
            project=project,
            filename=filename,
            content_bytes=content_bytes,
            seen_hashes=seen_hashes,
            page_to_section=page_to_section,
        )

    return []


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
    seen_hashes = _existing_project_image_hashes(project)

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
                    saved = _save_valid_image_bytes(
                        project.project_id,
                        ir.content,
                        seen_hashes=seen_hashes,
                    )
                    if not saved or saved["source_url"] in existing_urls:
                        continue
                    seen_hashes.add(saved["sha1"])
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
