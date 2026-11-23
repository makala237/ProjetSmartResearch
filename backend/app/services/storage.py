from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.core.config import get_settings
from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import get_doc_labels, get_localized_canvas, normalize_lang
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
    lang = normalize_lang(getattr(project, "language", "fr"))
    labels = get_doc_labels(lang)
    localized_canvas = get_localized_canvas(lang)

    if section_id == "toc":
        heading = "### TABLE OF CONTENTS (CAMES / FMSB STRUCTURE)" if lang == "en" else "### TABLE DES MATIÈRES (STRUCTURE FMSB)"
        note = (
            "*Note: Exact pagination is dynamically generated in Word (F9) on final export.*"
            if lang == "en"
            else "*Note : La pagination exacte est générée automatiquement dans Word (F9) lors de l'export final.*"
        )
        lines = [heading, ""]
        current_chap = None
        for spec in localized_canvas:
            if spec.id == "cover_page":
                continue
            if spec.chapter_title != current_chap:
                current_chap = spec.chapter_title
                lines.append(f"#### {current_chap}")
            lines.append(f"- {spec.title}")
        lines.append("")
        lines.append(note)
        return "\n".join(lines)

    if section_id == "abbreviations":
        heading = "### LIST OF ABBREVIATIONS, ACRONYMS & SYMBOLS" if lang == "en" else "### LISTE DES ABRÉVIATIONS, ACRONYMES & SIGLES"
        if project.abbreviations:
            lines = [heading, ""]
            for ab in sorted(project.abbreviations, key=lambda x: x.acronym.lower()):
                lines.append(f"- **{ab.acronym}** : {ab.full_form}")
            return "\n".join(lines)
        return (
            f"{heading}\n\n"
            "- **FMSB** : Faculty of Medicine and Biomedical Sciences\n"
            "- **UY1** : The University of Yaoundé I\n"
            "- **WHO** : World Health Organization"
            if lang == "en"
            else f"{heading}\n\n"
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
        for spec in localized_canvas:
            if spec.id in ("cover_page", "toc", "abbreviations", "list_of_tables", "list_of_figures", "preliminaires"):
                continue
            for tbl in get_effective_section_tables(project, spec.id):
                t_clean = clean_caption_title(tbl.title, labels["table_seq"]) or tbl.title
                k = f"{spec.id}::{t_clean.lower()}"
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                entries.append(f"- **{labels['table_seq']} {idx}** : {t_clean} *({spec.title})*")
                idx += 1
        if not entries:
            return labels["no_tables"]
        return "\n".join(entries)

    if section_id == "list_of_figures":
        from app.services.list_builder import clean_caption_title

        entries: List[str] = []
        idx = 1
        seen_keys: set[str] = set()
        for spec in localized_canvas:
            if spec.id in ("cover_page", "toc", "abbreviations", "list_of_tables", "list_of_figures", "preliminaires"):
                continue
            for img in project.images:
                if img.approved and img.target_section_id == spec.id:
                    t_clean = clean_caption_title(img.title, labels["figure_seq"]) or img.title
                    k = f"{spec.id}::{t_clean.lower()}"
                    if k in seen_keys:
                        continue
                    seen_keys.add(k)
                    entries.append(f"- **{labels['figure_seq']} {idx}** : {t_clean} *({spec.title})*")
                    idx += 1
            sec = project.sections.get(spec.id)
            if sec and getattr(sec, "custom_diagrams", None):
                for dg in sec.custom_diagrams:
                    if not dg.nodes:
                        continue
                    t_clean = clean_caption_title(dg.title, labels["figure_seq"]) or dg.title
                    k = f"{spec.id}::{t_clean.lower()}"
                    if k in seen_keys:
                        continue
                    seen_keys.add(k)
                    entries.append(f"- **{labels['figure_seq']} {idx}** : {t_clean} *({spec.title})*")
                    idx += 1
        if not entries:
            return labels["no_figures"]
        return "\n".join(entries)

    if section_id == "references":
        heading = "### REFERENCES (VANCOUVER STYLE)" if lang == "en" else "### RÉFÉRENCES BIBLIOGRAPHIQUES (VANCOUVER)"
        empty_msg = "No references recorded yet." if lang == "en" else "Aucune référence enregistrée pour le moment."
        if project.references:
            lines = [heading, ""]
            for i, ref in enumerate(project.references, start=1):
                cit = (
                    ref.vancouver_citation
                    or f"{', '.join(ref.authors)}. {ref.title}. {ref.journal}. {ref.year or ''}."
                )
                lines.append(f"{i}. {cit}")
            return "\n".join(lines)
        return f"{heading}\n\n{empty_msg}"

    if section_id == "annexes":
        pack = build_annexes_pack(project)
        populated_dedicated = {
            sid
            for sid in (
                "annexe_notice_information",
                "annexe_consentement",
                "annexe_fiche_collecte",
                "annexe_autorisation_recherche",
                "annexe_clairance_ethique",
                "annexe_confidentialite",
                "annexe_engagement",
            )
            if project.sections.get(sid) and (project.sections[sid].content_markdown or "").strip()
        }
        pack = [
            a
            for a in pack
            if a.get("section_id") not in populated_dedicated
            and not (a.get("kind") == "data_collection" and "annexe_fiche_collecte" in populated_dedicated)
        ]
        heading = "### ADDITIONAL PROTOCOL APPENDICES" if lang == "en" else "### AUTRES ANNEXES DU PROTOCOLE"
        lines = [heading, ""]
        if not pack:
            lines.append(
                "*Appendices 1–7 are managed individually in their dedicated Chapter V sub-sections.*"
                if lang == "en"
                else "*Les annexes principales sont gérées individuellement dans les sous-sections dédiées du Chapitre V.*"
            )
            return "\n".join(lines)
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
    """Ensure all FMSB-CAMES 5-chapter sections exist and auto-populate/refresh derived special sections."""
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
    """Dual-mode store for protocol projects and presentation decks (Firestore+BlobStorage / SQL)."""

    def _ensure_ready(self) -> None:
        from app.db.database import init_db

        init_db()

    def _hydrate_project(self, meta, payload: dict) -> ProtocolProject:
        from app.core.security import ensure_utc

        proj = ProtocolProject.model_validate(payload)
        proj.owner_user_id = meta.owner_user_id
        proj.topic_locked = bool(meta.topic_locked)
        proj.locked_title = meta.locked_title or ""
        proj.locked_author_name = meta.locked_author_name or ""
        proj.locked_matricule = meta.locked_matricule or ""
        if meta.created_at:
            proj.created_at = ensure_utc(meta.created_at) or proj.created_at
        if meta.updated_at:
            proj.updated_at = ensure_utc(meta.updated_at) or proj.updated_at
        sync_special_sections(proj)
        return proj

    def get_presentation(self, project_id: str):
        from app.db.database import get_store
        from app.schemas.presentation import PresentationDeck

        self._ensure_ready()
        store = get_store()
        try:
            payload = store.get_presentation_payload(project_id)
            if not payload:
                return None
            return PresentationDeck.model_validate(payload)
        except Exception:
            return None
        finally:
            store.close()

    def save_presentation(self, deck) -> None:
        from app.db.database import get_store

        self._ensure_ready()
        store = get_store()
        try:
            proj_meta = store.get_project_meta(deck.project_id)
            if proj_meta is None:
                return
            store.save_presentation(
                project_id=deck.project_id,
                owner_user_id=proj_meta.owner_user_id,
                version=deck.version or "3.1.0",
                slides_count=len(deck.slides),
                payload_dict=deck.model_dump(mode="json"),
                updated_at=datetime.now(timezone.utc),
            )
        finally:
            store.close()

    def count_for_user(self, user_id: str) -> int:
        from app.db.database import get_store

        self._ensure_ready()
        store = get_store()
        try:
            return store.count_projects(owner_user_id=user_id)
        finally:
            store.close()

    def create(
        self,
        payload: CreateProjectRequest,
        owner_user_id: Optional[str] = None,
        owner_role: str = "admin",
        owner_full_name: str = "",
        owner_matricule: str = "",
    ) -> ProtocolProject:
        from app.db.database import get_store
        from app.db.datastore import ProjectMetaRecord

        self._ensure_ready()
        store = get_store()
        try:
            resolved_owner_id = owner_user_id
            if not resolved_owner_id:
                admin = store.get_first_admin()
                if admin is None:
                    raise RuntimeError("Aucun utilisateur administrateur initialisé en base.")
                resolved_owner_id = admin.id

            if owner_role != "admin":
                if store.count_projects(owner_user_id=resolved_owner_id) >= 1:
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
                locked_author_name = (cover.author_name or owner_full_name or "").strip()
                locked_matricule = (cover.matricule or owner_matricule or "").strip()
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
                language=normalize_lang(payload.language),
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
            meta = ProjectMetaRecord(
                project_id=project.project_id,
                owner_user_id=resolved_owner_id,
                name=project.name,
                locked_title=project.locked_title,
                locked_author_name=project.locked_author_name,
                locked_matricule=project.locked_matricule,
                topic_locked=bool(project.topic_locked),
                sections_validated_count=validated_count,
                patients_count=len(project.patient_records),
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            store.save_project(meta, project.model_dump(mode="json"))
            return project
        finally:
            store.close()

    def save(self, project: ProtocolProject) -> ProtocolProject:
        from app.db.database import get_store
        from app.db.datastore import ProjectMetaRecord

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

        store = get_store()
        try:
            existing_meta = store.get_project_meta(project.project_id)
            owner_id = project.owner_user_id or (existing_meta.owner_user_id if existing_meta else "")
            if not owner_id:
                admin = store.get_first_admin()
                if admin is None:
                    raise RuntimeError("Aucun utilisateur administrateur initialisé en base.")
                owner_id = admin.id
            project.owner_user_id = owner_id
            created_at = (
                existing_meta.created_at
                if existing_meta and existing_meta.created_at
                else project.created_at
            )

            meta = ProjectMetaRecord(
                project_id=project.project_id,
                owner_user_id=owner_id,
                name=project.name,
                locked_title=project.locked_title,
                locked_author_name=project.locked_author_name,
                locked_matricule=project.locked_matricule,
                topic_locked=bool(project.topic_locked),
                sections_validated_count=validated_count,
                patients_count=patients_count,
                created_at=created_at,
                updated_at=project.updated_at,
                blob_key=existing_meta.blob_key if existing_meta else "",
            )
            store.save_project(meta, payload)
            return project
        finally:
            store.close()

    def get(self, project_id: str) -> Optional[ProtocolProject]:
        from app.db.database import get_store

        self._ensure_ready()
        store = get_store()
        try:
            loaded = store.get_project_payload(project_id)
            if loaded is None:
                return None
            meta, payload = loaded
            return self._hydrate_project(meta, payload)
        finally:
            store.close()

    def list_all(self) -> List[ProtocolProject]:
        from app.db.database import get_store

        self._ensure_ready()
        store = get_store()
        try:
            out: List[ProtocolProject] = []
            for meta, payload in store.list_project_payloads():
                try:
                    out.append(self._hydrate_project(meta, payload))
                except Exception:
                    continue
            return out
        finally:
            store.close()

    def list_for_user(self, user_id: str, role: str) -> List[ProtocolProject]:
        """Return all projects for admins, or strictly owned projects for students."""
        if role == "admin":
            return self.list_all()

        from app.db.database import get_store

        self._ensure_ready()
        store = get_store()
        try:
            out: List[ProtocolProject] = []
            for meta, payload in store.list_project_payloads(owner_user_id=user_id):
                try:
                    out.append(self._hydrate_project(meta, payload))
                except Exception:
                    continue
            return out
        finally:
            store.close()

    def delete(self, project_id: str) -> bool:
        from app.db.database import get_store

        self._ensure_ready()
        store = get_store()
        try:
            return store.delete_project(project_id)
        finally:
            store.close()


project_storage = ProjectStorage()
