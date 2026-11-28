from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.data.i18n import get_language_prompt_directive, get_localized_section_spec, normalize_lang
from app.schemas.protocol import CoherenceAlert, ProtocolProject, StepStatus
from app.services.pipeline_tracker import emit_pipeline_step
from app.services.rich_formatter import markdown_to_clean_html


_ANALYTICAL_OBJECTIVE_RE = re.compile(
    r"\b("
    r"facteurs?\s+associ[ée]s?|"
    r"d[ée]terminants?|"
    r"comparer|comparaison|compare|"
    r"association|associ[ée]e?\s+[àa]|associated\s+factors?|"
    r"corr[ée]l(?:er|ation)|correlat\w*|"
    r"pr[ée]dicteurs?|predictors?|"
    r"facteurs?\s+de\s+risque|risk\s+factors?|"
    r"risque\s+relatif|relative\s+risk|"
    r"odds\s+ratio|"
    r"impact|influence\s+(?:de|of)|"
    r"identifier\s+les\s+(?:causes|facteurs)|identify\s+(?:determinants|factors|predictors)"
    r")\b",
    re.IGNORECASE,
)

_DESCRIPTIVE_OBJECTIVE_RE = re.compile(
    r"\b(d[ée]crire|describe|pr[ée]valence|prevalence|fr[ée]quence|frequency|r[ée]partition|distribution|profil\s+(?:clinique|sociod[ée]mographique|[ée]pid[ée]miologique)|(?:clinical|sociodemographic)\s+profile)\b",
    re.IGNORECASE,
)

_ANALYTICAL_DESIGN_RE = re.compile(
    r"\b(analytique|analytical|cas[\s\-–]t[ée]moins|case[\s\-–]control|cohorte?|comparati(?:f|ve)|essai\s+clinique|clinical\s+trial|exp[ée]rimental|quasi[\s\-]exp[ée]rimental|transversale\s+[àa]\s+vis[ée]e\s+analytique|analytical\s+cross[\s\-]sectional)\b",
    re.IGNORECASE,
)

_DESCRIPTIVE_DESIGN_RE = re.compile(
    r"\b(descripti(?:f|ve))\b",
    re.IGNORECASE,
)

_NO_HYPOTHESIS_RE = re.compile(
    r"\b(aucune\s+hypoth[èe]se|pas\s+d['’]hypoth[èe]se|sans\s+hypoth[èe]se|purement\s+descriptive\s+ne\s+n[ée]cessitant\s+pas\s+d['’]hypoth[èe]se|no\s+(?:analytical\s+)?hypothesis|without\s+hypothesis)\b",
    re.IGNORECASE,
)

_CASE_CONTROL_DESIGN_RE = re.compile(r"\b(cas[\s\-–]t[ée]moins|case[\s\-–]control)\b", re.IGNORECASE)
_CROSS_SECTIONAL_SAMPLE_RE = re.compile(r"\b(lorentz|cochran|schwartz|z\^?2\s*[×\*·]?\s*p)\b", re.IGNORECASE)
_CASE_CONTROL_SAMPLE_RE = re.compile(r"\b(kelsey|schlesselman|fleiss|appariement|matching|ratio\s+(?:cas|cases)|t[ée]moins?\s+pour\s+(?:chaque|un)\s+cas|controls?\s+per\s+case)\b", re.IGNORECASE)

_ANALYTICAL_STATS_RE = re.compile(
    r"\b(chi[\s\-]?2|chi[\s\-]?deux|chi[\s\-]?square|χ²|fisher|odds\s+ratio|\bor\b|r[ée]gression(?:\s+logistique)?|logistic\s+regression|student|mann[\s\-]?whitney|anova|kruskal|pearson|spearman|multivari[ée]e?|multivariate)\b",
    re.IGNORECASE,
)

_MINORS_RE = re.compile(
    r"\b(mineurs?|minors?|enfants?|children|adolescents?|nourrissons?|infants?|nouveau[\s\-]?n[ée]s?|neonates?|p[ée]diatrique?s?|pediatric)\b",
    re.IGNORECASE,
)
_ADULT_EXCLUSION_RE = re.compile(
    r"\b(exclusion\s+des\s+mineurs|excluding\s+minors|[âa]ge\s*(?:sup[ée]rieur\s+ou\s+[ée]gal|>=|≥)\s*18|18\s+(?:ans\s+et\s+plus|years\s+and\s+older)|majeurs?\s+uniquement|adults?\s+only)\b",
    re.IGNORECASE,
)
_PARENTAL_CONSENT_RE = re.compile(
    r"\b(parents?|parental[e]?|tuteurs?\s+l[ée]ga(?:l|ux)|legal\s+guardians?|repr[ée]sentants?\s+l[ée]ga(?:l|ux)|assentiment|assent)\b",
    re.IGNORECASE,
)

_STUDY_DURATION_MONTHS_RE = re.compile(
    r"(?:dur[ée]e(?:\s+totale|\s+globale)?|total\s+duration|duration)[^\n.]{0,35}?(\d{1,2})\s*(?:mois|months?)\b",
    re.IGNORECASE,
)


def _section_title(section_id: str, lang: str = "fr") -> str:
    try:
        return get_localized_section_spec(section_id, lang).title
    except ValueError:
        return section_id


def _combined_section_signal(project: ProtocolProject, memory_map: Dict[str, str], section_id: str) -> str:
    sec = project.sections.get(section_id)
    text = (sec.content_markdown or "").strip() if sec else ""
    mem = (memory_map.get(section_id) or "").strip()
    return f"{text}\n{mem}".strip()


def _make_alert_id(axis: str, source_id: str, target_id: str) -> str:
    digest = hashlib.sha1(f"{axis}:{source_id}:{target_id}".encode("utf-8")).hexdigest()[:8]
    return f"coh_{axis}_{digest}"


def evaluate_project_coherence(
    project: ProtocolProject,
    changed_section_id: Optional[str] = None,
) -> List[CoherenceAlert]:
    """Inspect working memory and drafted sections to detect cross-section methodological conflicts."""
    from app.services.working_memory import load_working_memory_map

    memory_map = load_working_memory_map(project)
    alerts: List[CoherenceAlert] = []

    obj_sec = project.sections.get("chap1_objectifs")
    obj_raw = (obj_sec.content_markdown or "").strip() if obj_sec else ""
    obj_sig = _combined_section_signal(project, memory_map, "chap1_objectifs")

    design_sec = project.sections.get("chap3_type_site_duree")
    design_raw = (design_sec.content_markdown or "").strip() if design_sec else ""
    design_sig = _combined_section_signal(project, memory_map, "chap3_type_site_duree")

    hyp_sec = project.sections.get("chap1_hypotheses")
    hyp_raw = (hyp_sec.content_markdown or "").strip() if hyp_sec else ""

    sample_sec = project.sections.get("chap3_population_echantillon")
    sample_raw = (sample_sec.content_markdown or "").strip() if sample_sec else ""

    stats_sec = project.sections.get("chap3_analyse_donnees")
    stats_raw = (stats_sec.content_markdown or "").strip() if stats_sec else ""

    ethics_sec = project.sections.get("chap3_ethique")
    ethics_raw = (ethics_sec.content_markdown or "").strip() if ethics_sec else ""

    chrono_sec = project.sections.get("chap3_chronogramme")
    chrono_raw = (chrono_sec.content_markdown or "").strip() if chrono_sec else ""

    has_analytical_obj = bool(obj_raw and _ANALYTICAL_OBJECTIVE_RE.search(obj_sig))
    has_descriptive_obj = bool(obj_raw and _DESCRIPTIVE_OBJECTIVE_RE.search(obj_sig))

    design_is_analytical = bool(_ANALYTICAL_DESIGN_RE.search(design_sig or project.study_type or ""))
    design_is_descriptive_only = bool(
        design_raw
        and _DESCRIPTIVE_DESIGN_RE.search(design_raw)
        and not _ANALYTICAL_DESIGN_RE.search(design_raw)
    )

    # Axis 1a: Objectifs analytiques <-> Type d'étude purement descriptif
    if has_analytical_obj and design_is_descriptive_only:
        alerts.append(
            CoherenceAlert(
                alert_id=_make_alert_id("objectives_vs_study_type", "chap1_objectifs", "chap3_type_site_duree"),
                axis="objectives_vs_study_type",
                source_section_id="chap1_objectifs",
                source_section_title=_section_title("chap1_objectifs"),
                target_section_id="chap3_type_site_duree",
                target_section_title=_section_title("chap3_type_site_duree"),
                title="Incohérence détectée : Objectifs analytiques vs Type d'étude descriptif",
                message=(
                    "Vos objectifs actuels intègrent une dimension analytique (facteurs associés, déterminants ou comparaison), "
                    "alors que la section « Type d'étude, Site et Durée » décrit actuellement une étude uniquement descriptive. "
                    "Voulez-vous reformuler le type d'étude en « étude transversale descriptive et analytique » ?"
                ),
                suggested_instruction=(
                    "Reformule cette section pour que le type d'étude devienne une étude transversale descriptive et analytique "
                    "(ou cohorte/cas-témoins selon le contexte), en cohérence stricte avec les nouveaux objectifs de recherche, "
                    "tout en conservant intacts les sites hospitaliers, la durée et la période de collecte existants."
                ),
                suggested_study_type="Étude transversale descriptive et analytique",
            )
        )
    # Axis 1b: Objectifs uniquement descriptifs <-> Type d'étude analytique
    elif obj_raw and len(obj_raw.split()) >= 12 and has_descriptive_obj and not has_analytical_obj and design_raw and _ANALYTICAL_DESIGN_RE.search(design_raw):
        alerts.append(
            CoherenceAlert(
                alert_id=_make_alert_id("objectives_vs_study_type_desc", "chap1_objectifs", "chap3_type_site_duree"),
                axis="objectives_vs_study_type_desc",
                source_section_id="chap1_objectifs",
                source_section_title=_section_title("chap1_objectifs"),
                target_section_id="chap3_type_site_duree",
                target_section_title=_section_title("chap3_type_site_duree"),
                title="Décalage méthodologique : Objectifs descriptifs vs Devis analytique",
                message=(
                    "Vos objectifs actuels portent exclusivement sur une description (prévalence, fréquence, profil), "
                    "mais le type d'étude mentionne un devis analytique ou comparatif. Souhaitez-vous aligner le type d'étude sur une visée descriptive ?"
                ),
                suggested_instruction=(
                    "Reformule le type d'étude en « étude transversale descriptive » alignée sur les objectifs descriptifs actuels, "
                    "en conservant les sites hospitaliers et la période d'étude."
                ),
                suggested_study_type="Étude transversale descriptive",
            )
        )

    # Axis 1c: Objectifs analytiques <-> Hypothèses niant la nécessité d'une hypothèse
    if has_analytical_obj and hyp_raw and _NO_HYPOTHESIS_RE.search(hyp_raw):
        alerts.append(
            CoherenceAlert(
                alert_id=_make_alert_id("objectives_vs_hypotheses", "chap1_objectifs", "chap1_hypotheses"),
                axis="objectives_vs_hypotheses",
                source_section_id="chap1_objectifs",
                source_section_title=_section_title("chap1_objectifs"),
                target_section_id="chap1_hypotheses",
                target_section_title=_section_title("chap1_hypotheses"),
                title="Hypothèse de recherche à actualiser",
                message=(
                    "Vos objectifs comportent désormais la recherche de facteurs associés ou de comparaisons, "
                    "mais votre section « Hypothèse(s) » mentionne l'absence d'hypothèse analytique. "
                    "Souhaitez-vous formuler une hypothèse principale à la forme affirmative ?"
                ),
                suggested_instruction=(
                    "Rédige une hypothèse principale affirmative et vérifiable (ainsi que des hypothèses secondaires) "
                    "en cohérence directe avec les nouveaux objectifs analytiques."
                ),
            )
        )

    # Axis 2: Type d'étude Cas-Témoins <-> Formule transversale de Lorentz/Cochran
    if design_raw and _CASE_CONTROL_DESIGN_RE.search(design_raw) and sample_raw and _CROSS_SECTIONAL_SAMPLE_RE.search(sample_raw) and not _CASE_CONTROL_SAMPLE_RE.search(sample_raw):
        alerts.append(
            CoherenceAlert(
                alert_id=_make_alert_id("study_type_vs_sample_size", "chap3_type_site_duree", "chap3_population_echantillon"),
                axis="study_type_vs_sample_size",
                source_section_id="chap3_type_site_duree",
                source_section_title=_section_title("chap3_type_site_duree"),
                target_section_id="chap3_population_echantillon",
                target_section_title=_section_title("chap3_population_echantillon"),
                title="Formule de taille d'échantillon incompatible avec le devis Cas-Témoins",
                message=(
                    "Votre type d'étude est défini comme une étude cas-témoins, mais le calcul d'échantillon repose sur une formule "
                    "transversale de prévalence (Cochran/Lorentz). Voulez-vous adapter le calcul à une formule cas-témoins (Kelsey / Schlesselman) ?"
                ),
                suggested_instruction=(
                    "Adapte le paragraphe de calcul de la taille d'échantillon pour une étude cas-témoins (formule de Schlesselman / Kelsey "
                    "avec ratio cas/témoins, puissance 80 % et α = 5 %), tout en conservant la population et le site."
                ),
            )
        )

    # Axis 3: Objectifs analytiques <-> Plan d'analyse sans tests bivariés/multivariés
    if has_analytical_obj and stats_raw and len(stats_raw.split()) >= 25 and not _ANALYTICAL_STATS_RE.search(stats_raw):
        alerts.append(
            CoherenceAlert(
                alert_id=_make_alert_id("objectives_vs_data_analysis", "chap1_objectifs", "chap3_analyse_donnees"),
                axis="objectives_vs_data_analysis",
                source_section_id="chap1_objectifs",
                source_section_title=_section_title("chap1_objectifs"),
                target_section_id="chap3_analyse_donnees",
                target_section_title=_section_title("chap3_analyse_donnees"),
                title="Tests analytiques manquants dans le plan d'analyse statistique",
                message=(
                    "Vos objectifs visent l'identification de facteurs associés ou comparaisons, mais la section « Analyse des données » "
                    "ne détaille pas encore les tests comparatifs (Chi-2 / Fisher, Odds Ratio, régression logistique)."
                ),
                suggested_instruction=(
                    "Complète le plan d'analyse des données en ajoutant l'analyse bivariée (test du Chi-2 ou test exact de Fisher, "
                    "Student/Mann-Whitney, calcul des Odds Ratio avec IC à 95 %) et l'analyse multivariée par régression logistique "
                    "pour répondre aux objectifs analytiques (seuil p < 0,05)."
                ),
            )
        )

    # Axis 4: Population incluant des mineurs <-> Éthique sans consentement parental/assentiment
    if (
        sample_raw
        and _MINORS_RE.search(sample_raw)
        and not _ADULT_EXCLUSION_RE.search(sample_raw)
        and ethics_raw
        and len(ethics_raw.split()) >= 25
        and not _PARENTAL_CONSENT_RE.search(ethics_raw)
    ):
        alerts.append(
            CoherenceAlert(
                alert_id=_make_alert_id("population_vs_ethics", "chap3_population_echantillon", "chap3_ethique"),
                axis="population_vs_ethics",
                source_section_id="chap3_population_echantillon",
                source_section_title=_section_title("chap3_population_echantillon"),
                target_section_id="chap3_ethique",
                target_section_title=_section_title("chap3_ethique"),
                title="Consentement parental et assentiment requis dans l'Éthique",
                message=(
                    "Votre population d'étude mentionne des mineurs ou sujets pédiatriques, mais les considérations éthiques "
                    "n'explicitent pas encore le consentement éclairé des parents/tuteurs légaux ni l'assentiment."
                ),
                suggested_instruction=(
                    "Intègre explicitement dans les considérations éthiques le recueil du consentement éclairé écrit des parents ou tuteurs légaux "
                    "ainsi que l'assentiment adapté des mineurs/adolescents en âge de comprendre."
                ),
            )
        )

    # Axis 5: Durée globale explicite contradictoire entre III.1-3 et Chronogramme
    if design_raw and chrono_raw and len(chrono_raw.split()) >= 15:
        m_design = _STUDY_DURATION_MONTHS_RE.search(design_raw)
        m_chrono = _STUDY_DURATION_MONTHS_RE.search(chrono_raw)
        if m_design and m_chrono and m_design.group(1) != m_chrono.group(1):
            alerts.append(
                CoherenceAlert(
                    alert_id=_make_alert_id("duration_vs_timeline", "chap3_type_site_duree", "chap3_chronogramme"),
                    axis="duration_vs_timeline",
                    source_section_id="chap3_type_site_duree",
                    source_section_title=_section_title("chap3_type_site_duree"),
                    target_section_id="chap3_chronogramme",
                    target_section_title=_section_title("chap3_chronogramme"),
                    title="Durée d'étude divergente avec le Chronogramme",
                    message=(
                        f"La section Type d'étude/Durée indique {m_design.group(1)} mois alors que le texte du Chronogramme "
                        f"mentionne {m_chrono.group(1)} mois."
                    ),
                    suggested_instruction=(
                        f"Harmonise le texte du chronogramme des activités avec la durée totale de {m_design.group(1)} mois "
                        "définie dans la section Type d'étude, Site et Durée."
                    ),
                )
            )

    # Prioritize alerts whose source is the just-edited section
    if changed_section_id:
        alerts.sort(key=lambda a: (0 if a.source_section_id == changed_section_id else 1))

    project.coherence_alerts = alerts
    return alerts


def _apply_deterministic_harmonization(
    project: ProtocolProject,
    section_id: str,
    alert: CoherenceAlert,
) -> str:
    """Safely rewrite conflicting sentences in `section_id` without altering valid local facts."""
    sec = project.sections.get(section_id)
    text = (sec.content_markdown or "").strip() if sec else ""

    if alert.axis == "objectives_vs_study_type":
        updated = re.sub(
            r"\b([ée]tude\s+transversale)\s+(?:purement\s+)?descripti(?:ve|f)\b",
            r"\1 descriptive et analytique",
            text,
            flags=re.IGNORECASE,
        )
        updated = re.sub(
            r"\b([ée]tude)\s+purement\s+descripti(?:ve|f)\b",
            r"\1 descriptive et analytique",
            updated,
            flags=re.IGNORECASE,
        )
        if updated == text:
            updated = re.sub(
                r"\bdescripti(?:ve|f)\b",
                "descriptive et analytique",
                text,
                count=1,
                flags=re.IGNORECASE,
            )
        if "facteurs" not in updated.lower() and "analytique" in updated.lower():
            updated = (
                f"{updated.rstrip('.')}. Ce devis mixte permettra à la fois de décrire le profil clinique des participants "
                "et d'identifier les facteurs associés conformément aux objectifs spécifiques du protocole."
            )
        return updated

    if alert.axis == "objectives_vs_study_type_desc":
        updated = re.sub(
            r"\b([ée]tude\s+transversale)\s+(?:descriptive\s+et\s+)?analytique\b",
            r"\1 descriptive",
            text,
            flags=re.IGNORECASE,
        )
        if updated == text:
            updated = re.sub(
                r"\b(?:à\s+visée\s+)?analytique\b",
                "descriptive",
                text,
                count=1,
                flags=re.IGNORECASE,
            )
        return updated

    if alert.axis == "objectives_vs_hypotheses":
        subj = (project.cover.title or project.name or "les facteurs cliniques et sociodémographiques étudiés").strip()
        return (
            f"Hypothèse principale : Des facteurs sociodémographiques et cliniques spécifiques sont statistiquement "
            f"associés à l'issue principale dans le cadre de « {subj} ».\n\n"
            "Hypothèses secondaires : La sévérité du tableau clinique et certaines comorbidités modulent significativement "
            "les paramètres évalués conformément aux objectifs spécifiques."
        )

    if alert.axis == "objectives_vs_data_analysis":
        addition = (
            "\n\nL'analyse bivariée reposera sur le test du Chi-2 ou le test exact de Fisher pour les variables qualitatives, "
            "et sur le test de Student ou de Mann-Whitney pour les variables quantitatives. Les forces d'association seront exprimées "
            "en Odds Ratio (OR) avec leur intervalle de confiance à 95 %, complétés par une régression logistique multivariée afin de "
            "contrôler les facteurs de confusion (seuil de significativité fixé à p < 0,05)."
        )
        return f"{text}{addition}".strip()

    if alert.axis == "population_vs_ethics":
        addition = (
            "\n\nPour les participants mineurs inclus dans l'étude, le consentement éclairé écrit des parents ou tuteurs légaux "
            "sera systématiquement recueilli au préalable, accompagné d'un assentiment libre et éclairé de l'enfant ou de l'adolescent."
        )
        return f"{text}{addition}".strip()

    if alert.axis == "study_type_vs_sample_size":
        addition = (
            "\n\nCompte tenu du devis cas-témoins retenu, l'effectif minimal sera ajusté selon la formule comparative de Schlesselman/Kelsey "
            "(puissance statistique de 80 %, risque α = 5 % et ratio cas/témoins adapté)."
        )
        return f"{text}{addition}".strip()

    if alert.axis == "duration_vs_timeline":
        m_design = _STUDY_DURATION_MONTHS_RE.search(
            (project.sections.get("chap3_type_site_duree").content_markdown if project.sections.get("chap3_type_site_duree") else "") or ""
        )
        if m_design:
            return _STUDY_DURATION_MONTHS_RE.sub(f"durée totale de {m_design.group(1)} mois", text)

    return text


def harmonize_section_with_working_memory(
    project: ProtocolProject,
    section_id: str,
    alert_id: Optional[str] = None,
    use_ai: bool = True,
) -> Dict[str, Any]:
    """Reformulate `section_id` to resolve a cross-section methodological conflict."""
    from pydantic import BaseModel, Field

    from app.services.drafting import deterministic_section_checks
    from app.services.gemini_client import gemini_router
    from app.services.humanizer import sanitize_llm_typography

    lang = normalize_lang(getattr(project, "language", "fr"))
    emit_pipeline_step(
        "coherence_check",
        "Vérification des alertes de cohérence inter-sections...",
        "Checking cross-section methodological coherence...",
        progress_pct=25,
    )

    class _HarmonizedSectionOutput(BaseModel):
        content_markdown: str = Field(
            description="Harmonized protocol text keeping 100% of numbers, sites, and [n] citations."
        )

    sec = project.sections.get(section_id)
    if sec is None:
        raise ValueError(f"Section inconnue : {section_id}")

    if not project.coherence_alerts:
        evaluate_project_coherence(project)

    alert: Optional[CoherenceAlert] = None
    for a in project.coherence_alerts:
        if alert_id and a.alert_id == alert_id and a.target_section_id == section_id:
            alert = a
            break
    if alert is None:
        alert = next((a for a in project.coherence_alerts if a.target_section_id == section_id), None)

    if alert is None:
        return {
            "harmonized": False,
            "section_id": section_id,
            "model_used": "none",
            "content_markdown": sec.content_markdown,
            "content_html": sec.content_html,
            "study_type": project.study_type,
            "remaining_alerts_count": len(project.coherence_alerts),
            "message": "No active coherence conflict detected for this section." if lang == "en" else "Aucune incohérence active détectée pour cette section.",
        }

    previous_text = (sec.content_markdown or "").strip()
    if alert.suggested_study_type:
        project.study_type = alert.suggested_study_type

    det_md = _apply_deterministic_harmonization(project, section_id, alert)
    final_md = det_md
    model_used = "coherence-harmonizer-deterministe"

    if use_ai:
        emit_pipeline_step(
            "llm_harmonize",
            "Reformulation de la section pour rétablir la cohérence...",
            "Harmonizing section text with updated protocol decisions...",
            progress_pct=65,
        )
        source_sec = project.sections.get(alert.source_section_id)
        source_excerpt = (source_sec.content_markdown if source_sec else "")[:1500]
        orig_nums = re.findall(r"\b\d+(?:[.,]\d+)?\b", previous_text)
        orig_cites = sorted({int(x) for x in re.findall(r"\[(\d+)\]", previous_text)})
        lang_dir = get_language_prompt_directive(lang)

        prompt = f"""
Tu es un directeur de thèse FMSB / CAMES. Reformule de manière chirurgicale la section suivante pour résoudre une incohérence méthodologique détectée via la mémoire de travail.

INCOHÉRENCE DÉTECTÉE : {alert.title}
CONSIGNE D'HARMONISATION : {alert.suggested_instruction}
NOUVELLE QUALIFICATION RETENUE : {project.study_type or '(inchangée)'}

CONTENU RÉCENT DE LA SECTION SOURCE ({alert.source_section_title}) :
{source_excerpt}

TEXTE ACTUEL DE LA SECTION À REFORMULER :
{previous_text}

RÈGLES STRICTES :
1. Conserve mot pour mot et EN CHIFFRES EXACTS tous les nombres (ex. 6 mois, tailles, âges, pourcentages), tous les noms d'hôpitaux/sites et toutes les citations [n] présentes.
2. Corrige uniquement la cohérence méthodologique vis-à-vis de la section source sans ajouter d'hallucinations ni de clichés IA.
3. {lang_dir}
"""
        try:
            res = gemini_router.generate_structured(
                role="drafting",
                contents=prompt,
                schema=_HarmonizedSectionOutput,
                temperature=0.15,
            )
            dto: _HarmonizedSectionOutput = res["data"]
            cand_md = (dto.content_markdown or "").strip()
            nums_ok = all(num in cand_md for num in orig_nums)
            cites_ok = all(f"[{c}]" in cand_md for c in orig_cites)
            if cand_md and nums_ok and cites_ok:
                # Verify candidate actually clears this alert
                sec.content_markdown = cand_md
                post_alerts = evaluate_project_coherence(project)
                if not any(a.alert_id == alert.alert_id for a in post_alerts):
                    final_md = cand_md
                    model_used = str(res.get("model_used") or "gemini-harmonizer")
        except Exception:
            pass

    final_md, _ = sanitize_llm_typography(final_md, lang=lang)
    sec.content_markdown = final_md
    sec.content_html = markdown_to_clean_html(final_md)
    sec.audit_notes = deterministic_section_checks(project, section_id, final_md)
    if sec.status == StepStatus.NEEDS_REVISION and len(sec.audit_notes) == 0:
        sec.status = StepStatus.IN_PROGRESS

    try:
        from app.services.working_memory import (
            load_working_memory_map,
            save_working_memory_file,
        )

        mmap = load_working_memory_map(project)
        mmap[section_id] = re.sub(r"\s+", " ", final_md)[:320].strip()
        save_working_memory_file(project, mmap)
    except Exception:
        pass

    remaining = evaluate_project_coherence(project, changed_section_id=section_id)
    return {
        "harmonized": True,
        "section_id": section_id,
        "resolved_axis": alert.axis,
        "model_used": model_used,
        "content_markdown": sec.content_markdown,
        "content_html": sec.content_html,
        "study_type": project.study_type,
        "remaining_alerts_count": len(remaining),
        "message": (
            f"Section harmonisée avec « {alert.source_section_title} »"
            + (f" (type d'étude actualisé : {project.study_type})." if alert.suggested_study_type else ".")
        ),
    }


def extract_canonical_protocol_ledger(project: ProtocolProject) -> Dict[str, Any]:
    """Build a deterministic ledger of locked canonical protocol facts across sections
    so Module 1 (plan generation) and background coherence checks always see one source of truth.
    """
    lang = normalize_lang(getattr(project, "language", "fr"))
    title = (getattr(project.cover, "title", "") or project.name or "").strip()
    study_type = (project.study_type or "").strip()

    # Extract key facts from Methodologie & Objectifs
    type_cadre = (project.sections.get("chap3_type_site_duree").content_markdown if project.sections.get("chap3_type_site_duree") else "") or ""
    pop_txt = (project.sections.get("chap3_population_echantillon").content_markdown if project.sections.get("chap3_population_echantillon") else "") or ""
    obj_txt = (project.sections.get("chap1_objectifs").content_markdown if project.sections.get("chap1_objectifs") else "") or ""
    hyp_txt = (project.sections.get("chap1_hypotheses").content_markdown if project.sections.get("chap1_hypotheses") else "") or ""

    sample_match = re.search(
        r"(?:taille(?:\s+(?:théorique\s+)?minimale)?\s+de\s+l['’]?échantillon|effectif\s+(?:théorique\s+)?minimal|\bn\s*=)\s*(?:est\s+)?(?:d['’]environ\s+|de\s+)?(\d{2,5})",
        f"{type_cadre}\n{pop_txt}",
        re.IGNORECASE,
    )
    duration_match = re.search(
        r"(\d+\s*(?:mois|semaines?|ans?|months?|weeks?|years?))",
        type_cadre,
        re.IGNORECASE,
    )

    timeline_periods = list(project.timeline_grid.periods) if getattr(project, "timeline_grid", None) and project.timeline_grid.periods else []
    timeline_span = f"{timeline_periods[0]} → {timeline_periods[-1]} ({len(timeline_periods)} périodes)" if timeline_periods else ""
    budget_total = sum(float(b.quantity) * float(b.unit_cost_fcfa) for b in (project.budget or []))

    return {
        "lang": lang,
        "titre_protocole": title,
        "type_etude": study_type or "(non encore qualifié)",
        "sites_ou_cadre": re.sub(r"\s+", " ", type_cadre)[:260].strip(),
        "duree_detention_ou_etude": duration_match.group(1) if duration_match else "",
        "taille_echantillon_n": sample_match.group(1) if sample_match else "",
        "objectifs_resume": re.sub(r"\s+", " ", obj_txt)[:280].strip(),
        "hypotheses_resume": re.sub(r"\s+", " ", hyp_txt)[:200].strip(),
        "chronogramme": timeline_span,
        "budget_total_fcfa": f"{int(round(budget_total)):,} FCFA".replace(",", " ") if budget_total > 0 else "",
        "variables_count": len(project.variables or []),
        "references_count": len(project.references or []),
    }


def run_background_coherence_check(project_id: str) -> None:
    """Non-blocking background sentinel task triggered after section saves, drafts, edits, and imports.
    Re-evaluates cross-section coherence against the canonical ledger and persists updated alerts.
    """
    try:
        from app.services.storage import project_storage

        project = project_storage.load(project_id)
        if project is None:
            return
        before = [(a.alert_id, a.severity, a.title) for a in (project.coherence_alerts or [])]
        alerts = evaluate_project_coherence(project)
        after = [(a.alert_id, a.severity, a.title) for a in alerts]
        if before != after:
            project_storage.save(project)
    except Exception:
        pass
