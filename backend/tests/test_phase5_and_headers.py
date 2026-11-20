import sys
import tempfile
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app
from app.services.blob_storage import BlobStorage, LocalBlobStorage, build_object_key
from app.services.docx_template import compile_protocol_docx
from app.services.pdf_exporter import compile_protocol_pdf, resolve_render_engine
from app.services.pptx_exporter import compile_presentation_pptx
from app.services.pptx_renderer import export_pptx_to_pdf, render_pdf_pages_to_pngs
from app.services.presentation_orchestrator import PresentationOrchestrator
from app.services.storage import project_storage


def test_word_and_pdf_running_header_footer_from_page_2(tmp_path: Path):
    project = project_storage.get("5221c9f3-65e")
    assert project is not None, "Le projet 5221c9f3-65e doit exister"

    docx_path = tmp_path / "headers_test.docx"
    pdf_path = tmp_path / "headers_test.pdf"

    compile_protocol_docx(project, docx_path)
    doc = Document(str(docx_path))

    assert len(doc.sections) >= 1
    sec = doc.sections[0]
    assert sec.different_first_page_header_footer is True

    # Cover Page 1 first_page_header & first_page_footer must remain empty
    fp_hdr_txt = "".join(p.text for p in sec.first_page_header.paragraphs).strip()
    fp_ftr_txt = "".join(p.text for p in sec.first_page_footer.paragraphs).strip()
    assert fp_hdr_txt == "", f"Page 1 header should be empty, got: {fp_hdr_txt}"
    assert fp_ftr_txt == "", f"Page 1 footer should be empty, got: {fp_ftr_txt}"

    # Running Header (Page 2+): Protocol title
    hdr_txt = "".join(p.text for p in sec.header.paragraphs).strip()
    expected_title_frag = (project.cover.title or project.name)[:30].lower()
    assert expected_title_frag in hdr_txt.lower(), f"Expected title in header, got: {hdr_txt}"

    # Running Footer (Page 2+): Candidate name + PAGE field
    ftr_txt = "".join(p.text for p in sec.footer.paragraphs).strip()
    author_name = (project.cover.author_name or "").strip()
    assert author_name in ftr_txt, f"Expected candidate '{author_name}' in footer, got: {ftr_txt}"
    assert "PAGE" in sec.footer._element.xml

    # Real PDF Export check
    compile_protocol_pdf(project, pdf_path)
    reader = PdfReader(str(pdf_path))
    assert len(reader.pages) >= 5

    # Extract text on Page 1 (Cover - no running header/footer) and Page 2+ (running header/footer)
    p1_txt = reader.pages[0].extract_text() or ""
    p1_compact = "".join(p1_txt.split()).lower()
    assert "protocoledethèse" in p1_compact, f"Unexpected Page 1 text:\n{p1_txt[:300]}"
    assert "étude…" not in p1_compact, "Cover Page 1 must NOT have truncated running title header"
    assert f"{''.join(author_name.split()).lower()}ii" not in p1_compact, "Cover Page 1 must NOT have running footer"

    p2_txt = (reader.pages[1].extract_text() or "").lower()
    p2_compact = "".join(p2_txt.split())
    title_frag_compact = "".join(expected_title_frag.split())
    author_compact = "".join(author_name.lower().split())
    assert title_frag_compact in p2_compact, f"Page 2 PDF should display running title header, got:\n{p2_txt[:400]}"
    assert author_compact in p2_compact, f"Page 2 PDF should display candidate footer, got:\n{p2_txt[-300:]}"

    # Also check a later body page (e.g., last page)
    last_txt = (reader.pages[-1].extract_text() or "").lower()
    last_compact = "".join(last_txt.split())
    assert title_frag_compact in last_compact
    assert author_compact in last_compact


def test_blob_storage_and_pdftoppm_rendering(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    # 1. LocalBlobStorage upload, download, delete roundtrip
    store: BlobStorage = LocalBlobStorage(tmp_path / "store")
    assert store.backend_name == "local" and store.is_remote is False
    src_file = tmp_path / "sample.png"
    Image.new("RGB", (64, 64), (0, 74, 143)).save(src_file)

    key = build_object_key("uploads", "proj-demo", "sample.png")
    assert "uploads/proj-demo/sample.png" in key

    # Simulate direct storage file retrieval across LocalBlobStorage
    key_path = store._resolve(key)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(src_file.read_bytes())

    dl_dest = tmp_path / "dl" / "sample.png"
    assert store.download_to_path(key, dl_dest) is True
    assert dl_dest.exists() and dl_dest.stat().st_size == src_file.stat().st_size
    store.delete(key)
    assert not key_path.exists()

    # 2. Render Engine resolution & Poppler pdftoppm PNG rasterization
    assert resolve_render_engine() in ("com", "libreoffice", "cloud_api")

    project = project_storage.get("5221c9f3-65e")
    deck = PresentationOrchestrator().build_default_deck_from_protocol(project)
    pptx_path = tmp_path / "deck.pptx"
    slides_pdf = tmp_path / "slides.pdf"
    compile_presentation_pptx(deck, pptx_path, project=project)
    export_pptx_to_pdf(pptx_path, slides_pdf)

    png_dir = tmp_path / "poppler_pngs"
    pngs = render_pdf_pages_to_pngs(slides_pdf, png_dir, width=1280, height=720, prefix="slide")
    assert len(pngs) == len(deck.slides)
    with Image.open(pngs[0]) as im:
        assert im.size == (1280, 720)


def test_health_endpoint_phase5_and_rebranding():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["app"] == "Smart Research"
    assert data["render_engine"] in ("com", "libreoffice", "cloud_api")
    assert data["storage_backend"] in ("local", "gcs", "s3")
    assert data["pdftoppm_available"] is True


def test_custom_header_footer_separator_color_and_width(tmp_path: Path):
    import zipfile

    project = project_storage.get("5221c9f3-65e")
    assert project is not None, "Le projet 5221c9f3-65e doit exister"
    proj_copy = project.model_copy(deep=True)
    proj_copy.formatting.header_footer_rule_color = "#1E40AF"
    proj_copy.formatting.header_footer_rule_width_pt = 2.25

    docx_path = tmp_path / "custom_hf_rules.docx"
    compile_protocol_docx(proj_copy, docx_path)
    assert docx_path.exists()

    doc = Document(str(docx_path))
    hdr_el_xml = doc.sections[0].header._element.xml
    ftr_el_xml = doc.sections[0].footer._element.xml
    # 2.25 pt * 8 eighths = 18
    assert 'w:color="1E40AF"' in hdr_el_xml, f"Header separator color missing: {hdr_el_xml}"
    assert 'w:sz="18"' in hdr_el_xml, f"Header separator width missing: {hdr_el_xml}"
    assert 'w:color="1E40AF"' in ftr_el_xml, f"Footer separator color missing: {ftr_el_xml}"
    assert 'w:sz="18"' in ftr_el_xml, f"Footer separator width missing: {ftr_el_xml}"

    with zipfile.ZipFile(docx_path, "r") as zf:
        all_hdr_xml = "\n".join(
            zf.read(n).decode("utf-8")
            for n in zf.namelist()
            if n.startswith("word/header") and n.endswith(".xml")
        )
        all_ftr_xml = "\n".join(
            zf.read(n).decode("utf-8")
            for n in zf.namelist()
            if n.startswith("word/footer") and n.endswith(".xml")
        )

    assert 'w:color="1E40AF"' in all_hdr_xml and 'w:sz="18"' in all_hdr_xml
    assert 'w:color="1E40AF"' in all_ftr_xml and 'w:sz="18"' in all_ftr_xml


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        test_word_and_pdf_running_header_footer_from_page_2(Path(td) / "hdr")
        print("[OK] test_word_and_pdf_running_header_footer_from_page_2")
        test_custom_header_footer_separator_color_and_width(Path(td) / "hf_custom")
        print("[OK] test_custom_header_footer_separator_color_and_width")
        test_blob_storage_and_pdftoppm_rendering(Path(td) / "blob")
        print("[OK] test_blob_storage_and_pdftoppm_rendering")
    test_health_endpoint_phase5_and_rebranding()
    print("[OK] test_health_endpoint_phase5_and_rebranding")
    print("ALL PHASE 5 & HEADER/FOOTER TESTS PASSED")
