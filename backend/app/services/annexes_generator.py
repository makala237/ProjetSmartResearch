from typing import Dict, List
from app.data.i18n import normalize_lang
from app.schemas.protocol import ProtocolProject
from app.services.crf_builder import (
    ensure_project_crf_fields,
    extract_hospital_sites_from_project,
)


def build_annexes_pack(project: ProtocolProject) -> List[Dict[str, object]]:
    """Generate clean, pre-filled FMSB ethical & administrative annexes from project metadata."""
    lang = normalize_lang(getattr(project, "language", "fr"))
    c = project.cover
    cfg = project.annexes_config
    author = c.author_name or ("[Candidate Full Name]" if lang == "en" else "[Noms & Prénoms de l'étudiant(e)]")
    matricule = c.matricule or ("[Reg. No.]" if lang == "en" else "[Matricule]")
    level = c.student_level or (
        "Final-year Medical Student" if lang == "en" else "Étudiant(e) en 6ème année de médecine générale"
    )
    phone = c.student_phone or ("[Phone]" if lang == "en" else "[Téléphone]")
    email = c.student_email or ("[E-mail]" if lang == "en" else "[E-mail]")
    title = c.title or project.name
    period = c.study_period_label or ("[Study Period]" if lang == "en" else "[Période d'étude]")
    goal = cfg.study_goal_short or project.research_idea or title
    director = c.director or ("[Supervisor]" if lang == "en" else "[Directeur de thèse]")
    codirs = ", ".join(c.co_directors) if c.co_directors else ("[Co-supervisor(s)]" if lang == "en" else "[Co-directeur(s)]")
    institutions = c.host_institutions or (
        ["[Host Hospital / Study Site Name]"] if lang == "en" else ["[Nom de la structure hospitalière d'accueil]"]
    )
    committees = c.ethics_committees or [
        "Institutional Ethics and Research Committee (CIER-FMSB)"
        if lang == "en"
        else "Comité Institutionnel d'Éthique et de la Recherche de la FMSB (CIER-FMSB)"
    ]
    faculty_name = (
        (getattr(c, "faculty_en", "") or "Faculty of Medicine and Biomedical Sciences").strip()
        if lang == "en"
        else (getattr(c, "faculty_fr", "") or "Faculté de Médecine et des Sciences Biomédicales").strip()
    )
    university_name = (
        (getattr(c, "university_en", "") or "The University of Yaoundé I").strip()
        if lang == "en"
        else (getattr(c, "university_fr", "") or "Université de Yaoundé I").strip()
    )

    annexes: List[Dict[str, object]] = []
    idx = 1

    # 1. Data Collection Form / Fiche de collecte
    if cfg.include_data_collection_form:
        ensure_project_crf_fields(project)
        crf_serialized = [f.model_dump() for f in project.crf_fields]
        hospital_sites = extract_hospital_sites_from_project(project)
        grouped: Dict[str, List[Dict[str, str]]] = {}
        default_sec_title = "General Variables" if lang == "en" else "Variables générales"
        free_lbl = "Free text" if lang == "en" else "Champ libre"
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
        annexes.append(
            {
                "number": idx,
                "title": f"APPENDIX {idx}: DATA COLLECTION FORM (CRF)" if lang == "en" else f"ANNEXE {idx} : FICHE DE COLLECTE DES DONNÉES",
                "kind": "data_collection",
                "grouped_variables": grouped,
                "crf_fields": crf_serialized,
                "hospital_sites": hospital_sites,
                "author_label": f"{author} ({'Reg. No.' if lang == 'en' else 'Matricule'} : {matricule})",
                "study_title": title,
                "paragraphs": (
                    [
                        f"Study Title: {title}",
                        "Instructions: Check the appropriate box or write the value legibly inside the designated field.",
                    ]
                    if lang == "en"
                    else [
                        f"Titre de l'étude : {title}",
                        "Consigne : Cocher la case correspondante ou inscrire lisiblement la valeur dans le cadre réservé.",
                    ]
                ),
            }
        )
        idx += 1

    # 2. Information notice
    if cfg.include_info_notice:
        annexes.append(
            {
                "number": idx,
                "title": f"APPENDIX {idx}: PARTICIPANT INFORMATION SHEET" if lang == "en" else f"ANNEXE {idx} : NOTICE D'INFORMATION DU PARTICIPANT",
                "kind": "letter",
                "paragraphs": (
                    [
                        f"Study Title: {title}",
                        f"Principal Investigator: {author} ({level}, {faculty_name} - {university_name}). Tel: {phone} | E-mail: {email}.",
                        f"Academic Supervision: {director}; Co-supervision: {codirs}.",
                        f"Purpose of the Study: {goal}.",
                        "Procedure & Confidentiality: Your participation is strictly voluntary and confidential. No personally identifiable details will appear on the data collection sheet or in resulting scientific publications. You may withdraw at any time without any impact on your medical care.",
                    ]
                    if lang == "en"
                    else [
                        f"Titre de l'étude : {title}",
                        f"Investigateur principal : {author} ({level}, {faculty_name} - {university_name}). Tél : {phone} | E-mail : {email}.",
                        f"Encadrement scientifique : {director} ; Co-encadrement : {codirs}.",
                        f"But de l'étude : {goal}.",
                        "Déroulement et confidentialité : Votre participation est strictement volontaire et confidentielle. Aucune donnée nominative ne figurera sur la fiche de collecte ni dans les publications issues de ce travail. Vous pouvez vous retirer à tout moment sans aucun préjudice sur votre prise en charge médicale.",
                    ]
                ),
            }
        )
        idx += 1

    # 3. Informed consent form
    if cfg.include_consent_form:
        annexes.append(
            {
                "number": idx,
                "title": f"APPENDIX {idx}: INFORMED CONSENT FORM" if lang == "en" else f"ANNEXE {idx} : FICHE DE CONSENTEMENT ÉCLAIRÉ",
                "kind": "letter",
                "paragraphs": (
                    [
                        f"I, the undersigned Mr./Mrs./Ms. .........................................................., confirm that I have read and understood the information sheet for the study titled: « {title} ».",
                        f"I have had the opportunity to ask all questions to the investigator ({author}, Tel: {phone}) and received clear answers.",
                        "I freely and voluntarily consent to participate in this study, understanding that I may withdraw my consent at any time without justification and without affecting my clinical care.",
                        "Done at .............................., on ....../....../20......",
                        "Participant (or Legal Guardian) Signature: ..............................          Investigator Signature: ..............................",
                    ]
                    if lang == "en"
                    else [
                        f"Je soussigné(e) M./Mme .........................................................., reconnais avoir lu et compris la notice d'information relative à l'étude intitulée : « {title} ».",
                        f"J'ai pu poser toutes les questions souhaitées à l'investigateur ({author}, Tél : {phone}) et j'ai reçu des réponses satisfaisantes.",
                        "J'accepte librement et volontairement de participer à cette étude, tout en sachant que je peux retirer mon consentement à tout moment sans justification et sans conséquence sur mes soins.",
                        "Fait à .............................., le ....../....../20......",
                        "Signature du participant (ou représentant légal) : ..............................          Signature de l'investigateur : ..............................",
                    ]
                ),
            }
        )
        idx += 1

    # 4. Ethics clearance requests
    if cfg.include_ethics_clearance_request:
        for comm in committees:
            annexes.append(
                {
                    "number": idx,
                    "title": f"APPENDIX {idx}: ETHICAL CLEARANCE REQUEST ({comm})" if lang == "en" else f"ANNEXE {idx} : DEMANDE DE CLAIRANCE ÉTHIQUE ({comm})",
                    "kind": "letter",
                    "paragraphs": (
                        [
                            f"{author} — Yaoundé, on ....../....../20......",
                            f"{level} | Reg. No.: {matricule} | Tel: {phone} | E-mail: {email}",
                            f"To the Chairperson of the {comm}",
                            "Subject: Request for Ethical Clearance",
                            "Dear Chairperson,",
                            f"I respectfully write to request ethical clearance from your committee for my research project entitled: « {title} ».",
                            f"I am currently a {level} at the {faculty_name} of {university_name}, working under the supervision of {director}.",
                            f"The main aim of this study is: {goal}. Scheduled period: {period}.",
                            "Please accept, Dear Chairperson, the assurances of my highest consideration.",
                            "Attachments: 01 copy of the research protocol; receipt of academic/ethics review fees; participant information sheet and consent form.",
                        ]
                        if lang == "en"
                        else [
                            f"{author} — Yaoundé, le ....../....../20......",
                            f"{level} | Matricule : {matricule} | Tél : {phone} | E-mail : {email}",
                            f"À Monsieur/Madame le Président(e) du {comm}",
                            "Objet : Demande de clairance éthique",
                            "Monsieur/Madame le Président(e),",
                            f"J'ai l'honneur de venir auprès de votre haute bienveillance solliciter une clairance éthique pour la réalisation de mon travail de recherche intitulé : « {title} ».",
                            f"En effet, je suis {level} à la {faculty_name} de {university_name}, sous la direction de {director}.",
                            f"Le but de ce travail est de : {goal}. L'étude est prévue sur la période : {period}.",
                            "Dans l'attente d'une suite favorable, veuillez agréer, Monsieur/Madame le Président(e), l'expression de mon profond respect.",
                            "Pièces jointes : 01 copie du protocole de recherche ; reçu des droits universitaires / frais d'évaluation éthique ; fiche d'information et formulaire de consentement.",
                        ]
                    ),
                }
            )
            idx += 1

    # 5. Hospital research authorization requests
    if cfg.include_research_auth_requests:
        for inst in institutions:
            annexes.append(
                {
                    "number": idx,
                    "title": f"APPENDIX {idx}: RESEARCH AUTHORIZATION REQUEST ({inst})" if lang == "en" else f"ANNEXE {idx} : DEMANDE D'AUTORISATION DE RECHERCHE ({inst})",
                    "kind": "letter",
                    "paragraphs": (
                        [
                            f"{author} — Yaoundé, on ....../....../20......",
                            f"{level} | Reg. No.: {matricule} | Tel: {phone} | E-mail: {email}",
                            f"To the General Director / Head of {inst}",
                            "Subject: Request for Research Authorization",
                            "Dear Director,",
                            f"I have the honor to request permission to conduct research at your hospital facility during the period {period}.",
                            f"I am a {level} at the {faculty_name} of {university_name}, conducting a study entitled: « {title} », supervised by {director}.",
                            f"Study aim: {goal}.",
                            "Please accept, Dear Director, the expression of my highest esteem.",
                            "Attachments: 01 copy of the protocol; ethical clearance; copy of national ID / student card; research fee receipt.",
                        ]
                        if lang == "en"
                        else [
                            f"{author} — Yaoundé, le ....../....../20......",
                            f"{level} | Matricule : {matricule} | Tél : {phone} | E-mail : {email}",
                            f"À Monsieur/Madame le Directeur Général / Responsable de {inst}",
                            "Objet : Demande d'autorisation de recherche",
                            "Monsieur/Madame le Directeur,",
                            f"J'ai l'honneur de venir auprès de votre haute personnalité solliciter une autorisation de recherche au sein de votre structure hospitalière pour la période allant du {period}.",
                            f"En effet, je suis {level} à la {faculty_name} de {university_name}, et je mène une étude intitulée : « {title} », sous la supervision de {director}.",
                            f"Objectif principal / but : {goal}.",
                            "Dans l'attente d'une suite favorable, veuillez agréer, Monsieur/Madame le Directeur, l'expression de mon profond respect.",
                            "Pièces jointes : 01 copie du protocole ; clairance éthique ; photocopie de la CNI / carte d'étudiant ; reçu des frais de recherche.",
                        ]
                    ),
                }
            )
            idx += 1

    # 6. Researcher confidentiality declaration
    if cfg.include_confidentiality_declaration:
        annexes.append(
            {
                "number": idx,
                "title": f"APPENDIX {idx}: RESEARCHER CONFIDENTIALITY DECLARATION" if lang == "en" else f"ANNEXE {idx} : DÉCLARATION DE CONFIDENTIALITÉ DU CHERCHEUR",
                "kind": "letter",
                "paragraphs": (
                    [
                        f"I, the undersigned {author} (Reg. No.: {matricule}), {level} at {faculty_name} ({university_name}), solemnly pledge to:",
                        "1. Maintain strict confidentiality regarding all clinical and personal data collected during this study.",
                        f"2. Use collected data exclusively for this research protocol under the supervision of my advisors ({director}; {codirs}).",
                        "3. Ensure physical and digital data security (coded anonymization, password-protected storage).",
                        "4. Never disclose identifying participant information to third parties in any form.",
                        "5. Uphold international medical research ethics (Declaration of Helsinki) and national regulations.",
                        f"Done at Yaoundé, on ....../....../20......     Signature: {author}",
                    ]
                    if lang == "en"
                    else [
                        f"Je soussigné(e), {author} (Matricule : {matricule}), {level} à la {faculty_name} de {university_name}, m'engage solennellement à :",
                        "1. Respecter la plus stricte confidentialité concernant toutes les informations médicales et personnelles recueillies dans le cadre de cette étude.",
                        f"2. N'utiliser ces données que dans le cadre exclusif de ce protocole de recherche, sous la supervision de mon équipe encadrante ({director} ; {codirs}).",
                        "3. Assurer la sécurité physique et numérique des données (anonymisation par code, accès protégé par mot de passe).",
                        "4. Ne divulguer aucune donnée identifiante à des tiers sous quelque forme que ce soit.",
                        "5. Respecter les principes éthiques de la recherche médicale (Déclaration d'Helsinki) et la réglementation en vigueur au Cameroun.",
                        f"Fait à Yaoundé, le ....../....../20......     Signature : {author}",
                    ]
                ),
            }
        )
        idx += 1

    # 7. Commitment letter
    if cfg.include_commitment_letter:
        annexes.append(
            {
                "number": idx,
                "title": f"APPENDIX {idx}: COMMITMENT LETTER" if lang == "en" else f"ANNEXE {idx} : LETTRE D'ENGAGEMENT",
                "kind": "letter",
                "paragraphs": (
                    [
                        f"I, {author} (Reg. No.: {matricule}), {level} at {faculty_name} ({university_name}), hereby commit to:",
                        "• Preserve strict confidentiality of medical records and participant data.",
                        "• Use collected data solely within the scope of this research project.",
                        f"• Acknowledge host institutions ({', '.join(institutions)}) in the thesis acknowledgements.",
                        "• Share study findings and scientific feedback with participating clinical sites.",
                        f"Done at Yaoundé, on ....../....../20......     Signature: {author}",
                    ]
                    if lang == "en"
                    else [
                        f"Je soussigné(e), {author} (Matricule : {matricule}), {level} à {faculty_name} ({university_name}), m'engage à :",
                        "• Préserver la confidentialité absolue des dossiers médicaux et des données des participants.",
                        "• N'utiliser les données recueillies exclusivement que dans le cadre de ce travail de recherche.",
                        f"• Citer les structures d'accueil ({', '.join(institutions)}) dans les remerciements du mémoire/thèse.",
                        "• Assurer la restitution et la rétro-information des résultats scientifiques aux structures partenaires.",
                        f"Fait à Yaoundé, le ....../....../20......     Signature : {author}",
                    ]
                ),
            }
        )

    return annexes
