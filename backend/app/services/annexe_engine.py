from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field

from app.data.fmsb_canvas import NON_CRF_SUB_ANNEX_IDS
from app.data.i18n import normalize_lang
from app.schemas.protocol import ProtocolProject, SectionState, StepStatus
from app.services.gemini_client import gemini_router
from app.services.pipeline_tracker import emit_pipeline_step
from app.services.rich_formatter import markdown_to_clean_html


# ---------------------------------------------------------------------------
# Faculty & Investigator Context Resolver (zero hardcoded faculty/dean strings)
# ---------------------------------------------------------------------------

_KNOWN_CITIES = (
    "Yaoundé",
    "Douala",
    "Garoua",
    "Buéa",
    "Buea",
    "Bamenda",
    "Dschang",
    "Maroua",
    "Ngaoundéré",
    "Bertoua",
    "Ebolowa",
    "Bangangté",
    "Bafoussam",
    "Limbe",
    "Kumba",
    "Dakar",
    "Abidjan",
    "Cotonou",
    "Lomé",
    "Ouagadougou",
    "Bamako",
    "Niamey",
    "Libreville",
    "Brazzaville",
    "Kinshasa",
    "Conakry",
)


def _infer_city_from_context(parts: Sequence[str], lang: str = "fr") -> str:
    blob = " ".join(p for p in parts if p)
    low = blob.lower()
    for city in _KNOWN_CITIES:
        if city.lower() in low:
            return city
    return "[City]" if lang == "en" else "[Ville]"


def _french_prep_institution(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return "de la Faculté"
    if re.match(r"^[AEIOUYÀÂÉÈÊËÎÏÔÙÛŒH]", s, re.IGNORECASE):
        return f"de l'{s}"
    low = s.lower()
    if low.startswith(("institut", "école", "ecole", "centre", "collège", "college")):
        if low.startswith(("école", "ecole", "institut")):
            return f"de l'{s}"
        return f"du {s}"
    return f"de la {s}"


def resolve_faculty_admin_context(project: ProtocolProject) -> Dict[str, Any]:
    """
    Build dynamic faculty, university, city, dean/director recipient, ethics committee,
    host institutions, and student identity metadata directly from `project.cover`.
    """
    lang = normalize_lang(getattr(project, "language", "fr"))
    c = project.cover
    cfg = project.annexes_config

    faculty_name = (
        (c.faculty_en or c.faculty_fr or "Faculty of Medicine and Biomedical Sciences")
        if lang == "en"
        else (c.faculty_fr or c.faculty_en or "Faculté de Médecine et des Sciences Biomédicales")
    ).strip()
    university_name = (
        (c.university_en or c.university_fr or "The University of Yaoundé I")
        if lang == "en"
        else (c.university_fr or c.university_en or "Université de Yaoundé I")
    ).strip()
    department_name = (
        (c.department_en or c.department_fr or "")
        if lang == "en"
        else (c.department_fr or c.department_en or "")
    ).strip()

    host_institutions = [h.strip() for h in (c.host_institutions or []) if h and h.strip()]
    ethics_committees = [e.strip() for e in (c.ethics_committees or []) if e and e.strip()]
    if not ethics_committees:
        ethics_committees = [
            f"Institutional Ethics and Research Committee ({faculty_name})"
            if lang == "en"
            else f"Comité Institutionnel d'Éthique et de la Recherche ({_french_prep_institution(faculty_name)})"
        ]

    city = _infer_city_from_context(
        [university_name, faculty_name, *host_institutions, *ethics_committees],
        lang=lang,
    )

    # Determine whether faculty/school is led by a Dean or a Director
    low_fac = faculty_name.lower()
    is_director_led = any(
        k in low_fac
        for k in ("institut", "institute", "école", "ecole", "school", "istm", "ens ", "enstp", "iut")
    )
    if lang == "en":
        head_title = "The Director" if is_director_led else "The Dean"
        faculty_head_recipient = f"{head_title} of {faculty_name}"
        faculty_head_salutation = f"Dear {'Director' if is_director_led else 'Dean'},"
    else:
        head_title = "Monsieur/Madame le Directeur" if is_director_led else "Monsieur/Madame le Doyen"
        faculty_head_recipient = f"{head_title} {_french_prep_institution(faculty_name)}"
        faculty_head_salutation = (
            "Monsieur/Madame le Directeur," if is_director_led else "Monsieur/Madame le Doyen,"
        )

    author_name = (c.author_name or "").strip() or (
        "[Candidate Full Name]" if lang == "en" else "[Nom et Prénoms de l'étudiant(e)]"
    )
    matricule = (c.matricule or "").strip() or ("[Reg. No.]" if lang == "en" else "[Matricule]")
    student_level = (c.student_level or "").strip() or (
        "Medical Student" if lang == "en" else "Étudiant(e) en médecine"
    )
    student_phone = (c.student_phone or "").strip() or ("[Phone]" if lang == "en" else "[Téléphone]")
    student_email = (c.student_email or "").strip() or ("[E-mail]" if lang == "en" else "[E-mail]")
    director = (c.director or "").strip() or (
        "[Thesis Supervisor]" if lang == "en" else "[Directeur de thèse/mémoire]"
    )
    co_directors = [x.strip() for x in (c.co_directors or []) if x and x.strip()]
    study_title = (c.title or project.name or "").strip()
    study_goal = (getattr(cfg, "study_goal_short", "") or project.research_idea or study_title).strip()
    study_period = (c.study_period_label or "").strip() or (
        "[Study Period]" if lang == "en" else "[Période de l'étude]"
    )
    academic_year = (c.academic_year or "").strip() or "2026-2027"
    degree_label = (c.degree_label or "").strip() or (
        "Doctor of Medicine" if lang == "en" else "Doctorat en Médecine"
    )

    return {
        "lang": lang,
        "faculty_name": faculty_name,
        "university_name": university_name,
        "department_name": department_name,
        "city": city,
        "is_director_led": is_director_led,
        "faculty_head_recipient": faculty_head_recipient,
        "faculty_head_salutation": faculty_head_salutation,
        "ethics_committee": ethics_committees[0],
        "ethics_committees": ethics_committees,
        "host_institutions": host_institutions,
        "author_name": author_name,
        "matricule": matricule,
        "student_level": student_level,
        "student_phone": student_phone,
        "student_email": student_email,
        "director": director,
        "co_directors": co_directors,
        "study_title": study_title,
        "study_goal": study_goal,
        "study_period": study_period,
        "academic_year": academic_year,
        "degree_label": degree_label,
    }


# ---------------------------------------------------------------------------
# Layer 1: Multi-Sub-Annex Boundary Extractor (Raw Word / PDF / Markdown)
# ---------------------------------------------------------------------------

_CHAPTER_V_ANNEXES_START_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*)?(?:(?:CHAPITRE|CHAPTER)\s*(?:V|5)\s*[:\-–.]?\s*(?:ANNEXES|APPENDICES)|V\.\s*ANNEXES|ANNEXES|APPENDICES)\b",
    re.IGNORECASE,
)

_ANY_ANNEX_HEADER_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*)?(?:ANNEXE|APPENDIX)\s*(?:\d+|[IVX]+)\b",
    re.IGNORECASE,
)

_SUB_ANNEX_HEADER_RULES: List[Tuple[str, re.Pattern[str]]] = [
    (
        "annexe_notice_information",
        re.compile(
            r"(?:notice\s+d['’]information|fiche\s+d['’]information|participant\s+information\s+sheet|information\s+sheet|"
            r"formulaire\s+d['’]information)",
            re.IGNORECASE,
        ),
    ),
    (
        "annexe_consentement",
        re.compile(
            r"(?:formulaire\s+de\s+consentement|fiche\s+de\s+consentement|consentement\s+[ÉE]clair[ÉE]|"
            r"informed\s+consent\s+form|consent\s+form)",
            re.IGNORECASE,
        ),
    ),
    (
        "annexe_clairance_ethique",
        re.compile(
            r"(?:demande\s+(?:de\s+la\s+|d['’])?clairance\s+[ÉE]thique|clairance\s+[ÉE]thique|"
            r"ethical\s+clearance\s+request|request\s+for\s+ethical\s+clearance)",
            re.IGNORECASE,
        ),
    ),
    (
        "annexe_autorisation_recherche",
        re.compile(
            r"(?:demande\s+(?:de\s+l['’]|d['’])?autorisation\s+(?:de\s+recherche|administrative|d['’]enqu[êe]te)|"
            r"autorisation\s+de\s+recherche|research\s+authorization\s+request|request\s+for\s+(?:research\s+)?authori[sz]ation)",
            re.IGNORECASE,
        ),
    ),
    (
        "annexe_confidentialite",
        re.compile(
            r"(?:d[ÉE]claration\s+de\s+confidentialit[ÉE]|engagement\s+de\s+confidentialit[ÉE]|"
            r"confidentiality\s+declaration)",
            re.IGNORECASE,
        ),
    ),
    (
        "annexe_engagement",
        re.compile(
            r"(?:lettre\s+d['’]engagement|engagement\s+de\s+l['’]investigateur|commitment\s+letter)",
            re.IGNORECASE,
        ),
    ),
]

_CRF_CONFLICT_RE = re.compile(
    r"(?:fiche\s+(?:de\s+collecte|d['’]enqu[êe]te|d['’]observation)|questionnaire|cahier\s+d['’]observation|"
    r"grille\s+de\s+collecte|case\s+report\s+form|data\s+collection\s+(?:form|sheet))",
    re.IGNORECASE,
)


def _classify_annex_heading(line: str) -> Optional[str]:
    """Identify which dedicated sub-annex section a header line opens, or None."""
    s = (line or "").strip(" #*")
    if not s or len(s) > 150 or s.startswith(("-", "•", "*")):
        return None
    # Do not confuse an inline question like "- A3. Consentement éclairé obtenu : [ ] Oui"
    if "[ ]" in s or "☐" in s or "□" in s or "____" in s or "....." in s:
        return None
    if _CRF_CONFLICT_RE.search(s):
        return None

    has_notice = bool(_SUB_ANNEX_HEADER_RULES[0][1].search(s))
    has_consent = bool(_SUB_ANNEX_HEADER_RULES[1][1].search(s))
    # Combined "Fiche d'information et formulaire de consentement éclairé" starts in notice_information
    # and is cleanly split later between Notice and Consent!
    if has_notice and has_consent:
        return "annexe_notice_information"

    for sid, pat in _SUB_ANNEX_HEADER_RULES:
        if pat.search(s):
            return sid
    return None


def _split_combined_notice_and_consent(text: str) -> Tuple[str, str]:
    """
    When an annex combines Participant Information Sheet and Informed Consent
    (including bilingual FR + EN versions as in Cameroonian IRB/FOKOU protocols),
    cleanly separate notice paragraphs from consent checkbox/signature paragraphs.
    """
    lines = (text or "").splitlines()
    notice_lines: List[str] = []
    consent_lines: List[str] = []

    in_consent_block = False
    for idx, ln in enumerate(lines):
        s = ln.strip()
        low = s.lower()

        # Check switch into bilingual second part (English version / Version française)
        if in_consent_block and re.match(
            r"^(?:english\s+version|french\s+version|version\s+fran[çc]aise|version\s+anglaise)\b",
            low,
        ):
            in_consent_block = False
            notice_lines.append(ln)
            continue

        # Check switch into Consent sub-block inside Notice
        is_consent_start = bool(
            re.match(r"^(?:#{1,4}\s*)?(?:\*\*)?(?:consentement|consent|informed\s+consent)\b[:：]?$", low.strip(" *#"))
            or re.match(r"^(?:je\s+soussign[ÉE]|i,\s*the\s+undersigned|en\s+cochant\s+la\s+case|by\s+checking\s+the\s+box)", low)
            or re.match(r"^(?:☐|□|\[\s*\])\s*(?:j['’]accepte|je\s+n['’]accepte|i\s+agree|i\s+do\s+not\s+agree)", low)
        )
        if is_consent_start:
            in_consent_block = True

        if in_consent_block:
            consent_lines.append(ln)
        else:
            notice_lines.append(ln)

    notice_txt = "\n".join(notice_lines).strip()
    consent_txt = "\n".join(consent_lines).strip()
    if len(consent_txt) < 35:
        return text.strip(), ""
    return notice_txt, consent_txt


def _looks_like_toc_or_body_reference(block_idx: int, total_blocks: int, blocks: Sequence[Any]) -> bool:
    """Ignore Table of Contents blocks (with '.....') and early body chapters before References/Annexes."""
    if total_blocks <= 0:
        return False
    blk = blocks[block_idx]
    txt = (getattr(blk, "full_text", "") or "")
    # TOC dot-leaders
    if txt.count(".....") >= 2 or txt.count("…") >= 3:
        return True
    # Only consider blocks inside Annex region OR tail 50% when an annex header exists
    return False


def extract_all_sub_annexes_from_blocks(
    blocks: Sequence[Any],
    excluded_block_ids: Optional[Set[int]] = None,
    lang: str = "fr",
) -> Dict[str, Any]:
    """
    Scan ALL blocks in a document (supporting raw Word/PDF without '#' Markdown) and slice
    each non-CRF dedicated sub-annex between its exact start boundary and the next annex boundary.
    Excludes any `excluded_block_ids` already claimed by the CRF extractor.
    """
    _ = normalize_lang(lang)
    if not blocks:
        return {"extracted": {}, "claimed_block_ids": set(), "detected_section_ids": []}

    excluded = set(excluded_block_ids or set())
    n = len(blocks)

    # Locate the start of the Appendix zone (`CHAPITRE V : ANNEXES` or first `ANNEXE 1/I` in latter half)
    annex_zone_start = 0
    for idx, b in enumerate(blocks):
        head = (getattr(b, "heading", "") or "").strip()
        first_line = ((getattr(b, "full_text", "") or "").splitlines() or [""])[0].strip()
        probe = f"{head}\n{first_line}"
        # Skip PDF running header / page number lines at top of a page (check up to 5 top lines)
        if idx < min(5, n // 3) and ("....." in (getattr(b, "full_text", "") or "") or "table des matières" in probe.lower()):
            continue
        b_lines = [ln.strip() for ln in (getattr(b, "full_text", "") or "").splitlines() if ln.strip()]
        if _CHAPTER_V_ANNEXES_START_RE.match(head) or any(_CHAPTER_V_ANNEXES_START_RE.match(ln) for ln in b_lines[:5]):
            annex_zone_start = idx
            break
    else:
        # No explicit "CHAPITRE V : ANNEXES" block — look for first explicit `ANNEXE 1` / sub-annex in tail 65%
        search_from = max(0, int(n * 0.35)) if n >= 8 else 0
        for idx in range(search_from, n):
            head = (getattr(blocks[idx], "heading", "") or "").strip()
            lines = [ln.strip() for ln in (getattr(blocks[idx], "full_text", "") or "").splitlines() if ln.strip()]
            if _ANY_ANNEX_HEADER_RE.match(head) or any(_ANY_ANNEX_HEADER_RE.match(ln) for ln in lines[:5]):
                annex_zone_start = idx
                break
            if _classify_annex_heading(head) or any(_classify_annex_heading(ln) for ln in lines[:5]):
                annex_zone_start = idx
                break

    # Find every distinct annex starter inside [annex_zone_start .. n-1]
    anchors: List[Tuple[int, str, str]] = []  # (idx, section_id_or_other, header_title)
    for idx in range(annex_zone_start, n):
        blk = blocks[idx]
        bid = getattr(blk, "block_id", idx)
        if bid in excluded:
            # Treat CRF span start as an opaque boundary so preceding annex ends cleanly
            if not anchors or anchors[-1][1] != "__crf_boundary__":
                anchors.append((idx, "__crf_boundary__", "CRF"))
            continue
        txt = getattr(blk, "full_text", "") or ""
        if _looks_like_toc_or_body_reference(idx, n, blocks):
            continue
        head = (getattr(blk, "heading", "") or "").strip()
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]

        matched_sid = _classify_annex_heading(head)
        matched_title = head
        if not matched_sid:
            for ln in lines[:5]:
                cand_sid = _classify_annex_heading(ln)
                if cand_sid:
                    matched_sid = cand_sid
                    matched_title = ln
                    break

        # Stop/mark boundary on generic ANNEXE N / APPENDIX N too (even if other custom scale)
        if not matched_sid:
            if _ANY_ANNEX_HEADER_RE.match(head):
                matched_sid = "__other_annex__"
                matched_title = head
            else:
                for ln in lines[:5]:
                    if _ANY_ANNEX_HEADER_RE.match(ln):
                        matched_sid = "__other_annex__"
                        matched_title = ln
                        break

        if matched_sid:
            # Merge consecutive English/French continuation headers of the SAME sub-annex only when obvious
            anchors.append((idx, matched_sid, matched_title.strip(" #*")[:140]))

    if not anchors:
        return {"extracted": {}, "claimed_block_ids": set(), "detected_section_ids": []}

    extracted: Dict[str, Dict[str, Any]] = {}
    claimed_block_ids: Set[int] = set()

    for a_i, (s_idx, sid, title) in enumerate(anchors):
        if sid in ("__crf_boundary__", "__other_annex__"):
            continue
        e_idx = anchors[a_i + 1][0] - 1 if a_i + 1 < len(anchors) else n - 1
        span_blocks = [
            blocks[k]
            for k in range(s_idx, e_idx + 1)
            if getattr(blocks[k], "block_id", k) not in excluded
        ]
        if not span_blocks:
            continue
        span_bids = [getattr(b, "block_id", s_idx) for b in span_blocks]
        raw_span_text = "\n\n".join(
            (getattr(b, "full_text", "") or "").strip()
            for b in span_blocks
            if (getattr(b, "full_text", "") or "").strip()
        ).strip()
        if not raw_span_text:
            continue

        # Also check line-level inline splits if two annexes appeared inside the same block
        if sid == "annexe_notice_information":
            notice_part, consent_part = _split_combined_notice_and_consent(raw_span_text)
            if notice_part:
                extracted["annexe_notice_information"] = {
                    "section_id": "annexe_notice_information",
                    "title": title,
                    "content_markdown": notice_part,
                    "block_ids": span_bids,
                    "confidence": 0.9,
                }
                claimed_block_ids.update(span_bids)
            if consent_part and "annexe_consentement" not in extracted:
                extracted["annexe_consentement"] = {
                    "section_id": "annexe_consentement",
                    "title": "Formulaire de consentement éclairé",
                    "content_markdown": consent_part,
                    "block_ids": [],
                    "confidence": 0.88,
                }
            continue

        if sid in extracted:
            # Append secondary letter (e.g. multiple hospital authorization letters!)
            extracted[sid]["content_markdown"] += "\n\n---\n\n" + raw_span_text
            extracted[sid]["block_ids"].extend(span_bids)
        else:
            extracted[sid] = {
                "section_id": sid,
                "title": title,
                "content_markdown": raw_span_text,
                "block_ids": span_bids,
                "confidence": 0.9,
            }
        claimed_block_ids.update(span_bids)

    ordered_detected = [sid for sid in NON_CRF_SUB_ANNEX_IDS if sid in extracted]
    return {
        "extracted": extracted,
        "claimed_block_ids": claimed_block_ids,
        "detected_section_ids": ordered_detected,
    }


# ---------------------------------------------------------------------------
# Layer 2: Faculty-Aware Dedicated Sub-Annex Templates & AI Refinement
# ---------------------------------------------------------------------------


class _AISubAnnexOut(BaseModel):
    content_markdown: str = Field(
        description="Texte officiel complet et bien structuré de cette sous-annexe au format Markdown propre."
    )
    assistant_commentary: str = Field(
        default="",
        description="Brève note explicative sur l'adaptation à la faculté, au comité d'éthique et aux sites d'étude.",
    )


def _build_deterministic_notice_template(ctx: Dict[str, Any], bilingual: bool) -> str:
    fr_part = f"""## NOTICE D'INFORMATION DU PARTICIPANT

**Document soumis au comité d'éthique :** {ctx['ethics_committee']}
**Titre de l'étude :** {ctx['study_title']}
**Investigateur principal :** {ctx['author_name']} ({ctx['student_level']}, {ctx['faculty_name']}, {ctx['university_name']})
**Directeur de recherche :** {ctx['director']}
{f"**Co-directeur(s) :** {', '.join(ctx['co_directors'])}" if ctx['co_directors'] else ""}

### 1. Pourquoi cette étude ?
Cette recherche vise à étudier le sujet suivant : « {ctx['study_title']} ». Objectif principal : {ctx['study_goal']}.

### 2. Pourquoi êtes-vous invité(e) à participer ?
Vous êtes invité(e) car vous répondez aux critères de la population concernée par cette étude menée au sein de : {', '.join(ctx['host_institutions']) or ctx['faculty_name']}.

### 3. En quoi consiste votre participation ?
Votre participation consiste à fournir des informations cliniques et/ou sociodémographiques de manière anonyme à l'aide d'une fiche de collecte standardisée durant la période {ctx['study_period']}.

### 4. Participation volontaire
Votre participation est entièrement libre et volontaire. Vous pouvez refuser de participer ou vous retirer à tout moment, sans avoir à vous justifier et sans aucune conséquence sur la qualité de votre prise en charge médicale ou vos relations avec l'équipe.

### 5. Avantages et inconvénients
- **Avantages :** Aucune rémunération financière n'est prévue, mais votre contribution permettra d'améliorer la compréhension et la qualité de la prise en charge médicale.
- **Inconvénients / Risques :** L'étude ne comporte aucun geste invasif supplémentaire ; seule la gêne mineure liée à quelques minutes d'entretien ou à certaines questions personnelles peut être ressentie.

### 6. Confidentialité
Aucune donnée directement identifiante (nom complet, téléphone, adresse) ne figure sur la fiche d'analyse. Les données sont codées par un identifiant anonyme, stockées dans un support protégé par mot de passe accessible uniquement à l'équipe de recherche, puis exploitées de manière globale.

### 7. Diffusion des résultats
Les résultats feront l'objet d'un travail académique ({ctx['degree_label']}) à la {ctx['faculty_name']} ({ctx['university_name']}) et pourront être publiés dans des revues scientifiques dans le strict respect de l'anonymat. Un résumé peut vous être communiqué sur demande.

### 8. Que faire en cas de question ou de difficulté ?
En cas de gêne psychologique, clinique ou d'interrogation particulière liée à votre état de santé, vous pouvez solliciter l'équipe médicale traitante ou le service d'accueil compétent de la structure partenaire.

### 9. Contacts de l'investigateur
Pour toute information complémentaire : **{ctx['author_name']}** — Tél. : {ctx['student_phone']} | E-mail : {ctx['student_email']}.
"""

    en_part = f"""## PARTICIPANT INFORMATION SHEET (English Version)

**Submitted to Ethics Committee:** {ctx['ethics_committee']}
**Study Title:** {ctx['study_title']}
**Principal Investigator:** {ctx['author_name']} ({ctx['student_level']}, {ctx['faculty_name']}, {ctx['university_name']})
**Supervisor:** {ctx['director']}

### 1. Why this study?
This research investigates: "{ctx['study_title']}". Main purpose: {ctx['study_goal']}.

### 2. Why are you invited?
You are invited because you meet the eligibility profile at {', '.join(ctx['host_institutions']) or ctx['faculty_name']}.

### 3. What does your participation involve?
Your participation consists of completing an anonymous, standardized research questionnaire/data sheet during {ctx['study_period']}.

### 4. Voluntary participation
Participation is completely voluntary. You may decline or withdraw at any time without explanation and without any impact on your medical care or institutional relationships.

### 5. Benefits and drawbacks
- **Benefits:** No monetary compensation is provided, but your responses will contribute to improving clinical care and scientific knowledge.
- **Drawbacks / Risks:** No invasive study procedure is added beyond routine care; only minimal time or mild sensitivity regarding personal questions may occur.

### 6. Confidentiality
No names, phone numbers, or direct personal identifiers appear in the research database. Data are coded anonymously, securely stored under password protection accessible only to the research team, and analyzed in aggregate.

### 7. Results
Findings will support a {ctx['degree_label']} thesis at {ctx['faculty_name']} ({ctx['university_name']}) and scientific publications while preserving full anonymity.

### 8. Support
If any question or clinical concern arises, please contact the attending care team on site.

### 9. Contacts
For further information: **{ctx['author_name']}** — Tel: {ctx['student_phone']} | E-mail: {ctx['student_email']}.
"""
    if bilingual:
        return (fr_part.strip() + "\n\n---\n\n" + en_part.strip()).strip()
    return (en_part if ctx["lang"] == "en" else fr_part).strip()


def _build_deterministic_consent_template(ctx: Dict[str, Any], bilingual: bool) -> str:
    fr_part = f"""## FORMULAIRE DE CONSENTEMENT ÉCLAIRÉ

**Étude :** {ctx['study_title']}
**Institution académique :** {ctx['faculty_name']} — {ctx['university_name']}
**Investigateur principal :** {ctx['author_name']} (Tél. : {ctx['student_phone']} | E-mail : {ctx['student_email']})

Je soussigné(e) M./Mme/Mlle ...............................................................,
déclare avoir lu et compris la notice d'information relative à l'étude intitulée « {ctx['study_title']} ».

J'ai pu poser toutes mes questions à l'investigateur et j'ai reçu des réponses claires et satisfaisantes. J'ai bien compris que ma participation est libre et volontaire, que les données recueillies resteront strictement confidentielles et anonymes, et que je peux retirer mon consentement à tout moment sans justification et sans conséquence sur ma prise en charge.

- [ ] J'accepte librement de participer à cette étude.
- [ ] Je n'accepte pas de participer à cette étude.

Fait à {ctx['city']}, le ........ / ........ / 20......

**Signature du participant (ou représentant légal) :** __________________________

**Signature de l'investigateur :** __________________________ ({ctx['author_name']})
"""

    en_part = f"""## INFORMED CONSENT FORM (English Version)

**Study:** {ctx['study_title']}
**Institution:** {ctx['faculty_name']} — {ctx['university_name']}
**Principal Investigator:** {ctx['author_name']} (Tel: {ctx['student_phone']} | E-mail: {ctx['student_email']})

I, the undersigned Mr./Mrs./Ms. ...............................................................,
confirm that I have read and understood the information sheet for the study titled "{ctx['study_title']}".

I have had the opportunity to ask questions and received clear answers. I understand that my participation is strictly voluntary, that all collected information will remain confidential and anonymous, and that I may withdraw at any time without penalty or impact on my care.

- [ ] I freely agree to participate in this study.
- [ ] I do not agree to participate in this study.

Done at {ctx['city']}, on ........ / ........ / 20......

**Participant (or Legal Guardian) Signature:** __________________________

**Investigator Signature:** __________________________ ({ctx['author_name']})
"""
    if bilingual:
        return (fr_part.strip() + "\n\n---\n\n" + en_part.strip()).strip()
    return (en_part if ctx["lang"] == "en" else fr_part).strip()


def _build_deterministic_authorization_template(ctx: Dict[str, Any]) -> str:
    codir_clause_fr = (
        f" et co-dirigée par {', '.join(ctx['co_directors'])}"
        if ctx["co_directors"]
        else ""
    )
    codir_clause_en = (
        f" and co-supervised by {', '.join(ctx['co_directors'])}"
        if ctx["co_directors"]
        else ""
    )
    sites = ctx["host_institutions"]

    if ctx["lang"] == "en":
        letters = [
            f"""## REQUEST FOR ACADEMIC RESEARCH AUTHORIZATION

**{ctx['author_name']}**
Registration No.: {ctx['matricule']}
{ctx['student_level']}
{ctx['faculty_name']} — {ctx['university_name']}
Tel: {ctx['student_phone']} | E-mail: {ctx['student_email']}

**To {ctx['faculty_head_recipient']}**
{ctx['city']}, on ........ / ........ / 20......

**Subject:** Request for research authorization.

{ctx['faculty_head_salutation']}

I have the honor to respectfully request your authorization to carry out my research work in partial fulfillment of the **{ctx['degree_label']}** degree at **{ctx['faculty_name']}** ({ctx['university_name']}).

My protocol is entitled: **« {ctx['study_title']} »**, supervised by **{ctx['director']}**{codir_clause_en}, scheduled for the period **{ctx['study_period']}**.

Looking forward to your favorable response, please accept, {ctx['faculty_head_salutation'].rstrip(',')}, the assurance of my highest consideration.

**Attachment:** Copy of the research protocol.

**{ctx['author_name']}**"""
        ]
        for site in sites:
            letters.append(
                f"""### Request Addressed to Host Study Site: {site}

**To the Director / Head of {site}**
{ctx['city']}, on ........ / ........ / 20......

**Subject:** Request for authorization to collect research data.

Dear Director,

As a {ctx['student_level']} at **{ctx['faculty_name']}** ({ctx['university_name']}), I respectfully request permission to conduct data collection within **{site}** for my study entitled **« {ctx['study_title']} »** during **{ctx['study_period']}**, under the supervision of **{ctx['director']}**{codir_clause_en}.

I remain committed to strict confidentiality and institutional ethics guidelines.

**Attachments:** Copy of the protocol; Faculty cover note; Ethical clearance request.

**{ctx['author_name']}**"""
            )
        return "\n\n---\n\n".join(letters)

    letters_fr = [
        f"""## DEMANDE D'AUTORISATION DE RECHERCHE

**{ctx['author_name']}**
Matricule : {ctx['matricule']}
{ctx['student_level']}
{ctx['faculty_name']} / {ctx['university_name']}
Tél. : {ctx['student_phone']} | E-mail : {ctx['student_email']}

**À {ctx['faculty_head_recipient']}**
{ctx['city']}, le ........ / ........ / 20......

**Objet :** Demande d'autorisation de recherche.

{ctx['faculty_head_salutation']}

J'ai l'honneur de venir respectueusement auprès de votre haute bienveillance solliciter une autorisation de recherche dans le cadre de mes travaux de fin d'études pour l'obtention du diplôme de **{ctx['degree_label']}** au sein {_french_prep_institution(ctx['faculty_name'])} ({ctx['university_name']}).

Ce travail de recherche porte sur le thème : **« {ctx['study_title']} »**, sous la direction de **{ctx['director']}**{codir_clause_fr}, et est prévu sur la période **{ctx['study_period']}**.

Dans l'attente d'une suite favorable à ma requête, veuillez agréer, {ctx['faculty_head_salutation'].rstrip(',')}, l'expression de mon profond respect et de ma haute considération.

**Pièce jointe :** Protocole de recherche.

**{ctx['author_name']}**"""
    ]
    for site in sites:
        letters_fr.append(
            f"""### Demande adressée au site d'accueil : {site}

**À Monsieur/Madame le Directeur / Responsable de {site}**
{ctx['city']}, le ........ / ........ / 20......

**Objet :** Demande d'autorisation d'enquête / de collecte des données.

Monsieur/Madame le Directeur,

Étudiant(e) ({ctx['student_level']}) {_french_prep_institution(ctx['faculty_name'])} de **{ctx['university_name']}** (Matricule : {ctx['matricule']}), j'ai l'honneur de solliciter l'autorisation de mener une collecte de données au sein de **{site}** durant la période **{ctx['study_period']}**, dans le cadre de mon étude intitulée **« {ctx['study_title']} »**, dirigée par **{ctx['director']}**{codir_clause_fr}.

Je m'engage au respect rigoureux de la confidentialité, du secret médical et du bon fonctionnement du service.

**Pièces jointes :** 01 copie du protocole de recherche ; attestation/demande académique ; dossier de clairance éthique.

**{ctx['author_name']}**"""
        )
    return "\n\n---\n\n".join(letters_fr)


def _build_deterministic_ethics_clearance_template(ctx: Dict[str, Any]) -> str:
    codir_fr = f" et co-dirigée par {', '.join(ctx['co_directors'])}" if ctx["co_directors"] else ""
    codir_en = f" and co-supervised by {', '.join(ctx['co_directors'])}" if ctx["co_directors"] else ""
    sites_line_fr = (
        f" Sites d'étude prévus : {', '.join(ctx['host_institutions'])}."
        if ctx["host_institutions"]
        else ""
    )
    sites_line_en = (
        f" Planned study sites: {', '.join(ctx['host_institutions'])}."
        if ctx["host_institutions"]
        else ""
    )

    if ctx["lang"] == "en":
        return f"""## REQUEST FOR ETHICAL CLEARANCE

**{ctx['author_name']}**
Registration No.: {ctx['matricule']}
{ctx['student_level']}
{ctx['faculty_name']} — {ctx['university_name']}
Tel: {ctx['student_phone']} | E-mail: {ctx['student_email']}

**To the Chairperson of {ctx['ethics_committee']}**
{ctx['city']}, on ........ / ........ / 20......

**Subject:** Application for ethical clearance.

Dear Chairperson,

I respectfully submit for your committee's ethical evaluation my research protocol entitled: **« {ctx['study_title']} »**, conducted in fulfillment of the **{ctx['degree_label']}** degree at **{ctx['faculty_name']}** ({ctx['university_name']}), under the supervision of **{ctx['director']}**{codir_en}.{sites_line_en}

This study complies with the Declaration of Helsinki, strict participant anonymity, free informed consent, and secure data management.

**Attachments:** Research protocol; Participant information sheet; Informed consent form; Data collection form.

**{ctx['author_name']}**"""

    return f"""## DEMANDE DE CLAIRANCE ÉTHIQUE

**{ctx['author_name']}**
Matricule : {ctx['matricule']}
{ctx['student_level']}
{ctx['faculty_name']} / {ctx['university_name']}
Tél. : {ctx['student_phone']} | E-mail : {ctx['student_email']}

**À Monsieur/Madame le Président du {ctx['ethics_committee']}**
{ctx['city']}, le ........ / ........ / 20......

**Objet :** Demande de clairance éthique.

Monsieur/Madame le Président,

J'ai l'honneur de venir très respectueusement auprès de votre haute personnalité solliciter une clairance éthique pour mes travaux de recherche dont le thème s'intitule :
**« {ctx['study_title']} »**, dirigés par **{ctx['director']}**{codir_fr}.

Étudiant(e) ({ctx['student_level']}) {_french_prep_institution(ctx['faculty_name'])} de **{ctx['university_name']}**, je souhaite obtenir l'approbation éthique de votre comité afin de mener cette étude dans le strict respect des principes de la Déclaration d'Helsinki, de l'anonymat et du consentement éclairé des participants.{sites_line_fr}

En espérant que ma demande recevra une attention bienveillante, je vous prie d'agréer, Monsieur/Madame le Président, l'expression de mon profond respect.

**Pièces jointes :** Protocole de recherche ; Notice d'information ; Formulaire de consentement ; Fiche de collecte des données.

**{ctx['author_name']}**"""


def _build_deterministic_confidentiality_template(ctx: Dict[str, Any]) -> str:
    if ctx["lang"] == "en":
        return f"""## RESEARCHER CONFIDENTIALITY DECLARATION

I, the undersigned **{ctx['author_name']}** (Reg. No.: {ctx['matricule']}), {ctx['student_level']} at **{ctx['faculty_name']}** ({ctx['university_name']}), solemnly pledge to:

1. Maintain strict confidentiality regarding all clinical, personal, and institutional data collected for the study « {ctx['study_title']} ».
2. Use collected data exclusively for this approved research protocol under the supervision of **{ctx['director']}**.
3. Enforce physical and digital data protection (coded anonymization, password-protected storage, zero direct personal identifiers).
4. Never disclose participant-identifying data to any unauthorized third party.
5. Uphold international medical research ethics (Declaration of Helsinki) and national data protection standards.

Done at {ctx['city']}, on ........ / ........ / 20......

**Signature:** __________________________ ({ctx['author_name']})"""

    return f"""## DÉCLARATION DE CONFIDENTIALITÉ DU CHERCHEUR

Je soussigné(e), **{ctx['author_name']}** (Matricule : {ctx['matricule']}), {ctx['student_level']} {_french_prep_institution(ctx['faculty_name'])} de **{ctx['university_name']}**, m'engage solennellement à :

1. Respecter la plus stricte confidentialité concernant l'ensemble des données médicales, cliniques et personnelles recueillies dans le cadre de l'étude « {ctx['study_title']} ».
2. N'utiliser ces informations que dans le cadre exclusif de ce protocole de recherche, sous la supervision de mon encadrement scientifique (**{ctx['director']}**).
3. Garantir la sécurité physique et numérique des données (anonymisation systématique par code participant, stockage sécurisé par mot de passe).
4. Ne divulguer aucune donnée identifiante à des tiers sous quelque forme que ce soit.
5. Respecter les principes éthiques de la recherche médicale (Déclaration d'Helsinki) et la réglementation en vigueur.

Fait à {ctx['city']}, le ........ / ........ / 20......

**Signature de l'investigateur :** __________________________ ({ctx['author_name']})"""


def _build_deterministic_commitment_template(ctx: Dict[str, Any]) -> str:
    sites_txt = ", ".join(ctx["host_institutions"]) or ctx["faculty_name"]
    if ctx["lang"] == "en":
        return f"""## INVESTIGATOR COMMITMENT LETTER

I, the undersigned **{ctx['author_name']}** (Reg. No.: {ctx['matricule']}), {ctx['student_level']} at **{ctx['faculty_name']}** ({ctx['university_name']}), hereby commit within the framework of the study « {ctx['study_title']} » to:

- Respect institutional rules and clinical workflows at participating host sites (**{sites_txt}**).
- Ensure complete data integrity, scientific honesty, and participant protection.
- Acknowledge participating host structures in the thesis acknowledgements and resulting publications.
- Share scientific findings and feedback with **{ctx['faculty_name']}** and partner clinical departments upon study completion.

Done at {ctx['city']}, on ........ / ........ / 20......

**Signature:** __________________________ ({ctx['author_name']})"""

    return f"""## LETTRE D'ENGAGEMENT DE L'INVESTIGATEUR

Je soussigné(e), **{ctx['author_name']}** (Matricule : {ctx['matricule']}), {ctx['student_level']} {_french_prep_institution(ctx['faculty_name'])} de **{ctx['university_name']}**, m'engage dans le cadre de mon travail de recherche intitulé « {ctx['study_title']} » à :

- Respecter le règlement intérieur et le bon fonctionnement des structures d'accueil (**{sites_txt}**).
- Veiller à l'intégrité scientifique des données recueillies et au strict anonymat des participants.
- Mentionner les structures partenaires dans les remerciements de la thèse / du mémoire et des publications associées.
- Assurer la restitution et le retour des résultats scientifiques auprès de la **{ctx['faculty_name']}** et des services partenaires.

Fait à {ctx['city']}, le ........ / ........ / 20......

**Signature de l'investigateur :** __________________________ ({ctx['author_name']})"""


def _build_deterministic_sub_annex(section_id: str, ctx: Dict[str, Any], bilingual: bool = False) -> str:
    if section_id == "annexe_notice_information":
        return _build_deterministic_notice_template(ctx, bilingual=bilingual)
    if section_id == "annexe_consentement":
        return _build_deterministic_consent_template(ctx, bilingual=bilingual)
    if section_id == "annexe_autorisation_recherche":
        return _build_deterministic_authorization_template(ctx)
    if section_id == "annexe_clairance_ethique":
        return _build_deterministic_ethics_clearance_template(ctx)
    if section_id == "annexe_confidentialite":
        return _build_deterministic_confidentiality_template(ctx)
    if section_id == "annexe_engagement":
        return _build_deterministic_commitment_template(ctx)
    raise ValueError(f"Sous-annexe non reconnue : {section_id}")


def generate_sub_annex_content(
    project: ProtocolProject,
    section_id: str,
    user_instruction: str = "",
    bilingual: bool = False,
    use_ai: bool = True,
) -> Dict[str, Any]:
    """
    Generate or improve any dedicated non-CRF sub-annex tailored dynamically to the
    student's exact Faculty/Institute, Dean/Director, Ethics Committee, Host Sites, and Identity.
    """
    if section_id not in NON_CRF_SUB_ANNEX_IDS:
        raise ValueError(f"Section {section_id} n'est pas une sous-annexe administrative/éthique dédiée.")

    ctx = resolve_faculty_admin_context(project)
    lang = ctx["lang"]
    auto_bilingual = bool(
        bilingual
        or "bilingue" in (user_instruction or "").lower()
        or "bilingual" in (user_instruction or "").lower()
        or "english version" in (user_instruction or "").lower()
    )

    template_md = _build_deterministic_sub_annex(section_id, ctx, bilingual=auto_bilingual)
    current_sec = project.sections.get(section_id)
    existing_md = (current_sec.content_markdown or "").strip() if current_sec else ""

    final_md = template_md
    model_used = "faculty-aware-template"
    commentary = (
        f"Sous-annexe générée automatiquement selon votre établissement ({ctx['faculty_name']} — {ctx['university_name']}) et votre comité d'éthique ({ctx['ethics_committee']})."
        if lang == "fr"
        else f"Sub-appendix generated automatically for your institution ({ctx['faculty_name']} — {ctx['university_name']}) and ethics committee ({ctx['ethics_committee']})."
    )

    if use_ai:
        emit_pipeline_step(
            "generate_sub_annex",
            f"Adaptation de la sous-annexe ({ctx['faculty_name']})...",
            f"Generating faculty-specific sub-appendix ({ctx['faculty_name']})...",
            progress_pct=55,
        )
        methodo_ethics = (
            (project.sections.get("chap3_ethique").content_markdown or "").strip()[:1000]
            if project.sections.get("chap3_ethique")
            else ""
        )
        methodo_proc = (
            (project.sections.get("chap3_outils_procedure").content_markdown or "").strip()[:1000]
            if project.sections.get("chap3_outils_procedure")
            else ""
        )

        is_request_letter = section_id in ("annexe_autorisation_recherche", "annexe_clairance_ethique")
        layout_contract = (
            "FORMAT OBLIGATOIRE DE LETTRE DE DEMANDE ADMINISTRATIVE (Cameroun / Francophonie) :\n"
            "1. Lieu & date sur une ligne dédiée (ex. `Yaoundé, le ____ / ____ / 20____`).\n"
            "2. Destinataire exact sur 2–3 lignes commençant par `À` puis la qualité exacte (`Monsieur le Directeur...` / `Monsieur le Président du...`).\n"
            "3. Ligne `Objet : ...` claire et précise + ligne `P.J. : ...` (pièces jointes).\n"
            "4. Formule d'appel respectueuse terminée par une virgule (`Monsieur le Directeur,`).\n"
            "5. Corps structuré en 3 paragraphes académiques (motif de la demande, objectifs/méthodologie/confidentialité, formule de politesse).\n"
            "6. Bloc signature final (`L'Investigateur principal / Le Candidat`)."
            if is_request_letter
            else "FORMAT OBLIGATOIRE DE FORMULAIRE MÉDICAL / ÉTHIQUE (OMS / CIER-FMSB) :\n"
            "1. Sections numérotées claires (1., 2., 3...) en langage accessible et rigoureux.\n"
            "2. Pour le consentement éclairé ou les engagements : inclure 4 à 6 déclarations précises précédées de la case `☐ ` (elles seront automatiquement rendues dans un tableau structuré Oui/Non dans Word et PDF).\n"
            "3. Ne pas ajouter de mise en page ASCII manuelle : l'en-tête officiel encadré et le tableau de signatures (Participant / Investigateur / Témoin impartial) sont générés automatiquement à l'export."
        )

        prompt = f"""
Tu es un expert en rédaction réglementaire et éthique de protocoles médicaux (espace CAMES / Cameroun).
Tu génères ou améliores la sous-annexe `{section_id}`.

{layout_contract}

RÈGLE ABSOLUE D'IDENTITÉ INSTITUTIONNELLE (NE JAMAIS INVENTER NI CODER EN DUR UNE AUTRE FACULTÉ) :
- Université : {ctx['university_name']}
- Faculté / École / Institut : {ctx['faculty_name']}
- Ville inférée : {ctx['city']}
- Destinataire hiérarchique facultaire : {ctx['faculty_head_recipient']}
- Comité d'éthique compétent : {ctx['ethics_committee']}
- Sites hospitaliers / d'accueil : {', '.join(ctx['host_institutions']) or 'Non précisés'}
- Étudiant(e) : {ctx['author_name']} | Matricule : {ctx['matricule']} | Niveau : {ctx['student_level']} | Tél : {ctx['student_phone']} | Email : {ctx['student_email']}
- Directeur : {ctx['director']} | Co-directeurs : {', '.join(ctx['co_directors']) or 'Aucun'}
- Titre de l'étude : {ctx['study_title']}
- Langue cible : {'bilingue complet Français + English' if auto_bilingual else ('Anglais' if lang == 'en' else 'Français')}

TEXTE EXISTANT DANS LA SECTION (à améliorer/conserver si pertinent) :
{existing_md[:3500] or '(Vide)'}

EXTRAIT MÉTHODOLOGIE / ÉTHIQUE DU PROTOCOLE :
{methodo_ethics or methodo_proc or '(Non encore rédigé)'}

CONSIGNE UTILISATEUR :
{user_instruction.strip() or 'Produire un document prêt à être déposé et signé, fidèle aux métadonnées ci-dessus.'}

MODÈLE DE RÉFÉRENCE ADAPTÉ À LA FACULTÉ (à enrichir sans perdre aucune information exacte) :
{template_md}
"""
        try:
            res = gemini_router.generate_structured(
                role="drafting",
                contents=prompt,
                schema=_AISubAnnexOut,
                temperature=0.15,
            )
            dto: _AISubAnnexOut = res["data"]
            if dto.content_markdown and len(dto.content_markdown.strip()) >= 80:
                final_md = dto.content_markdown.strip()
                model_used = str(res["model_used"])
                if dto.assistant_commentary.strip():
                    commentary = dto.assistant_commentary.strip()
        except Exception:
            pass

    if section_id not in project.sections:
        project.sections[section_id] = SectionState(section_id=section_id)
    sec = project.sections[section_id]
    sec.content_markdown = final_md
    sec.content_html = markdown_to_clean_html(final_md)
    sec.status = StepStatus.IN_PROGRESS

    return {
        "section_id": section_id,
        "content_markdown": sec.content_markdown,
        "content_html": sec.content_html,
        "model_used": model_used,
        "assistant_commentary": commentary,
        "faculty_context": {
            "faculty_name": ctx["faculty_name"],
            "university_name": ctx["university_name"],
            "faculty_head_recipient": ctx["faculty_head_recipient"],
            "ethics_committee": ctx["ethics_committee"],
            "host_institutions": ctx["host_institutions"],
            "city": ctx["city"],
        },
    }
