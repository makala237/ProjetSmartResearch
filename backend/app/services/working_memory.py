from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_settings
from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import ProtocolProject
from app.services.gemini_client import gemini_router

logger = logging.getLogger(__name__)

_SKIP_UPSTREAM_IDS = {
    "toc",
    "abbreviations",
    "list_of_tables",
    "list_of_figures",
}

_SECTION_BLOCK_RE = re.compile(
    r"^##\s+\[\d+\]\s+.*?<!--\s*section_id:\s*([a-zA-Z0-9_]+)\s*-->\s*$",
    re.MULTILINE,
)

_EMPTY_MARKER = "(Aucun résumé enregistré pour le moment.)"


def get_working_memory_path(project_id: str) -> Path:
    mem_dir = get_settings().storage_dir / "working_memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir / f"{project_id}_memoire_travail.md"


def load_working_memory_map(project: ProtocolProject) -> Dict[str, str]:
    """Read the persistent working-memory Markdown file into {section_id: summary}."""
    path = get_working_memory_path(project.project_id)
    if not path.exists():
        initial: Dict[str, str] = {}
        save_working_memory_file(project, initial)
        return initial

    raw = path.read_text(encoding="utf-8")
    matches = list(_SECTION_BLOCK_RE.finditer(raw))
    memory_map: Dict[str, str] = {}
    for idx, m in enumerate(matches):
        sid = m.group(1).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        body = raw[start:end].strip()
        if body and body != _EMPTY_MARKER:
            memory_map[sid] = body
    return memory_map


def save_working_memory_file(
    project: ProtocolProject,
    memory_map: Dict[str, str],
) -> Path:
    """Persist the structured Working Memory file with every FMSB section heading in order."""
    path = get_working_memory_path(project.project_id)
    title = project.cover.title or project.name
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# MÉMOIRE DE TRAVAIL — PROTOCOLE FMSB ({project.project_id})",
        f"**Thème :** {title}",
        f"**Dernière mise à jour :** {now_iso}",
        "",
    ]

    for spec in FMSB_SECTIONS:
        lines.append(f"## [{spec.order}] {spec.title} <!-- section_id: {spec.id} -->")
        summary = (memory_map.get(spec.id) or "").strip()
        lines.append(summary if summary else _EMPTY_MARKER)
        lines.append("")

    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def update_section_working_memory(
    project: ProtocolProject,
    section_id: str,
    use_decision_1: bool = True,
) -> Dict[str, Any]:
    """After a section is modified:
    1. Gemini Decision 1 evaluates whether the Working Memory summary must be updated.
    2. Gemini Decision 1 invokes Gemini 3.8 Live to produce a brief summary.
    3. The new summary overwrites the previous summary under that section's heading in memoire_travail.md.
    """
    spec = next((s for s in FMSB_SECTIONS if s.id == section_id), None)
    if spec is None:
        return {"updated": False, "reason": f"Section inconnue: {section_id}"}

    memory_map = load_working_memory_map(project)
    sec = project.sections.get(section_id)
    content = (sec.content_markdown or "").strip() if sec else ""

    if section_id == "cover_page":
        c = project.cover
        content = (
            f"Titre: {c.title or project.name}. "
            f"Auteur: {c.author_name or 'N/R'} (Mat: {c.matricule or 'N/R'}). "
            f"Département: {c.department_fr}. Directeur: {c.director or 'N/R'}."
        )

    if not content:
        if section_id in memory_map:
            del memory_map[section_id]
        fpath = save_working_memory_file(project, memory_map)
        try:
            from app.services.coherence_sentinel import evaluate_project_coherence

            evaluate_project_coherence(project, changed_section_id=section_id)
        except Exception:
            pass
        return {
            "updated": True,
            "decision_model": "section-vide",
            "live_model": "none",
            "summary": "",
            "file_path": str(fpath),
        }

    should_summarize = True
    decision_model = "direct"
    if use_decision_1:
        try:
            dres = gemini_router.run_decision_1(
                input_text=(
                    f"Section modifiée : [{spec.order}] {spec.title} ({section_id}).\n"
                    f"Longueur du nouveau contenu : {len(content.split())} mots.\n"
                    f"Résumé précédent en mémoire : {memory_map.get(section_id, '(aucun)')[:300]}"
                ),
                questions={
                    "update_working_memory": {
                        "type": "noul",
                        "instructions": (
                            "Cette section vient d'être modifiée ou rédigée. Faut-il appeler Gemini 3.8 Live "
                            "pour générer un nouveau résumé bref et écraser l'ancien dans la Mémoire de Travail ?"
                        ),
                        "criteria": {
                            "true_description": "La section contient du texte substantiel modifié : mettre à jour la mémoire de travail via Gemini 3.8 Live.",
                            "false_description": "Aucun contenu utile à résumer.",
                        },
                    }
                },
            )
            score = dres.get("scores", {}).get("update_working_memory", 1.0)
            should_summarize = score >= 0.40 or len(content.split()) >= 10
            decision_model = f"Gemini Decision 1 ({dres.get('model_used', 'models/gemini-decision-1')})"
        except Exception as exc:
            logger.warning("Decision 1 working memory fallback (%s)", exc)
            decision_model = "Gemini Decision 1 (fallback)"
            should_summarize = True

    if not should_summarize:
        return {
            "updated": False,
            "decision_model": decision_model,
            "live_model": "skipped",
            "summary": memory_map.get(section_id, ""),
            "file_path": str(get_working_memory_path(project.project_id)),
        }

    sum_res = gemini_router.summarize_section_live(
        section_title=spec.title,
        content_text=content,
        study_title=project.cover.title or project.name,
    )
    new_summary = sum_res["summary"].strip()
    # Overwrite previous summary under the section's heading
    memory_map[section_id] = new_summary
    fpath = save_working_memory_file(project, memory_map)

    try:
        from app.services.coherence_sentinel import evaluate_project_coherence

        evaluate_project_coherence(project, changed_section_id=section_id)
    except Exception:
        pass

    return {
        "updated": True,
        "decision_model": decision_model,
        "live_model": sum_res["model_used"],
        "summary": new_summary,
        "file_path": str(fpath),
    }


_INTERDEPENDENT_DOWNSTREAM_LINKS: Dict[str, List[str]] = {
    "chap1_objectifs": ["chap3_type_site_duree", "chap3_analyse_donnees"],
    "chap1_hypotheses": ["chap3_type_site_duree", "chap3_analyse_donnees"],
    "chap1_variables": ["chap3_analyse_donnees"],
    "chap3_type_site_duree": ["chap3_chronogramme", "chap3_population_echantillon"],
}


def get_upstream_and_current_memory(
    project: ProtocolProject,
    current_section_id: str,
) -> Tuple[str, int]:
    """Read `memoire_travail.md` and return summaries of sections ABOVE `current_section_id`,
    the summary of `current_section_id` itself, PLUS any already-drafted interdependent downstream sections.
    """
    memory_map = load_working_memory_map(project)
    target_spec = next((s for s in FMSB_SECTIONS if s.id == current_section_id), None)
    target_order = target_spec.order if target_spec else 999
    linked_downstream_ids = set(_INTERDEPENDENT_DOWNSTREAM_LINKS.get(current_section_id, []))

    upstream_lines: List[str] = []
    downstream_lines: List[str] = []
    current_line: Optional[str] = None
    count = 0

    for spec in FMSB_SECTIONS:
        if spec.id in _SKIP_UPSTREAM_IDS:
            continue
        is_upstream_or_self = spec.order <= target_order
        is_linked_downstream = spec.order > target_order and spec.id in linked_downstream_ids
        if not (is_upstream_or_self or is_linked_downstream):
            continue

        summary = (memory_map.get(spec.id) or "").strip()
        if not summary:
            # Instant digest fallback if a section has text not yet summarized
            sec = project.sections.get(spec.id)
            if sec and (sec.content_markdown or "").strip():
                words = sec.content_markdown.strip().split()
                if len(words) >= 12:
                    compact = re.sub(r"\s+", " ", sec.content_markdown)[:280].strip()
                    summary = f"{compact}..."

        if not summary:
            continue

        if spec.id == current_section_id:
            current_line = f"[SECTION EN COURS — {spec.title}] : {summary}"
        elif is_linked_downstream:
            count += 1
            downstream_lines.append(f"- [{spec.order}] {spec.title} : {summary}")
        else:
            count += 1
            upstream_lines.append(f"- [{spec.order}] {spec.title} : {summary}")

    blocks: List[str] = []
    if upstream_lines:
        blocks.append("RÉSUMÉS DES SECTIONS EN AMONT (À HARMONISER, NE PAS RÉPÉTER) :")
        blocks.extend(upstream_lines)
    if downstream_lines:
        blocks.append("")
        blocks.append("SECTIONS INTERDÉPENDANTES DÉJÀ RÉDIGÉES (COHÉRENCE MÉTHODOLOGIQUE À VÉRIFIER) :")
        blocks.extend(downstream_lines)
    if current_line:
        blocks.append("")
        blocks.append(current_line)

    if not blocks:
        return "Aucune section en amont n'est encore résumée dans la mémoire de travail.", 0

    return "\n".join(blocks), count


def refresh_all_working_memory(project: ProtocolProject) -> Dict[str, Any]:
    """Regenerate working memory summaries for all non-empty sections in canvas order."""
    updated_ids: List[str] = []
    last_live_model = "none"
    for spec in FMSB_SECTIONS:
        if spec.id in _SKIP_UPSTREAM_IDS:
            continue
        sec = project.sections.get(spec.id)
        has_text = bool(sec and (sec.content_markdown or "").strip())
        if spec.id == "cover_page" or has_text:
            res = update_section_working_memory(project, spec.id, use_decision_1=True)
            if res.get("updated"):
                updated_ids.append(spec.id)
                if res.get("live_model") and res["live_model"] != "none":
                    last_live_model = res["live_model"]

    fpath = get_working_memory_path(project.project_id)
    return {
        "updated_sections": updated_ids,
        "updated_count": len(updated_ids),
        "live_model": last_live_model,
        "file_path": str(fpath),
        "markdown": fpath.read_text(encoding="utf-8") if fpath.exists() else "",
    }
