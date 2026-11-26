from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from app.data.i18n import normalize_lang
from app.schemas.protocol import CRFFieldSpec, ProtocolProject, SectionState, StepStatus, VariableItem


# ---------------------------------------------------------------------------
# Checkbox PNG graphic cache (crisp 300-DPI drawn square for Word .docx)
# ---------------------------------------------------------------------------
_CHECKBOX_ICON_PATH: Optional[Path] = None


def get_crf_checkbox_png_bytes(checked: bool = False) -> bytes:
    """Return PNG bytes for a crisp drawn graphical checkbox square."""
    size = 128
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    pad = 10
    # Outer white background + crisp bordeaux medical stroke
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=14,
        fill=(255, 255, 255, 255),
        outline=(108, 40, 38, 255),
        width=9,
    )
    if checked:
        draw.line(
            [(30, 66), (54, 92), (98, 36)],
            fill=(108, 40, 38, 255),
            width=12,
        )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers for parsing Annexe 1 Markdown & project variables into CRFFieldSpec
# ---------------------------------------------------------------------------

_ANNEXE1_START_RE = re.compile(
    r"^#{1,4}\s*(?:"
    r"(?:ANNEXE|APPENDIX)\s*(?:1|I\b)|"
    r"(?:(?:ANNEXE|APPENDIX)\s*(?:[1-9]|I+)\s*[:\-–]\s*)?(?:FICHE\s+DE\s+COLLECTE|FORMULAIRE\s+D['’]ENREGISTREMENT|FORMULAIRE\s+DE\s+COLLECTE|CAHIER\s+D['’]OBSERVATION|QUESTIONNAIRE\s+D['’]ENQU[ÊE]TE|QUESTIONNAIRE|CRF\b|CASE\s+REPORT\s+FORM|DATA\s+COLLECTION)"
    r")\b",
    re.IGNORECASE,
)
_NEXT_ANNEXE_RE = re.compile(
    r"^#{1,4}\s*(?:"
    r"(?:ANNEXE|APPENDIX)\s*(?:[2-9]|II|III|IV|V|VI|VII)\b|"
    r"NOTICE\s+D['’]INFORMATION|"
    r"INFORMATION\s+SHEET|"
    r"FORMULAIRE\s+DE\s+CONSENTEMENT|"
    r"CONSENT\s+FORM|"
    r"CONSENTEMENT\s+[ÉE]CLAIR[ÉE]|"
    r"CLAIRANCE\s+[ÉE]THIQUE|"
    r"ETHICAL\s+CLEARANCE|"
    r"DEMANDE\s+D['’]AUTORISATION|"
    r"AUTORISATION\s+ADMINISTRATIVE|"
    r"D[ÉE]CLARATION\s+DE\s+CONFIDENTIALIT[ÉE]|"
    r"LETTRE\s+D['’]ENGAGEMENT"
    r")\b",
    re.IGNORECASE,
)
_SECTION_HEAD_RE = re.compile(r"^#{2,4}\s*(.+)$")
_CHECKBOX_SPLIT_RE = re.compile(r"(?:\[\s*[xX ]?\s*\]|☐)")

_UNIT_HINTS = [
    "mois / ans",
    "mois/ans",
    "ans / mois",
    "grammes",
    "kg/m²",
    "kg/m2",
    "minutes",
    "heures",
    "jours",
    "semaines",
    "mois",
    "ans",
    "SA",
    "mmHg",
    "FCFA",
    "g/dL",
    "UI/L",
    "ng/mL",
    "cm",
    "mm",
    "kg",
    "%",
]


def is_crf_annexe_content(title: str, text: str) -> bool:
    """Check if an annex title or body represents a CRF / data collection sheet."""
    t_lower = (title or "").lower()
    if any(
        k in t_lower
        for k in (
            "fiche de collecte",
            "data collection",
            "cahier d'observation",
            "questionnaire",
            "formulaire d'enregistrement",
            "formulaire de collecte",
            "case report form",
            "crf",
            "grille de collecte",
            "guide d'entretien",
        )
    ):
        return True
    body = (text or "").lower()
    has_boxes = body.count("[ ]") + body.count("☐") + body.count("[_") >= 3
    has_sections = "section a" in body or "section 1" in body
    return bool(has_boxes and has_sections)


def extract_crf_annexe_block(annexes_md: str) -> Tuple[str, str, str, str]:
    """
    Find and isolate the CRF / data collection form block anywhere in annexes_md,
    regardless of whether it is positioned at Annexe 1, Annexe 2, Annexe 3, or Annexe N!
    Returns (before_crf, crf_block, after_crf, original_title).
    """
    if not annexes_md.strip():
        return "", "", "", ""
    lines = annexes_md.splitlines()

    # Locate all annex header lines (supporting BOTH Markdown # headers AND raw Word/PDF headers without #)
    header_indices: List[int] = []
    for idx, line in enumerate(lines):
        s = line.strip()
        if not s or len(s) > 130:
            continue
        has_ctrl = bool("[ ]" in s or "☐" in s or "□" in s or "____" in s or "....." in s)
        if re.match(r"^#{1,3}\s+(?:ANNEXE|APPENDIX)\b", s, re.IGNORECASE):
            header_indices.append(idx)
        elif re.match(
            r"^#{1,3}\s+(?:FICHE|FORMULAIRE|QUESTIONNAIRE|NOTICE|CONSENTEMENT|CLAIRANCE|LETTRE|D[ÉE]CLARATION|CAHIER|CASE\s+REPORT)\b",
            s,
            re.IGNORECASE,
        ):
            header_indices.append(idx)
        elif not has_ctrl and not s.startswith(("-", "•", "*")) and (
            re.match(r"^(?:\*\*)?(?:ANNEXE|APPENDIX)\s+(?:\d+|[IVX]+)\b", s, re.IGNORECASE)
            or re.match(
                r"^(?:\*\*)?(?:FICHE\s+(?:DE\s+COLLECTE|D['’]ENQU[ÊE]TE|D['’]INFORMATION)|"
                r"FORMULAIRE\s+(?:DE\s+COLLECTE|D['’]ENREGISTREMENT|DE\s+CONSENTEMENT)|"
                r"NOTICE\s+D['’]INFORMATION|CONSENTEMENT\s+[ÉE]CLAIR[ÉE]|CLAIRANCE\s+[ÉE]THIQUE|"
                r"DEMANDE\s+D['’]AUTORISATION|LETTRE\s+D['’]ENGAGEMENT|CAHIER\s+D['’]OBSERVATION|"
                r"QUESTIONNAIRE(?:\s+D['’]ENQU[ÊE]TE)?|CASE\s+REPORT\s+FORM)\b",
                s,
                re.IGNORECASE,
            )
        ):
            header_indices.append(idx)

    if not header_indices:
        for idx, line in enumerate(lines):
            s = line.strip()
            if s.startswith("## "):
                header_indices.append(idx)

    if not header_indices:
        return "", annexes_md.strip(), "", "## ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES"

    # Slice into distinct annex blocks
    blocks: List[Tuple[int, int, str, str, str]] = []
    for i, h_idx in enumerate(header_indices):
        next_h = header_indices[i + 1] if i + 1 < len(header_indices) else len(lines)
        title = lines[h_idx].strip()
        body = "\n".join(lines[h_idx + 1 : next_h]).strip()
        full_text = "\n".join(lines[h_idx:next_h]).strip()
        blocks.append((h_idx, next_h, title, body, full_text))

    # Identify the block that corresponds to the CRF
    crf_block_idx: Optional[int] = None
    for idx, (_, _, title, body, _) in enumerate(blocks):
        if is_crf_annexe_content(title, body):
            crf_block_idx = idx
            break

    if crf_block_idx is None:
        # Fallback 1: check if block matches _ANNEXE1_START_RE
        for idx, (_, _, title, body, _) in enumerate(blocks):
            if _ANNEXE1_START_RE.match(title):
                crf_block_idx = idx
                break

    if crf_block_idx is None:
        crf_block_idx = 0

    s_idx, e_idx, title, body, full_text = blocks[crf_block_idx]
    before = "\n".join(lines[:s_idx]).strip()
    crf_block = full_text
    after = "\n".join(lines[e_idx:]).strip()
    return before, crf_block, after, title


def extract_annexe1_block(annexes_md: str) -> Tuple[str, str, str]:
    """
    Split annexes_md into (before_crf, crf_block, after_crf).
    Agnostic to whether the CRF is Annexe 1, Annexe 2, Annexe 3, or Annexe N.
    Preserves all other annexes intact.
    """
    before, crf, after, _ = extract_crf_annexe_block(annexes_md)
    return before, crf, after


def get_crf_markdown_from_project(project: ProtocolProject) -> str:
    """
    Retrieve the project's CRF / data collection Markdown, prioritizing the dedicated
    `annexe_fiche_collecte` sub-section and falling back to `annexes` for legacy projects.
    """
    crf_sec = project.sections.get("annexe_fiche_collecte")
    if crf_sec and crf_sec.content_markdown and crf_sec.content_markdown.strip():
        return crf_sec.content_markdown.strip()

    ann_sec = project.sections.get("annexes")
    if ann_sec and ann_sec.content_markdown and ann_sec.content_markdown.strip():
        _, crf_block, _, title = extract_crf_annexe_block(ann_sec.content_markdown)
        if crf_block and (
            is_crf_annexe_content(title, crf_block)
            or "[ ]" in crf_block
            or "☐" in crf_block
            or "____" in crf_block
            or bool(re.search(r"(?m)^(?:-\s+\*\*|\d+\.\s+).*[:：]", crf_block))
        ):
            return crf_block.strip()
    return ""


def _clean_md_text(raw: str) -> str:
    txt = re.sub(r"\*\*([^*]+)\*\*", r"\1", raw or "")
    txt = re.sub(r"\*([^*]+)\*", r"\1", txt)
    txt = txt.replace("**", "")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def _slug_id(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", _clean_md_text(text).lower()).strip("_")
    return slug[:40] or fallback


def _extract_conditional_sub(raw_line: str) -> Tuple[str, str]:
    """
    Extract inline conditional follow-up prompts like:
      '(Si oui, préciser : [__________________])'
      '(Âge gestationnel : ____ SA)'
      '/ Autre : [__________________]'
    Returns (cleaned_main_line, sub_field_label).
    """
    sub_label = ""
    work = raw_line

    # Parenthetical prompt containing ____ or [...]
    paren_m = re.search(
        r"\(([^()]*?(?:____|\[_{2,}\]|⟦[^⟧]*⟧)[^()]*?)\)",
        work,
    )
    if paren_m:
        inside = paren_m.group(1)
        lbl = re.sub(r"\[[^\]]*\]|_+|\b20__\b", "", inside)
        lbl = re.sub(r"\s*:\s*", " ", lbl)
        lbl = _clean_md_text(lbl).strip(" :-")
        if lbl:
            sub_label = lbl
        work = (work[: paren_m.start()] + " " + work[paren_m.end() :]).strip()

    # Trailing 'Autre : [_____]' on a checkbox line
    autre_m = re.search(
        r"(?:\[\s*[xX ]?\s*\]|☐)?\s*(?:Autre|Autres?)\s*[:：]\s*(?:\[_{2,}\]|_+)",
        work,
        re.IGNORECASE,
    )
    if autre_m and not sub_label:
        sub_label = "Si Autre, préciser"
        work = work[: autre_m.start()].strip() + " [ ] Autre"

    return work, sub_label


def _parse_checkbox_options(text_block: str) -> List[str]:
    parts = _CHECKBOX_SPLIT_RE.split(text_block)
    opts: List[str] = []
    for p in parts[1:]:
        cand = _clean_md_text(p)
        cand = re.sub(r"[:：]\s*(?:\[_{2,}\]|_+|⟦[^⟧]*⟧).*$", "", cand).strip(" /|,;-")
        if cand and cand not in opts:
            opts.append(cand)
    return opts


def _infer_unit(label: str, answer_spec: str) -> str:
    combined = f"{label} {answer_spec}"
    for u in _UNIT_HINTS:
        if re.search(rf"(?i)(?:\b|/|_| ){re.escape(u)}(?:\b|$|\))", combined):
            return u
    return ""


def _infer_field_from_spec(
    code: str,
    label: str,
    answer_spec: str,
    section_title: str,
    extra_sub_label: str = "",
) -> CRFFieldSpec:
    cleaned_spec, inline_sub = _extract_conditional_sub(answer_spec)
    sub_field = extra_sub_label or inline_sub
    clean_lbl = _clean_md_text(label).rstrip(" :：")

    has_checkboxes = bool(_CHECKBOX_SPLIT_RE.search(cleaned_spec))
    options = _parse_checkbox_options(cleaned_spec) if has_checkboxes else []

    lower_lbl = clean_lbl.lower()
    lower_opts = [o.lower() for o in options]

    if options:
        if len(options) == 2 and set(lower_opts) == {"oui", "non"}:
            ftype = "dichotomous"
        elif any(
            kw in lower_lbl
            for kw in (
                "choix multiple",
                "multiple choice",
                "complication",
                "malformation",
                "antécédent",
                "procédure",
                "signe",
                "symptôme",
                "facteur",
                "médicament",
            )
        ) and not any(kw in lower_opts for kw in ("oui", "non")):
            ftype = "multi_choice"
        else:
            ftype = "single_choice"
    elif "____/____" in answer_spec or "date" in lower_lbl:
        ftype = "date"
    elif (
        "____" in re.sub(r"\[_{4,}\]", "", answer_spec)
        and "[" not in answer_spec
    ) or any(
        lower_lbl.startswith(k)
        for k in (
            "âge",
            "age",
            "poids",
            "taille",
            "durée",
            "délai",
            "rang",
            "score",
            "nombre",
            "indice",
            "fréquence",
            "température",
            "glycémie",
        )
    ):
        ftype = "number"
    else:
        ftype = "text"

    if ftype in ("dichotomous", "single_choice", "multi_choice", "date"):
        unit = ""
    elif ftype == "number":
        unit = _infer_unit(clean_lbl, cleaned_spec)
    else:
        unit = _infer_unit("", cleaned_spec)
        if unit and "____" in cleaned_spec and "[" not in cleaned_spec:
            ftype = "number"

    fid = _slug_id(f"{code}_{clean_lbl}" if code else clean_lbl, f"field_{code or 'item'}")
    placeholder = "JJ / MM / AAAA" if ftype == "date" else (f"Ex. valeur ({unit})" if unit else "")

    return CRFFieldSpec(
        field_id=fid,
        code=code,
        section_title=section_title,
        label=clean_lbl,
        field_type=ftype,
        options=options if options else (["Oui", "Non"] if ftype == "dichotomous" else []),
        unit=unit,
        placeholder=placeholder,
        sub_field_label=sub_field,
        required=code.upper().startswith(("A1", "A4", "B1", "D1", "D4", "E")),
    )


_SITE_LABEL_KEYWORDS = (
    "site d'étude",
    "site d’étude",
    "site de l'étude",
    "site de l’étude",
    "site d'inclusion",
    "site d’inclusion",
    "site de collecte",
    "site hospitalier",
    "hôpital",
    "hopital",
    "hospital",
    "établissement",
    "etablissement",
    "centre d'étude",
    "centre de collecte",
    "centre d'inclusion",
    "formation sanitaire",
    "prison",
    "lieu d'inclusion",
    "lieu de collecte",
    "study site",
    "inclusion site",
)


def _is_site_field_label(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    if any(kw in low for kw in _SITE_LABEL_KEYWORDS):
        return True
    return bool(re.search(r"\b(?:site|sites|centre|centres)\b", low)) and not any(
        bad in low for bad in ("visite", "parasite", "web", "internet", "anatomique", "lésion", "lesion", "injection")
    )


def _clean_site_option(opt: str) -> str:
    s = re.sub(r"^[\-\*•☐☑✓\[\]\s]+", "", (opt or "").strip())
    s = re.sub(r"(?i)^(?:autre|other)\s*(?:\(.*?\))?\s*:?.*$", "", s).strip()
    s = re.sub(r"_{2,}|\.{3,}", "", s).strip(" :;,-/")
    if len(s) < 2 or len(s) > 90:
        return ""
    return s


def extract_hospital_sites_from_project(project: ProtocolProject) -> List[str]:
    """Extract study/hospital site choices from CRF fields, CRF markdown, and cover metadata.

    Priority:
      1. Structured `project.crf_fields` option lists for any site/hospital/establishment field
         (ensures 100% parity with the paper CRF preview).
      2. Full CRF markdown (`annexe_fiche_collecte` / `annexes`) — inline checkboxes,
         slash/semicolon lists, or indented bullet lists under a site/hospital header.
      3. `project.cover.host_institutions`.
    """
    sites: List[str] = []
    seen_lower: set[str] = set()

    def _add_site(raw: str) -> None:
        cleaned = _clean_site_option(raw)
        if not cleaned:
            return
        key = cleaned.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            sites.append(cleaned)

    # 1. Structured CRF fields (exact parity with Aperçu fiche papier)
    for fld in project.crf_fields or []:
        if fld.options and (_is_site_field_label(fld.label) or _is_site_field_label(fld.field_id)):
            for opt in fld.options:
                _add_site(opt)

    # 2. Full CRF markdown scan (all lines, not just first 15)
    crf_md = get_crf_markdown_from_project(project)
    if crf_md:
        lines = crf_md.splitlines()
        for idx, line in enumerate(lines):
            s = line.strip()
            if not s or not _is_site_field_label(s):
                continue
            # 2a. Inline checkbox options on the same line: [ ] Site A  [ ] Site B
            cleaned_line = re.sub(r"(?i)/?\s*(?:Autre|Other)\s*:.*$", "", s)
            opts = _parse_checkbox_options(cleaned_line)
            for o in opts:
                _add_site(o)

            # 2b. Inline delimited options after colon: "Site d'étude : CHUY / HGY / HCY"
            if not opts and ":" in cleaned_line:
                after_colon = cleaned_line.split(":", 1)[1].strip()
                if after_colon and not re.fullmatch(r"[\s_\.\-\[\]]+", after_colon):
                    if any(sep in after_colon for sep in ("/", ";", "|", "•")):
                        for part in re.split(r"\s*[/;|•]\s*", after_colon):
                            _add_site(part)

            # 2c. Following indented/bullet/checkbox lines belonging to this site field
            for nxt in lines[idx + 1 : idx + 12]:
                ns = nxt.strip()
                if not ns:
                    if sites:
                        break
                    continue
                if re.match(r"^(?:#{1,6}\s+|SECTION\s+[A-Z]|(?:\*\*)?[A-Z]\d+[\.\):])", ns, re.IGNORECASE):
                    break
                nxt_opts = _parse_checkbox_options(re.sub(r"(?i)/?\s*(?:Autre|Other)\s*:.*$", "", ns))
                if nxt_opts:
                    for o in nxt_opts:
                        _add_site(o)
                elif re.match(r"^(?:[-*•☐]|\[\s*\]|\d+[\.\)])\s+", ns):
                    _add_site(re.sub(r"^(?:[-*•☐]|\[\s*\]|\d+[\.\)])\s+", "", ns))
                else:
                    break

    # 3. Cover host_institutions (added if not already captured from CRF)
    for inst in project.cover.host_institutions or []:
        if inst and inst.strip():
            _add_site(inst)

    return sites[:12]


def extract_crf_fields_from_project(project: ProtocolProject) -> List[CRFFieldSpec]:
    """
    Extract structured CRFFieldSpec objects from the dedicated CRF sub-section
    (`annexe_fiche_collecte` or fallback `annexes`) and merge any additional
    variables from `project.variables`.
    """
    fields: List[CRFFieldSpec] = []
    seen_ids: set[str] = set()

    def _add_field(spec: CRFFieldSpec) -> None:
        base_id = spec.field_id
        idx = 2
        while spec.field_id in seen_ids:
            spec.field_id = f"{base_id}_{idx}"
            idx += 1
        seen_ids.add(spec.field_id)
        fields.append(spec)

    ann1_md = get_crf_markdown_from_project(project)
    if ann1_md:
        from app.services.crf_extractor import _preprocess_raw_crf_text

        ann1_md = _preprocess_raw_crf_text(ann1_md)
        lines = ann1_md.splitlines()
        current_section = "SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES"

        i = 0
        while i < len(lines):
            raw = lines[i]
            s = raw.strip()
            if not s or _ANNEXE1_START_RE.match(s) or s.startswith("---"):
                i += 1
                continue

            # Section header line: ### SECTION A : ...
            sec_m = _SECTION_HEAD_RE.match(s)
            if sec_m:
                current_section = _clean_md_text(sec_m.group(1))
                i += 1
                continue

            # Skip the 3 top identification metadata lines before SECTION A
            # (they map to PatientRecord.patient_code / inclusion_date / hospital_site / investigator_name)
            lower_s = s.lower()
            if (
                "**code participant" in lower_s
                or "**hôpital d'inclusion" in lower_s
                or "**nom de l'enquêteur" in lower_s
            ):
                i += 1
                continue

            # Top-level question bullet: - **A1. Label :** rest, or - A1. Label : rest, or 1. Label : rest
            top_m = re.match(
                r"^-\s+\*\*([A-Z]\d+)\.\s*([^:*]+?)\s*:\*\*\s*(.*)$",
                s,
            ) or re.match(
                r"^-\s+\*\*([^:*]+?)\s*:\*\*\s*(.*)$",
                s,
            ) or re.match(
                r"^(?:[-*•]\s+)?(?:\*\*)?(?:([A-Za-z]\d+|Q\d+|\d+)\.?\s+)?([^*:：]+?)(?:\*\*)?\s*[:：]\s*(.*)$",
                s,
            )
            if top_m:
                groups = top_m.groups()
                if len(groups) == 3:
                    q_code = (groups[0] or "").strip()
                    q_label = (groups[1] or "").strip().strip("*").strip()
                    q_rest = (groups[2] or "").strip().lstrip("*").strip()
                else:
                    q_code, q_label, q_rest = "", groups[0].strip(), groups[1].strip()

                # Gather immediately following indented child lines (indent >= 2 spaces)
                children: List[Tuple[int, str]] = []
                j = i + 1
                while j < len(lines):
                    nxt_raw = lines[j]
                    nxt_s = nxt_raw.strip()
                    if not nxt_s:
                        j += 1
                        continue
                    indent = len(nxt_raw) - len(nxt_raw.lstrip(" "))
                    if indent >= 2 and nxt_s.startswith("-"):
                        children.append((indent, nxt_s))
                        j += 1
                    else:
                        break

                if q_rest:
                    # Top line already defines the question control!
                    # Check if the child line is a follow-up ("Si oui, résultat : ...")
                    inline_sub = ""
                    extra_children: List[Tuple[int, str]] = []
                    for ch_indent, ch_txt in children:
                        ch_body = ch_txt.lstrip("- ").strip()
                        if (
                            re.match(r"^(?:\*\*)?si\s+oui\b", ch_body, re.IGNORECASE)
                            and not _CHECKBOX_SPLIT_RE.search(ch_body)
                        ):
                            ch_lbl = _clean_md_text(ch_body.split(":")[0])
                            inline_sub = ch_lbl
                        else:
                            extra_children.append((ch_indent, ch_txt))

                    _add_field(
                        _infer_field_from_spec(
                            code=q_code,
                            label=q_label,
                            answer_spec=q_rest,
                            section_title=current_section,
                            extra_sub_label=inline_sub,
                        )
                    )
                    # Process any standalone conditional/option child questions (e.g. C1.1)
                    sub_idx = 1
                    for ch_indent, ch_txt in extra_children:
                        ch_body = ch_txt.lstrip("- ").strip()
                        if ":" in ch_body:
                            sub_lhs, sub_rhs = ch_body.split(":", 1)
                            sub_code = f"{q_code}.{sub_idx}" if q_code else ""
                            sub_idx += 1
                            _add_field(
                                _infer_field_from_spec(
                                    code=sub_code,
                                    label=f"{q_label} — {_clean_md_text(sub_lhs)}",
                                    answer_spec=sub_rhs.strip(),
                                    section_title=current_section,
                                )
                            )
                else:
                    # Group header or multi-line option block!
                    # Classify children: option bullets vs named sub-questions
                    are_option_bullets = False
                    if children:
                        first_body = children[0][1].lstrip("- ").strip()
                        if first_body.startswith("[ ]") or first_body.startswith("☐"):
                            are_option_bullets = True

                    if are_option_bullets:
                        # Combine all option bullets + any trailing "Autre ... : [____]" into ONE field
                        combined_opts: List[str] = []
                        sub_follow = ""
                        for _, ch_txt in children:
                            ch_body = ch_txt.lstrip("- ").strip()
                            if _CHECKBOX_SPLIT_RE.search(ch_body):
                                cleaned_ch, sub_inline = _extract_conditional_sub(ch_body)
                                if sub_inline and not sub_follow:
                                    sub_follow = sub_inline
                                combined_opts.extend(_parse_checkbox_options(cleaned_ch))
                            elif ":" in ch_body and ("____" in ch_body or "[" in ch_body):
                                sub_lhs = _clean_md_text(ch_body.split(":", 1)[0])
                                if not sub_follow:
                                    sub_follow = sub_lhs
                        rebuilt_spec = "  ".join(f"[ ] {o}" for o in combined_opts)
                        _add_field(
                            _infer_field_from_spec(
                                code=q_code,
                                label=q_label,
                                answer_spec=rebuilt_spec,
                                section_title=current_section,
                                extra_sub_label=sub_follow,
                            )
                        )
                    else:
                        # Nested named sub-questions (e.g. B2.1, B2.2, E3.1, E3.2)
                        k = 0
                        sub_idx = 1
                        while k < len(children):
                            ch_indent, ch_txt = children[k]
                            ch_body = ch_txt.lstrip("- ").strip()
                            if ":" in ch_body:
                                sub_lhs, sub_rhs = ch_body.split(":", 1)
                                sub_lhs_clean = _clean_md_text(sub_lhs)
                                sub_rhs_clean = re.sub(r"^\*+|\*+$", "", sub_rhs.strip()).strip()
                                # Collect deeper-indented option lines (e.g. E3.2 Trophicité)
                                if not sub_rhs_clean:
                                    deep_opts: List[str] = []
                                    sub_follow = ""
                                    m = k + 1
                                    while m < len(children) and children[m][0] > ch_indent:
                                        deep_body = children[m][1].lstrip("- ").strip()
                                        cleaned_d, d_sub = _extract_conditional_sub(deep_body)
                                        if d_sub and not sub_follow:
                                            sub_follow = d_sub
                                        deep_opts.extend(_parse_checkbox_options(cleaned_d))
                                        m += 1
                                    rebuilt_spec = "  ".join(f"[ ] {o}" for o in deep_opts)
                                    sub_code = f"{q_code}.{sub_idx}" if q_code else ""
                                    sub_idx += 1
                                    _add_field(
                                        _infer_field_from_spec(
                                            code=sub_code,
                                            label=f"{q_label} — {sub_lhs_clean}",
                                            answer_spec=rebuilt_spec,
                                            section_title=current_section,
                                            extra_sub_label=sub_follow,
                                        )
                                    )
                                    k = m
                                    continue

                                sub_code = f"{q_code}.{sub_idx}" if q_code else ""
                                sub_idx += 1
                                _add_field(
                                    _infer_field_from_spec(
                                        code=sub_code,
                                        label=f"{q_label} — {sub_lhs_clean}",
                                        answer_spec=sub_rhs_clean,
                                        section_title=current_section,
                                    )
                                )
                            k += 1

                i = j
                continue

            i += 1

    # Merge any project.variables not already represented in fields
    existing_labels = {f.label.lower() for f in fields}
    for idx, var in enumerate(project.variables or [], start=1):
        v_name = (var.name or "").strip()
        if not v_name or v_name.lower() in existing_labels:
            continue
        mod = (var.modalities or "").strip()
        sec = (var.category or "SECTION A : VARIABLES OPÉRATIONNELLES").strip()
        if mod and ("/" in mod or "oui" in mod.lower()):
            opts = [o.strip(" []☐") for o in re.split(r"[/|;]|☐|\[\s*\]", mod) if o.strip(" []☐")]
            spec = _infer_field_from_spec(
                code=f"V{idx}",
                label=v_name,
                answer_spec="  ".join(f"[ ] {o}" for o in opts) if opts else mod,
                section_title=sec,
            )
        elif "quantitat" in (var.measurement_scale or "").lower():
            spec = CRFFieldSpec(
                field_id=_slug_id(v_name, f"v{idx}"),
                code=f"V{idx}",
                section_title=sec,
                label=v_name,
                field_type="number",
                unit=_infer_unit(v_name, mod),
            )
        else:
            spec = _infer_field_from_spec(
                code=f"V{idx}",
                label=v_name,
                answer_spec=mod or "[__________________]",
                section_title=sec,
            )
        _add_field(spec)
        existing_labels.add(v_name.lower())

    # Ultimate fallback if project has neither Annexe 1 questions nor variables yet
    if not fields:
        defaults = [
            CRFFieldSpec(
                field_id="a1_age",
                code="A1",
                section_title="SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES",
                label="Âge du participant",
                field_type="number",
                unit="ans",
                required=True,
            ),
            CRFFieldSpec(
                field_id="a2_sexe",
                code="A2",
                section_title="SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES",
                label="Sexe",
                field_type="single_choice",
                options=["Masculin", "Féminin"],
                required=True,
            ),
            CRFFieldSpec(
                field_id="a3_consentement",
                code="A3",
                section_title="SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES",
                label="Consentement éclairé obtenu",
                field_type="dichotomous",
                options=["Oui", "Non"],
                required=True,
            ),
            CRFFieldSpec(
                field_id="b1_antecedent",
                code="B1",
                section_title="SECTION B : DONNÉES CLINIQUES ET PARACLINIQUES",
                label="Antécédents médico-chirurgicaux particuliers",
                field_type="dichotomous",
                options=["Oui", "Non"],
                sub_field_label="Si oui, préciser",
            ),
            CRFFieldSpec(
                field_id="b2_observation",
                code="B2",
                section_title="SECTION B : DONNÉES CLINIQUES ET PARACLINIQUES",
                label="Observations cliniques principales",
                field_type="text",
            ),
        ]
        return defaults

    return fields


def ensure_project_crf_fields(project: ProtocolProject) -> bool:
    """
    Ensure `project.crf_fields` (and `project.variables` if empty) are populated.
    Returns True if the project was modified.
    """
    changed = False
    if not project.crf_fields:
        extracted = extract_crf_fields_from_project(project)
        if extracted:
            project.crf_fields = extracted
            changed = True

    if not project.variables and project.crf_fields:
        project.variables = crf_fields_to_variables(project.crf_fields)
        changed = True
    return changed


def crf_fields_to_variables(crf_fields: List[CRFFieldSpec]) -> List[VariableItem]:
    """Convert CRF fields back to VariableItem entries for protocol consistency."""
    items: List[VariableItem] = []
    for f in crf_fields:
        if f.field_type == "dichotomous":
            scale = "Qualitative dichotomique"
            modalities = " / ".join(f.options or ["Oui", "Non"])
        elif f.field_type in ("single_choice", "multi_choice"):
            scale = "Qualitative nominale"
            modalities = " / ".join(f.options)
        elif f.field_type == "number":
            scale = "Quantitative"
            modalities = f"Valeur numérique ({f.unit})" if f.unit else "Valeur numérique"
        elif f.field_type == "date":
            scale = "Date"
            modalities = "JJ/MM/AAAA"
        else:
            scale = "Qualitative libre"
            modalities = "Texte libre"

        items.append(
            VariableItem(
                name=f"{f.code + '. ' if f.code else ''}{f.label}",
                category=f.section_title,
                role="Variable collectée (Annexe 1)",
                measurement_scale=scale,
                definition=f.sub_field_label or "",
                modalities=modalities,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Visual Markdown & HTML Drawing Generators for Annexe 1
# ---------------------------------------------------------------------------

def draw_field_control_markdown(field: CRFFieldSpec) -> str:
    """Render graphical-style box and checkbox indicators for Markdown preview."""
    if field.field_type == "dichotomous":
        opts = field.options or ["Oui", "Non"]
        ctl = "    ".join(f"☐ {o}" for o in opts)
    elif field.field_type in ("single_choice", "multi_choice"):
        opts = field.options or ["Option 1", "Option 2"]
        ctl = "    ".join(f"☐ {o}" for o in opts)
    elif field.field_type == "date":
        ctl = "⟦  |  ⟧ / ⟦  |  ⟧ / ⟦ 2 | 0 |  |  ⟧"
    elif field.field_type == "number":
        u = f" {field.unit}" if field.unit else ""
        ctl = f"⟦______________⟧{u}"
    else:
        ctl = "⟦____________________________________⟧"

    if field.sub_field_label:
        ctl += f"   *(▸ {field.sub_field_label} : ⟦____________________⟧)*"
    return ctl


def render_crf_annexe1_markdown(project: ProtocolProject, header_title: str = "") -> str:
    """Render Annexe Markdown using visual checkbox & fill-in box drawings (standard blank collection site)."""
    ensure_project_crf_fields(project)
    lang = normalize_lang(getattr(project, "language", "fr"))
    is_en = lang == "en"
    author = project.cover.author_name or ("_________________________" if is_en else "_________________________")
    matricule = f" (Matricule : {project.cover.matricule})" if project.cover.matricule else ""

    main_heading = header_title.strip() if header_title.strip() else (
        "## APPENDIX 1: DATA COLLECTION FORM (CRF)" if is_en else "## ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES"
    )
    if not main_heading.startswith("#"):
        main_heading = f"## {main_heading}"

    lines = (
        [
            main_heading,
            "**Participant ID:** ⟦  |  |  |  -  |  |  ⟧ | **Collection Date:** ⟦  |  ⟧ / ⟦  |  ⟧ / ⟦ 2 | 0 |  |  ⟧",
            "**Collection Site / Institution:** ⟦____________________________________⟧ | **Department / Unit:** ⟦______________________⟧",
            f"**Investigator:** {author}{matricule}",
            "",
        ]
        if is_en
        else [
            main_heading,
            "**Code participant :** ⟦  |  |  |  -  |  |  ⟧ | **Date de collecte :** ⟦  |  ⟧ / ⟦  |  ⟧ / ⟦ 2 | 0 |  |  ⟧",
            "**Site de collecte / Établissement :** ⟦____________________________________⟧ | **Service / Unité :** ⟦______________________⟧",
            f"**Nom de l'enquêteur :** {author}{matricule}",
            "",
        ]
    )

    grouped: Dict[str, List[CRFFieldSpec]] = {}
    for f in project.crf_fields:
        grouped.setdefault(f.section_title or ("SECTION A: COLLECTED VARIABLES" if is_en else "SECTION A : DONNÉES COLLECTÉES"), []).append(f)

    for sec_title, items in grouped.items():
        lines.append(f"### {sec_title}")
        for f in items:
            code_prefix = f"{f.code}. " if f.code else ""
            lines.append(f"- **{code_prefix}{f.label} :** {draw_field_control_markdown(f)}")
        lines.append("")

    if is_en:
        lines.append("**Investigator Verification & Signature:** Date: ____/____/20____ | Signature: _________________________")
    else:
        lines.append("**Validation & Signature de l'enquêteur :** Date : ____/____/20____ | Signature : _________________________")

    return "\n".join(lines).strip()


def _html_esc(txt: str) -> str:
    return (
        (txt or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _draw_html_checkbox(label: str) -> str:
    return (
        '<span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;margin-bottom:4px;">'
        '<span style="display:inline-block;width:15px;height:15px;border:1.8px solid #6C2826;'
        'border-radius:2px;background:#FFFFFF;flex-shrink:0;"></span>'
        f'<span style="font-size:12.5px;color:#1E293B;">{_html_esc(label)}</span>'
        "</span>"
    )


def _draw_html_fillbox(width_px: int = 175, unit: str = "", hint: str = "") -> str:
    unit_html = (
        f'<span style="font-size:12px;font-weight:600;color:#475569;margin-left:8px;">{_html_esc(unit)}</span>'
        if unit
        else ""
    )
    hint_html = f'<span style="color:#94A3B8;font-size:10.5px;">{_html_esc(hint)}</span>' if hint else ""
    return (
        '<span style="display:inline-flex;align-items:center;gap:4px;">'
        f'<span style="display:inline-flex;align-items:center;justify-content:center;min-width:{width_px}px;'
        'height:24px;border:1.5px solid #475569;border-radius:3px;background:#F8FAFC;padding:0 8px;">'
        f"{hint_html}"
        "</span>"
        f"{unit_html}"
        "</span>"
    )


def _draw_html_date_boxes() -> str:
    box = (
        '<span style="display:inline-block;width:20px;height:24px;border:1.5px solid #475569;'
        'background:#F8FAFC;text-align:center;line-height:22px;font-family:monospace;font-size:11px;'
        'color:#64748B;">{}</span>'
    )
    sep = '<span style="margin:0 4px;font-weight:700;color:#475569;">/</span>'
    return (
        '<span style="display:inline-flex;align-items:center;gap:2px;">'
        + box.format("J")
        + box.format("J")
        + sep
        + box.format("M")
        + box.format("M")
        + sep
        + box.format("2")
        + box.format("0")
        + box.format("A")
        + box.format("A")
        + "</span>"
    )


def render_crf_annexe1_html(project: ProtocolProject) -> str:
    """
    Generate an authentic A4 paper-like graphical HTML preview of Annexe 1
    with drawn square checkboxes and drawn fill-in rectangular boxes.
    """
    ensure_project_crf_fields(project)
    sites = extract_hospital_sites_from_project(project)
    title = project.cover.title or project.name
    author = project.cover.author_name or "Investigateur principal"
    matricule = f" (Matricule : {project.cover.matricule})" if project.cover.matricule else ""

    site_checks = "".join(_draw_html_checkbox(s) for s in sites) + _draw_html_checkbox("Autre :") + _draw_html_fillbox(110)

    sections_html: List[str] = []
    grouped: Dict[str, List[CRFFieldSpec]] = {}
    for f in project.crf_fields:
        grouped.setdefault(f.section_title or "SECTION A : DONNÉES COLLECTÉES", []).append(f)

    for sec_title, items in grouped.items():
        rows_html: List[str] = []
        for idx, f in enumerate(items):
            bg = "#FFFFFF" if idx % 2 == 0 else "#F8FAFC"
            if f.field_type == "dichotomous":
                opts = f.options or ["Oui", "Non"]
                ctl_html = "".join(_draw_html_checkbox(o) for o in opts)
            elif f.field_type in ("single_choice", "multi_choice"):
                opts = f.options or ["Option 1", "Option 2"]
                ctl_html = (
                    '<div style="display:flex;flex-wrap:wrap;gap:6px 12px;">'
                    + "".join(_draw_html_checkbox(o) for o in opts)
                    + "</div>"
                )
            elif f.field_type == "date":
                ctl_html = _draw_html_date_boxes()
            elif f.field_type == "number":
                ctl_html = _draw_html_fillbox(width_px=130, unit=f.unit)
            else:
                ctl_html = _draw_html_fillbox(width_px=260, unit=f.unit)

            if f.sub_field_label:
                ctl_html += (
                    '<div style="margin-top:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">'
                    f'<span style="font-size:11.5px;color:#475569;font-style:italic;">▸ {_html_esc(f.sub_field_label)} :</span>'
                    + _draw_html_fillbox(width_px=165)
                    + "</div>"
                )

            code_badge = (
                f'<span style="font-weight:700;color:#6C2826;margin-right:5px;">{_html_esc(f.code)}.</span>'
                if f.code
                else ""
            )
            rows_html.append(
                f'<tr style="background:{bg};">'
                '<td style="width:44%;padding:8px 10px;border:1px solid #CBD5E1;vertical-align:top;font-size:12.5px;color:#0F172A;">'
                f"{code_badge}{_html_esc(f.label)}"
                "</td>"
                '<td style="width:56%;padding:8px 10px;border:1px solid #CBD5E1;vertical-align:middle;">'
                f"{ctl_html}"
                "</td>"
                "</tr>"
            )

        sections_html.append(
            '<div style="margin-top:14px;">'
            '<div style="background:#6C2826;color:#FFFFFF;font-weight:700;font-size:12.5px;'
            'padding:6px 10px;letter-spacing:0.3px;">'
            f"{_html_esc(sec_title)}"
            "</div>"
            '<table style="width:100%;border-collapse:collapse;">'
            + "".join(rows_html)
            + "</table></div>"
        )

    code_boxes = (
        '<span style="display:inline-flex;align-items:center;gap:2px;">'
        + "".join(
            '<span style="display:inline-block;width:18px;height:22px;border:1.5px solid #475569;background:#fff;"></span>'
            for _ in range(3)
        )
        + '<span style="margin:0 4px;font-weight:700;">-</span>'
        + "".join(
            '<span style="display:inline-block;width:18px;height:22px;border:1.5px solid #475569;background:#fff;"></span>'
            for _ in range(3)
        )
        + "</span>"
    )

    return (
        '<div style="font-family:Times New Roman, Georgia, serif;color:#0F172A;background:#FFFFFF;padding:18px;border:1px solid #CBD5E1;border-radius:6px;">'
        '<div style="border:2px solid #6C2826;padding:12px 14px;background:#FDF8F8;">'
        '<div style="text-align:center;font-size:15px;font-weight:700;color:#6C2826;letter-spacing:0.4px;">'
        "ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES (ANONYME)"
        "</div>"
        f'<div style="text-align:center;font-size:11.5px;color:#334155;margin-top:4px;">{_html_esc(title)}</div>'
        '<div style="text-align:center;font-size:11px;color:#64748B;margin-top:4px;font-style:italic;">'
        "Consigne : Cocher la case correspondante ☐ ou inscrire lisiblement la valeur dans le cadre prévu à cet effet."
        "</div>"
        '<div style="margin-top:10px;padding-top:8px;border-top:1px dashed #CBD5E1;display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px;">'
        f'<div><strong>Code participant :</strong> {code_boxes}</div>'
        f'<div><strong>Date de collecte :</strong> {_draw_html_date_boxes()}</div>'
        '<div><strong>Site de collecte / Établissement :</strong> <span style="display:inline-block;min-width:185px;border-bottom:1.2px solid #475569;">&nbsp;</span></div>'
        '<div><strong>Service / Unité :</strong> <span style="display:inline-block;min-width:135px;border-bottom:1.2px solid #475569;">&nbsp;</span></div>'
        f'<div style="grid-column:span 2;"><strong>Enquêteur :</strong> {_html_esc(author)}{_html_esc(matricule)}</div>'
        "</div>"
        "</div>"
        + "".join(sections_html)
        + '<div style="margin-top:14px;padding:10px 12px;border:1px solid #CBD5E1;background:#F8FAFC;font-size:11.5px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;">'
        '<span><strong>Validation de l\'enquêteur</strong> — Date : ____ / ____ / 20____</span>'
        '<span><strong>Signature :</strong> ____________________________</span>'
        "</div>"
        + "</div>"
    )


def sync_crf_to_project(
    project: ProtocolProject,
    sync_annexe1_markdown: bool = True,
    header_title: str = "",
) -> None:
    """
    Synchronize `project.crf_fields` into `project.variables` and update the dedicated
    `annexe_fiche_collecte` sub-section (preserving custom heading and updating any legacy
    embedded CRF block inside `annexes` only if one already existed there).
    """
    from app.services.rich_formatter import markdown_to_clean_html

    project.variables = crf_fields_to_variables(project.crf_fields)

    if not sync_annexe1_markdown:
        return

    lang = normalize_lang(getattr(project, "language", "fr"))
    default_title = (
        "## APPENDIX 1: DATA COLLECTION FORM (CRF)"
        if lang == "en"
        else "## ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES"
    )

    crf_sec = project.sections.get("annexe_fiche_collecte")
    if crf_sec is None:
        crf_sec = SectionState(section_id="annexe_fiche_collecte")
        project.sections["annexe_fiche_collecte"] = crf_sec

    resolved_title = header_title.strip()
    if not resolved_title and crf_sec.content_markdown.strip():
        first_line = crf_sec.content_markdown.strip().splitlines()[0].strip()
        if first_line.startswith("#"):
            resolved_title = first_line

    ann_sec = project.sections.get("annexes")
    has_embedded_crf_in_annexes = False
    if ann_sec and ann_sec.content_markdown.strip():
        before, old_crf, after, orig_ann_title = extract_crf_annexe_block(ann_sec.content_markdown)
        if old_crf and is_crf_annexe_content(orig_ann_title, old_crf):
            has_embedded_crf_in_annexes = True
            if not resolved_title:
                resolved_title = orig_ann_title
            rendered_for_ann = render_crf_annexe1_markdown(project, header_title=resolved_title or default_title)
            parts: List[str] = []
            if before:
                parts.append(before)
            elif not ann_sec.content_markdown.strip().startswith("# "):
                parts.append("# ANNEXES DU PROTOCOLE DE RECHERCHE")
            parts.append(rendered_for_ann)
            if after:
                parts.append("---\n\n" + after if not after.startswith("---") else after)
            ann_sec.content_markdown = "\n\n".join(p for p in parts if p).strip()
            ann_sec.content_html = markdown_to_clean_html(ann_sec.content_markdown)

    rendered_crf = render_crf_annexe1_markdown(project, header_title=resolved_title or default_title)
    crf_sec.content_markdown = rendered_crf
    crf_sec.content_html = markdown_to_clean_html(rendered_crf)
    if crf_sec.status == StepStatus.PENDING and project.crf_fields:
        crf_sec.status = StepStatus.IN_PROGRESS
    _ = has_embedded_crf_in_annexes


# ---------------------------------------------------------------------------
# Phase 2: AI CAMES-Compliant CRF Generator & Improver
# ---------------------------------------------------------------------------

class _AICRFFieldOut(BaseModel):
    code: str = Field(description="Code hiérarchique unique, ex: A1, A2, B1, C1, D1, E1")
    section_title: str = Field(description="Titre de section clinique (SECTION A .. SECTION E)")
    label: str = Field(description="Libellé clinique clair, sans donnée nominative")
    field_type: Literal["dichotomous", "single_choice", "multi_choice", "number", "date", "text"]
    options: List[str] = Field(
        default_factory=list,
        description="Modalités fermées mutuellement exclusives pour dichotomous / single_choice / multi_choice",
    )
    unit: str = Field(default="", description="Unité explicite pour champ number (ex: ans, kg, mmHg, g/dL, SA)")
    sub_field_label: str = Field(default="", description="Sous-question conditionnelle, ex: Si oui, préciser")
    required: bool = Field(default=False)


class _AICRFGenerationOut(BaseModel):
    form_title: str = Field(description="Titre de la fiche de collecte (ex: ## ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES)")
    assistant_commentary: str = Field(
        description="Commentaire méthodologique synthétique expliquant les choix (variables, scores issus de la littérature, conformité CAMES)"
    )
    cames_compliance_checklist: List[str] = Field(
        default_factory=list,
        description="Liste des critères CAMES / BPC vérifiés (anonymat, concordance objectifs, unités, etc.)",
    )
    fields: List[_AICRFFieldOut] = Field(default_factory=list)


def _collect_crf_ai_context(project: ProtocolProject, lang: str) -> str:
    """Gather objectives, literature, scores, variables, and methodology for CRF AI synthesis."""
    parts: List[str] = [
        f"Titre / Sujet : {project.cover.title or project.name}",
        f"Type d'étude : {project.study_type or 'Non spécifié'}",
        f"Idée / Cadre : {project.research_idea or 'Non spécifié'}",
        f"Sites hospitaliers : {', '.join(project.cover.host_institutions or []) or 'Non spécifiés'}",
    ]

    sec_map = [
        ("Objectifs (I.4)", "chap1_objectifs"),
        ("Hypothèses (I.3)", "chap1_hypotheses"),
        ("Variables opérationnelles (I.5)", "chap1_variables"),
        ("Rappel & scores (II.1)", "chap2_rappel_connaissances"),
        ("Revue / État des connaissances (II.2)", "chap2_etat_connaissances"),
        ("Type, lieu & durée (III.1)", "chap3_type_site_duree"),
        ("Population & sélection (III.2)", "chap3_population_echantillon"),
        ("Outils & procédures (III.3)", "chap3_outils_procedure"),
        ("Plan d'analyse statistique (III.4)", "chap3_analyse_donnees"),
    ]
    for lbl, sid in sec_map:
        sec = project.sections.get(sid)
        if sec and sec.content_markdown and sec.content_markdown.strip():
            parts.append(f"\n### {lbl}\n{sec.content_markdown.strip()[:1800]}")

    if project.variables:
        var_lines = [
            f"- {v.name} [{v.category} | {v.role} | {v.measurement_scale} | Modalités: {v.modalities}]"
            for v in project.variables[:40]
        ]
        parts.append("\n### Tableau structuré des variables du protocole\n" + "\n".join(var_lines))

    if project.references:
        ref_lines = []
        for idx, r in enumerate(project.references[:8], start=1):
            abs_snip = (r.abstract or "").strip().replace("\n", " ")[:240]
            ref_lines.append(f"[{idx}] {r.title} ({r.year or 's.d.'}) — {abs_snip}")
        parts.append("\n### Revue de la littérature & scores cliniques identifiés\n" + "\n".join(ref_lines))

    return "\n".join(parts)


def generate_or_improve_crf_with_ai(
    project: ProtocolProject,
    user_instruction: str = "",
    mode: str = "auto",
) -> Dict[str, Any]:
    """
    Use Gemini (drafting role -> gemini-3.5-flash-lite) to generate a new CAMES/BPC-compliant
    data collection form (CRF) OR improve an existing one using user needs, literature review,
    protocol objectives/variables, and CAMES/GCP standards. Rebuilds project.crf_fields & Mask.
    """
    from app.services.gemini_client import gemini_router
    from app.services.rich_formatter import markdown_to_clean_html

    lang = normalize_lang(getattr(project, "language", "fr"))
    existing_md = get_crf_markdown_from_project(project)
    has_existing = bool(existing_md.strip()) or len(project.crf_fields or []) >= 3
    if mode in ("generate", "improve"):
        effective_mode = mode
    else:
        effective_mode = "improve" if has_existing else "generate"

    study_ctx = _collect_crf_ai_context(project, lang=lang)
    existing_fields_summary = "\n".join(
        f"- {f.code}. {f.label} ({f.field_type}) [{f.section_title}]"
        + (f" Options: {' / '.join(f.options)}" if f.options else "")
        + (f" Unité: {f.unit}" if f.unit else "")
        + (f" Sous-question: {f.sub_field_label}" if f.sub_field_label else "")
        for f in (project.crf_fields or [])[:50]
    )

    if lang == "en":
        system_inst = (
            "You are a senior CAMES/FMSB medical biostatistician and clinical research methodologist. "
            "Design or refine a Case Report Form (CRF) strictly compliant with CAMES, ICMJE, and ICH-GCP standards."
        )
        lang_rules = (
            "Write 100% of labels, section titles, options, commentary, and checklist items in academic medical English. "
            "For dichotomous fields, use options [\"Yes\", \"No\"]."
        )
        mode_block = (
            f"TASK MODE: IMPROVE EXISTING CRF (preserve valid clinical items while fixing gaps, missing units, vague open fields, or missing literature scores).\nEXISTING CRF MARKDOWN:\n{existing_md[:3000]}\nEXISTING FIELDS:\n{existing_fields_summary}"
            if effective_mode == "improve"
            else "TASK MODE: GENERATE A COMPLETE NEW CRF FROM SCRATCH aligned with all protocol sections and variables."
        )
    else:
        system_inst = (
            "Tu es un biostatisticien et méthodologiste senior en médecine (espace CAMES / FMSB). "
            "Tu conçois ou améliores une Fiche de Collecte des Données (Cahier d'Observation / CRF) "
            "parfaitement conforme aux exigences CAMES, ICMJE et Bonnes Pratiques Cliniques (BPC / ICH-GCP)."
        )
        lang_rules = (
            "Rédige 100 % des libellés, sections, modalités, commentaire et checklist en français médical académique. "
            "Pour les champs dichotomiques (`dichotomous`), utilise obligatoirement les options [\"Oui\", \"Non\"]."
        )
        mode_block = (
            f"MODE : AMÉLIORATION DE LA FICHE EXISTANTE (conserve les items cliniques pertinents, corrige les manques d'unités, ferme les questions vagues en cases à cocher, et ajoute les scores/variables issus des objectifs et de la littérature).\nFICHE EXISTANTE :\n{existing_md[:3000]}\nCHAMPS ACTUELS :\n{existing_fields_summary}"
            if effective_mode == "improve"
            else "MODE : CRÉATION COMPLÈTE D'UNE NOUVELLE FICHE DE COLLECTE alignée 1-pour-1 sur les objectifs, variables, méthodologie et revue de la littérature."
        )

    prompt = f"""
{mode_block}

BESOINS ET INSTRUCTIONS EXPLICITES DE L'UTILISATEUR :
{user_instruction.strip() or ("Aucun besoin spécifique supplémentaire : couverture complète et rigoureuse selon le protocole et la littérature." if lang == "fr" else "No extra user constraint: produce complete coverage aligned with protocol and literature.")}

RÈGLES MÉTHODOLOGIQUES OBLIGATOIRES (CAMES / BPC / ICH-GCP) :
1. ANONYMAT STRICT : Aucune donnée nominative (ni nom, ni prénom, ni téléphone, ni CNI). Le code participant, la date d'inclusion, le site hospitalier et l'enquêteur sont déjà gérés par l'en-tête fixe du CRF — ne crée pas de doublon de champ « Nom du patient ».
2. CONCORDANCE 1-POUR-1 : Chaque variable du protocole (notamment critère de jugement principal et covariables), chaque objectif spécifique et les scores cliniques validés trouvés dans la revue de la littérature (ex. NYHA, Glasgow, Apgar, CIM-10, classification OMS, biomarqueurs) doivent être collectés sans variables superflues.
3. STANDARDISATION STATISTIQUE : Privilégie fortement les questions fermées (`dichotomous`, `single_choice`, `multi_choice`) avec des modalités mutuellement exclusives et une option `Autre` + `sub_field_label="Si Autre, préciser"` si pertinent. Réserve `text` aux seules précisions indispensables.
4. UNITÉS ET DATES EXPLICITES : Indique toujours `unit` pour tout champ `number` (ex. `ans`, `mois`, `kg`, `cm`, `kg/m²`, `mmHg`, `g/dL`, `SA`, `FCFA`, `heures`). Utilise `date` pour les dates.
5. PROGRESSION CLINIQUE LOGIQUE A–E (12 à 26 questions bien structurées) :
   - `SECTION A : CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES` (A1..An)
   - `SECTION B : ANTÉCÉDENTS ET FACTEURS D'EXPOSITION / RISQUE` (B1..Bn)
   - `SECTION C : DONNÉES CLINIQUES À L'ADMISSION` (C1..Cn)
   - `SECTION D : DONNÉES PARACLINIQUES ET SCORES VALIDÉS` (D1..Dn)
   - `SECTION E : PRISE EN CHARGE, ÉVOLUTION ET CRITÈRE DE JUGEMENT PRINCIPAL` (E1..En)
6. CODIFICATION & LOGIQUE CONDITIONNELLE : Codes A1, A2, B1... et `sub_field_label` clair quand une réponse appelle une précision (ex. « Si oui, préciser »).
7. {lang_rules}

CONTEXTE COMPLET DU PROTOCOLE ET DE LA LITTÉRATURE :
{study_ctx}
"""

    res = gemini_router.generate_structured(
        role="drafting",
        contents=prompt,
        schema=_AICRFGenerationOut,
        system_instruction=system_inst,
        temperature=0.2,
    )
    dto: _AICRFGenerationOut = res["data"]

    valid_types = {"dichotomous", "single_choice", "multi_choice", "number", "date", "text"}
    built_fields: List[CRFFieldSpec] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(dto.fields, start=1):
        ftype = item.field_type if item.field_type in valid_types else "text"
        opts = [o.strip() for o in (item.options or []) if o and o.strip()]
        if ftype == "dichotomous" and not opts:
            opts = ["Yes", "No"] if lang == "en" else ["Oui", "Non"]
        elif ftype in ("single_choice", "multi_choice") and len(opts) < 2:
            if len(opts) == 1:
                opts.append("Other" if lang == "en" else "Autre")
            else:
                opts = ["Option 1", "Option 2"]

        code = (item.code or f"Q{idx}").strip()
        label = _clean_md_text(item.label)
        if not label:
            continue
        # Reject inadvertent PII fields
        lower_lbl = label.lower()
        if any(pii in lower_lbl for pii in ("nom complet", "prénom", "téléphone", "phone number", "full name", "adresse exacte")):
            continue

        base_fid = _slug_id(f"{code}_{label}", f"field_{idx}")
        fid = base_fid
        suffix_n = 2
        while fid in seen_ids:
            fid = f"{base_fid}_{suffix_n}"
            suffix_n += 1
        seen_ids.add(fid)

        unit = (item.unit or "").strip() if ftype == "number" else ""
        placeholder = (
            ("DD / MM / YYYY" if lang == "en" else "JJ / MM / AAAA")
            if ftype == "date"
            else (f"Ex. ({unit})" if unit else "")
        )
        built_fields.append(
            CRFFieldSpec(
                field_id=fid,
                code=code,
                section_title=_clean_md_text(item.section_title) or "SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES",
                label=label,
                field_type=ftype,
                options=opts if ftype in ("dichotomous", "single_choice", "multi_choice") else [],
                unit=unit,
                placeholder=placeholder,
                sub_field_label=_clean_md_text(item.sub_field_label),
                required=bool(item.required),
            )
        )

    if built_fields:
        project.crf_fields = built_fields
    else:
        ensure_project_crf_fields(project)

    sync_crf_to_project(project, sync_annexe1_markdown=True, header_title=dto.form_title)
    crf_sec = project.sections.get("annexe_fiche_collecte")
    if crf_sec is not None:
        crf_sec.status = StepStatus.IN_PROGRESS
        if crf_sec.content_markdown:
            crf_sec.content_html = markdown_to_clean_html(crf_sec.content_markdown)

    return {
        "model_used": res["model_used"],
        "mode_applied": effective_mode,
        "form_title": dto.form_title,
        "content_markdown": crf_sec.content_markdown if crf_sec else "",
        "content_html": crf_sec.content_html if crf_sec else "",
        "crf_fields": project.crf_fields,
        "annexe1_html": render_crf_annexe1_html(project),
        "assistant_commentary": dto.assistant_commentary,
        "cames_compliance_checklist": dto.cames_compliance_checklist,
    }


# ---------------------------------------------------------------------------
# CSV / Excel Patient Dataset Exporter
# ---------------------------------------------------------------------------

def export_patient_records_csv(project: ProtocolProject) -> str:
    """Export collected patient records as a semicolon-delimited UTF-8-BOM CSV."""
    ensure_project_crf_fields(project)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    headers = [
        "ID_Enregistrement",
        "Code_Patient",
        "Date_Inclusion",
        "Site_Hospitalier",
        "Enqueteur",
    ]
    field_keys: List[Tuple[str, bool]] = []
    for f in project.crf_fields:
        col_name = f"{f.code} - {f.label}" if f.code else f.label
        if f.unit:
            col_name += f" ({f.unit})"
        headers.append(col_name)
        has_sub = bool(f.sub_field_label)
        field_keys.append((f.field_id, has_sub))
        if has_sub:
            headers.append(f"{col_name} [{f.sub_field_label}]")
    headers.append("Observations")

    writer.writerow(headers)

    for rec in project.patient_records:
        row: List[str] = [
            rec.record_id,
            rec.patient_code,
            rec.inclusion_date,
            rec.hospital_site,
            rec.investigator_name,
        ]
        vals = rec.values or {}
        for fid, has_sub in field_keys:
            v = vals.get(fid, "")
            if isinstance(v, list):
                row.append(" | ".join(str(x) for x in v))
            else:
                row.append("" if v is None else str(v))
            if has_sub:
                sv = vals.get(f"{fid}__sub", "")
                row.append("" if sv is None else str(sv))
        row.append(rec.notes or "")
        writer.writerow(row)

    return "\ufeff" + buf.getvalue()
