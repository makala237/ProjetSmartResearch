from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import (
    CoherenceAlert,
    InternalLibraryEntry,
    ProtocolProject,
    ReferenceItem,
    SectionState,
    SectionVersionEntry,
    StepStatus,
    SupervisorDirectiveItem,
)
from app.services.copilot_live import build_live_system_instruction
from app.services.copilot_service import CopilotTurnOutput, run_copilot_chat
from app.services.copilot_tools import (
    CompareMergeOutput,
    tool_draft_compare_and_merge,
    tool_inspect_internal_library,
    tool_run_section_audit,
    tool_search_protocol_versions,
)


def _make_rich_project() -> ProtocolProject:
    sections = {
        spec.id: SectionState(section_id=spec.id, status=StepStatus.PENDING)
        for spec in FMSB_SECTIONS
    }
    sections["intro_contexte"].content_markdown = (
        "Le paludisme grave reste une urgence pédiatrique fréquente au Cameroun [1]."
    )
    sections["intro_contexte"].internal_library = [
        InternalLibraryEntry(
            source_id="src-1",
            source_filename="these_paludisme_yaounde.pdf",
            source_kind="thesis",
            citable=True,
            ref_id="ref-1",
            vancouver_citation="Kamga J. Paludisme grave à Yaoundé. Thèse FMSB 2023.",
            content_markdown="L'incidence hospitalière au CHUY atteignait 14,2 % chez les moins de 5 ans.",
        )
    ]
    sections["intro_contexte"].versions = [
        SectionVersionEntry(
            version_id="ver-old-1",
            source="generation",
            note="Ancienne définition complète",
            content_markdown="Définition OMS détaillée de la neuropaludisme grave avec critères de sévérité.",
            word_count=11,
        ),
        SectionVersionEntry(
            version_id="ver-new-2",
            source="manual",
            note="Brouillon écourté",
            content_markdown="Introduction courte sur le paludisme pédiatrique.",
            word_count=7,
        ),
    ]
    return ProtocolProject(
        project_id="test-phase3-tools",
        name="Aspects cliniques du paludisme grave pédiatrique au CHUY",
        study_type="Étude transversale analytique",
        language="fr",
        sections=sections,
        references=[
            ReferenceItem(
                ref_id="ref-1",
                order_index=1,
                title="Paludisme grave au Cameroun",
                vancouver_citation="Kamga J. Paludisme grave à Yaoundé. FMSB; 2023.",
            )
        ],
        coherence_alerts=[
            CoherenceAlert(
                alert_id="coh-1",
                axis="population",
                source_section_id="intro_contexte",
                source_section_title="Contexte",
                target_section_id="chap3_population_echantillon",
                target_section_title="Population",
                title="Tranche d'âge à harmoniser",
                message="Préciser < 5 ans dans les critères d'inclusion.",
                suggested_instruction="Uniformiser sur les enfants de moins de 5 ans.",
            )
        ],
        supervisor_directives=[
            SupervisorDirectiveItem(
                text="Se limiter exclusivement au service de pédiatrie du CHUY.",
                active=True,
            ),
            SupervisorDirectiveItem(
                text="Ancienne consigne désactivée.",
                active=False,
            ),
        ],
    )


class TestAssistantSmartResearchPhase3(unittest.TestCase):
    def test_tool_inspect_internal_library_finds_sources_and_refs(self) -> None:
        project = _make_rich_project()
        res = tool_inspect_internal_library(
            project,
            query="incidence hospitalière CHUY Kamga",
            active_section_id="intro_contexte",
        )
        self.assertEqual(res["tool"], "inspect_internal_library")
        self.assertGreaterEqual(len(res["matches"]), 1)
        filenames = [m["filename"] for m in res["matches"]]
        self.assertIn("these_paludisme_yaounde.pdf", filenames)
        self.assertIn("14,2 %", res["summary_text"])

    def test_tool_run_section_audit_is_read_only_with_alerts(self) -> None:
        project = _make_rich_project()
        before = project.sections["intro_contexte"].content_markdown
        res = tool_run_section_audit(project, active_section_id="intro_contexte")
        self.assertEqual(res["tool"], "run_section_audit")
        self.assertEqual(project.sections["intro_contexte"].content_markdown, before)
        self.assertEqual(len(res["coherence_alerts"]), 1)
        self.assertGreater(res["word_count"], 5)

    def test_tool_search_protocol_versions_ranks_matching_snapshot(self) -> None:
        project = _make_rich_project()
        res = tool_search_protocol_versions(
            project,
            active_section_id="intro_contexte",
            query="critères de sévérité OMS neuropaludisme",
        )
        self.assertEqual(res["best_version_id"], "ver-old-1")
        self.assertGreaterEqual(len(res["matches"]), 1)

    def test_tool_draft_compare_and_merge_is_sandboxed_and_non_destructive(self) -> None:
        project = _make_rich_project()
        original = project.sections["intro_contexte"].content_markdown

        def _fake_draft(sandbox_proj, section_id, user_instruction=None):
            # Mutate only the sandbox copy passed by tool_draft_compare_and_merge
            sandbox_proj.sections[section_id].content_markdown = "Draft sandboxed nouveau texte."
            return {
                "content_markdown": "Proposition générée en arrière-plan pour le CHUY [1].",
                "model_used": "gemini-3.5-flash-lite",
            }

        fake_merge = {
            "data": CompareMergeOutput(
                merged_markdown="Le paludisme grave reste une urgence pédiatrique fréquente au CHUY de Yaoundé [1].",
                comparison_summary="Définition initiale conservée et enrichie avec précision du site CHUY.",
            ),
            "model_used": "gemini-3.8-flash",
        }

        with (
            patch("app.services.drafting.draft_section_with_ai", side_effect=_fake_draft),
            patch(
                "app.services.copilot_tools.gemini_router.generate_structured",
                return_value=fake_merge,
            ),
        ):
            res = tool_draft_compare_and_merge(
                project,
                section_id="intro_contexte",
                instruction="Comparer et conserver la meilleure formulation",
            )

        # Crucial invariant: live project section was NEVER mutated
        self.assertEqual(project.sections["intro_contexte"].content_markdown, original)
        self.assertIn("CHUY", res["proposed_markdown"])
        self.assertIn("Définition initiale conservée", res["summary"])

    def test_copilot_chat_orchestrates_internal_tool_call(self) -> None:
        project = _make_rich_project()
        fake_turn = {
            "data": CopilotTurnOutput(
                intent="restore_version",
                reply="J'ai vérifié l'historique.",
                tool_call="search_protocol_versions",
                tool_query="critères sévérité OMS",
            ),
            "model_used": "gemini-3.8-flash",
        }
        with patch(
            "app.services.copilot_service.gemini_router.generate_structured",
            return_value=fake_turn,
        ):
            res = run_copilot_chat(
                project,
                message="Retrouve le passage supprimé avec les critères OMS de sévérité.",
                active_module="module1",
                active_section_id="intro_contexte",
                mode="auto",
            )
        msg = res["assistant_message"]
        self.assertEqual(msg.tools_used, ["search_protocol_versions"])
        self.assertEqual(msg.restore_version_id, "ver-old-1")
        self.assertEqual(msg.intent, "restore_version")

    def test_build_live_system_instruction_includes_directives_and_context(self) -> None:
        project = _make_rich_project()
        instr = build_live_system_instruction(
            project,
            active_module="module1",
            active_section_id="intro_contexte",
        )
        self.assertIn("Assistant Smart Research", instr)
        self.assertIn("service de pédiatrie du CHUY", instr)
        self.assertNotIn("Ancienne consigne désactivée", instr)
        self.assertIn("paludisme grave", instr.lower())


if __name__ == "__main__":
    unittest.main()
