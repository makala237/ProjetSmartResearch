"""Supervisor video/audio directive extraction and post-generation Supervisor Alignment Gate.

Uses the dedicated `supervisor_media` Gemini routing role:
- Default model: `gemini-3.5-flash-lite` (with automatic relay to `gemini-3.1-flash-lite` -> `gemini-3.5-flash`).
- Admin-configurable per user in the Admin Portal (`model_routing_config.supervisor_media_models`).
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict, List, Optional

from google.genai import types
from pydantic import BaseModel, Field

from app.data.i18n import get_localized_canvas, normalize_lang
from app.schemas.protocol import ProtocolProject, SupervisorDirectiveItem
from app.services.copilot_tools import apply_surgical_text_patches
from app.services.gemini_client import gemini_router
from app.services.humanizer import sanitize_llm_typography

logger = logging.getLogger(__name__)

MAX_SUPERVISOR_MEDIA_BYTES = 25 * 1024 * 1024  # 25 MB (safe under Cloud Run 32 MB HTTP/1 limit)
INLINE_MEDIA_THRESHOLD_BYTES = 15 * 1024 * 1024

ALLOWED_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".mpeg", ".mpg", ".3gp"}
ALLOWED_AUDIO_EXTS = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".amr"}

_EXT_TO_MIME = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".3gp": "video/3gpp",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".amr": "audio/amr",
}


def _clean_text(raw: str) -> str:
    if not raw:
        return ""
    res = sanitize_llm_typography(raw)
    if isinstance(res, tuple):
        return str(res[0] or "")
    return str(res or "")


def resolve_media_mime_and_kind(filename: str, content_type: Optional[str] = None) -> tuple[str, str]:
    """Return (mime_type, 'video'|'audio') or raise ValueError if unsupported."""
    ext = Path(filename or "").suffix.lower()
    ct = (content_type or "").split(";")[0].strip().lower()
    if ext in ALLOWED_VIDEO_EXTS or ct.startswith("video/"):
        mime = _EXT_TO_MIME.get(ext) or (ct if ct.startswith("video/") else "video/mp4")
        return mime, "video"
    if ext in ALLOWED_AUDIO_EXTS or ct.startswith("audio/"):
        mime = _EXT_TO_MIME.get(ext) or (ct if ct.startswith("audio/") else "audio/mpeg")
        return mime, "audio"
    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed and (guessed.startswith("video/") or guessed.startswith("audio/")):
        return guessed, ("video" if guessed.startswith("video/") else "audio")
    raise ValueError(
        "Format média non supporté. Formats acceptés : MP4, WEBM, MOV (vidéo) ou OGG, MP3, M4A, WAV (audio)."
    )


class ExtractedDirectiveItem(BaseModel):
    text: str = Field(
        ...,
        description="Actionable, concise methodological or editorial directive spoken or shown by the supervisor.",
    )
    target_section_id: str = Field(
        default="",
        description="Exact canvas section_id if specific to one section, or empty string '' if global.",
    )
    timestamp_hint: str = Field(
        default="",
        description="Approximate timestamp in the video/audio (e.g. '00:45' or '02:10') if detectable.",
    )


class ExtractedMediaDirectivesOutput(BaseModel):
    summary: str = Field(
        default="",
        description="Short 1-2 sentence synthesis of the supervisor's feedback in the video/audio.",
    )
    directives: List[ExtractedDirectiveItem] = Field(
        default_factory=list,
        description="Ordered list of concrete, non-redundant directives to enforce during drafting/reformulation/audit.",
    )


class AlignmentPatchItem(BaseModel):
    target_text: str = Field(
        ...,
        description="Exact verbatim substring from the draft that violates or omits a supervisor directive.",
    )
    replacement_text: str = Field(
        ...,
        description="Updated passage aligned with the supervisor's directive.",
    )
    reason: str = Field(default="", description="Which supervisor directive this patch enforces.")


class SupervisorAlignmentOutput(BaseModel):
    is_compliant: bool = Field(
        default=True,
        description="True if the generated draft already satisfies all active supervisor directives.",
    )
    patches: List[AlignmentPatchItem] = Field(
        default_factory=list,
        description="Minimal surgical verbatim patches to align non-compliant passages with supervisor directives.",
    )
    append_markdown: str = Field(
        default="",
        description="Optional short paragraph to append ONLY if a required supervisor element is completely missing.",
    )
    summary: str = Field(
        default="",
        description="Short 1-sentence report of supervisor compliance check / adjustments made.",
    )


def get_active_directives_for_section(
    project: ProtocolProject,
    section_id: Optional[str] = None,
) -> List[SupervisorDirectiveItem]:
    """Return active global directives plus any active directives scoped to `section_id`."""
    out: List[SupervisorDirectiveItem] = []
    for d in getattr(project, "supervisor_directives", None) or []:
        if not getattr(d, "active", True):
            continue
        if not (getattr(d, "text", "") or "").strip():
            continue
        d_sec = (getattr(d, "section_id", None) or "").strip()
        if not d_sec or (section_id and d_sec == section_id):
            out.append(d)
    return out


def extract_directives_from_media(
    project: ProtocolProject,
    file_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
    section_id: Optional[str] = None,
    user_note: str = "",
) -> Dict[str, Any]:
    """Analyze a supervisor's video or audio clip with `supervisor_media` (default gemini-3.5-flash-lite)
    and append structured directives into `project.supervisor_directives`.
    """
    if not file_bytes:
        raise ValueError("Fichier média vide.")
    if len(file_bytes) > MAX_SUPERVISOR_MEDIA_BYTES:
        raise ValueError(
            "Le fichier dépasse la limite de 25 Mo. Compressez la vidéo ou découpez-la en segments plus courts."
        )

    mime_type, media_kind = resolve_media_mime_and_kind(filename, content_type)
    lang = normalize_lang(getattr(project, "language", "fr"))
    canvas = get_localized_canvas(lang)
    valid_section_ids = {s.id for s in canvas}
    canvas_listing = "\n".join(f"- {s.id} : {s.chapter_title} — {s.title}" for s in canvas[:28])

    default_scope = section_id if (section_id and section_id in valid_section_ids) else ""

    prompt = f"""
Tu es un assistant méthodologique expert (FMSB / CAMES).
L'étudiant vient de charger un enregistrement {media_kind.upper()} de son encadreur / directeur de thèse (ou une note orale personnelle).
Analyse attentivement TOUT le contenu {"visuel (diapositives, texte à l'écran, schémas) et sonore (voix)" if media_kind == "video" else "sonore (voix)"} de ce fichier.

TÂCHE :
1. Extrais toutes les consignes, corrections, exigences méthodologiques, limites, critères ou attentes précises formulées par l'encadreur.
2. Transforme chaque consigne en une directive claire, concise, directement exploitable lors de la rédaction, reformulation et audit du protocole.
3. Pour chaque directive, indique `target_section_id` parmi les identifiants du canevas ci-dessous si elle concerne une section précise, ou `""` si elle s'applique à tout le protocole.
   {"(Contexte actuel de l'étudiant : section '" + default_scope + "'. Si la consigne porte clairement sur cette section, utilise '" + default_scope + "'.)" if default_scope else ""}
4. Indique `timestamp_hint` (format `MM:SS`) lorsque c'est identifiable.
5. Langue de sortie : {"English" if lang == "en" else "Français"}.
{f"NOTE DE L'ÉTUDIANT : {user_note.strip()}" if user_note.strip() else ""}

SECTIONS DU CANEVAS FMSB :
{canvas_listing}
"""

    uploaded_file = None
    tmp_path: Optional[str] = None
    client = gemini_router._get_client()

    try:
        if len(file_bytes) <= INLINE_MEDIA_THRESHOLD_BYTES:
            media_part: Any = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        else:
            suffix = Path(filename or "media.bin").suffix or ".bin"
            fd, tmp_path = tempfile.mkstemp(prefix="sr_sup_media_", suffix=suffix)
            with os.fdopen(fd, "wb") as fh:
                fh.write(file_bytes)
            uploaded_file = client.files.upload(file=tmp_path, config={"mime_type": mime_type})
            deadline = time.monotonic() + 60.0
            while getattr(uploaded_file, "state", None) and str(uploaded_file.state).upper().endswith("PROCESSING"):
                if time.monotonic() >= deadline:
                    break
                time.sleep(2.0)
                uploaded_file = client.files.get(name=uploaded_file.name)
            media_part = uploaded_file

        res = gemini_router.generate_structured(
            role="supervisor_media",
            contents=[media_part, prompt],
            schema=ExtractedMediaDirectivesOutput,
            temperature=0.15,
        )
    finally:
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    dto: ExtractedMediaDirectivesOutput = res["data"]
    model_used = str(res.get("model_used") or "")
    summary = _clean_text((dto.summary or "").strip())

    existing_texts = {
        (d.text or "").strip().lower() for d in (project.supervisor_directives or [])
    }
    created: List[SupervisorDirectiveItem] = []
    for item in dto.directives or []:
        clean_txt = _clean_text((item.text or "").strip())
        if not clean_txt or clean_txt.lower() in existing_texts:
            continue
        existing_texts.add(clean_txt.lower())
        raw_sid = (item.target_section_id or "").strip()
        resolved_sid: Optional[str] = raw_sid if raw_sid in valid_section_ids else (default_scope or None)
        d_obj = SupervisorDirectiveItem(
            text=clean_txt,
            source=media_kind,
            section_id=resolved_sid,
            media_filename=(filename or "")[:120],
            timestamp_hint=(item.timestamp_hint or "").strip()[:24],
            active=True,
        )
        project.supervisor_directives.append(d_obj)
        created.append(d_obj)

    if not summary:
        summary = (
            f"{len(created)} directive(s) extraite(s) depuis « {filename} »."
            if lang != "en"
            else f"{len(created)} directive(s) extracted from '{filename}'."
        )

    return {
        "media_kind": media_kind,
        "filename": filename,
        "summary": summary,
        "created_directives": [d.model_dump(mode="json") for d in created],
        "created_count": len(created),
        "model_used": model_used,
    }


def align_text_with_supervisor_directives(
    project: ProtocolProject,
    section_id: str,
    content_markdown: str,
) -> Dict[str, Any]:
    """Internal post-generation Supervisor Alignment Gate.

    Runs ONLY when active directives exist for this section (or globally), checking the
    generated text with `supervisor_media` (default gemini-3.5-flash-lite) and applying
    minimal surgical patches if any supervisor requirement was missed.
    """
    clean_input = (content_markdown or "").strip()
    directives = get_active_directives_for_section(project, section_id=section_id)
    if not clean_input or not directives:
        return {
            "content_markdown": content_markdown,
            "adjusted": False,
            "directives_checked": len(directives),
            "summary": "",
            "model_used": "",
        }

    lang = normalize_lang(getattr(project, "language", "fr"))
    directives_lines = "\n".join(
        f"- [{d.source.upper()}{' · ' + d.timestamp_hint if d.timestamp_hint else ''}] {d.text}"
        for d in directives
    )

    prompt = f"""
Tu es le vérificateur interne de conformité aux directives de l'encadreur (FMSB / CAMES).
Compare le BROUILLON GÉNÉRÉ ci-dessous avec CHAQUE DIRECTIVE ACTIVE DE L'ENCADREUR.

RÈGLES STRICTES :
1. Si le brouillon respecte déjà l'ensemble des directives applicables à cette section → réponds `is_compliant=true` avec `patches=[]` et `append_markdown=""`.
2. Si une ou plusieurs directives ne sont pas respectées → réponds `is_compliant=false` et fournis UNIQUEMENT les `patches` chirurgicaux minimaux (`target_text` copié MOT POUR MOT depuis le brouillon → `replacement_text`) et/ou un court `append_markdown` si un élément obligatoire manque totalement.
3. Ne supprime jamais les citations Vancouver `[n]` existantes et n'invente jamais de données chiffrées.
4. Langue : {"English" if lang == "en" else "Français"}.

SECTION : {section_id}
DIRECTIVES ACTIVES DE L'ENCADREUR ({len(directives)}) :
{directives_lines}

BROUILLON GÉNÉRÉ À VÉRIFIER :
{clean_input}
"""

    try:
        res = gemini_router.generate_structured(
            role="supervisor_media",
            contents=prompt,
            schema=SupervisorAlignmentOutput,
            temperature=0.15,
        )
        dto: SupervisorAlignmentOutput = res["data"]
        model_used = str(res.get("model_used") or "")
        summary = _clean_text((dto.summary or "").strip())

        if dto.is_compliant and not dto.patches and not (dto.append_markdown or "").strip():
            return {
                "content_markdown": content_markdown,
                "adjusted": False,
                "directives_checked": len(directives),
                "summary": summary
                or (
                    f"Conforme aux {len(directives)} directive(s) de l'encadreur."
                    if lang != "en"
                    else f"Compliant with all {len(directives)} supervisor directive(s)."
                ),
                "model_used": model_used,
            }

        updated_md = clean_input
        applied_count = 0
        if dto.patches:
            raw_patches = [
                {
                    "target_text": p.target_text,
                    "replacement_text": _clean_text(p.replacement_text),
                    "reason": _clean_text(p.reason),
                }
                for p in dto.patches
            ]
            patch_res = apply_surgical_text_patches(updated_md, raw_patches)
            updated_md = patch_res["patched_markdown"]
            applied_count = patch_res["applied_count"]

        append_txt = _clean_text((dto.append_markdown or "").strip())
        if append_txt:
            updated_md = f"{updated_md}\n\n{append_txt}".strip()
            applied_count += 1

        if applied_count > 0:
            return {
                "content_markdown": updated_md,
                "adjusted": True,
                "directives_checked": len(directives),
                "summary": summary
                or (
                    f"Aligné automatiquement sur {len(directives)} directive(s) de l'encadreur ({applied_count} ajustement(s))."
                    if lang != "en"
                    else f"Auto-aligned with {len(directives)} supervisor directive(s) ({applied_count} adjustment(s))."
                ),
                "model_used": model_used,
            }

        return {
            "content_markdown": content_markdown,
            "adjusted": False,
            "directives_checked": len(directives),
            "summary": summary,
            "model_used": model_used,
        }
    except Exception as exc:
        logger.warning("Supervisor alignment gate skipped due to error: %s", exc)
        return {
            "content_markdown": content_markdown,
            "adjusted": False,
            "directives_checked": len(directives),
            "summary": "",
            "model_used": "",
        }
