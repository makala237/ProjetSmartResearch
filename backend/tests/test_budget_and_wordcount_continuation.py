from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.presentation import PresentationDeck, Slide
from app.schemas.protocol import BudgetItem, CoverMetadata, ProtocolProject, SectionState, SectionTable
from app.services.budget_engine import (
    StructuredBudgetOut,
    BudgetItemDraft,
    compute_budget_total,
    extract_budget_items_from_section,
    generate_structured_budget_with_ai,
    parse_fcfa_number,
    resolve_authoritative_budget,
    strip_markdown_budget_tables,
    sync_budget_from_section,
    sync_section_from_budget,
)
from app.services.drafting import (
    _count_words,
    _ensure_target_word_count,
    _stitch_continuation,
)
from app.services.presentation_orchestrator import (
    PresentationOrchestrator,
    refresh_budget_slide_if_present,
)


def _make_project() -> ProtocolProject:
    sections = {
        s.id: SectionState(section_id=s.id)
        for s in FMSB_SECTIONS
    }
    return ProtocolProject(
        project_id="test-proj-001",
        name="Protocole Test Budget & Mots",
        language="fr",
        cover=CoverMetadata(protocol_title="Évaluation du diabète à Yaoundé"),
        sections=sections,
    )


class TestBudgetAndWordCountContinuation(unittest.TestCase):
    def test_parse_fcfa_number_variants(self):
        self.assertEqual(parse_fcfa_number("150 000 FCFA"), 150000.0)
        self.assertEqual(parse_fcfa_number("150\u202f000 F CFA"), 150000.0)
        self.assertEqual(parse_fcfa_number("150.000"), 150000.0)
        self.assertEqual(parse_fcfa_number("1,250,000"), 1250000.0)
        self.assertEqual(parse_fcfa_number("25k"), 25000.0)

    def test_extract_budget_from_markdown_5col_and_bullets(self):
        sec_md = SectionState(
            section_id="chap3_budget",
            content_markdown=(
                "Le financement est assuré sur fonds propres.\n\n"
                "| Catégorie | Désignation | Quantité | Prix unitaire (FCFA) | Prix total (FCFA) |\n"
                "| :--- | :--- | :---: | :---: | :---: |\n"
                "| Administration | Clairance éthique | 1 | 30 000 | 30 000 |\n"
                "| Logistique | Impression et reliure | 5 | 12 000 | 60 000 |\n"
                "| **TOTAL GÉNÉRAL** | | | | **90 000 FCFA** |\n"
            ),
        )
        items = extract_budget_items_from_section(sec_md)
        self.assertEqual(len(items), 2)
        self.assertEqual(compute_budget_total(items), 90000.0)

        # Strip inline table for DOCX export
        prose_only = strip_markdown_budget_tables(sec_md.content_markdown)
        self.assertNotIn("|", prose_only)
        self.assertIn("fonds propres", prose_only)

        # Bulleted format fallback
        sec_bullets = SectionState(
            section_id="chap3_budget",
            content_markdown=(
                "- Clairance éthique : 25 000 FCFA\n"
                "- Transport et collecte : 40 000 FCFA\n"
                "- Total général : 65 000 FCFA\n"
            ),
        )
        b_items = extract_budget_items_from_section(sec_bullets)
        self.assertEqual(len(b_items), 2)
        self.assertEqual(compute_budget_total(b_items), 65000.0)

    def test_budget_end_to_end_sync_modules_1_2_4(self):
        project = _make_project()
        deck = PresentationDeck(
            project_id=project.project_id,
            slides=[
                Slide(
                    id="slide-budget",
                    slide_index=1,
                    slide_type="budget",
                    section_key="chap3_budget",
                    title="Budget prévisionnel",
                    subtitle="À budgétiser",
                    elements=[],
                )
            ],
        )

        fake_dto = StructuredBudgetOut(
            intro_paragraph="Budget prévisionnel sur fonds propres du candidat.",
            items=[
                BudgetItemDraft(category="Éthique", designation="Clairance éthique", quantity=1, unit_cost_fcfa=50000),
                BudgetItemDraft(category="Logistique", designation="Impression protocoles", quantity=4, unit_cost_fcfa=15000),
                BudgetItemDraft(category="Collecte", designation="Transport sites hospitaliers", quantity=1, unit_cost_fcfa=40000),
            ],
            include_contingency_10pct=True,
            closing_note="Le coût global reste maîtrisé.",
        )

        with patch("app.services.budget_engine.gemini_router.generate_structured") as mock_gen:
            mock_gen.return_value = {"model_used": "gemini-3.5-flash-lite", "data": fake_dto, "fallback_errors": []}
            res = generate_structured_budget_with_ai(project)

        # Subtotal = 50k + 60k + 40k = 150k; +10% contingency = 15k => 165,000 FCFA
        self.assertEqual(res["grand_total_fcfa"], 165000.0)
        self.assertEqual(len(project.budget), 4)
        self.assertIn("165 000 FCFA", res["content_markdown"])

        # Module 4 PowerPoint slide captures exact same total & pills
        orch = PresentationOrchestrator()
        total_str, pills = orch._extract_budget_pills(project)
        self.assertEqual(total_str, "165 000 FCFA")
        self.assertTrue(any("Clairance" in p for p in pills))

        # Live refresh of existing PPTX budget slide (subtitle + radial elements)
        refreshed = refresh_budget_slide_if_present(project, deck=deck)
        self.assertTrue(refreshed)
        self.assertEqual(deck.slides[0].subtitle, "165 000 FCFA")
        self.assertTrue(any("Clairance" in e.content for e in deck.slides[0].elements))

        # Edit in Module 2 (project.budget) -> syncs back to chap3_budget & PPTX slide
        project.budget.append(
            BudgetItem(category="Analyse", designation="Logiciel statistique", quantity=1, unit_cost_fcfa=35000, total_fcfa=35000)
        )
        sync_section_from_budget(project)
        refresh_budget_slide_if_present(project, deck=deck)
        self.assertIn("200 000 FCFA", project.sections["chap3_budget"].content_markdown)
        self.assertEqual(deck.slides[0].subtitle, "200 000 FCFA")
        self.assertTrue(any("Logiciel statistique" in e.content for e in deck.slides[0].elements))

        # Authoritative resolver matches
        auth_items, auth_total = resolve_authoritative_budget(project)
        self.assertEqual(len(auth_items), 5)
        self.assertEqual(auth_total, 200000.0)

    def test_word_count_continuation_loop_and_overlap_stitching(self):
        project = _make_project()
        spec = next(s for s in FMSB_SECTIONS if (s.constraints.min_words or 0) >= 700)
        self.assertGreaterEqual(spec.constraints.min_words, 700)

        # Initial short draft (~300 words)
        intro_text = (
            "### 1. Définition et épidémiologie\n\n"
            + ("Le diabète sucré constitue un problème majeur de santé publique au Cameroun. " * 25)
            + "La dernière phrase de la première génération précise l'importance du dépistage précoce."
        )
        self.assertLess(_count_words(intro_text), 700)

        # Mock 2 continuation passes (first repeats anchor sentence to verify overlap stripping!)
        pass1_text = (
            "La dernière phrase de la première génération précise l'importance du dépistage précoce. "
            "### 2. Physiopathologie et mécanismes\n\n"
            + ("L'insulinorésistance périphérique s'accompagne d'une dysfonction progressive des cellules bêta pancréatiques. " * 22)
        )
        pass2_text = (
            "### 3. Prise en charge et complications\n\n"
            + ("La prévention des complications microvasculaires repose sur un équilibre glycémique strict et un suivi régulier. " * 22)
        )

        with patch("app.services.drafting.gemini_router.generate_with_fallback") as mock_cont:
            mock_cont.side_effect = [
                {"text": pass1_text, "model_used": "gemini-3.5-flash-lite"},
                {"text": pass2_text, "model_used": "gemini-3.5-flash-lite"},
            ]
            final_md, trace = _ensure_target_word_count(
                project=project,
                section_id="chap2_generalites",
                spec=spec,
                content_md=intro_text,
                refs_ctx="[1] WHO 2024",
                cross_memory="",
                lang="fr",
            )

        self.assertTrue(trace["triggered"])
        self.assertEqual(trace["continuation_passes"], 2)
        self.assertGreaterEqual(_count_words(final_md), 700)
        # Verify anchor sentence was NOT duplicated
        self.assertEqual(
            final_md.count("La dernière phrase de la première génération précise l'importance du dépistage précoce."),
            1,
        )

    def test_word_count_continuation_skips_when_already_sufficient(self):
        project = _make_project()
        spec = next(s for s in FMSB_SECTIONS if (s.constraints.min_words or 0) >= 700)
        long_text = " ".join(["mot"] * 750)
        with patch("app.services.drafting.gemini_router.generate_with_fallback") as mock_cont:
            final_md, trace = _ensure_target_word_count(
                project=project,
                section_id="chap2_generalites",
                spec=spec,
                content_md=long_text,
                refs_ctx="",
                cross_memory="",
                lang="fr",
            )
        mock_cont.assert_not_called()
        self.assertFalse(trace["triggered"])
        self.assertEqual(final_md, long_text)


if __name__ == "__main__":
    unittest.main()
