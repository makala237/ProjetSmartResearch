from typing import Dict, List
from app.schemas.protocol import ProtocolProject
from app.services.crf_builder import (
    ensure_project_crf_fields,
    extract_hospital_sites_from_project,
)


def build_annexes_pack(project: ProtocolProject) -> List[Dict[str, object]]:
    """Generate clean, pre-filled FMSB ethical & administrative annexes from project metadata."""
    c = project.cover
    cfg = project.annexes_config
    author = c.author_name or "[Noms & Prénoms de l'étudiant(e)]"
    matricule = c.matricule or "[Matricule]"
    level = c.student_level or "Étudiant(e) en 6ème année de médecine générale"
    phone = c.student_phone or "[Téléphone]"
    email = c.student_email or "[E-mail]"
    title = c.title or project.name
    period = c.study_period_label or "[Période d'étude]"
    goal = cfg.study_goal_short or project.research_idea or title
    director = c.director or "[Directeur de thèse]"
    codirs = ", ".join(c.co_directors) if c.co_directors else "[Co-directeur(s)]"
    institutions = c.host_institutions or ["[Nom de la structure hospitalière d'accueil]"]
    committees = c.ethics_committees or [
        "Comité Institutionnel d'Éthique et de la Recherche de la FMSB (CIER-FMSB)"
    ]

    annexes: List[Dict[str, object]] = []
    idx = 1

    # 1. Fiche de collecte de données (enrichie via le moteur CRF)
    if cfg.include_data_collection_form:
        ensure_project_crf_fields(project)
        crf_serialized = [f.model_dump() for f in project.crf_fields]
        hospital_sites = extract_hospital_sites_from_project(project)
        grouped: Dict[str, List[Dict[str, str]]] = {}
        for f in project.crf_fields:
            grouped.setdefault(f.section_title or "Variables générales", []).append(
                {
                    "code": f.code,
                    "name": f.label,
                    "field_type": f.field_type,
                    "options": " | ".join(f.options),
                    "unit": f.unit,
                    "sub_field_label": f.sub_field_label,
                    "modalities": " / ".join(f.options) if f.options else (f.unit or "Champ libre"),
                }
            )
        annexes.append(
            {
                "number": idx,
                "title": f"ANNEXE {idx} : FICHE DE COLLECTE DES DONNÉES",
                "kind": "data_collection",
                "grouped_variables": grouped,
                "crf_fields": crf_serialized,
                "hospital_sites": hospital_sites,
                "author_label": f"{author} (Matricule : {matricule})",
                "study_title": title,
                "paragraphs": [
                    f"Titre de l'étude : {title}",
                    "Consigne : Cocher la case correspondante ou inscrire lisiblement la valeur dans le cadre réservé.",
                ],
            }
        )
        idx += 1

    # 2. Notice d'information
    if cfg.include_info_notice:
        annexes.append(
            {
                "number": idx,
                "title": f"ANNEXE {idx} : NOTICE D'INFORMATION DU PARTICIPANT",
                "kind": "letter",
                "paragraphs": [
                    f"Titre de l'étude : {title}",
                    f"Investigateur principal : {author} ({level}, FMSB - Université de Yaoundé I). Tél : {phone} | E-mail : {email}.",
                    f"Encadrement scientifique : {director} ; Co-encadrement : {codirs}.",
                    f"But de l'étude : {goal}.",
                    f"Déroulement et confidentialité : Votre participation est strictement volontaire et confidentielle. Aucune donnée nominative ne figurera sur la fiche de collecte ni dans les publications issues de ce travail. Vous pouvez vous retirer à tout moment sans aucun préjudice sur votre prise en charge médicale.",
                ],
            }
        )
        idx += 1

    # 3. Fiche de consentement éclairé
    if cfg.include_consent_form:
        annexes.append(
            {
                "number": idx,
                "title": f"ANNEXE {idx} : FICHE DE CONSENTEMENT ÉCLAIRÉ",
                "kind": "letter",
                "paragraphs": [
                    f"Je soussigné(e) M./Mme .........................................................., reconnais avoir lu et compris la notice d'information relative à l'étude intitulée : « {title} ».",
                    f"J'ai pu poser toutes les questions souhaitées à l'investigateur ({author}, Tél : {phone}) et j'ai reçu des réponses satisfaisantes.",
                    "J'accepte librement et volontairement de participer à cette étude, tout en sachant que je peux retirer mon consentement à tout moment sans justification et sans conséquence sur mes soins.",
                    "Fait à .............................., le ....../....../20......",
                    "Signature du participant (ou représentant légal) : ..............................          Signature de l'investigateur : ..............................",
                ],
            }
        )
        idx += 1

    # 4. Demandes de clairance éthique
    if cfg.include_ethics_clearance_request:
        for comm in committees:
            annexes.append(
                {
                    "number": idx,
                    "title": f"ANNEXE {idx} : DEMANDE DE CLAIRANCE ÉTHIQUE ({comm})",
                    "kind": "letter",
                    "paragraphs": [
                        f"{author} — Yaoundé, le ....../....../20......",
                        f"{level} | Matricule : {matricule} | Tél : {phone} | E-mail : {email}",
                        f"À Monsieur/Madame le Président(e) du {comm}",
                        "Objet : Demande de clairance éthique",
                        "Monsieur/Madame le Président(e),",
                        f"J'ai l'honneur de venir auprès de votre haute bienveillance solliciter une clairance éthique pour la réalisation de mon travail de recherche intitulé : « {title} ».",
                        f"En effet, je suis {level} à la Faculté de Médecine et des Sciences Biomédicales de l'Université de Yaoundé I, sous la direction de {director}.",
                        f"Le but de ce travail est de : {goal}. L'étude est prévue sur la période : {period}.",
                        "Dans l'attente d'une suite favorable, veuillez agréer, Monsieur/Madame le Président(e), l'expression de mon profond respect.",
                        "Pièces jointes : 01 copie du protocole de recherche ; reçu des droits universitaires / frais d'évaluation éthique ; fiche d'information et formulaire de consentement.",
                    ],
                }
            )
            idx += 1

    # 5. Demandes d'autorisation de recherche par site
    if cfg.include_research_auth_requests:
        for inst in institutions:
            annexes.append(
                {
                    "number": idx,
                    "title": f"ANNEXE {idx} : DEMANDE D'AUTORISATION DE RECHERCHE ({inst})",
                    "kind": "letter",
                    "paragraphs": [
                        f"{author} — Yaoundé, le ....../....../20......",
                        f"{level} | Matricule : {matricule} | Tél : {phone} | E-mail : {email}",
                        f"À Monsieur/Madame le Directeur Général / Responsable de {inst}",
                        "Objet : Demande d'autorisation de recherche",
                        "Monsieur/Madame le Directeur,",
                        f"J'ai l'honneur de venir auprès de votre haute personnalité solliciter une autorisation de recherche au sein de votre structure hospitalière pour la période allant du {period}.",
                        f"En effet, je suis {level} à la Faculté de Médecine et des Sciences Biomédicales de l'Université de Yaoundé I, et je mène une étude intitulée : « {title} », sous la supervision de {director}.",
                        f"Objectif principal / but : {goal}.",
                        "Dans l'attente d'une suite favorable, veuillez agréer, Monsieur/Madame le Directeur, l'expression de mon profond respect.",
                        "Pièces jointes : 01 copie du protocole ; clairance éthique ; photocopie de la CNI / carte d'étudiant ; reçu des frais de recherche.",
                    ],
                }
            )
            idx += 1

    # 6. Déclaration de confidentialité du chercheur
    if cfg.include_confidentiality_declaration:
        annexes.append(
            {
                "number": idx,
                "title": f"ANNEXE {idx} : DÉCLARATION DE CONFIDENTIALITÉ DU CHERCHEUR",
                "kind": "letter",
                "paragraphs": [
                    f"Je soussigné(e), {author} (Matricule : {matricule}), {level} à la Faculté de Médecine et des Sciences Biomédicales (FMSB) de l'Université de Yaoundé I, m'engage solennellement à :",
                    "1. Respecter la plus stricte confidentialité concernant toutes les informations médicales et personnelles recueillies dans le cadre de cette étude.",
                    f"2. N'utiliser ces données que dans le cadre exclusif de ce protocole de recherche, sous la supervision de mon équipe encadrante ({director} ; {codirs}).",
                    "3. Assurer la sécurité physique et numérique des données (anonymisation par code, accès protégé par mot de passe).",
                    "4. Ne divulguer aucune donnée identifiante à des tiers sous quelque forme que ce soit.",
                    "5. Respecter les principes éthiques de la recherche médicale (Déclaration d'Helsinki) et la réglementation en vigueur au Cameroun.",
                    f"Fait à Yaoundé, le ....../....../20......     Signature : {author}",
                ],
            }
        )
        idx += 1

    # 7. Lettre d'engagement
    if cfg.include_commitment_letter:
        annexes.append(
            {
                "number": idx,
                "title": f"ANNEXE {idx} : LETTRE D'ENGAGEMENT",
                "kind": "letter",
                "paragraphs": [
                    f"Je soussigné(e), {author} (Matricule : {matricule}), {level} à la FMSB-UYI, m'engage à :",
                    "• Préserver la confidentialité absolue des dossiers médicaux et des données des participants.",
                    "• N'utiliser les données recueillies exclusivement que dans le cadre de ce travail de recherche.",
                    f"• Citer les structures d'accueil ({', '.join(institutions)}) dans les remerciements du mémoire/thèse.",
                    "• Assurer la restitution et la rétro-information des résultats scientifiques aux structures partenaires.",
                    f"Fait à Yaoundé, le ....../....../20......     Signature : {author}",
                ],
            }
        )

    return annexes
