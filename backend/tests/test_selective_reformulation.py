from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas.protocol import ProtocolProject, SectionState, StepStatus
from app.services.selective_reformulation import (
    SelectionTransformOutput,
    SelectivePatchItem,
    SmartReformulateOutput,
    smart_reformulate_section,
    transform_selected_text,
)


def _sample_project() -> ProtocolProject:
    sec_text = (
        "Le paludisme demeure un problème majeur de santé publique au Cameroun.\n\n"
        "Notre étude vise à décrire les facteurs associés à la mortalité hospitalière.\n\n"
        "Les données seront recueillies de manière anonyme."
    )
    return ProtocolProject(
        project_id="proj-test-sel",
        name="Test Selective",
        sections={
            "intro_contexte": SectionState(
                section_id="intro_contexte",
                status=StepStatus.IN_PROGRESS,
                content_markdown=sec_text,
                audit_notes=["Préciser la période d'étude dans le deuxième paragraphe."],
            )
        },
    )


class TestSelectiveReformulation(unittest.TestCase):
    def test_selective_patch_preserves_untouched_paragraphs(self):
        proj = _sample_project()
        mock_dto = SmartReformulateOutput(
            strategy="selective_patch",
            patches=[
                SelectivePatchItem(
                    target_text="Notre étude vise à décrire les facteurs associés à la mortalité hospitalière.",
                    replacement_text="Notre étude analytique vise à identifier les déterminants de la mortalité hospitalière à Yaoundé.",
                    reason="Précision méthodologique demandée.",
                )
            ],
            summary="Seul le deuxième paragraphe a été reformulé.",
        )
        with patch(
            "app.services.selective_reformulation.gemini_router.generate_structured",
            return_value={"data": mock_dto, "model_used": "gemini-3.5-flash-lite"},
        ):
            res = smart_reformulate_section(
                proj,
                section_id="intro_contexte",
                instruction="Rends l'objectif plus précis",
            )
        self.assertEqual(res["strategy"], "selective_patch")
        self.assertIn("Le paludisme demeure un problème majeur de santé publique au Cameroun.", res["proposed_markdown"])
        self.assertIn("déterminants de la mortalité hospitalière à Yaoundé", res["proposed_markdown"])
        self.assertIn("Les données seront recueillies de manière anonyme.", res["proposed_markdown"])
        self.assertEqual(len(res["patches"]), 1)

    def test_append_complement_keeps_original_intact(self):
        proj = _sample_project()
        mock_dto = SmartReformulateOutput(
            strategy="append_complement",
            append_markdown="Selon l'OMS, la région d'Afrique subsaharienne concentre plus de 90 % des cas mondiaux.",
            summary="Complément épidémiologique ajouté en fin de section.",
        )
        with patch(
            "app.services.selective_reformulation.gemini_router.generate_structured",
            return_value={"data": mock_dto, "model_used": "gemini-3.5-flash-lite"},
        ):
            res = smart_reformulate_section(
                proj,
                section_id="intro_contexte",
                instruction="Ajoute une donnée OMS sans toucher à mon texte",
                mode="complement",
            )
        self.assertEqual(res["strategy"], "append_complement")
        self.assertTrue(res["proposed_markdown"].startswith(res["original_markdown"]))
        self.assertIn("Selon l'OMS", res["proposed_markdown"])

    def test_transform_selected_text_splices_exact_passage(self):
        proj = _sample_project()
        mock_dto = SelectionTransformOutput(
            replacement_text="Les données cliniques et paracliniques seront collectées de façon strictement anonymisée.",
            summary="Passage sur l'anonymat rendu plus rigoureux.",
        )
        with patch(
            "app.services.selective_reformulation.gemini_router.generate_structured",
            return_value={"data": mock_dto, "model_used": "gemini-3.5-flash-lite"},
        ):
            res = transform_selected_text(
                proj,
                section_id="intro_contexte",
                selected_text="Les données seront recueillies de manière anonyme.",
                action="reformulate",
            )
        self.assertTrue(res["matched_in_section"])
        self.assertIn("Le paludisme demeure un problème majeur", res["proposed_markdown"])
        self.assertIn("strictement anonymisée", res["proposed_markdown"])
        self.assertNotIn("recueillies de manière anonyme", res["proposed_markdown"])


if __name__ == "__main__":
    unittest.main()
