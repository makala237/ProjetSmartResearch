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


_VALID_INTENTS = frozenset(
    {"answer", "propose_edit", "suggest_directive", "restore_version", "navigate_module"}
)
_VALID_MODULES = frozenset({"module1", "module2", "module3", "module4"})


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


def run_copilot_chat(
    project: ProtocolProject,
    message: str,
    active_module: str = "module1",
    active_section_id: Optional[str] = None,
    mode: str = "auto",
) -> Dict[str, Any]:
    """Run one non-destructive turn of Assistant Smart Research grounded in the active project."""
    clean_msg = (message or "").strip()
    if not clean_msg:
        raise ValueError("Le message ne peut pas être vide.")

    lang = normalize_lang(getattr(project, "language", "fr"))
    mod_clean = active_module if active_module in _VALID_MODULES else "module1"
    sec_ctx = _build_section_context(project, active_section_id, lang)
    directives_block = format_supervisor_directives_block(project)

    history_lines: List[str] = []
    for prev in (project.copilot_messages or [])[-6:]:
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
- Si l'utilisateur demande d'améliorer, comparer, fusionner, reformuler ou compléter la section active → intent='propose_edit' et rédige une proposition complète et soignée dans proposed_markdown (en préservant les définitions/passages existants de qualité et en respectant les directives de l'encadreur). L'utilisateur décidera ensuite s'il clique sur « Appliquer ».
- Si l'utilisateur formule une consigne générale de son directeur/encadreur à appliquer sur tout le protocole → intent='suggest_directive' et fournis la consigne formulée clairement dans suggested_directive.
- Si l'utilisateur demande de retrouver ou restaurer une ancienne version ou un passage supprimé → identifie le version_id le plus pertinent dans VERSIONS RÉCENTES DE LA SECTION, mets intent='restore_version' et restore_version_id='<id>'.
- Si l'utilisateur demande à ouvrir un autre module (module1=Rédaction & Recherche, module2=Édition Word/PDF, module3=Masque de saisie & Kobo, module4=Diaporama Soutenance PPTX) → intent='navigate_module' et target_module='<moduleX>'.
- Règle stricte anti-hallucination : n'invente jamais une source, un auteur ou un chiffre absent du contexte ci-dessous.

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

    proposal_obj: Optional[CopilotProposal] = None
    if (
        intent == "propose_edit"
        and active_section_id
        and active_section_id in project.sections
        and (dto.proposed_markdown or "").strip()
    ):
        proposal_obj = CopilotProposal(
            section_id=active_section_id,
            proposed_markdown=dto.proposed_markdown.strip(),
            summary=(dto.edit_summary or "").strip(),
            applied=False,
        )
    elif intent == "propose_edit":
        intent = "answer"

    suggested_dir = (
        (dto.suggested_directive or "").strip()
        if intent == "suggest_directive" and (dto.suggested_directive or "").strip()
        else None
    )

    restore_vid: Optional[str] = None
    if intent == "restore_version" and active_section_id and active_section_id in project.sections:
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
        content=(dto.reply or "").strip(),
        intent=intent,
        model_used=model_used,
        active_module=mod_clean,
        active_section_id=active_section_id,
        proposal=proposal_obj,
        suggested_directive=suggested_dir,
        restore_version_id=restore_vid,
        target_module=target_mod,
    )

    project.copilot_messages.extend([user_msg, assistant_msg])
    if len(project.copilot_messages) > 40:
        project.copilot_messages = project.copilot_messages[-40:]
    project.updated_at = datetime.now(timezone.utc)

    return {
        "assistant_message": assistant_msg,
        "project": project,
        "model_used": model_used,
        "active_text_models": text_chain,
        "active_voice_models": voice_chain,
    }
