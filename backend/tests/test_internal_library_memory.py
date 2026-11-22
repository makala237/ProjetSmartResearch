from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import (
    InternalLibraryEntry,
    InternalSourceDocument,
    ProtocolProject,
    ReferenceItem,
    SectionState,
    StepStatus,
)
from app.services.drafting import DraftSectionOutput, draft_section_with_ai
from app.services.importer import (
    ImportedProtocolIndexAnalysis,
    ImportedSectionIndexMapping,
    SourceCitabilityMetadata,
    _fallback_detect_citability,
    analyze_and_import_protocol,
    remove_internal_source_from_project,
)


def _make_full_project(project_id: str = "test-internal-lib") -> ProtocolProject:
    sections = {
        spec.id: SectionState(section_id=spec.id, status=StepStatus.PENDING)
        for spec in FMSB_SECTIONS
    }
    return ProtocolProject(
        project_id=project_id,
        name="Prévalence et facteurs associés à l'hypertension artérielle à Yaoundé",
        research_idea="Étude transversale analytique sur l'HTA non contrôlée à l'HCY",
        study_type="Étude transversale analytique",
        language="fr",
        sections=sections,
    )


class TestInternalLibraryMemory(unittest.TestCase):
    def test_internal_source_preserves_existing_sections_and_populates_library(self):
        project = _make_full_project()
        original_intro = "Rédaction originale de l'étudiant sur le contexte de l'HTA au Cameroun."
        original_method = "Étude transversale analytique déjà rédigée par l'utilisateur."
        project.sections["intro_contexte"].content_markdown = original_intro
        project.sections["intro_contexte"].content_html = f"<p>{original_intro}</p>"
        project.sections["intro_contexte"].status = StepStatus.VALIDATED
        project.sections["chap3_type_site_duree"].content_markdown = original_method
        project.sections["chap3_type_site_duree"].content_html = f"<p>{original_method}</p>"
        project.sections["chap3_type_site_duree"].status = StepStatus.IN_PROGRESS

        sample_doc = (
            "1. INTRODUCTION ET CONTEXTE\n\n"
            "Au Cameroun, la prévalence hospitalière de l'HTA dépasse 30 % chez l'adulte.\n\n"
            "| Indicateur | Valeur |\n"
            "|---|---|\n"
            "| Prévalence HTA | 31,4 % |\n\n"
            "2. METHODOLOGIE - TYPE D'ETUDE\n\n"
            "Une enquête descriptive et analytique de six mois a été menée à l'Hôpital Central de Yaoundé.\n"
        ).encode("utf-8")

        mapping_out = ImportedProtocolIndexAnalysis(
            detected_title="HTA Yaoundé",
            detected_study_type="Transversale",
            global_summary="Source interne segmentée sans écraser les sections.",
            mapped_sections=[
                ImportedSectionIndexMapping(
                    section_id="intro_contexte",
                    block_ids=[0, 1, 2],
                    diagnostic_note="Données épidémiologiques avec tableau.",
                ),
                ImportedSectionIndexMapping(
                    section_id="chap3_type_site_duree",
                    block_ids=[3, 4],
                    diagnostic_note="Design hospitalier comparatif.",
                ),
            ],
        )
        citability_out = SourceCitabilityMetadata(
            citable=False,
            source_kind="unreferenced_draft",
            title="Notes internes HTA",
        )

        def fake_generate_structured(*, role, contents, schema, **kwargs):
            if schema is ImportedProtocolIndexAnalysis:
                return {"data": mapping_out, "model_used": "mock-seg", "total_tokens": 100}
            if schema is SourceCitabilityMetadata:
                return {"data": citability_out, "model_used": "mock-audit", "total_tokens": 50}
            raise AssertionError(f"Unexpected schema {schema}")

        with patch("app.services.importer.gemini_router.generate_structured", side_effect=fake_generate_structured), patch(
            "app.services.importer.extract_images_from_uploaded_document", return_value=[]
        ):
            report = analyze_and_import_protocol(
                project,
                "notes_brouillon.txt",
                sample_doc,
                import_mode="internal_source",
            )

        # Existing drafted sections MUST NOT be overwritten
        self.assertEqual(project.sections["intro_contexte"].content_markdown, original_intro)
        self.assertEqual(project.sections["intro_contexte"].status, StepStatus.VALIDATED)
        self.assertEqual(project.sections["chap3_type_site_duree"].content_markdown, original_method)
        self.assertEqual(project.sections["chap3_type_site_duree"].status, StepStatus.IN_PROGRESS)

        # Internal source and per-section library populated cleanly (including extracted table)
        self.assertEqual(report["import_mode"], "internal_source")
        self.assertEqual(report["internal_entries_added"], 2)
        self.assertFalse(report["citable"])
        self.assertEqual(len(project.internal_sources), 1)
        self.assertEqual(project.internal_sources[0].filename, "notes_brouillon.txt")

        intro_lib = project.sections["intro_contexte"].internal_library
        self.assertEqual(len(intro_lib), 1)
        self.assertIn("30 %", intro_lib[0].content_markdown)
        self.assertEqual(len(intro_lib[0].tables_markdown), 1)
        self.assertIn("Prévalence HTA", intro_lib[0].tables_markdown[0])

    def test_citability_detection_citable_thesis_vs_unreferenced_draft(self):
        # 1) Deterministic fallback verifies thesis/article vs plain draft detection
        thesis_front = (
            "UNIVERSITE DE YAOUNDE I\n"
            "FACULTE DE MEDECINE ET DES SCIENCES BIOMEDICALES\n"
            "THESE DE DOCTORAT EN MEDECINE\n"
            "Facteurs de risque de pré-éclampsie sévère à l'Hôpital Gynéco-Obstétrique\n"
            "Soutenue en 2024\n"
        )
        meta_thesis_fb = _fallback_detect_citability("these_mbarga_2024.pdf", thesis_front)
        self.assertTrue(meta_thesis_fb.citable)
        self.assertEqual(meta_thesis_fb.source_kind, "thesis")
        self.assertEqual(meta_thesis_fb.year, 2024)

        plain_draft_front = "Notes personnelles de protocole\nObjectif : inclure 120 patients sur 3 mois."
        meta_draft_fb = _fallback_detect_citability("brouillon_notes.txt", plain_draft_front)
        self.assertFalse(meta_draft_fb.citable)
        self.assertEqual(meta_draft_fb.source_kind, "unreferenced_draft")
        self.assertEqual(meta_draft_fb.vancouver_citation, "")

        # 2) Full import with citable thesis creates a verified Vancouver ReferenceItem linked via ref_id
        project_citable = _make_full_project("proj-citable")
        doc_bytes = (
            "1. INTRODUCTION\n\n"
            "UNIVERSITE DE YAOUNDE I - FACULTE DE MEDECINE\n"
            "Thèse de Doctorat en Médecine par Dr Mbarga J., 2024.\n"
            "La pré-éclampsie demeure une cause majeure de morbi-mortalité maternelle au Cameroun.\n"
        ).encode("utf-8")

        mapping_out = ImportedProtocolIndexAnalysis(
            global_summary="Thèse citable indexée dans la bibliothèque interne.",
            mapped_sections=[
                ImportedSectionIndexMapping(section_id="intro_contexte", block_ids=[0, 1])
            ],
        )
        citability_out = SourceCitabilityMetadata(
            citable=True,
            source_kind="thesis",
            authors=["Mbarga J"],
            title="Facteurs associés à la pré-éclampsie sévère à Yaoundé",
            year=2024,
            institution_or_journal="Université de Yaoundé I, FMSB",
            vancouver_citation="Mbarga J. Facteurs associés à la pré-éclampsie sévère à Yaoundé [Thèse de Doctorat en Médecine]. Yaoundé : Université de Yaoundé I, FMSB ; 2024.",
        )

        def fake_gen_citable(*, role, contents, schema, **kwargs):
            if schema is ImportedProtocolIndexAnalysis:
                return {"data": mapping_out, "model_used": "mock-seg", "total_tokens": 80}
            if schema is SourceCitabilityMetadata:
                return {"data": citability_out, "model_used": "mock-audit", "total_tokens": 60}
            raise AssertionError(schema)

        with patch("app.services.importer.gemini_router.generate_structured", side_effect=fake_gen_citable), patch(
            "app.services.importer.extract_images_from_uploaded_document", return_value=[]
        ):
            report = analyze_and_import_protocol(
                project_citable,
                "these_mbarga_2024.md",
                doc_bytes,
                import_mode="internal_source",
            )

        self.assertTrue(report["citable"])
        self.assertIn("Mbarga J", report["vancouver_citation"])
        self.assertEqual(len(project_citable.references), 1)
        ref = project_citable.references[0]
        self.assertEqual(ref.source_platform, "internal_library")
        self.assertTrue(ref.verified)
        src = project_citable.internal_sources[0]
        self.assertTrue(src.citable)
        self.assertEqual(src.ref_id, ref.ref_id)
        entry = project_citable.sections["intro_contexte"].internal_library[0]
        self.assertTrue(entry.citable)
        self.assertEqual(entry.ref_id, ref.ref_id)

        # 3) Plain unreferenced draft must NEVER fabricate a Vancouver reference
        project_uncitable = _make_full_project("proj-uncitable")
        citability_uncitable = SourceCitabilityMetadata(
            citable=False,
            source_kind="unreferenced_draft",
            title="Brouillon sans auteur",
        )

        def fake_gen_uncitable(*, role, contents, schema, **kwargs):
            if schema is ImportedProtocolIndexAnalysis:
                return {"data": mapping_out, "model_used": "mock-seg", "total_tokens": 80}
            if schema is SourceCitabilityMetadata:
                return {"data": citability_uncitable, "model_used": "mock-audit", "total_tokens": 60}
            raise AssertionError(schema)

        with patch("app.services.importer.gemini_router.generate_structured", side_effect=fake_gen_uncitable), patch(
            "app.services.importer.extract_images_from_uploaded_document", return_value=[]
        ):
            report_uncit = analyze_and_import_protocol(
                project_uncitable,
                "mon_brouillon.txt",
                doc_bytes,
                import_mode="internal_source",
            )

        self.assertFalse(report_uncit["citable"])
        self.assertEqual(len(project_uncitable.references), 0)
        self.assertIsNone(project_uncitable.internal_sources[0].ref_id)
        self.assertFalse(project_uncitable.sections["intro_contexte"].internal_library[0].citable)

    def test_internal_first_orchestration_and_force_external_enrichment(self):
        # 1) Sufficient internal library for chap3_type_site_duree (min_words=200 => threshold 70w, min_refs=0)
        project = _make_full_project("proj-orch")
        rich_internal_text = " ".join(
            [
                "Il s'agit d'une étude transversale descriptive et analytique réalisée au service de médecine interne "
                "de l'Hôpital Central de Yaoundé sur une période consécutive de six mois allant de janvier à juin."
            ]
            * 4
        )
        project.sections["chap3_type_site_duree"].internal_library = [
            InternalLibraryEntry(
                source_id="src-1",
                source_filename="protocole_interne.docx",
                source_kind="unreferenced_draft",
                citable=False,
                content_markdown=rich_internal_text,
            )
        ]

        captured_prompts = []

        def fake_draft_structured(*, role, contents, schema, **kwargs):
            captured_prompts.append(contents)
            return {
                "data": DraftSectionOutput(
                    content_markdown="Étude transversale analytique conduite à l'HCY sur six mois selon les données internes.",
                    cited_order_indices=[],
                    assistant_commentary="Rédaction basée sur la bibliothèque interne.",
                ),
                "model_used": "gemini-3.5-flash-lite",
                "total_tokens": 250,
            }

        with patch(
            "app.services.section_orchestrator.gemini_router.run_decision_1",
            return_value={
                "scores": {
                    "internal_library_sufficient": 0.91,
                    "call_antigravity_research": 0.12,
                    "call_antigravity_images": 0.0,
                },
                "total_tokens": 60,
            },
        ), patch(
            "app.services.section_orchestrator.execute_antigravity_section_mission",
            return_value={"added_references_count": 2, "added_images_count": 0},
        ) as mock_ext_research, patch(
            "app.services.drafting.gemini_router.generate_structured",
            side_effect=fake_draft_structured,
        ), patch(
            "app.services.drafting.update_section_working_memory",
            return_value={"updated": True},
        ):
            # Case A: Internal library sufficient => external APIs skipped, can_enrich_externally=True
            res_internal = draft_section_with_ai(
                project,
                "chap3_type_site_duree",
                force_external_research=False,
            )
            mock_ext_research.assert_not_called()
            self.assertTrue(res_internal["used_internal_library_only"])
            self.assertTrue(res_internal["can_enrich_externally"])
            self.assertEqual(res_internal["internal_library_entries_used"], 1)
            self.assertIn("protocole_interne.docx", captured_prompts[-1])

            # Case B: User clicks "Enrichir via PubMed / AJOL / OpenAlex" => force_external_research=True
            res_forced = draft_section_with_ai(
                project,
                "chap3_type_site_duree",
                force_external_research=True,
            )
            mock_ext_research.assert_called_once()
            self.assertFalse(res_forced["used_internal_library_only"])
            self.assertFalse(res_forced["can_enrich_externally"])

        # Case C: Insufficient internal library on intro_contexte (min_words=350, min_refs=2) => external search automatic
        project.sections["intro_contexte"].internal_library = [
            InternalLibraryEntry(
                source_id="src-short",
                source_filename="court.txt",
                source_kind="unreferenced_draft",
                citable=False,
                content_markdown="Note très courte insuffisante.",
            )
        ]
        with patch(
            "app.services.section_orchestrator.gemini_router.run_decision_1",
            return_value={
                "scores": {
                    "internal_library_sufficient": 0.10,
                    "call_antigravity_research": 0.95,
                    "call_antigravity_images": 0.0,
                },
                "total_tokens": 55,
            },
        ), patch(
            "app.services.section_orchestrator.execute_antigravity_section_mission",
            return_value={"added_references_count": 3, "added_images_count": 0},
        ) as mock_ext_auto, patch(
            "app.services.drafting.gemini_router.generate_structured",
            side_effect=fake_draft_structured,
        ), patch(
            "app.services.drafting.update_section_working_memory",
            return_value={"updated": True},
        ):
            res_auto_ext = draft_section_with_ai(project, "intro_contexte")
            mock_ext_auto.assert_called_once()
            self.assertFalse(res_auto_ext["used_internal_library_only"])

    def test_remove_internal_source_from_project(self):
        project = _make_full_project("proj-del")
        project.internal_sources = [
            InternalSourceDocument(source_id="src-keep", filename="keep.pdf"),
            InternalSourceDocument(source_id="src-drop", filename="drop.docx"),
        ]
        project.sections["intro_contexte"].internal_library = [
            InternalLibraryEntry(source_id="src-keep", source_filename="keep.pdf", content_markdown="A"),
            InternalLibraryEntry(source_id="src-drop", source_filename="drop.docx", content_markdown="B"),
        ]
        project.sections["chap3_type_site_duree"].internal_library = [
            InternalLibraryEntry(source_id="src-drop", source_filename="drop.docx", content_markdown="C"),
        ]

        removed = remove_internal_source_from_project(project, "src-drop")
        self.assertTrue(removed)
        self.assertEqual([s.source_id for s in project.internal_sources], ["src-keep"])
        self.assertEqual(
            [e.source_id for e in project.sections["intro_contexte"].internal_library],
            ["src-keep"],
        )
        self.assertEqual(len(project.sections["chap3_type_site_duree"].internal_library), 0)
        self.assertFalse(remove_internal_source_from_project(project, "does-not-exist"))


if __name__ == "__main__":
    unittest.main()
