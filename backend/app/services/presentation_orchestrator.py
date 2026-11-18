import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from app.schemas.presentation import (
    PresentationDeck,
    QueueDeckItem,
    Slide,
    SlideElement,
)
from app.schemas.protocol import ProtocolProject
from app.services.gemini_client import GeminiRouter

logger = logging.getLogger(__name__)


def _clean_text_for_display(text: str) -> str:
    """Strip Markdown formatting and HTML tags for presentation display."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"[*_#`]", "", clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


class PresentationOrchestrator:
    """Orchestration Engine:
    
    Generates a canonical 17-slide research protocol presentation deck
    adhering strictly to CAMES / FMSB standards, mapped directly to
    the user's high-definition graphic templates and reference skeleton.
    """

    def __init__(self) -> None:
        self.router = GeminiRouter()

    def build_default_deck_from_protocol(self, project: ProtocolProject) -> PresentationDeck:
        """Build the canonical 17-slide presentation deck matching tamplatePowerpointProtocole.pptx."""
        deck = PresentationDeck(
            project_id=project.project_id,
            title=project.cover.title or project.name or "Protocole de Recherche FMSB",
            max_slides=None,
            theme_palette="fmsb_burgundy",
            version="2.1.0",
        )

        slides: List[Slide] = []
        cover = project.cover
        cand_name = cover.author_name or "MBIANDA TCHOUAFA BRICE KEVIN"
        cand_mat = cover.matricule or "2026-FMSB-042"
        dept_name = cover.department_fr or "DÉPARTEMENT DE CHIRURGIE ET SPÉCIALITÉS"
        title_str = cover.title or project.name or "TITRE DU PROTOCOLE DE RECHERCHE"
        dir_name = cover.director or "Professeur / Docteur FMSB"
        co_dir_name = ", ".join(cover.co_directors) if cover.co_directors else "Dr. Co-directeur FMSB"
        acad_year = cover.academic_year or "2025 - 2026"

        # Helper to extract clean bullet points from section markdown
        def _get_section_bullets(sec_id: str, default_bullets: List[str]) -> List[str]:
            sec = project.sections.get(sec_id)
            if not sec or not (sec.content_markdown or sec.content_html):
                return default_bullets
            raw = sec.content_markdown or ""
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            bullets: List[str] = []
            for ln in lines:
                if ln.startswith(("-", "*", "•", "1.", "2.", "3.", "4.", "5.")):
                    clean_l = ln.lstrip("-*•0123456789. ")
                    if 15 <= len(clean_l) <= 160:
                        bullets.append(clean_l)
            if not bullets:
                text_clean = _clean_text_for_display(raw)
                sentences = [s.strip() for s in text_clean.split(".") if len(s.strip()) > 20]
                bullets = sentences[:4]
            return bullets[:4] or default_bullets

        # =========================================================================
        # SLIDE 1: BIENVENUE (Welcome Slide)
        # =========================================================================
        slides.append(
            Slide(
                id="slide-1",
                slide_index=1,
                slide_type="welcome",
                title="BIENVENUE À LA SOUTENANCE DU PROTOCOLE DE RECHERCHE",
                subtitle=f"DE L'IMPÉTRANT(E) : {cand_name.upper()}",
                breadcrumb="ACCUEIL SOUTENANCE",
                background_image_path="tamplates/tamplate image de fond première et deuxième diapositive.png",
                elements=[
                    SlideElement(
                        id="el-1-welcome",
                        element_type="author",
                        content=cand_name.upper(),
                        order=1,
                    )
                ],
                presenter_notes=(
                    f"Monsieur le Président du Jury, Honorables membres du jury, chers maîtres.\n"
                    f"Soyez les bienvenus à la séance de soutenance de protocole de recherche de {cand_name}."
                ),
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 2: PAGE DE GARDE OFFICIELLE (Strict FMSB / CAMES Cover)
        # =========================================================================
        slides.append(
            Slide(
                id="slide-2",
                slide_index=2,
                slide_type="cover",
                title=title_str,
                subtitle=f"Directeur de thèse : {dir_name}\nCo-directeur(s) : {co_dir_name}",
                breadcrumb="PAGE DE GARDE OFFICIELLE",
                background_image_path="tamplates/tamplate image de fond première et deuxième diapositive.png",
                elements=[
                    SlideElement(
                        id="el-2-dept",
                        element_type="department",
                        content=dept_name.upper(),
                        order=1,
                    ),
                    SlideElement(
                        id="el-2-degree",
                        element_type="degree",
                        content="Protocole de Thèse rédigé en vue de l'obtention du Diplôme de Docteur en Médecine",
                        order=2,
                    ),
                    SlideElement(
                        id="el-2-author",
                        element_type="author",
                        content=f"Rédigé et présenté par : {cand_name}\nMatricule : {cand_mat}",
                        order=3,
                    ),
                    SlideElement(
                        id="el-2-directors",
                        element_type="directors",
                        content=f"Directeur : {dir_name}\nCo-directeur(s) : {co_dir_name}",
                        order=4,
                    ),
                    SlideElement(
                        id="el-2-year",
                        element_type="year",
                        content=f"Année académique {acad_year}",
                        order=5,
                    ),
                ],
                presenter_notes=(
                    f"Monsieur le Président du Jury, Honorables membres du jury, chers maîtres.\n"
                    f"J'ai l'honneur de vous présenter ce jour notre protocole de recherche intitulé : « {title_str} ».\n"
                    f"Ce travail s'inscrit au sein du {dept_name} sous la direction éclairée de nos maîtres."
                ),
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 3: PLAN DU DIAPORAMA (6 grands axes - Template Plan)
        # =========================================================================
        plan_bullets = [
            "1. INTRODUCTION & CONTEXTE",
            "2. OBJECTIF GÉNÉRAL",
            "3. OBJECTIFS SPÉCIFIQUES",
            "4. MÉTHODOLOGIE DE L'ÉTUDE",
            "5. CHRONOGRAMME DES ACTIVITÉS",
            "6. BUDGET PRÉVISIONNEL",
        ]
        slides.append(
            Slide(
                id="slide-3",
                slide_index=3,
                slide_type="plan",
                title="PLAN DU DIAPORAMA",
                subtitle="Sommaire du Protocole de Recherche",
                breadcrumb="SOMMAIRE EXÉCUTIF",
                background_image_path="tamplates/tamplate diapositive Plan.png",
                elements=[
                    SlideElement(
                        id=f"el-3-pill-{idx+1}",
                        element_type="bullet_main",
                        content=p_txt,
                        order=idx + 1,
                    )
                    for idx, p_txt in enumerate(plan_bullets)
                ],
                presenter_notes=(
                    "Notre exposé s'articulera autour de six axes majeurs :\n"
                    "1. Introduction & Contexte clinique\n"
                    "2. Objectif Général\n"
                    "3. Objectifs Spécifiques\n"
                    "4. Méthodologie détaillée de l'étude\n"
                    "5. Chronogramme prévisionnel des activités\n"
                    "6. Budgétisation de la recherche."
                ),
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 4: INTRODUCTION (1/2) - Définition & Contexte Clinique
        # =========================================================================
        intro1_defaults = [
            "L'occlusion intestinale aiguë (OIA) sur brides constitue l'une des urgences médico-chirurgicales les plus fréquentes.",
            "Représente 60 à 75 % des occlusions mécaniques du grêle chez l'adulte aux antécédents de chirurgie abdominale.",
            "Risque évolutif majeur d'ischémie intestinale et de nécrose pariétale engageant le pronostic vital.",
            "Débat persistant entre approche cœlioscopique mini-invasive et laparotomie médiane exploratrice.",
        ]
        intro1_bullets = _get_section_bullets("contexte_justification", intro1_defaults)
        slides.append(
            Slide(
                id="slide-4",
                slide_index=4,
                slide_type="introduction",
                title="INTRODUCTION (1/2) : Définition & Contexte Clinique",
                breadcrumb="1. INTRODUCTION > CONTEXTE",
                background_image_path="tamplates/tamplate diapositive introduction.png",
                elements=[
                    SlideElement(
                        id=f"el-4-b-{i+1}",
                        element_type="bullet_main",
                        content=b,
                        order=i + 1,
                    )
                    for i, b in enumerate(intro1_bullets)
                ],
                presenter_notes=(
                    "Sur le plan clinique, la pathologie représente un motif quotidien d'admission en urgence. "
                    "L'approche mini-invasive par cœlioscopie suscite un intérêt croissant face à la chirurgie ouverte classique."
                ),
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 5: INTRODUCTION (2/2) - Épidémiologie & Justification
        # =========================================================================
        intro2_defaults = [
            "Données épidémiologiques africaines encore très parcellaires avec prédominance du recours tardif aux soins.",
            "Méta-analyses 2020 (Krielen et al.) et 2024 (Patwardhan et al.) confirmant la sécurité et l'efficacité de la cœlioscopie.",
            "Bénéfices documentés : réduction sensible de la durée d'hospitalisation et des complications pariétales.",
            "Nécessité impérieuse d'établir des données probantes locales dans nos hôpitaux universitaires de référence.",
        ]
        slides.append(
            Slide(
                id="slide-5",
                slide_index=5,
                slide_type="introduction",
                title="INTRODUCTION (2/2) : Épidémiologie & Justification",
                breadcrumb="1. INTRODUCTION > JUSTIFICATION",
                background_image_path="tamplates/tamplate diapositive introduction.png",
                elements=[
                    SlideElement(
                        id=f"el-5-b-{i+1}",
                        element_type="bullet_main",
                        content=b,
                        order=i + 1,
                    )
                    for i, b in enumerate(intro2_defaults)
                ],
                presenter_notes=(
                    "Au Cameroun, le retard de consultation complique souvent le tableau clinique. "
                    "Il est essentiel d'évaluer rigoureusement nos résultats pour guider nos pratiques chirurgicales."
                ),
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 6: OBJECTIF GÉNÉRAL (Template Objectif Général)
        # =========================================================================
        obj_gen_text = (
            "Étudier les résultats à court terme de l'abaissement testiculaire cœlio-assisté chez les patients "
            "pris en charge dans trois hôpitaux de référence du Cameroun."
        )
        sec_obj = project.sections.get("objectifs")
        if sec_obj and sec_obj.content_markdown:
            for ln in sec_obj.content_markdown.splitlines():
                if "général" in ln.lower() and len(ln.strip()) > 30:
                    obj_gen_text = _clean_text_for_display(ln)
                    break

        slides.append(
            Slide(
                id="slide-6",
                slide_index=6,
                slide_type="objective_general",
                title="OBJECTIF GÉNÉRAL",
                breadcrumb="2. OBJECTIFS > OBJECTIF GÉNÉRAL",
                background_image_path="tamplates/tamplate diapositive objectif générale.png",
                elements=[
                    SlideElement(
                        id="el-6-obj-gen",
                        element_type="bullet_main",
                        content=obj_gen_text,
                        order=1,
                    )
                ],
                presenter_notes=f"Notre objectif général est le suivant : {obj_gen_text}",
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 7: OBJECTIFS SPÉCIFIQUES (1/2) (Template Objectifs Spécifiques)
        # =========================================================================
        os_defaults_1 = [
            "OS 1 : Décrire le profil épidémiologique et clinique des patients opérés (âge, sexe, comorbidités, antécédents).",
            "OS 2 : Répertorier les indications opératoires et les données diagnostiques préopératoires (échographie, imagerie).",
            "OS 3 : Préciser les aspects techniques peropératoires (localisation, abord, temps opératoire, incidents).",
        ]
        slides.append(
            Slide(
                id="slide-7",
                slide_index=7,
                slide_type="objective_specific",
                title="OBJECTIFS SPÉCIFIQUES (1/2)",
                breadcrumb="3. OBJECTIFS > OBJECTIFS SPÉCIFIQUES",
                background_image_path="tamplates/tamplate objectifs spécifiques.png",
                elements=[
                    SlideElement(
                        id=f"el-7-os-{i+1}",
                        element_type="bullet_main",
                        content=os_txt,
                        order=i + 1,
                    )
                    for i, os_txt in enumerate(os_defaults_1)
                ],
                presenter_notes="Pour atteindre cet objectif général, nos trois premiers objectifs spécifiques portent sur le profil et la technique.",
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 8: OBJECTIFS SPÉCIFIQUES (2/2) (Template Objectifs Spécifiques)
        # =========================================================================
        os_defaults_2 = [
            "OS 4 : Évaluer la morbi-mortalité postopératoire précoce (classification de Clavien-Dindo à J30).",
            "OS 5 : Comparer la durée d'hospitalisation et les suites opératoires selon la technique chirurgicale.",
            "OS 6 : Spécifier le coût financier moyen direct de la prise en charge médico-chirurgicale par patient.",
        ]
        slides.append(
            Slide(
                id="slide-8",
                slide_index=8,
                slide_type="objective_specific",
                title="OBJECTIFS SPÉCIFIQUES (2/2)",
                breadcrumb="3. OBJECTIFS > OBJECTIFS SPÉCIFIQUES",
                background_image_path="tamplates/tamplate objectifs spécifiques.png",
                elements=[
                    SlideElement(
                        id=f"el-8-os-{i+4}",
                        element_type="bullet_main",
                        content=os_txt,
                        order=i + 1,
                    )
                    for i, os_txt in enumerate(os_defaults_2)
                ],
                presenter_notes="Les trois derniers objectifs spécifiques concernent la morbidité, les suites et l'analyse médico-économique.",
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 9: MÉTHODOLOGIE (1/6) - Type, Lieux & Cadre
        # =========================================================================
        m1_defaults = [
            "Type d'étude : Étude observationnelle descriptive et analytique à double composante (rétrospective et prospective).",
            "Cadre d'étude : Trois formations hospitalières universitaires de référence à Yaoundé :",
            "  • Centre Hospitalier et Universitaire de Yaoundé (CHUY)",
            "  • Hôpital Central de Yaoundé (HCY) et Hôpital Général de Yaoundé (HGY)",
            "Plateaux techniques : Services de chirurgie et blocs opératoires équipés de colonnes de laparoscopie vidéo HD.",
        ]
        slides.append(
            Slide(
                id="slide-9",
                slide_index=9,
                slide_type="methodology",
                title="MÉTHODOLOGIE (1/6) : Type, Lieux & Cadre de l'Étude",
                breadcrumb="4. MÉTHODOLOGIE > TYPE & LIEUX",
                background_image_path="tamplates/tamplate diapositive  méthodologie.png",
                elements=[
                    SlideElement(
                        id=f"el-9-m-{i+1}",
                        element_type="bullet_main",
                        content=b,
                        order=i + 1,
                    )
                    for i, b in enumerate(m1_defaults)
                ],
                presenter_notes="Nous abordons la méthodologie avec une étude multicentrique dans trois hôpitaux de référence de Yaoundé.",
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 10: MÉTHODOLOGIE (2/6) - Période & Durée
        # =========================================================================
        m2_defaults = [
            "Durée totale de l'étude : 8 mois, s'étendant du 1er Novembre 2025 au 30 Juin 2026.",
            "Période d'étude globale : 75 mois (du 1er Janvier 2020 au 31 Mars 2026).",
            "  • Phase rétrospective : Dossiers cliniques archivés du 01/01/2020 au 31/10/2025.",
            "  • Phase prospective : Recrutement actif des cas incidents du 01/11/2025 au 31/03/2026.",
            "Suivi clinique post-opératoire : Évaluations systématiques standardisées à J7, J14 et J30.",
        ]
        slides.append(
            Slide(
                id="slide-10",
                slide_index=10,
                slide_type="methodology",
                title="MÉTHODOLOGIE (2/6) : Durée & Période de l'Étude",
                breadcrumb="4. MÉTHODOLOGIE > DURÉE & PÉRIODE",
                background_image_path="tamplates/tamplate diapositive  méthodologie.png",
                elements=[
                    SlideElement(
                        id=f"el-10-m-{i+1}",
                        element_type="bullet_main",
                        content=b,
                        order=i + 1,
                    )
                    for i, b in enumerate(m2_defaults)
                ],
                presenter_notes="La fenêtre de recrutement couvre 75 mois pour assurer une puissance statistique robuste.",
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 11: MÉTHODOLOGIE (3/6) - Population & Critères d'Inclusion
        # =========================================================================
        m3_defaults = [
            "Population cible : Tout patient de tous âges et de deux sexes (M/F) admis pour suspicion d'OIA sur brides / cryptorchidie.",
            "Critères d'inclusion majeurs :",
            "  • Diagnostic peropératoire confirmé et prise en charge chirurgicale dans l'un des trois centres.",
            "  • Consentement éclairé écrit et signé par le patient ou son représentant légal.",
            "  • Dossier médical complet comportant le protocole anesthésique et le compte-rendu opératoire (CRO).",
        ]
        slides.append(
            Slide(
                id="slide-11",
                slide_index=11,
                slide_type="methodology",
                title="MÉTHODOLOGIE (3/6) : Population & Critères d'Inclusion",
                breadcrumb="4. MÉTHODOLOGIE > INCLUSION",
                background_image_path="tamplates/tamplate diapositive  méthodologie.png",
                elements=[
                    SlideElement(
                        id=f"el-11-m-{i+1}",
                        element_type="bullet_main",
                        content=b,
                        order=i + 1,
                    )
                    for i, b in enumerate(m3_defaults)
                ],
                presenter_notes="Les critères d'inclusion sont stricts pour garantir l'homogénéité de notre cohorte.",
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 12: MÉTHODOLOGIE (4/6) - Critères de Non-Inclusion & Éthique
        # =========================================================================
        m4_defaults = [
            "Critères de non-inclusion & d'exclusion :",
            "  • Occlusion sur néoplasie maligne digestive ou hernie étranglée méconnue.",
            "  • Dossiers médicaux inexploitables ou patients perdus de vue avant le suivi à J30.",
            "Considérations éthiques & déontologiques :",
            "  • Clairance éthique institutionnelle délivrée par le Comité d'Éthique de la FMSB / UY1.",
            "  • Autorisations administratives préalables obtenues auprès des directions hospitalières.",
            "  • Confidentialité absolue des données assurée par anonymisation et codage numérique.",
        ]
        slides.append(
            Slide(
                id="slide-12",
                slide_index=12,
                slide_type="methodology",
                title="MÉTHODOLOGIE (4/6) : Critères de Non-Inclusion & Éthique",
                breadcrumb="4. MÉTHODOLOGIE > NON-INCLUSION & ÉTHIQUE",
                background_image_path="tamplates/tamplate diapositive  méthodologie.png",
                elements=[
                    SlideElement(
                        id=f"el-12-m-{i+1}",
                        element_type="bullet_main",
                        content=b,
                        order=i + 1,
                    )
                    for i, b in enumerate(m4_defaults)
                ],
                presenter_notes="Le respect des règles éthiques et la protection des données de nos patients sont au premier plan.",
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 13: MÉTHODOLOGIE (5/6) - Procédure & Déroulement
        # =========================================================================
        m5_defaults = [
            "Phase 1 : Démarches administratives, clairance éthique et autorisations hospitalières.",
            "Phase 2 : Collecte des données rétrospectives (registres opératoires, dossiers cliniques et fiches d'anesthésie).",
            "Phase 3 : Recrutement prospectif au bloc opératoire et suivi clinique hospitalier puis ambulatoire.",
            "Phase 4 : Enregistrement sur formulaire électronique standardisé (CRF) et contrôle de cohérence.",
        ]
        slides.append(
            Slide(
                id="slide-13",
                slide_index=13,
                slide_type="methodology",
                title="MÉTHODOLOGIE (5/6) : Procédure & Déroulement de l'Étude",
                breadcrumb="4. MÉTHODOLOGIE > PROCÉDURE",
                background_image_path="tamplates/tamplate diapositive  méthodologie.png",
                elements=[
                    SlideElement(
                        id=f"el-13-m-{i+1}",
                        element_type="bullet_main",
                        content=b,
                        order=i + 1,
                    )
                    for i, b in enumerate(m5_defaults)
                ],
                presenter_notes="Le déroulement de l'étude s'articule en 4 phases séquentielles rigoureusement encadrées.",
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 14: MÉTHODOLOGIE (6/6) - Variables & Analyse Statistique
        # =========================================================================
        m6_defaults = [
            "Variables étudiées : sociodémographiques (âge, sexe), cliniques (délai, antécédents), peropératoires et évolutives (Clavien-Dindo, séjour).",
            "Taille d'échantillon : Calculée selon la formule de Schwartz pour études observationnelles.",
            "Outils informatiques : Masque de saisie sécurisé Sphinx / EpiData et analyse sous SPSS v26 / R v4.3.",
            "Tests statistiques : Chi-2 / Test exact de Fisher pour variables qualitatives, Student / Mann-Whitney pour variables quantitatives.",
            "Seuil de significativité : Valeur de p < 0,05 avec intervalle de confiance à 95 %.",
        ]
        slides.append(
            Slide(
                id="slide-14",
                slide_index=14,
                slide_type="methodology",
                title="MÉTHODOLOGIE (6/6) : Variables & Analyse Statistique",
                breadcrumb="4. MÉTHODOLOGIE > VARIABLES & STATISTIQUES",
                background_image_path="tamplates/tamplate diapositive  méthodologie.png",
                elements=[
                    SlideElement(
                        id=f"el-14-m-{i+1}",
                        element_type="bullet_main",
                        content=b,
                        order=i + 1,
                    )
                    for i, b in enumerate(m6_defaults)
                ],
                presenter_notes="L'analyse statistique respecte les canons méthodologiques avec un seuil de significativité fixé à 5%.",
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 15: CHRONOGRAMME DES ACTIVITÉS (Déposée telle quelle sans texte !)
        # =========================================================================
        slides.append(
            Slide(
                id="slide-15",
                slide_index=15,
                slide_type="chronogram",
                title="CHRONOGRAMME DES ACTIVITÉS 2026–2027",
                subtitle="Diagramme de Gantt des Activités Prévisionnelles",
                breadcrumb="5. ORGANISATION > CHRONOGRAMME",
                background_image_path="tamplates/Diapositive..Chronogramme des activités 2026–2027 (1).png",
                elements=[],  # Strictly deposited as-is without extra text
                presenter_notes=(
                    "Voici notre chronogramme des activités articulé selon les étapes prévisionnelles de 2026 à 2027 :\n"
                    "- Mai 2026 : Rédaction du protocole\n"
                    "- Juillet 2026 : Obtention des autorisations et clairance éthique\n"
                    "- Octobre 2026 à Mars 2027 : Recueil des données sur le terrain\n"
                    "- Avril 2027 : Analyse statistique et bio-informatique\n"
                    "- Mai 2027 : Rédaction finale et correction de la thèse\n"
                    "- Juin 2027 : Dépôt et soutenance publique."
                ),
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 16: BUDGET PRÉVISIONNEL (Template Budget Radial)
        # =========================================================================
        budget_pills = [
            "1. Matériel & consommables : 350 000 FCFA",
            "2. Transport & logistique : 200 000 FCFA",
            "3. Clairance éthique : 100 000 FCFA",
            "4. Analyse statistique : 150 000 FCFA",
            "5. Reprographie & reliures : 120 000 FCFA",
            "6. Enquêtes & fiches CRF : 80 000 FCFA",
            "7. Communication & réseau : 50 000 FCFA",
            "8. Restitution & publication : 70 000 FCFA",
            "9. Imprévus (10%) : 130 000 FCFA",
            "10. Financement : Fonds propres du candidat",
        ]
        slides.append(
            Slide(
                id="slide-16",
                slide_index=16,
                slide_type="budget",
                title="BUDGET PRÉVISIONNEL ET FINANCEMENT",
                subtitle="1 250 000 FCFA",
                breadcrumb="6. LOGISTIQUE > BUDGET PRÉVISIONNEL",
                background_image_path="tamplates/Tamplate diapositive budget.png",
                elements=[
                    SlideElement(
                        id=f"el-16-b-{idx+1}",
                        element_type="stat_callout" if idx == 0 else "bullet_main",
                        content=pill_txt,
                        order=idx + 1,
                    )
                    for idx, pill_txt in enumerate(budget_pills)
                ],
                presenter_notes=(
                    "Le budget global prévisionnel s'élève à 1 250 000 FCFA, ventilé entre les consommables médicaux, "
                    "les frais administratifs et logistiques, l'analyse des données et la reprographie. "
                    "Le financement est entièrement assuré par les fonds propres du candidat."
                ),
                is_validated=True,
            )
        )

        # =========================================================================
        # SLIDE 17: REMERCIEMENTS & CLÔTURE (Template Merci)
        # =========================================================================
        slides.append(
            Slide(
                id="slide-17",
                slide_index=17,
                slide_type="acknowledgements",
                title="MERCI POUR VOTRE AIMABLE ATTENTION",
                subtitle="« Sapientia Collativa Cognitio »\nFaculté de Médecine et des Sciences Biomédicales\nUniversité de Yaoundé I",
                breadcrumb="CLÔTURE SOUTENANCE",
                background_image_path="tamplates/Merci pour votre attention !.png",
                elements=[
                    SlideElement(
                        id="el-17-thanks",
                        element_type="bullet_main",
                        content="Merci pour votre aimable attention\nThank you for your kind attention",
                        order=1,
                    )
                ],
                presenter_notes=(
                    "Monsieur le Président du Jury, Honorables membres du jury, chers maîtres.\n"
                    "C'est sur ces éléments que se termine la présentation de notre protocole de recherche.\n"
                    "Je vous remercie très humblement pour votre bienveillante attention et me tiens prêt à recevoir vos remarques et orientations."
                ),
                is_validated=True,
            )
        )

        deck.slides = slides
        deck.current_slide_index = 1
        deck.queue_items = [
            QueueDeckItem(
                id=f"queue-{s.slide_index}",
                section_id=s.slide_type,
                section_title=s.title,
                element_type="slide_block",
                text_content=s.title,
                suggested_template="custom_template",
                suggested_slide_index=s.slide_index,
                presenter_cue=s.presenter_notes or "",
            )
            for s in slides
        ]
        return deck
