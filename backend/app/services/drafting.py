import re
from typing import Any, Dict, List, Optional, Tuple
import uuid
from pydantic import BaseModel, Field

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import (
    get_language_prompt_directive,
    get_localized_section_spec,
    normalize_lang,
)
from app.schemas.protocol import (
    DiagramConnection,
    DiagramNode,
    ProtocolProject,
    SectionDiagram,
    SectionTable,
    StepStatus,
)
from app.services.copilot_service import (
    format_supervisor_directives_block,
    snapshot_section_version,
)
from app.services.gemini_client import gemini_router
from app.services.humanizer import (
    HUMAN_ACADEMIC_STYLE_INSTRUCTIONS,
    get_human_style_instructions,
    sanitize_llm_french_typography,
    sanitize_llm_typography,
)
from app.services.pipeline_tracker import emit_pipeline_step
from app.services.rich_formatter import markdown_to_clean_html, strip_ascii_diagrams
from app.services.section_orchestrator import orchestrate_section_action
from app.services.working_memory import (
    get_upstream_and_current_memory,
    update_section_working_memory,
)


class SuggestedTableInline(BaseModel):
    title: str
    anchor_hint: str = ""
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)


class SuggestedNodeOutput(BaseModel):
    id: str
    shape: str = "rounded"  # "rectangle" | "rounded" | "circle" | "diamond"
    title: str
    items: List[str] = Field(default_factory=list)
    width_px: int = 420
    color: str = "#6C2826"


class SuggestedConnectionOutput(BaseModel):
    from_id: str
    to_id: str
    label: str = ""
    style: str = "solid"


class SuggestedDiagramOutput(BaseModel):
    title: str
    caption: str = ""
    anchor_hint: str = ""
    layout: str = "vertical"
    nodes: List[SuggestedNodeOutput] = Field(default_factory=list)
    connections: List[SuggestedConnectionOutput] = Field(default_factory=list)


class DraftSectionOutput(BaseModel):
    content_markdown: str = Field(
        description="Texte académique rédigé en français médical rigoureux pour cette section (sans schémas ASCII ni tableaux Markdown bruts)"
    )
    cited_order_indices: List[int] = Field(
        default_factory=list,
        description="Numéros [n] des références effectivement citées dans le texte",
    )
    assistant_commentary: str = Field(
        description="Brève explication pédagogique des choix méthodologiques et points à vérifier par l'étudiant"
    )
    suggested_tables: List[SuggestedTableInline] = Field(
        default_factory=list,
        description="Tableaux structurés à insérer dans cette section (au lieu de tableaux Markdown)",
    )
    suggested_diagrams: List[SuggestedDiagramOutput] = Field(
        default_factory=list,
        description="Schémas/diagrammes vectoriels structurés (ex: modèle conceptuel, diagramme de flux) avec nœuds et connexions",
    )


STRUCTURED_TOOLS_INSTRUCTIONS = """
OUTILS DE MISE EN FORME & SCHEMAS DISPONIBLES :
1. FORMULES MATHEMATIQUES : Pour toute équation ou symbole, utilise uniquement les opérateurs standards entre $...$ (en ligne) ou $$...$$ (centré) : \\frac{num}{den}, ^{exp}, _{ind}, \\cdot, \\times, \\pm, \\leq, \\geq, \\neq, \\approx, \\sqrt{...}, \\alpha, \\beta, \\sigma, \\mu. Ils seront automatiquement convertis en équations natives Word et HTML.
2. SCHEMAS / MODELES CONCEPTUELS / FLUX : N'écris JAMAIS de schéma en texte ASCII (+---+, |, v). Utilise exclusivement le champ structuré `suggested_diagrams` en définissant des nœuds (`id`, `shape`: "rounded"|"rectangle"|"circle"|"diamond", `title`, `items`, `width_px`) et des connexions (`from_id`, `to_id`, `label`).
3. TABLEAUX (USAGE STRICTEMENT JUSTIFIE) : N'écris JAMAIS de tableau Markdown (| col |) dans `content_markdown`, et N'INSERE PAS de tableau superflu dans `suggested_tables` (`suggested_tables=[]`) sauf si cette section l'exige méthodologiquement (variables, synthèse d'études, chronogramme, budget, critères d'éligibilité) ou si l'étudiant en fait la demande explicite.
"""


class AuditOutput(BaseModel):
    compliant: bool
    issues: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class SuggestedTableOutput(BaseModel):
    title: str
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)


def _get_section_spec(section_id: str, lang: str = "fr"):
    return get_localized_section_spec(section_id, lang)


def _format_cames_granularity_block(spec, lang: str = "fr") -> str:
    """Format CAMES/FMSB granular section expectations (concepts, volume, refs, tables/figures)."""
    c = spec.constraints
    if normalize_lang(lang) == "en":
        parts = [
            f"CAMES / FMSB BENCHMARK FOR '{spec.title}':",
            f"- Target length: {c.min_pages or '?'} to {c.max_pages or '?'} page(s) ({c.min_words or '?'} to {c.max_words or '?'} words).",
            f"- Allowed references in this section: min={c.min_references if c.min_references is not None else 0}, max={c.max_references if c.max_references is not None else 'unlimited'}.",
            f"- TABLE policy: {c.table_policy}" + (f" ({c.table_guidance})" if c.table_guidance else ""),
            f"- FIGURE policy: {c.figure_policy}" + (f" ({c.figure_guidance})" if c.figure_guidance else ""),
        ]
        if getattr(spec, "cames_concepts_required", None):
            parts.append("- MANDATORY CAMES CONCEPTS TO COVER:")
            parts.extend(f"  * {item}" for item in spec.cames_concepts_required)
        if getattr(spec, "concepts_to_avoid", None):
            parts.append("- ELEMENTS TO AVOID HERE (zero redundancy):")
            parts.extend(f"  * {item}" for item in spec.concepts_to_avoid)
        return "\n".join(parts)

    parts = [
        f"REFERENTIEL CAMES / FMSB POUR '{spec.title}' :",
        f"- Volume cible : {c.min_pages or '?'} à {c.max_pages or '?'} page(s) ({c.min_words or '?'} à {c.max_words or '?'} mots).",
        f"- Références autorisées dans cette section : min={c.min_references if c.min_references is not None else 0}, max={c.max_references if c.max_references is not None else 'libre'}.",
        f"- Politique TABLEAUX : {c.table_policy}" + (f" ({c.table_guidance})" if c.table_guidance else ""),
        f"- Politique FIGURES : {c.figure_policy}" + (f" ({c.figure_guidance})" if c.figure_guidance else ""),
    ]
    if getattr(spec, "cames_concepts_required", None):
        parts.append("- CONCEPTS CAMES OBLIGATOIRES A DEVELOPPER :")
        parts.extend(f"  * {item}" for item in spec.cames_concepts_required)
    if getattr(spec, "concepts_to_avoid", None):
        parts.append("- ELEMENTS A EVITER ICI (pour zéro redondance) :")
        parts.extend(f"  * {item}" for item in spec.concepts_to_avoid)
    return "\n".join(parts)


def deterministic_section_checks(project: ProtocolProject, section_id: str, text: str) -> List[str]:
    """Run hard-coded CAMES/FMSB methodological, volume, reference, and table-policy rules."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    notes: List[str] = []
    spec = _get_section_spec(section_id, lang)
    c = spec.constraints
    words = len(text.split()) if text.strip() else 0

    if c.required and words == 0:
        notes.append(
            "Empty section: content is required by CAMES/FMSB guidelines."
            if lang == "en"
            else "Section vide : un contenu est requis par le canevas CAMES/FMSB."
        )

    if words > 0:
        if c.min_words and words < c.min_words:
            notes.append(
                f"Insufficient length per CAMES/FMSB: {words} words (recommended minimum: {c.min_words} words)."
                if lang == "en"
                else f"Volume insuffisant selon CAMES/FMSB : {words} mots (minimum recommandé : {c.min_words} mots)."
            )
        if c.max_words and words > c.max_words and section_id not in ("intro_contexte", "intro_interet"):
            notes.append(
                f"Word limit exceeded per CAMES/FMSB: {words} words (recommended maximum: {c.max_words} words)."
                if lang == "en"
                else f"Dépassement de volume CAMES/FMSB : {words} mots (maximum recommandé : {c.max_words} mots)."
            )

    if words > 0 and section_id not in (
        "intro_contexte",
        "intro_interet",
        "references",
        "toc",
        "abbreviations",
        "list_of_tables",
        "list_of_figures",
        "annexe_notice_information",
        "annexe_consentement",
        "annexe_fiche_collecte",
        "annexe_autorisation_recherche",
        "annexe_clairance_ethique",
        "annexe_confidentialite",
        "annexe_engagement",
        "annexes",
    ):
        cited_nums = set(re.findall(r"\[(\d+)\]", text))
        if c.min_references is not None and c.min_references > 0 and len(cited_nums) < c.min_references:
            notes.append(
                f"Insufficient citations ({len(cited_nums)} ref(s) cited): CAMES requires at least {c.min_references} citation(s) [n] in this section."
                if lang == "en"
                else f"Citations insuffisantes ({len(cited_nums)} réf. citées) : le référentiel CAMES attend au moins {c.min_references} référence(s) [n] dans cette section."
            )
        if c.max_references is not None and len(cited_nums) > c.max_references:
            notes.append(
                f"Too many citations ({len(cited_nums)} ref(s) cited): maximum allowed is {c.max_references} citation(s) in this section."
                if lang == "en"
                else f"Trop de citations ({len(cited_nums)} réf. citées) : maximum autorisé de {c.max_references} référence(s) dans cette section."
            )

    if c.table_policy == "forbidden":
        sec_obj = project.sections.get(section_id)
        has_raw_table = bool(re.search(r"(?m)^\s*\|[\s:\-|]+\|\s*$", text or ""))
        if has_raw_table or (sec_obj and getattr(sec_obj, "custom_tables", None)):
            notes.append(
                "CAMES policy: no tables are expected in this section (remove superfluous tables)."
                if lang == "en"
                else "Politique CAMES : aucun tableau n'est attendu dans cette section (supprimer les tableaux superflus)."
            )

    if section_id in ("intro_contexte", "intro_interet"):
        if section_id == "intro_contexte":
            other = project.sections.get("intro_interet").content_markdown if project.sections.get("intro_interet") else ""
            intro_text = f"{text} {other}"
        else:
            other = project.sections.get("intro_contexte").content_markdown if project.sections.get("intro_contexte") else ""
            intro_text = f"{other} {text}"

        intro_words = len(intro_text.split())
        if intro_words > 900:
            notes.append(
                f"Introduction length alert: ~{intro_words} words total (target <= 2 pages ≈ 800–900 words)."
                if lang == "en"
                else f"Alerte volume Introduction : ~{intro_words} mots au total (cible ≤ 2 pages ≈ 800-900 mots)."
            )
        cited_nums = set(re.findall(r"\[(\d+)\]", intro_text))
        if len(cited_nums) > 5:
            notes.append(
                f"FMSB Introduction alert: {len(cited_nums)} references cited ({sorted(cited_nums)}) whereas maximum allowed is 5."
                if lang == "en"
                else f"Alerte FMSB Introduction : {len(cited_nums)} références citées ({sorted(cited_nums)}) alors que le maximum autorisé est 5."
            )

    if section_id == "chap1_hypotheses" and text.strip().endswith("?"):
        notes.append(
            "Primary hypothesis must be phrased as an affirmative statement (never as a question)."
            if lang == "en"
            else "L'hypothèse principale doit être formulée à la forme affirmative (pas sous forme de question)."
        )

    return notes


def _ordered_unique_citations(text: str) -> List[str]:
    """Return unique numeric citation IDs in order of first appearance."""
    seen: set[str] = set()
    ordered: List[str] = []
    for num in re.findall(r"\[(\d+)\]", text or ""):
        if num not in seen:
            seen.add(num)
            ordered.append(num)
    return ordered


def _strip_disallowed_citations(text: str, allowed: set[str]) -> str:
    """Remove numeric [n] citations not in `allowed` while keeping prose spacing/punctuation clean."""
    if not text:
        return text

    def _repl(match: re.Match[str]) -> str:
        num = match.group(1)
        return match.group(0) if num in allowed else ""

    cleaned = re.sub(r"\s*\[(\d+)\]", _repl, text)
    # Collapse leftover empty brackets/commas or double spaces before punctuation
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    return cleaned.strip()


def _build_intro_shared_budget_block(
    project: ProtocolProject,
    section_id: str,
    lang: str,
) -> str:
    """Provide cross-part Introduction citation & word consensus rules for intro_contexte & intro_interet."""
    if section_id not in ("intro_contexte", "intro_interet"):
        return ""
    sibling_id = "intro_interet" if section_id == "intro_contexte" else "intro_contexte"
    sibling = project.sections.get(sibling_id)
    sibling_text = sibling.content_markdown if sibling else ""
    sibling_words = len(sibling_text.split())
    sibling_refs = _ordered_unique_citations(sibling_text)
    remaining_new = max(0, 5 - len(sibling_refs))
    remaining_words = max(250, 880 - sibling_words)
    refs_str = ", ".join(f"[{r}]" for r in sibling_refs) if sibling_refs else ("none" if lang == "en" else "aucune")

    if lang == "en":
        return (
            "SHARED INTRODUCTION CONSENSUS (FMSB/CAMES HARD RULE):\n"
            f"- `intro_contexte` + `intro_interet` COMBINED must stay <= 5 unique references total and <= 900 words total.\n"
            f"- The sibling part (`{sibling_id}`) currently has {sibling_words} words and {len(sibling_refs)} cited reference(s): {refs_str}.\n"
            f"- Target length for THIS part: stay within your minimum word count and <= {remaining_words} words.\n"
            f"- Citation budget for THIS part: you may reuse citations from {refs_str} and introduce AT MOST {remaining_new} new reference(s) "
            f"(if sibling already has >= 5 refs, ONLY reuse citations from {refs_str[:40]} or cite 0 new refs)."
        )
    return (
        "CONSENSUS PARTAGÉ INTRODUCTION (RÈGLE STRICTE FMSB/CAMES) :\n"
        f"- L'ensemble `intro_contexte` + `intro_interet` réunis NE DOIT PAS dépasser 5 références uniques au total ni 900 mots au total.\n"
        f"- L'autre sous-partie (`{sibling_id}`) contient actuellement {sibling_words} mots et {len(sibling_refs)} référence(s) citée(s) : {refs_str}.\n"
        f"- Volume cible pour CETTE sous-partie : respecter le minimum recommandé tout en restant ≤ {remaining_words} mots.\n"
        f"- Budget citations pour CETTE sous-partie : réutilise prioritairement les citations de {refs_str} et n'introduis AU MAXIMUM que {remaining_new} nouvelle(s) référence(s) "
        f"(si l'autre sous-partie a déjà ≥ 5 références, réutilise uniquement celles-là sans en ajouter de nouvelles)."
    )


def _reconcile_intro_citation_cap(
    project: ProtocolProject,
    section_id: str,
    current_md: str,
    max_refs: int = 5,
) -> str:
    """Enforce the <= 5 unique references consensus across BOTH intro_contexte and intro_interet."""
    if section_id not in ("intro_contexte", "intro_interet"):
        return current_md

    sibling_id = "intro_interet" if section_id == "intro_contexte" else "intro_contexte"
    sibling = project.sections.get(sibling_id)
    sibling_md = sibling.content_markdown if sibling else ""

    cur_refs = _ordered_unique_citations(current_md)
    sib_refs = _ordered_unique_citations(sibling_md)
    union_ordered = _ordered_unique_citations(f"{current_md} {sibling_md}")
    if len(union_ordered) <= max_refs:
        # Refresh sibling audit notes too in case the fix resolved the shared alert
        if sibling is not None and sibling_md.strip():
            # Temporarily evaluate with updated current_md via project copy-free check
            orig_cur = project.sections[section_id].content_markdown if section_id in project.sections else ""
            if section_id in project.sections:
                project.sections[section_id].content_markdown = current_md
            try:
                sibling.audit_notes = deterministic_section_checks(project, sibling_id, sibling_md)
            finally:
                if section_id in project.sections:
                    project.sections[section_id].content_markdown = orig_cur
        return current_md

    # Build a deterministic 5-ref consensus: prefer shared refs, then balance between contexte & interet
    shared = [r for r in cur_refs if r in set(sib_refs)]
    selected: List[str] = list(shared[:max_refs])
    for pool in (cur_refs, sib_refs):
        for r in pool:
            if len(selected) >= max_refs:
                break
            if r not in selected:
                selected.append(r)
    allowed = set(selected[:max_refs])

    reconciled_cur = _strip_disallowed_citations(current_md, allowed)
    if sibling is not None and sibling_md.strip():
        reconciled_sib = _strip_disallowed_citations(sibling_md, allowed)
        if reconciled_sib != sibling_md:
            sibling.content_markdown = reconciled_sib
            sibling.content_html = markdown_to_clean_html(reconciled_sib)
        orig_cur = project.sections[section_id].content_markdown if section_id in project.sections else ""
        if section_id in project.sections:
            project.sections[section_id].content_markdown = reconciled_cur
        try:
            sib_notes = deterministic_section_checks(project, sibling_id, sibling.content_markdown)
            sibling.audit_notes = sib_notes
        finally:
            if section_id in project.sections:
                project.sections[section_id].content_markdown = orig_cur

    return reconciled_cur


def _count_words(text: str) -> int:
    """Exact word counter aligned with deterministic_section_checks."""
    return len((text or "").split()) if (text or "").strip() else 0


def _extract_continuation_anchor(text: str) -> Dict[str, Any]:
    """Extract the last 2-3 sentences, existing Markdown headings, and cited [n] markers."""
    clean = (text or "").strip()
    headings = [
        ln.strip()
        for ln in clean.splitlines()
        if re.match(r"^#{1,4}\s+\S", ln.strip())
    ]
    # Remove trailing incomplete markdown artifacts and split into sentences
    prose_lines = [ln.strip() for ln in clean.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    prose_blob = " ".join(prose_lines)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", prose_blob) if s.strip()]
    last_sentences = " ".join(sentences[-3:]) if sentences else prose_blob[-360:]
    last_sentence = sentences[-1] if sentences else prose_blob[-180:]
    used_citations = _ordered_unique_citations(clean)
    return {
        "last_sentences": last_sentences[-600:],
        "last_sentence": last_sentence[-260:],
        "headings": headings[:20],
        "used_citations": used_citations,
    }


def _stitch_continuation(existing_md: str, addition_md: str) -> str:
    """Seamlessly join a continuation chunk while stripping accidental overlap of the anchor sentence."""
    base = (existing_md or "").rstrip()
    add = (addition_md or "").strip()
    if not add:
        return base
    if not base:
        return add

    # Strip accidental markdown code fences if the LLM wrapped the continuation
    add = re.sub(r"^```(?:markdown|md)?\s*\n?", "", add, flags=re.IGNORECASE)
    add = re.sub(r"\n?```\s*$", "", add).strip()

    anchor = _extract_continuation_anchor(base)
    last_sent = anchor["last_sentence"].strip()
    if last_sent and len(last_sent) >= 24:
        # If the continuation starts by repeating the last sentence, remove the duplicate prefix
        norm_add = add.lstrip()
        if norm_add.lower().startswith(last_sent.lower()):
            add = norm_add[len(last_sent):].lstrip(" \n\r-–—")
        else:
            # Also check trailing 60-char snippet overlap
            tail_snip = last_sent[-60:].strip()
            pos = norm_add.lower().find(tail_snip.lower())
            if 0 <= pos <= 140:
                add = norm_add[pos + len(tail_snip):].lstrip(" \n\r-–—")

    if not add:
        return base
    return f"{base}\n\n{add}"


def _ensure_target_word_count(
    project: ProtocolProject,
    section_id: str,
    spec: Any,
    content_md: str,
    refs_ctx: str,
    cross_memory: str,
    lang: str,
    max_passes: int = 3,
) -> Tuple[str, Dict[str, Any]]:
    """Internal word-count monitor & bounded continuation loop for long FMSB sections (e.g. 700/1000/1400 words).

    Counts words generated by the LLM, compares against spec.constraints.min_words, and if short,
    prompts the LLM to seamlessly continue from the last sentences without repeating already-covered points.
    Only returns the final stitched text to the caller.
    """
    c = getattr(spec, "constraints", None)
    min_words = int(getattr(c, "min_words", 0) or 0) if c else 0
    max_words = int(getattr(c, "max_words", 0) or 0) if c else 0

    initial_words = _count_words(content_md)
    trace: Dict[str, Any] = {
        "triggered": False,
        "min_words_required": min_words,
        "initial_words": initial_words,
        "final_words": initial_words,
        "continuation_passes": 0,
    }

    # Skip sections without a meaningful minimum word threshold or structured budget section
    if min_words < 300 or section_id == "chap3_budget" or initial_words == 0 or initial_words >= min_words:
        return content_md, trace

    # For 2-part Introduction, cap target to avoid exceeding sibling/combined max_words
    target_min = min_words
    if max_words and target_min > max_words:
        target_min = max_words

    current_md = content_md
    current_words = initial_words
    lang_directive = get_language_prompt_directive(lang)
    style_instructions = get_human_style_instructions(lang)
    intro_budget_block = _build_intro_shared_budget_block(project, section_id, lang=lang)

    for pass_idx in range(1, max_passes + 1):
        if current_words >= target_min:
            break
        deficit = target_min - current_words
        ask_words = deficit + 80
        if max_words:
            remaining_cap = max(80, max_words - current_words)
            ask_words = min(ask_words, remaining_cap)
            if remaining_cap < 40:
                break

        anchor = _extract_continuation_anchor(current_md)
        headings_block = (
            "\n".join(f"  - {h}" for h in anchor["headings"])
            if anchor["headings"]
            else "  (Aucun sous-titre encore écrit)"
        )
        used_cits = ", ".join(f"[{n}]" for n in anchor["used_citations"]) or "Aucune"

        emit_pipeline_step(
            "llm_continue",
            f"Poursuite rédactionnelle ({current_words}/{target_min} mots · passe {pass_idx}/{max_passes})...",
            f"Expanding section to target length ({current_words}/{target_min} words · pass {pass_idx}/{max_passes})...",
            progress_pct=min(88, 74 + pass_idx * 4),
        )

        continuation_prompt = f"""You are seamlessly continuing an academic FMSB/CAMES medical thesis protocol section that is currently too short.
{lang_directive}

SECTION: {spec.chapter_title} — {spec.title}
REQUIRED MINIMUM LENGTH: {target_min} words.
CURRENT LENGTH SO FAR: {current_words} words (MISSING AT LEAST {deficit} words).
TARGET FOR THIS CONTINUATION: write approximately {ask_words} additional words of dense, substantive academic prose.

ALREADY-COVERED SUB-HEADINGS (DO NOT REPEAT THESE):
{headings_block}

CITATIONS ALREADY USED SO FAR: {used_cits}
{intro_budget_block}

LAST SENTENCES ALREADY WRITTEN (pick up the exact train of thought right after this — DO NOT repeat these sentences):
\"\"\"{anchor["last_sentences"]}\"\"\"

AVAILABLE VERIFIED REFERENCES (cite ONLY from this list with [n], never invent references):
{refs_ctx or "None"}

CROSS-SECTION MEMORY (do not duplicate):
{(cross_memory or "")[:1200]}

STRICT CONTINUATION RULES:
1. Return ONLY the continuation text in clean Markdown prose (NO introductory meta-commentary like "Voici la suite", NO repeating the previous sentences).
2. Deepen the scientific, epidemiological, pathophysiological, or methodological analysis expected for « {spec.title} » without filler or redundancy.
3. Keep exact Vancouver [n] citation consistency and never invent statistics or references.
4. Close the continuation cleanly with complete sentences.
{style_instructions}
"""
        try:
            cont_res = gemini_router.generate_with_fallback(
                role="drafting",
                contents=continuation_prompt,
            )
            addition_raw = (cont_res.get("text") or "").strip()
        except Exception:
            break

        if not addition_raw:
            break

        addition_clean = strip_ascii_diagrams(addition_raw)
        addition_clean, _ = sanitize_llm_typography(addition_clean, lang=lang)
        stitched = _stitch_continuation(current_md, addition_clean)
        new_words = _count_words(stitched)
        gained = new_words - current_words
        trace["triggered"] = True
        trace["continuation_passes"] = pass_idx

        if gained < 35:
            # Stop early if the model cannot add substantial new content
            if gained > 0:
                current_md = stitched
                current_words = new_words
            break

        current_md = stitched
        current_words = new_words

    trace["final_words"] = current_words
    return current_md, trace


def draft_section_with_ai(
    project: ProtocolProject,
    section_id: str,
    user_instruction: Optional[str] = None,
    force_external_research: bool = False,
) -> Dict[str, object]:
    """Draft or refine a section grounded strictly in approved ArticleSegments and FMSB rules."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    spec = _get_section_spec(section_id, lang)
    if section_id == "chap3_budget":
        from app.services.budget_engine import generate_structured_budget_with_ai
        from app.services.presentation_orchestrator import refresh_budget_slide_if_present

        emit_pipeline_step(
            "llm_draft",
            "Génération du budget prévisionnel structuré (FCFA) & synchronisation Modules 1-2-4...",
            "Generating structured provisional budget (FCFA) & syncing Modules 1-2-4...",
            progress_pct=65,
        )
        bud_res = generate_structured_budget_with_ai(
            project,
            user_instruction=user_instruction or "",
        )
        clean_md = bud_res["content_markdown"]
        clean_html = markdown_to_clean_html(clean_md)
        det_notes = deterministic_section_checks(project, section_id, clean_md)
        current_sec = project.sections.get(section_id)
        if current_sec is not None:
            snapshot_section_version(
                current_sec,
                source="generation",
                note="Avant regénération du budget",
            )
            current_sec.content_markdown = clean_md
            current_sec.content_html = clean_html
            current_sec.status = StepStatus.IN_PROGRESS
            current_sec.audit_notes = det_notes
            current_sec.custom_tables = []
        refresh_budget_slide_if_present(project)
        update_section_working_memory(project, section_id)
        return {
            "model_used": bud_res["model_used"],
            "content_markdown": clean_md,
            "content_html": clean_html,
            "cited_ref_ids": [],
            "assistant_commentary": bud_res["assistant_commentary"],
            "deterministic_notes": det_notes,
            "orchestration_trace": {
                "budget_items_count": len(bud_res["items"]),
                "budget_grand_total_fcfa": bud_res["grand_total_fcfa"],
            },
        }
    if section_id == "annexe_fiche_collecte":
        from app.services.crf_builder import generate_or_improve_crf_with_ai

        snapshot_section_version(
            project.sections.get(section_id),
            source="generation",
            note="Avant regénération Fiche de collecte",
        )
        crf_res = generate_or_improve_crf_with_ai(
            project,
            user_instruction=user_instruction or "",
            mode="auto",
        )
        return {
            "model_used": crf_res["model_used"],
            "content_markdown": crf_res["content_markdown"],
            "content_html": crf_res["content_html"],
            "cited_ref_ids": [],
            "assistant_commentary": crf_res["assistant_commentary"],
            "deterministic_notes": crf_res.get("cames_compliance_checklist") or [],
            "orchestration_trace": {"crf_mode": crf_res["mode_applied"], "crf_fields_count": len(crf_res["crf_fields"])},
        }
    if section_id in (
        "annexe_notice_information",
        "annexe_consentement",
        "annexe_autorisation_recherche",
        "annexe_clairance_ethique",
        "annexe_confidentialite",
        "annexe_engagement",
    ):
        from app.services.annexe_engine import generate_sub_annex_content

        snapshot_section_version(
            project.sections.get(section_id),
            source="generation",
            note="Avant regénération Annexe",
        )
        bilingual = "bilingue" in (user_instruction or "").lower() or "english" in (user_instruction or "").lower()
        ann_res = generate_sub_annex_content(
            project,
            section_id=section_id,
            user_instruction=user_instruction or "",
            bilingual=bilingual,
            use_ai=True,
        )
        return {
            "model_used": ann_res["model_used"],
            "content_markdown": ann_res["content_markdown"],
            "content_html": ann_res["content_html"],
            "cited_ref_ids": [],
            "assistant_commentary": ann_res["assistant_commentary"],
            "deterministic_notes": [],
            "orchestration_trace": {"sub_annex_faculty_context": ann_res.get("faculty_context", {})},
        }
    if section_id in ("toc", "abbreviations", "list_of_tables", "list_of_figures", "references", "annexes"):
        from app.services.storage import sync_special_sections

        sync_special_sections(project, force_section_id=section_id)
        if section_id in ("toc", "list_of_tables", "list_of_figures") and not (user_instruction or "").strip():
            synced_sec = project.sections[section_id]
            return {
                "model_used": "synchronisation-deterministe",
                "content_markdown": synced_sec.content_markdown,
                "content_html": synced_sec.content_html,
                "cited_ref_ids": [],
                "assistant_commentary": (
                    "Preliminary section automatically synchronized per CAMES/FMSB order."
                    if lang == "en"
                    else "Section liminaire synchronisée automatiquement selon l'ordre officiel FMSB."
                ),
                "deterministic_notes": [],
            }

    current_sec = project.sections.get(section_id)
    current_text = current_sec.content_markdown if current_sec else ""

    orch = orchestrate_section_action(
        project=project,
        section_id=section_id,
        action="draft",
        user_instruction=user_instruction,
        force_external_research=force_external_research,
    )
    orch_trace = orch["orchestration_trace"]
    cross_memory = orch["cross_section_memory"]
    internal_library_ctx = (orch.get("internal_library_context") or "").strip()

    ref_by_id = {r.ref_id: r for r in project.references}
    matching_segments = [
        seg
        for seg in project.article_segments
        if seg.approved and section_id in seg.target_section_ids and seg.ref_id in ref_by_id
    ]

    if matching_segments:
        segments_context = "\n".join(
            f"- Réf [{ref_by_id[s.ref_id].order_index}]"
            f"{f' [{ref_by_id[s.ref_id].african_source_label}]' if ref_by_id[s.ref_id].african_source_label else ''} "
            f"({ref_by_id[s.ref_id].authors[0] if ref_by_id[s.ref_id].authors else ''}, {ref_by_id[s.ref_id].year or ''}) "
            f"| Portée={s.epidemiology_scope or 'N/A'} : {s.summary} | Données exactes: {s.key_quote_or_data}"
            for s in matching_segments[:15]
        )
    else:
        segments_context = "\n".join(
            f"- Réf [{r.order_index}]"
            f"{f' [{r.african_source_label}]' if r.african_source_label else ''} "
            f"{r.title} ({r.year or ''}): {r.abstract[:280]}"
            for r in project.references[:6]
        ) or ("No segmented references available for this section yet." if lang == "en" else "Aucune référence segmentée pour cette section pour le moment.")

    if lang == "en":
        mode_priority_rule = (
            "PRIORITY RULE (EXISTING TEXT PRESENT): A draft already exists below. "
            "Reason FIRST on this existing text to refine, sharpen, and meet CAMES/FMSB standards without discarding valid points."
            if orch_trace["initial_text_present"]
            else "INITIALLY EMPTY SECTION: Draft a complete, rigorous proposal grounded strictly in the provided references."
        )
    else:
        mode_priority_rule = (
            "REGLE PRIORITAIRE (TEXTE INITIAL PRESENT) : Un brouillon ou contenu existe déjà ci-dessous. "
            "Raisonne EN PRIORITE sur ce texte existant pour l'améliorer, le préciser et atteindre les objectifs FMSB sans effacer l'acquis valide."
            if orch_trace["initial_text_present"]
            else "SECTION INITIALEMENT VIDE : Rédige une proposition complète et rigoureuse en t'appuyant sur les références apportées par Antigravity."
        )

    cames_block = _format_cames_granularity_block(spec, lang=lang)
    intro_budget_block = _build_intro_shared_budget_block(project, section_id, lang=lang)
    supervisor_block = format_supervisor_directives_block(project)
    lang_directive = get_language_prompt_directive(lang)
    style_instructions = get_human_style_instructions(lang)

    prompt = f"""
You are a senior medical research supervisor (FMSB / CAMES / CADMEF standards).
Draft or refine the following medical research protocol section:

{lang_directive}

TARGET SECTION: {spec.chapter_title} — {spec.title}
DESCRIPTION: {spec.description}
EXPECTED POINTS:
{chr(10).join('- ' + g for g in spec.guidance_points)}

{supervisor_block}

{cames_block}

{intro_budget_block}

{mode_priority_rule}

STUDY CONTEXT:
- Title: {project.cover.title or project.name}
- Study type: {project.study_type}
- Research idea / Setting: {project.research_idea}

CROSS-SECTION WORKING MEMORY (DO NOT REPEAT FACTS ALREADY WRITTEN ELSEWHERE):
{cross_memory}

CURRENT SECTION DRAFT:
{current_text if current_text.strip() else '(Empty)'}

USER INSTRUCTION:
{user_instruction or ('Draft a complete, well-structured, rigorous academic text for this section in English.' if lang == 'en' else 'Rédige une proposition complète, structurée et académique pour cette section.')}

VERIFIED SCIENTIFIC SEGMENTS AVAILABLE:
{segments_context}

INTERNAL SECTION LIBRARY (SECONDARY WORKING MEMORY):
{internal_library_ctx if internal_library_ctx else '(Empty for this section)'}

STRICT ANTI-HALLUCINATION & ANTI-REDUNDANCY RULES:
1. NEVER invent any statistic, rate, sample figure, or reference.
2. Cite ONLY the provided references above using [n] notation (e.g., [1], [2]). For citable internal sources (`Réf [n]`), cite `[n]` normally. For `Brouillon interne non référencé`, exploit its scientific/clinical content but NEVER invent a fake `[n]` citation.
3. NEVER repeat explanations, definitions, or statistics already written in the CROSS-SECTION WORKING MEMORY.
4. If local institutional data is missing from sources, explicitly write {'[To be completed by investigator: ...]' if lang == 'en' else '[À compléter par l’investigateur : ...]'}.
{style_instructions}
{STRUCTURED_TOOLS_INSTRUCTIONS}
"""
    emit_pipeline_step(
        "llm_draft",
        f"Rédaction scientifique de « {spec.title} »...",
        f"Drafting « {spec.title} »...",
        progress_pct=72,
    )
    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=DraftSectionOutput,
        temperature=0.28,
    )
    dto: DraftSectionOutput = res["data"]

    if spec.constraints.table_policy == "forbidden" and "tableau" not in (user_instruction or "").lower() and "table" not in (user_instruction or "").lower():
        dto.suggested_tables = []

    emit_pipeline_step(
        "postprocess",
        "Contrôle typographique, normes CAMES & mémoire de travail...",
        "Typography cleanup, CAMES compliance check & working memory...",
        detail="",
        progress_pct=90,
        active_model=res["model_used"],
    )
    order_to_ref_id = {r.order_index: r.ref_id for r in project.references if r.order_index}
    cited_ref_ids = [
        order_to_ref_id[idx] for idx in dto.cited_order_indices if idx in order_to_ref_id
    ]
    clean_md = strip_ascii_diagrams(dto.content_markdown)
    clean_md, _ = sanitize_llm_typography(clean_md, lang=lang)
    clean_md, wc_trace = _ensure_target_word_count(
        project=project,
        section_id=section_id,
        spec=spec,
        content_md=clean_md,
        refs_ctx=segments_context,
        cross_memory=cross_memory,
        lang=lang,
    )
    orch_trace["word_count_continuation"] = wc_trace
    clean_md = _reconcile_intro_citation_cap(project, section_id, clean_md, max_refs=5)
    # Recompute cited_ref_ids including any citations introduced during continuation passes
    all_cited_nums = _ordered_unique_citations(clean_md)
    cited_ref_ids = list(
        dict.fromkeys(
            cited_ref_ids
            + [order_to_ref_id[int(n)] for n in all_cited_nums if n.isdigit() and int(n) in order_to_ref_id]
        )
    )
    clean_html = markdown_to_clean_html(clean_md)
    det_notes = deterministic_section_checks(project, section_id, clean_md)

    if current_sec is not None:
        snapshot_section_version(
            current_sec,
            source="generation",
            note=f"Avant rédaction IA ({res['model_used']})",
        )
        current_sec.content_markdown = clean_md
        current_sec.content_html = clean_html
        current_sec.status = StepStatus.IN_PROGRESS
        current_sec.cited_ref_ids = cited_ref_ids
        current_sec.audit_notes = det_notes
        _append_structured_elements(current_sec, dto)
        from app.services.vancouver import renumber_and_sync_vancouver_references

        renumber_and_sync_vancouver_references(project)
        clean_md = current_sec.content_markdown
        clean_html = current_sec.content_html
        cited_ref_ids = list(current_sec.cited_ref_ids or [])

    wm_update = update_section_working_memory(project, section_id)
    orch_trace["working_memory_update"] = wm_update

    return {
        "model_used": res["model_used"],
        "content_markdown": clean_md,
        "content_html": clean_html,
        "cited_ref_ids": cited_ref_ids,
        "assistant_commentary": dto.assistant_commentary,
        "deterministic_notes": det_notes,
        "orchestration_trace": orch_trace,
        "used_internal_library_only": bool(orch.get("used_internal_library_only", False)),
        "internal_library_entries_used": int(orch.get("internal_library_entries_used", 0) or 0),
        "can_enrich_externally": bool(orch.get("can_enrich_externally", False)),
    }


def _append_structured_elements(sec, dto: DraftSectionOutput) -> None:
    """Persist AI-produced tables and diagrams into the section without duplicating titles."""
    existing_tbl_titles = {t.title.strip().lower() for t in sec.custom_tables}
    for tbl in dto.suggested_tables or []:
        t_title = (tbl.title or "").strip()
        if not t_title or not tbl.headers or t_title.lower() in existing_tbl_titles:
            continue
        sec.custom_tables.append(
            SectionTable(
                table_id=f"tbl_{uuid.uuid4().hex[:8]}",
                title=t_title,
                anchor_hint=(tbl.anchor_hint or "").strip(),
                headers=tbl.headers,
                rows=tbl.rows,
            )
        )
        existing_tbl_titles.add(t_title.lower())

    existing_diag_titles = {d.title.strip().lower() for d in getattr(sec, "custom_diagrams", [])}
    for dg in dto.suggested_diagrams or []:
        d_title = (dg.title or "").strip()
        if not d_title or not dg.nodes or d_title.lower() in existing_diag_titles:
            continue
        sec.custom_diagrams.append(
            SectionDiagram(
                diagram_id=f"diag_{uuid.uuid4().hex[:8]}",
                title=d_title,
                caption=(dg.caption or "").strip(),
                anchor_hint=(dg.anchor_hint or "").strip(),
                layout=dg.layout if dg.layout in ("vertical", "horizontal") else "vertical",
                nodes=[
                    DiagramNode(
                        id=n.id,
                        shape=n.shape if n.shape in ("rectangle", "rounded", "circle", "diamond") else "rounded",
                        title=n.title,
                        items=n.items,
                        width_px=n.width_px or 420,
                        color=n.color or "#6C2826",
                    )
                    for n in dg.nodes
                ],
                connections=[
                    DiagramConnection(
                        from_id=c.from_id,
                        to_id=c.to_id,
                        label=c.label,
                        style=c.style if c.style in ("solid", "dashed") else "solid",
                    )
                    for c in dg.connections
                ],
            )
        )
        existing_diag_titles.add(d_title.lower())


def _is_issue_waived(issue: str, waivers: List[Any]) -> bool:
    iss_low = (issue or "").strip().lower()
    if not iss_low:
        return False
    for w in waivers or []:
        w_txt = (getattr(w, "issue_text", None) or "").strip().lower()
        if not w_txt:
            continue
        if w_txt == "*" or w_txt in iss_low or iss_low in w_txt:
            return True
    return False


def audit_section_with_ai(project: ProtocolProject, section_id: str) -> Dict[str, object]:
    """Run Tri-Engine orchestration + deterministic checks + Gemini audit role on a section."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    spec = _get_section_spec(section_id, lang)
    sec = project.sections.get(section_id)
    text = sec.content_markdown if sec else ""

    orch = orchestrate_section_action(
        project=project,
        section_id=section_id,
        action="audit",
    )
    orch_trace = orch["orchestration_trace"]
    cross_memory = orch["cross_section_memory"]

    det_notes = deterministic_section_checks(project, section_id, text)

    if not text.strip():
        if lang == "en":
            extra_rec = (
                f"The literature agent prepared {orch_trace['added_references_count']} Vancouver reference(s) for this section. "
                if orch_trace["added_references_count"] > 0
                else ""
            )
            return {
                "model_used": orch_trace.get("decision_model"),
                "compliant": False,
                "issues": det_notes or ["Section is empty."],
                "recommendations": [
                    extra_rec + "Click « Draft this step » to generate the structured draft."
                ],
                "orchestration_trace": orch_trace,
            }
        extra_rec = (
            f"L'assistant documentaire a préparé {orch_trace['added_references_count']} référence(s) Vancouver pour cette section. "
            if orch_trace["added_references_count"] > 0
            else ""
        )
        return {
            "model_used": orch_trace.get("decision_model"),
            "compliant": False,
            "issues": det_notes or ["La section est vide."],
            "recommendations": [
                extra_rec + "Cliquez sur « Rédiger cette étape » pour générer le brouillon structuré."
            ],
            "orchestration_trace": orch_trace,
        }

    from app.services.copilot_service import format_supervisor_directives_block

    cames_block = _format_cames_granularity_block(spec, lang=lang)
    lang_directive = get_language_prompt_directive(lang)
    directives_block = format_supervisor_directives_block(project)
    waivers = list(getattr(sec, "audit_waivers", None) or []) if sec is not None else []
    waivers_block = ""
    if waivers:
        waivers_lines = [
            f"- Issue waived: {w.issue_text} | Justification: {w.justification}"
            for w in waivers
        ]
        waivers_block = (
            "APPROVED AUTHOR / SUPERVISOR AUDIT WAIVERS (DO NOT RE-FLAG THESE POINTS):\n"
            + "\n".join(waivers_lines)
        )
    cover = getattr(project, "cover", None)
    hosts = ", ".join(getattr(cover, "host_institutions", None) or []) if cover else ""
    period = getattr(cover, "study_period_label", "") if cover else ""
    project_meta_ctx = (
        f"Study type: {project.study_type or 'unspecified'} | "
        f"Host institutions: {hosts or 'unspecified'} | "
        f"Study period: {period or 'unspecified'}"
    )

    prompt = f"""
Evaluate CAMES / FMSB methodological compliance of this medical research protocol section.
{lang_directive}

Section: {spec.chapter_title} — {spec.title}
Expectations: {spec.guidance_points}
Project context: {project_meta_ctx}

{cames_block}

{directives_block if directives_block else ""}

{waivers_block if waivers_block else ""}

IMPORTANT OVERRIDE RULES:
- Active supervisor / methodological directives and approved author waivers OVERRIDE generic CAMES defaults.
- Do NOT flag or insist on any requirement that is already satisfied or explicitly exempted/defined by an active directive, approved waiver, or declared host-institution choice above.

CROSS-SECTION MEMORY (detect redundancy or repetition):
{cross_memory[:1800]}

Drafted text:
{text}

Verify: coverage of mandatory CAMES concepts (subject to the override rules above), word/reference bounds, table/figure policy compliance, presence of [n] citations, and zero repetition across sections. Return `issues` and `recommendations` in {'English' if lang == 'en' else 'French'}.
"""
    emit_pipeline_step(
        "llm_audit",
        f"Audit méthodologique CAMES de « {spec.title} »...",
        f"Auditing CAMES compliance for « {spec.title} »...",
        progress_pct=75,
    )
    res = gemini_router.generate_structured(
        role="audit",
        contents=prompt,
        schema=AuditOutput,
        temperature=0.1,
    )
    dto: AuditOutput = res["data"]
    raw_issues = det_notes + [i for i in dto.issues if i not in det_notes]
    all_issues = [i for i in raw_issues if not _is_issue_waived(i, waivers)]
    is_compliant = bool(len(all_issues) == 0 and (dto.compliant or len(waivers) > 0))
    if sec is not None:
        sec.audit_notes = all_issues
        if is_compliant:
            sec.status = StepStatus.VALIDATED
        else:
            sec.status = StepStatus.NEEDS_REVISION

    return {
        "model_used": res["model_used"],
        "compliant": is_compliant,
        "issues": all_issues,
        "recommendations": dto.recommendations,
        "orchestration_trace": orch_trace,
    }


def fix_section_issues_with_ai(
    project: ProtocolProject,
    section_id: str,
) -> Dict[str, object]:
    """Fix flagged audit notifications in a section after explicit user authorization."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    spec = _get_section_spec(section_id, lang)
    sec = project.sections.get(section_id)
    current_text = sec.content_markdown if sec else ""
    issues = sec.audit_notes if sec else []

    orch = orchestrate_section_action(
        project=project,
        section_id=section_id,
        action="fix",
        user_instruction=" | ".join(issues) if issues else None,
    )
    orch_trace = orch["orchestration_trace"]
    cross_memory = orch["cross_section_memory"]

    refs_ctx = "\n".join(
        f"[{r.order_index}] {r.vancouver_citation}" for r in project.references[:20]
    )

    cames_block = _format_cames_granularity_block(spec, lang=lang)
    intro_budget_block = _build_intro_shared_budget_block(project, section_id, lang=lang)
    supervisor_block = format_supervisor_directives_block(project)
    lang_directive = get_language_prompt_directive(lang)
    style_instructions = get_human_style_instructions(lang)

    prompt = f"""
The investigator authorizes you to fix every CAMES/FMSB audit issue flagged in this section.
{lang_directive}

SECTION: {spec.chapter_title} — {spec.title}
EXPECTATIONS: {spec.guidance_points}

{supervisor_block}

{cames_block}

{intro_budget_block}

FLAGGED ISSUES TO FIX:
{chr(10).join('- ' + i for i in issues) if issues else '- Improve overall FMSB compliance'}

CROSS-SECTION MEMORY (do not duplicate):
{cross_memory[:1800]}

CURRENT TEXT:
{current_text or '(Empty)'}

AVAILABLE REFERENCES:
{refs_ctx or 'None'}

Instructions:
- Reason first on the current text to resolve each flagged issue precisely while preserving valid content and exact [n] citations.
- Never invent figures, statistics, or references.
{style_instructions}
{STRUCTURED_TOOLS_INSTRUCTIONS}
"""
    emit_pipeline_step(
        "llm_fix",
        f"Correction ciblée de « {spec.title} »...",
        f"Fixing flagged audit issues in « {spec.title} »...",
        progress_pct=72,
    )
    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=DraftSectionOutput,
        temperature=0.22,
    )
    dto: DraftSectionOutput = res["data"]
    if spec.constraints.table_policy == "forbidden":
        dto.suggested_tables = []

    emit_pipeline_step(
        "postprocess",
        "Contrôle CAMES final & mise à jour mémoire de travail...",
        "Running final CAMES checks & working memory sync...",
        detail="",
        progress_pct=90,
        active_model=res["model_used"],
    )
    clean_md = strip_ascii_diagrams(dto.content_markdown)
    clean_md, _ = sanitize_llm_typography(clean_md, lang=lang)
    clean_md, wc_trace = _ensure_target_word_count(
        project=project,
        section_id=section_id,
        spec=spec,
        content_md=clean_md,
        refs_ctx=refs_ctx,
        cross_memory=cross_memory,
        lang=lang,
    )
    orch_trace["word_count_continuation"] = wc_trace
    clean_md = _reconcile_intro_citation_cap(project, section_id, clean_md, max_refs=5)
    clean_html = markdown_to_clean_html(clean_md)
    det_notes = deterministic_section_checks(project, section_id, clean_md)

    if sec is not None:
        snapshot_section_version(
            sec,
            source="fix",
            note="Avant correction CAMES",
        )
        sec.content_markdown = clean_md
        sec.content_html = clean_html
        if len(det_notes) == 0:
            sec.status = StepStatus.VALIDATED
            sec.audit_notes = []
        else:
            sec.status = StepStatus.IN_PROGRESS
            sec.audit_notes = det_notes
        _append_structured_elements(sec, dto)
        if section_id == "chap3_budget":
            from app.services.budget_engine import sync_budget_from_section
            from app.services.presentation_orchestrator import refresh_budget_slide_if_present

            sync_budget_from_section(project, overwrite=True)
            sec.custom_tables = []
            refresh_budget_slide_if_present(project)
        from app.services.vancouver import renumber_and_sync_vancouver_references

        renumber_and_sync_vancouver_references(project)
        clean_md = sec.content_markdown
        clean_html = sec.content_html

    wm_update = update_section_working_memory(project, section_id)
    orch_trace["working_memory_update"] = wm_update

    return {
        "model_used": res["model_used"],
        "content_markdown": clean_md,
        "content_html": clean_html,
        "assistant_commentary": dto.assistant_commentary,
        "remaining_notes": det_notes,
        "orchestration_trace": orch_trace,
    }


def suggest_section_diagram_with_ai(
    project: ProtocolProject,
    section_id: str,
    diagram_title: str,
    diagram_goal: str = "",
) -> Dict[str, object]:
    """Generate a structured node-and-arrow vector diagram tailored to a section."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    spec = _get_section_spec(section_id, lang)
    sec = project.sections.get(section_id)
    upstream_mem, _ = get_upstream_and_current_memory(project, section_id)
    lang_dir = get_language_prompt_directive(lang)
    prompt = f"""
Design a structured conceptual diagram or flowchart for a medical research protocol.
{lang_dir}
Section: {spec.chapter_title} — {spec.title}
Protocol topic: {project.cover.title or project.name}
Diagram title: {diagram_title}
Goal: {diagram_goal or 'Clearly model relationships / steps for this section'}
Upstream working memory:
{upstream_mem[:1500]}

Current section text: {(sec.content_markdown if sec else '')[:2500]}

Return short node `id` values ("n1", "n2"...), `shape`: "rounded"|"rectangle"|"circle"|"diamond", `title`, concise `items`, and `connections` (`from_id`, `to_id`, `label`).
"""
    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=SuggestedDiagramOutput,
        temperature=0.2,
    )
    dto: SuggestedDiagramOutput = res["data"]
    return {
        "model_used": res["model_used"],
        "title": dto.title or diagram_title,
        "caption": dto.caption,
        "layout": dto.layout if dto.layout in ("vertical", "horizontal") else "vertical",
        "nodes": [n.model_dump() for n in dto.nodes],
        "connections": [c.model_dump() for c in dto.connections],
    }


def suggest_section_table_with_ai(
    project: ProtocolProject,
    section_id: str,
    table_title: str,
    table_goal: str = "",
) -> Dict[str, object]:
    """Generate a structured table (headers + rows) tailored to a specific FMSB section."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    spec = _get_section_spec(section_id, lang)
    sec = project.sections.get(section_id)
    upstream_mem, _ = get_upstream_and_current_memory(project, section_id)
    lang_dir = get_language_prompt_directive(lang)
    prompt = f"""
Generate a structured table for a CAMES/FMSB medical research protocol.
{lang_dir}
Section: {spec.chapter_title} — {spec.title}
Protocol topic: {project.cover.title or project.name}
Table title: {table_title}
Goal: {table_goal or 'Clearly structure data for this section'}
Upstream working memory:
{upstream_mem[:1500]}

Current section text: {(sec.content_markdown if sec else '')[:2000]}

Return concise column `headers` and 3 to 6 realistic `rows` (use {'[To be completed]' if lang == 'en' else '[À compléter]'} if local figures are missing).
"""
    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=SuggestedTableOutput,
        temperature=0.2,
    )
    dto: SuggestedTableOutput = res["data"]
    return {
        "model_used": res["model_used"],
        "title": dto.title or table_title,
        "headers": dto.headers,
        "rows": dto.rows,
    }
