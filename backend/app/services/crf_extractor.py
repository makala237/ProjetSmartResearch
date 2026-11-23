from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.data.i18n import normalize_lang
from app.schemas.protocol import CRFFieldSpec, ProtocolProject, SectionState, StepStatus
from app.services.crf_builder import (
    _AICRFFieldOut,
    _SECTION_HEAD_RE,
    _clean_md_text,
    _infer_field_from_spec,
    _slug_id,
    extract_crf_fields_from_project,
    render_crf_annexe1_html,
    sync_crf_to_project,
)
from app.services.gemini_client import gemini_router
from app.services.rich_formatter import markdown_to_clean_html


# ---------------------------------------------------------------------------
# Schemas for Boundary Detection & Hybrid Compilation
# ---------------------------------------------------------------------------


class CRFBoundaryDetection(BaseModel):
    found: bool = False
    start_block_id: Optional[int] = None
    end_block_id: Optional[int] = None
    start_excerpt: str = ""
    end_excerpt: str = ""
    confidence: float = 0.0
    detected_title: str = ""
    method: str = "none"  # "ai+heuristic" | "heuristic" | "none"
    excluded_neighbor_annexes: List[str] = Field(default_factory=list)
    notes: str = ""


class _AIBoundaryDecision(BaseModel):
    found: bool = Field(
        default=False,
        description="True uniquement si un véritable questionnaire / fiche de collecte / grille d'observation existe.",
    )
    start_block_id: Optional[int] = Field(
        default=None,
        description="ID entier [B#] du premier bloc appartenant à la fiche de collecte.",
    )
    end_block_id: Optional[int] = Field(
        default=None,
        description="ID entier [B#] du dernier bloc appartenant à la fiche de collecte (avant Notice d'information, Consentement, Clairance, etc.).",
    )
    detected_title: str = Field(
        default="",
        description="Titre exact repéré pour la fiche de collecte (ex: ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES).",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    excluded_neighbor_annexes: List[str] = Field(
        default_factory=list,
        description="Titres des autres annexes voisines exclues (Notice d'information, Consentement éclairé, Clairance, etc.).",
    )
    reason: str = Field(default="", description="Brève justification des bornes choisies.")


class _AICRFCompilationOut(BaseModel):
    detected_title: str = Field(
        default="",
        description="Titre principal de la fiche de collecte (ex: ## ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES).",
    )
    fields: List[_AICRFFieldOut] = Field(default_factory=list)
    cames_warnings: List[str] = Field(
        default_factory=list,
        description="Alertes CAMES éventuelles (champs nominatifs supprimés, unités manquantes, questions ambiguës).",
    )


# ---------------------------------------------------------------------------
# Regex & Heuristic Patterns (works on raw Word / PDF text WITHOUT Markdown #)
# ---------------------------------------------------------------------------

_OPEN_CRF_HEADER_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*)?(?:(?:ANNEXE|APPENDIX)\s*(?:[0-9]+|[IVX]+)\s*[:\-–.]\s*)?"
    r"(?:FICHE\s+(?:DE\s+COLLECTE|D['’]ENQU[ÊE]TE|INDIVIDUELLE|D['’]OBSERVATION)|"
    r"FORMULAIRE\s+(?:DE\s+COLLECTE|D['’]ENREGISTREMENT|D['’]ENQU[ÊE]TE)|"
    r"CAHIER\s+D['’]OBSERVATION|"
    r"GRILLE\s+(?:DE\s+COLLECTE|D['’]OBSERVATION)|"
    r"QUESTIONNAIRE(?:\s+D['’]ENQU[ÊE]TE|\s+ANONYME)?|"
    r"OUTIL\s+DE\s+COLLECTE\s+DES\s+DONN[ÉE]ES|"
    r"CASE\s+REPORT\s+FORM|"
    r"DATA\s+COLLECTION\s+(?:FORM|SHEET)|"
    r"\bCRF\b)",
    re.IGNORECASE,
)

_CLOSING_ANNEX_HEADER_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*)?(?:(?:ANNEXE|APPENDIX)\s*(?:[2-9]\d*|II|III|IV|V|VI|VII|VIII|IX|X)\s*[:\-–.]?\s*)?"
    r"(?:NOTICE\s+D['’]INFORMATION|"
    r"FICHE\s+D['’]INFORMATION(?:\s+DU\s+PARTICIPANT)?|"
    r"INFORMATION\s+SHEET|"
    r"FORMULAIRE\s+DE\s+CONSENTEMENT|"
    r"CONSENTEMENT\s+[ÉE]CLAIR[ÉE]|"
    r"CONSENT\s+FORM|"
    r"CLAIRANCE\s+[ÉE]THIQUE|"
    r"ETHICAL\s+CLEARANCE|"
    r"DEMANDE\s+D['’]AUTORISATION|"
    r"AUTORISATION\s+(?:ADMINISTRATIVE|DE\s+RECHERCHE)|"
    r"LETTRE\s+D['’]ENGAGEMENT|"
    r"D[ÉE]CLARATION\s+DE\s+CONFIDENTIALIT[ÉE]|"
    r"R[ÉE]F[ÉE]RENCES(?:\s+BIBLIOGRAPHIQUES)?|"
    r"BIBLIOGRAPHIE|"
    r"TABLE\s+DES\s+MATI[ÈE]RES|"
    r"CHRONOGRAMME|"
    r"BUDGET(?:\s+PR[ÉE]VISIONNEL)?)",
    re.IGNORECASE,
)

_GENERIC_NEXT_ANNEX_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:ANNEXE|APPENDIX)\s+([2-9]\d*|II|III|IV|V|VI|VII|VIII|IX|X)\b",
    re.IGNORECASE,
)

_NARRATIVE_SECTION_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:CHAPITRE\s+[IVX0-9]+|INTRODUCTION|PROBL[ÉE]MATIQUE|JUSTIFICATION|OBJECTIFS|"
    r"REVUE\s+DE\s+LA\s+LITT[ÉE]RATURE|M[ÉE]THODOLOGIE|CONSID[ÉE]RATIONS\s+[ÉE]THIQUES)\b",
    re.IGNORECASE,
)

_FORM_SUBSECTION_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*)?(?:SECTION\s+[A-Z0-9]+|PARTIE\s+[A-Z0-9]+|VOLET\s+[A-Z0-9]+|"
    r"MODULE\s+[A-Z0-9]+|IDENTIFICATION(?:\s+DU\s+PARTICIPANT)?|"
    r"CARACT[ÉE]RISTIQUES\s+SOCIOD[ÉE]MOGRAPHIQUES|"
    r"DONN[ÉE]ES\s+(?:CLINIQUES|PARACLINIQUES|BIOLOGIQUES|D['’]HOSPITALISATION|TH[ÉE]RAPEUTIQUES)|"
    r"ANT[ÉE]C[ÉE]DENTS|EXAMEN\s+CLINIQUE|[ÉE]VOLUTION|CRIT[ÈE]RE\s+DE\s+JUGEMENT)",
    re.IGNORECASE,
)

_CODED_QUESTION_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s+)?(?:\*\*)?(?:[A-Za-z]\d{1,2}(?:\.\d+)?|Q\d{1,3}|\d{1,2})[.)]?\s+[^:\n]{2,110}\s*[:：?]"
)

_CHECKBOX_MARKER_RE = re.compile(r"(?:\[\s*[xX ]?\s*\]|☐|□|▢|◯|○\s+[A-ZÀ-ÿa-z0-9])")
_BLANK_MARKER_RE = re.compile(r"(?:_{3,}|\.{4,}|…{2,}|\[_{2,}\]|/\s*(?:Non|No|F[ée]minin|Masculin)\b)", re.IGNORECASE)

_PII_KEYWORDS = (
    "nom complet",
    "nom et prénom",
    "nom et prenom",
    "noms et prénoms",
    "noms et prenoms",
    "prénom du patient",
    "prenom du patient",
    "nom du patient",
    "numéro de téléphone",
    "numero de telephone",
    "téléphone du patient",
    "telephone du patient",
    "phone number",
    "full name",
    "adresse exacte",
    "adresse domiciliaire",
    "numéro cni",
    "numero cni",
    "carte nationale d'identité",
)

_SAFE_ID_HINTS = ("code participant", "code patient", "identifiant", "anonymat", "numéro d'inclusion", "id ")


def _line_has_form_control(line: str) -> bool:
    return bool(_CHECKBOX_MARKER_RE.search(line) or _BLANK_MARKER_RE.search(line))


def _is_standalone_closing_header(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 130:
        return False
    if s.lstrip().startswith(("-", "•", "*")):
        return False
    # Do NOT misclassify a CRF question like "A3. Consentement éclairé obtenu : [ ] Oui [ ] Non"
    if _line_has_form_control(s):
        return False
    if _OPEN_CRF_HEADER_RE.match(s):
        return False
    if _CLOSING_ANNEX_HEADER_RE.match(s):
        return True
    if _GENERIC_NEXT_ANNEX_RE.match(s) and not _OPEN_CRF_HEADER_RE.search(s):
        return True
    return False


def _score_block_as_crf(heading: str, text: str) -> Dict[str, Any]:
    """Compute structural signals indicating whether a raw block belongs to a CRF / questionnaire."""
    head = (heading or "").strip()
    body = (text or "").strip()
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    words = len(re.findall(r"\w+", body))

    open_cue = bool(_OPEN_CRF_HEADER_RE.search(head) or any(_OPEN_CRF_HEADER_RE.match(ln) for ln in lines[:4]))
    form_section_cue = bool(
        _FORM_SUBSECTION_RE.search(head) or any(_FORM_SUBSECTION_RE.match(ln) for ln in lines[:5])
    )
    closing_cue = _is_standalone_closing_header(head) or (
        bool(lines) and _is_standalone_closing_header(lines[0])
    )
    narrative_cue = bool(_NARRATIVE_SECTION_RE.match(head)) and not open_cue and not form_section_cue

    checkbox_hits = len(_CHECKBOX_MARKER_RE.findall(body))
    blank_hits = len(_BLANK_MARKER_RE.findall(body))
    coded_lines = sum(1 for ln in lines if _CODED_QUESTION_LINE_RE.match(ln))
    slash_choice_lines = sum(
        1
        for ln in lines
        if ":" in ln
        and (
            bool(re.search(r"\b(?:oui|yes)\s*/\s*(?:non|no)\b", ln, re.IGNORECASE))
            or bool(re.search(r"\b(?:masculin|m|homme)\s*/\s*(?:f[ée]minin|f|femme)\b", ln, re.IGNORECASE))
            or ln.count("/") >= 2
        )
    )
    pipe_table_lines = sum(1 for ln in lines if ln.startswith("|") or " | " in ln)

    control_hits = checkbox_hits + blank_hits + coded_lines + slash_choice_lines + max(0, pipe_table_lines - 1)

    score = 0.0
    if open_cue:
        score += 6.0
    if form_section_cue:
        score += 3.5
    score += min(9.0, control_hits * 1.15)

    # Penalize long narrative prose without form controls
    if words >= 110 and control_hits <= 1 and not open_cue:
        score -= 4.5
    if narrative_cue and control_hits <= 2:
        score -= 5.0
    if closing_cue and not open_cue and control_hits <= 1:
        score -= 10.0

    # Inline split marker: what if closing header appears midway through the same block?
    internal_close_line = None
    for idx, ln in enumerate(lines):
        if idx > 0 and _is_standalone_closing_header(ln):
            internal_close_line = ln
            break

    return {
        "heading": head,
        "words": words,
        "open_cue": open_cue,
        "form_section_cue": form_section_cue,
        "closing_cue": closing_cue,
        "narrative_cue": narrative_cue,
        "control_hits": control_hits,
        "pipe_table_lines": pipe_table_lines,
        "internal_close_line": internal_close_line,
        "score": round(score, 2),
    }


def _find_heuristic_crf_span(blocks: Sequence[Any], block_infos: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find best contiguous span of blocks [start_idx..end_idx] representing the CRF."""
    n = len(blocks)
    if n == 0:
        return None

    spans: List[Tuple[int, int]] = []
    i = 0
    while i < n:
        info = block_infos[i]
        is_starter = info["score"] >= 2.5 or info["open_cue"] or (info["form_section_cue"] and info["control_hits"] >= 1)
        if not is_starter:
            i += 1
            continue

        start = i
        end = i
        j = i + 1
        while j < n:
            nxt = block_infos[j]
            if nxt["closing_cue"] and not nxt["open_cue"]:
                break
            if nxt["narrative_cue"] and nxt["control_hits"] <= 2:
                break
            # Explicit open/section cues or form controls extend the CRF span cleanly
            if nxt["score"] >= 1.5 or nxt["control_hits"] >= 2 or nxt["form_section_cue"] or nxt["open_cue"]:
                end = j
                j += 1
                continue
            # Bridge a single mild transition block (e.g. short table note) if next block resumes CRF
            if j + 1 < n and nxt["score"] >= -1.0:
                nxt2 = block_infos[j + 1]
                if not nxt2["closing_cue"] and (nxt2["score"] >= 2.2 or nxt2["control_hits"] >= 2 or nxt2["form_section_cue"]):
                    end = j + 1
                    j += 2
                    continue
            break

        spans.append((start, end))
        i = end + 1

    if not spans:
        return None

    best_span: Optional[Tuple[int, int]] = None
    best_rank = -999.0
    for s_idx, e_idx in spans:
        span_infos = block_infos[s_idx : e_idx + 1]
        total_score = sum(x["score"] for x in span_infos)
        total_controls = sum(x["control_hits"] for x in span_infos)
        has_open = any(x["open_cue"] for x in span_infos)
        # Prefer spans located in the latter part of the document (Annexes) over Chap 3 mentions
        position_bonus = (s_idx / max(1, n)) * 2.5 if n >= 4 else 0.0
        rank = total_score + position_bonus

        if total_controls < 2 and not (has_open and total_controls >= 1):
            continue
        if total_score < 3.5 and not (has_open and total_controls >= 1):
            continue
        if rank > best_rank:
            best_rank = rank
            best_span = (s_idx, e_idx)

    if best_span is None:
        return None

    s_idx, e_idx = best_span
    span_blocks = blocks[s_idx : e_idx + 1]
    span_infos = block_infos[s_idx : e_idx + 1]
    total_controls = sum(x["control_hits"] for x in span_infos)

    # Identify neighboring non-CRF annexes outside [s_idx..e_idx]
    excluded: List[str] = []
    for idx_other, inf in enumerate(block_infos):
        if s_idx <= idx_other <= e_idx:
            continue
        if inf["closing_cue"] or inf["internal_close_line"]:
            title_cand = (inf["heading"] or inf["internal_close_line"] or "").strip()
            if title_cand and title_cand not in excluded:
                excluded.append(title_cand[:100])
    for inf in span_infos:
        if inf["internal_close_line"] and inf["internal_close_line"] not in excluded:
            excluded.append(inf["internal_close_line"][:100])

    # Determine clean title from span heading/lines
    detected_title = ""
    first_text = getattr(span_blocks[0], "full_text", "") or ""
    first_head = getattr(span_blocks[0], "heading", "") or ""
    for cand_line in [first_head] + first_text.splitlines()[:4]:
        c = cand_line.strip(" #*")
        if _OPEN_CRF_HEADER_RE.search(c):
            detected_title = c[:120]
            break
    if not detected_title:
        detected_title = first_head[:120] or "ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES"

    confidence = min(
        0.95,
        0.48
        + (0.20 if any(x["open_cue"] for x in span_infos) else 0.0)
        + min(0.22, total_controls * 0.025)
        + (0.05 if any(x["form_section_cue"] for x in span_infos) else 0.0),
    )

    return {
        "start_idx": s_idx,
        "end_idx": e_idx,
        "start_block_id": getattr(span_blocks[0], "block_id", s_idx),
        "end_block_id": getattr(span_blocks[-1], "block_id", e_idx),
        "detected_title": detected_title,
        "confidence": round(confidence, 2),
        "excluded_neighbor_annexes": excluded[:8],
    }


def detect_crf_boundaries_in_blocks(
    blocks: Sequence[Any],
    lang: str = "fr",
    use_ai: bool = True,
) -> CRFBoundaryDetection:
    """
    Scan ALL document blocks (no 140-block cutoff) to locate the exact start and end
    of the Data Collection Form / CRF, using structural density scoring + Gemini validation.
    """
    if not blocks:
        return CRFBoundaryDetection(found=False, method="none")

    block_infos = [
        _score_block_as_crf(getattr(b, "heading", ""), getattr(b, "full_text", ""))
        for b in blocks
    ]
    heur = _find_heuristic_crf_span(blocks, block_infos)
    id_to_idx = {getattr(b, "block_id", idx): idx for idx, b in enumerate(blocks)}

    # Decide candidate window for AI boundary inspection without truncating the annex tail
    n = len(blocks)
    has_any_signal = heur is not None or any(
        inf["open_cue"] or inf["form_section_cue"] or inf["control_hits"] >= 2
        for inf in block_infos[max(0, n - 60) :]
    )

    chosen_start_idx: Optional[int] = heur["start_idx"] if heur else None
    chosen_end_idx: Optional[int] = heur["end_idx"] if heur else None
    detected_title = heur["detected_title"] if heur else ""
    confidence = heur["confidence"] if heur else 0.0
    excluded = list(heur["excluded_neighbor_annexes"]) if heur else []
    method = "heuristic" if heur else "none"
    notes = ""

    if use_ai and has_any_signal:
        if heur is not None:
            w_start = max(0, heur["start_idx"] - 3)
            w_end = min(n, heur["end_idx"] + 5)
        else:
            w_start = max(0, n - 50)
            w_end = n

        window_lines: List[str] = []
        for idx in range(w_start, w_end):
            b = blocks[idx]
            bid = getattr(b, "block_id", idx)
            inf = block_infos[idx]
            full_t = (getattr(b, "full_text", "") or "").replace("\n", " | ")
            preview = re.sub(r"\s+", " ", full_t).strip()[:280]
            window_lines.append(
                f"[B{bid}] score={inf['score']} controls={inf['control_hits']} "
                f"close={inf['closing_cue']} | Titre: {inf['heading']} | Extrait: {preview}"
            )

        prompt = f"""
Tu es un outil expert d'extraction documentaire FMSB/CAMES.
Voici une fenêtre de blocs extraits d'un protocole médical brut :

{chr(10).join(window_lines)}

Tâche : identifie avec précision le DÉBUT (`start_block_id`) et la FIN (`end_block_id`)
de la **Fiche de collecte des données / Formulaire d'enquête / Questionnaire (CRF)** uniquement.
Règles strictes :
1. Inclure le titre de la fiche et toutes ses sections cliniques (SECTION A, B, C, tableaux, questions, cases à cocher, scores).
2. S'ARRÊTER impérativement avant les autres annexes : Notice d'information, Formulaire de consentement éclairé, Clairance éthique, Autorisation administrative, Lettre d'engagement, Références.
3. Ne pas confondre un chapitre méthodologique narratif (« III.3 Outils de collecte ») avec la fiche elle-même.
4. S'il n'y a aucune véritable fiche/questionnaire, renvoie `found = false`.
Langue : {'anglais' if normalize_lang(lang) == 'en' else 'français'}.
"""
        try:
            res = gemini_router.generate_structured(
                role="segmentation",
                contents=prompt,
                schema=_AIBoundaryDecision,
                temperature=0.0,
            )
            ai_dec: _AIBoundaryDecision = res["data"]
            if ai_dec.found and ai_dec.start_block_id in id_to_idx and ai_dec.end_block_id in id_to_idx:
                s_i = id_to_idx[ai_dec.start_block_id]
                e_i = id_to_idx[ai_dec.end_block_id]
                if s_i <= e_i:
                    span_controls = sum(block_infos[k]["control_hits"] for k in range(s_i, e_i + 1))
                    if span_controls >= 1 or block_infos[s_i]["open_cue"]:
                        chosen_start_idx = s_i
                        chosen_end_idx = e_i
                        if ai_dec.detected_title.strip():
                            detected_title = ai_dec.detected_title.strip()[:120]
                        confidence = max(float(ai_dec.confidence or 0.8), heur["confidence"] if heur else 0.78)
                        for ex_a in ai_dec.excluded_neighbor_annexes:
                            if ex_a and ex_a not in excluded:
                                excluded.append(ex_a)
                        method = "ai+heuristic"
                        notes = ai_dec.reason.strip()
            elif not ai_dec.found and heur is None:
                chosen_start_idx = None
                chosen_end_idx = None
                method = "none"
                notes = ai_dec.reason.strip()
        except Exception as exc:
            if heur is not None:
                notes = f"Détection heuristique robuste utilisée (arbitrage IA indisponible : {exc})"

    if chosen_start_idx is None or chosen_end_idx is None:
        return CRFBoundaryDetection(
            found=False,
            method=method,
            excluded_neighbor_annexes=excluded,
            notes=notes or "Aucune fiche de collecte clairement délimitée détectée.",
        )

    s_blk = blocks[chosen_start_idx]
    e_blk = blocks[chosen_end_idx]
    s_lines = [ln.strip() for ln in (getattr(s_blk, "full_text", "") or "").splitlines() if ln.strip()]
    e_lines = [ln.strip() for ln in (getattr(e_blk, "full_text", "") or "").splitlines() if ln.strip()]

    return CRFBoundaryDetection(
        found=True,
        start_block_id=getattr(s_blk, "block_id", chosen_start_idx),
        end_block_id=getattr(e_blk, "block_id", chosen_end_idx),
        start_excerpt=(s_lines[0] if s_lines else getattr(s_blk, "heading", ""))[:120],
        end_excerpt=(e_lines[-1] if e_lines else getattr(e_blk, "heading", ""))[:120],
        confidence=round(min(1.0, max(0.0, confidence)), 2),
        detected_title=detected_title or "ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES",
        method=method,
        excluded_neighbor_annexes=excluded,
        notes=notes,
    )


def _split_raw_text_into_pseudo_blocks(raw_text: str) -> List[Any]:
    """Split arbitrary raw text into structural blocks for boundary detection."""
    lines = (raw_text or "").splitlines()
    blocks: List[Any] = []
    cur_heading = "Début du document"
    cur_lines: List[str] = []

    def _flush() -> None:
        nonlocal cur_lines
        body = "\n".join(cur_lines).strip()
        if body:
            blocks.append(
                SimpleNamespace(
                    block_id=len(blocks),
                    heading=cur_heading[:120],
                    paragraphs=[body],
                    full_text=body,
                )
            )
        cur_lines = []

    for ln in lines:
        s = ln.strip()
        is_head = False
        if s and len(s) <= 130 and not _line_has_form_control(s):
            if (
                s.startswith("#")
                or _OPEN_CRF_HEADER_RE.match(s)
                or _is_standalone_closing_header(s)
                or _FORM_SUBSECTION_RE.match(s)
                or (s.isupper() and len(s) >= 5)
            ):
                is_head = True
        if is_head and cur_lines:
            _flush()
            cur_heading = s.lstrip("#* ").strip() or "Bloc"
        cur_lines.append(ln)
    _flush()
    return blocks


def _trim_inline_crf_boundaries(crf_raw: str) -> Tuple[str, str, str]:
    """
    Fine line-level trim inside the isolated CRF text:
    - Trims any preamble lines before a clear CRF start header (e.g. '# ANNEXES DU PROTOCOLE').
    - Trims at the first standalone closing header (Notice, Consentement, Annexe 2, Clairance...).
    Returns (leading_non_crf, pure_crf, trailing_non_crf).
    """
    lines = (crf_raw or "").splitlines()
    if not lines:
        return "", "", ""

    start_i = 0
    for idx, ln in enumerate(lines[:15]):
        s = ln.strip()
        if _OPEN_CRF_HEADER_RE.match(s):
            # Only push start forward if preceding lines contain no questions/controls
            preceding = "\n".join(lines[:idx]).strip()
            if preceding and not _line_has_form_control(preceding) and not _CODED_QUESTION_LINE_RE.search(preceding):
                start_i = idx
            break

    end_i = len(lines)
    for idx in range(start_i + 1, len(lines)):
        if _is_standalone_closing_header(lines[idx]):
            end_i = idx
            break

    before = "\n".join(lines[:start_i]).strip()
    pure = "\n".join(lines[start_i:end_i]).strip()
    after = "\n".join(lines[end_i:]).strip()
    return before, pure, after


def slice_crf_from_text_or_blocks(
    blocks: Optional[Sequence[Any]] = None,
    raw_text: str = "",
    lang: str = "fr",
    use_ai: bool = True,
    precomputed_detection: Optional[CRFBoundaryDetection] = None,
) -> Tuple[str, str, str, CRFBoundaryDetection]:
    """
    Isolate `(before_crf, pure_crf_text, after_crf, detection)` from either pre-parsed
    document blocks (`importer.py`) or raw Markdown/text (`annexes` / `internal_library`).
    """
    work_blocks = list(blocks) if blocks is not None else _split_raw_text_into_pseudo_blocks(raw_text)
    if not work_blocks:
        return "", "", "", CRFBoundaryDetection(found=False)

    detection = (
        precomputed_detection
        if precomputed_detection is not None
        else detect_crf_boundaries_in_blocks(work_blocks, lang=lang, use_ai=use_ai)
    )
    if not detection.found or detection.start_block_id is None or detection.end_block_id is None:
        return raw_text.strip(), "", "", detection

    id_to_idx = {getattr(b, "block_id", idx): idx for idx, b in enumerate(work_blocks)}
    s_idx = id_to_idx.get(detection.start_block_id)
    e_idx = id_to_idx.get(detection.end_block_id)
    if s_idx is None or e_idx is None or s_idx > e_idx:
        return raw_text.strip(), "", "", CRFBoundaryDetection(found=False)

    before_parts = [getattr(b, "full_text", "").strip() for b in work_blocks[:s_idx] if getattr(b, "full_text", "").strip()]
    crf_raw = "\n\n".join(
        getattr(b, "full_text", "").strip() for b in work_blocks[s_idx : e_idx + 1] if getattr(b, "full_text", "").strip()
    )
    after_parts = [getattr(b, "full_text", "").strip() for b in work_blocks[e_idx + 1 :] if getattr(b, "full_text", "").strip()]

    lead, pure_crf, trail = _trim_inline_crf_boundaries(crf_raw)
    if lead:
        before_parts.append(lead)
    if trail:
        after_parts.insert(0, trail)
        first_trail = trail.splitlines()[0].strip()
        if first_trail and first_trail not in detection.excluded_neighbor_annexes:
            detection.excluded_neighbor_annexes.append(first_trail[:100])

    # Refresh excerpts to reflect line-accurate cut
    pure_lines = [ln.strip() for ln in pure_crf.splitlines() if ln.strip()]
    if pure_lines:
        detection.start_excerpt = pure_lines[0][:120]
        detection.end_excerpt = pure_lines[-1][:120]

    return (
        "\n\n".join(before_parts).strip(),
        pure_crf,
        "\n\n".join(after_parts).strip(),
        detection,
    )


# ---------------------------------------------------------------------------
# Layer 2: Preprocessor & Hybrid Compiler (CRF Text/Tables -> CRFFieldSpec[])
# ---------------------------------------------------------------------------


def _expand_pipe_table_lines(lines: List[str]) -> List[str]:
    """Convert raw Word/Markdown pipe tables into clean CRF field lines for deterministic fallback."""
    out: List[str] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if "|" in s and (s.startswith("|") or s.count("|") >= 2):
            tbl_rows: List[List[str]] = []
            while i < len(lines):
                rs = lines[i].strip()
                if "|" not in rs or (not rs.startswith("|") and rs.count("|") < 2):
                    break
                cells = [c.strip() for c in rs.strip("|").split("|")]
                # Skip Markdown divider row |---|---|
                if all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                    i += 1
                    continue
                if any(cells):
                    tbl_rows.append(cells)
                i += 1
            for r_idx, cells in enumerate(tbl_rows):
                non_empty = [c for c in cells if c]
                if not non_empty:
                    continue
                joined = " ".join(non_empty)
                # Skip obvious table column header row
                if r_idx == 0 and any(
                    h in joined.lower()
                    for h in ("variable", "question", "modalit", "code", "libell", "réponse", "reponse")
                ) and not _line_has_form_control(joined):
                    continue
                if len(non_empty) == 1:
                    if _FORM_SUBSECTION_RE.match(non_empty[0]) or non_empty[0].isupper():
                        out.append(f"### {non_empty[0]}")
                    continue
                # Case: [Code, Question, Answer/Modalities, ...] or [Question, Answer/Modalities]
                if len(non_empty) >= 3 and re.fullmatch(r"[A-Za-z]?\d+(?:\.\d+)?|Q\d+", non_empty[0]):
                    code_c = non_empty[0]
                    lbl_c = non_empty[1]
                    ans_c = " / ".join(non_empty[2:])
                    out.append(f"- **{code_c}. {lbl_c} :** {ans_c}")
                elif re.match(r"^([A-Za-z]\d+|Q\d+|\d+)[.)]\s+(.+)$", non_empty[0]):
                    m_q = re.match(r"^([A-Za-z]\d+|Q\d+|\d+)[.)]\s+(.+)$", non_empty[0])
                    code_c = m_q.group(1) if m_q else ""
                    lbl_c = m_q.group(2) if m_q else non_empty[0]
                    ans_c = " / ".join(non_empty[1:])
                    out.append(f"- **{code_c}. {lbl_c} :** {ans_c}")
                else:
                    lbl_c = non_empty[0].rstrip(" :：")
                    ans_c = " / ".join(non_empty[1:])
                    out.append(f"- **{lbl_c} :** {ans_c}")
            continue
        out.append(lines[i])
        i += 1
    return out


def _preprocess_raw_crf_text(raw_crf: str) -> str:
    """
    Normalize Word/PDF questionnaire formatting into clean CRF Markdown:
    - Pipe tables -> bulleted coded questions
    - Dotted blanks ('.....', '……') -> '____'
    - Unicode square/circle checkboxes -> '[ ]'
    - Slash choices ('Oui / Non', 'Masculin / Féminin') -> '[ ] Oui  [ ] Non'
    - Section headers without '#' -> '### SECTION ...'
    """
    raw = (raw_crf or "").strip()
    if not raw:
        return ""
    lines = _expand_pipe_table_lines(raw.splitlines())
    norm_lines: List[str] = []
    for ln in lines:
        s = ln.rstrip()
        if _FORM_SUBSECTION_RE.match(s.strip()) and not s.strip().startswith("#"):
            norm_lines.append(f"### {s.strip().strip('*')}")
            continue
        # Replace unicode checkboxes and long dotted blanks
        s = re.sub(r"[☐□▢]", "[ ]", s)
        s = re.sub(r"○(?=\s+[A-Za-zÀ-ÿ0-9])", "[ ]", s)
        s = re.sub(r"…{2,}|\.{4,}", "____", s)
        # Expand slash choices on question lines that don't yet have checkboxes (avoid headings, dates, units)
        low_s = s.strip().lower()
        if (
            ":" in s
            and not s.strip().startswith("#")
            and "[ ]" not in s
            and "____" not in s
            and "**code participant" not in low_s
            and "**hôpital" not in low_s
            and "**nom de l'enquêteur" not in low_s
        ):
            lhs, rhs = s.split(":", 1)
            rhs_clean = rhs.strip()
            if (
                "/" in rhs_clean
                and not re.search(r"JJ\s*/\s*MM|DD\s*/\s*MM|kg/m|g/dl|ui/l|ng/ml|mmol/l", rhs_clean, re.IGNORECASE)
            ):
                parts = [p.strip(" .;") for p in rhs_clean.split("/") if p.strip(" .;")]
                if 2 <= len(parts) <= 7 and all(len(p) <= 45 for p in parts):
                    s = f"{lhs}: " + "  ".join(f"[ ] {p}" for p in parts)
        norm_lines.append(s)
    return "\n".join(norm_lines).strip()


def _is_pii_field(label: str) -> bool:
    low = _clean_md_text(label).lower()
    if any(safe in low for safe in _SAFE_ID_HINTS):
        return False
    if any(pii in low for pii in _PII_KEYWORDS):
        return True
    # Standalone "Nom" or "Téléphone" or "Email"
    if re.fullmatch(r"(?:nom|pr[ée]nom|noms?|pr[ée]noms?|t[ée]l[ée]phone|telephone|e-?mail|adresse)", low):
        return True
    return False


def _sanitize_compiled_fields(
    project: ProtocolProject,
    raw_fields: List[CRFFieldSpec],
    extra_warnings: Optional[List[str]] = None,
) -> Tuple[List[CRFFieldSpec], List[str]]:
    """Enforce CAMES anonymity (remove pure PII fields), deduplicate IDs, and report missing variables."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    valid_types = {"dichotomous", "single_choice", "multi_choice", "number", "date", "text"}
    warnings: List[str] = list(extra_warnings or [])
    clean: List[CRFFieldSpec] = []
    seen_ids: set[str] = set()

    for idx, f in enumerate(raw_fields, start=1):
        lbl = _clean_md_text(f.label)
        if not lbl:
            continue
        if _is_pii_field(lbl):
            warnings.append(
                f"Anonymat CAMES : champ nominatif retiré automatiquement (« {lbl} »)."
                if lang == "fr"
                else f"CAMES anonymity: removed nominative field ('{lbl}')."
            )
            continue

        ftype = f.field_type if f.field_type in valid_types else "text"
        opts = [o.strip() for o in (f.options or []) if o and o.strip()]
        if ftype == "dichotomous" and not opts:
            opts = ["Yes", "No"] if lang == "en" else ["Oui", "Non"]
        elif ftype in ("single_choice", "multi_choice"):
            if len(opts) == 1:
                opts.append("Other" if lang == "en" else "Autre")
            elif len(opts) == 0:
                ftype = "text"

        code = (f.code or "").strip()
        base_id = _slug_id(f"{code}_{lbl}" if code else lbl, f"q_{idx}")
        fid = base_id
        suf = 2
        while fid in seen_ids:
            fid = f"{base_id}_{suf}"
            suf += 1
        seen_ids.add(fid)

        unit = (f.unit or "").strip() if ftype == "number" else ""
        placeholder = f.placeholder or (
            ("DD / MM / YYYY" if lang == "en" else "JJ / MM / AAAA")
            if ftype == "date"
            else (f"Ex. ({unit})" if unit else "")
        )
        clean.append(
            CRFFieldSpec(
                field_id=fid,
                code=code,
                section_title=_clean_md_text(f.section_title) or "SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES",
                label=lbl,
                field_type=ftype,
                options=opts if ftype in ("dichotomous", "single_choice", "multi_choice") else [],
                unit=unit,
                placeholder=placeholder,
                sub_field_label=_clean_md_text(f.sub_field_label),
                required=bool(f.required),
            )
        )

    # Check alignment with Chapter 1 operational variables without inventing unrelated questions
    field_lbl_blob = " || ".join(c.label.lower() for c in clean)
    for var in project.variables or []:
        v_name = _clean_md_text(var.name)
        if not v_name:
            continue
        tokens = [t for t in re.findall(r"[a-zà-ÿ0-9]{4,}", v_name.lower())]
        if tokens and not any(tok in field_lbl_blob for tok in tokens):
            warnings.append(
                f"Variable du protocole absente de la fiche extraite : {v_name}"
                if lang == "fr"
                else f"Protocol variable missing from extracted CRF: {v_name}"
            )

    return clean, warnings


def _compile_deterministic_from_text(project: ProtocolProject, crf_text: str) -> List[CRFFieldSpec]:
    """Deterministic parser running on preprocessed Word/PDF/Markdown CRF text."""
    normalized_md = _preprocess_raw_crf_text(crf_text)
    if not normalized_md:
        return []
    # Run `extract_crf_fields_from_project` on an isolated shadow copy whose `annexe_fiche_collecte` holds normalized_md
    shadow = project.model_copy(deep=True)
    shadow.variables = []  # Do NOT dilute pure extraction with unrelated variables here; missing vars are reported cleanly
    shadow.sections["annexe_fiche_collecte"] = SectionState(
        section_id="annexe_fiche_collecte",
        content_markdown=normalized_md,
    )
    return extract_crf_fields_from_project(shadow)


def compile_crf_fields_hybrid(
    project: ProtocolProject,
    crf_text: str,
    use_ai: bool = True,
) -> Dict[str, Any]:
    """
    Compile raw/Markdown CRF text (including Word tables, dotted blanks, slash choices,
    multi-line items, clinical scores) into structured CRFFieldSpec[], combining Gemini AI
    structured compilation with a deterministic normalizer fallback and CAMES guardrails.
    """
    lang = normalize_lang(getattr(project, "language", "fr"))
    raw = (crf_text or "").strip()
    if not raw:
        return {
            "fields": [],
            "detected_title": "",
            "model_used": "none",
            "cames_warnings": ["Aucun contenu de fiche de collecte fourni."],
        }

    normalized_preview = _preprocess_raw_crf_text(raw)
    detected_title = ""
    first_line = raw.splitlines()[0].strip()
    if _OPEN_CRF_HEADER_RE.match(first_line) or first_line.startswith("#"):
        detected_title = first_line

    raw_fields: List[CRFFieldSpec] = []
    ai_warnings: List[str] = []
    model_used = "deterministic-crf-compiler"

    if use_ai:
        vars_ctx = "\n".join(
            f"- {v.name} ({v.measurement_scale or 'N/A'}; modalités: {v.modalities or 'N/A'})"
            for v in (project.variables or [])[:35]
        ) or "Aucune variable structurée préalable."
        prompt = f"""
Tu es un compilateur expert de Fiches de Collecte médicales (CRF) vers un Masque de Saisie clinique (normes CAMES / BPC).
Analyse FIDÈLEMENT la fiche de collecte ci-dessous (même si elle contient des tableaux Word `| ... |`, des pointillés `.....`, des choix `Oui / Non` ou `M / F`, des cases `☐`, ou des questions sur plusieurs lignes) et convertis CHAQUE question en champ structuré `CRFFieldSpec`.

RÈGLES STRICTES :
1. FIDÉLITÉ EXHAUSTIVE : Extrais TOUTES les variables/questions réellement présentes dans la fiche (ne résume pas, ne saute aucune question, ne rajoute pas de champs inventés).
2. TYPES EXACTS :
   - `dichotomous` : exactement 2 modalités opposées (ex: ["Oui", "Non"] ou ["Yes", "No"]).
   - `single_choice` : choix unique parmi 2+ modalités (ex: Sexe Masculin/Féminin, tranches, stades).
   - `multi_choice` : choix multiple possible (antécédents, symptômes, facteurs, complications...).
   - `number` : valeur numérique/mesure quantitative (âge, poids, PA, score chiffré, durée...) avec `unit` explicite (`ans`, `mmHg`, `kg`, `g/dL`, `SA`, etc.).
   - `date` : toute date (`JJ / MM / AAAA`).
   - `text` : texte libre uniquement si aucune case/nombre/date ne convient.
3. ANONYMAT CAMES : Ne crée pas de champ pour Nom complet, Prénom, Téléphone ou Adresse nominative (ajoute un avertissement dans `cames_warnings` s'il y en avait dans le brouillon). Les colonnes fixes d'en-tête (Code participant, Date d'inclusion, Site hospitalier, Enquêteur) peuvent être ignorées si elles servent d'en-tête global du CRF.
4. SOUS-QUESTIONS CONDITIONNELLES : Si une question contient « (Si oui, préciser : ...) » ou « Autre : ____ », renseigne `sub_field_label`.
5. CONTEXTE DES VARIABLES DU PROTOCOLE (pour vérification des oublis dans `cames_warnings` uniquement) :
{vars_ctx}

FICHE DE COLLECTE EXTRAITE :
{normalized_preview[:14000]}
"""
        try:
            res = gemini_router.generate_structured(
                role="drafting",
                contents=prompt,
                schema=_AICRFCompilationOut,
                temperature=0.1,
            )
            dto: _AICRFCompilationOut = res["data"]
            model_used = str(res["model_used"])
            if dto.detected_title.strip():
                detected_title = dto.detected_title.strip()
            ai_warnings.extend([w.strip() for w in (dto.cames_warnings or []) if w and w.strip()])
            for idx, item in enumerate(dto.fields, start=1):
                raw_fields.append(
                    CRFFieldSpec(
                        field_id=_slug_id(f"{item.code}_{item.label}", f"q_{idx}"),
                        code=(item.code or "").strip(),
                        section_title=item.section_title or "SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES",
                        label=item.label,
                        field_type=item.field_type,
                        options=list(item.options or []),
                        unit=item.unit or "",
                        sub_field_label=item.sub_field_label or "",
                        required=bool(item.required),
                    )
                )
        except Exception as exc:
            ai_warnings.append(f"Compilation déterministe appliquée (IA indisponible : {exc})")

    if not raw_fields:
        raw_fields = _compile_deterministic_from_text(project, raw)
        if model_used == "none":
            model_used = "deterministic-crf-compiler"

    clean_fields, all_warnings = _sanitize_compiled_fields(project, raw_fields, extra_warnings=ai_warnings)
    if lang == "en" and detected_title and not detected_title.startswith("#"):
        detected_title = f"## {detected_title}"
    elif detected_title and not detected_title.startswith("#"):
        detected_title = f"## {detected_title}"

    return {
        "fields": clean_fields,
        "detected_title": detected_title,
        "model_used": model_used,
        "cames_warnings": all_warnings,
    }


# ---------------------------------------------------------------------------
# Layer 3: Non-Destructive Field Reconciliation (preserves patient_records)
# ---------------------------------------------------------------------------


def _norm_code(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (code or "").lower())


def _norm_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zà-ÿ0-9]{2,}", _clean_md_text(text).lower()))


def reconcile_crf_fields(
    existing_fields: Sequence[CRFFieldSpec],
    new_fields: Sequence[CRFFieldSpec],
) -> Tuple[List[CRFFieldSpec], Dict[str, Any]]:
    """
    Merge compiled `new_fields` against `existing_fields` WITHOUT breaking existing
    `field_id` keys referenced by `project.patient_records`.
    """
    existing = list(existing_fields or [])
    incoming = list(new_fields or [])
    used_existing_indices: set[int] = set()
    used_field_ids: set[str] = {f.field_id for f in existing}

    reconciled: List[CRFFieldSpec] = []
    preserved_count = 0
    modified_count = 0
    added_count = 0

    for nf in incoming:
        n_code = _norm_code(nf.code)
        n_lbl = _clean_md_text(nf.label).lower()
        n_toks = _norm_tokens(nf.label)

        best_idx: Optional[int] = None
        best_score = 0.0

        for idx, ef in enumerate(existing):
            if idx in used_existing_indices:
                continue
            e_code = _norm_code(ef.code)
            e_lbl = _clean_md_text(ef.label).lower()

            # Tier 1: exact normalized label (+ bonus for matching code)
            if e_lbl == n_lbl:
                score = 1.0 if (not n_code or not e_code or e_code == n_code) else 0.92
            else:
                e_toks = _norm_tokens(ef.label)
                union = e_toks | n_toks
                jaccard = (len(e_toks & n_toks) / len(union)) if union else 0.0
                if e_code and n_code and e_code == n_code and jaccard >= 0.45:
                    score = 0.80 + jaccard * 0.15
                elif jaccard >= 0.80:
                    score = jaccard
                else:
                    score = 0.0

            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None and best_score >= 0.72:
            used_existing_indices.add(best_idx)
            old_f = existing[best_idx]
            changed = (
                old_f.label != nf.label
                or old_f.field_type != nf.field_type
                or old_f.options != nf.options
                or old_f.unit != nf.unit
                or old_f.code != nf.code
                or old_f.sub_field_label != nf.sub_field_label
            )
            if changed:
                modified_count += 1
            else:
                preserved_count += 1
            reconciled.append(
                CRFFieldSpec(
                    field_id=old_f.field_id,
                    code=nf.code,
                    section_title=nf.section_title,
                    label=nf.label,
                    field_type=nf.field_type,
                    options=list(nf.options),
                    unit=nf.unit,
                    placeholder=nf.placeholder,
                    sub_field_label=nf.sub_field_label,
                    required=nf.required,
                )
            )
        else:
            # New field: guarantee zero collision with any existing field_id
            fid = nf.field_id or _slug_id(f"{nf.code}_{nf.label}", "q_item")
            base_fid = fid
            suf = 2
            while fid in used_field_ids:
                fid = f"{base_fid}_{suf}"
                suf += 1
            used_field_ids.add(fid)
            added_count += 1
            reconciled.append(nf.model_copy(update={"field_id": fid}))

    removed_count = max(0, len(existing) - len(used_existing_indices))
    types_breakdown: Dict[str, int] = {}
    sections_seen: set[str] = set()
    for rf in reconciled:
        types_breakdown[rf.field_type] = types_breakdown.get(rf.field_type, 0) + 1
        sections_seen.add(rf.section_title)

    return reconciled, {
        "preserved_count": preserved_count,
        "modified_count": modified_count,
        "added_count": added_count,
        "removed_count": removed_count,
        "total_fields": len(reconciled),
        "sections_count": len(sections_seen),
        "types_breakdown": types_breakdown,
    }


def _resolve_crf_source_text(
    project: ProtocolProject,
    source_mode: str = "auto",
    source_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Return `(raw_text, resolved_origin)` where resolved_origin is 'section' | 'annexes' | 'internal_source'."""
    crf_sec = project.sections.get("annexe_fiche_collecte")
    ann_sec = project.sections.get("annexes")
    crf_md = (crf_sec.content_markdown or "").strip() if crf_sec else ""
    ann_md = (ann_sec.content_markdown or "").strip() if ann_sec else ""

    if source_mode == "internal_source":
        chunks: List[str] = []
        # Prioritize annex sections' internal libraries, then any section matching source_id
        for sid in ("annexe_fiche_collecte", "annexes"):
            sec = project.sections.get(sid)
            if not sec:
                continue
            for entry in getattr(sec, "internal_library", []) or []:
                if source_id and entry.source_id != source_id:
                    continue
                if entry.content_markdown and entry.content_markdown.strip():
                    chunks.append(entry.content_markdown.strip())
                for tbl in entry.tables_markdown or []:
                    if tbl.strip() and tbl.strip() not in entry.content_markdown:
                        chunks.append(tbl.strip())
        if not chunks and source_id:
            for sec in project.sections.values():
                for entry in getattr(sec, "internal_library", []) or []:
                    if entry.source_id == source_id and entry.content_markdown:
                        chunks.append(entry.content_markdown.strip())
        return "\n\n".join(chunks).strip(), "internal_source"

    if source_mode == "section":
        if crf_md:
            return crf_md, "section"
        if ann_md:
            return ann_md, "annexes"
        return "", "section"

    # auto mode
    if crf_md:
        return crf_md, "section"
    if ann_md:
        return ann_md, "annexes"
    # Fallback to internal_library if sections are empty
    for sid in ("annexe_fiche_collecte", "annexes"):
        sec = project.sections.get(sid)
        if not sec:
            continue
        entries = getattr(sec, "internal_library", []) or []
        if entries:
            return "\n\n".join(e.content_markdown.strip() for e in entries if e.content_markdown), "internal_source"
    return "", "section"


def extract_and_sync_crf_for_project(
    project: ProtocolProject,
    source_mode: str = "auto",
    source_id: Optional[str] = None,
    use_ai: bool = True,
) -> Dict[str, Any]:
    """
    End-to-end dedicated pipeline:
    1. Resolve raw CRF text from `annexe_fiche_collecte`, `annexes`, or `internal_library`.
    2. Isolate exact CRF start/end boundaries without dropping adjacent annexes.
    3. Compile fields with hybrid AI + deterministic normalizer (supporting Word tables & raw syntax).
    4. Reconcile non-destructively against existing `crf_fields` (preserving `patient_records` keys).
    5. Update `annexe_fiche_collecte`, `annexes` (if sliced from annexes), `crf_fields`, and `variables`.
    """
    lang = normalize_lang(getattr(project, "language", "fr"))
    raw_text, origin = _resolve_crf_source_text(project, source_mode=source_mode, source_id=source_id)

    if not raw_text.strip():
        raise ValueError(
            "No data collection form found in Appendix 1 or internal sources. Draft or import one first."
            if lang == "en"
            else "Aucune fiche de collecte trouvée dans la section Annexe ou la bibliothèque interne."
        )

    before_crf, pure_crf, after_crf, detection = slice_crf_from_text_or_blocks(
        blocks=None,
        raw_text=raw_text,
        lang=lang,
        use_ai=use_ai,
    )

    effective_crf_text = pure_crf.strip()
    if not effective_crf_text and origin == "section":
        # User is already directly inside `annexe_fiche_collecte`: treat full section text as the form
        effective_crf_text = raw_text.strip()
        detection = CRFBoundaryDetection(
            found=True,
            start_excerpt=(effective_crf_text.splitlines()[0][:120] if effective_crf_text else ""),
            end_excerpt=(effective_crf_text.splitlines()[-1][:120] if effective_crf_text else ""),
            confidence=0.85,
            detected_title=(
                effective_crf_text.splitlines()[0].lstrip("# ").strip()[:120]
                if effective_crf_text
                else "FICHE DE COLLECTE DES DONNÉES"
            ),
            method="direct-section",
        )
    elif not effective_crf_text:
        raise ValueError(
            "Unable to isolate a distinct Data Collection Form in the selected source."
            if lang == "en"
            else "Impossible d'identifier le début et la fin d'une fiche de collecte dans la source sélectionnée."
        )

    compiled = compile_crf_fields_hybrid(project, effective_crf_text, use_ai=use_ai)
    compiled_fields: List[CRFFieldSpec] = compiled["fields"]
    if not compiled_fields:
        compiled_fields = _compile_deterministic_from_text(project, effective_crf_text)

    if not compiled_fields:
        raise ValueError(
            "No exploitable clinical questions detected in the extracted form."
            if lang == "en"
            else "Aucune question ou variable exploitable détectée dans la fiche extraite."
        )

    reconciled_fields, sync_report = reconcile_crf_fields(project.crf_fields, compiled_fields)
    sync_report["cames_warnings"] = compiled.get("cames_warnings", [])
    sync_report["source_origin"] = origin

    # If sliced out of `annexes`, leave Notice / Consentement clean inside `annexes`
    if origin == "annexes":
        ann_sec = project.sections.get("annexes")
        if ann_sec is not None:
            remaining = [p for p in (before_crf, after_crf) if p and p.strip()]
            cleaned_ann = "\n\n---\n\n".join(remaining).strip()
            ann_sec.content_markdown = cleaned_ann
            ann_sec.content_html = markdown_to_clean_html(cleaned_ann) if cleaned_ann else ""

    if "annexe_fiche_collecte" not in project.sections:
        project.sections["annexe_fiche_collecte"] = SectionState(section_id="annexe_fiche_collecte")

    crf_sec = project.sections["annexe_fiche_collecte"]
    crf_sec.content_markdown = effective_crf_text
    crf_sec.content_html = markdown_to_clean_html(effective_crf_text)
    crf_sec.status = StepStatus.IN_PROGRESS

    project.crf_fields = reconciled_fields
    header_title = compiled.get("detected_title") or detection.detected_title or ""
    sync_crf_to_project(project, sync_annexe1_markdown=True, header_title=header_title)

    return {
        "model_used": compiled.get("model_used", "deterministic-crf-compiler"),
        "detection": detection.model_dump(mode="json"),
        "sync_report": sync_report,
        "crf_fields": project.crf_fields,
        "annexe1_html": render_crf_annexe1_html(project),
    }
