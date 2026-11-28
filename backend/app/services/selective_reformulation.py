"""Selective AI reformulation, audit amendment patching, and text-selection transformation.

Every operation is non-destructive by default: it returns a structured preview with
exact changed spans (`patches`) so the frontend can highlight modified/added zones
in color and let the user Validate, Retry, or Cancel before committing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.data.i18n import normalize_lang
from app.schemas.protocol import ProtocolProject, SectionWritingPlan, SectionWritingPlanItem
from app.services.copilot_tools import apply_surgical_text_patches
from app.services.gemini_client import gemini_router
from app.services.humanizer import sanitize_llm_typography
from app.services.pedagogy_service import format_pedagogy_notes_for_section
from app.services.rich_formatter import markdown_to_clean_html


def _clean_text(raw: str) -> str:
    """Safely unpack sanitize_llm_typography() which returns (cleaned_str, count)."""
    if not raw:
        return ""
    res = sanitize_llm_typography(raw)
    if isinstance(res, tuple):
        return str(res[0] or "")
    return str(res or "")


class SelectivePatchItem(BaseModel):
    target_text: str = Field(
        ...,
        description="Exact verbatim substring copied from the original section text to replace.",
    )
    replacement_text: str = Field(
        ...,
        description="Improved/corrected replacement for ONLY target_text.",
    )
    reason: str = Field(
        default="",
        description="Short explanation of why this specific passage was modified.",
    )


class SmartReformulateOutput(BaseModel):
    strategy: Literal["selective_patch", "append_complement", "full_rewrite"] = Field(
        default="selective_patch",
        description=(
            "'selective_patch' to modify ONLY specific passages while keeping everything else untouched; "
            "'append_complement' to keep 100% of the existing text intact and add complementary paragraph(s); "
            "'full_rewrite' ONLY when explicitly requested or when the original text is empty."
        ),
    )
    patches: List[SelectivePatchItem] = Field(
        default_factory=list,
        description="Targeted verbatim replacements when strategy is 'selective_patch'.",
    )
    append_markdown: str = Field(
        default="",
        description="Complementary paragraph(s) to append when strategy is 'append_complement'.",
    )
    full_markdown: str = Field(
        default="",
        description="Complete section text ONLY when strategy is 'full_rewrite'.",
    )
    summary: str = Field(
        default="",
        description="Concise explanation of what was modified vs preserved.",
    )


class SelectionTransformOutput(BaseModel):
    replacement_text: str = Field(
        ...,
        description="The transformed passage replacing the user's highlighted selection (or the original selection followed by the complement when action='complement').",
    )
    summary: str = Field(
        default="",
        description="Short 1-sentence summary of the transformation applied to the selection.",
    )


class _MatchedPlanItemOut(BaseModel):
    item_id: str = ""


def identify_plan_item_for_selection(
    plan: Optional[SectionWritingPlan],
    selected_text: str,
    use_llm_fallback: bool = True,
) -> Optional[SectionWritingPlanItem]:
    """Module 4 helper: map a highlighted selection to its exact SectionWritingPlanItem."""
    if plan is None or not plan.items:
        return None
    sel = (selected_text or "").strip()
    if not sel:
        return None
    if len(plan.items) == 1:
        return plan.items[0]

    sel_tokens = set(re.findall(r"[a-zà-ÿ]{4,}", sel.lower()))
    best_item: Optional[SectionWritingPlanItem] = None
    best_score = -1.0
    second_score = -1.0

    for it in plan.items:
        ref_txt = " ".join(
            [it.title, it.objective, *it.required_elements, *it.pedagogical_rules]
        ).lower()
        it_tokens = set(re.findall(r"[a-zà-ÿ]{4,}", ref_txt))
        overlap = len(sel_tokens & it_tokens)
        score = overlap / max(1.0, len(it_tokens) ** 0.5) if sel_tokens and it_tokens else 0.0
        if score > best_score:
            second_score = best_score
            best_score = score
            best_item = it
        elif score > second_score:
            second_score = score

    # Clear lexical match found
    if best_item is not None and best_score >= 0.45 and (best_score - second_score) >= 0.15:
        return best_item

    if use_llm_fallback:
        try:
            catalog = "\n".join(
                f"- {it.item_id}: {it.title} ({it.objective})" for it in plan.items
            )
            prompt = (
                "Identifie uniquement `item_id` correspondant le mieux au passage sélectionné.\n"
                f"OPTIONS DU PLAN :\n{catalog}\n\n"
                f"PASSAGE SÉLECTIONNÉ :\n{sel[:1200]}"
            )
            res = gemini_router.generate_structured(
                role="reasoning",
                contents=prompt,
                schema=_MatchedPlanItemOut,
                temperature=0.0,
            )
            chosen_id = (res["data"].item_id or "").strip()
            for it in plan.items:
                if it.item_id == chosen_id:
                    return it
        except Exception:
            pass

    return best_item or plan.items[0]


def _format_plan_item_directive(
    section_id: str,
    plan: Optional[SectionWritingPlan],
    matched_item: Optional[SectionWritingPlanItem],
) -> str:
    if matched_item is not None:
        lines = [
            f"BLOC DU PLAN DE RÉDACTION CONCERNÉ ([{matched_item.item_id}] {matched_item.title}) :",
            f"- Objectif du bloc : {matched_item.objective}" if matched_item.objective else "",
            ("- Éléments obligatoires à respecter : " + " ; ".join(matched_item.required_elements)) if matched_item.required_elements else "",
            ("- Règles pédagogiques (cours) : " + " ; ".join(matched_item.pedagogical_rules)) if matched_item.pedagogical_rules else "",
        ]
        return "\n".join(ln for ln in lines if ln)
    if plan is not None and plan.items:
        return "PLAN DE RÉDACTION DE LA SECTION :\n" + "\n".join(
            f"- [{it.item_id}] {it.title} : {' ; '.join(it.required_elements[:3])}" for it in plan.items
        )
    ped = format_pedagogy_notes_for_section(section_id, max_chars=2200)
    if ped:
        return f"RAPPELS PÉDAGOGIQUES (NOTES DE COURS) :\n{ped}"
    return ""


def _format_refs_context(project: ProtocolProject, limit: int = 18) -> str:
    lines: List[str] = []
    for r in (project.references or [])[:limit]:
        if r.order_index:
            lines.append(f"[{r.order_index}] {r.vancouver_citation}")
    return "\n".join(lines) or "(Aucune référence enregistrée)"


def smart_reformulate_section(
    project: ProtocolProject,
    section_id: str,
    instruction: str,
    current_markdown: Optional[str] = None,
    mode: str = "auto",
    audit_issues: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Analyze existing section text and return a non-destructive selective reformulation preview."""
    if section_id not in project.sections:
        raise ValueError(f"Section introuvable : {section_id}")

    from app.services.copilot_service import format_supervisor_directives_block

    sec = project.sections[section_id]
    lang = normalize_lang(getattr(project, "language", "fr"))
    original_md = (
        current_markdown
        if current_markdown is not None and current_markdown.strip()
        else (sec.content_markdown or "")
    ).strip()

    directives_block = format_supervisor_directives_block(project, section_id=section_id)
    plan_directive = _format_plan_item_directive(
        section_id,
        getattr(sec, "writing_plan", None),
        identify_plan_item_for_selection(getattr(sec, "writing_plan", None), instruction or original_md[:800], use_llm_fallback=False),
    )
    refs_ctx = _format_refs_context(project)
    issues = [i.strip() for i in (audit_issues or []) if i and i.strip()]
    if mode == "audit_fix" and not issues and sec.audit_notes:
        issues = [n.strip() for n in sec.audit_notes if n and n.strip()]

    issues_block = ""
    if issues:
        issues_block = "AMENDEMENTS / POINTS D'AUDIT À CORRIGER :\n" + "\n".join(
            f"- {item}" for item in issues
        )

    mode_guidance = {
        "audit_fix": (
            "MODE CORRECTION D'AUDIT CHIRURGICALE : Corrige UNIQUEMENT les passages concernés par les "
            "amendements d'audit ci-dessous. Conserve INTACT tout le reste du texte (en particulier les "
            "notes et modifications manuelles de l'étudiant). Utilise 'selective_patch' en priorité, ou "
            "'append_complement' si l'audit signale un élément manquant à ajouter."
        ),
        "complement": (
            "MODE COMPLÉMENT D'INFORMATION : Laisse le texte existant 100 % INTACT (strategy='append_complement') "
            "et rédige uniquement le(s) paragraphe(s) complémentaire(s) dans `append_markdown`."
        ),
    }.get(
        mode,
        (
            "MODE REFORMULATION INTELLIGENTE SÉLECTIVE : Analyse le texte existant et la demande.\n"
            "- Si seule une partie/un paragraphe/un aspect est concerné → choisis `strategy='selective_patch'` "
            "et fournis UNIQUEMENT les `patches` exacts (`target_text` copié mot pour mot → `replacement_text`).\n"
            "- Si l'utilisateur demande d'ajouter/compléter une information sans toucher à l'existant → choisis "
            "`strategy='append_complement'` et remplis `append_markdown`.\n"
            "- Ne choisis `strategy='full_rewrite'` QUE si l'utilisateur demande explicitement de tout réécrire "
            "ou si la section est vide."
        ),
    )

    prompt = f"""
Tu es un directeur de thèse et éditeur scientifique senior (FMSB / CAMES).
{mode_guidance}

RÈGLES STRICTES :
1. Pour `strategy='selective_patch'`, chaque `target_text` DOIT être copié MOT POUR MOT depuis le TEXTE ACTUEL ci-dessous afin que le patch s'applique de manière déterministe sans altérer le reste.
2. Préserve scrupuleusement le style académique médical, la cohérence méthodologique et les citations Vancouver `[n]` existantes. N'invente jamais de références hors de la liste ci-dessous.
3. Langue de rédaction obligatoire : {"English" if lang == "en" else "Français"}.

SECTION : {section_id}
DEMANDE DE L'UTILISATEUR : {(instruction or '').strip() or 'Améliorer et corriger sélectivement les passages nécessaires.'}

{issues_block}
{directives_block if directives_block else ''}
{plan_directive if plan_directive else ''}

TEXTE ACTUEL DE LA SECTION :
{original_md or '(Section vide)'}

RÉFÉRENCES BIBLIOGRAPHIQUES DU PROJET :
{refs_ctx}
"""

    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=SmartReformulateOutput,
        temperature=0.25,
    )
    dto: SmartReformulateOutput = res["data"]
    model_used = str(res.get("model_used") or "")
    strategy = dto.strategy
    summary = _clean_text((dto.summary or "").strip())

    applied_patches: List[Dict[str, Any]] = []
    append_md = ""
    proposed_md = original_md

    if strategy == "append_complement" and (dto.append_markdown or "").strip():
        append_md = _clean_text(dto.append_markdown.strip())
        proposed_md = f"{original_md}\n\n{append_md}".strip() if original_md else append_md
        applied_patches = [
            {
                "target_text": "",
                "replacement_text": append_md,
                "reason": "Complément ajouté (texte existant conservé intact)"
                if lang != "en"
                else "Complement added (existing text kept intact)",
            }
        ]
    elif strategy == "selective_patch" and dto.patches and original_md:
        raw_patches = [
            {
                "target_text": p.target_text,
                "replacement_text": _clean_text(p.replacement_text),
                "reason": _clean_text(p.reason),
            }
            for p in dto.patches
        ]
        patch_res = apply_surgical_text_patches(original_md, raw_patches)
        applied_patches = patch_res["applied_patches"]
        proposed_md = patch_res["patched_markdown"]

        # Graceful fallback if model returned append_markdown or full_markdown when no patch matched verbatim
        if patch_res["applied_count"] == 0:
            if (dto.append_markdown or "").strip():
                strategy = "append_complement"
                append_md = _clean_text(dto.append_markdown.strip())
                proposed_md = f"{original_md}\n\n{append_md}".strip()
                applied_patches = [
                    {
                        "target_text": "",
                        "replacement_text": append_md,
                        "reason": "Complément ajouté" if lang != "en" else "Complement added",
                    }
                ]
            elif (dto.full_markdown or "").strip():
                strategy = "full_rewrite"
                proposed_md = _clean_text(dto.full_markdown.strip())
    else:
        candidate_full = _clean_text((dto.full_markdown or dto.append_markdown or "").strip())
        if candidate_full:
            strategy = "full_rewrite"
            proposed_md = candidate_full
            if original_md:
                applied_patches = [
                    {
                        "target_text": original_md,
                        "replacement_text": proposed_md,
                        "reason": "Réécriture complète demandée"
                        if lang != "en"
                        else "Full rewrite requested",
                    }
                ]
        elif dto.patches and original_md:
            patch_res = apply_surgical_text_patches(original_md, [p.model_dump() for p in dto.patches])
            strategy = "selective_patch"
            applied_patches = patch_res["applied_patches"]
            proposed_md = patch_res["patched_markdown"]

    if not summary:
        if strategy == "append_complement":
            summary = (
                "Le texte initial a été conservé intact et complété."
                if lang != "en"
                else "Existing text was kept intact and complemented."
            )
        elif strategy == "selective_patch":
            summary = (
                f"{len(applied_patches)} zone(s) reformulée(s) sélectivement ; le reste du texte est inchangé."
                if lang != "en"
                else f"{len(applied_patches)} passage(s) selectively reformulated; the rest stays untouched."
            )
        else:
            summary = (
                "Réécriture complète proposée pour validation."
                if lang != "en"
                else "Full rewrite proposed for review."
            )

    # Internal Supervisor Alignment Gate before returning final preview
    from app.services.supervisor_media import align_text_with_supervisor_directives

    sup_align = align_text_with_supervisor_directives(project, section_id, proposed_md)
    if sup_align.get("adjusted") and (sup_align.get("content_markdown") or "").strip():
        proposed_md = sup_align["content_markdown"]
        if sup_align.get("summary"):
            summary = f"{summary} ({sup_align['summary']})" if summary else sup_align["summary"]

    return {
        "section_id": section_id,
        "strategy": strategy,
        "summary": summary,
        "original_markdown": original_md,
        "proposed_markdown": proposed_md,
        "proposed_html": markdown_to_clean_html(proposed_md),
        "patches": applied_patches,
        "append_markdown": append_md,
        "model_used": model_used,
    }


def transform_selected_text(
    project: ProtocolProject,
    section_id: str,
    selected_text: str,
    action: str = "reformulate",
    instruction: str = "",
    current_markdown: Optional[str] = None,
) -> Dict[str, Any]:
    """Transform ONLY the user's highlighted selection and return a non-destructive diff preview."""
    if section_id not in project.sections:
        raise ValueError(f"Section introuvable : {section_id}")

    clean_sel = (selected_text or "").strip()
    if not clean_sel:
        raise ValueError("Aucun texte sélectionné.")

    sec = project.sections[section_id]
    lang = normalize_lang(getattr(project, "language", "fr"))
    original_md = (
        current_markdown
        if current_markdown is not None and current_markdown.strip()
        else (sec.content_markdown or "")
    )
    refs_ctx = _format_refs_context(project)

    action_norm = (action or "reformulate").strip().lower()
    action_prompts = {
        "reformulate": (
            "Reformule UNIQUEMENT le passage sélectionné dans un style académique médical clair, fluide et rigoureux (FMSB/CAMES), "
            "sans altérer le sens scientifique ni les données chiffrées."
        ),
        "modify": (
            f"Modifie UNIQUEMENT le passage sélectionné selon cette instruction précise : {instruction.strip() or 'Améliorer la précision scientifique.'}"
        ),
        "complement": (
            "Garde le passage sélectionné INTACT au début de `replacement_text`, puis ajoute immédiatement après un complément "
            f"d'information scientifique pertinent ({instruction.strip() or 'détails méthodologiques ou épidémiologiques utiles'})."
        ),
        "cite": (
            "Enrichis UNIQUEMENT le passage sélectionné en y intégrant de manière naturelle les citations Vancouver `[n]` "
            "pertinentes issues de la liste des références du projet (n'invente aucun numéro hors liste)."
        ),
        "shorten": (
            "Raccourcis et rends plus concis UNIQUEMENT le passage sélectionné tout en conservant toutes les informations essentielles."
        ),
        "expand": (
            "Développe et approfondis UNIQUEMENT le passage sélectionné avec davantage de rigueur scientifique et de clarté académique."
        ),
    }
    task_desc = action_prompts.get(action_norm, action_prompts["reformulate"])
    if instruction.strip() and action_norm not in ("modify", "complement"):
        task_desc += f"\nConsigne complémentaire : {instruction.strip()}"

    # Module 4: Identify the matching Module 1 plan item for this highlighted selection
    section_plan = getattr(sec, "writing_plan", None)
    matched_item = identify_plan_item_for_selection(section_plan, clean_sel, use_llm_fallback=True)
    plan_directive = _format_plan_item_directive(section_id, section_plan, matched_item)

    # Provide only brief surrounding context so Gemini matches tone/grammar without rewriting the whole section
    context_excerpt = original_md[:2400] if original_md else ""

    prompt = f"""
Tu es le **Module 2 (Raisonnement & Rédaction)** sollicité par le **Module 4 (Reformulation sélective consciente du plan)** (FMSB / CAMES).
TÂCHE : {task_desc}

RÈGLES ABSOLUES :
- Renvoie dans `replacement_text` UNIQUEMENT le texte destiné à remplacer le PASSAGE SÉLECTIONNÉ (ne renvoie JAMAIS le reste de la section).
- Pas de préambule, pas de guillemets décoratifs autour du texte, pas de commentaires méta.
- Respecte fidèlement le bloc du plan et les règles pédagogiques applicables ci-dessous.
- Langue : {"English" if lang == "en" else "Français"}.

{plan_directive if plan_directive else ''}

PASSAGE SÉLECTIONNÉ PAR L'UTILISATEUR :
{clean_sel}

CONTEXTE DE LA SECTION (lecture seule pour cohérence de ton) :
{context_excerpt}

RÉFÉRENCES DU PROJET :
{refs_ctx}
"""

    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=SelectionTransformOutput,
        temperature=0.25,
    )
    dto: SelectionTransformOutput = res["data"]
    model_used = str(res.get("model_used") or "")
    replacement = _clean_text((dto.replacement_text or "").strip())
    if not replacement:
        replacement = clean_sel

    # Splice replacement cleanly into original_md at the exact selected passage
    patch_res = apply_surgical_text_patches(
        original_md,
        [
            {
                "target_text": clean_sel,
                "replacement_text": replacement,
                "reason": _clean_text(dto.summary or "") or f"Action sur sélection : {action_norm}",
            }
        ],
    )
    proposed_md = patch_res["patched_markdown"] if patch_res["applied_count"] > 0 else original_md

    summary = _clean_text((dto.summary or "").strip()) or (
        "Passage sélectionné transformé — validez ou relancez si besoin."
        if lang != "en"
        else "Selected passage transformed — validate or retry as needed."
    )

    return {
        "section_id": section_id,
        "action": action_norm,
        "selected_text": clean_sel,
        "replacement_text": replacement,
        "original_markdown": original_md,
        "proposed_markdown": proposed_md,
        "proposed_html": markdown_to_clean_html(proposed_md),
        "patches": [
            {
                "target_text": clean_sel,
                "replacement_text": replacement,
                "reason": summary,
            }
        ],
        "matched_in_section": patch_res["applied_count"] > 0,
        "matched_plan_item_id": matched_item.item_id if matched_item else "",
        "matched_plan_item_title": matched_item.title if matched_item else "",
        "summary": summary,
        "model_used": model_used,
    }
