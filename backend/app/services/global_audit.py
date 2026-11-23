import html as html_lib
import re
from typing import Dict, List, Set, Tuple

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import (
    AbbreviationItem,
    BudgetItem,
    LiteratureSynthesisItem,
    ProtocolProject,
    StepStatus,
    VariableItem,
)
from app.services.docx_template import _extract_abbreviations_from_markdown
from app.services.storage import sync_special_sections

_STOP_UPPER_TOKENS: Set[str] = {
    "II",
    "III",
    "IV",
    "VI",
    "VII",
    "VIII",
    "IX",
    "XI",
    "XII",
    "XIII",
    "XIV",
    "XV",
    "DE",
    "DU",
    "LA",
    "LE",
    "LES",
    "DES",
    "ET",
    "OU",
    "EN",
    "AU",
    "AUX",
    "UN",
    "UNE",
    "PAR",
    "SUR",
    "SOUS",
    "DANS",
    "POUR",
    "AVEC",
    "SANS",
    "EST",
    "SONT",
    "ONT",
    "SERA",
    "PEUT",
    "NOUS",
    "VOUS",
    "CM",
    "MM",
    "KM",
    "ML",
    "DL",
    "MG",
    "KG",
    "UI",
    "HZ",
    "FCFA",
    "USD",
    "EUR",
    "PDF",
    "DOCX",
    "HTML",
    "SVG",
    "PNG",
    "URL",
    "DOI",
    "PMID",
    "HTTP",
    "HTTPS",
    "INTRODUCTION",
    "CHAPITRE",
    "REFERENCES",
    "ANNEXES",
    "METHODOLOGIE",
    "PROBLEMATIQUE",
    "TABLE",
}


def _strip_html(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw or "")
    return html_lib.unescape(re.sub(r"\s+", " ", txt)).strip()


def _section_plain_text(project: ProtocolProject, section_id: str) -> str:
    sec = project.sections.get(section_id)
    if not sec:
        return ""
    if sec.content_markdown and sec.content_markdown.strip():
        return sec.content_markdown.strip()
    if sec.content_html and sec.content_html.strip():
        return _strip_html(sec.content_html)
    return ""


def _parse_float(raw: str, default: float = 0.0) -> float:
    cleaned = re.sub(r"[^\d.,-]", "", (raw or "").replace("\xa0", "").replace(" ", ""))
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return default


def _clean_full_form(phrase: str) -> str:
    s = re.sub(r"\s+", " ", (phrase or "").strip(" :;.,-–—'\""))
    s = re.sub(
        r"^(?:de\s+la|de\s+l'|du|des|la|le|les|l'|un|une|en|par)\s+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    return s.strip()


def _collect_defined_acronyms(project: ProtocolProject) -> Set[str]:
    defined: Set[str] = {
        a.acronym.strip().upper()
        for a in project.abbreviations
        if a.acronym and a.acronym.strip()
    }
    abbr_sec = project.sections.get("abbreviations")
    if abbr_sec:
        pairs, _ = _extract_abbreviations_from_markdown(abbr_sec.content_markdown)
        for ac, _ in pairs:
            if ac.strip():
                defined.add(ac.strip().upper())
        for ct in getattr(abbr_sec, "custom_tables", []) or []:
            for row in ct.rows:
                if row and row[0].strip():
                    defined.add(row[0].strip().upper())
    return defined


def sync_structured_from_sections(project: ProtocolProject) -> Dict[str, int]:
    """Extract abbreviations, variables, literature synthesis, and budget rows from authored sections."""
    added_abbr = 0
    added_vars = 0
    added_lit = 0
    added_budget = 0

    # 1. Abbreviations from abbreviations section + custom_tables + inline 'Definition (SIGLE)' patterns
    existing_abbr_keys = {a.acronym.strip().upper(): a for a in project.abbreviations if a.acronym.strip()}
    abbr_sec = project.sections.get("abbreviations")
    candidates: List[Tuple[str, str]] = []
    if abbr_sec:
        pairs, _ = _extract_abbreviations_from_markdown(abbr_sec.content_markdown)
        candidates.extend(pairs)
        for ct in getattr(abbr_sec, "custom_tables", []) or []:
            for row in ct.rows:
                if len(row) >= 2 and row[0].strip() and row[1].strip():
                    candidates.append((row[0].strip(), row[1].strip()))

    inline_abbr_re = re.compile(
        r"([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9\s'’-]{5,85}?)\s*\(([A-Z][A-Z0-9/-]{1,9})\)"
    )
    for spec in FMSB_SECTIONS:
        if spec.chapter_id in ("preliminaires", "references", "annexes"):
            continue
        txt = _section_plain_text(project, spec.id)
        for full_raw, sigle in inline_abbr_re.findall(txt):
            clean_sigle = sigle.strip().upper()
            if clean_sigle in _STOP_UPPER_TOKENS:
                continue
            full_clean = _clean_full_form(full_raw)
            if len(full_clean) >= 6:
                candidates.append((clean_sigle, full_clean))

    for ac, ff in candidates:
        key = ac.strip().upper()
        if not key or key in existing_abbr_keys or key in _STOP_UPPER_TOKENS:
            continue
        item = AbbreviationItem(acronym=ac.strip(), full_form=ff.strip())
        project.abbreviations.append(item)
        existing_abbr_keys[key] = item
        added_abbr += 1

    # 2. Study variables from chap1_variables custom tables or bullet definitions
    existing_var_keys = {v.name.strip().lower() for v in project.variables if v.name.strip()}
    var_sec = project.sections.get("chap1_variables")
    if var_sec:
        for ct in getattr(var_sec, "custom_tables", []) or []:
            for row in ct.rows:
                if not row or not row[0].strip():
                    continue
                vname = row[0].strip()
                if vname.lower() in existing_var_keys:
                    continue
                project.variables.append(
                    VariableItem(
                        name=vname,
                        role=row[1].strip() if len(row) > 1 and row[1].strip() else "Indépendante",
                        definition=row[2].strip() if len(row) > 2 else "",
                        measurement_scale=row[3].strip() if len(row) > 3 else "",
                        collection_tool=row[4].strip() if len(row) > 4 and row[4].strip() else "Fiche de collecte",
                    )
                )
                existing_var_keys.add(vname.lower())
                added_vars += 1
        var_bullet_re = re.compile(r"^\s*(?:[-*•]\s+)?\*\*([^*]{2,60})\*\*\s*[:–-]\s*(.+)$")
        for line in (var_sec.content_markdown or "").splitlines():
            m = var_bullet_re.match(line.strip())
            if not m:
                continue
            vname = m.group(1).strip()
            vdef = m.group(2).strip()
            if vname.lower() in existing_var_keys:
                continue
            if any(w in vname.lower() for w in ("variable", "type", "remarque", "note")):
                continue
            project.variables.append(VariableItem(name=vname, definition=vdef))
            existing_var_keys.add(vname.lower())
            added_vars += 1

    # 3. Literature synthesis from chap2_etat_connaissances custom tables or approved segments/refs
    existing_lit_keys = {
        it.authors_country_year.strip().lower()
        for it in project.literature_synthesis
        if it.authors_country_year.strip()
    }
    lit_sec = project.sections.get("chap2_etat_connaissances")
    if lit_sec:
        for ct in getattr(lit_sec, "custom_tables", []) or []:
            for row in ct.rows:
                if len(row) < 2 or not row[0].strip():
                    continue
                key = row[0].strip().lower()
                if key in existing_lit_keys:
                    continue
                project.literature_synthesis.append(
                    LiteratureSynthesisItem(
                        authors_country_year=row[0].strip(),
                        study_design=row[1].strip() if len(row) > 1 else "",
                        population=row[2].strip() if len(row) > 2 else "",
                        main_results=row[3].strip() if len(row) > 3 else "",
                        limits=row[4].strip() if len(row) > 4 else "",
                    )
                )
                existing_lit_keys.add(key)
                added_lit += 1

    if not project.literature_synthesis and project.references:
        ref_map = {r.ref_id: r for r in project.references}
        for seg in project.article_segments:
            if not seg.approved or seg.ref_id not in ref_map:
                continue
            ref = ref_map[seg.ref_id]
            author_lbl = f"{ref.authors[0]} et al. ({ref.year or 's.d.'})" if ref.authors else (ref.title[:40] or ref.ref_id)
            if author_lbl.lower() in existing_lit_keys:
                continue
            project.literature_synthesis.append(
                LiteratureSynthesisItem(
                    authors_country_year=author_lbl,
                    study_design="Étude publiée",
                    population=seg.epidemiology_scope or "À préciser",
                    main_results=seg.summary or seg.key_quote_or_data,
                    limits="",
                )
            )
            existing_lit_keys.add(author_lbl.lower())
            added_lit += 1

    # 4. Budget items from chap3_budget (Markdown tables, custom_tables, or bulleted FCFA lines)
    from app.services.budget_engine import sync_budget_from_section

    added_budget = sync_budget_from_section(project, overwrite=False)

    force_abbr = (
        "abbreviations"
        if added_abbr > 0
        and project.sections.get("abbreviations")
        and project.sections["abbreviations"].status != StepStatus.VALIDATED
        else None
    )
    sync_special_sections(project, force_section_id=force_abbr)

    return {
        "added_abbreviations": added_abbr,
        "added_variables": added_vars,
        "added_literature_rows": added_lit,
        "added_budget_items": added_budget,
    }


def _extract_cited_numbers(text: str) -> Set[int]:
    """Parse Vancouver inline citation numbers like [1], [1, 2], [3-5]."""
    nums: Set[int] = set()
    for grp in re.findall(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]", text or ""):
        for part in re.split(r"\s*,\s*", grp):
            if "-" in part or "–" in part:
                bounds = re.split(r"\s*[-–]\s*", part)
                if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
                    a, b = int(bounds[0]), int(bounds[1])
                    if 1 <= a <= b <= 500:
                        nums.update(range(a, b + 1))
            elif part.strip().isdigit():
                nums.add(int(part.strip()))
    return nums


def run_global_protocol_audit(project: ProtocolProject) -> Dict[str, object]:
    """Run deterministic cross-section FMSB consistency checks prior to print export."""
    total_sections = len(FMSB_SECTIONS)
    validated_count = 0
    drafted_count = 0
    missing_required: List[str] = []
    revision_needed: List[str] = []

    for spec in FMSB_SECTIONS:
        sec = project.sections.get(spec.id)
        has_text = bool(_section_plain_text(project, spec.id))
        has_tables = bool(sec and getattr(sec, "custom_tables", None))
        status = sec.status if sec else StepStatus.PENDING
        if status == StepStatus.VALIDATED:
            validated_count += 1
        elif status == StepStatus.IN_PROGRESS or has_text or has_tables:
            drafted_count += 1
        if status == StepStatus.NEEDS_REVISION:
            revision_needed.append(spec.title)
        elif spec.constraints.required and spec.id != "cover_page" and not has_text and not has_tables:
            missing_required.append(spec.title)

    checks: List[Dict[str, object]] = []

    # 1. Exhaustivité du canevas (24 étapes)
    if missing_required or revision_needed:
        st_exh = "error" if missing_required else "warning"
        det_exh = []
        if missing_required:
            det_exh.append(
                f"Sections obligatoires vides ({len(missing_required)}) : "
                + ", ".join(missing_required[:5])
                + ("..." if len(missing_required) > 5 else "")
            )
        if revision_needed:
            det_exh.append("Sections marquées à réviser : " + ", ".join(revision_needed[:5]))
        sum_exh = f"{validated_count}/{total_sections} validées ({len(missing_required)} vide(s))."
    else:
        st_exh = "ok"
        det_exh = [f"Toutes les sections obligatoires sont renseignées ({validated_count}/{total_sections} validées)."]
        sum_exh = f"Couverture complète ({validated_count}/{total_sections} sections validées)."
    checks.append(
        {
            "id": "exhaustivite",
            "label": "Exhaustivité du canevas FMSB (24 étapes)",
            "status": st_exh,
            "summary": sum_exh,
            "details": det_exh,
        }
    )

    # 2. Alignement Objectifs <-> Variables <-> Analyse statistique
    obj_txt = _section_plain_text(project, "chap1_objectifs")
    var_txt = _section_plain_text(project, "chap1_variables")
    ana_txt = _section_plain_text(project, "chap3_analyse_donnees")
    var_sec = project.sections.get("chap1_variables")
    has_vars = bool(project.variables) or bool(var_txt) or bool(var_sec and var_sec.custom_tables)
    missing_align: List[str] = []
    if not obj_txt:
        missing_align.append("Objectifs spécifiques (Chapitre I) non rédigés")
    if not has_vars:
        missing_align.append("Variables opérationnelles (Chapitre I / Tableau structuré) absentes")
    if not ana_txt:
        missing_align.append("Plan d'analyse statistique (Chapitre III) non rédigé")
    if missing_align:
        st_align = "error" if len(missing_align) >= 2 else "warning"
        sum_align = f"{len(missing_align)} maillon(s) manquant(s) dans la chaîne Objectifs → Variables → Analyse."
        det_align = missing_align
    else:
        st_align = "ok"
        sum_align = f"Chaîne alignée ({len(project.variables)} variable(s) structurée(s))."
        det_align = ["Objectifs, variables et plan d'analyse statistique sont présents et reliés."]
    checks.append(
        {
            "id": "objectifs_variables",
            "label": "Cohérence Objectifs ↔ Variables ↔ Plan d'analyse",
            "status": st_align,
            "summary": sum_align,
            "details": det_align,
        }
    )

    # 3. Échantillon, site, période & critères
    pop_txt = _section_plain_text(project, "chap3_population_echantillon").lower()
    site_txt = _section_plain_text(project, "chap3_type_site_duree").lower()
    det_ech: List[str] = []
    if not pop_txt:
        det_ech.append("Section Population & calcul de taille d'échantillon (Chapitre III) vide.")
    else:
        if not re.search(r"\d+", pop_txt):
            det_ech.append("Aucun effectif chiffré (n) ou seuil numérique détecté dans Population & échantillon.")
        if not any(k in pop_txt for k in ("inclusion", "exclusion", "sélection", "éligib")):
            det_ech.append("Critères d'inclusion / exclusion peu explicites dans Chapitre III.")
    if not site_txt:
        det_ech.append("Type d'étude, site et période (Chapitre III) non renseignés.")
    if not (project.cover.host_institutions or []):
        det_ech.append("Aucune structure d'accueil renseignée sur la Page de garde.")

    if not pop_txt and not site_txt:
        st_ech = "error"
    elif det_ech:
        st_ech = "warning"
    else:
        st_ech = "ok"
    checks.append(
        {
            "id": "echantillon_analyse",
            "label": "Population, taille d'échantillon, site & période",
            "status": st_ech,
            "summary": "Critères méthodologiques complets." if st_ech == "ok" else f"{len(det_ech)} point(s) à vérifier.",
            "details": det_ech or ["Effectifs, critères d'éligibilité et sites d'étude sont présents."],
        }
    )

    # 4. Intégrité des citations Vancouver [1..N]
    body_text_parts: List[str] = []
    for spec in FMSB_SECTIONS:
        if spec.chapter_id in ("preliminaires", "references", "annexes"):
            continue
        t = _section_plain_text(project, spec.id)
        if t:
            body_text_parts.append(t)
    full_body = "\n".join(body_text_parts)
    cited_nums = _extract_cited_numbers(full_body)
    ref_sec_text = _section_plain_text(project, "references")
    ref_lines_count = len(
        [ln for ln in ref_sec_text.splitlines() if re.match(r"^\s*\d+\.\s+", ln)]
    )
    total_refs = max(len(project.references), ref_lines_count)
    det_cit: List[str] = []
    out_of_range = sorted(n for n in cited_nums if n > total_refs)
    uncited = sorted(n for n in range(1, total_refs + 1) if n not in cited_nums) if total_refs > 0 else []
    if total_refs == 0 and body_text_parts:
        det_cit.append("Aucune référence Vancouver enregistrée pour le protocole.")
    if out_of_range:
        det_cit.append(
            "Appels bibliographiques hors liste (numéro supérieur au nombre de références) : "
            + ", ".join(f"[{n}]" for n in out_of_range[:10])
        )
    if uncited:
        det_cit.append(
            f"{len(uncited)} référence(s) non citée(s) dans le texte : "
            + ", ".join(f"[{n}]" for n in uncited[:8])
            + ("..." if len(uncited) > 8 else "")
        )
    if total_refs == 0 and cited_nums:
        st_cit = "error"
    elif out_of_range:
        st_cit = "error"
    elif det_cit:
        st_cit = "warning"
    else:
        st_cit = "ok"
    checks.append(
        {
            "id": "citations_vancouver",
            "label": "Intégrité des citations Vancouver [1..N]",
            "status": st_cit,
            "summary": (
                f"{len(cited_nums)} appel(s) vérifié(s) sur {total_refs} référence(s)."
                if st_cit == "ok"
                else f"{len(out_of_range)} appel(s) orphelin(s), {len(uncited)} référence(s) non citée(s)."
            ),
            "details": det_cit or ["Tous les appels [n] correspondent aux références Vancouver."],
        }
    )

    # 5. Abréviations utilisées vs Tableau des abréviations
    defined_acronyms = _collect_defined_acronyms(project)
    used_acronyms = set(re.findall(r"\b([A-Z][A-Z0-9-]{1,7})\b", full_body))
    missing_acronyms = sorted(
        tok
        for tok in used_acronyms
        if tok not in defined_acronyms and tok not in _STOP_UPPER_TOKENS and not tok.isdigit()
    )
    if missing_acronyms:
        st_abbr = "warning"
        sum_abbr = f"{len(missing_acronyms)} sigle(s) détecté(s) absent(s) de la liste des abréviations."
        det_abbr = [
            "Sigles manquants suggérés pour synchronisation : " + ", ".join(missing_acronyms[:15])
        ]
    else:
        st_abbr = "ok"
        sum_abbr = f"Tous les sigles détectés figurent dans la table ({len(defined_acronyms)} défini(s))."
        det_abbr = ["Aucun sigle orphelin détecté dans les chapitres rédigés."]
    checks.append(
        {
            "id": "abreviations",
            "label": "Cohérence des sigles & tableau des abréviations",
            "status": st_abbr,
            "summary": sum_abbr,
            "details": det_abbr,
        }
    )

    # 6. Chronogramme, budget & pack d'annexes FMSB
    det_ann: List[str] = []
    if not project.timeline_grid.rows:
        det_ann.append("Chronogramme des activités (Gantt) vide.")
    if not project.budget:
        det_ann.append("Tableau du budget prévisionnel vide.")
    if not project.variables:
        det_ann.append("Aucune variable structurée pour générer automatiquement la Fiche de collecte (Annexe 1).")
    cfg = project.annexes_config
    if not (cfg.include_info_notice and cfg.include_consent_form and cfg.include_ethics_clearance_request):
        det_ann.append("Certaines annexes éthiques recommandées FMSB sont désactivées.")
    st_ann = "ok" if not det_ann else ("warning" if len(det_ann) <= 2 else "error")
    checks.append(
        {
            "id": "annexes_collecte",
            "label": "Chronogramme, budget & pack d'annexes FMSB",
            "status": st_ann,
            "summary": (
                "Annexes, Gantt, budget et fiche de collecte prêts."
                if st_ann == "ok"
                else f"{len(det_ann)} élément(s) à compléter."
            ),
            "details": det_ann or ["Chronogramme, budget et annexes réglementaires complets."],
        }
    )

    weight_map = {"ok": 100.0, "warning": 60.0, "error": 0.0}
    score_percent = round(sum(weight_map[str(c["status"])] for c in checks) / len(checks))
    has_errors = any(c["status"] == "error" for c in checks)
    ready_for_print = not has_errors and score_percent >= 80

    return {
        "ready_for_print": ready_for_print,
        "score_percent": int(score_percent),
        "validated_sections_count": validated_count,
        "drafted_sections_count": drafted_count,
        "total_sections_count": total_sections,
        "checks": checks,
        "missing_acronyms": missing_acronyms,
    }
