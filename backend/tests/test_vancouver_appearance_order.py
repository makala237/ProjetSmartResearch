from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data.fmsb_canvas import FMSB_SECTIONS
from app.schemas.protocol import (
    ProtocolProject,
    ReferenceItem,
    SectionState,
    StepStatus,
)
from app.services.docx_template import W_NS, compile_protocol_docx
from app.services.global_audit import run_global_protocol_audit
from app.services.vancouver import (
    build_and_validate_vancouver_references,
    get_cited_references_in_order,
    renumber_and_sync_vancouver_references,
)


def _make_pool_project(total_refs: int = 25) -> ProtocolProject:
    sections = {
        spec.id: SectionState(section_id=spec.id, status=StepStatus.PENDING)
        for spec in FMSB_SECTIONS
    }
    refs = []
    for i in range(1, total_refs + 1):
        refs.append(
            ReferenceItem(
                ref_id=f"ref-{i}",
                order_index=i,
                authors=[f"Auteur{i} A"],
                title=f"Etude scientifique numero {i}",
                journal="Rev Med Afr",
                year=2020 + (i % 5),
                vancouver_citation=f"Auteur{i} A. Etude scientifique numero {i}. Rev Med Afr. {2020 + (i % 5)}.",
                verified=True,
            )
        )
    return ProtocolProject(
        project_id="test-vancouver-order",
        name="Protocole test renumérotation Vancouver",
        language="fr",
        sections=sections,
        references=refs,
    )


class TestVancouverAppearanceOrder(unittest.TestCase):
    def test_first_appearance_renumbering_and_cited_only_bibliography(self) -> None:
        proj = _make_pool_project(25)
        intro = proj.sections["intro_contexte"]
        intro.content_markdown = (
            "Le fardeau initial est documenté en Afrique centrale [22]. "
            "Une deuxième cohorte rapporte des facteurs similaires en [7]. "
            "Synthèse récente en [2024] avec donnée [À PRÉCISER] sur la période [2020-2024]."
        )
        intro.content_html = (
            "<p>Le fardeau initial est documenté en Afrique centrale <sup>[22]</sup>. "
            "Une deuxième cohorte rapporte des facteurs similaires en [7]. "
            "Synthèse récente en [2024] avec donnée [À PRÉCISER] sur la période [2020-2024].</p>"
        )

        report = renumber_and_sync_vancouver_references(proj)
        self.assertEqual(report["cited_count"], 2)
        self.assertEqual(report["total_pool_count"], 25)
        self.assertEqual(report["uncited_count"], 23)

        # In Intro, [22] -> [1] and [7] -> [2]; years and placeholders remain untouched
        self.assertIn("[1]", intro.content_markdown)
        self.assertIn("[2]", intro.content_markdown)
        self.assertNotIn("[22]", intro.content_markdown)
        self.assertNotIn("[7]", intro.content_markdown)
        self.assertIn("[2024]", intro.content_markdown)
        self.assertIn("[À PRÉCISER]", intro.content_markdown)
        self.assertIn("[2020-2024]", intro.content_markdown)
        self.assertIn("<sup>[1]</sup>", intro.content_html)
        self.assertEqual(intro.cited_ref_ids, ["ref-22", "ref-7"])

        # References section contains ONLY the 2 cited entries in exact appearance order
        ref_md = proj.sections["references"].content_markdown
        self.assertIn("1. Auteur22 A.", ref_md)
        self.assertIn("2. Auteur7 A.", ref_md)
        self.assertNotIn("Auteur1 A.", ref_md)
        self.assertNotIn("Auteur10 A.", ref_md)
        # Full 25-ref pool is still intact
        self.assertEqual(len(proj.references), 25)
        self.assertEqual(proj.references[0].ref_id, "ref-22")
        self.assertEqual(proj.references[1].ref_id, "ref-7")

        # Now later section (Chap II) cites ref-15 at its current pool index
        cur_idx_15 = next(r.order_index for r in proj.references if r.ref_id == "ref-15")
        later = proj.sections["chap2_etat_connaissances"]
        later.content_markdown = f"Des données régionales confirment cette tendance [{cur_idx_15}]."
        later.content_html = f"<p>Des données régionales confirment cette tendance <sup>[{cur_idx_15}]</sup>.</p>"

        renumber_and_sync_vancouver_references(proj)
        self.assertIn("[3]", later.content_markdown)
        self.assertIn("<sup>[3]</sup>", later.content_html)

        # Now insert ref-10 earlier in Intro -> becomes [3], pushing ref-15 in Chap II from [3] to [4]
        cur_idx_10 = next(r.order_index for r in proj.references if r.ref_id == "ref-10")
        intro.content_markdown += f" Voir aussi l'étude nationale [{cur_idx_10}]."
        intro.content_html += f"<p>Voir aussi l'étude nationale [{cur_idx_10}].</p>"

        renumber_and_sync_vancouver_references(proj)
        self.assertIn("[3]", intro.content_markdown)
        self.assertIn("[4]", later.content_markdown)
        self.assertIn("<sup>[4]</sup>", later.content_html)

        cited_ordered = get_cited_references_in_order(proj)
        self.assertEqual([r.ref_id for r in cited_ordered], ["ref-22", "ref-7", "ref-10", "ref-15"])

        # Grouped & range citations test
        proj_grp = _make_pool_project(25)
        proj_grp.sections["intro_contexte"].content_markdown = (
            "Première vague [22, 7] puis série homogène [20-21]."
        )
        # Expand [20-21] = [20, 21] -> 22->1, 7->2, 20->3, 21->4
        renumber_and_sync_vancouver_references(proj_grp)
        md_grp = proj_grp.sections["intro_contexte"].content_markdown
        self.assertIn("[1, 2]", md_grp)
        self.assertIn("[3, 4]", md_grp)
        self.assertEqual(
            [r.ref_id for r in get_cited_references_in_order(proj_grp)],
            ["ref-22", "ref-7", "ref-20", "ref-21"],
        )

    def test_docx_export_and_global_audit_only_use_cited_references(self) -> None:
        proj = _make_pool_project(10)
        # Mark uncited #1 distinctly so we can prove it never appears in DOCX bibliography
        proj.references[0].authors = ["UncitedUniqueAuthor"]
        proj.references[0].vancouver_citation = (
            "UncitedUniqueAuthor X. Etude Non Citee UniqueMarker99. J Test. 2022."
        )
        proj.references[6].vancouver_citation = (
            "CitedSept S. Etude Citee En Premier Marker07. J Test. 2023."
        )
        proj.references[3].vancouver_citation = (
            "CitedQuatre Q. Etude Citee En Second Marker04. J Test. 2024."
        )

        proj.sections["intro_contexte"].content_markdown = (
            "Introduction avec première citation [7] puis seconde citation [4]."
        )
        proj.sections["intro_contexte"].status = StepStatus.VALIDATED

        res_val = build_and_validate_vancouver_references(
            proj, use_gemini_decision=False, mark_validated=True
        )
        self.assertEqual(res_val["references_count"], 2)
        self.assertEqual(res_val["total_pool_count"], 10)
        self.assertEqual(proj.sections["intro_contexte"].content_markdown, "Introduction avec première citation [1] puis seconde citation [2].")

        # Global audit must report OK (not warn about 8 uncited pool references!)
        audit_rep = run_global_protocol_audit(proj)
        cit_check = next(c for c in audit_rep["checks"] if c["id"] == "citations_vancouver")
        self.assertEqual(cit_check["status"], "ok", cit_check)

        # DOCX export must render ONLY 1. CitedSept and 2. CitedQuatre (never UncitedUniqueAuthor)
        with tempfile.TemporaryDirectory() as tmp:
            out_docx = Path(tmp) / "cited_only.docx"
            with patch(
                "app.services.docx_template.build_unified_tables_and_figures_lists",
                return_value=([], [], None),
            ):
                compile_protocol_docx(proj, out_docx)
            with zipfile.ZipFile(out_docx) as zf:
                root = ET.fromstring(zf.read("word/document.xml"))
            ns = {"w": W_NS}
            all_text = "\n".join(
                "".join(t.text or "" for t in p.findall(".//w:t", ns)).strip()
                for p in root.findall(".//w:body/w:p", ns)
            )
            self.assertIn("1. CitedSept S. Etude Citee En Premier Marker07.", all_text)
            self.assertIn("2. CitedQuatre Q. Etude Citee En Second Marker04.", all_text)
            self.assertNotIn("UncitedUniqueAuthor", all_text)
            self.assertNotIn("UniqueMarker99", all_text)


if __name__ == "__main__":
    unittest.main()
