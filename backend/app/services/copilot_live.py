import asyncio
import base64
import logging
from typing import Any, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from app.data.i18n import get_localized_canvas, normalize_lang
from app.db.datastore import as_store
from app.schemas.protocol import ProtocolProject
from app.services.copilot_service import format_supervisor_directives_block
from app.services.gemini_client import gemini_router
from app.services.licensing_service import (
    resolve_user_gemini_api_key,
    resolve_user_model_chain,
)

logger = logging.getLogger(__name__)

LIVE_SESSION_MAX_SECONDS = 900  # 15-minute safety bound per live voice session


def build_live_system_instruction(
    project: ProtocolProject,
    active_module: str = "module1",
    active_section_id: Optional[str] = None,
) -> str:
    """Build a concise voice-friendly system prompt grounded in the active protocol."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    canvas = get_localized_canvas(lang)
    spec = next((s for s in canvas if s.id == active_section_id), None)
    sec_title = f"{spec.chapter_title} — {spec.title}" if spec else (active_section_id or "Vue globale")

    sec_excerpt = ""
    if active_section_id and active_section_id in project.sections:
        sec_excerpt = (project.sections[active_section_id].content_markdown or "").strip()[:1800]

    directives = format_supervisor_directives_block(project)
    if lang == "en":
        return (
            "You are Assistant Smart Research, a warm, concise methodological mentor for medical research protocols (FMSB/CAMES).\n"
            "Speak naturally in conversational English without heavy Markdown formatting or invented numbers/citations.\n"
            f"Project title: {project.cover.title or project.name}\n"
            f"Study type: {project.study_type or 'Not specified'}\n"
            f"Active module: {active_module} | Active section: {sec_title}\n"
            + (f"{directives}\n" if directives else "")
            + (f"Active section excerpt:\n{sec_excerpt}\n" if sec_excerpt else "")
            + "Never overwrite the user's document directly from voice; explain clearly and guide step by step."
        )

    return (
        "Tu es Assistant Smart Research, un mentor méthodologique bienveillant et précis pour les protocoles médicaux FMSB / CAMES.\n"
        "Réponds à l'oral de manière claire, naturelle et concise en français, sans syntaxe Markdown ni chiffres/citations inventés.\n"
        f"Titre du protocole : {project.cover.title or project.name}\n"
        f"Type d'étude : {project.study_type or 'Non précisé'}\n"
        f"Module actif : {active_module} | Section active : {sec_title}\n"
        + (f"{directives}\n" if directives else "")
        + (f"Extrait de la section active :\n{sec_excerpt}\n" if sec_excerpt else "")
        + "Ne modifie jamais le document directement depuis la voix ; conseille l'utilisateur et explique les sources ou étapes clairement."
    )


async def _run_live_bridge(
    websocket: WebSocket,
    session: Any,
    project: ProtocolProject,
) -> None:
    stop_event = asyncio.Event()

    async def client_to_gemini() -> None:
        try:
            while not stop_event.is_set():
                msg = await websocket.receive_json()
                mtype = str(msg.get("type") or "").strip().lower()
                if mtype == "audio":
                    b64 = msg.get("data") or ""
                    if not b64:
                        continue
                    raw = base64.b64decode(b64)
                    mime = str(msg.get("mime_type") or "audio/pcm;rate=16000")
                    await session.send_realtime_input(
                        audio=types.Blob(data=raw, mime_type=mime)
                    )
                elif mtype == "video":
                    b64 = msg.get("data") or ""
                    if not b64:
                        continue
                    raw = base64.b64decode(b64)
                    mime = str(msg.get("mime_type") or "image/jpeg")
                    await session.send_realtime_input(
                        video=types.Blob(data=raw, mime_type=mime)
                    )
                elif mtype == "text":
                    txt = str(msg.get("text") or "").strip()
                    if txt:
                        await session.send_client_content(
                            turns=[{"role": "user", "parts": [{"text": txt}]}],
                            turn_complete=True,
                        )
                elif mtype == "context":
                    mod = str(msg.get("active_module") or "")
                    sid = str(msg.get("active_section_id") or "")
                    lang = normalize_lang(getattr(project, "language", "fr"))
                    note = (
                        f"[Context update: user switched to {mod}, section {sid}.]"
                        if lang == "en"
                        else f"[Changement de contexte : l'utilisateur est passé sur {mod}, section {sid}.]"
                    )
                    await session.send_realtime_input(text=note)
                elif mtype == "stop":
                    stop_event.set()
                    break
        except WebSocketDisconnect:
            stop_event.set()
        except Exception as exc:
            logger.debug("Live client_to_gemini ended: %s", exc)
            stop_event.set()

    async def gemini_to_client() -> None:
        try:
            async for resp in session.receive():
                if stop_event.is_set():
                    break
                sc = getattr(resp, "server_content", None)
                if sc is None:
                    continue

                if getattr(sc, "interrupted", False):
                    await websocket.send_json({"type": "interrupted"})

                in_tx = getattr(sc, "input_transcription", None)
                if in_tx and getattr(in_tx, "text", None):
                    await websocket.send_json(
                        {"type": "transcript", "role": "user", "text": in_tx.text}
                    )

                model_turn = getattr(sc, "model_turn", None)
                if model_turn and getattr(model_turn, "parts", None):
                    for part in model_turn.parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            raw_audio = inline.data
                            if isinstance(raw_audio, str):
                                b64_out = raw_audio
                            else:
                                b64_out = base64.b64encode(raw_audio).decode("ascii")
                            await websocket.send_json(
                                {
                                    "type": "audio",
                                    "data": b64_out,
                                    "mime_type": getattr(inline, "mime_type", None)
                                    or "audio/pcm;rate=24000",
                                }
                            )

                out_tx = getattr(sc, "output_transcription", None)
                if out_tx and getattr(out_tx, "text", None):
                    await websocket.send_json(
                        {"type": "transcript", "role": "assistant", "text": out_tx.text}
                    )

                if getattr(sc, "turn_complete", False):
                    await websocket.send_json({"type": "turn_complete"})
        except WebSocketDisconnect:
            stop_event.set()
        except Exception as exc:
            logger.debug("Live gemini_to_client ended: %s", exc)
            stop_event.set()

    tasks = [
        asyncio.create_task(client_to_gemini()),
        asyncio.create_task(gemini_to_client()),
    ]
    try:
        _, pending = await asyncio.wait(
            tasks,
            timeout=LIVE_SESSION_MAX_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def handle_copilot_live_websocket(
    websocket: WebSocket,
    project: ProtocolProject,
    user: Any,
    db: Any,
    active_module: str = "module1",
    active_section_id: Optional[str] = None,
) -> None:
    """Connect browser WebSocket cleanly to the user's configured Gemini Live model chain."""
    store = as_store(db)
    _, active_key = resolve_user_gemini_api_key(store, user)
    if not active_key:
        await websocket.send_json(
            {"type": "error", "message": "Clé API Gemini manquante pour le mode vocal."}
        )
        return

    chain_info = resolve_user_model_chain(store, user)
    custom_voice = list(chain_info.get("copilot_voice_models") or [])
    voice_models: List[str] = (
        custom_voice if custom_voice else gemini_router.resolve_copilot_voice_models()
    )

    system_instruction = build_live_system_instruction(
        project,
        active_module=active_module,
        active_section_id=active_section_id,
    )

    last_err: Optional[Exception] = None
    for model_id in voice_models:
        try:
            client = genai.Client(
                api_key=active_key,
                http_options={"api_version": "v1beta"},
            )
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction=system_instruction,
                input_audio_transcription={},
                output_audio_transcription={},
            )
            async with client.aio.live.connect(model=model_id, config=config) as session:
                await websocket.send_json(
                    {
                        "type": "ready",
                        "model_used": model_id,
                        "voice_models": voice_models,
                    }
                )
                await _run_live_bridge(websocket, session, project)
                return
        except WebSocketDisconnect:
            return
        except Exception as exc:
            last_err = exc
            logger.warning("Gemini Live model %s failed: %s", model_id, exc)
            continue

    await websocket.send_json(
        {
            "type": "error",
            "message": f"Impossible de démarrer la session vocale Live ({last_err or 'aucun modèle disponible'}).",
        }
    )
