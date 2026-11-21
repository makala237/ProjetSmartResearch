import unittest
from app.schemas.protocol import ProtocolProject, SectionState, VariableItem
from app.services.crf_builder import (
    extract_annexe1_block,
    extract_crf_fields_from_project,
    ensure_project_crf_fields,
    crf_fields_to_variables,
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


if __name__ == "__main__":
    unittest.main()
