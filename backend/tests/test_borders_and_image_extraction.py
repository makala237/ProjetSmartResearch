import io
from pathlib import Path
import re
import shutil
import sys
import tempfile
from uuid import uuid4
import zipfile

from docx import Document
from docx.shared import Inches
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings
from app.main import app
from app.schemas.protocol import FormattingRules, ProtocolProject, SectionTable, TimelineGrid, TimelineGridRow
from app.services.docx_template import compile_protocol_docx
from app.services.figure_extractor import (
    _extract_captions_from_text,
    _extract_pdf_images_pypdf_stream,
    extract_images_from_uploaded_document,
)
from app.services.pdf_exporter import compile_protocol_pdf
from app.services.pptx_renderer import render_pdf_pages_to_pngs
from app.services.storage import project_storage
from tests._auth_helper import login_as_admin


def _png_bytes(size=(260, 180), rgb=(25, 110, 175)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, rgb).save(buf, format="PNG")
    return buf.getvalue()


def test_blue_borders_and_accents_in_docx_and_pdf() -> None:
    client = TestClient(app)
    login_as_admin(client)

    r_create = client.post(
        "/api/projects",
        json={
            "name": f"Test Couleur Bordures {uuid4().hex[:6]}",
            "entry_mode": "new_topic",
            "research_idea": "Évaluation des facteurs cliniques en médecine interne",
            "study_type": "Étude transversale",
        },
    )
    assert r_create.status_code == 200, r_create.text
    pid = r_create.json()["project_id"]

    try:
        proj = project_storage.get(pid)
        assert proj is not None
        proj.cover.title = "Étude de validation de la couleur bleue sur bordures Word et PDF"
        proj.cover.author_name = "Dr Test Couleur"
        proj.sections["intro_contexte"].content_markdown = "Contexte médical avec tableau."
        proj.sections["intro_contexte"].custom_tables.append(
            SectionTable(
                title="Répartition test",
                anchor_hint="",
                headers=["Paramètre", "Valeur"],
                rows=[["Âge moyen", "42 ans"]],
            )
        )
        proj.timeline_grid = TimelineGrid(
            periods=["Jan", "Fév"],
            rows=[TimelineGridRow(activity="Collecte", active_periods=["Jan"])],
        )
        # Keep stored formatting as default maroon (#6C2826) before calling export!
        assert (proj.formatting.border_color or "").upper() == "#6C2826"
        project_storage.save(proj)

        # Call export/docx directly WITH unsaved blue border formatting in POST body
        blue_fmt = proj.formatting.model_dump()
        blue_fmt["border_color"] = "#1D4ED8"
        blue_fmt["cover_border_style"] = "single"
        r_exp = client.post(
            f"/api/projects/{pid}/export/docx",
            json={"formatting": blue_fmt},
        )
        assert r_exp.status_code == 200, r_exp.text

        with zipfile.ZipFile(io.BytesIO(r_exp.content)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")

        assert len(re.findall(r"6c2826", xml, re.I)) == 0, "Expected 0 maroon 6C2826 literals when border_color=#1D4ED8"
        assert len(re.findall(r"1d4ed8", xml, re.I)) >= 12, "Expected blue 1D4ED8 across pgBorders, boxes, tables, and CRF"

        pgb_match = re.search(r"<w:pgBorders.*?</w:pgBorders>", xml, re.S)
        assert pgb_match is not None
        pgb_xml = pgb_match.group(0)
        assert 'w:color="1D4ED8"' in pgb_xml
        assert "themeColor" not in pgb_xml

        # Also compile real PDF and check rendered cover Page 1 border pixels are actually blue
        loaded = project_storage.get(pid)
        assert loaded is not None
        loaded.formatting = FormattingRules(**blue_fmt)
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            pdf_out = td_path / "blue_protocol.pdf"
            compile_protocol_pdf(loaded, pdf_out)
            pngs = render_pdf_pages_to_pngs(pdf_out, td_path / "pages", width=900, height=1273, prefix="p")
            assert len(pngs) >= 2
            with Image.open(pngs[0]) as p1_im:
                rgb_im = p1_im.convert("RGB")
                w, h = rgb_im.size
                # Scan horizontal & vertical slices near the cover page outer frame for blue pixels (#1D4ED8 -> B >> R)
                blue_pixels = 0
                maroon_pixels = 0
                for x in range(15, 110):
                    for y in range(200, h - 200, 12):
                        r, g, b = rgb_im.getpixel((x, y))
                        if b - r >= 45 and b >= 110:
                            blue_pixels += 1
                        elif r - b >= 35 and r >= 70 and g < 80:
                            maroon_pixels += 1
                assert blue_pixels >= 20, f"Expected rendered cover border to contain blue pixels, got blue={blue_pixels}, maroon={maroon_pixels}"
                assert maroon_pixels == 0, f"Expected zero maroon border pixels on cover page, got {maroon_pixels}"
    finally:
        client.delete(f"/api/projects/{pid}")


def test_pdf_image_extraction_on_garoua_template() -> None:
    pdf_path = ROOT_DIR / "Tamplate protocole Garoua.pdf"
    assert pdf_path.exists(), f"Missing {pdf_path}"
    raw_pdf = pdf_path.read_bytes()

    pid = f"test_pdf_{uuid4().hex[:8]}"
    upload_dir = get_settings().storage_dir / "uploads" / pid
    try:
        proj = ProtocolProject(project_id=pid, name="Test Garoua PDF")
        sec_ctx = {
            "page_to_section": {
                1: "cover_page",
                10: "chap2_rappel_connaissances",
                11: "chap2_rappel_connaissances",
                12: "chap2_rappel_connaissances",
                17: "chap2_rappel_connaissances",
                19: "chap2_rappel_connaissances",
                21: "chap2_rappel_connaissances",
                24: "chap2_etat_connaissances",
                40: "chap3_outils_procedure",
            }
        }
        extracted = extract_images_from_uploaded_document(
            proj,
            "Tamplate protocole Garoua.pdf",
            raw_pdf,
            section_context=sec_ctx,
        )
        # Garoua PDF contains exactly 8 scientific figures (pages 10,11,12,17,19,21,24,40), plus 2 cover logos (p1) and 15x15/25x22 icons
        assert len(extracted) == 8, f"Expected 8 scientific figures from Garoua PDF, got {len(extracted)}: {[a.title for a in extracted]}"

        all_titles = " | ".join(a.title for a in extracted)
        for expected_cap in ("Figure 1", "Figure 2", "Figure 3", "Figure 4", "Figure 5", "Figure 6"):
            assert expected_cap in all_titles, f"Missing caption {expected_cap} in {all_titles}"

        # Verify section mapping & captions don't suffer from 'MFigure 6' typo
        assert "MFigure" not in all_titles
        fig6 = [a for a in extracted if "Figure 6" in a.title][0]
        assert fig6.target_section_id == "chap2_etat_connaissances"
        p40_figs = [a for a in extracted if "p.40" in a.title or a.target_section_id == "chap3_outils_procedure"]
        assert len(p40_figs) == 1

        # Second pass on same project must produce 0 duplicates
        dup_pass = extract_images_from_uploaded_document(proj, "Tamplate protocole Garoua.pdf", raw_pdf, section_context=sec_ctx)
        assert len(dup_pass) == 0

        # Also test pure-Python pypdf stream fallback directly
        pid_py = f"test_pypdf_{uuid4().hex[:8]}"
        upload_dir_py = get_settings().storage_dir / "uploads" / pid_py
        try:
            proj_py = ProtocolProject(project_id=pid_py, name="Test Garoua pypdf")
            reader = PdfReader(io.BytesIO(raw_pdf))
            p_texts = {idx: (pg.extract_text() or "").strip() for idx, pg in enumerate(reader.pages, 1)}
            p_caps = {idx: _extract_captions_from_text(txt) for idx, txt in p_texts.items()}
            py_extracted = _extract_pdf_images_pypdf_stream(
                project=proj_py,
                filename="Tamplate protocole Garoua.pdf",
                reader=reader,
                page_texts=p_texts,
                page_captions=p_caps,
                page_to_section=sec_ctx["page_to_section"],
                seen_hashes=set(),
            )
            assert len(py_extracted) == 8, f"Pure-Python fallback extracted {len(py_extracted)} figures instead of 8"
        finally:
            shutil.rmtree(upload_dir_py, ignore_errors=True)
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)


def test_docx_image_extraction_and_import_route() -> None:
    doc = Document()
    # Header logo (must be ignored)
    hdr_p = doc.sections[0].header.paragraphs[0]
    hdr_p.add_run().add_picture(io.BytesIO(_png_bytes((110, 110), (180, 180, 180))), width=Inches(1.0))

    # Cover logo (must be ignored)
    doc.add_paragraph("RÉPUBLIQUE DU CAMEROUN\nUNIVERSITÉ DE DOUALA\nFACULTÉ DE MÉDECINE\nPROTOCOLE DE THÈSE")
    doc.add_paragraph().add_run().add_picture(io.BytesIO(_png_bytes((140, 140), (108, 40, 38))), width=Inches(1.2))

    # Chapitre 1 context + scientific figure 1 + caption
    doc.add_paragraph("CHAPITRE I : PROBLÉMATIQUE ET CADRE CONCEPTUEL")
    doc.add_paragraph("Le modèle conceptuel des facteurs associés à l'hypertension est détaillé dans la figure ci-dessous.")
    doc.add_paragraph().add_run().add_picture(io.BytesIO(_png_bytes((320, 200), (20, 90, 170))), width=Inches(3.2))
    doc.add_paragraph("Figure 1 : Modèle conceptuel de l'HTA chez l'adulte")

    # Chapitre 2 context + scientific figure 2 + caption
    doc.add_paragraph("CHAPITRE II : RAPPEL DES CONNAISSANCES")
    doc.add_paragraph("Les mécanismes physiopathologiques impliquent le système rénine-angiotensine.")
    doc.add_paragraph().add_run().add_picture(io.BytesIO(_png_bytes((360, 220), (31, 168, 79))), width=Inches(3.5))
    doc.add_paragraph("Schéma 2 : Cascade physiopathologique rénine-angiotensine")

    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    pid = f"test_docx_{uuid4().hex[:8]}"
    upload_dir = get_settings().storage_dir / "uploads" / pid
    try:
        proj = ProtocolProject(project_id=pid, name="Test DOCX Images")
        extracted = extract_images_from_uploaded_document(
            proj,
            "brouillon_test.docx",
            docx_bytes,
            section_context={
                "elem_idx_to_section": {
                    0: "cover_page",
                    1: "cover_page",
                    2: "chap1_cadre_theorique",
                    3: "chap1_cadre_theorique",
                    4: "chap1_cadre_theorique",
                    5: "chap1_cadre_theorique",
                    6: "chap2_rappel_connaissances",
                    7: "chap2_rappel_connaissances",
                    8: "chap2_rappel_connaissances",
                    9: "chap2_rappel_connaissances",
                }
            },
        )
        assert len(extracted) == 2, f"Expected 2 scientific figures from DOCX, got {len(extracted)}: {[a.title for a in extracted]}"
        assert "Modèle conceptuel" in extracted[0].title
        assert extracted[0].target_section_id == "chap1_cadre_theorique"
        assert "modèle conceptuel" in extracted[0].anchor_hint.lower()

        assert "physiopathologique" in extracted[1].title.lower()
        assert extracted[1].target_section_id == "chap2_rappel_connaissances"
        assert "rénine-angiotensine" in extracted[1].anchor_hint.lower()
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    # Also test POST /api/projects/{pid}/import route end-to-end returns extracted_images_count & extracted_images
    client = TestClient(app)
    login_as_admin(client)
    r_proj = client.post(
        "/api/projects",
        json={"name": f"Import Route Test {uuid4().hex[:6]}", "entry_mode": "existing_protocol"},
    )
    assert r_proj.status_code == 200, r_proj.text
    route_pid = r_proj.json()["project_id"]
    try:
        r_imp = client.post(
            f"/api/projects/{route_pid}/import",
            files={"file": ("protocole_test.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert r_imp.status_code == 200, r_imp.text
        imp_rep = r_imp.json()["report"]
        assert imp_rep["extracted_images_count"] == 2
        assert len(imp_rep["extracted_images"]) == 2
    finally:
        client.delete(f"/api/projects/{route_pid}")


if __name__ == "__main__":
    test_blue_borders_and_accents_in_docx_and_pdf()
    print("[OK] test_blue_borders_and_accents_in_docx_and_pdf")
    test_pdf_image_extraction_on_garoua_template()
    print("[OK] test_pdf_image_extraction_on_garoua_template")
    test_docx_image_extraction_and_import_route()
    print("[OK] test_docx_image_extraction_and_import_route")
    print("ALL BORDERS & IMAGE EXTRACTION TESTS PASSED")
