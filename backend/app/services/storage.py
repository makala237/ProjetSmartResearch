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
    """SQLAlchemy-backed store for protocol projects and presentation decks (PostgreSQL / SQLite)."""

    def _ensure_ready(self) -> None:
        from app.db.database import init_db

        init_db()

    def _hydrate_project(self, row) -> ProtocolProject:
        from app.core.security import ensure_utc

        proj = ProtocolProject.model_validate(row.data_json)
        proj.owner_user_id = row.owner_user_id
        proj.topic_locked = bool(row.topic_locked)
        proj.locked_title = row.locked_title or ""
        proj.locked_author_name = row.locked_author_name or ""
        proj.locked_matricule = row.locked_matricule or ""
        if row.created_at:
            proj.created_at = ensure_utc(row.created_at) or proj.created_at
        if row.updated_at:
            proj.updated_at = ensure_utc(row.updated_at) or proj.updated_at
        sync_special_sections(proj)
        return proj

    def get_presentation(self, project_id: str):
        from app.db.database import SessionLocal
        from app.db.models import PresentationORM
        from app.schemas.presentation import PresentationDeck

        self._ensure_ready()
        db = SessionLocal()
        try:
            row = db.get(PresentationORM, project_id)
            if not row:
                return None
            return PresentationDeck.model_validate(row.data_json)
        except Exception:
            return None
        finally:
            db.close()

    def save_presentation(self, deck) -> None:
        from app.db.database import SessionLocal
        from app.db.models import PresentationORM, ProjectORM, utcnow

        self._ensure_ready()
        db = SessionLocal()
        try:
            proj_row = db.get(ProjectORM, deck.project_id)
            if proj_row is None:
                return
            payload = deck.model_dump(mode="json")
            now = utcnow()
            existing = db.get(PresentationORM, deck.project_id)
            if existing is None:
                db.add(
                    PresentationORM(
                        project_id=deck.project_id,
                        owner_user_id=proj_row.owner_user_id,
                        version=deck.version or "3.1.0",
                        slides_count=len(deck.slides),
                        data_json=payload,
                        updated_at=now,
                    )
                )
            else:
                existing.owner_user_id = proj_row.owner_user_id
                existing.version = deck.version or "3.1.0"
                existing.slides_count = len(deck.slides)
                existing.data_json = payload
                existing.updated_at = now
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def count_for_user(self, user_id: str) -> int:
        from app.db.database import SessionLocal
        from app.db.models import ProjectORM

        self._ensure_ready()
        db = SessionLocal()
        try:
            return db.query(ProjectORM).filter(ProjectORM.owner_user_id == user_id).count()
        finally:
            db.close()

    def create(
        self,
        payload: CreateProjectRequest,
        owner_user_id: Optional[str] = None,
        owner_role: str = "admin",
        owner_full_name: str = "",
        owner_matricule: str = "",
    ) -> ProtocolProject:
        from app.db.database import SessionLocal
        from app.db.models import ProjectORM, UserORM

        self._ensure_ready()
        db = SessionLocal()
        try:
            resolved_owner_id = owner_user_id
            if not resolved_owner_id:
                admin = db.query(UserORM).filter(UserORM.role == "admin").first()
                if admin is None:
                    raise RuntimeError("Aucun utilisateur administrateur initialisé en base.")
                resolved_owner_id = admin.id

            if owner_role != "admin":
                existing_count = (
                    db.query(ProjectORM)
                    .filter(ProjectORM.owner_user_id == resolved_owner_id)
                    .count()
                )
                if existing_count >= 1:
                    raise PermissionError("ONE_PROTOCOL_LIMIT")

            sections = {
                spec.id: SectionState(section_id=spec.id) for spec in FMSB_SECTIONS
            }
            cover = payload.cover.model_copy(deep=True) if payload.cover else CoverMetadata(title=payload.name)
            if not cover.title:
                cover.title = payload.name

            if owner_role != "admin":
                topic_locked = True
                locked_title = (cover.title or payload.name).strip()
                locked_author_name = (owner_full_name or cover.author_name or "").strip()
                locked_matricule = (owner_matricule or cover.matricule or "").strip()
                cover.title = locked_title
                if locked_author_name:
                    cover.author_name = locked_author_name
                if locked_matricule:
                    cover.matricule = locked_matricule
            else:
                topic_locked = False
                locked_title = (cover.title or payload.name).strip()
                locked_author_name = (cover.author_name or "").strip()
                locked_matricule = (cover.matricule or "").strip()

            project = ProtocolProject(
                owner_user_id=resolved_owner_id,
                topic_locked=topic_locked,
                locked_title=locked_title,
                locked_author_name=locked_author_name,
                locked_matricule=locked_matricule,
                name=payload.name,
                entry_mode=payload.entry_mode,
                research_idea=payload.research_idea,
                study_type=payload.study_type,
                cover=cover,
                sections=sections,
            )
            sync_special_sections(project)

            validated_count = sum(
                1
                for sec in project.sections.values()
                if getattr(sec.status, "value", str(sec.status)) == "validated"
            )
            row = ProjectORM(
                project_id=project.project_id,
                owner_user_id=resolved_owner_id,
                name=project.name,
                locked_title=project.locked_title,
                locked_author_name=project.locked_author_name,
                locked_matricule=project.locked_matricule,
                topic_locked=project.topic_locked,
                data_json=project.model_dump(mode="json"),
                sections_validated_count=validated_count,
                patients_count=len(project.patient_records),
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            db.add(row)
            db.commit()
            return project
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save(self, project: ProtocolProject) -> ProtocolProject:
        from app.db.database import SessionLocal
        from app.db.models import ProjectORM, UserORM

        self._ensure_ready()
        sync_special_sections(project)
        project.updated_at = datetime.now(timezone.utc)
        validated_count = sum(
            1
            for sec in project.sections.values()
            if getattr(sec.status, "value", str(sec.status)) == "validated"
        )
        patients_count = len(project.patient_records)
        payload = project.model_dump(mode="json")

        db = SessionLocal()
        try:
            row = db.get(ProjectORM, project.project_id)
            if row is None:
                owner_id = project.owner_user_id
                if not owner_id:
                    admin = db.query(UserORM).filter(UserORM.role == "admin").first()
                    if admin is None:
                        raise RuntimeError("Aucun utilisateur administrateur initialisé en base.")
                    owner_id = admin.id
                    project.owner_user_id = owner_id
                row = ProjectORM(
                    project_id=project.project_id,
                    owner_user_id=owner_id,
                    name=project.name,
                    locked_title=project.locked_title,
                    locked_author_name=project.locked_author_name,
                    locked_matricule=project.locked_matricule,
                    topic_locked=bool(project.topic_locked),
                    data_json=payload,
                    sections_validated_count=validated_count,
                    patients_count=patients_count,
                    created_at=project.created_at,
                    updated_at=project.updated_at,
                )
                db.add(row)
            else:
                if project.owner_user_id:
                    row.owner_user_id = project.owner_user_id
                row.name = project.name
                row.locked_title = project.locked_title
                row.locked_author_name = project.locked_author_name
                row.locked_matricule = project.locked_matricule
                row.topic_locked = bool(project.topic_locked)
                row.data_json = payload
                row.sections_validated_count = validated_count
                row.patients_count = patients_count
                row.updated_at = project.updated_at
            db.commit()
            return project
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, project_id: str) -> Optional[ProtocolProject]:
        from app.db.database import SessionLocal
        from app.db.models import ProjectORM

        self._ensure_ready()
        db = SessionLocal()
        try:
            row = db.get(ProjectORM, project_id)
            if row is None:
                return None
            return self._hydrate_project(row)
        finally:
            db.close()

    def list_all(self) -> List[ProtocolProject]:
        from app.db.database import SessionLocal
        from app.db.models import ProjectORM

        self._ensure_ready()
        db = SessionLocal()
        try:
            rows = db.query(ProjectORM).order_by(ProjectORM.updated_at.desc()).all()
            out: List[ProtocolProject] = []
            for row in rows:
                try:
                    out.append(self._hydrate_project(row))
                except Exception:
                    continue
            return out
        finally:
            db.close()

    def list_for_user(self, user_id: str, role: str) -> List[ProtocolProject]:
        """Return all projects for admins, or strictly owned projects for students."""
        if role == "admin":
            return self.list_all()

        from app.db.database import SessionLocal
        from app.db.models import ProjectORM

        self._ensure_ready()
        db = SessionLocal()
        try:
            rows = (
                db.query(ProjectORM)
                .filter(ProjectORM.owner_user_id == user_id)
                .order_by(ProjectORM.updated_at.desc())
                .all()
            )
            out: List[ProtocolProject] = []
            for row in rows:
                try:
                    out.append(self._hydrate_project(row))
                except Exception:
                    continue
            return out
        finally:
            db.close()

    def delete(self, project_id: str) -> bool:
        from app.db.database import SessionLocal
        from app.db.models import ProjectORM

        self._ensure_ready()
        db = SessionLocal()
        try:
            row = db.get(ProjectORM, project_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


project_storage = ProjectStorage()
