from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.data.i18n import get_localized_canvas, normalize_lang
from app.schemas.protocol import (
    CopilotMessage,
    CopilotProposal,
    ProtocolProject,
    SectionState,
    SectionVersionEntry,
    TextPatchOperation,
)
from app.services.gemini_client import gemini_router
from app.services.rich_formatter import markdown_to_clean_html


def _count_words(text: str) -> int:
    return len(re.findall(r"\S+", (text or "").strip()))


def snapshot_section_version(
    sec: Optional[SectionState],
    source: str = "generation",
    note: str = "",
    max_versions: int = 20,
) -> Optional[SectionVersionEntry]:
    """Archive current non-empty section text into sec.versions if changed since last snapshot."""
    if sec is None:
        return None
    md = (sec.content_markdown or "").strip()
    if not md:
        return None
    if sec.versions and (sec.versions[-1].content_markdown or "").strip() == md:
        return None

    entry = SectionVersionEntry(
        source=source or "generation",
        note=(note or "")[:240],
        content_markdown=sec.content_markdown,
        cited_ref_ids=list(sec.cited_ref_ids or []),
        word_count=_count_words(md),
    )
    sec.versions.append(entry)
    if len(sec.versions) > max_versions:
        sec.versions = sec.versions[-max_versions:]
    return entry


def restore_section_version(
    project: ProtocolProject,
    section_id: str,
    version_id: str,
) -> SectionVersionEntry:
    """Restore an archived section version safely (snapshotting current text first)."""
    sec = project.sections.get(section_id)
    if sec is None:
        raise ValueError(f"Section introuvable : {section_id}")
    target = next((v for v in (sec.versions or []) if v.version_id == version_id), None)
    if target is None:
        raise ValueError(f"Version introuvable : {version_id}")

    snapshot_section_version(
        sec,
        source="restore",
        note=f"Avant restauration de la version {version_id}",
    )
    sec.content_markdown = target.content_markdown
    sec.content_html = markdown_to_clean_html(target.content_markdown)
    sec.cited_ref_ids = list(target.cited_ref_ids or [])
    now = datetime.now(timezone.utc)
    sec.updated_at = now
    project.updated_at = now
    return target


def format_supervisor_directives_block(project: ProtocolProject) -> str:
    """Render active project-wide supervisor directives as a top-priority prompt block."""
    directives = [
        d for d in (getattr(project, "supervisor_directives", None) or [])
        if getattr(d, "active", True) and (getattr(d, "text", "") or "").strip()
    ]
    if not directives:
        return ""
    lang = normalize_lang(getattr(project, "language", "fr"))
    lines = [f"- {d.text.strip()}" for d in directives]
    if lang == "en":
        return (
            "GLOBAL SUPERVISOR DIRECTIVES (TOP PRIORITY — OVERRIDE GENERIC STYLE DEFAULTS):\n"
            + "\n".join(lines)
            + "\nApply all active supervisor directives consistently in this section while never inventing fake numbers or citations."
        )
    return (
        "DIRECTIVES GLOBALES DE L'ENCADREUR (PRIORITÉ ABSOLUE — PRIMENT SUR LES RÈGLES GÉNÉRIQUES) :\n"
        + "\n".join(lines)
        + "\nApplique rigoureusement ces consignes de l'encadreur dans cette section tout en n'inventant jamais de statistiques ni de références fictives."
    )


class CopilotTurnOutput(BaseModel):
    intent: str = Field(
        default="answer",
        description="One of: answer | propose_edit | suggest_directive | restore_version | navigate_module",
    )
    reply: str = Field(
        ...,
        description="Clear, helpful pedagogical reply in the user's language (French or English).",
    )
    proposed_markdown: Optional[str] = Field(
        default=None,
        description="Complete proposed section Markdown ONLY when intent=='propose_edit'. Null otherwise.",
    )
    edit_summary: str = Field(
        default="",
        description="Short summary of improvements when proposed_markdown is returned.",
    )
    suggested_directive: Optional[str] = Field(
        default=None,
        description="Concise project-wide directive text ONLY when intent=='suggest_directive'.",
    )
    restore_version_id: Optional[str] = Field(
        default=None,
        description="Matching version_id from recent section versions ONLY when intent=='restore_version'.",
    )
    target_module: Optional[str] = Field(
        default=None,
        description="One of module1 | module2 | module3 | module4 ONLY when intent=='navigate_module'.",
    )
    tool_call: str = Field(
        default="none",
        description="Optional internal tool to execute: none | inspect_internal_library | draft_compare_and_merge | patch_section_text | run_section_audit | search_protocol_versions",
    )
    tool_query: str = Field(
        default="",
        description="Optional search query or drafting/patching instruction for tool_call.",
    )


_VALID_INTENTS = frozenset(
    {"answer", "propose_edit", "suggest_directive", "restore_version", "navigate_module"}
)
_VALID_MODULES = frozenset({"module1", "module2", "module3", "module4"})
_VALID_TOOLS = frozenset(
    {
        "none",
        "inspect_internal_library",
        "draft_compare_and_merge",
        "patch_section_text",
        "run_section_audit",
        "search_protocol_versions",
    }
)


def _build_section_context(
    project: ProtocolProject,
    active_section_id: Optional[str],
    lang: str,
) -> Dict[str, str]:
    if not active_section_id or active_section_id not in project.sections:
        return {
            "section_title": "Page de garde / Vue globale" if lang != "en" else "Cover page / Global view",
            "section_md": "",
            "refs_text": "",
            "lib_text": "",
            "versions_text": "",
        }

    sec = project.sections[active_section_id]
    canvas = get_localized_canvas(lang)
    spec = next((s for s in canvas if s.id == active_section_id), None)
    sec_title = f"{spec.chapter_title} — {spec.title}" if spec else active_section_id

    ref_by_id = {r.ref_id: r for r in (project.references or [])}
    cited_lines: List[str] = []
    seen_ref_ids: set[str] = set()
    for rid in sec.cited_ref_ids or []:
        r = ref_by_id.get(rid)
        if r and rid not in seen_ref_ids:
            seen_ref_ids.add(rid)
            idx_lbl = f"[{r.order_index}]" if r.order_index else f"[{rid}]"
            cited_lines.append(f"{idx_lbl} {r.vancouver_citation}")

    # Also resolve numeric [n] citations present directly inside section text
    nums_in_md = re.findall(r"\[(\d+)\]", sec.content_markdown or "")
    ref_by_idx = {str(r.order_index): r for r in (project.references or []) if r.order_index}
    for num_str in nums_in_md:
        r = ref_by_idx.get(num_str)
        if r and r.ref_id not in seen_ref_ids:
            seen_ref_ids.add(r.ref_id)
            cited_lines.append(f"[{r.order_index}] {r.vancouver_citation}")

    if not cited_lines and project.references:
        for r in project.references[:8]:
            idx_lbl = f"[{r.order_index}]" if r.order_index else f"[{r.ref_id}]"
            cited_lines.append(f"{idx_lbl} {r.vancouver_citation}")

    lib_lines: List[str] = []
    for entry in (sec.internal_library or [])[:5]:
        cite_tag = entry.vancouver_citation or entry.source_kind
        excerpt = re.sub(r"\s+", " ", (entry.content_markdown or "").strip())[:380]
        lib_lines.append(
            f"- {entry.source_filename} ({entry.source_kind}, citable={entry.citable}, {cite_tag}): {excerpt}"
        )

    ver_lines: List[str] = []
    for v in (sec.versions or [])[-6:]:
        preview = re.sub(r"\s+", " ", (v.content_markdown or "").strip())[:140]
        ver_lines.append(
            f"- version_id={v.version_id} | {v.created_at.strftime('%d/%m %H:%M')} | source={v.source} | {v.word_count} mots | note={v.note or '—'} | extrait=\"{preview}\""
        )

    return {
        "section_title": sec_title,
        "section_md": (sec.content_markdown or "")[:6500],
        "refs_text": "\n".join(cited_lines[:15]),
        "lib_text": "\n".join(lib_lines),
        "versions_text": "\n".join(ver_lines),
    }


def _message_belongs_to_user(
    msg: CopilotMessage,
    project: ProtocolProject,
    user_id: str,
) -> bool:
    uid = (user_id or "").strip()
    if not uid:
        return False
    msg_owner = (getattr(msg, "owner_user_id", None) or "").strip()
    if msg_owner:
        return msg_owner == uid
    # Legacy messages created before per-user tagging belong strictly to the protocol owner.
    proj_owner = (getattr(project, "owner_user_id", None) or "").strip()
    return bool(proj_owner and proj_owner == uid)


def get_user_copilot_messages(
    project: ProtocolProject,
    user_id: Optional[str],
) -> List[CopilotMessage]:
    """Return strictly the assistant conversation messages belonging to user_id."""
    uid = (user_id or "").strip()
    if not uid:
        return []
    return [
        m
        for m in (getattr(project, "copilot_messages", None) or [])
        if _message_belongs_to_user(m, project, uid)
    ]


def scope_project_copilot_for_user(
    project: ProtocolProject,
    user_id: Optional[str],
) -> ProtocolProject:
    """Return a non-mutating copy of project containing only user_id's copilot messages."""
    return project.model_copy(
        update={"copilot_messages": get_user_copilot_messages(project, user_id)}
    )


def append_scoped_copilot_messages(
    project: ProtocolProject,
    new_messages: List[CopilotMessage],
    user_id: Optional[str],
    max_per_user: int = 40,
) -> None:
    """Append tagged messages for user_id while preserving and isolating all other users' chats."""
    uid = (user_id or "").strip() or (getattr(project, "owner_user_id", None) or "").strip() or None
    existing = list(getattr(project, "copilot_messages", None) or [])
    if uid:
        others = [m for m in existing if not _message_belongs_to_user(m, project, uid)]
        mine = [m for m in existing if _message_belongs_to_user(m, project, uid)]
    else:
        others = existing
        mine = []
    for m in new_messages:
        m.owner_user_id = uid
    mine.extend(new_messages)
    if len(mine) > max_per_user:
        mine = mine[-max_per_user:]
    project.copilot_messages = others + mine


def clear_user_copilot_messages(
    project: ProtocolProject,
    user_id: Optional[str],
) -> None:
    """Remove only user_id's copilot messages without affecting any other user's messages."""
    uid = (user_id or "").strip()
    if not uid:
        return
    project.copilot_messages = [
        m
        for m in (getattr(project, "copilot_messages", None) or [])
        if not _message_belongs_to_user(m, project, uid)
    ]


def run_copilot_chat(
    project: ProtocolProject,
    message: str,
    active_module: str = "module1",
    active_section_id: Optional[str] = None,
    mode: str = "auto",
    current_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one non-destructive turn of Assistant Smart Research grounded in the active project."""
    clean_msg = (message or "").strip()
    if not clean_msg:
        raise ValueError("Le message ne peut pas être vide.")

    eff_user_id = (
        (current_user_id or "").strip()
        or (getattr(project, "owner_user_id", None) or "").strip()
        or None
    )

    lang = normalize_lang(getattr(project, "language", "fr"))
    mod_clean = active_module if active_module in _VALID_MODULES else "module1"
    sec_ctx = _build_section_context(project, active_section_id, lang)
    directives_block = format_supervisor_directives_block(project)

    user_history = get_user_copilot_messages(project, eff_user_id)
    history_lines: List[str] = []
    for prev in user_history[-6:]:
        who = "Étudiant" if prev.role == "user" else "Assistant Smart Research"
        history_lines.append(f"{who}: {prev.content[:350]}")

    text_chain, _ = gemini_router._resolve_candidates("copilot_text")
    voice_chain = gemini_router.resolve_copilot_voice_models()

    mode_hint = (
        "MODE=QUESTION_ONLY: L'utilisateur pose une question ou demande un avis. "
        "TU DOIS mettre intent='answer' et proposed_markdown=null. Ne propose aucune réécriture."
        if mode == "question"
        else "MODE=EDIT_PROPOSAL: L'utilisateur demande une amélioration/comparaison/fusion de texte pour la section active. "
        "Mets intent='propose_edit', propose le meilleur texte fusionné dans proposed_markdown en conservant les bons passages existants, sans effacer directement la section."
        if mode == "edit"
        else "MODE=AUTO: Analyse précisément l'intention réelle de l'utilisateur sans jamais écraser son texte par erreur."
    )

    prompt = f"""
Tu es **Assistant Smart Research**, le copilote méthodologique et guide intelligent de la plateforme Smart Research (protocoles FMSB / CAMES / CADMEF).
Langue de réponse attendue : {"English" if lang == "en" else "Français"}.

MISSION & GARDE-FOUS NON DESTRUCTIFS :
- {mode_hint}
- Si l'utilisateur pose une QUESTION (ex: d'où vient une définition, quelle source a été utilisée, explication d'une méthode ou d'un bouton de l'application, critique méthodologique) → intent='answer', proposed_markdown=null. NE RÉÉCRIS JAMAIS la section pour une question !
- Si l'utilisateur demande d'améliorer, comparer, fusionner, reformuler ou compléter la section active → intent='propose_edit' et rédige une proposition complète et soignée dans proposed_markdown (ou déclenche l'outil interne `draft_compare_and_merge`). L'utilisateur décidera ensuite s'il clique sur « Appliquer ».
- Si l'utilisateur formule une consigne générale de son directeur/encadreur à appliquer sur tout le protocole → intent='suggest_directive' et fournis la consigne formulée clairement dans suggested_directive.
- Si l'utilisateur demande de retrouver ou restaurer une ancienne version ou un passage supprimé → déclenche `search_protocol_versions` ou identifie le version_id dans VERSIONS RÉCENTES, mets intent='restore_version' et restore_version_id='<id>'.
- Si l'utilisateur demande à ouvrir un autre module (module1=Rédaction & Recherche, module2=Édition Word/PDF, module3=Masque de saisie & Kobo, module4=Diaporama Soutenance PPTX) → intent='navigate_module' et target_module='<moduleX>'.
- Règle stricte anti-hallucination : n'invente jamais une source, un auteur ou un chiffre absent du contexte ci-dessous.

OUTILS INTERNES ORCHESTRABLES (`tool_call`) :
- `none` : réponse directe suffisante avec le contexte ci-dessous.
- `patch_section_text` : **ÉDITION CHIRURGICALE PRÉFÉRÉE** dès que l'étudiant demande de modifier, corriger, préciser, remplacer ou supprimer une phrase, une ligne ou un passage précis sans altérer le reste de la section (retourne uniquement des remplacements ciblés ; tout le reste reste intact au caractère près).
- `inspect_internal_library` : recherche approfondie dans la bibliothèque interne multi-sections, les documents importés et la bibliographie complète (utilise `tool_query`).
- `run_section_audit` : exécute l'audit déterministe CAMES/FMSB (volume de mots, citations, alertes de cohérence) sur la section active sans la modifier.
- `search_protocol_versions` : fouille toutes les versions archivées de la section active pour retrouver un passage ou terme précis (utilise `tool_query`).
- `draft_compare_and_merge` : lance le rédacteur scientifique en arrière-plan sur une copie isolée, compare sa nouvelle génération avec le texte actuel, garde le meilleur des deux et retourne une proposition fusionnée sans écraser la section (uniquement pour une réécriture/comparaison globale, jamais pour une simple question).

CONTEXTE DU PROJET :
- Titre : {project.cover.title or project.name}
- Type d'étude : {project.study_type or 'Non précisé'}
- Idée / Contexte : {project.research_idea or 'Non précisé'}
- Module actif : {mod_clean}
- Section active : {active_section_id or 'aucune'} ({sec_ctx['section_title']})

{directives_block if directives_block else "DIRECTIVES GLOBALES DE L'ENCADREUR : Aucune directive enregistrée pour l'instant."}

CONTENU ACTUEL DE LA SECTION ACTIVE :
{sec_ctx['section_md'] or '(Section vide pour le moment)'}

RÉFÉRENCES BIBLIOGRAPHIQUES ASSOCIÉES :
{sec_ctx['refs_text'] or '(Aucune référence associée pour le moment)'}

BIBLIOTHÈQUE INTERNE DE LA SECTION ACTIVE :
{sec_ctx['lib_text'] or '(Aucune source interne importée dans cette section)'}

VERSIONS RÉCENTES DE LA SECTION (HISTORIQUE RESTAURABLE) :
{sec_ctx['versions_text'] or '(Aucune version archivée pour cette section)'}

HISTORIQUE RÉCENT DE CONVERSATION :
{chr(10).join(history_lines) if history_lines else '(Début de la conversation)'}

DEMANDE ACTUELLE DE L'UTILISATEUR :
{clean_msg}
"""

    model_used = ""
    try:
        res = gemini_router.generate_structured(
            role="copilot_text",
            contents=prompt,
            schema=CopilotTurnOutput,
            temperature=0.3,
        )
        dto: CopilotTurnOutput = res["data"]
        model_used = str(res.get("model_used") or "")
    except Exception as exc:
        fallback_reply = (
            "I could not reach the AI model right now, so your section content was left completely untouched. Please retry in a moment."
            if lang == "en"
            else f"Je n'ai pas pu joindre le modèle IA pour l'instant ({exc}), mais rassurez-vous : le contenu de votre section n'a subi aucune modification. Réessayez dans un instant."
        )
        dto = CopilotTurnOutput(intent="answer", reply=fallback_reply)
        model_used = "copilot-safe-fallback"

    intent = (dto.intent or "answer").strip().lower()
    if intent not in _VALID_INTENTS:
        intent = "answer"
    if mode == "question":
        intent = "answer"

    reply_text = (dto.reply or "").strip()
    proposal_markdown = (dto.proposed_markdown or "").strip()
    edit_summary = (dto.edit_summary or "").strip()
    proposal_patches: List[TextPatchOperation] = []
    restore_vid: Optional[str] = None
    tools_used: List[str] = []

    tool_name = (getattr(dto, "tool_call", "none") or "none").strip().lower()
    tool_q = (getattr(dto, "tool_query", "") or "").strip() or clean_msg
    if tool_name not in _VALID_TOOLS:
        tool_name = "none"
    if mode == "question" and tool_name in ("draft_compare_and_merge", "patch_section_text"):
        tool_name = "none"

    if tool_name != "none":
        try:
            from app.services.copilot_tools import (
                tool_draft_compare_and_merge,
                tool_inspect_internal_library,
                tool_patch_section_text,
                tool_run_section_audit,
                tool_search_protocol_versions,
            )

            if tool_name == "inspect_internal_library":
                t_res = tool_inspect_internal_library(
                    project, query=tool_q, active_section_id=active_section_id
                )
                tools_used.append("inspect_internal_library")
                synth_prompt = (
                    f"Demande étudiant : {clean_msg}\n"
                    f"Résultats de l'outil Bibliothèque Interne :\n{t_res['summary_text']}\n\n"
                    f"Réponds en {'English' if lang == 'en' else 'Français'} de façon claire, pédagogique et fidèle à ces extraits sans rien modifier."
                )
                try:
                    s_res = gemini_router.generate_with_fallback(
                        role="copilot_text", contents=synth_prompt
                    )
                    if str(s_res.get("text") or "").strip():
                        reply_text = str(s_res["text"]).strip()
                        model_used = str(s_res.get("model_used") or model_used)
                except Exception:
                    reply_text = f"{reply_text}\n\n{t_res['summary_text']}".strip()

            elif tool_name == "run_section_audit":
                t_res = tool_run_section_audit(
                    project, active_section_id=active_section_id
                )
                tools_used.append("run_section_audit")
                synth_prompt = (
                    f"Demande étudiant : {clean_msg}\n"
                    f"Résultats de l'outil Audit CAMES/FMSB (lecture seule) :\n{t_res['summary_text']}\n\n"
                    f"Explique clairement à l'étudiant en {'English' if lang == 'en' else 'Français'} ce qui est conforme et ce qui reste à corriger."
                )
                try:
                    s_res = gemini_router.generate_with_fallback(
                        role="copilot_text", contents=synth_prompt
                    )
                    if str(s_res.get("text") or "").strip():
                        reply_text = str(s_res["text"]).strip()
                        model_used = str(s_res.get("model_used") or model_used)
                except Exception:
                    reply_text = f"{reply_text}\n\n{t_res['summary_text']}".strip()

            elif tool_name == "search_protocol_versions" and active_section_id:
                t_res = tool_search_protocol_versions(
                    project, active_section_id=active_section_id, query=tool_q
                )
                tools_used.append("search_protocol_versions")
                best_vid = t_res.get("best_version_id")
                if best_vid:
                    restore_vid = str(best_vid)
                    if mode != "edit":
                        intent = "restore_version"
                    if restore_vid not in reply_text:
                        reply_text = (
                            f"{reply_text}\n\nVersions correspondantes :\n{t_res['summary_text']}"
                        ).strip()

            elif (
                tool_name == "patch_section_text"
                and active_section_id
                and active_section_id in project.sections
            ):
                t_res = tool_patch_section_text(
                    project, section_id=active_section_id, instruction=tool_q
                )
                tools_used.append("patch_section_text")
                raw_patches = t_res.get("patches") or []
                if raw_patches:
                    proposal_patches = [TextPatchOperation(**p) for p in raw_patches]
                    proposal_markdown = str(t_res.get("proposed_markdown") or "").strip()
                    edit_summary = str(t_res.get("summary") or edit_summary).strip()
                    intent = "propose_edit"
                    if t_res.get("model_used"):
                        model_used = str(t_res["model_used"])
                    if edit_summary and edit_summary not in reply_text:
                        reply_text = f"{reply_text}\n\n{edit_summary}".strip()

            elif (
                tool_name == "draft_compare_and_merge"
                and active_section_id
                and active_section_id in project.sections
            ):
                t_res = tool_draft_compare_and_merge(
                    project, section_id=active_section_id, instruction=tool_q
                )
                tools_used.append("draft_compare_and_merge")
                if (t_res.get("proposed_markdown") or "").strip():
                    proposal_markdown = t_res["proposed_markdown"].strip()
                    edit_summary = t_res.get("summary") or edit_summary
                    intent = "propose_edit"
                    if t_res.get("model_used"):
                        model_used = str(t_res["model_used"])
                    if edit_summary and edit_summary not in reply_text:
                        reply_text = f"{reply_text}\n\n{edit_summary}".strip()
        except Exception:
            pass

    proposal_obj: Optional[CopilotProposal] = None
    if (
        intent == "propose_edit"
        and active_section_id
        and active_section_id in project.sections
        and (proposal_markdown or proposal_patches)
    ):
        proposal_obj = CopilotProposal(
            section_id=active_section_id,
            proposed_markdown=proposal_markdown,
            summary=edit_summary,
            patches=proposal_patches,
            applied=False,
        )
    elif intent == "propose_edit":
        intent = "answer"

    suggested_dir = (
        (dto.suggested_directive or "").strip()
        if intent == "suggest_directive" and (dto.suggested_directive or "").strip()
        else None
    )

    if (
        not restore_vid
        and intent == "restore_version"
        and active_section_id
        and active_section_id in project.sections
    ):
        valid_vids = {v.version_id for v in project.sections[active_section_id].versions}
        cand_vid = (dto.restore_version_id or "").strip()
        if cand_vid in valid_vids:
            restore_vid = cand_vid

    target_mod = (
        (dto.target_module or "").strip()
        if intent == "navigate_module" and (dto.target_module or "").strip() in _VALID_MODULES
        else None
    )

    user_msg = CopilotMessage(
        role="user",
        content=clean_msg,
        intent=mode,
        active_module=mod_clean,
        active_section_id=active_section_id,
    )
    assistant_msg = CopilotMessage(
        role="assistant",
        content=reply_text,
        intent=intent,
        model_used=model_used,
        active_module=mod_clean,
        active_section_id=active_section_id,
        proposal=proposal_obj,
        suggested_directive=suggested_dir,
        restore_version_id=restore_vid,
        target_module=target_mod,
        tools_used=tools_used,
    )

    append_scoped_copilot_messages(
        project,
        [user_msg, assistant_msg],
        eff_user_id,
        max_per_user=40,
    )
    project.updated_at = datetime.now(timezone.utc)

    return {
        "assistant_message": assistant_msg,
        "project": project,
        "model_used": model_used,
        "active_text_models": text_chain,
        "active_voice_models": voice_chain,
    }
