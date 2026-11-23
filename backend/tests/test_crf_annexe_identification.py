from unittest.mock import patch
import unittest
from app.schemas.protocol import ProtocolProject, ReferenceItem, SectionState, StepStatus, VariableItem
from app.services.crf_builder import (
    _AICRFFieldOut,
    _AICRFGenerationOut,
    extract_annexe1_block,
    extract_crf_fields_from_project,
    ensure_project_crf_fields,
    crf_fields_to_variables,
    generate_or_improve_crf_with_ai,
)


class TestCRFAnnexeIdentification(unittest.TestCase):
    def test_extract_annexe1_block_standard(self):
        md = """# ANNEXES
## ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES
### SECTION A : SOCIODÉMOGRAPHIQUE
- **A1. Âge :** [____] ans
- **A2. Sexe :** [ ] Masculin  [ ] Féminin

## ANNEXE 2 : NOTICE D'INFORMATION DU PARTICIPANT
Texte de la notice...
"""
        before, ann1, after = extract_annexe1_block(md)
        self.assertIn("ANNEXE 1", ann1)
        self.assertIn("Âge", ann1)
        self.assertIn("ANNEXE 2", after)
        self.assertNotIn("ANNEXE 2", ann1)

    def test_extract_annexe1_block_roman_numeral_and_french_variants(self):
        md = """## ANNEXE I : FICHE DE COLLECTE DES DONNÉES
### SECTION A : IDENTIFICATION
- 1. Âge du patient : [____] ans
- 2. Tabagisme : [ ] Oui  [ ] Non

## ANNEXE II : FORMULAIRE DE CONSENTEMENT ÉCLAIRÉ
Texte consentement...
"""
        before, ann1, after = extract_annexe1_block(md)
        self.assertIn("ANNEXE I", ann1)
        self.assertIn("Âge du patient", ann1)
        self.assertIn("ANNEXE II", after)
        self.assertNotIn("ANNEXE II", ann1)

    def test_extract_annexe1_block_formulaire_denregistrement(self):
        md = """# ANNEXES MÉDICALES
## FORMULAIRE D'ENREGISTREMENT CLINIQUE
### SECTION A : DONNÉES DU PATIENT
- Q1. Statut matrimonial : [ ] Célibataire  [ ] Marié(e)  [ ] Veuf/Veuve
- Q2. Poids : [____] kg

## FORMULAIRE DE CONSENTEMENT
Texte consentement...
"""
        before, ann1, after = extract_annexe1_block(md)
        self.assertIn("FORMULAIRE D'ENREGISTREMENT", ann1)
        self.assertIn("Poids", ann1)
        self.assertIn("FORMULAIRE DE CONSENTEMENT", after)

    def test_extract_annexe1_block_english_appendix(self):
        md = """# APPENDIX SECTION
## APPENDIX 1: DATA COLLECTION FORM (CRF)
### SECTION A: DEMOGRAPHICS
- **A1. Age:** [____] ans
- **A2. Gender:** [ ] Male  [ ] Female

## APPENDIX 2: PARTICIPANT INFORMATION SHEET
Info notice...
"""
        before, ann1, after = extract_annexe1_block(md)
        self.assertIn("APPENDIX 1", ann1)
        self.assertIn("Age", ann1)
        self.assertIn("APPENDIX 2", after)

    def test_extract_crf_fields_from_project_varied_headers(self):
        project = ProtocolProject(
            project_id="test-proj-crf",
            name="Test Projet CRF",
            sections={
                "annexes": SectionState(
                    section_id="annexes",
                    content_markdown="""## FICHE DE COLLECTE DES DONNÉES
### SECTION A : SOCIODÉMOGRAPHIQUE
- 1. Âge : [____] ans
- 2. Sexe : [ ] Masculin  [ ] Féminin
- 3. Antécédents d'hypertension : [ ] Oui  [ ] Non (Si oui, préciser : [________])
- 4. Traitement en cours : [__________________]

## NOTICE D'INFORMATION DU PARTICIPANT
Information ici...
""",
                )
            },
        )

        fields = extract_crf_fields_from_project(project)
        self.assertGreaterEqual(len(fields), 4)

        labels = [f.label for f in fields]
        self.assertTrue(any("Âge" in l for l in labels))
        self.assertTrue(any("Sexe" in l for l in labels))
        self.assertTrue(any("Antécédents d'hypertension" in l for l in labels))

        # Check field types inferred
        age_f = next(f for f in fields if "Âge" in f.label)
        self.assertEqual(age_f.field_type, "number")
        self.assertEqual(age_f.unit, "ans")

        sexe_f = next(f for f in fields if "Sexe" in f.label)
        self.assertIn(sexe_f.field_type, ("dichotomous", "single_choice"))

        hta_f = next(f for f in fields if "Antécédents d'hypertension" in f.label)
        self.assertEqual(hta_f.field_type, "dichotomous")
        self.assertIn("préciser", (hta_f.sub_field_label or "").lower())

    def test_crf_positioned_at_annexe_3_not_annexe_1(self):
        md = """# ANNEXES DU PROTOCOLE
## ANNEXE 1 : NOTICE D'INFORMATION DU PARTICIPANT
Texte de la notice d'information...

## ANNEXE 2 : FORMULAIRE DE CONSENTEMENT ÉCLAIRÉ
Texte du consentement éclairé...

## ANNEXE 3 : FICHE DE COLLECTE DES DONNÉES
### SECTION A : SOCIODÉMOGRAPHIQUE
- **A1. Âge :** [____] ans
- **A2. Statut matrimonial :** [ ] Célibataire  [ ] Marié(e)
- **A3. Antécédents :** [ ] HTA  [ ] Diabète

## ANNEXE 4 : ÉCHELLE DE GLASGOW
Critères de l'échelle de Glasgow...
"""
        before, crf, after = extract_annexe1_block(md)
        self.assertIn("ANNEXE 1", before)
        self.assertIn("ANNEXE 2", before)
        self.assertIn("ANNEXE 3 : FICHE DE COLLECTE", crf)
        self.assertIn("Âge", crf)
        self.assertIn("ANNEXE 4 : ÉCHELLE DE GLASGOW", after)

    def test_build_annexes_pack_preserves_user_custom_annexes_and_order(self):
        from app.services.annexes_generator import build_annexes_pack

        project = ProtocolProject(
            project_id="custom-order-proj",
            name="Etude Traumatologie",
            sections={
                "annexes": SectionState(
                    section_id="annexes",
                    content_markdown="""# ANNEXES
## ANNEXE 1 : NOTICE D'INFORMATION DU PATIENT
Paragraphe 1 de la notice...
Paragraphe 2 de la notice...

## ANNEXE 2 : FORMULAIRE DE CONSENTEMENT
Paragraphe du consentement...

## ANNEXE 3 : FICHE DE COLLECTE DES DONNÉES
### SECTION A : VARIABLES CLINIQUES
- A1. Score de Glasgow : [____]
- A2. Délais de prise en charge : [____] heures

## ANNEXE 4 : ÉCHELLE DE DOULEUR EVA
Description de l'échelle visuelle analogique...
""",
                )
            },
        )

        pack = build_annexes_pack(project)
        self.assertEqual(len(pack), 4)

        # Order must be preserved exactly
        self.assertIn("NOTICE D'INFORMATION", pack[0]["title"])
        self.assertEqual(pack[0]["kind"], "letter")

        self.assertIn("CONSENTEMENT", pack[1]["title"])
        self.assertEqual(pack[1]["kind"], "letter")

        self.assertIn("FICHE DE COLLECTE", pack[2]["title"])
        self.assertEqual(pack[2]["kind"], "data_collection")
        self.assertIn("grouped_variables", pack[2])

        self.assertIn("ÉCHELLE DE DOULEUR", pack[3]["title"])
        self.assertEqual(pack[3]["kind"], "letter")

    def test_phase1_dedicated_crf_subsection_in_fr_and_en_canvases(self):
        from app.data.fmsb_canvas import FMSB_SECTIONS
        from app.data.i18n import get_localized_canvas
        from app.services.humanizer import NON_NARRATIVE_SECTION_IDS
        from app.services.segmentation import ALLOWED_TARGET_SECTIONS

        fr_ids = [s.id for s in FMSB_SECTIONS]
        en_sections = get_localized_canvas("en")
        en_ids = [s.id for s in en_sections]

        self.assertEqual(len(FMSB_SECTIONS), 34)
        self.assertEqual(len(en_sections), 34)
        self.assertIn("annexe_fiche_collecte", fr_ids)
        self.assertIn("annexe_fiche_collecte", en_ids)

        crf_fr = next(s for s in FMSB_SECTIONS if s.id == "annexe_fiche_collecte")
        ann_fr = next(s for s in FMSB_SECTIONS if s.id == "annexes")
        self.assertEqual(crf_fr.chapter_id, "annexes")
        self.assertLess(crf_fr.order, ann_fr.order)

        crf_en = next(s for s in en_sections if s.id == "annexe_fiche_collecte")
        self.assertIn("Data Collection Form", crf_en.title)

        self.assertNotIn("annexe_fiche_collecte", ALLOWED_TARGET_SECTIONS)
        self.assertIn("annexe_fiche_collecte", NON_NARRATIVE_SECTION_IDS)

    def test_phase2_and_3_ai_generate_improve_crf_and_build_pack_without_duplication(self):
        from app.services.annexes_generator import build_annexes_pack
        from app.services.drafting import draft_section_with_ai

        project = ProtocolProject(
            project_id="cames-crf-proj",
            name="Facteurs associés à l'hypertension artérielle non contrôlée",
            research_idea="Étude transversale analytique à l'Hôpital Central de Yaoundé",
            study_type="Étude transversale analytique",
            variables=[
                VariableItem(
                    name="Observance thérapeutique",
                    category="SECTION E : PRISE EN CHARGE ET ÉVOLUTION",
                    role="Dépendante",
                    measurement_scale="Qualitative nominale",
                    modalities="Bonne / Moyenne / Mauvaise",
                )
            ],
            references=[
                ReferenceItem(
                    title="Classification OMS de l'hypertension et score d'observance de Morisky (MMAS-8)",
                    journal="Cardiol Trop",
                    year=2024,
                    abstract="L'évaluation repose sur le score MMAS-8 et le contrôle tensionnel < 140/90 mmHg.",
                )
            ],
            sections={
                "chap1_objectifs": SectionState(
                    section_id="chap1_objectifs",
                    content_markdown="Déterminer la prévalence du non-contrôle tensionnel et évaluer l'observance.",
                ),
                "annexe_fiche_collecte": SectionState(section_id="annexe_fiche_collecte"),
                "annexes": SectionState(
                    section_id="annexes",
                    content_markdown="## ANNEXE 2 : NOTICE D'INFORMATION\nTexte d'information au participant.\n\n## ANNEXE 3 : CONSENTEMENT ÉCLAIRÉ\nJe soussigné(e)...",
                ),
            },
        )

        mock_out = _AICRFGenerationOut(
            form_title="## ANNEXE 1 : FICHE DE COLLECTE DES DONNÉES (HTA)",
            assistant_commentary="Fiche structurée CAMES incluant pression artérielle en mmHg et score MMAS-8 issu de la revue.",
            cames_compliance_checklist=[
                "Anonymat strict vérifié (aucun nom/téléphone)",
                "Concordance 1-pour-1 avec objectifs et variables",
            ],
            fields=[
                _AICRFFieldOut(
                    code="A0",
                    section_title="SECTION A : IDENTIFICATION",
                    label="Nom complet et téléphone du patient",
                    field_type="text",
                    options=[],
                ),
                _AICRFFieldOut(
                    code="A1",
                    section_title="SECTION A : CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES",
                    label="Âge révolu",
                    field_type="number",
                    unit="ans",
                    required=True,
                ),
                _AICRFFieldOut(
                    code="D1",
                    section_title="SECTION D : PARACLINIQUE ET SCORES",
                    label="Pression artérielle systolique",
                    field_type="number",
                    unit="mmHg",
                    required=True,
                ),
                _AICRFFieldOut(
                    code="E1",
                    section_title="SECTION E : PRISE EN CHARGE ET ÉVOLUTION",
                    label="Observance thérapeutique (MMAS-8)",
                    field_type="single_choice",
                    options=["Bonne", "Moyenne", "Mauvaise"],
                    sub_field_label="Si mauvaise, motif principal",
                    required=True,
                ),
            ],
        )

        with patch(
            "app.services.gemini_client.gemini_router.generate_structured",
            return_value={"model_used": "gemini-3.5-flash-lite", "data": mock_out, "fallback_errors": []},
        ):
            res = draft_section_with_ai(project, "annexe_fiche_collecte", user_instruction="Inclure MMAS-8")

        self.assertEqual(res["model_used"], "gemini-3.5-flash-lite")
        self.assertEqual(res["orchestration_trace"]["crf_mode"], "generate")
        # PII field ("Nom complet et téléphone du patient") must be stripped automatically
        labels = [f.label for f in project.crf_fields]
        self.assertFalse(any("téléphone" in l.lower() for l in labels))
        self.assertIn("Âge révolu", labels)
        self.assertIn("Observance thérapeutique (MMAS-8)", labels)

        # Dedicated sub-section populated cleanly and drives extraction
        crf_sec = project.sections["annexe_fiche_collecte"]
        self.assertIn("ANNEXE 1 : FICHE DE COLLECTE", crf_sec.content_markdown)
        self.assertEqual(crf_sec.status, StepStatus.IN_PROGRESS)
        re_extracted = extract_crf_fields_from_project(project)
        self.assertTrue(any(f.unit == "mmHg" for f in re_extracted))

        # Word / export pack combines dedicated CRF (1) + custom non-CRF annexes (2, 3) with zero duplication
        pack = build_annexes_pack(project)
        self.assertEqual(len(pack), 3)
        self.assertEqual(pack[0]["kind"], "data_collection")
        self.assertEqual(pack[1]["kind"], "letter")
        self.assertIn("NOTICE", pack[1]["title"])
        self.assertEqual(pack[2]["kind"], "letter")
        self.assertIn("CONSENTEMENT", pack[2]["title"])

        # Subsequent auto run switches cleanly to improve mode
        with patch(
            "app.services.gemini_client.gemini_router.generate_structured",
            return_value={"model_used": "gemini-3.5-flash-lite", "data": mock_out, "fallback_errors": []},
        ):
            res_imp = generate_or_improve_crf_with_ai(project, user_instruction="Affiner les tranches d'âge", mode="auto")
        self.assertEqual(res_imp["mode_applied"], "improve")


if __name__ == "__main__":
    unittest.main()
