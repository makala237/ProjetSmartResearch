from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.model_catalog import (
    GEMINI_LIVE_MODEL_CATALOG,
    normalize_model_routing_config,
)
from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import (
    ProtocolProject,
    SectionState,
    StepStatus,
    SupervisorDirectiveItem,
)
from app.services.copilot_service import (
    CopilotTurnOutput,
    format_supervisor_directives_block,
    restore_section_version,
    run_copilot_chat,
    snapshot_section_version,
)
from app.services.gemini_client import (
    GeminiRouter,
    reset_ai_routing_policy,
    set_ai_routing_policy,
)


def _make_project() -> ProtocolProject:
    sections = {
        spec.id: SectionState(section_id=spec.id, status=StepStatus.PENDING)
        for spec in FMSB_SECTIONS
    }
    return ProtocolProject(
        project_id="test-copilot-p1",
        name="Prévalence de l'hypertension artérielle à Yaoundé",
        research_idea="Étude transversale analytique en milieu hospitalier",
        study_type="Étude transversale analytique",
        language="fr",
        sections=sections,
    )


class TestAssistantSmartResearchPhase1(unittest.TestCase):
    def tearDown(self) -> None:
        reset_ai_routing_policy()

    def test_live_catalog_and_copilot_routing_normalization(self) -> None:
        live_ids = {m["id"] for m in GEMINI_LIVE_MODEL_CATALOG}
        self.assertIn("gemini-3.8-live", live_ids)
        self.assertIn("gemini-2.5-flash-native-audio-latest", live_ids)

        # Backward compatibility: omitting copilot arrays keeps exact minimal keys
        base_cfg = normalize_model_routing_config(
            {
                "enabled": True,
                "models": [{"model": "gemini-3.5-flash-lite", "max_rpd": 20, "max_tpm": 250000}],
            }
        )
        self.assertEqual(
            base_cfg,
            {
                "enabled": True,
                "models": [{"model": "gemini-3.5-flash-lite", "max_rpd": 20, "max_tpm": 250000}],
            },
        )

        # Extended config supports independent copilot_text_models and copilot_voice_models
        ext_cfg = normalize_model_routing_config(
            {
                "enabled": True,
                "models": [{"model": "gemini-3.5-flash-lite", "max_rpd": 20, "max_tpm": 250000}],
                "copilot_text_models": [{"model": "gemini-3.8-flash", "max_rpd": 15}],
                "copilot_voice_models": [{"model": "gemini-3.8-live", "max_rpd": 10}],
            }
        )
        self.assertIsNotNone(ext_cfg)
        self.assertEqual(ext_cfg["copilot_text_models"][0]["model"], "gemini-3.8-flash")
        self.assertEqual(ext_cfg["copilot_voice_models"][0]["model"], "gemini-3.8-live")

    def test_gemini_router_copilot_text_and_voice_resolution(self) -> None:
        router = GeminiRouter()
        reset_ai_routing_policy()

        text_default, _ = router._resolve_candidates("copilot_text")
        self.assertEqual(text_default[0], "gemini-3.8-flash")
        self.assertEqual(
            router.resolve_copilot_voice_models(),
            ["gemini-3.8-live", "gemini-2.5-flash-native-audio-latest"],
        )

        set_ai_routing_policy(
            prefer_fallback_35=False,
            custom_model_chain=["gemini-3.5-flash-lite"],
            custom_chain_enabled=True,
            copilot_text_chain=["gemini-3.5-flash", "gemini-3.8-flash"],
            copilot_voice_chain=["gemini-3.8-live-extended-thinking", "gemini-3.8-live"],
        )
        text_custom, _ = router._resolve_candidates("copilot_text")
        self.assertEqual(text_custom, ["gemini-3.5-flash", "gemini-3.8-flash"])
        self.assertEqual(
            router.resolve_copilot_voice_models(),
            ["gemini-3.8-live-extended-thinking", "gemini-3.8-live"],
        )

    def test_version_snapshot_dedup_cap_and_safe_restore(self) -> None:
        project = _make_project()
        sec = project.sections["intro_contexte"]

        # Empty text does not create a snapshot
        self.assertIsNone(snapshot_section_version(sec, source="generation"))

        sec.content_markdown = "Définition initiale de l'HTA selon l'OMS."
        v1 = snapshot_section_version(sec, source="generation", note="V1 initiale")
        self.assertIsNotNone(v1)
        # Duplicate identical text is skipped
        self.assertIsNone(snapshot_section_version(sec, source="manual"))

        # Test 20-entry cap
        for i in range(25):
            sec.content_markdown = f"Révision numéro {i} avec contenu clinique détaillé."
            snapshot_section_version(sec, source="manual", note=f"Rev {i}")
        self.assertEqual(len(sec.versions), 20)

        # Restore earlier version snapshots the current state before rolling back
        target_vid = sec.versions[0].version_id
        target_text = sec.versions[0].content_markdown
        sec.content_markdown = "Texte actuel avant restauration."
        restored = restore_section_version(project, "intro_contexte", target_vid)
        self.assertEqual(restored.version_id, target_vid)
        self.assertEqual(sec.content_markdown, target_text)
        self.assertTrue(
            any(
                v.source == "restore" and v.content_markdown == "Texte actuel avant restauration."
                for v in sec.versions
            )
        )

    def test_supervisor_directives_formatting_filters_inactive(self) -> None:
        project = _make_project()
        project.supervisor_directives = [
            SupervisorDirectiveItem(text="Limiter la période d'étude à 2021-2025.", active=True),
            SupervisorDirectiveItem(text="Ancienne directive ignorée.", active=False),
        ]
        block = format_supervisor_directives_block(project)
        self.assertIn("Limiter la période d'étude à 2021-2025.", block)
        self.assertNotIn("Ancienne directive ignorée.", block)

    def test_copilot_chat_is_non_destructive_for_questions_and_proposals(self) -> None:
        project = _make_project()
        sec_id = "intro_contexte"
        original_text = "L'hypertension artérielle est définie selon l'OMS par une PAS >= 140 mmHg [1]."
        project.sections[sec_id].content_markdown = original_text

        fake_answer = {
            "data": CopilotTurnOutput(
                intent="answer",
                reply="Cette définition provient de la référence [1] de l'OMS.",
                proposed_markdown=None,
            ),
            "model_used": "gemini-3.8-flash",
        }
        with patch("app.services.copilot_service.gemini_router.generate_structured", return_value=fake_answer):
            res_q = run_copilot_chat(
                project,
                message="D'où vient cette définition ?",
                active_module="module1",
                active_section_id=sec_id,
                mode="auto",
            )
        self.assertEqual(res_q["assistant_message"].intent, "answer")
        self.assertIsNone(res_q["assistant_message"].proposal)
        self.assertEqual(project.sections[sec_id].content_markdown, original_text)

        fake_edit = {
            "data": CopilotTurnOutput(
                intent="propose_edit",
                reply="Voici une version enrichie conservant votre définition initiale.",
                proposed_markdown=original_text + " Au Cameroun, sa prévalence augmente [1].",
                edit_summary="Ajout du contexte camerounais sans supprimer la définition.",
            ),
            "model_used": "gemini-3.8-flash",
        }
        with patch("app.services.copilot_service.gemini_router.generate_structured", return_value=fake_edit):
            res_e = run_copilot_chat(
                project,
                message="Compare et propose une amélioration sans effacer ma définition.",
                active_module="module1",
                active_section_id=sec_id,
                mode="auto",
            )
        self.assertEqual(res_e["assistant_message"].intent, "propose_edit")
        self.assertIsNotNone(res_e["assistant_message"].proposal)
        # Even for propose_edit, existing section content remains untouched until user clicks Apply
        self.assertEqual(project.sections[sec_id].content_markdown, original_text)


if __name__ == "__main__":
    unittest.main()
