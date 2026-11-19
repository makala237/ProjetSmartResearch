import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app
from app.schemas.presentation import SlideElement
from app.services.pptx_exporter import compile_presentation_pptx
from app.services.pptx_renderer import export_pptx_to_pdf, render_pptx_previews
from app.services.presentation_orchestrator import PresentationOrchestrator
from app.services.slide_layout_engine import (
    SLIDE_WIDTH_IN,
    compute_deck_layout,
    split_elements_to_fit_slides,
)
from app.services.storage import project_storage


def test_real_protocol_extraction_and_no_fake_oia():
    project = project_storage.get("5221c9f3-65e")
    assert project is not None, "Le projet de référence 5221c9f3-65e doit exister"

    orch = PresentationOrchestrator()
    deck = orch.build_default_deck_from_protocol(project)

    assert len(deck.slides) >= 17, "Aucun plafond de 15 slides ne doit tronquer le deck"

    # Vérifier absence totale de texte fictif OIA / budget figé 1 250 000 FCFA
    full_text = "\n".join(
        f"{s.title} {s.subtitle or ''} " + " ".join(e.content for e in s.elements)
        for s in deck.slides
    )
    assert "occlusion intestinale aiguë" not in full_text.lower()
    assert "1 250 000" not in full_text

    # Vérifier budget réel exact (790 000 FCFA)
    budget_slide = next(s for s in deck.slides if s.slide_type == "budget")
    assert budget_slide.subtitle == "790 000 FCFA"
    assert len(budget_slide.elements) == 10

    # Vérifier objectifs réels
    obj_gen = next(s for s in deck.slides if s.slide_type == "objective_general")
    assert "testiculaire" in obj_gen.elements[0].content.lower()

    # Vérifier page de garde structurée (directeur + 2 co-directeurs + en-têtes complets)
    cover = next(s for s in deck.slides if s.slide_type == "cover")
    roles = {b.role: b for b in cover.render_boxes}
    assert "MINISTÈRE DE L'ENSEIGNEMENT SUPÉRIEUR" in roles["cover_header_fr"].text
    assert "MINISTRY OF HIGHER EDUCATION" in roles["cover_header_en"].text
    assert "Pr. Bang Guy Aristide" in roles["cover_director"].sub_text
    assert "Dr. Nwaha Makon Axel S." in roles["cover_codirector"].sub_text
    assert "Dr Mbouche Landry Oriole" in roles["cover_codirector"].sub_text


def test_slide_layout_engine_overflow_split_and_bounds():
    long_elements = [
        SlideElement(
            id=f"el-{i}",
            element_type="bullet_main",
            content=f"Point méthodologique détaillé numéro {i} décrivant la procédure clinique opératoire standardisée.",
            order=i,
        )
        for i in range(1, 8)
    ]
    pages = split_elements_to_fit_slides(long_elements, max_lines=6, font_pt=24.0)
    assert len(pages) >= 2, "7 longues puces à 24pt doivent se scinder sur au moins 2 diapositives"

    project = project_storage.get("5221c9f3-65e")
    deck = PresentationOrchestrator().build_default_deck_from_protocol(project)
    compute_deck_layout(deck, project)

    for s in deck.slides:
        for b in s.render_boxes:
            assert 0.0 <= b.x_pct <= 1.0, f"x_pct hors bornes sur slide {s.slide_index}: {b.x_pct}"
            assert 0.0 <= b.y_pct <= 1.0, f"y_pct hors bornes sur slide {s.slide_index}: {b.y_pct}"
            assert b.x_pct + b.w_pct <= 1.01
            assert b.y_pct + b.h_pct <= 1.01
        if s.slide_type in ("introduction", "objective_general", "objective_specific", "methodology"):
            for b in s.render_boxes:
                if b.role == "bullet_card":
                    assert b.font_size_pt >= 22.0


def test_pptx_compilation_com_pdf_and_png_previews(tmp_path: Path):
    project = project_storage.get("5221c9f3-65e")
    assert project is not None
    orch = PresentationOrchestrator()
    deck = orch.build_default_deck_from_protocol(project)

    pptx_path = tmp_path / "test_soutenance.pptx"
    compile_presentation_pptx(deck, pptx_path, project=project)
    assert pptx_path.exists() and pptx_path.stat().st_size > 100_000

    prs = Presentation(str(pptx_path))
    assert len(prs.slides) == len(deck.slides)
    assert round(prs.slide_width.inches, 2) == round(SLIDE_WIDTH_IN, 2)

    # Vérifier chaque diapositive : notes + textboxes individuelles sur contenu + chronogramme non déformé
    chrono_idx = next(s.slide_index for s in deck.slides if s.slide_type == "chronogram") - 1
    chrono_slide = prs.slides[chrono_idx]
    pics = [sh for sh in chrono_slide.shapes if sh.shape_type == 13]
    assert len(pics) == 1, "La slide chronogramme ne doit contenir que son image déposée telle quelle"
    pic = pics[0]
    ratio = pic.width.inches / pic.height.inches
    expected_ratio = 1345.0 / 1169.0
    assert abs(ratio - expected_ratio) < 0.03, f"Ratio chronogramme déformé : {ratio} vs {expected_ratio}"

    # Vérifier police >= 24pt et textboxes individuelles sur la première slide Objectifs spécifiques
    os_idx = next(s.slide_index for s in deck.slides if s.slide_type == "objective_specific") - 1
    os_slide = prs.slides[os_idx]
    body_textboxes = [
        sh for sh in os_slide.shapes
        if sh.shape_type == 17 and sh.has_text_frame and "• " in sh.text_frame.text
    ]
    assert len(body_textboxes) >= 2, "Chaque puce doit posséder sa propre TextBox éditable au premier plan"
    for tb in body_textboxes:
        assert tb.text_frame.paragraphs[0].font.size.pt >= 24.0

    # Export PDF + PNG via Microsoft PowerPoint COM
    pdf_path = tmp_path / "test_soutenance.pdf"
    export_pptx_to_pdf(pptx_path, pdf_path)
    assert pdf_path.exists() and pdf_path.stat().st_size > 50_000

    previews_dir = tmp_path / "previews"
    pngs = render_pptx_previews(pptx_path, previews_dir)
    assert len(pngs) == len(deck.slides)
    with Image.open(pngs[0]) as im:
        assert im.size == (1280, 720)


def test_deterministic_audit_report():
    project = project_storage.get("5221c9f3-65e")
    orch = PresentationOrchestrator()
    deck = orch.build_default_deck_from_protocol(project)
    report = orch.audit_presentation_deck(project, deck)
    assert report.total_slides == len(deck.slides)
    assert 0 <= report.score <= 100
    assert report.estimated_duration_sec > 0


def test_api_presentation_endpoints():
    client = TestClient(app)

    r_reset = client.post("/api/projects/5221c9f3-65e/presentation/reset-from-protocol")
    assert r_reset.status_code == 200, r_reset.text
    deck = r_reset.json()
    assert len(deck["slides"]) >= 17

    r_get = client.get("/api/projects/5221c9f3-65e/presentation")
    assert r_get.status_code == 200

    r_audit = client.post("/api/projects/5221c9f3-65e/presentation/audit")
    assert r_audit.status_code == 200, r_audit.text
    assert r_audit.json()["total_slides"] == len(deck["slides"])

    r_pptx = client.get("/api/projects/5221c9f3-65e/presentation/export/pptx")
    assert r_pptx.status_code == 200 and len(r_pptx.content) > 100_000

    r_pdf = client.get("/api/projects/5221c9f3-65e/presentation/export/pdf")
    assert r_pdf.status_code == 200 and len(r_pdf.content) > 50_000

    r_prev = client.post("/api/projects/5221c9f3-65e/presentation/render-previews")
    assert r_prev.status_code == 200, r_prev.text
    prev_data = r_prev.json()
    assert prev_data["count"] == len(deck["slides"])


def test_partial_protocol_welcome_first_and_no_foreign_data():
    import json
    import re

    client = TestClient(app)

    r_get = client.get("/api/projects/113fa64e-056/presentation")
    assert r_get.status_code == 200, r_get.text
    deck = r_get.json()

    assert deck["version"] == "3.1.0"
    slides = deck["slides"]
    assert len(slides) >= 10
    assert slides[0]["slide_type"] == "welcome"
    assert slides[1]["slide_type"] == "cover"
    assert slides[2]["slide_type"] == "plan"

    deck_text = json.dumps(deck, ensure_ascii=False).lower()
    assert "cycle menstruel" in deck_text

    forbidden_terms = [
        "testiculaire",
        "cryptorchidie",
        "occlusion",
        "1 250 000",
        "kouayi",
        "bang guy",
        "a2. sexe",
    ]
    for term in forbidden_terms:
        assert term not in deck_text, f"Unexpected foreign/seed term found: {term}"
    assert not re.search(r"\boia\b", deck_text), "Unexpected foreign acronym 'oia' found"

    budget_slides = [s for s in slides if s["slide_type"] == "budget"]
    assert len(budget_slides) >= 1
    assert budget_slides[0]["subtitle"] == "À budgétiser"
    assert budget_slides[0]["elements"] == []

    obj_slide = next(s for s in slides if s["slide_type"] == "objective_general")
    target_num = obj_slide["slide_index"]
    r_gen = client.post(
        f"/api/projects/113fa64e-056/presentation/generate-slide/{target_num}",
        json={},
    )
    assert r_gen.status_code == 200, r_gen.text
    assert r_gen.json()["status"] == "missing_source", r_gen.json()


if __name__ == "__main__":
    test_real_protocol_extraction_and_no_fake_oia()
    print("[OK] test_real_protocol_extraction_and_no_fake_oia")
    test_slide_layout_engine_overflow_split_and_bounds()
    print("[OK] test_slide_layout_engine_overflow_split_and_bounds")
    with tempfile.TemporaryDirectory() as td:
        test_pptx_compilation_com_pdf_and_png_previews(Path(td))
    print("[OK] test_pptx_compilation_com_pdf_and_png_previews")
    test_deterministic_audit_report()
    print("[OK] test_deterministic_audit_report")
    test_api_presentation_endpoints()
    print("[OK] test_api_presentation_endpoints")
    test_partial_protocol_welcome_first_and_no_foreign_data()
    print("[OK] test_partial_protocol_welcome_first_and_no_foreign_data")
    print("ALL PHASE 4 TESTS PASSED")
