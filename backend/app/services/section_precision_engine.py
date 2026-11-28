"""Four-Module Precision Drafting & Plan Validation Engine (FMSB / CAMES).

Architecture:
  - Module 1 (Learning & Plan Generation): `generate_section_writing_plan`
      Combines FMSB/CAMES norms, internal drafting guidelines, canonical protocol ledger,
      supervisor directives, and section course notes (`pedagogie_service`) using the user's
      configured `reasoning` model chain.
  - Module 2 (Plan-Driven Reasoning & Drafting): `build_plan_guidance_block`, `rectify_draft_with_plan`
      Guides drafting and Antigravity research using the explicit `SectionWritingPlan` items,
      and applies targeted rectification when Module 3 flags unmet items.
  - Module 3 (Verification & 97% Validation Gate): `validate_draft_against_plan`, `run_module3_validation_loop`
      Checks CAMES/FMSB norms (word counts, citation count, canonical coherence, item-by-item plan
      execution). If execution score < 97%, returns the annotated plan back to Module 2 for
      rectification (up to `max_retries=2`), retaining the highest-scoring version.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.data.i18n import (
    get_language_prompt_directive,
    get_localized_section_spec,
    get_project_section_spec,
    normalize_lang,
)
from app.schemas.protocol import (
    ProtocolProject,
    SectionState,
    SectionValidationItemResult,
    SectionValidationReport,
    SectionWritingPlan,
    SectionWritingPlanItem,
)
from app.services.gemini_client import gemini_router
from app.services.pedagogy_service import format_pedagogy_notes_for_section, list_course_notes
from app.services.pipeline_tracker import emit_pipeline_step


PLAN_VALIDATION_THRESHOLD_PCT = 97.0
MAX_PLAN_RECTIFICATION_RETRIES = 2


class _GeneratedPlanItemDTO(BaseModel):
    item_id: str = ""
    title: str
    objective: str = ""
    required_elements: List[str] = Field(default_factory=list)
    pedagogical_rules: List[str] = Field(default_factory=list)
    target_words: int = 120
    data_needs: List[str] = Field(default_factory=list)
    weight: float = 20.0


class _GeneratedSectionPlanDTO(BaseModel):
    summary: str = ""
    cames_norms_summary: str = ""
    items: List[_GeneratedPlanItemDTO] = Field(default_factory=list)


class _ValidationItemScoreDTO(BaseModel):
    item_id: str
    status: str = "passed"  # passed | partial | missing
    score_pct: float = 100.0
    rectification_note: str = ""


class _PlanExecutionAuditDTO(BaseModel):
    item_results: List[_ValidationItemScoreDTO] = Field(default_factory=list)
    global_observations: List[str] = Field(default_factory=list)


class _RectifiedDraftDTO(BaseModel):
    content_markdown: str
    rectification_summary: str = ""


def _count_prose_words_local(text: str) -> int:
    if not text or not text.strip():
        return 0
    lines = [
        ln
        for ln in text.splitlines()
        if not (ln.strip().startswith("|") and ln.strip().endswith("|"))
    ]
    cleaned = re.sub(r"[#*_`>\[\]\(\)]+", " ", "\n".join(lines))
    return len(re.findall(r"\b[\wÀ-ÖØ-öø-ÿ'-]+\b", cleaned))


def _count_citations_local(text: str) -> int:
    nums: set[int] = set()
    for grp in re.findall(r"\[([0-9,\s\-–]+)\]", text or ""):
        for part in re.split(r"[,]", grp):
            part = part.strip()
            if "-" in part or "–" in part:
                bounds = re.split(r"[-–]", part, maxsplit=1)
                if len(bounds) == 2 and bounds[0].strip().isdigit() and bounds[1].strip().isdigit():
                    a, b = int(bounds[0].strip()), int(bounds[1].strip())
                    if a <= b <= a + 20:
                        nums.update(range(a, b + 1))
            elif part.isdigit():
                nums.add(int(part))
    return len(nums)


def generate_section_writing_plan(
    project: ProtocolProject,
    section_id: str,
    user_instruction: Optional[str] = None,
    force_regenerate: bool = False,
) -> SectionWritingPlan:
    """Module 1: Learn CAMES/FMSB norms + course notes + internal directives and generate a weighted SectionWritingPlan."""
    sec = project.sections.get(section_id)
    if (
        not force_regenerate
        and sec is not None
        and sec.writing_plan is not None
        and sec.writing_plan.items
        and not (user_instruction or "").strip()
    ):
        return sec.writing_plan

    lang = normalize_lang(getattr(project, "language", "fr"))
    spec = get_project_section_spec(project, section_id) or get_localized_section_spec(section_id, lang=lang)
    if spec is None:
        return SectionWritingPlan(section_id=section_id, summary=section_id)

    emit_pipeline_step(
        "module1_plan",
        f"Module 1 — Apprentissage des normes, notes de cours et génération du plan ({spec.title})...",
        f"Module 1 — Learning norms & course notes to build writing plan ({spec.title})...",
        progress_pct=15,
    )

    from app.services.coherence_sentinel import extract_canonical_protocol_ledger
    from app.services.copilot_service import format_supervisor_directives_block

    notes_records = list_course_notes(section_id=section_id, include_disabled=False)
    pedagogy_digest = format_pedagogy_notes_for_section(section_id, max_chars=7500)
    pedagogy_titles = [n.title for n in notes_records]
    supervisor_block = format_supervisor_directives_block(project, section_id=section_id)
    ledger = extract_canonical_protocol_ledger(project)

    cons = spec.constraints
    min_words = int(getattr(cons, "min_words", None) or 120)
    max_words = int(getattr(cons, "max_words", None) or max(min_words + 150, 600))
    target_words = int((min_words + max_words) // 2)
    min_citations = int(getattr(cons, "min_references", None) or 0)
    table_policy = str(getattr(cons, "table_policy", None) or "optional_if_justified")

    ledger_lines = [
        f"- {k}: {v}"
        for k, v in ledger.items()
        if v is not None and str(v).strip()
    ]
    ledger_block = "\n".join(ledger_lines) or "(Aucun fait canonique verrouillé pour le moment)"

    lang_dir = get_language_prompt_directive(lang)
    prompt = f"""
Tu es le **Module 1 (Apprentissage & Génération de Plan de Rédaction)** de Smart Research.
Ta mission est d'assimiler toutes les composantes méthodologiques externes et internes pour concevoir un **plan de rédaction pondéré et opérationnel (total des poids = 100)** pour la section ci-dessous.

{lang_dir}

SECTION CIBLE : {spec.chapter_title} — {spec.title} (id={section_id})
DESCRIPTION OFFICIELLE : {spec.description}
POINTS ATTENDUS (CAMES / FMSB) :
{chr(10).join('- ' + g for g in spec.guidance_points)}

CONTRAINTES QUANTITATIVES FMSB/CAMES :
- Mots attendus : {min_words} à {max_words} mots (cible optimale ~{target_words} mots)
- Citations scientifiques minimales : {min_citations}
- Politique tableaux : {table_policy}

REGISTRE CANONIQUE DES FAITS DU PROTOCOLE (COHÉRENCE GLOBALE OBLIGATOIRE) :
{ledger_block}

{supervisor_block}

NOTES DE COURS & DIRECTIVES PÉDAGOGIQUES ASSOCIÉES À CETTE SECTION :
{pedagogy_digest if pedagogy_digest else "(Aucune note de cours spécifique chargée ; applique les standards méthodologiques FMSB/CAMES de référence.)"}

CONSIGNE UTILISATEUR ÉVENTUELLE : {(user_instruction or '').strip() or 'Aucune'}

EXIGENCES DE SORTIE :
- Découpe la rédaction en 3 à 6 sous-blocs (`items`) logiques, ordonnés, chacun avec un `item_id` court (`item_1`, `item_2`, ...), un `title` précis, un `objective`, les `required_elements` précis à vérifier, les `pedagogical_rules` tirées des notes de cours, un `target_words` réaliste, les `data_needs` à rechercher par Antigravity/bibliothèque interne, et un `weight` tel que la somme des poids fasse 100.
"""
    model_used = "deterministic-fallback-plan"
    plan_items: List[SectionWritingPlanItem] = []
    summary = spec.description
    cames_summary = (
        f"{min_words}-{max_words} mots | ≥ {min_citations} citations | {table_policy}"
    )

    try:
        res = gemini_router.generate_structured(
            role="reasoning",
            contents=prompt,
            schema=_GeneratedSectionPlanDTO,
            temperature=0.18,
        )
        dto: _GeneratedSectionPlanDTO = res["data"]
        model_used = str(res.get("model_used") or "reasoning")
        if (dto.summary or "").strip():
            summary = dto.summary.strip()
        if (dto.cames_norms_summary or "").strip():
            cames_summary = dto.cames_norms_summary.strip()
        raw_items = [it for it in (dto.items or []) if (it.title or "").strip()]
        if raw_items:
            total_w = sum(max(1.0, float(it.weight or 0.0)) for it in raw_items)
            for idx, it in enumerate(raw_items, start=1):
                norm_w = round((max(1.0, float(it.weight or 1.0)) / total_w) * 100.0, 2)
                plan_items.append(
                    SectionWritingPlanItem(
                        item_id=(it.item_id or f"item_{idx}").strip(),
                        title=it.title.strip(),
                        objective=(it.objective or "").strip(),
                        required_elements=[e.strip() for e in (it.required_elements or []) if e and e.strip()],
                        pedagogical_rules=[r.strip() for r in (it.pedagogical_rules or []) if r and r.strip()],
                        target_words=max(40, int(it.target_words or (target_words // max(1, len(raw_items))))),
                        data_needs=[d.strip() for d in (it.data_needs or []) if d and d.strip()],
                        weight=norm_w,
                    )
                )
    except Exception:
        pass

    # Deterministic fallback if AI routing unavailable or returned empty items
    if not plan_items:
        guides = list(spec.guidance_points or [spec.description or spec.title])
        w_each = round(100.0 / max(1, len(guides)), 2)
        words_each = max(50, int(target_words // max(1, len(guides))))
        for idx, gp in enumerate(guides, start=1):
            plan_items.append(
                SectionWritingPlanItem(
                    item_id=f"item_{idx}",
                    title=gp[:100],
                    objective=gp,
                    required_elements=[gp],
                    pedagogical_rules=pedagogy_titles[:3],
                    target_words=words_each,
                    data_needs=[gp],
                    weight=w_each,
                )
            )

    plan = SectionWritingPlan(
        section_id=section_id,
        summary=summary,
        items=plan_items,
        cames_norms_summary=cames_summary,
        pedagogy_sources_used=pedagogy_titles,
        min_words=min_words,
        max_words=max_words,
        min_citations=min_citations,
        model_used=model_used,
        generated_at=datetime.now(timezone.utc),
    )
    if sec is not None:
        sec.writing_plan = plan
    return plan


def build_plan_guidance_block(
    plan: Optional[SectionWritingPlan],
    rectification_instructions: Optional[List[str]] = None,
) -> str:
    """Format Module 1 plan (and optional Module 3 rectification feedback) for Module 2 drafting."""
    if plan is None or not plan.items:
        return ""
    lines = [
        "=== PLAN DE RÉDACTION STRICT (ISSU DU MODULE 1 : NORMES CAMES/FMSB + NOTES DE COURS) ===",
        f"Synthèse : {plan.summary}",
        f"Normes : {plan.cames_norms_summary} (Cible : {plan.min_words}-{plan.max_words} mots, ≥ {plan.min_citations} citations)",
    ]
    if plan.pedagogy_sources_used:
        lines.append("Notes de cours intégrées : " + ", ".join(plan.pedagogy_sources_used))
    lines.append("SOUS-BLOCS OBLIGATOIRES À EXÉCUTER INTÉGRALEMENT :")
    for idx, it in enumerate(plan.items, start=1):
        lines.append(f"{idx}. [{it.item_id} · Poids {it.weight:.0f}% · ~{it.target_words} mots] {it.title}")
        if it.objective:
            lines.append(f"   - Objectif : {it.objective}")
        if it.required_elements:
            lines.append("   - Éléments obligatoires : " + " ; ".join(it.required_elements))
        if it.pedagogical_rules:
            lines.append("   - Règles pédagogiques (cours) : " + " ; ".join(it.pedagogical_rules))
        if it.data_needs:
            lines.append("   - Besoins factuels / bibliographiques : " + " ; ".join(it.data_needs))

    if rectification_instructions:
        lines.append("\nPOINTS PRÉCIS À RECTIFIER SIGNALÉS PAR LE MODULE 3 :")
        for r in rectification_instructions:
            lines.append(f"  * {r}")
    return "\n".join(lines)


def validate_draft_against_plan(
    project: ProtocolProject,
    section_id: str,
    plan: SectionWritingPlan,
    draft_md: str,
    attempt: int = 1,
) -> SectionValidationReport:
    """Module 3: Evaluate deterministic CAMES norms + semantic plan adherence (0..100%)."""
    from app.services.drafting import deterministic_section_checks

    word_count = _count_prose_words_local(draft_md)
    citation_count = _count_citations_local(draft_md)
    det_issues = deterministic_section_checks(project, section_id, draft_md)

    # Penalty calculation on deterministic norms (max 15 pts penalty)
    det_penalty = 0.0
    if plan.min_words > 0 and word_count < int(plan.min_words * 0.85):
        det_issues.append(
            f"Volume insuffisant vis-à-vis du plan ({word_count} mots vs minimum attendu {plan.min_words})."
        )
        det_penalty += 6.0
    if plan.max_words > 0 and word_count > int(plan.max_words * 1.18):
        det_issues.append(
            f"Volume excessif vis-à-vis du plan ({word_count} mots vs maximum {plan.max_words})."
        )
        det_penalty += 4.0
    if plan.min_citations > 0 and citation_count < plan.min_citations:
        det_issues.append(
            f"Nombre de références citées insuffisant ({citation_count} vs minimum {plan.min_citations})."
        )
        det_penalty += 6.0

    # Extra penalty for factual/deterministic audit issues flagged by deterministic_section_checks
    for di in det_issues[:3]:
        low = di.lower()
        if "placeholder" in low or "compléter" in low or "incongru" in low:
            det_penalty += 2.0
    det_penalty = min(20.0, det_penalty)

    plan_outline = "\n".join(
        f"- {it.item_id} ({it.weight:.1f} pts) — {it.title} | Attendus: {' ; '.join(it.required_elements)}"
        for it in plan.items
    )
    lang = normalize_lang(getattr(project, "language", "fr"))
    audit_prompt = f"""
Tu es le **Module 3 (Vérification et Validation du Plan de Rédaction)** (standards FMSB/CAMES).
Évalue rigoureusement le degré d'exécution de CHAQUE sous-bloc du plan du Module 1 dans le texte rédigé ci-dessous.

SECTION : {section_id}
LANGUE ATTENDUE : {"English" if lang == "en" else "Français"}
MÉTRIQUES ACTUELLES : {word_count} mots (cible {plan.min_words}-{plan.max_words}), {citation_count} citations uniques (min {plan.min_citations}).

PLAN DU MODULE 1 À VÉRIFIER :
{plan_outline}

TEXTE RÉDIGÉ À ÉVALUER :
{draft_md}

RÈGLES DE NOTATION STRICTE :
- Pour CHAQUE `item_id` du plan, attribue un `score_pct` de 0 à 100 et un `status` :
  * `"passed"` (95–100) uniquement si TOUS les éléments obligatoires et règles pédagogiques de l'item sont clairement et scientifiquement traités.
  * `"partial"` (50–94) si l'item est survolé, trop vague ou incomplet ; précise EXACTEMENT ce qu'il faut rectifier dans `rectification_note`.
  * `"missing"` (0–49) si l'item est absent ou contredit ; précise EXACTEMENT quoi rédiger dans `rectification_note`.
"""
    item_results: List[SectionValidationItemResult] = []
    model_used = "deterministic-validator"
    try:
        res = gemini_router.generate_structured(
            role="audit",
            contents=audit_prompt,
            schema=_PlanExecutionAuditDTO,
            temperature=0.1,
        )
        dto: _PlanExecutionAuditDTO = res["data"]
        model_used = str(res.get("model_used") or "audit")
        by_id = {r.item_id.strip(): r for r in (dto.item_results or []) if r.item_id}
        for it in plan.items:
            matched = by_id.get(it.item_id)
            if matched:
                sc = max(0.0, min(100.0, float(matched.score_pct)))
                st = matched.status if matched.status in ("passed", "partial", "missing") else (
                    "passed" if sc >= 95 else ("partial" if sc >= 50 else "missing")
                )
                item_results.append(
                    SectionValidationItemResult(
                        item_id=it.item_id,
                        title=it.title,
                        status=st,
                        score_pct=sc,
                        rectification_note=(matched.rectification_note or "").strip(),
                    )
                )
    except Exception:
        item_results = []

    if not item_results:
        # Deterministic lexical coverage fallback if LLM validator fails
        low_text = (draft_md or "").lower()
        for it in plan.items:
            tokens = [w for w in re.findall(r"[a-zà-ÿ]{4,}", (it.title + " " + " ".join(it.required_elements)).lower())]
            hits = sum(1 for tok in set(tokens) if tok in low_text)
            cov = (hits / max(1, len(set(tokens)))) * 100.0 if tokens else 85.0
            sc = round(min(100.0, max(50.0, cov * 1.15)), 1)
            st = "passed" if sc >= 95.0 else "partial"
            item_results.append(
                SectionValidationItemResult(
                    item_id=it.item_id,
                    title=it.title,
                    status=st,
                    score_pct=sc,
                    rectification_note="" if st == "passed" else f"Approfondir : {it.title}",
                )
            )

    weight_map = {it.item_id: max(1.0, float(it.weight or 1.0)) for it in plan.items}
    total_w = sum(weight_map.get(r.item_id, 1.0) for r in item_results) or 1.0
    weighted_semantic = sum(r.score_pct * weight_map.get(r.item_id, 1.0) for r in item_results) / total_w
    final_score = round(max(0.0, min(100.0, weighted_semantic - det_penalty)), 2)

    rectifications: List[str] = []
    for ir in item_results:
        if ir.score_pct < PLAN_VALIDATION_THRESHOLD_PCT or ir.status != "passed":
            note = ir.rectification_note or f"Compléter selon les attendus du plan."
            rectifications.append(f"[{ir.item_id} — {ir.title} ({ir.score_pct:.0f}%)] : {note}")
    for di in det_issues:
        if di not in rectifications:
            rectifications.append(f"[Norme CAMES/FMSB] : {di}")

    passed = final_score >= PLAN_VALIDATION_THRESHOLD_PCT and len(rectifications) == 0
    # Allow pass cleanly if weighted score >= 97.0 even with minor stylistic advisory notes
    if final_score >= PLAN_VALIDATION_THRESHOLD_PCT and det_penalty == 0.0:
        passed = True

    return SectionValidationReport(
        execution_score_pct=final_score,
        threshold_pct=PLAN_VALIDATION_THRESHOLD_PCT,
        passed=passed,
        attempts=attempt,
        word_count=word_count,
        citation_count=citation_count,
        deterministic_issues=det_issues,
        item_results=item_results,
        rectification_instructions=rectifications,
        model_used=model_used,
        validated_at=datetime.now(timezone.utc),
    )


def rectify_draft_with_plan(
    project: ProtocolProject,
    section_id: str,
    plan: SectionWritingPlan,
    draft_md: str,
    report: SectionValidationReport,
    refs_ctx: str = "",
) -> Tuple[str, str]:
    """Module 2 targeted rectification pass triggered when Module 3 score < 97%."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    plan_block = build_plan_guidance_block(plan, rectification_instructions=report.rectification_instructions)
    prompt = f"""
Tu es le **Module 2 (Raisonnement & Rédaction)** de Smart Research.
Le **Module 3 (Validation)** a évalué la rédaction ci-dessous à **{report.execution_score_pct:.1f} %** (seuil exigé : {PLAN_VALIDATION_THRESHOLD_PCT:.0f} %).

MISSION DE RECTIFICATION CIBLÉE :
1. Corrige et enrichis PRÉCISÉMENT chaque point signalé par le Module 3 ci-dessous, en respectant strictement le plan du Module 1 et les notes de cours.
2. Conserve intégralement les passages déjà conformes et toutes les citations Vancouver `[n]` valides sans inventer de fausses références ni statistiques.
3. Maintiens le volume dans la fourchette {plan.min_words}-{plan.max_words} mots avec au moins {plan.min_citations} citations valides.
4. Rédige en {"English" if lang == "en" else "Français"}.

{plan_block}

RÉFÉRENCES DISPONIBLES :
{refs_ctx or '(Utiliser uniquement les [n] déjà fournies dans le texte.)'}

TEXTE ACTUEL À RECTIFIER :
{draft_md}
"""
    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=_RectifiedDraftDTO,
        temperature=0.22,
    )
    dto: _RectifiedDraftDTO = res["data"]
    updated = (dto.content_markdown or "").strip() or draft_md
    return updated, str(res.get("model_used") or "")


def run_module3_validation_loop(
    project: ProtocolProject,
    section_id: str,
    plan: SectionWritingPlan,
    initial_draft_md: str,
    refs_ctx: str = "",
    threshold_pct: float = PLAN_VALIDATION_THRESHOLD_PCT,
    max_retries: int = MAX_PLAN_RECTIFICATION_RETRIES,
) -> Tuple[str, SectionValidationReport]:
    """Execute Module 3 validation against Module 1 plan; loop back to Module 2 while score < 97% (up to max_retries)."""
    best_md = initial_draft_md
    emit_pipeline_step(
        "module3_validate",
        "Module 3 — Contrôle d'exécution du plan et des normes CAMES (seuil 97 %)...",
        "Module 3 — Checking plan execution & CAMES compliance (97% threshold)...",
        progress_pct=84,
    )
    best_report = validate_draft_against_plan(project, section_id, plan, initial_draft_md, attempt=1)

    if best_report.execution_score_pct >= threshold_pct and best_report.passed:
        return best_md, best_report

    current_md = initial_draft_md
    current_report = best_report

    for retry in range(1, max_retries + 1):
        emit_pipeline_step(
            "module3_rectify",
            f"Module 3 ({current_report.execution_score_pct:.1f}% < 97%) → Retour au Module 2 (rectification {retry}/{max_retries})...",
            f"Module 3 ({current_report.execution_score_pct:.1f}% < 97%) → Returning to Module 2 (pass {retry}/{max_retries})...",
            progress_pct=84 + retry * 3,
        )
        try:
            rectified_md, _ = rectify_draft_with_plan(
                project=project,
                section_id=section_id,
                plan=plan,
                draft_md=current_md,
                report=current_report,
                refs_ctx=refs_ctx,
            )
        except Exception:
            break
        if not rectified_md or rectified_md.strip() == current_md.strip():
            break

        next_report = validate_draft_against_plan(
            project, section_id, plan, rectified_md, attempt=retry + 1
        )
        if next_report.execution_score_pct >= best_report.execution_score_pct:
            best_md = rectified_md
            best_report = next_report
        current_md = rectified_md
        current_report = next_report
        if best_report.execution_score_pct >= threshold_pct and best_report.passed:
            break

    sec = project.sections.get(section_id)
    if sec is not None:
        sec.last_validation = best_report
    return best_md, best_report
