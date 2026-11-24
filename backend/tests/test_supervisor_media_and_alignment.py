from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.model_catalog import normalize_model_routing_config
from app.schemas.protocol import ProtocolProject, SectionState, StepStatus, SupervisorDirectiveItem
from app.services.gemini_client import (
    GeminiRouter,
    reset_ai_routing_policy,
    set_ai_routing_policy,
)
from app.services.supervisor_media import (
    AlignmentPatchItem,
    ExtractedDirectiveItem,
    ExtractedMediaDirectivesOutput,
    SupervisorAlignmentOutput,
    align_text_with_supervisor_directives,
    extract_directives_from_media,
)


def _sample_project() -> ProtocolProject:
    return ProtocolProject(
        project_id="proj-sup-media",
        name="Test Supervisor Media",
        sections={
            "intro_contexte": SectionState(
                section_id="intro_contexte",
                status=StepStatus.IN_PROGRESS,
                content_markdown="Le paludisme touche toute la population adulte au Cameroun.",
            )
        },
    )


class TestSupervisorMediaAndAlignment(unittest.TestCase):
    def tearDown(self):
        reset_ai_routing_policy()

    def test_supervisor_media_default_and_admin_override_routing(self):
        router = GeminiRouter()
        reset_ai_routing_policy()
        default_chain, _ = router._resolve_candidates("supervisor_media")
        self.assertEqual(default_chain[0], "gemini-3.5-flash-lite")

        cfg = normalize_model_routing_config(
            {
                "enabled": True,
                "supervisor_media_models": [
                    {"model": "gemini-3.8-flash", "max_rpd": 10, "max_tpm": 250000},
                    {"model": "gemini-3.5-flash-lite", "max_rpd": 20, "max_tpm": 250000},
                ],
            }
        )
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["supervisor_media_models"][0]["model"], "gemini-3.8-flash")

        set_ai_routing_policy(
            prefer_fallback_35=False,
            supervisor_media_chain=["gemini-3.8-flash", "gemini-3.5-flash-lite"],
        )
        override_chain, _ = router._resolve_candidates("supervisor_media")
        self.assertEqual(override_chain, ["gemini-3.8-flash", "gemini-3.5-flash-lite"])

    def test_extract_directives_from_video_media(self):
        proj = _sample_project()
        mock_dto = ExtractedMediaDirectivesOutput(
            summary="L'encadreur demande de cibler uniquement les enfants de moins de 5 ans au CHUY.",
            directives=[
                ExtractedDirectiveItem(
                    text="Restreindre la population cible aux enfants de moins de 5 ans au CHUY.",
                    target_section_id="intro_contexte",
                    timestamp_hint="01:12",
                )
            ],
        )
        with patch(
            "app.services.supervisor_media.gemini_router._get_client",
            return_value=MagicMock(),
        ), patch(
            "app.services.supervisor_media.gemini_router.generate_structured",
            return_value={"data": mock_dto, "model_used": "gemini-3.5-flash-lite"},
        ):
            res = extract_directives_from_media(
                proj,
                file_bytes=b"\x00\x00\x00\x18ftypmp42",
                filename="feedback_encadreur.mp4",
                content_type="video/mp4",
                section_id="intro_contexte",
            )
        self.assertEqual(res["media_kind"], "video")
        self.assertEqual(res["created_count"], 1)
        self.assertEqual(len(proj.supervisor_directives), 1)
        d = proj.supervisor_directives[0]
        self.assertEqual(d.source, "video")
        self.assertEqual(d.section_id, "intro_contexte")
        self.assertEqual(d.timestamp_hint, "01:12")

    def test_alignment_gate_skips_when_empty_and_patches_when_directive_exists(self):
        proj = _sample_project()
        # 1. No directives -> 0 LLM calls
        with patch("app.services.supervisor_media.gemini_router.generate_structured") as mock_gen:
            res_empty = align_text_with_supervisor_directives(
                proj, "intro_contexte", "Texte initial inchangé."
            )
        mock_gen.assert_not_called()
        self.assertFalse(res_empty["adjusted"])
        self.assertEqual(res_empty["content_markdown"], "Texte initial inchangé.")

        # 2. Active video directive -> surgical patch applied before display
        proj.supervisor_directives.append(
            SupervisorDirectiveItem(
                text="Cibler uniquement les enfants de moins de 5 ans au CHUY.",
                source="video",
                section_id="intro_contexte",
                timestamp_hint="01:12",
                active=True,
            )
        )
        mock_align = SupervisorAlignmentOutput(
            is_compliant=False,
            patches=[
                AlignmentPatchItem(
                    target_text="toute la population adulte au Cameroun",
                    replacement_text="les enfants de moins de 5 ans pris en charge au CHUY",
                    reason="Directive vidéo encadreur (01:12)",
                )
            ],
            summary="Aligné sur la directive vidéo de l'encadreur (population pédiatrique CHUY).",
        )
        with patch(
            "app.services.supervisor_media.gemini_router.generate_structured",
            return_value={"data": mock_align, "model_used": "gemini-3.5-flash-lite"},
        ):
            res_aligned = align_text_with_supervisor_directives(
                proj,
                "intro_contexte",
                "Le paludisme touche toute la population adulte au Cameroun.",
            )
        self.assertTrue(res_aligned["adjusted"])
        self.assertIn("enfants de moins de 5 ans pris en charge au CHUY", res_aligned["content_markdown"])
        self.assertIsInstance(res_aligned["summary"], str)


if __name__ == "__main__":
    unittest.main()
