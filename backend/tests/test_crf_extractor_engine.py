import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from docx import Document

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import (
    CRFFieldSpec,
    InternalLibraryEntry,
    InternalSourceDocument,
    PatientRecord,
    ProtocolProject,
    SectionState,
    StepStatus,
    VariableItem,
)
from app.services.crf_builder import _AICRFFieldOut
from app.services.crf_extractor import (
    _AIBoundaryDecision,
    _AICRFCompilationOut,
    compile_crf_fields_hybrid,
    detect_crf_boundaries_in_blocks,
    extract_and_sync_crf_for_project,
    reconcile_crf_fields,
    slice_crf_from_text_or_blocks,
)
from app.services.importer import (
    ImportedProtocolIndexAnalysis,
    ImportedSectionIndexMapping,
    analyze_and_import_protocol,
    split_upload_into_blocks,
)


def _make_project(project_id: str = "test-crf-extractor") -> ProtocolProject:
    sections = {
        spec.id: SectionState(section_id=spec.id, status=StepStatus.PENDING)
        for spec in FMSB_SECTIONS
    }
    return ProtocolProject(
        project_id=project_id,
        name="Facteurs associés à la pré-éclampsie sévère à Yaoundé",
        research_idea="Étude cas-témoins à l'HGOPY",
        study_type="Étude cas-témoins",
        language="fr",
        sections=sections,
    )


def _build_realistic_word_protocol_bytes() -> bytes:
    """Create an in-memory .docx without '#' Markdown, featuring Chap 3, Annexe 1 (CRF + table), Annexe 2 (Notice), Annexe 3 (Consent)."""
    doc = Document()
    doc.add_paragraph("CHAPITRE III : METHODOLOGIE")
    doc.add_paragraph(
        "Outils de collecte des données : une fiche de collecte pré-testée et anonyme sera utilisée pour recueillir "
        "les caractéristiques sociodémographiques, cliniques et biologiques des patientes incluses dans l'étude. "
        "Les données seront saisies de manière confidentielle après obtention du consentement."
    )
    doc.add_paragraph("ANNEXE 1 : FICHE DE COLLECTE DES DONNEES")
    doc.add_paragraph("SECTION A : CARACTERISTIQUES SOCIODEMOGRAPHIQUES")
    doc.add_paragraph("A1. Âge de la patiente : ............. ans")
    doc.add_paragraph("A2. Statut matrimonial : □ Célibataire  □ Mariée  □ Veuve")
    doc.add_paragraph("A3. Consentement éclairé obtenu : □ Oui  □ Non")
    doc.add_paragraph("SECTION B : DONNEES CLINIQUES ET PARACLINIQUES")

    tbl = doc.add_table(rows=4, cols=3)
    rows_data = [
        ("Code", "Variable", "Réponse / Modalités"),
        ("B1", "Pression artérielle systolique", "........ mmHg"),
        ("B2", "Antécédent d'hypertension", "Oui / Non"),
        ("B3", "Protéinurie à la bandelette", "□ Négative  □ +  □ ++  □ +++"),
    ]
    for r_i, vals in enumerate(rows_data):
        for c_i, val in enumerate(vals):
            tbl.cell(r_i, c_i).text = val

    doc.add_paragraph("ANNEXE 2 : NOTICE D'INFORMATION DU PARTICIPANT")
    doc.add_paragraph(
        "Madame, Monsieur, vous êtes invité(e) à participer à une étude médicale menée à l'Hôpital Gynéco-Obstétrique. "
        "Votre participation est entièrement libre et anonyme sans aucun risque supplémentaire."
    )
    doc.add_paragraph("ANNEXE 3 : FORMULAIRE DE CONSENTEMENT ECLAIRE")
    doc.add_paragraph("Je soussigné(e) ____________ déclare accepter librement de participer à cette recherche.")
    doc.add_paragraph("Fait à Yaoundé, le ________ / ________ / 20____")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class TestCRFExtractorEngine(unittest.TestCase):
    def test_raw_word_boundary_detection_excludes_notice_and_consent(self):
        docx_bytes = _build_realistic_word_protocol_bytes()
        blocks = split_upload_into_blocks("protocole_brut.docx", docx_bytes)
        self.assertGreaterEqual(len(blocks), 5)

        det = detect_crf_boundaries_in_blocks(blocks, lang="fr", use_ai=False)
        self.assertTrue(det.found)
        self.assertIsNotNone(det.start_block_id)
        self.assertIsNotNone(det.end_block_id)

        before, pure_crf, after, sliced_det = slice_crf_from_text_or_blocks(
            blocks=blocks,
            lang="fr",
            use_ai=False,
            precomputed_detection=det,
        )
        self.assertTrue(sliced_det.found)
        # Chapitre III must remain outside CRF, Annexe 2 & Annexe 3 must stay in `after`
        self.assertIn("CHAPITRE III", before)
        self.assertIn("ANNEXE 1 : FICHE DE COLLECTE", pure_crf)
        self.assertIn("SECTION A", pure_crf)
        self.assertIn("Consentement éclairé obtenu", pure_crf)  # Q inside Section A NOT cut off!
        self.assertIn("Pression artérielle systolique", pure_crf)  # Word table captured
        self.assertNotIn("NOTICE D'INFORMATION", pure_crf)
        self.assertNotIn("Je soussigné", pure_crf)
        self.assertIn("NOTICE D'INFORMATION", after)
        self.assertIn("CONSENTEMENT ECLAIRE", after)

    def test_long_document_tail_not_truncated_on_import(self):
        # Create a 162-block document where blocks 152..159 contain the CRF + Notice at the very tail
        chunks = [
            f"PARTIE {i}\nParagraphe descriptif sur la prise en charge clinique numéro {i}."
            for i in range(1, 152)
        ]
        chunks.extend([
            "ANNEXE 1 : FICHE D'ENQUETE",
            "SECTION A : SOCIODEMOGRAPHIE\n- A1. Âge révolu : ......... ans\n- A2. Sexe : Masculin / Féminin",
            "SECTION B : PARACLINIQUE\n- B1. Glycémie à jeun : ____ g/dL\n- B2. Diabète connu : □ Oui  □ Non",
            "ANNEXE 2 : NOTICE D'INFORMATION\nExplications détaillées sur la confidentialité de l'étude.",
            "ANNEXE 3 : CONSENTEMENT ECLAIRE\nSignature du participant.",
        ])
        raw_bytes = "\n\n".join(chunks).encode("utf-8")

        project = _make_project("long-doc-tail")
        seen_prompts = []

        def fake_generate_structured(*, role, contents, schema, **kwargs):
            seen_prompts.append((schema, contents))
            if schema is ImportedProtocolIndexAnalysis:
                # Mapper puts blocks [151..155] into `annexes` just like real-world rough mapping
                return {
                    "data": ImportedProtocolIndexAnalysis(
                        detected_title="Étude longue",
                        global_summary="Document long analysé.",
                        mapped_sections=[
                            ImportedSectionIndexMapping(section_id="intro_contexte", block_ids=[0, 1]),
                            ImportedSectionIndexMapping(section_id="annexes", block_ids=[151, 152, 153, 154, 155]),
                        ],
                    ),
                    "model_used": "mock-seg",
                    "total_tokens": 100,
                }
            if schema is _AIBoundaryDecision:
                return {
                    "data": _AIBoundaryDecision(
                        found=True,
                        start_block_id=151,
                        end_block_id=153,
                        detected_title="ANNEXE 1 : FICHE D'ENQUETE",
                        confidence=0.94,
                        excluded_neighbor_annexes=["ANNEXE 2 : NOTICE D'INFORMATION"],
                    ),
                    "model_used": "mock-seg",
                    "total_tokens": 50,
                }
            if schema is _AICRFCompilationOut:
                return {
                    "data": _AICRFCompilationOut(
                        detected_title="## ANNEXE 1 : FICHE D'ENQUETE",
                        fields=[
                            _AICRFFieldOut(
                                code="A1",
                                section_title="SECTION A : SOCIODEMOGRAPHIE",
                                label="Âge révolu",
                                field_type="number",
                                unit="ans",
                                required=True,
                            ),
                            _AICRFFieldOut(
                                code="A2",
                                section_title="SECTION A : SOCIODEMOGRAPHIE",
                                label="Sexe",
                                field_type="single_choice",
                                options=["Masculin", "Féminin"],
                            ),
                            _AICRFFieldOut(
                                code="B1",
                                section_title="SECTION B : PARACLINIQUE",
                                label="Glycémie à jeun",
                                field_type="number",
                                unit="g/dL",
                            ),
                            _AICRFFieldOut(
                                code="B2",
                                section_title="SECTION B : PARACLINIQUE",
                                label="Diabète connu",
                                field_type="dichotomous",
                                options=["Oui", "Non"],
                            ),
                        ],
                    ),
                    "model_used": "gemini-3.5-flash-lite",
                    "total_tokens": 120,
                }
            raise AssertionError(f"Unexpected schema: {schema}")

        with patch("app.services.importer.gemini_router.generate_structured", side_effect=fake_generate_structured), patch(
            "app.services.crf_extractor.gemini_router.generate_structured", side_effect=fake_generate_structured
        ), patch(
            "app.services.importer.extract_images_from_uploaded_document", return_value=[]
        ):
            report = analyze_and_import_protocol(
                project,
                "protocole_long.md",
                raw_bytes,
                import_mode="skeleton",
            )

        # Verify tail block `[B151]` was present in the importer catalog prompt (not truncated at 140!)
        seg_prompts = [c for s, c in seen_prompts if s is ImportedProtocolIndexAnalysis]
        self.assertTrue(seg_prompts and "[B151]" in seg_prompts[0])

        self.assertTrue(report["crf_detected"])
        self.assertEqual(report["crf_fields_count"], 4)
        self.assertIn("ANNEXE 1 : FICHE D'ENQUETE", project.sections["annexe_fiche_collecte"].content_markdown)
        self.assertNotIn("NOTICE D'INFORMATION", project.sections["annexe_fiche_collecte"].content_markdown)
        self.assertIn("NOTICE D'INFORMATION", project.sections["annexes"].content_markdown)
        self.assertNotIn("Glycémie à jeun", project.sections["annexes"].content_markdown)

    def test_hybrid_compilation_word_tables_slash_choices_and_cames_pii_filter(self):
        project = _make_project("hybrid-compile")
        project.variables = [
            VariableItem(name="Score d'Apgar à 5 min", category="SECTION C : NOUVEAU-NÉ", measurement_scale="Quantitative"),
        ]
        raw_crf = """ANNEXE 1 : FICHE DE COLLECTE
SECTION A : IDENTIFICATION ET SOCIODÉMOGRAPHIE
A0. Nom complet et téléphone du patient : ............
A1. Âge du patient : ......... ans
A2. Sexe : Masculin / Féminin

SECTION B : PARAMÈTRES CLINIQUES
| Code | Variable | Modalités / Valeur |
|---|---|---|
| B1 | Pression artérielle systolique | ........ mmHg |
| B2 | Comorbidité hypertensive | □ Oui  □ Non |
"""
        res = compile_crf_fields_hybrid(project, raw_crf, use_ai=False)
        fields = res["fields"]
        labels = [f.label for f in fields]
        self.assertNotIn("Nom complet et téléphone du patient", labels)
        self.assertIn("Âge du patient", labels)
        self.assertIn("Sexe", labels)
        self.assertIn("Pression artérielle systolique", labels)
        self.assertIn("Comorbidité hypertensive", labels)

        sexe = next(f for f in fields if f.label == "Sexe")
        self.assertEqual(sexe.field_type, "single_choice")
        self.assertEqual(sexe.options, ["Masculin", "Féminin"])

        pas = next(f for f in fields if f.label == "Pression artérielle systolique")
        self.assertEqual(pas.field_type, "number")
        self.assertEqual(pas.unit, "mmHg")

        hta = next(f for f in fields if f.label == "Comorbidité hypertensive")
        self.assertEqual(hta.field_type, "dichotomous")

        warnings_joined = " | ".join(res["cames_warnings"])
        self.assertIn("Anonymat CAMES", warnings_joined)
        self.assertIn("Score d'Apgar", warnings_joined)

    def test_reconcile_crf_fields_preserves_existing_patient_record_field_ids(self):
        existing = [
            CRFFieldSpec(
                field_id="cust_a1_age_uid",
                code="A1",
                section_title="SECTION A",
                label="Âge du patient",
                field_type="number",
                unit="ans",
            ),
            CRFFieldSpec(
                field_id="cust_a2_sexe_uid",
                code="A2",
                section_title="SECTION A",
                label="Sexe",
                field_type="single_choice",
                options=["M", "F"],
            ),
        ]
        incoming = [
            CRFFieldSpec(
                field_id="new_slug_age",
                code="A1",
                section_title="SECTION A",
                label="Âge du patient",
                field_type="number",
                unit="ans",
            ),
            CRFFieldSpec(
                field_id="new_slug_sexe",
                code="A2",
                section_title="SECTION A",
                label="Sexe",
                field_type="single_choice",
                options=["Masculin", "Féminin"],
            ),
            CRFFieldSpec(
                field_id="b1_glycemie",
                code="B1",
                section_title="SECTION B",
                label="Glycémie",
                field_type="number",
                unit="g/dL",
            ),
        ]

        reconciled, report = reconcile_crf_fields(existing, incoming)
        self.assertEqual(reconciled[0].field_id, "cust_a1_age_uid")
        self.assertEqual(reconciled[1].field_id, "cust_a2_sexe_uid")
        self.assertEqual(reconciled[1].options, ["Masculin", "Féminin"])
        self.assertEqual(reconciled[2].field_id, "b1_glycemie")
        self.assertEqual(report["preserved_count"], 1)
        self.assertEqual(report["modified_count"], 1)
        self.assertEqual(report["added_count"], 1)
        self.assertEqual(report["removed_count"], 0)

        # PatientRecord storing values under original custom IDs remains 100% compatible
        rec = PatientRecord(patient_code="PAT-001", values={"cust_a1_age_uid": "52", "cust_a2_sexe_uid": "F"})
        self.assertIn(reconciled[0].field_id, rec.values)
        self.assertIn(reconciled[1].field_id, rec.values)

    def test_extract_and_sync_crf_from_internal_library_source(self):
        project = _make_project("internal-lib-crf")
        project.internal_sources = [
            InternalSourceDocument(
                source_id="src-these-1",
                filename="these_glycemie.docx",
                mapped_sections=["annexe_fiche_collecte"],
            )
        ]
        project.sections["annexe_fiche_collecte"].internal_library = [
            InternalLibraryEntry(
                source_id="src-these-1",
                source_filename="these_glycemie.docx",
                content_markdown=(
                    "FICHE DE COLLECTE DES DONNEES\n"
                    "SECTION A : SOCIODEMOGRAPHIE\n"
                    "- A1. Âge révolu : ......... ans\n"
                    "- A2. Diabète connu : □ Oui  □ Non\n\n"
                    "ANNEXE 2 : NOTICE D'INFORMATION\n"
                    "Texte informatif exclu."
                ),
            )
        ]

        out = extract_and_sync_crf_for_project(
            project,
            source_mode="internal_source",
            source_id="src-these-1",
            use_ai=False,
        )
        self.assertTrue(out["detection"]["found"])
        self.assertEqual(out["sync_report"]["source_origin"], "internal_source")
        self.assertGreaterEqual(len(project.crf_fields), 2)
        self.assertNotIn("NOTICE D'INFORMATION", project.sections["annexe_fiche_collecte"].content_markdown)
        self.assertIn("Diabète connu", project.sections["annexe_fiche_collecte"].content_markdown)


if __name__ == "__main__":
    unittest.main()
