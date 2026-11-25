"""Conversational Section AI Command Router for Module 1 Assistant IA bar.

Allows the user to freely converse with the section AI input to:
  - Create & insert a structured SectionTable (auto-syncing `timeline_grid` / `budget` when relevant)
  - Create & insert a publication-grade vector SectionDiagram
  - Search & insert an open-access medical figure/image (Wikimedia Commons)
  - Or delegate cleanly to Smart Selective Reformulation for prose editing
"""

import logging
from pathlib import Path
import re
from typing import Any, Dict, Optional
import uuid

import httpx

from app.core.config import get_settings
from app.schemas.protocol import (
    DiagramConnection,
    DiagramNode,
    ImageAsset,
    ProtocolProject,
    SectionDiagram,
    SectionTable,
)

logger = logging.getLogger(__name__)

_TABLE_INTENT_RE = re.compile(
    r"\b(?:cr[ée]e[rz]?|g[ée]n[èe]re[rz]?|ajoute[rz]?|ins[èe]re[rz]?|fais|faire|construi[st]|produi[st]|create|generate|add|insert|build|make)\b"
    r".{0,45}\b(?:tableau|table|chronogramme|gantt|budget|matrice)\b"
    r"|\b(?:tableau|table)\s+(?:du|de|des|pour|sur|of|for)\b",
    re.IGNORECASE,
)

_DIAGRAM_INTENT_RE = re.compile(
    r"\b(?:cr[ée]e[rz]?|g[ée]n[èe]re[rz]?|ajoute[rz]?|ins[èe]re[rz]?|dessine[rz]?|fais|faire|create|generate|add|insert|draw|build|make)\b"
    r".{0,45}\b(?:sch[ée]ma|diagramme|organigramme|flowchart|prisma|consort|mod[èe]le\s+conceptuel|arbre\s+d[ée]cisionnel|flux)\b",
    re.IGNORECASE,
)

_IMAGE_INTENT_RE = re.compile(
    r"\b(?:cherche[rz]?|trouve[rz]?|ins[èe]re[rz]?|ajoute[rz]?|t[ée]l[ée]charge[rz]?|search|find|look\s+for|fetch|insert|add)\b"
    r".{0,45}\b(?:image|photo|photographie|illustration|figure\s+m[ée]dicale|anatomie|picture)\b",
    re.IGNORECASE,
)


def classify_section_command_intent(instruction: str) -> str:
    """Deterministically classify free-form section instruction into a concrete action."""
    text = (instruction or "").strip()
    if not text:
        return "delegate_reformulate"
    # Check diagram before image so "schéma / figure conceptuelle" goes to vector diagram
    if _DIAGRAM_INTENT_RE.search(text):
        return "create_diagram"
    if _IMAGE_INTENT_RE.search(text):
        return "search_image"
    if _TABLE_INTENT_RE.search(text):
        return "create_table"
    return "delegate_reformulate"


def _extract_clean_subject(instruction: str, fallback: str) -> str:
    cleaned = re.sub(
        r"^(?:s['’]il\s+te\s+pla[îi]t\s*,?\s*|stp\s*,?\s*|please\s*,?\s*)?"
        r"(?:peux-tu\s+|veux-tu\s+|merci\s+de\s+)?"
        r"(?:cr[ée]e[rz]?|g[ée]n[èe]re[rz]?|ajoute[rz]?|ins[èe]re[rz]?|cherche[rz]?|trouve[rz]?|fais|faire|create|generate|add|insert|search|find)\s+"
        r"(?:moi\s+|nous\s+)?(?:un|une|le|la|les|des|a|an|the)?\s*"
        r"(?:tableau|table|sch[ée]ma|diagramme|organigramme|image|photo|illustration|figure)\s*"
        r"(?:du|de\s+la|de\s+l['’]|des|de|d['’]|sur|pour|of|about|for)?\s*",
        "",
        (instruction or "").strip(),
        flags=re.IGNORECASE,
    ).strip(" .?!:")
    if len(cleaned) >= 3:
        return cleaned[0].upper() + cleaned[1:]
    return fallback


def execute_section_assistant_command(
    project: ProtocolProject,
    section_id: str,
    instruction: str,
    lang: str = "fr",
    mirror_upload_cb: Optional[Any] = None,
    action_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a conversational section command (table, diagram, image) or delegate to reformulation."""
    is_en = (lang or "fr").strip().lower() == "en"
    sec = project.sections.get(section_id)
    if sec is None:
        raise ValueError("Section inconnue.")

    override_clean = (action_override or "").strip().lower()
    if override_clean in ("create_table", "create_diagram", "search_image", "delegate_reformulate"):
        intent = override_clean
    else:
        intent = classify_section_command_intent(instruction)
    if intent == "delegate_reformulate":
        return {"action_type": "delegate_reformulate", "message": "", "mutated": False}

    if intent == "create_table":
        from app.services.drafting import suggest_section_table_with_ai, sync_timeline_from_section

        default_title = "Chronogramme des activités" if section_id == "chap3_chronogramme" else (
            "Table" if is_en else "Tableau récapitulatif"
        )
        title_hint = _extract_clean_subject(instruction, default_title)
        res = suggest_section_table_with_ai(
            project,
            section_id=section_id,
            table_title=title_hint,
            table_goal=instruction,
        )
        tbl = SectionTable(
            table_id=f"tbl_{uuid.uuid4().hex[:8]}",
            title=str(res.get("title") or title_hint).strip(),
            anchor_hint="",
            headers=[str(h) for h in (res.get("headers") or [])],
            rows=[[str(c) for c in r] for r in (res.get("rows") or [])],
        )
        sec.custom_tables.append(tbl)
        extra_sync = ""
        if section_id == "chap3_chronogramme":
            if sync_timeline_from_section(project, overwrite=True):
                extra_sync = (
                    " (synced with the interactive Gantt timeline)"
                    if is_en
                    else " (synchronisé avec le chronogramme Gantt interactif)"
                )
        elif section_id == "chap3_budget":
            from app.services.budget_engine import sync_budget_from_section
            from app.services.presentation_orchestrator import refresh_budget_slide_if_present

            sync_budget_from_section(project, overwrite=True)
            refresh_budget_slide_if_present(project)
            extra_sync = (
                " (synced with the structured study budget)"
                if is_en
                else " (synchronisé avec le budget structuré)"
            )

        msg = (
            f"Table « {tbl.title} » created and added to this section ({len(tbl.headers)} columns, {len(tbl.rows)} rows){extra_sync}."
            if is_en
            else f"Tableau « {tbl.title} » généré et ajouté dans cette section ({len(tbl.headers)} colonnes, {len(tbl.rows)} lignes){extra_sync}."
        )
        return {
            "action_type": "table_created",
            "message": msg,
            "model_used": str(res.get("model_used") or ""),
            "mutated": True,
        }

    if intent == "create_diagram":
        from app.services.diagram_renderer import VALID_DIAGRAM_LAYOUTS, VALID_DIAGRAM_SHAPES
        from app.services.drafting import suggest_section_diagram_with_ai

        title_hint = _extract_clean_subject(
            instruction,
            "Conceptual Diagram" if is_en else "Schéma conceptuel",
        )
        res = suggest_section_diagram_with_ai(
            project,
            section_id=section_id,
            diagram_title=title_hint,
            diagram_goal=instruction,
        )
        nodes = []
        for n in res.get("nodes") or []:
            nd = n if isinstance(n, dict) else n.model_dump()
            shape = str(nd.get("shape") or "rounded").strip().lower()
            if shape not in VALID_DIAGRAM_SHAPES:
                shape = "rounded"
            nodes.append(
                DiagramNode(
                    id=str(nd.get("id") or f"n{len(nodes)+1}"),
                    shape=shape,
                    title=str(nd.get("title") or "Bloc"),
                    items=[str(x) for x in (nd.get("items") or [])],
                    width_px=int(nd.get("width_px") or 400),
                    color=str(nd.get("color") or "#6C2826"),
                )
            )
        connections = [
            DiagramConnection(**c) if isinstance(c, dict) else c
            for c in (res.get("connections") or [])
        ]
        layout_raw = str(res.get("layout") or "horizontal_flow").strip().lower()
        layout = layout_raw if layout_raw in VALID_DIAGRAM_LAYOUTS else "horizontal_flow"
        dg = SectionDiagram(
            diagram_id=f"diag_{uuid.uuid4().hex[:8]}",
            title=str(res.get("title") or title_hint).strip(),
            caption=str(res.get("caption") or "").strip(),
            anchor_hint="",
            layout=layout,
            nodes=nodes,
            connections=connections,
        )
        sec.custom_diagrams.append(dg)
        msg = (
            f"Diagram « {dg.title} » generated and added to this section ({len(dg.nodes)} blocks)."
            if is_en
            else f"Schéma « {dg.title} » généré et inséré dans cette section ({len(dg.nodes)} blocs)."
        )
        return {
            "action_type": "diagram_created",
            "message": msg,
            "model_used": str(res.get("model_used") or ""),
            "mutated": True,
        }

    if intent == "search_image":
        from app.services.figure_extractor import (
            _existing_project_image_hashes,
            _save_valid_image_bytes,
            _search_wikimedia_medical_images,
        )
        from app.services.gemini_client import gemini_router

        subject = _extract_clean_subject(instruction, project.cover.title or project.name or "medical anatomy")
        # Translate subject into a concise English Wikimedia medical query for highest recall
        query_en = subject
        try:
            q_res = gemini_router.generate_with_fallback(
                role="research",
                contents=(
                    "Convert this medical figure request into a concise 2-5 word English search query "
                    f"for Wikimedia Commons medical diagrams (return ONLY the query, no quotes): {subject}"
                ),
            )
            candidate_q = (q_res.get("text") or "").strip().strip('"\'').splitlines()[0].strip()
            if 2 <= len(candidate_q) <= 80:
                query_en = candidate_q
        except Exception:
            pass

        hits = _search_wikimedia_medical_images(query_en, limit=3)
        if not hits and query_en != subject:
            hits = _search_wikimedia_medical_images(subject, limit=3)

        settings = get_settings()
        upload_dir = settings.storage_dir / "uploads" / project.project_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        existing_hashes = _existing_project_image_hashes(project)

        inserted: Optional[ImageAsset] = None
        for hit in hits:
            img_url = hit.get("url") or ""
            if not img_url:
                continue
            try:
                with httpx.Client(
                    timeout=15.0,
                    follow_redirects=True,
                    headers={"User-Agent": "SmartResearchFMSB/1.0 (Academic Protocol Assistant)"},
                ) as client:
                    r = client.get(img_url)
                    if r.status_code != 200 or not r.content:
                        continue
                    asset_id = f"img_{uuid.uuid4().hex[:10]}"
                    saved_path = _save_valid_image_bytes(
                        r.content,
                        upload_dir,
                        asset_id,
                        existing_hashes,
                    )
                    if saved_path is None:
                        continue
                    if mirror_upload_cb is not None:
                        try:
                            mirror_upload_cb(project.project_id, Path(saved_path))
                        except Exception:
                            pass
                    inserted = ImageAsset(
                        asset_id=asset_id,
                        title=subject[:110] or hit.get("title", "Figure médicale"),
                        source_url=f"/api/projects/{project.project_id}/images/{asset_id}/file",
                        local_path=str(saved_path),
                        caption=hit.get("title", ""),
                        license_info=hit.get("license", "Wikimedia Commons (Open Access)"),
                        target_section_id=section_id,
                        approved=True,
                    )
                    project.images.append(inserted)
                    break
            except Exception as exc:
                logger.debug("Image fetch failed for %s: %s", img_url, exc)
                continue

        if inserted is not None:
            msg = (
                f"Figure « {inserted.title} » found on Wikimedia Commons and inserted into this section."
                if is_en
                else f"Figure « {inserted.title} » trouvée (Wikimedia Commons) et insérée dans cette section."
            )
            return {
                "action_type": "image_inserted",
                "message": msg,
                "model_used": "wikimedia-commons",
                "mutated": True,
            }

        msg = (
            f"No open-access medical figure matched « {subject} » on Wikimedia Commons. You can upload your own image directly in the Figures panel below."
            if is_en
            else f"Aucune figure médicale libre de droits trouvée sur Wikimedia Commons pour « {subject} ». Vous pouvez téléverser votre propre image directement dans le panneau Figures ci-dessous."
        )
        return {
            "action_type": "info",
            "message": msg,
            "model_used": "",
            "mutated": False,
        }

    return {"action_type": "delegate_reformulate", "message": "", "mutated": False}
