from pathlib import Path
import sys
import tempfile
import unittest

from docx import Document

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data.fmsb_canvas import DEDICATED_SUB_ANNEX_IDS, FMSB_SECTIONS
from app.data.i18n import get_chapter_titles, get_localized_canvas
from app.schemas.protocol import CoverMetadata, ProtocolProject, SectionState, StepStatus
from app.services.annexe_engine import (
    extract_all_sub_annexes_from_blocks,
    generate_sub_annex_content,
    resolve_faculty_admin_context,
)
from app.services.annexes_generator import build_annexes_pack
from app.services.crf_extractor import (
    compile_crf_fields_hybrid,
    detect_crf_boundaries_in_blocks,
    slice_crf_from_text_or_blocks,
)
from app.services.docx_template import W_NS, compile_protocol_docx
from app.services.importer import split_upload_into_blocks


def _make_full_project(project_id: str = "test-5ch", cover: CoverMetadata | None = None) -> ProtocolProject:
    sections = {
        spec.id: SectionState(section_id=spec.id, status=StepStatus.PENDING)
        for spec in FMSB_SECTIONS
    }
    return ProtocolProject(
        project_id=project_id,
        name="Prévalence et facteurs associés à l'anxiété chez les étudiants en médecine",
        research_idea="Étude transversale analytique multicentrique",
        study_type="Étude transversale analytique",
        language="fr",
        cover=cover or CoverMetadata(),
        sections=sections,
    )


class TestSubAnnexesAnd5Chapters(unittest.TestCase):
    def test_five_chapter_canvas_fr_and_en_and_individual_sub_annexes(self):
        fr_chapters = get_chapter_titles("fr")
        en_chapters = get_chapter_titles("en")

        self.assertEqual(
            list(fr_chapters.keys()),
            ["chapitre_1", "chapitre_2", "chapitre_3", "references", "annexes"],
        )
        self.assertEqual(fr_chapters["chapitre_1"], "CHAPITRE I : INTRODUCTION")
        self.assertEqual(fr_chapters["references"], "CHAPITRE IV : RÉFÉRENCES")
        self.assertEqual(fr_chapters["annexes"], "CHAPITRE V : ANNEXES")

        self.assertEqual(en_chapters["chapitre_1"], "CHAPTER I: INTRODUCTION")
        self.assertEqual(en_chapters["references"], "CHAPTER IV: REFERENCES")
        self.assertEqual(en_chapters["annexes"], "CHAPTER V: APPENDICES")

        by_id = {s.id: s for s in FMSB_SECTIONS}
        self.assertEqual(len(FMSB_SECTIONS), 34)
        self.assertEqual(by_id["intro_contexte"].chapter_id, "chapitre_1")
        self.assertEqual(by_id["intro_contexte"].title, "I.1. Situation du problème et contexte")
        self.assertIn("chap1_definitions_operationnelles", by_id)
        self.assertEqual(by_id["chap1_definitions_operationnelles"].chapter_id, "chapitre_1")
        self.assertEqual(by_id["chap1_variables"].chapter_id, "chapitre_3")
        self.assertIn("chap3_limites_biais", by_id)
        self.assertIn("chap3_formalites_admin", by_id)

        for sid in DEDICATED_SUB_ANNEX_IDS:
            self.assertIn(sid, by_id)
            self.assertEqual(by_id[sid].chapter_id, "annexes")

        en_by_id = {s.id: s for s in get_localized_canvas("en")}
        self.assertEqual(len(en_by_id), 34)
        self.assertEqual(en_by_id["annexe_notice_information"].chapter_title, "CHAPTER V: APPENDICES")
        self.assertIn("Operational Definitions", en_by_id["chap1_definitions_operationnelles"].title)

    def test_multi_annex_extraction_on_synthetic_and_real_fokou_protocol(self):
        # 1) Synthetic multi-annex document including bilingual combined Notice+Consent
        synth_bytes = (
            "CHAPITRE V : ANNEXES\n\n"
            "ANNEXE 1 : Fiche d'information et formulaire de consentement éclairé\n"
            "1. Pourquoi cette étude ? Évaluer l'anxiété.\n"
            "2. Participation volontaire.\n"
            "Consentement\n"
            "☐ J'accepte de participer\n"
            "☐ Je n'accepte pas de participer\n\n"
            "ANNEXE 2 : Questionnaire\n"
            "Section 1. Sociodémographie\n"
            "Q1. Sexe ?\n"
            "• Masculin\n"
            "• Féminin\n"
            "Q2. Consommation de stimulants ?\n"
            "• Jamais\n"
            "• Régulièrement\n\n"
            "ANNEXE 3 : Demande de l'autorisation de recherche\n"
            "Objet : Demande d'autorisation de recherche.\n"
            "J'ai l'honneur de solliciter l'autorisation auprès de M. le Doyen.\n\n"
            "ANNEXE 4 : Demande de la clairance éthique\n"
            "Objet : Demande de clairance éthique au Président du Comité d'Éthique."
        ).encode("utf-8")

        s_blocks = split_upload_into_blocks("synth.md", synth_bytes)
        crf_det = detect_crf_boundaries_in_blocks(s_blocks, lang="fr", use_ai=False)
        self.assertTrue(crf_det.found)
        crf_ids = set(range(crf_det.start_block_id, crf_det.end_block_id + 1))  # type: ignore[operator]
        sub_res = extract_all_sub_annexes_from_blocks(s_blocks, excluded_block_ids=crf_ids, lang="fr")

        for expected_sid in (
            "annexe_notice_information",
            "annexe_consentement",
            "annexe_autorisation_recherche",
            "annexe_clairance_ethique",
        ):
            self.assertIn(expected_sid, sub_res["extracted"])

        # 2) Real FOKOU_Protocole.pdf verification when present in workspace root
        fokou_path = REPO_ROOT / "FOKOU_Protocole.pdf"
        if fokou_path.exists():
            f_bytes = fokou_path.read_bytes()
            f_blocks = split_upload_into_blocks("FOKOU_Protocole.pdf", f_bytes)
            f_crf_det = detect_crf_boundaries_in_blocks(f_blocks, lang="fr", use_ai=False)
            self.assertTrue(f_crf_det.found, "CRF Questionnaire in FOKOU_Protocole.pdf must be detected")
            _, pure_crf, _, _ = slice_crf_from_text_or_blocks(
                blocks=f_blocks,
                lang="fr",
                use_ai=False,
                precomputed_detection=f_crf_det,
            )
            self.assertIn("Q6. Quel est votre sexe", pure_crf)
            self.assertNotIn("Demande de l’autorisation de recherche", pure_crf)

            proj_f = _make_full_project("fokou-check")
            compiled = compile_crf_fields_hybrid(proj_f, pure_crf, use_ai=False)
            self.assertGreaterEqual(len(compiled["fields"]), 30, "FOKOU multi-line Qn/bullet questions must compile cleanly")
            self.assertTrue(any("sexe" in f.label.lower() for f in compiled["fields"]))

            f_crf_ids = {
                b.block_id
                for b in f_blocks
                if f_crf_det.start_block_id <= b.block_id <= f_crf_det.end_block_id  # type: ignore[operator]
            }
            f_sub = extract_all_sub_annexes_from_blocks(f_blocks, excluded_block_ids=f_crf_ids, lang="fr")
            ext = f_sub["extracted"]
            self.assertIn("annexe_notice_information", ext)
            self.assertIn("annexe_consentement", ext)
            self.assertIn("annexe_autorisation_recherche", ext)
            self.assertIn("annexe_clairance_ethique", ext)
            # Bilingual Notice + Consent preserved cleanly from pages 33-36
            self.assertIn("English version", ext["annexe_notice_information"]["content_markdown"])
            self.assertIn("I agree to participate", ext["annexe_consentement"]["content_markdown"])

    def test_faculty_aware_sub_annex_generation_douala_and_istm(self):
        # Case A: University of Douala - Faculty of Medicine & Pharmaceutical Sciences (FMSP)
        douala_cover = CoverMetadata(
            title="Facteurs associés à l'hypertension artérielle à l'Hôpital Laquintinie",
            university_fr="UNIVERSITÉ DE DOUALA",
            faculty_fr="Faculté de Médecine et des Sciences Pharmaceutiques",
            degree_label="Doctorat en Médecine Générale",
            author_name="NDONGO Claire",
            matricule="21M044",
            student_level="Étudiante en 7ème année de médecine générale",
            student_phone="699001122",
            student_email="claire.ndongo@example.org",
            director="Pr EBONGUE Solange",
            co_directors=["Dr MBAPPE Paul"],
            host_institutions=["Hôpital Laquintinie de Douala"],
            ethics_committees=["Comité d'Éthique Institutionnel de l'Université de Douala (CEI-UD)"],
        )
        proj_d = _make_full_project("douala-proj", cover=douala_cover)
        res_auth = generate_sub_annex_content(proj_d, "annexe_autorisation_recherche", use_ai=False)
        res_eth = generate_sub_annex_content(proj_d, "annexe_clairance_ethique", use_ai=False)

        auth_md = res_auth["content_markdown"]
        eth_md = res_eth["content_markdown"]
        self.assertIn("Faculté de Médecine et des Sciences Pharmaceutiques", auth_md)
        self.assertIn("UNIVERSITÉ DE DOUALA", auth_md)
        self.assertIn("Douala", auth_md)
        self.assertIn("Monsieur/Madame le Doyen", auth_md)
        self.assertIn("Hôpital Laquintinie de Douala", auth_md)
        self.assertNotIn("FMSB", auth_md)
        self.assertNotIn("Yaoundé", auth_md)

        self.assertIn("CEI-UD", eth_md)
        self.assertNotIn("CIER-FMSB", eth_md)

        # Case B: Institute/School led by a Director ("Directeur")
        istm_cover = CoverMetadata(
            university_fr="INSTITUT UNIVERSITAIRE DU GOLFE DE GUINÉE",
            faculty_fr="Institut Supérieur des Techniques Médicales de Garoua",
            ethics_committees=["Comité Régional d'Éthique du Nord"],
        )
        proj_i = _make_full_project("istm-proj", cover=istm_cover)
        ctx_i = resolve_faculty_admin_context(proj_i)
        self.assertTrue(ctx_i["is_director_led"])
        self.assertIn("Directeur", ctx_i["faculty_head_recipient"])
        self.assertEqual(ctx_i["city"], "Garoua")

        # Case C: Bilingual Notice & Consentement
        res_bi = generate_sub_annex_content(
            proj_d,
            "annexe_notice_information",
            bilingual=True,
            use_ai=False,
        )
        self.assertIn("NOTICE D'INFORMATION", res_bi["content_markdown"])
        self.assertIn("PARTICIPANT INFORMATION SHEET", res_bi["content_markdown"])

    def test_build_annexes_pack_and_docx_export_with_5_chapters(self):
        proj = _make_full_project("docx-5ch")
        generate_sub_annex_content(proj, "annexe_notice_information", use_ai=False)
        generate_sub_annex_content(proj, "annexe_consentement", use_ai=False)
        generate_sub_annex_content(proj, "annexe_autorisation_recherche", use_ai=False)
        proj.sections["intro_contexte"].content_markdown = "Contexte épidémiologique camerounais détaillé."
        proj.sections["chap3_type_site_duree"].content_markdown = "Étude transversale analytique à Yaoundé."

        pack = build_annexes_pack(proj)
        self.assertGreaterEqual(len(pack), 3)
        titles = [str(p["title"]) for p in pack]
        self.assertTrue(any("NOTICE D'INFORMATION" in t for t in titles))
        self.assertTrue(any("CONSENTEMENT" in t for t in titles))
        self.assertTrue(any("AUTORISATION" in t for t in titles))

        with tempfile.TemporaryDirectory() as tmp:
            out_docx = Path(tmp) / "proto_5ch.docx"
            compile_protocol_docx(proj, out_docx)
            self.assertTrue(out_docx.exists())

            doc = Document(str(out_docx))
            all_paras = [
                "".join(t.text for t in p._element.iter(f"{{{W_NS}}}t") if t.text).strip()
                for p in doc.paragraphs
            ]
            joined = "\n".join(all_paras)
            self.assertIn("CHAPITRE I : INTRODUCTION", joined)
            self.assertIn("CHAPITRE IV : RÉFÉRENCES", joined)
            self.assertIn("CHAPITRE V : ANNEXES", joined)
            # Standalone legacy "INTRODUCTION" chapter banner must be absent
            self.assertNotIn("INTRODUCTION", [p for p in all_paras if p == "INTRODUCTION"])


if __name__ == "__main__":
    unittest.main()
