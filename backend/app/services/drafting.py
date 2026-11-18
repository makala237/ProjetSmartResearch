import re
from typing import Dict, List, Optional
import uuid
from pydantic import BaseModel, Field

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import (
    DiagramConnection,
    DiagramNode,
    ProtocolProject,
    SectionDiagram,
    SectionTable,
    StepStatus,
)
from app.services.gemini_client import gemini_router
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


def _get_section_spec(section_id: str):
    for s in FMSB_SECTIONS:
        if s.id == section_id:
            return s
    raise ValueError(f"Section inconnue : {section_id}")


def _format_cames_granularity_block(spec) -> str:
    """Format CAMES/FMSB granular section expectations (concepts, volume, refs, tables/figures)."""
    c = spec.constraints
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
    notes: List[str] = []
    spec = _get_section_spec(section_id)
    c = spec.constraints
    words = len(text.split()) if text.strip() else 0

    if c.required and words == 0:
        notes.append("Section vide : un contenu est requis par le canevas CAMES/FMSB.")

    if words > 0:
        if c.min_words and words < c.min_words:
            notes.append(
                f"Volume insuffisant selon CAMES/FMSB : {words} mots (minimum recommandé : {c.min_words} mots)."
            )
        if c.max_words and words > c.max_words and section_id not in ("intro_contexte", "intro_interet"):
            notes.append(
                f"Dépassement de volume CAMES/FMSB : {words} mots (maximum recommandé : {c.max_words} mots)."
            )

    # Check per-section citation bounds (excluding intro which shares a global 5-ref rule and references list itself)
    if words > 0 and section_id not in ("intro_contexte", "intro_interet", "references", "toc", "abbreviations", "list_of_tables", "list_of_figures"):
        cited_nums = set(re.findall(r"\[(\d+)\]", text))
        if c.min_references is not None and c.min_references > 0 and len(cited_nums) < c.min_references:
            notes.append(
                f"Citations insuffisantes ({len(cited_nums)} réf. citées) : le référentiel CAMES attend au moins {c.min_references} référence(s) [n] dans cette section."
            )
        if c.max_references is not None and len(cited_nums) > c.max_references:
            notes.append(
                f"Trop de citations ({len(cited_nums)} réf. citées) : maximum autorisé de {c.max_references} référence(s) dans cette section."
            )

    # Check table policy
    if c.table_policy == "forbidden":
        sec_obj = project.sections.get(section_id)
        has_raw_table = bool(re.search(r"(?m)^\s*\|[\s:\-|]+\|\s*$", text or ""))
        if has_raw_table or (sec_obj and getattr(sec_obj, "custom_tables", None)):
            notes.append(
                "Politique CAMES : aucun tableau n'est attendu dans cette section (supprimer les tableaux superflus)."
            )

    # Introduction rule: max 2 pages (~900 words) and max 5 references across intro sections
    if section_id in ("intro_contexte", "intro_interet"):
        intro_text = " ".join(
            [
                project.sections.get("intro_contexte").content_markdown
                if project.sections.get("intro_contexte")
                else "",
                project.sections.get("intro_interet").content_markdown
                if project.sections.get("intro_interet")
                else "",
            ]
        )
        # If checking current draft before saving, swap with candidate text
        if section_id == "intro_contexte":
            other = project.sections.get("intro_interet").content_markdown if project.sections.get("intro_interet") else ""
            intro_text = f"{text} {other}"
        else:
            other = project.sections.get("intro_contexte").content_markdown if project.sections.get("intro_contexte") else ""
            intro_text = f"{other} {text}"

        intro_words = len(intro_text.split())
        if intro_words > 900:
            notes.append(
                f"Alerte volume Introduction : ~{intro_words} mots au total (cible ≤ 2 pages ≈ 800-900 mots)."
            )
        cited_nums = set(re.findall(r"\[(\d+)\]", intro_text))
        if len(cited_nums) > 5:
            notes.append(
                f"Alerte FMSB Introduction : {len(cited_nums)} références citées ({sorted(cited_nums)}) alors que le maximum autorisé est 5."
            )

    if section_id == "chap1_hypotheses" and text.strip().endswith("?"):
        notes.append("L'hypothèse principale doit être formulée à la forme affirmative (pas sous forme de question).")

    return notes


def draft_section_with_ai(
    project: ProtocolProject,
    section_id: str,
    user_instruction: Optional[str] = None,
) -> Dict[str, object]:
    """Draft or refine a section grounded strictly in approved ArticleSegments and FMSB rules."""
    spec = _get_section_spec(section_id)
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
                "assistant_commentary": "Section liminaire synchronisée automatiquement selon l'ordre officiel FMSB.",
                "deterministic_notes": [],
            }

    current_sec = project.sections.get(section_id)
    current_text = current_sec.content_markdown if current_sec else ""

    # Preliminary Tri-Engine Orchestration:
    # Memory (Gemini Flash) -> Check initial text -> Gemini Decision 1 -> Agent Antigravity (Vancouver + Images)
    orch = orchestrate_section_action(
        project=project,
        section_id=section_id,
        action="draft",
        user_instruction=user_instruction,
    )
    orch_trace = orch["orchestration_trace"]
    cross_memory = orch["cross_section_memory"]

    # Gather approved segments targeting this section (including any newly produced by Antigravity)
    ref_by_id = {r.ref_id: r for r in project.references}
    matching_segments = [
        seg
        for seg in project.article_segments
        if seg.approved and section_id in seg.target_section_ids and seg.ref_id in ref_by_id
    ]

    if matching_segments:
        segments_context = "\n".join(
            f"- Réf [{ref_by_id[s.ref_id].order_index}] ({ref_by_id[s.ref_id].authors[0] if ref_by_id[s.ref_id].authors else ''}, {ref_by_id[s.ref_id].year or ''}) | Portée={s.epidemiology_scope or 'N/A'} : {s.summary} | Données exactes: {s.key_quote_or_data}"
            for s in matching_segments[:15]
        )
    else:
        # Fallback: provide available reference list summaries if segmentation hasn't targeted this section
        segments_context = "\n".join(
            f"- Réf [{r.order_index}] {r.title} ({r.year or ''}): {r.abstract[:280]}"
            for r in project.references[:6]
        ) or "Aucune référence segmentée pour cette section pour le moment."

    mode_priority_rule = (
        "REGLE PRIORITAIRE (TEXTE INITIAL PRESENT) : Un brouillon ou contenu existe déjà ci-dessous. "
        "Raisonne EN PRIORITE sur ce texte existant pour l'améliorer, le préciser et atteindre les objectifs FMSB sans effacer l'acquis valide."
        if orch_trace["initial_text_present"]
        else "SECTION INITIALEMENT VIDE : Rédige une proposition complète et rigoureuse en t'appuyant sur les références apportées par Antigravity."
    )

    cames_block = _format_cames_granularity_block(spec)

    prompt = f"""
Tu es un directeur de recherche médicale à la FMSB (Université de Yaoundé I), expert du référentiel CAMES / CADMEF.
Rédige (ou améliore) la section suivante du protocole de recherche :

SECTION CIBLE : {spec.chapter_title} — {spec.title}
DESCRIPTION : {spec.description}
ATTENDUS FMSB :
{chr(10).join('- ' + g for g in spec.guidance_points)}

{cames_block}

{mode_priority_rule}

SUJET DE L'ETUDIANT :
- Titre : {project.cover.title or project.name}
- Type d'étude : {project.study_type}
- Idée / Contexte : {project.research_idea}

MEMOIRE TRANSVERSALE DES AUTRES SECTIONS (NE PAS RE-EXPLIQUER CE QUI EST DEJA ECRIT AILLEURS) :
{cross_memory}

BROUILLON ACTUEL DE CETTE SECTION :
{current_text if current_text.strip() else '(Vide)'}

INSTRUCTION / RETOUR DE L'ETUDIANT :
{user_instruction or 'Rédige une proposition complète, structurée et académique pour cette section.'}

SEGMENTS SCIENTIFIQUES VERIFIES DISPONIBLES (dont références Vancouver préparées par Antigravity) :
{segments_context}

REGLES ANTI-HALLUCINATION ET ANTI-REDONDANCE STRICTES :
1. N'invente AUCUNE statistique, AUCUN taux, AUCUNE référence bibliographique.
2. Cite uniquement les références fournies ci-dessus sous la forme [n] (ex: [1], [2]).
3. Ne répète JAMAIS une explication, définition ou statistique déjà exposée dans la MEMOIRE TRANSVERSALE ci-dessus.
4. Si une donnée locale précise (ex. effectif d'un hôpital ou taux local manquant) n'est pas dans les sources, écris explicitement [À compléter par l'investigateur : ...].
5. Respecte un style scientifique médical sobre en français, prêt pour un protocole FMSB.
{STRUCTURED_TOOLS_INSTRUCTIONS}
"""
    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=DraftSectionOutput,
        temperature=0.25,
    )
    dto: DraftSectionOutput = res["data"]

    if spec.constraints.table_policy == "forbidden" and "tableau" not in (user_instruction or "").lower():
        dto.suggested_tables = []

    order_to_ref_id = {r.order_index: r.ref_id for r in project.references if r.order_index}
    cited_ref_ids = [
        order_to_ref_id[idx] for idx in dto.cited_order_indices if idx in order_to_ref_id
    ]
    clean_md = strip_ascii_diagrams(dto.content_markdown)
    clean_html = markdown_to_clean_html(clean_md)
    det_notes = deterministic_section_checks(project, section_id, clean_md)

    if current_sec is not None:
        current_sec.content_markdown = clean_md
        current_sec.content_html = clean_html
        current_sec.status = StepStatus.IN_PROGRESS
        current_sec.cited_ref_ids = cited_ref_ids
        current_sec.audit_notes = det_notes
        _append_structured_elements(current_sec, dto)

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


def audit_section_with_ai(project: ProtocolProject, section_id: str) -> Dict[str, object]:
    """Run Tri-Engine orchestration + deterministic checks + Gemini audit role on a section."""
    spec = _get_section_spec(section_id)
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
        extra_rec = (
            f"Antigravity a préparé {orch_trace['added_references_count']} référence(s) Vancouver pour cette section. "
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

    cames_block = _format_cames_granularity_block(spec)

    prompt = f"""
Évalue la conformité méthodologique CAMES / FMSB de cette section de protocole de recherche.
Section : {spec.chapter_title} — {spec.title}
Attendus FMSB : {spec.guidance_points}

{cames_block}

MÉMOIRE DES AUTRES SECTIONS (détecter toute redondance ou répétition inutile) :
{cross_memory[:1800]}

Texte rédigé :
{text}

Vérifie : développement des concepts CAMES obligatoires, respect des bornes de mots/références, conformité de la politique tableaux/figures, présence de citations [n], absence de répétition avec les autres sections.
"""
    res = gemini_router.generate_structured(
        role="audit",
        contents=prompt,
        schema=AuditOutput,
        temperature=0.1,
    )
    dto: AuditOutput = res["data"]
    all_issues = det_notes + [i for i in dto.issues if i not in det_notes]
    is_compliant = bool(dto.compliant and len(all_issues) == 0)
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
    spec = _get_section_spec(section_id)
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

    cames_block = _format_cames_granularity_block(spec)

    prompt = f"""
L'étudiant t'autorise à corriger les manquements signalés par l'audit CAMES/FMSB sur cette section.

SECTION : {spec.chapter_title} — {spec.title}
ATTENDUS FMSB : {spec.guidance_points}

{cames_block}

MANQUEMENTS SIGNALES A CORRIGER :
{chr(10).join('- ' + i for i in issues) if issues else '- Améliorer la conformité FMSB globale'}

MEMOIRE TRANSVERSALE DES AUTRES SECTIONS (ne pas dupliquer ailleurs) :
{cross_memory[:1800]}

TEXTE ACTUEL :
{current_text or '(Vide)'}

REFERENCES DISPONIBLES :
{refs_ctx or 'Aucune'}

Consignes :
- Raisonne en priorité sur le texte actuel pour corriger précisément chaque manquement signalé tout en conservant les éléments déjà valides et les citations [n] exactes.
- N'invente aucune donnée chiffrée ni aucune référence.
{STRUCTURED_TOOLS_INSTRUCTIONS}
"""
    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=DraftSectionOutput,
        temperature=0.2,
    )
    dto: DraftSectionOutput = res["data"]
    if spec.constraints.table_policy == "forbidden":
        dto.suggested_tables = []
    clean_md = strip_ascii_diagrams(dto.content_markdown)
    clean_html = markdown_to_clean_html(clean_md)
    det_notes = deterministic_section_checks(project, section_id, clean_md)

    if sec is not None:
        sec.content_markdown = clean_md
        sec.content_html = clean_html
        if len(det_notes) == 0:
            sec.status = StepStatus.VALIDATED
            sec.audit_notes = []
        else:
            sec.status = StepStatus.IN_PROGRESS
            sec.audit_notes = det_notes
        _append_structured_elements(sec, dto)

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
    spec = _get_section_spec(section_id)
    sec = project.sections.get(section_id)
    upstream_mem, _ = get_upstream_and_current_memory(project, section_id)
    prompt = f"""
Conçois un schéma conceptuel ou diagramme de flux structuré pour un protocole médical FMSB.
Section : {spec.chapter_title} — {spec.title}
Sujet du protocole : {project.cover.title or project.name}
Titre souhaité du schéma : {diagram_title}
Objectif du schéma : {diagram_goal or 'Modéliser clairement les relations / étapes de cette section'}
Mémoire de travail en amont (pour harmonie) :
{upstream_mem[:1500]}

Texte actuel de la section : {(sec.content_markdown if sec else '')[:2500]}

Renvoie des nœuds (`id` courts ex: "n1", "n2", `shape`: "rounded"|"rectangle"|"circle"|"diamond", `title`, `items` concis) et des `connections` (`from_id`, `to_id`, `label`).
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
    spec = _get_section_spec(section_id)
    sec = project.sections.get(section_id)
    upstream_mem, _ = get_upstream_and_current_memory(project, section_id)
    prompt = f"""
Génère un tableau structuré pertinent pour un protocole médical FMSB.
Section : {spec.chapter_title} — {spec.title}
Sujet du protocole : {project.cover.title or project.name}
Titre souhaité du tableau : {table_title}
Précision de l'étudiant : {table_goal or 'Structurer clairement les données de cette section'}
Mémoire de travail en amont (pour harmonie) :
{upstream_mem[:1500]}

Texte actuel de la section : {(sec.content_markdown if sec else '')[:2000]}

Renvoie des colonnes concises (`headers`) et 3 à 6 lignes (`rows`) réalistes et rigoureuses (utilise [À compléter] si une valeur locale manque).
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
