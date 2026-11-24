from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.data.i18n import get_localized_canvas, normalize_lang
from app.schemas.protocol import (
    CopilotAppAction,
    CopilotMessage,
    CopilotProposal,
    ProtocolProject,
    SectionState,
    SectionVersionEntry,
    TextPatchOperation,
)
from app.services.copilot_actions import VALID_COPILOT_ACTION_TYPES
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
        description="One of: answer | propose_edit | propose_app_action | suggest_directive | restore_version | navigate_module",
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
    action_type: Optional[str] = Field(
        default=None,
        description="Target app action name ONLY when intent=='propose_app_action'. Null otherwise.",
    )
    action_label: str = Field(
        default="",
        description="Short button/card title for the proposed app action (e.g. 'Enregistrer la directive méthodologique').",
    )
    action_summary: str = Field(
        default="",
        description="Clear explanation of what executing the proposed app action will change.",
    )
    action_params_json: str = Field(
        default="",
        description="Valid JSON object string of parameters ONLY when intent=='propose_app_action'. Leave empty string otherwise.",
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
    {
        "answer",
        "propose_edit",
        "propose_app_action",
        "suggest_directive",
        "restore_version",
        "navigate_module",
    }
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


def _build_app_state_summary(project: ProtocolProject, lang: str) -> str:
    cov = project.cover
    fmt = project.formatting
    hosts = ", ".join(cov.host_institutions or []) if cov.host_institutions else ("Aucun" if lang != "en" else "None")
    period = cov.study_period_label or ("Non précisée" if lang != "en" else "Not specified")
    tl_rows = len(getattr(project.timeline_grid, "rows", None) or [])
    return (
        f"Sites d'étude (page de garde) : {hosts} | Période : {period}\n"
        f"Mise en forme (Module 2) : police={fmt.font_family}, taille={fmt.body_font_size_pt}pt, "
        f"interligne={fmt.line_spacing}, alignement={fmt.text_align}, marge={fmt.margin_cm}cm\n"
        f"Données structurées : {len(project.abbreviations or [])} abréviation(s), "
        f"{len(project.variables or [])} variable(s), {len(project.budget or [])} ligne(s) budget, "
        f"{tl_rows} tâche(s) chronogramme, {len(project.crf_fields or [])} champ(s) CRF (Module 3), "
        f"{len(project.references or [])} référence(s)."
    )


def _build_section_context(
    project: ProtocolProject,
    active_section_id: Optional[str],
    lang: str,
) -> Dict[str, str]:
    app_state_summary = _build_app_state_summary(project, lang)
    if not active_section_id or active_section_id not in project.sections:
        return {
            "section_title": "Page de garde / Vue globale" if lang != "en" else "Cover page / Global view",
            "section_md": "",
            "refs_text": "",
            "lib_text": "",
            "versions_text": "",
            "audit_notes_text": "",
            "app_state_summary": app_state_summary,
        }

    sec = project.sections[active_section_id]
    canvas = get_localized_canvas(lang)
    spec = next((s for s in canvas if s.id == active_section_id), None)
    sec_title = f"{spec.chapter_title} — {spec.title}" if spec else active_section_id

    sec_status = sec.status.value if hasattr(sec.status, "value") else str(sec.status)
    audit_lines = [f"Statut section : {sec_status}"]
    if sec.audit_notes:
        audit_lines.append("Remarques d'audit actuelles :")
        audit_lines.extend(f"- {note}" for note in sec.audit_notes[:10])
    else:
        audit_lines.append("Remarques d'audit actuelles : Aucune.")
    waivers = getattr(sec, "audit_waivers", None) or []
    if waivers:
        audit_lines.append("Dérogations tracées actives :")
        audit_lines.extend(
            f"- [{w.issue_text}] Justification : {w.justification}" for w in waivers[:5]
        )

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
        "audit_notes_text": "\n".join(audit_lines),
        "app_state_summary": app_state_summary,
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


def _parse_action_params_json(raw: str) -> Dict[str, Any]:
    txt = (raw or "").strip()
    if not txt:
        return {}
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        parsed = json.loads(txt)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        start = txt.find("{")
        end = txt.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(txt[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}


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
        "TU DOIS mettre intent='answer' et proposed_markdown=null. Ne propose aucune réécriture ni action mutante."
        if mode == "question"
        else "MODE=EDIT_PROPOSAL: L'utilisateur demande une amélioration/comparaison/fusion de texte pour la section active. "
        "Mets intent='propose_edit', propose le meilleur texte fusionné dans proposed_markdown en conservant les bons passages existants, sans effacer directement la section."
        if mode == "edit"
        else "MODE=AUTO: Analyse précisément l'intention réelle de l'utilisateur sans jamais écraser son texte ou ses données sans confirmation."
    )

    prompt = f"""
Tu es **Assistant Smart Research**, le copilote méthodologique et opérateur universel de la plateforme Smart Research (protocoles FMSB / CAMES / CADMEF).
Langue de réponse attendue : {"English" if lang == "en" else "Français"}.

MISSION & GARDE-FOUS NON DESTRUCTIFS :
- {mode_hint}
- Si l'utilisateur pose une QUESTION → intent='answer', proposed_markdown=null. NE RÉÉCRIS JAMAIS la section pour une question !
- Si l'utilisateur demande d'améliorer, comparer, fusionner, reformuler ou corriger une phrase/section → intent='propose_edit' (via `patch_section_text` si correction ciblée, ou `proposed_markdown` / `draft_compare_and_merge`).
- Si l'utilisateur demande de modifier un outil ou paramètre de l'application hors texte simple → utilise `intent='propose_app_action'` avec `action_type`, `action_label`, `action_summary` et `action_params_json` (JSON valide). L'utilisateur confirmera par un clic sur « Exécuter cette action ».
- Si l'utilisateur demande de retrouver ou restaurer une ancienne version → `search_protocol_versions` / `intent='restore_version'`.
- Si l'utilisateur demande d'ouvrir un autre module (module1..module4) → `intent='navigate_module'` et `target_module`.
- Règle stricte anti-hallucination : n'invente jamais une source, un auteur ou un chiffre absent du contexte ci-dessous.

RÉSOLUTION DES BLOCAGES D'AUDIT CAMES EN 3 NIVEAUX (TRÈS IMPORTANT) :
- Si l'utilisateur signale que l'audit insiste sur un point malgré sa saisie (ex. sites d'étude comme « tous les établissements pénitentiaires de la ville », taille d'échantillon ou anonymisation) :
  - **Niveau 1 (Diagnostic + Reformulation académique)** : explique la remarque d'audit exacte présente dans ÉTAT D'AUDIT ci-dessous, propose soit de reformuler le passage de façon rigoureuse avec `patch_section_text` (ex. qualifier le caractère multicentrique exhaustif et nommer ou motiver la confidentialité des sites), soit de guider l'étudiant sur ce qui manque.
  - **Niveau 2 (Directive respectée par l'audit)** : si l'utilisateur confirme que ce choix méthodologique est volontaire (ex. « Je veux garder tous les établissements pénitentiaires sans les nommer individuellement »), propose `intent='propose_app_action'` avec `action_type='add_directive'` et `action_params_json='{{"text": "..."}}'` pour que l'audit CAMES tienne compte durablement de cette contrainte lors des prochains audits.
  - **Niveau 3 (Dérogation explicite et tracée)** : si l'utilisateur demande explicitement de lever l'alerte/valider quand même pour un faux positif persistant, propose `intent='propose_app_action'` avec `action_type='waive_audit_and_validate'` et `action_params_json='{{"section_id": "{active_section_id or ''}", "issue_text": "*", "justification": "...", "also_add_directive": "..."}}'`.

CATALOGUE DES ACTIONS APPLICATIVES DISPONIBLES (`intent='propose_app_action'`) :
- `add_directive` : enregistrer une directive méthodologique respectée par l'audit et la rédaction. Params: `{{"text": "..."}}`.
- `waive_audit_and_validate` : lever une remarque d'audit avec justification tracée et valider la section. Params: `{{"section_id": "...", "issue_text": "* ou texte exact", "justification": "...", "also_add_directive": "optionnel"}}`.
- `run_ai_audit` : relancer l'audit IA CAMES complet de la section. Params: `{{"section_id": "..."}}`.
- `fix_section_issues` : lancer le correcteur automatique IA selon les notes d'audit. Params: `{{"section_id": "..."}}`.
- `humanize_section` : humaniser le style académique de la section. Params: `{{"section_id": "...", "use_ai": true}}`.
- `update_project_meta` : modifier `study_type`, `research_idea`, ou `language` (`"fr"`/`"en"`).
- `update_cover` (**RÉSERVÉ AUX ADMINISTRATEURS** : ne jamais proposer `update_cover` à un étudiant ; pour un étudiant, modifier la section méthodologique ou ajouter une directive). Params (admin) : `host_institutions`, `study_period_label`, `ethics_committees`, `director`, `department_fr`, etc.
- `generate_sub_annex` : générer/actualiser une sous-annexe du Chapitre V (`annexe_notice_information`, etc.). Params: `{{"section_id": "...", "user_instruction": "...", "bilingual": false}}`.
- `update_formatting` (Module 2) : modifier `font_family`, `body_font_size_pt`, `line_spacing`, `text_align` (`justify`|`left`), `margin_cm`, `auto_toc`, etc.
- `update_structured_data` (Module 2) : mettre à jour `add_abbreviations` (`[{{"acronym": "...", "full_form": "..."}}]`), `add_variables`, `add_budget_items` (`[{{"category": "...", "designation": "...", "quantity": 1, "unit_cost_fcfa": 5000}}]`), `timeline_grid`, `annexes_config`.
- `sync_structured` (Module 2) : resynchroniser abréviations, variables et budget depuis le texte rédigé. Params: `{{}}`.
- `generate_toc_and_lists` (Module 2) : régénérer sommaire, liste des tableaux/figures/abréviations. Params: `{{}}`.
- `validate_references` (Module 2) : valider et normaliser les références Vancouver ICMJE. Params: `{{}}`.
- `update_crf_fields` (Module 3) : modifier le masque de saisie et l'Annexe 1 (`add_fields`: `[{{"code": "...", "label": "...", "field_type": "text|number|date|single_choice|multi_choice|dichotomous", "options": []}}]`, `remove_field_codes`: `[...]`).
- `update_presentation_slide` (Module 4) : modifier `slide_index`, `title`, `subtitle`, `bullets`, `presenter_notes`.
- `generate_presentation_slide` (Module 4) : régénérer une diapositive par IA (`slide_index`, `instruction`).

OUTILS INTERNES DE LECTURE / PRÉPARATION (`tool_call`) :
- `none` : réponse directe suffisante avec le contexte ci-dessous.
- `patch_section_text` : **ÉDITION CHIRURGICALE PRÉFÉRÉE** dès que l'étudiant demande de modifier, corriger, préciser, remplacer ou supprimer une phrase, une ligne ou un passage précis sans altérer le reste de la section.
- `inspect_internal_library` : recherche approfondie dans la bibliothèque interne multi-sections, les documents importés et la bibliographie complète (utilise `tool_query`).
- `run_section_audit` : lit les contrôles déterministes FMSB/CAMES, les remarques du dernier audit IA enregistrées et les alertes de cohérence.
- `search_protocol_versions` : fouille toutes les versions archivées de la section active (`tool_query`).
- `draft_compare_and_merge` : comparaison et fusion sur copie isolée pour une réécriture globale.

CONTEXTE DU PROJET :
- Titre : {project.cover.title or project.name}
- Type d'étude : {project.study_type or 'Non précisé'}
- Idée / Contexte : {project.research_idea or 'Non précisé'}
- Module actif : {mod_clean}
- Section active : {active_section_id or 'aucune'} ({sec_ctx['section_title']})

ÉTAT APPLICATIF GLOBAL :
{sec_ctx['app_state_summary']}

ÉTAT D'AUDIT DE LA SECTION ACTIVE :
{sec_ctx['audit_notes_text'] or '(Aucune section active sélectionnée)'}

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

    app_action_obj: Optional[CopilotAppAction] = None
    raw_action_type = (getattr(dto, "action_type", None) or "").strip()
    if (
        intent == "propose_app_action"
        and mode != "question"
        and raw_action_type in VALID_COPILOT_ACTION_TYPES
    ):
        parsed_params = _parse_action_params_json(
            getattr(dto, "action_params_json", "") or ""
        )
        if (
            active_section_id
            and raw_action_type
            in (
                "waive_audit_and_validate",
                "run_ai_audit",
                "fix_section_issues",
                "humanize_section",
                "generate_sub_annex",
            )
            and not parsed_params.get("section_id")
        ):
            parsed_params["section_id"] = active_section_id
        app_action_obj = CopilotAppAction(
            action_type=raw_action_type,
            label=(getattr(dto, "action_label", "") or raw_action_type).strip(),
            summary=(getattr(dto, "action_summary", "") or reply_text[:180]).strip(),
            params=parsed_params,
            applied=False,
        )
    elif intent == "propose_app_action":
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
        app_action=app_action_obj,
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
