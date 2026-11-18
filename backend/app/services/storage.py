from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.core.config import get_settings
from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import (
    CoverMetadata,
    CreateProjectRequest,
    ProtocolProject,
    SectionState,
    StepStatus,
)
from app.services.annexes_generator import build_annexes_pack
from app.services.rich_formatter import markdown_to_clean_html


def _build_special_section_markdown(project: ProtocolProject, section_id: str) -> str:
    """Build synchronized Markdown content for front/back-matter FMSB sections."""
    if section_id == "toc":
        lines = ["### TABLE DES MATIÈRES (STRUCTURE FMSB)", ""]
        current_chap = None
        for spec in FMSB_SECTIONS:
            if spec.id == "cover_page":
                continue
            if spec.chapter_title != current_chap:
                current_chap = spec.chapter_title
                lines.append(f"#### {current_chap}")
            lines.append(f"- {spec.title}")
        lines.append("")
        lines.append(
            "*Note : La pagination exacte est générée automatiquement dans Word (F9) lors de l'export final.*"
        )
        return "\n".join(lines)

    if section_id == "abbreviations":
        if project.abbreviations:
            lines = ["### LISTE DES ABRÉVIATIONS, ACRONYMES & SIGLES", ""]
            for ab in sorted(project.abbreviations, key=lambda x: x.acronym.lower()):
                lines.append(f"- **{ab.acronym}** : {ab.full_form}")
            return "\n".join(lines)
        return (
            "### LISTE DES ABRÉVIATIONS, ACRONYMES & SIGLES\n\n"
            "- **FMSB** : Faculté de Médecine et des Sciences Biomédicales\n"
            "- **UY1** : Université de Yaoundé I\n"
            "- **OMS** : Organisation Mondiale de la Santé"
        )

    if section_id == "list_of_tables":
        from app.services.list_builder import clean_caption_title
        from app.services.media_transport import get_effective_section_tables

        entries: List[str] = []
        idx = 1
        seen_keys: set[str] = set()
        for spec in FMSB_SECTIONS:
            if spec.id in ("cover_page", "toc", "abbreviations", "list_of_tables", "list_of_figures", "preliminaires"):
                continue
            for tbl in get_effective_section_tables(project, spec.id):
                t_clean = clean_caption_title(tbl.title, "Tableau") or tbl.title
                k = f"{spec.id}::{t_clean.lower()}"
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                entries.append(f"- **Tableau {idx}** : {t_clean} *({spec.title})*")
                idx += 1
        if not entries:
            return "Aucun tableau personnalisé ou structuré n'est encore enregistré."
        return "\n".join(entries)

    if section_id == "list_of_figures":
        from app.services.list_builder import clean_caption_title

        entries: List[str] = []
        idx = 1
        seen_keys: set[str] = set()
        for spec in FMSB_SECTIONS:
            if spec.id in ("cover_page", "toc", "abbreviations", "list_of_tables", "list_of_figures", "preliminaires"):
                continue
            for img in project.images:
                if img.approved and img.target_section_id == spec.id:
                    t_clean = clean_caption_title(img.title, "Figure") or img.title
                    k = f"{spec.id}::{t_clean.lower()}"
                    if k in seen_keys:
                        continue
                    seen_keys.add(k)
                    entries.append(f"- **Figure {idx}** : {t_clean} *({spec.title})*")
                    idx += 1
            sec = project.sections.get(spec.id)
            if sec and getattr(sec, "custom_diagrams", None):
                for dg in sec.custom_diagrams:
                    if not dg.nodes:
                        continue
                    t_clean = clean_caption_title(dg.title, "Figure") or dg.title
                    k = f"{spec.id}::{t_clean.lower()}"
                    if k in seen_keys:
                        continue
                    seen_keys.add(k)
                    entries.append(f"- **Figure {idx}** : {t_clean} *({spec.title})*")
                    idx += 1
        if not entries:
            return "Aucune figure ou schéma vectoriel n'est encore inclus."
        return "\n".join(entries)

    if section_id == "references":
        if project.references:
            lines = ["### RÉFÉRENCES BIBLIOGRAPHIQUES (VANCOUVER)", ""]
            for i, ref in enumerate(project.references, start=1):
                cit = (
                    ref.vancouver_citation
                    or f"{', '.join(ref.authors)}. {ref.title}. {ref.journal}. {ref.year or ''}."
                )
                lines.append(f"{i}. {cit}")
            return "\n".join(lines)
        return "### RÉFÉRENCES BIBLIOGRAPHIQUES (VANCOUVER)\n\nAucune référence enregistrée pour le moment."

    if section_id == "annexes":
        pack = build_annexes_pack(project)
        lines = ["### ANNEXES DU PROTOCOLE FMSB", ""]
        for ann in pack:
            lines.append(f"#### {ann['title']}")
            for p_txt in ann.get("paragraphs", []):
                lines.append(p_txt)
            lines.append("")
        return "\n".join(lines)

    return ""


def sync_special_sections(
    project: ProtocolProject,
    force_section_id: Optional[str] = None,
) -> None:
    """Ensure all 24 FMSB sections exist and auto-populate/refresh derived special sections."""
    special_ids = ("toc", "abbreviations", "list_of_tables", "list_of_figures", "references", "annexes")
    for spec in FMSB_SECTIONS:
        if spec.id not in project.sections:
            project.sections[spec.id] = SectionState(section_id=spec.id)

    for sid in special_ids:
        sec = project.sections[sid]
        has_structured_table = "<table" in (sec.content_html or "").lower() or "| :---" in (sec.content_markdown or "")
        should_refresh = (
            force_section_id == sid
            or not sec.content_markdown.strip()
            or (
                sid in ("toc", "list_of_tables", "list_of_figures", "references")
                and sec.status != StepStatus.VALIDATED
                and not has_structured_table
            )
        )
        if should_refresh:
            md = _build_special_section_markdown(project, sid)
            if md:
                sec.content_markdown = md
                sec.content_html = markdown_to_clean_html(md)


class ProjectStorage:
    """Local JSON file store for protocol projects (Phase 1 persistence)."""

    def __init__(self) -> None:
        settings = get_settings()
        self.projects_dir: Path = settings.storage_dir / "projects"
        self.presentations_dir: Path = settings.storage_dir / "presentations"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.presentations_dir.mkdir(parents=True, exist_ok=True)

    def _project_path(self, project_id: str) -> Path:
        safe_id = "".join(c for c in project_id if c.isalnum() or c in ("-", "_"))
        return self.projects_dir / f"{safe_id}.json"

    def _presentation_path(self, project_id: str) -> Path:
        safe_id = "".join(c for c in project_id if c.isalnum() or c in ("-", "_"))
        return self.presentations_dir / f"{safe_id}.json"

    def get_presentation(self, project_id: str):
        from app.schemas.presentation import PresentationDeck

        path = self._presentation_path(project_id)
        if not path.exists():
            return None
        try:
            return PresentationDeck.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save_presentation(self, deck) -> None:
        path = self._presentation_path(deck.project_id)
        path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")

    def create(self, payload: CreateProjectRequest) -> ProtocolProject:
        sections = {
            spec.id: SectionState(section_id=spec.id) for spec in FMSB_SECTIONS
        }
        cover = payload.cover or CoverMetadata(title=payload.name)
        if not cover.title:
            cover.title = payload.name

        project = ProtocolProject(
            name=payload.name,
            entry_mode=payload.entry_mode,
            research_idea=payload.research_idea,
            study_type=payload.study_type,
            cover=cover,
            sections=sections,
        )
        sync_special_sections(project)
        self.save(project)
        return project

    def save(self, project: ProtocolProject) -> ProtocolProject:
        sync_special_sections(project)
        project.updated_at = datetime.now(timezone.utc)
        path = self._project_path(project.project_id)
        path.write_text(project.model_dump_json(indent=2), encoding="utf-8")
        return project

    def get(self, project_id: str) -> Optional[ProtocolProject]:
        path = self._project_path(project_id)
        if not path.exists():
            return None
        project = ProtocolProject.model_validate_json(path.read_text(encoding="utf-8"))
        sync_special_sections(project)
        return project

    def list_all(self) -> List[ProtocolProject]:
        projects: List[ProtocolProject] = []
        for p in sorted(
            self.projects_dir.glob("*.json"),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        ):
            try:
                proj = ProtocolProject.model_validate_json(p.read_text(encoding="utf-8"))
                sync_special_sections(proj)
                projects.append(proj)
            except Exception:
                continue
        return projects

    def delete(self, project_id: str) -> bool:
        path = self._project_path(project_id)
        if path.exists():
            path.unlink()
            return True
        return False


project_storage = ProjectStorage()
