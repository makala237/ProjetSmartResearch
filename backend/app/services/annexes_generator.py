import re
from typing import Dict, List, Optional, Tuple
from app.data.fmsb_canvas import DEDICATED_SUB_ANNEX_IDS, NON_CRF_SUB_ANNEX_IDS
from app.data.i18n import normalize_lang
from app.schemas.protocol import ProtocolProject
from app.services.annexe_engine import _build_deterministic_sub_annex, resolve_faculty_admin_context
from app.services.crf_builder import (
    ensure_project_crf_fields,
    extract_hospital_sites_from_project,
    is_crf_annexe_content,
)


def parse_user_annexes_from_markdown(project: ProtocolProject, md: str) -> List[Dict[str, object]]:
    """
    Parse custom user-defined annexes from Markdown, respecting their actual order.
    The annex identified as the data collection form / CRF gets kind='data_collection'
    and includes all structured crf_fields and hospital sites. Other annexes get kind='letter'.
    """
    if not md or not md.strip():
        return []

    lines = md.splitlines()
    header_indices: List[int] = []
    for idx, line in enumerate(lines):
        s = line.strip()
        if re.match(r"^#{1,3}\s+(?:ANNEXE|APPENDIX)\b", s, re.IGNORECASE):
            header_indices.append(idx)
        elif re.match(
            r"^#{1,3}\s+(?:FICHE|FORMULAIRE|QUESTIONNAIRE|NOTICE|CONSENTEMENT|CLAIRANCE|LETTRE|D[ÉE]CLARATION|CAHIER|CASE\s+REPORT)\b",
            s,
            re.IGNORECASE,
        ):
            header_indices.append(idx)

    if not header_indices:
        for idx, line in enumerate(lines):
            s = line.strip()
            if s.startswith("## ") and len(s) > 3:
                header_indices.append(idx)

    # Need at least one clearly defined annex header
    if not header_indices:
        return []

    c = project.cover
    author = c.author_name or "[Nom de l'étudiant]"
    matricule = c.matricule or ""
    title = c.title or project.name
    lang = normalize_lang(getattr(project, "language", "fr"))
    ensure_project_crf_fields(project)
    crf_serialized = [f.model_dump() for f in project.crf_fields]
    hospital_sites = extract_hospital_sites_from_project(project)
    default_sec_title = "General Variables" if lang == "en" else "Variables générales"
    free_lbl = "Free text" if lang == "en" else "Champ libre"

    grouped: Dict[str, List[Dict[str, str]]] = {}
    for f in project.crf_fields:
        grouped.setdefault(f.section_title or default_sec_title, []).append(
            {
                "code": f.code,
                "name": f.label,
                "field_type": f.field_type,
                "options": " | ".join(f.options),
                "unit": f.unit,
                "sub_field_label": f.sub_field_label,
                "modalities": " / ".join(f.options) if f.options else (f.unit or free_lbl),
            }
        )

    annexes: List[Dict[str, object]] = []
    for i, h_idx in enumerate(header_indices):
        next_h = header_indices[i + 1] if i + 1 < len(header_indices) else len(lines)
        raw_title = lines[h_idx].strip().lstrip("#").strip()
        body_lines = [l.strip() for l in lines[h_idx + 1 : next_h] if l.strip() and not l.strip().startswith("---")]
        body_text = "\n".join(body_lines)

        is_crf = is_crf_annexe_content(raw_title, body_text)
        if is_crf:
            annexes.append(
                {
                    "number": i + 1,
                    "title": raw_title,
                    "kind": "data_collection",
                    "grouped_variables": grouped,
                    "crf_fields": crf_serialized,
                    "hospital_sites": hospital_sites,
                    "author_label": f"{author} ({'Reg. No.' if lang == 'en' else 'Matricule'} : {matricule})"
                    if matricule
                    else author,
                    "study_title": title,
                    "paragraphs": body_lines
                    or [
                        f"{'Study Title' if lang == 'en' else 'Titre de l\'étude'} : {title}",
                        "Instructions: Check the appropriate box or write legibly inside the field."
                        if lang == "en"
                        else "Consigne : Cocher la case correspondante ou inscrire lisiblement la valeur dans le cadre réservé.",
                    ],
                }
            )
        else:
            annexes.append(
                {
                    "number": i + 1,
                    "title": raw_title,
                    "kind": "letter",
                    "paragraphs": body_lines if body_lines else [raw_title],
                }
            )

    return _attach_admin_context_to_annexes(project, annexes)


def _build_data_collection_annex_entry(
    project: ProtocolProject,
    number: int,
    custom_title: str = "",
    paragraphs: List[str] | None = None,
) -> Dict[str, object]:
    lang = normalize_lang(getattr(project, "language", "fr"))
    c = project.cover
    author = c.author_name or ("[Candidate Full Name]" if lang == "en" else "[Noms & Prénoms de l'étudiant(e)]")
    matricule = c.matricule or ("[Reg. No.]" if lang == "en" else "[Matricule]")
    title = c.title or project.name
    ensure_project_crf_fields(project)
    crf_serialized = [f.model_dump() for f in project.crf_fields]
    hospital_sites = extract_hospital_sites_from_project(project)
    default_sec_title = "General Variables" if lang == "en" else "Variables générales"
    free_lbl = "Free text" if lang == "en" else "Champ libre"

    grouped: Dict[str, List[Dict[str, str]]] = {}
    for f in project.crf_fields:
        grouped.setdefault(f.section_title or default_sec_title, []).append(
            {
                "code": f.code,
                "name": f.label,
                "field_type": f.field_type,
                "options": " | ".join(f.options),
                "unit": f.unit,
                "sub_field_label": f.sub_field_label,
                "modalities": " / ".join(f.options) if f.options else (f.unit or free_lbl),
            }
        )

    resolved_title = (custom_title or "").strip()
    if not resolved_title:
        resolved_title = (
            f"APPENDIX {number}: DATA COLLECTION FORM (CRF)"
            if lang == "en"
            else f"ANNEXE {number} : FICHE DE COLLECTE DES DONNÉES"
        )

    default_paras = (
        [
            f"Study Title: {title}",
            "Instructions: Check the appropriate box or write the value legibly inside the designated field.",
        ]
        if lang == "en"
        else [
            f"Titre de l'étude : {title}",
            "Consigne : Cocher la case correspondante ou inscrire lisiblement la valeur dans le cadre réservé.",
        ]
    )

    return {
        "number": number,
        "title": resolved_title,
        "kind": "data_collection",
        "grouped_variables": grouped,
        "crf_fields": crf_serialized,
        "hospital_sites": hospital_sites,
        "author_label": f"{author} ({'Reg. No.' if lang == 'en' else 'Matricule'} : {matricule})",
        "study_title": title,
        "paragraphs": paragraphs or default_paras,
    }


def classify_annex_kind(
    section_id: str = "",
    title: str = "",
    paragraphs: Optional[List[str]] = None,
) -> str:
    """Classify any annex (built-in or custom) into 'data_collection', 'request_letter', 'ethics_form', or 'letter'."""
    sid = (section_id or "").strip().lower()
    t_low = (title or "").strip().lower()
    body_sample = " ".join(str(p) for p in (paragraphs or [])[:8]).lower()

    if sid == "annexe_fiche_collecte" or is_crf_annexe_content(title, body_sample):
        return "data_collection"
    if sid in ("annexe_autorisation_recherche", "annexe_clairance_ethique") or any(
        k in t_low
        for k in (
            "demande",
            "autorisation",
            "clairance",
            "request",
            "authorization",
            "clearance",
            "lettre administrative",
            "sollicitation",
        )
    ) or ("objet :" in body_sample or "objet:" in body_sample or "subject:" in body_sample):
        return "request_letter"
    if sid in (
        "annexe_consentement",
        "annexe_notice_information",
        "annexe_confidentialite",
        "annexe_engagement",
    ) or any(
        k in t_low
        for k in (
            "consentement",
            "consent",
            "formulaire",
            "form",
            "notice d'information",
            "information sheet",
            "confidentialit",
            "confidentiality",
            "engagement",
            "commitment",
            "déclaration",
            "declaration",
        )
    ):
        return "ethics_form"
    return "letter"


def _attach_admin_context_to_annexes(
    project: ProtocolProject,
    annexes: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    ctx = resolve_faculty_admin_context(project)
    for ann in annexes:
        if ann.get("kind") != "data_collection":
            ann["kind"] = classify_annex_kind(
                section_id=str(ann.get("section_id") or ""),
                title=str(ann.get("title") or ""),
                paragraphs=ann.get("paragraphs") if isinstance(ann.get("paragraphs"), list) else [],
            )
            ann["admin_ctx"] = ctx
    return annexes


def _extract_heading_and_body_from_section(md: str, default_title: str) -> Tuple[str, List[str]]:
    lines = [l.strip() for l in (md or "").splitlines() if l.strip()]
    heading = default_title
    body: List[str] = []
    for idx, line in enumerate(lines):
        if idx == 0 and (
            line.startswith("#")
            or re.match(r"^(?:ANNEXE|APPENDIX|NOTICE|FORMULAIRE|DEMANDE|D[ÉE]CLARATION|LETTRE)\b", line, re.IGNORECASE)
        ):
            heading = line.lstrip("#* ").strip()
        else:
            body.append(line.lstrip("# ").strip() if line.startswith("### ") else line)
    return heading, body


def _extract_crf_heading_from_section(project: ProtocolProject) -> Tuple[str, List[str]]:
    crf_sec = project.sections.get("annexe_fiche_collecte")
    if not crf_sec or not crf_sec.content_markdown or not crf_sec.content_markdown.strip():
        return "", []
    lines = [l.strip() for l in crf_sec.content_markdown.splitlines() if l.strip()]
    heading = ""
    body: List[str] = []
    for idx, line in enumerate(lines):
        if idx == 0 and line.startswith("#"):
            heading = line.lstrip("#").strip()
        else:
            body.append(line)
    return heading, body


def build_annexes_pack(project: ProtocolProject) -> List[Dict[str, object]]:
    """
    Generate the protocol appendices list from the individualized sub-annex sections
    (Notice d'information, Consentement, Fiche de collecte, Autorisation, Clairance,
    Confidentialité, Engagement) plus custom `annexes` (or faculty-aware defaults),
    avoiding duplication while preserving custom ordering in legacy `annexes`-only projects.
    """
    lang = normalize_lang(getattr(project, "language", "fr"))
    prefix = "APPENDIX" if lang == "en" else "ANNEXE"
    def_titles = (
        {
            "annexe_notice_information": "PARTICIPANT INFORMATION SHEET",
            "annexe_consentement": "INFORMED CONSENT FORM",
            "annexe_autorisation_recherche": "RESEARCH AUTHORIZATION REQUEST",
            "annexe_clairance_ethique": "ETHICAL CLEARANCE REQUEST",
            "annexe_confidentialite": "RESEARCHER CONFIDENTIALITY DECLARATION",
            "annexe_engagement": "INVESTIGATOR COMMITMENT LETTER",
        }
        if lang == "en"
        else {
            "annexe_notice_information": "NOTICE D'INFORMATION DU PARTICIPANT",
            "annexe_consentement": "FORMULAIRE DE CONSENTEMENT ÉCLAIRÉ",
            "annexe_autorisation_recherche": "DEMANDE D'AUTORISATION DE RECHERCHE",
            "annexe_clairance_ethique": "DEMANDE DE CLAIRANCE ÉTHIQUE",
            "annexe_confidentialite": "DÉCLARATION DE CONFIDENTIALITÉ DU CHERCHEUR",
            "annexe_engagement": "LETTRE D'ENGAGEMENT DE L'INVESTIGATEUR",
        }
    )

    ann_sec = project.sections.get("annexes")
    user_annexes: List[Dict[str, object]] = []
    if ann_sec and ann_sec.content_markdown and ann_sec.content_markdown.strip():
        user_annexes = parse_user_annexes_from_markdown(project, ann_sec.content_markdown)

    has_non_crf_dedicated = any(
        project.sections.get(sid) and (project.sections[sid].content_markdown or "").strip()
        for sid in NON_CRF_SUB_ANNEX_IDS
    )
    has_crf_in_user_annexes = any(a.get("kind") == "data_collection" for a in user_annexes)
    crf_heading, crf_body = _extract_crf_heading_from_section(project)

    if has_non_crf_dedicated:
        combined: List[Dict[str, object]] = []
        for sid in DEDICATED_SUB_ANNEX_IDS:
            sec = project.sections.get(sid)
            if not sec or not (sec.content_markdown or "").strip():
                continue
            if sid == "annexe_fiche_collecte":
                combined.append(
                    _build_data_collection_annex_entry(
                        project,
                        number=len(combined) + 1,
                        custom_title=crf_heading or f"{prefix} {len(combined) + 1} : FICHE DE COLLECTE DES DONNÉES",
                        paragraphs=crf_body or None,
                    )
                )
            else:
                h_title, p_lines = _extract_heading_and_body_from_section(
                    sec.content_markdown,
                    def_titles.get(sid, sid.upper()),
                )
                clean_title = re.sub(
                    r"^(?:ANNEXE|APPENDIX)\s+(?:\d+|[IVX]+)\s*[:\-–.]\s*",
                    "",
                    h_title,
                    flags=re.IGNORECASE,
                ).strip()
                combined.append(
                    {
                        "number": len(combined) + 1,
                        "section_id": sid,
                        "title": f"{prefix} {len(combined) + 1} : {clean_title}" if clean_title else f"{prefix} {len(combined) + 1}",
                        "kind": "letter",
                        "paragraphs": p_lines,
                    }
                )
        for ua in user_annexes:
            if ua.get("kind") == "data_collection" and any(x.get("kind") == "data_collection" for x in combined):
                continue
            item_copy = dict(ua)
            item_copy["number"] = len(combined) + 1
            combined.append(item_copy)
        if combined:
            return _attach_admin_context_to_annexes(project, combined)

    if user_annexes:
        if has_crf_in_user_annexes:
            return _attach_admin_context_to_annexes(project, user_annexes)
        if crf_heading or crf_body or getattr(project.annexes_config, "include_data_collection_form", True):
            crf_entry = _build_data_collection_annex_entry(
                project,
                number=1,
                custom_title=crf_heading,
                paragraphs=crf_body or None,
            )
            combined = [crf_entry] + user_annexes
            for idx_item, item in enumerate(combined, start=1):
                item["number"] = idx_item
            return _attach_admin_context_to_annexes(project, combined)
        return _attach_admin_context_to_annexes(project, user_annexes)

    ctx = resolve_faculty_admin_context(project)
    cfg = project.annexes_config
    annexes: List[Dict[str, object]] = []
    idx = 1

    # Notice d'information
    if cfg.include_info_notice:
        md = _build_deterministic_sub_annex("annexe_notice_information", ctx, bilingual=False)
        _, p_lines = _extract_heading_and_body_from_section(md, def_titles["annexe_notice_information"])
        annexes.append(
            {
                "number": idx,
                "section_id": "annexe_notice_information",
                "title": f"{prefix} {idx}: {def_titles['annexe_notice_information']}" if lang == "en" else f"{prefix} {idx} : {def_titles['annexe_notice_information']}",
                "kind": "letter",
                "paragraphs": p_lines,
            }
        )
        idx += 1

    # Consentement éclairé
    if cfg.include_consent_form:
        md = _build_deterministic_sub_annex("annexe_consentement", ctx, bilingual=False)
        _, p_lines = _extract_heading_and_body_from_section(md, def_titles["annexe_consentement"])
        annexes.append(
            {
                "number": idx,
                "section_id": "annexe_consentement",
                "title": f"{prefix} {idx}: {def_titles['annexe_consentement']}" if lang == "en" else f"{prefix} {idx} : {def_titles['annexe_consentement']}",
                "kind": "letter",
                "paragraphs": p_lines,
            }
        )
        idx += 1

    # Fiche de collecte / CRF
    if cfg.include_data_collection_form or crf_body:
        annexes.append(
            _build_data_collection_annex_entry(
                project,
                number=idx,
                custom_title=crf_heading or (
                    f"APPENDIX {idx}: DATA COLLECTION FORM (CRF)"
                    if lang == "en"
                    else f"ANNEXE {idx} : FICHE DE COLLECTE DES DONNÉES"
                ),
                paragraphs=crf_body or None,
            )
        )
        idx += 1

    # Autorisation de recherche (adaptée à la Faculté + sites d'accueil)
    if cfg.include_research_auth_requests:
        md = _build_deterministic_sub_annex("annexe_autorisation_recherche", ctx, bilingual=False)
        _, p_lines = _extract_heading_and_body_from_section(md, def_titles["annexe_autorisation_recherche"])
        annexes.append(
            {
                "number": idx,
                "section_id": "annexe_autorisation_recherche",
                "title": f"{prefix} {idx}: {def_titles['annexe_autorisation_recherche']}" if lang == "en" else f"{prefix} {idx} : {def_titles['annexe_autorisation_recherche']}",
                "kind": "letter",
                "paragraphs": p_lines,
            }
        )
        idx += 1

    # Demande de clairance éthique
    if cfg.include_ethics_clearance_request:
        md = _build_deterministic_sub_annex("annexe_clairance_ethique", ctx, bilingual=False)
        _, p_lines = _extract_heading_and_body_from_section(md, def_titles["annexe_clairance_ethique"])
        annexes.append(
            {
                "number": idx,
                "section_id": "annexe_clairance_ethique",
                "title": f"{prefix} {idx}: {def_titles['annexe_clairance_ethique']}" if lang == "en" else f"{prefix} {idx} : {def_titles['annexe_clairance_ethique']}",
                "kind": "letter",
                "paragraphs": p_lines,
            }
        )
        idx += 1

    # Déclaration de confidentialité
    if cfg.include_confidentiality_declaration:
        md = _build_deterministic_sub_annex("annexe_confidentialite", ctx, bilingual=False)
        _, p_lines = _extract_heading_and_body_from_section(md, def_titles["annexe_confidentialite"])
        annexes.append(
            {
                "number": idx,
                "section_id": "annexe_confidentialite",
                "title": f"{prefix} {idx}: {def_titles['annexe_confidentialite']}" if lang == "en" else f"{prefix} {idx} : {def_titles['annexe_confidentialite']}",
                "kind": "letter",
                "paragraphs": p_lines,
            }
        )
        idx += 1

    # Lettre d'engagement
    if cfg.include_commitment_letter:
        md = _build_deterministic_sub_annex("annexe_engagement", ctx, bilingual=False)
        _, p_lines = _extract_heading_and_body_from_section(md, def_titles["annexe_engagement"])
        annexes.append(
            {
                "number": idx,
                "section_id": "annexe_engagement",
                "title": f"{prefix} {idx}: {def_titles['annexe_engagement']}" if lang == "en" else f"{prefix} {idx} : {def_titles['annexe_engagement']}",
                "kind": "letter",
                "paragraphs": p_lines,
            }
        )
        idx += 1

    return _attach_admin_context_to_annexes(project, annexes)
