import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import ProtocolProject
from app.services.gemini_client import gemini_router
from app.services.rich_formatter import markdown_to_clean_html, strip_ascii_diagrams
from app.services.working_memory import update_section_working_memory


NON_NARRATIVE_SECTION_IDS = {
    "cover_page",
    "toc",
    "abbreviations",
    "list_of_tables",
    "list_of_figures",
    "references",
    "annexes",
}

HUMAN_ACADEMIC_STYLE_INSTRUCTIONS = """
EXIGENCES DE PLUME CLINIQUE FMSB / STYLE ACADEMIQUE NATUREL (OBLIGATOIRE) :
1. Bannissement strict des tics lexicaux d'IA : n'écris JAMAIS « Il convient de noter que », « Force est de constater que », « Il est primordial/crucial/important de souligner », « Il sied de rappeler », « En tout état de cause », « À la lumière de ce qui précède », « joue un rôle crucial/prépondérant », « constitue un enjeu majeur de santé publique », ni de séries répétitives de « Par ailleurs, » / « En outre, » / « Dans ce contexte, ».
2. Variabilité rythmique naturelle (Burstiness élevée) : alterne volontairement des phrases courtes, cliniques et directes (8 à 14 mots) avec des phrases d'analyse épidémiologique ou physiopathologique plus denses (24 à 36 mots). Évite les paragraphes symétriques de 3 phrases de longueur uniforme.
3. Registre sobre d'impétrant en médecine (FMSB / CAMES) : utilise des attaques précises (« Sur le plan physiopathologique, ... », « Au Cameroun, les données hospitalières montrent... », « Chez les patients admis pour..., l'atteinte... », « Ce déficit de données locales justifie d'évaluer... »).
4. Aucune conclusion méta-discursive artificielle en fin de sous-section (ne résume pas ce que tu viens d'écrire).
5. Typographie française standard : n'utilise pas de tirets cadratins anglais (« — ») ni de caractères invisibles ; utilise la virgule ou les parenthèses pour les incises, et les guillemets français « ... » si besoin.
"""

_INVISIBLE_UNICODE_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060\u00ad]")

# Opening clichés matched at beginning of text, paragraph, HTML tag boundary, or sentence boundary.
_OPENING_CLICHE_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|[\n>]|[.!?]\s+)Il convient également de (?:noter|souligner|rappeler|mentionner|préciser)\s+que\s+", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Il convient d'(?:observer|indiquer|ajouter|insister sur le fait)\s+que\s+", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Il convient de (?:noter|souligner|rappeler|mentionner|préciser)\s+que\s+", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Force est de constater\s+que\s+", re.I), ""),
    (re.compile(
        r"(^|[\n>]|[.!?]\s+)Il est (?:important|primordial|crucial|essentiel|fondamental|indispensable|nécessaire|pertinent|utile|capital)\s+de\s+(?:noter|souligner|rappeler|mentionner|préciser|relever)\s+que\s+",
        re.I,
    ), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Il sied de (?:noter|souligner|rappeler|mentionner|préciser)\s+que\s+", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Dans cette optique,\s*(?:il faut souligner que\s+|il convient de noter que\s+)?", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Dans ce contexte,\s*(?:il est (?:crucial|important|primordial) de (?:noter|souligner) que\s+)?", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)À la lumière de ce qui précède,\s*", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)En tout état de cause,\s*", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Il va sans dire\s+que\s+", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Comme mentionné précédemment,\s*", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Il apparaît clairement\s+que\s+", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Soulignons d'emblée\s+que\s+", re.I), ""),
    (re.compile(r"(^|[\n>]|[.!?]\s+)Notons toutefois\s+que\s+", re.I), "Toutefois, "),
]

# Mid-sentence LLM phrases to neutralize into natural clinical phrasing.
_MID_SENTENCE_CLICHES: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bjoue un rôle (?:crucial|prépondérant|central|primordial|majeur|clé)\s+dans\b", re.I), "intervient directement dans"),
    (re.compile(r"\bjouent un rôle (?:crucial|prépondérant|central|primordial|majeur|clé)\s+dans\b", re.I), "interviennent directement dans"),
    (re.compile(r"\brevêt une importance (?:capitale|cruciale|primordiale|majeure)\b", re.I), "présente un intérêt majeur"),
    (re.compile(r"\bconstitue un enjeu (?:majeur|crucial|de taille) de santé publique\b", re.I), "représente un problème prioritaire de santé publique"),
    (re.compile(r"\bforce est de constater\s+que\s+", re.I), ""),
    (re.compile(r"\bil convient de (?:noter|souligner|rappeler)\s+que\s+", re.I), ""),
]

# Residual cliché detector patterns used for telemetry scoring.
_CLICHE_DETECTOR_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\bil convient de (?:noter|souligner|rappeler|mentionner|préciser)\b", re.I),
    re.compile(r"\bforce est de constater\b", re.I),
    re.compile(r"\bil est (?:primordial|crucial|fondamental|indispensable)\s+de\s+(?:noter|souligner|rappeler)\b", re.I),
    re.compile(r"\bil sied de (?:noter|souligner|rappeler)\b", re.I),
    re.compile(r"\bà la lumière de ce qui précède\b", re.I),
    re.compile(r"\ben tout état de cause\b", re.I),
    re.compile(r"\bil va sans dire que\b", re.I),
    re.compile(r"\bjoue(?:nt)? un rôle (?:crucial|prépondérant|primordial)\b", re.I),
    re.compile(r"\bconstitue un enjeu (?:majeur|crucial|de taille)\b", re.I),
]


class HumanizeSectionOutput(BaseModel):
    content_markdown: str = Field(
        description=(
            "Texte intégralement réécrit dans une prose médicale naturelle FMSB/CAMES, "
            "conservant 100 % des citations [n], chiffres, unités et formules mathématiques."
        )
    )
    style_adjustments_summary: str = Field(
        default="",
        description="Brève note en français expliquant les améliorations stylistiques apportées.",
    )


def _protect_sensitive_segments(text: str) -> Tuple[str, List[str]]:
    """Stash LaTeX math, Markdown pipe table lines, URLs, and HTML tags before sanitization."""
    placeholders: List[str] = []

    def _stash(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"@@SMRPROT{len(placeholders) - 1}@@"

    masked = re.sub(r"\$\$[\s\S]+?\$\$", _stash, text)
    masked = re.sub(r"(?<!\$)\$(?!\$)[^$\n]+?(?<!\$)\$(?!\$)", _stash, masked)
    masked = re.sub(r"(?m)^\s*\|.*\|\s*$", _stash, masked)
    masked = re.sub(r"https?://[^\s<>\"')]+", _stash, masked)
    masked = re.sub(r"<[^>]+>", _stash, masked)
    return masked, placeholders


def _restore_sensitive_segments(text: str, placeholders: List[str]) -> str:
    """Restore protected LaTeX, table lines, URLs, and HTML tags."""
    out = text
    for idx, val in enumerate(placeholders):
        out = out.replace(f"@@SMRPROT{idx}@@", val)
    return out


def _capitalize_after_prefix(prefix: str, remainder: str) -> str:
    """Capitalize the first alphabetical character of `remainder` unless it starts with a placeholder."""
    if not remainder:
        return remainder
    if remainder.startswith("@@SMRPROT"):
        return remainder
    return remainder[0].upper() + remainder[1:]


def sanitize_llm_french_typography(text: str) -> Tuple[str, int]:
    """Deterministically strip invisible LLM Unicode, AI clichés, and English em-dashes."""
    if not text:
        return text, 0

    changes = 0
    raw = text

    # 1. Remove zero-width / BOM / soft-hyphen characters everywhere (even before protecting HTML)
    cleaned_invis, n_invis = _INVISIBLE_UNICODE_RE.subn("", raw)
    changes += n_invis

    # Normalize Unicode non-breaking space only where repeated awkwardly
    masked, placeholders = _protect_sensitive_segments(cleaned_invis)

    # 2. Neutralize em-dashes '—' (typical strong GPT/Gemini marker)
    # Keep numeric ranges as standard en-dash / hyphen without spaces
    masked, n_num_em = re.subn(r"(\d)\s*[—]\s*(\d)", r"\1–\2", masked)
    changes += n_num_em

    # Paired em-dash parenthetical clauses: "mot — incise — suite" -> "mot, incise, suite"
    masked, n_pair_em = re.subn(r"\s+—\s+([^—\n]{2,140}?)\s+—(?=\s|[,.;:!?]|$)", r", \1,", masked)
    changes += n_pair_em

    # Remaining isolated em-dashes: " — " -> ", " (or ": " before capital? comma is safer)
    masked, n_iso_em = re.subn(r"\s*—\s*", ", ", masked)
    changes += n_iso_em

    # Collapse accidental double commas introduced by em-dash replacement (", ,")
    masked = re.sub(r",\s*,", ",", masked)

    # 3. Strip repetitive LLM opening clichés while restoring sentence capitalization
    for pattern, repl_prefix in _OPENING_CLICHE_PATTERNS:
        full_pat = re.compile(pattern.pattern + r"([^\s]+)", pattern.flags)

        def _replace_with_cap(m: re.Match[str], r_pre: str = repl_prefix) -> str:
            nonlocal changes
            changes += 1
            lead = m.group(1)
            tok = m.group(2)
            if tok.startswith("@@SMRPROT"):
                return f"{lead}{r_pre}{tok}" if r_pre else f"{lead}{tok}"
            is_acronym = len(tok) >= 2 and tok[:2].isupper()
            if r_pre:
                head = tok if is_acronym else (tok[0].lower() + tok[1:] if tok[0].isalpha() else tok)
                return f"{lead}{r_pre}{head}"
            head = tok[0].upper() + tok[1:] if tok[0].isalpha() else tok
            return f"{lead}{head}"

        masked = full_pat.sub(_replace_with_cap, masked)

    # 4. Neutralize mid-sentence clichés
    for pat, repl in _MID_SENTENCE_CLICHES:
        masked, n_mid = pat.subn(repl, masked)
        changes += n_mid

    # 5. Soften excessive consecutive "Par ailleurs," / "En outre," at start of paragraphs/sentences
    for connector in ("Par ailleurs,", "En outre,"):
        parts = re.split(rf"(?i)\b({re.escape(connector)}\s+)", masked)
        if len(parts) > 3:
            seen = 0
            rebuilt: List[str] = []
            for part in parts:
                if re.fullmatch(rf"(?i){re.escape(connector)}\s+", part):
                    seen += 1
                    if seen == 1:
                        rebuilt.append(part)
                    else:
                        changes += 1
                        rebuilt.append("@@CAPNEXT@@")
                else:
                    rebuilt.append(part)
            masked = "".join(rebuilt)
            masked = re.sub(
                r"@@CAPNEXT@@([^\s])",
                lambda m: m.group(1).upper() if m.group(1).isalpha() else m.group(1),
                masked,
            )

    # Normalize multiple spaces (without touching indentation/newlines)
    masked = re.sub(r" {2,}", " ", masked)
    restored = _restore_sensitive_segments(masked, placeholders)
    return restored.strip() if text.strip() else restored, changes


def _strip_markup_for_metrics(text: str) -> str:
    """Extract clean plain prose from Markdown/HTML to compute burstiness metrics."""
    s = re.sub(r"<[^>]+>", " ", text or "")
    s = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", s)
    s = re.sub(r"(?m)^\s*\|.*\|\s*$", "", s)
    s = re.sub(r"\$\$[\s\S]+?\$\$|\$[^$\n]+\$", " formule ", s)
    s = re.sub(r"[*_`~]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _split_french_sentences(plain_text: str) -> List[str]:
    """Split French medical prose into sentences while ignoring common abbreviations."""
    if not plain_text.strip():
        return []
    protected = plain_text
    for abbr in ("Dr.", "Pr.", "M.", "Mme.", "Mlle.", "p. ex.", "et al.", "cf.", "fig.", "Fig.", "Tab."):
        protected = protected.replace(abbr, abbr.replace(".", "@@DOT@@"))
    protected = re.sub(r"(\d)\.(\d)", r"\1@@DOT@@\2", protected)
    raw_sents = re.split(r"(?<=[.!?])\s+", protected)
    sents = [s.replace("@@DOT@@", ".").strip() for s in raw_sents if len(s.split()) >= 2]
    return sents


def compute_humanization_metrics(text: str) -> Dict[str, Any]:
    """Compute burstiness, cliché density, technical artifact counts, and overall style score."""
    invis_count = len(_INVISIBLE_UNICODE_RE.findall(text or ""))
    em_dash_count = len(re.findall(r"(?<!\d)—(?!\d)", text or ""))
    remaining_cliches = sum(len(p.findall(text or "")) for p in _CLICHE_DETECTOR_PATTERNS)

    plain = _strip_markup_for_metrics(text)
    sents = _split_french_sentences(plain)
    lengths = [len(s.split()) for s in sents]

    if lengths:
        mean_len = sum(lengths) / len(lengths)
        variance = sum((x - mean_len) ** 2 for x in lengths) / len(lengths)
        burstiness_sd = round(math.sqrt(variance), 2)
        avg_len = round(mean_len, 1)
    else:
        burstiness_sd = 0.0
        avg_len = 0.0

    # Score heuristic out of 100 (higher = fewer AI markers + richer rhythmic burstiness)
    score = 92
    score -= min(40, remaining_cliches * 12)
    score -= min(18, em_dash_count * 6)
    score -= min(15, invis_count * 5)
    if len(lengths) >= 4:
        if burstiness_sd < 3.0:
            score -= 12
        elif burstiness_sd >= 5.5:
            score += 5
    score = max(10, min(100, int(round(score))))

    return {
        "sentence_count": len(lengths),
        "avg_sentence_words": avg_len,
        "burstiness_sd": burstiness_sd,
        "remaining_cliches": remaining_cliches,
        "em_dash_count": em_dash_count,
        "invisible_unicode_count": invis_count,
        "human_style_score": score,
    }


def _extract_invariant_numbers(text: str) -> List[str]:
    """Extract critical scientific figures (%, p-values, IC 95 %, sample counts) for invariant checking."""
    patterns = [
        r"\b\d+(?:[.,]\d+)?\s*%",
        r"\bp\s*[<>=≤≥]\s*0[.,]\d+\b",
        r"\bn\s*=\s*\d+\b",
    ]
    found: List[str] = []
    for pat in patterns:
        for m in re.findall(pat, text or "", flags=re.I):
            norm = re.sub(r"\s+", "", m.lower()).replace(",", ".")
            found.append(norm)
    return found


def verify_and_restore_scientific_invariants(
    original_md: str,
    candidate_md: str,
) -> Tuple[str, Dict[str, Any]]:
    """Ensure 100% of Vancouver [n] citations and key statistics survive stylistic rewriting."""
    orig_cites = sorted({int(x) for x in re.findall(r"\[(\d+)\]", original_md or "")})
    cand_cites = sorted({int(x) for x in re.findall(r"\[(\d+)\]", candidate_md or "")})

    # Strip any citation tag [n] that did not exist in the original draft
    invented = [c for c in cand_cites if c not in set(orig_cites)]
    working = candidate_md or ""
    if invented:
        for inv in invented:
            working = re.sub(rf"\s*\[{inv}\]", "", working)

    cand_after_strip = sorted({int(x) for x in re.findall(r"\[(\d+)\]", working)})
    missing = [c for c in orig_cites if c not in set(cand_after_strip)]

    orig_nums = _extract_invariant_numbers(original_md)
    cand_nums = _extract_invariant_numbers(working)
    missing_nums = [x for x in set(orig_nums) if cand_nums.count(x) < orig_nums.count(x)]

    # Empty or severely truncated check
    orig_words = len((original_md or "").split())
    cand_words = len(working.split())
    too_short = orig_words >= 30 and cand_words < int(orig_words * 0.55)

    fallback_used = False
    restored_from_paragraphs = 0

    if missing or missing_nums or too_short:
        # Attempt granular paragraph-level restoration first where each original paragraph's invariants belong
        orig_paras = [p for p in re.split(r"\n\s*\n", (original_md or "").strip()) if p.strip()]
        cand_paras = [p for p in re.split(r"\n\s*\n", working.strip()) if p.strip()]

        if len(orig_paras) == len(cand_paras) and not too_short:
            merged_paras: List[str] = []
            for op, cp in zip(orig_paras, cand_paras):
                op_cites = set(re.findall(r"\[(\d+)\]", op))
                cp_cites = set(re.findall(r"\[(\d+)\]", cp))
                op_nums = set(_extract_invariant_numbers(op))
                cp_nums = set(_extract_invariant_numbers(cp))
                if not op_cites.issubset(cp_cites) or not op_nums.issubset(cp_nums):
                    sanitized_op, _ = sanitize_llm_french_typography(op)
                    merged_paras.append(sanitized_op)
                    restored_from_paragraphs += 1
                else:
                    merged_paras.append(cp)
            working = "\n\n".join(merged_paras)
        else:
            # Global safe fallback: deterministic humanization of the original text guarantees 100% invariance
            working, _ = sanitize_llm_french_typography(original_md or "")
            fallback_used = True

    final_cites = sorted({int(x) for x in re.findall(r"\[(\d+)\]", working)})
    still_missing = [c for c in orig_cites if c not in set(final_cites)]
    if still_missing:
        working, _ = sanitize_llm_french_typography(original_md or "")
        fallback_used = True
        final_cites = sorted({int(x) for x in re.findall(r"\[(\d+)\]", working)})

    return working, {
        "citations_expected": orig_cites,
        "citations_final": final_cites,
        "citations_preserved": set(orig_cites).issubset(set(final_cites)),
        "invented_removed": invented,
        "missing_detected": missing,
        "missing_numbers_detected": missing_nums,
        "paragraphs_restored": restored_from_paragraphs,
        "fallback_used": fallback_used,
    }


def _deterministic_humanize_prose(text: str) -> str:
    """Apply zero-hallucination deterministic rhythm & lexical variation when AI rewrite isn't used."""
    cleaned, _ = sanitize_llm_french_typography(text)
    # Break monotonous transitional leads if repeated
    cleaned = re.sub(r"(?m)^De plus,\s+", "", cleaned)
    cleaned = re.sub(r"(?m)^En effet,\s+", "", cleaned)
    # Capitalize start of paragraph lines if lowered after stripping "De plus, "/"En effet, "
    cleaned = re.sub(
        r"(?m)^([a-zà-ÿ])",
        lambda m: m.group(1).upper(),
        cleaned,
    )
    return cleaned


def humanize_section_with_ai(
    project: ProtocolProject,
    section_id: str,
    use_ai: bool = True,
) -> Dict[str, Any]:
    """Humanize prose style of a single protocol section while preserving 100% of facts & citations."""
    spec = next((s for s in FMSB_SECTIONS if s.id == section_id), None)
    sec = project.sections.get(section_id)
    if sec is None:
        raise ValueError(f"Section inconnue : {section_id}")

    if section_id in NON_NARRATIVE_SECTION_IDS:
        return {
            "section_id": section_id,
            "skipped": True,
            "reason": "Section structurée automatique (non soumise à réécriture stylistique).",
            "model_used": "none",
            "content_markdown": sec.content_markdown,
            "content_html": sec.content_html,
            "before_metrics": compute_humanization_metrics(sec.content_markdown),
            "after_metrics": compute_humanization_metrics(sec.content_markdown),
            "cliches_removed": 0,
            "citations_preserved": True,
        }

    orig_md = (sec.content_markdown or "").strip()
    if not orig_md:
        return {
            "section_id": section_id,
            "skipped": True,
            "reason": "Section vide.",
            "model_used": "none",
            "content_markdown": sec.content_markdown,
            "content_html": sec.content_html,
            "before_metrics": compute_humanization_metrics(""),
            "after_metrics": compute_humanization_metrics(""),
            "cliches_removed": 0,
            "citations_preserved": True,
        }

    before_metrics = compute_humanization_metrics(orig_md)
    section_title = f"{spec.chapter_title} — {spec.title}" if spec else section_id
    orig_cites = sorted({int(x) for x in re.findall(r"\[(\d+)\]", orig_md)})

    model_used = "humanizer-deterministe-fmsb"
    candidate_md = ""
    adjustment_note = ""

    if use_ai:
        prompt = f"""
Tu es un médecin chercheur et rédacteur scientifique chevronné de la FMSB (Université de Yaoundé I).
Réécris le texte ci-dessous pour lui donner une **plume humaine authentique, sobre et fluide**, indiscernable d'une rédaction académique manuelle.

SECTION : {section_title}

CONSIGNES STYLISTIQUES IMPÉRATIVES :
1. Supprime tous les tics de langage d'IA (aucun « Il convient de noter que », « Force est de constater », « Il est crucial/primordial de souligner », « Par ailleurs/En outre » répétitifs, aucune conclusion résumante artificielle).
2. Crée une **variabilité rythmique naturelle (Burstiness)** : mêle des phrases courtes et tranchantes (8 à 14 mots) à des phrases cliniques ou épidémiologiques plus élaborées (24 à 36 mots). Varie les longueurs de paragraphes et les attaques de phrases.
3. Utilise des tournures médicales naturelles françaises (voix active, sujets concrets : « Les séries hospitalières camerounaises… », « Chez l'adulte hypertendu… », « L'analyse portera sur… »).
4. **RÈGLE SCIENTIFIQUE ABSOLUE (ZÉRO PERTE, ZÉRO AJOUT)** :
   - Conserve à l'identique **TOUTES** les références entre crochets : {', '.join(f'[{c}]' for c in orig_cites) if orig_cites else 'aucune'}.
   - Conserve à l'identique **TOUS** les nombres, pourcentages, p-values, intervalles de confiance, noms d'hôpitaux, formules mathématiques `$...$` / `$$...$$`, titres Markdown `#`/`##` et balises `[À compléter ...]`.
   - N'ajoute aucune statistique, aucune citation inventée et aucun tiret cadratin (`—`).
5. Maintiens un volume de mots proche de l'original (±15 %).

TEXTE ORIGINAL À HUMANISER :
{orig_md}
"""
        try:
            res = gemini_router.generate_structured(
                role="drafting",
                contents=prompt,
                schema=HumanizeSectionOutput,
                temperature=0.42,
            )
            dto: HumanizeSectionOutput = res["data"]
            candidate_md = (dto.content_markdown or "").strip()
            adjustment_note = (dto.style_adjustments_summary or "").strip()
            model_used = str(res.get("model_used") or "gemini-humanizer")
        except Exception:
            candidate_md = _deterministic_humanize_prose(orig_md)
            adjustment_note = "Humanisation typographique et rythmique déterministe appliquée (sans appel LLM)."
            model_used = "humanizer-deterministe-fmsb"
    else:
        candidate_md = _deterministic_humanize_prose(orig_md)
        adjustment_note = "Purge déterministe des tics de langage et artefacts typographiques LLM."

    sanitized_md, sanitize_count = sanitize_llm_french_typography(candidate_md)
    verified_md, invariants_report = verify_and_restore_scientific_invariants(orig_md, sanitized_md)
    final_md = strip_ascii_diagrams(verified_md)
    final_html = markdown_to_clean_html(final_md)

    from app.services.drafting import deterministic_section_checks

    det_notes = deterministic_section_checks(project, section_id, final_md)
    sec.content_markdown = final_md
    sec.content_html = final_html
    sec.audit_notes = det_notes

    if use_ai:
        try:
            update_section_working_memory(project, section_id)
        except Exception:
            pass

    after_metrics = compute_humanization_metrics(final_md)
    cliches_delta = max(0, int(before_metrics["remaining_cliches"]) - int(after_metrics["remaining_cliches"]))

    return {
        "section_id": section_id,
        "skipped": False,
        "model_used": model_used,
        "content_markdown": final_md,
        "content_html": final_html,
        "before_metrics": before_metrics,
        "after_metrics": after_metrics,
        "cliches_removed": cliches_delta + sanitize_count,
        "citations_preserved": bool(invariants_report["citations_preserved"]),
        "invariants_report": invariants_report,
        "style_adjustments_summary": adjustment_note
        or f"Style FMSB appliqué (score naturel : {before_metrics['human_style_score']} → {after_metrics['human_style_score']}/100).",
        "remaining_notes": det_notes,
    }


def humanize_entire_protocol(
    project: ProtocolProject,
    use_ai: bool = True,
) -> Dict[str, Any]:
    """Humanize all drafted narrative sections of a protocol prior to Word/PDF compilation."""
    section_reports: List[Dict[str, Any]] = []
    total_cliches_removed = 0
    scores_before: List[int] = []
    scores_after: List[int] = []
    models_used: List[str] = []
    all_citations_preserved = True

    for spec in FMSB_SECTIONS:
        if spec.id in NON_NARRATIVE_SECTION_IDS:
            continue
        sec = project.sections.get(spec.id)
        if not sec or not (sec.content_markdown or "").strip():
            continue

        res = humanize_section_with_ai(project, spec.id, use_ai=use_ai)
        if res.get("skipped"):
            continue
        section_reports.append(
            {
                "section_id": spec.id,
                "title": spec.title,
                "model_used": res["model_used"],
                "before_score": res["before_metrics"]["human_style_score"],
                "after_score": res["after_metrics"]["human_style_score"],
                "burstiness_sd": res["after_metrics"]["burstiness_sd"],
                "cliches_removed": res["cliches_removed"],
                "citations_preserved": res["citations_preserved"],
            }
        )
        total_cliches_removed += int(res.get("cliches_removed") or 0)
        scores_before.append(int(res["before_metrics"]["human_style_score"]))
        scores_after.append(int(res["after_metrics"]["human_style_score"]))
        if res.get("model_used") and res["model_used"] not in models_used:
            models_used.append(str(res["model_used"]))
        if not res.get("citations_preserved", True):
            all_citations_preserved = False

    avg_before = round(sum(scores_before) / len(scores_before)) if scores_before else 100
    avg_after = round(sum(scores_after) / len(scores_after)) if scores_after else 100

    return {
        "sections_humanized": len(section_reports),
        "total_cliches_removed": total_cliches_removed,
        "average_before_score": avg_before,
        "average_human_score": avg_after,
        "citations_preserved": all_citations_preserved,
        "models_used": models_used,
        "model_used": next((m for m in models_used if m not in ("humanizer-deterministe-fmsb", "none")), models_used[0] if models_used else "humanizer-deterministe-fmsb"),
        "author_signature": (project.cover.author_name or "Impétrant FMSB").strip(),
        "details": section_reports,
    }


def apply_human_author_metadata_docx(doc: Any, project: ProtocolProject) -> None:
    """Replace default 'python-docx' properties with authentic student author metadata."""
    author = (project.cover.author_name or "Impétrant FMSB").strip()
    title = (project.cover.title or project.name or "Protocole de recherche médicale").strip()
    degree = (project.cover.degree_label or "Doctorat en Médecine").strip()
    dept = (project.cover.department_fr or "Médecine").strip()
    uni = (getattr(project.cover, "university_fr", "") or "Université de Yaoundé I").strip()
    fac = (getattr(project.cover, "faculty_fr", "") or "FMSB").strip()

    props = doc.core_properties
    props.author = author
    props.last_modified_by = author
    props.title = title[:250]
    props.subject = f"Protocole de thèse ({degree}) - {fac} / {uni}"
    props.category = dept
    props.comments = ""
    props.keywords = f"Protocole de recherche, {dept}, FMSB"


def apply_human_author_metadata_pdf(pdf_path: Path, project: ProtocolProject) -> None:
    """Overwrite PDF technical producer/creator metadata with authentic author & Word tags."""
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return

    from pypdf import PdfReader, PdfWriter

    author = (project.cover.author_name or "Impétrant FMSB").strip()
    title = (project.cover.title or project.name or "Protocole de recherche médicale").strip()
    degree = (project.cover.degree_label or "Doctorat en Médecine").strip()
    uni = (getattr(project.cover, "university_fr", "") or "Université de Yaoundé I").strip()
    fac = (getattr(project.cover, "faculty_fr", "") or "FMSB").strip()

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    writer.append(reader)
    writer.add_metadata(
        {
            "/Author": author,
            "/Title": title[:250],
            "/Subject": f"Protocole de recherche ({degree}) - {fac} / {uni}",
            "/Creator": "Microsoft® Word",
            "/Producer": "Microsoft® Word",
        }
    )

    tmp_path = pdf_path.with_suffix(".meta.tmp.pdf")
    with tmp_path.open("wb") as fh:
        writer.write(fh)
    tmp_path.replace(pdf_path)
