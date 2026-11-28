"""Pedagogical Knowledge Base & Course Notes Manager for Module 1 (Learning & Writing Plan).

Loads built-in course notes from `<project_root>/pedagogie/*.txt` AND admin-managed
custom course notes persisted in Firestore (when enabled) or local JSON storage.
"""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.config import get_settings


class CourseNoteRecord(BaseModel):
    note_id: str = Field(default_factory=lambda: f"note-{uuid4().hex[:10]}")
    title: str
    section_ids: List[str] = Field(default_factory=list)
    content: str
    source: str = "admin"  # "builtin" | "admin"
    filename: str = ""
    enabled: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# Deterministic mapping of built-in course notes in `pedagogie/` to FMSB/CAMES section IDs
BUILTIN_PEDAGOGY_SECTION_MAP: Dict[str, List[str]] = {
    "introduction.txt": ["intro_contexte", "intro_interet", "chap1_justification"],
    "introduction_2.txt": ["intro_contexte", "intro_interet", "chap1_justification"],
    "problématique.txt": ["intro_interet", "chap1_justification", "chap1_questions"],
    "Problème_problématique_objectifGeneral_ObjectifsSpecifiques.txt": [
        "intro_interet",
        "chap1_questions",
        "chap1_objectifs",
    ],
    "objectifGenerale_Objectifs specifiques.txt": ["chap1_objectifs"],
    "hypothèses de recherche.txt": ["chap1_hypotheses"],
    "Hypothèse_de_recherche_2.txt": ["chap1_hypotheses", "chap1_questions", "chap1_objectifs"],
    "revue_de_la_litterature.txt": ["chap2_rappel_connaissances", "chap2_etat_connaissances"],
    "revue_litterature_2.txt": ["chap2_rappel_connaissances", "chap2_etat_connaissances"],
    "revue_de_la_li_litterature_rechercher_extraire_rédiger.txt": [
        "chap2_rappel_connaissances",
        "chap2_etat_connaissances",
    ],
    "revue_narative_revue_systematique.txt": ["chap2_etat_connaissances"],
    "questionnaire_de_recherche_ou_fiche_de_collecte_des_donnees.txt": [
        "chap1_variables",
        "chap3_outils_procedure",
        "annexe_fiche_collecte",
    ],
}


def _repo_pedagogie_dir() -> Path:
    candidate = Path(__file__).resolve().parents[3] / "pedagogie"
    if candidate.exists():
        return candidate
    return Path(__file__).resolve().parents[2] / "pedagogie"


def _local_store_path() -> Path:
    settings = get_settings()
    base = Path(settings.storage_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base / "pedagogy_course_notes.json"


def _get_firestore_collection():
    """Return Firestore collection handle if Firestore backend is active, else None."""
    try:
        from app.db.datastore import get_datastore
        store = get_datastore()
        client = getattr(store, "_client", None)
        if client is not None:
            return client.collection("pedagogy_course_notes")
    except Exception:
        pass
    return None


def _load_builtin_notes() -> List[CourseNoteRecord]:
    ped_dir = _repo_pedagogie_dir()
    if not ped_dir.exists():
        return []
    notes: List[CourseNoteRecord] = []
    for path in sorted(ped_dir.glob("*.txt")):
        fname = path.name
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            continue
        if not text:
            continue
        sec_ids = list(BUILTIN_PEDAGOGY_SECTION_MAP.get(fname, []))
        if not sec_ids:
            low = fname.lower()
            if "intro" in low:
                sec_ids = ["intro_contexte", "intro_interet"]
            elif "probl" in low:
                sec_ids = ["intro_interet", "chap1_questions"]
            elif "objectif" in low:
                sec_ids = ["chap1_objectifs"]
            elif "hypoth" in low:
                sec_ids = ["chap1_hypotheses"]
            elif "revue" in low or "litt" in low:
                sec_ids = ["chap2_rappel_connaissances", "chap2_etat_connaissances"]
            elif "question" in low or "collecte" in low or "crf" in low:
                sec_ids = ["chap3_outils_procedure", "annexe_fiche_collecte"]
            elif "method" in low or "echant" in low:
                sec_ids = ["chap3_type_site_duree", "chap3_population_echantillon"]
            elif "analy" in low or "stat" in low:
                sec_ids = ["chap3_analyse_donnees"]
            elif "ethiq" in low:
                sec_ids = ["chap3_ethique"]
        clean_title = re.sub(r"[_\-]+", " ", path.stem).strip()
        clean_title = clean_title[0].upper() + clean_title[1:] if clean_title else fname
        note_id = f"builtin-{re.sub(r'[^a-z0-9]+', '-', path.stem.lower()).strip('-')}"
        notes.append(
            CourseNoteRecord(
                note_id=note_id,
                title=clean_title,
                section_ids=sec_ids,
                content=text,
                source="builtin",
                filename=fname,
                enabled=True,
            )
        )
    return notes


def _load_admin_overrides_and_notes() -> Dict[str, Any]:
    """Load persisted admin notes + builtin state overrides from Firestore or local JSON."""
    col = _get_firestore_collection()
    if col is not None:
        try:
            custom_notes: List[Dict[str, Any]] = []
            builtin_overrides: Dict[str, Dict[str, Any]] = {}
            for snap in col.stream():
                data = snap.to_dict() or {}
                nid = str(data.get("note_id") or snap.id)
                if nid.startswith("builtin-"):
                    builtin_overrides[nid] = data
                else:
                    data["note_id"] = nid
                    custom_notes.append(data)
            return {"custom_notes": custom_notes, "builtin_overrides": builtin_overrides}
        except Exception:
            pass

    path = _local_store_path()
    if not path.exists():
        return {"custom_notes": [], "builtin_overrides": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {
                "custom_notes": list(raw.get("custom_notes") or []),
                "builtin_overrides": dict(raw.get("builtin_overrides") or {}),
            }
    except Exception:
        pass
    return {"custom_notes": [], "builtin_overrides": {}}


def _save_admin_store(custom_notes: List[Dict[str, Any]], builtin_overrides: Dict[str, Dict[str, Any]]) -> None:
    col = _get_firestore_collection()
    if col is not None:
        try:
            for item in custom_notes:
                col.document(str(item["note_id"])).set(item)
            for nid, ov in builtin_overrides.items():
                col.document(nid).set({"note_id": nid, **ov})
            return
        except Exception:
            pass

    path = _local_store_path()
    path.write_text(
        json.dumps(
            {"custom_notes": custom_notes, "builtin_overrides": builtin_overrides},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def list_course_notes(section_id: Optional[str] = None, include_disabled: bool = True) -> List[CourseNoteRecord]:
    """List all course notes (built-in `pedagogie/` + admin-created)."""
    store = _load_admin_overrides_and_notes()
    overrides: Dict[str, Dict[str, Any]] = store["builtin_overrides"]
    result: List[CourseNoteRecord] = []

    for bn in _load_builtin_notes():
        ov = overrides.get(bn.note_id)
        if ov:
            if "section_ids" in ov and isinstance(ov["section_ids"], list):
                bn.section_ids = [str(s) for s in ov["section_ids"] if str(s).strip()]
            if "enabled" in ov:
                bn.enabled = bool(ov["enabled"])
            if "title" in ov and str(ov["title"]).strip():
                bn.title = str(ov["title"]).strip()
            if "content" in ov and str(ov["content"]).strip():
                bn.content = str(ov["content"]).strip()
        result.append(bn)

    for raw in store["custom_notes"]:
        try:
            result.append(CourseNoteRecord(**raw))
        except Exception:
            continue

    if not include_disabled:
        result = [r for r in result if r.enabled]
    if section_id:
        sec_clean = section_id.strip()
        result = [r for r in result if sec_clean in r.section_ids]
    return result


def create_course_note(
    title: str,
    content: str,
    section_ids: List[str],
    filename: str = "",
) -> CourseNoteRecord:
    clean_title = (title or "").strip()
    clean_content = (content or "").strip()
    clean_secs = list(dict.fromkeys(s.strip() for s in (section_ids or []) if s and s.strip()))
    if not clean_title:
        raise ValueError("Le titre de la note de cours est requis.")
    if not clean_content:
        raise ValueError("Le contenu de la note de cours est requis.")
    if not clean_secs:
        raise ValueError("Associez au moins une section du protocole.")

    rec = CourseNoteRecord(
        title=clean_title,
        section_ids=clean_secs,
        content=clean_content,
        source="admin",
        filename=filename.strip(),
        enabled=True,
    )
    store = _load_admin_overrides_and_notes()
    custom_notes = list(store["custom_notes"])
    custom_notes.append(rec.model_dump(mode="json"))
    _save_admin_store(custom_notes, store["builtin_overrides"])
    return rec


def update_course_note(
    note_id: str,
    title: Optional[str] = None,
    content: Optional[str] = None,
    section_ids: Optional[List[str]] = None,
    enabled: Optional[bool] = None,
) -> CourseNoteRecord:
    store = _load_admin_overrides_and_notes()
    now_iso = datetime.now(timezone.utc).isoformat()

    if note_id.startswith("builtin-"):
        builtins = {b.note_id: b for b in _load_builtin_notes()}
        if note_id not in builtins:
            raise ValueError("Note de cours introuvable.")
        ov = dict(store["builtin_overrides"].get(note_id) or {})
        if title is not None and title.strip():
            ov["title"] = title.strip()
        if content is not None and content.strip():
            ov["content"] = content.strip()
        if section_ids is not None:
            ov["section_ids"] = list(dict.fromkeys(s.strip() for s in section_ids if s and s.strip()))
        if enabled is not None:
            ov["enabled"] = bool(enabled)
        ov["updated_at"] = now_iso
        store["builtin_overrides"][note_id] = ov
        _save_admin_store(store["custom_notes"], store["builtin_overrides"])
        for n in list_course_notes(include_disabled=True):
            if n.note_id == note_id:
                return n
        raise ValueError("Note de cours introuvable après mise à jour.")

    custom_notes = list(store["custom_notes"])
    for idx, item in enumerate(custom_notes):
        if str(item.get("note_id")) == note_id:
            if title is not None and title.strip():
                item["title"] = title.strip()
            if content is not None and content.strip():
                item["content"] = content.strip()
            if section_ids is not None:
                item["section_ids"] = list(dict.fromkeys(s.strip() for s in section_ids if s and s.strip()))
            if enabled is not None:
                item["enabled"] = bool(enabled)
            item["updated_at"] = now_iso
            custom_notes[idx] = item
            _save_admin_store(custom_notes, store["builtin_overrides"])
            return CourseNoteRecord(**item)
    raise ValueError("Note de cours introuvable.")


def delete_course_note(note_id: str) -> bool:
    if note_id.startswith("builtin-"):
        raise ValueError("Les notes de cours intégrées ne peuvent pas être supprimées définitivement (désactivez-les).")
    store = _load_admin_overrides_and_notes()
    custom_notes = list(store["custom_notes"])
    kept = [item for item in custom_notes if str(item.get("note_id")) != note_id]
    if len(kept) == len(custom_notes):
        return False
    col = _get_firestore_collection()
    if col is not None:
        try:
            col.document(note_id).delete()
        except Exception:
            pass
    _save_admin_store(kept, store["builtin_overrides"])
    return True


def extract_text_from_course_upload(filename: str, content_bytes: bytes) -> str:
    """Extract clean plain text from an uploaded .txt, .md, .docx, or .pdf course note file."""
    low = (filename or "").lower()
    if low.endswith(".docx"):
        from docx import Document
        doc = Document(io.BytesIO(content_bytes))
        return "\n".join(p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()).strip()
    if low.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content_bytes))
        return "\n\n".join((pg.extract_text() or "").strip() for pg in reader.pages).strip()
    return content_bytes.decode("utf-8", errors="replace").strip()


def format_pedagogy_notes_for_section(section_id: str, max_chars: int = 9000) -> str:
    """Build a focused pedagogical knowledge digest for Module 1 (Learning & Plan Generation)."""
    notes = list_course_notes(section_id=section_id, include_disabled=False)
    if not notes:
        return ""
    per_note_budget = max(1400, max_chars // max(1, len(notes)))
    parts: List[str] = []
    for n in notes:
        body = n.content.strip()
        if len(body) > per_note_budget:
            head = body[: int(per_note_budget * 0.72)].rstrip()
            tail = body[-int(per_note_budget * 0.25) :].lstrip()
            body = f"{head}\n[...]\n{tail}"
        parts.append(f"### NOTE DE COURS : {n.title}\n{body}")
    return "\n\n".join(parts)[:max_chars]
