import io
import sys
import tempfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app
from app.services.cover_builder import build_cover_header_lines, format_contact_tier_lines
from app.services.docx_template import compile_protocol_docx
from app.services.pptx_exporter import compile_presentation_pptx
from app.services.presentation_orchestrator import PresentationOrchestrator
from app.services.storage import project_storage
from tests._auth_helper import login_as_admin


def _make_dummy_png_bytes(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (140, 140), color)
    im.save(buf, format="PNG")
    return buf.getvalue()


def _cell_lines(cell) -> list[str]:
    return [p.text.strip() for p in cell.paragraphs if p.text.strip()]


def test_optional_contact_tiers_omitted_when_empty():
    project = project_storage.get("5221c9f3-65e")
    assert project is not None

    with tempfile.TemporaryDirectory() as td:
        docx_path = Path(td) / "default_cover.docx"
        compile_protocol_docx(project, docx_path)
        doc = Document(str(docx_path))
        t0 = doc.tables[0]
        fr_lines = _cell_lines(t0.rows[0].cells[0])
        en_lines = _cell_lines(t0.rows[0].cells[2])
        fr_joined = "\n".join(fr_lines)
        en_joined = "\n".join(en_lines)

        assert "UNIVERSITÉ DE YAOUNDÉ I" in fr_joined
        assert "THE UNIVERSITY OF YAOUNDÉ I" in en_joined
        assert "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES" in fr_joined
        assert "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES" in en_joined
        assert "B.P." not in fr_joined
        assert "Courriel" not in fr_joined
        assert "P.O. Box" not in en_joined
        assert "E-mail" not in en_joined

        # Default single logo in center cell
        blips = list(t0.rows[0].cells[1]._tc.iter(qn("a:blip")))
        assert len(blips) == 1

    # Default PPTX cover should also have zero optional contact tiers & 1 logo
    deck = PresentationOrchestrator().build_default_deck_from_protocol(project)
    cover_slide = next(s for s in deck.slides if s.slide_type == "cover")
    roles = {b.role for b in cover_slide.render_boxes}
    assert "cover_logo" in roles
    assert "cover_faculty_logo" not in roles
    hdr_fr = next(b for b in cover_slide.render_boxes if b.role == "cover_header_fr")
    assert "B.P." not in hdr_fr.text and "Courriel" not in hdr_fr.text


def test_bilingual_translation_and_multi_university_cover_e2e():
    client = TestClient(app)
    login_as_admin(client)

    # 1. Test live translation helper for university & faculty fields
    r_tr_u = client.post(
        "/api/translate-department",
        json={
            "text": "UNIVERSITÉ DE DOUALA",
            "source_lang": "fr",
            "target_lang": "en",
            "field_type": "university",
        },
    )
    assert r_tr_u.status_code == 200, r_tr_u.text
    assert "DOUALA" in r_tr_u.json()["translated_text"].upper()
    assert "UNIVERSITY" in r_tr_u.json()["translated_text"].upper()

    r_tr_f = client.post(
        "/api/translate-department",
        json={
            "text": "FACULTÉ DE MÉDECINE ET DES SCIENCES PHARMACEUTIQUES",
            "source_lang": "fr",
            "target_lang": "en",
            "field_type": "faculty",
        },
    )
    assert r_tr_f.status_code == 200, r_tr_f.text
    assert "FACULTY OF MEDICINE" in r_tr_f.json()["translated_text"].upper()

    # 2. Create disposable test project
    r_create = client.post(
        "/api/projects",
        json={
            "name": "Test Multi-Universités CAMES",
            "entry_mode": "new_topic",
            "research_idea": "Profil épidémiologique à l'Hôpital Général de Douala",
            "study_type": "Étude transversale",
        },
    )
    assert r_create.status_code == 200, r_create.text
    proj = r_create.json()
    pid = proj["project_id"]

    try:
        # 3. Upload University logo + Faculty logo
        uni_png = _make_dummy_png_bytes((15, 76, 129))
        fac_png = _make_dummy_png_bytes((108, 40, 38))

        r_up_u = client.post(
            f"/api/projects/{pid}/cover/logo/university",
            files={"file": ("uni_logo.png", uni_png, "image/png")},
        )
        assert r_up_u.status_code == 200, r_up_u.text
        assert r_up_u.json()["cover"]["university_logo_url"].startswith(
            f"/api/projects/{pid}/cover/logo/university"
        )

        r_up_f = client.post(
            f"/api/projects/{pid}/cover/logo/faculty",
            files={"file": ("fac_logo.png", fac_png, "image/png")},
        )
        assert r_up_f.status_code == 200, r_up_f.text
        assert r_up_f.json()["cover"]["faculty_logo_url"].startswith(
            f"/api/projects/{pid}/cover/logo/faculty"
        )

        r_get_f = client.get(f"/api/projects/{pid}/cover/logo/faculty")
        assert r_get_f.status_code == 200 and r_get_f.content.startswith(b"\x89PNG")

        # 4. Update cover metadata with custom university, faculty, and BOTH optional contact tiers
        cover_payload = r_up_f.json()["cover"]
        cover_payload.update(
            {
                "university_fr": "UNIVERSITÉ DE DOUALA",
                "university_en": "THE UNIVERSITY OF DOUALA",
                "university_po_box": "2701 Douala",
                "university_email": "rectorat@univ-douala.com",
                "faculty_fr": "FACULTÉ DE MÉDECINE ET DES SCIENCES PHARMACEUTIQUES",
                "faculty_en": "FACULTY OF MEDICINE AND PHARMACEUTICAL SCIENCES",
                "department_fr": "SANTÉ PUBLIQUE",
                "department_en": "PUBLIC HEALTH",
                "faculty_po_box": "2701 Douala",
                "faculty_email": "decanat@fmsp-douala.cm",
            }
        )
        r_cov = client.patch(f"/api/projects/{pid}/cover", json=cover_payload)
        assert r_cov.status_code == 200, r_cov.text

        # 5. Compile Word .docx & verify tier order + bilingual contacts + 2 stacked logos
        loaded = project_storage.get(pid)
        assert loaded is not None
        with tempfile.TemporaryDirectory() as td:
            docx_out = Path(td) / "multi_uni.docx"
            compile_protocol_docx(loaded, docx_out)
            doc = Document(str(docx_out))
            t0 = doc.tables[0]
            fr_lines = _cell_lines(t0.rows[0].cells[0])
            en_lines = _cell_lines(t0.rows[0].cells[2])

            assert fr_lines.index("UNIVERSITÉ DE DOUALA") < fr_lines.index("B.P. : 2701 Douala")
            assert fr_lines.index("B.P. : 2701 Douala") < fr_lines.index("Courriel : rectorat@univ-douala.com")
            assert fr_lines.index("Courriel : rectorat@univ-douala.com") < fr_lines.index(
                "FACULTÉ DE MÉDECINE ET DES SCIENCES PHARMACEUTIQUES"
            )
            assert fr_lines.index("SANTÉ PUBLIQUE") < fr_lines.index("Courriel : decanat@fmsp-douala.cm")

            assert en_lines.index("THE UNIVERSITY OF DOUALA") < en_lines.index("P.O. Box: 2701 Douala")
            assert en_lines.index("P.O. Box: 2701 Douala") < en_lines.index("E-mail: rectorat@univ-douala.com")
            assert en_lines.index("E-mail: rectorat@univ-douala.com") < en_lines.index(
                "FACULTY OF MEDICINE AND PHARMACEUTICAL SCIENCES"
            )
            assert en_lines.index("PUBLIC HEALTH") < en_lines.index("E-mail: decanat@fmsp-douala.cm")

            # Verify 2 logos embedded vertically in center cell
            blips = list(t0.rows[0].cells[1]._tc.iter(qn("a:blip")))
            assert len(blips) == 2

            # 6. Verify PPTX cover layout boxes & exported .pptx
            deck = PresentationOrchestrator().build_default_deck_from_protocol(loaded)
            cover_slide = next(s for s in deck.slides if s.slide_type == "cover")
            box_map = {b.role: b for b in cover_slide.render_boxes}
            assert "cover_logo" in box_map and "cover_faculty_logo" in box_map
            assert box_map["cover_faculty_logo"].y_pct > box_map["cover_logo"].y_pct + box_map["cover_logo"].h_pct - 0.001
            assert box_map["cover_title"].y_pct > box_map["cover_header_fr"].y_pct + box_map["cover_header_fr"].h_pct
            assert "B.P. : 2701 Douala" in box_map["cover_header_fr"].text
            assert "Courriel : decanat@fmsp-douala.cm" in box_map["cover_header_fr"].text
            assert "P.O. Box: 2701 Douala" in box_map["cover_header_en"].text
            assert "E-mail: decanat@fmsp-douala.cm" in box_map["cover_header_en"].text

            pptx_out = Path(td) / "multi_uni.pptx"
            compile_presentation_pptx(deck, pptx_out, project=loaded)
            prs = Presentation(str(pptx_out))
            pptx_cover = prs.slides[1]
            # 1 lower background + 2 stacked logos = 3 pictures on cover slide
            pics = [sh for sh in pptx_cover.shapes if sh.shape_type == 13]
            assert len(pics) == 3

        # 7. Delete faculty logo & clear university contacts -> ensure clean removal
        r_del_f = client.delete(f"/api/projects/{pid}/cover/logo/faculty")
        assert r_del_f.status_code == 200
        assert r_del_f.json()["cover"]["faculty_logo_url"] == ""
        cov2 = r_del_f.json()["cover"]
        cov2["university_po_box"] = ""
        cov2["university_email"] = ""
        client.patch(f"/api/projects/{pid}/cover", json=cov2)

        loaded2 = project_storage.get(pid)
        assert loaded2 is not None
        fr_only, _ = build_cover_header_lines(loaded2.cover)
        joined_fr = "\n".join(ln[0] for ln in fr_only)
        assert "rectorat@univ-douala.com" not in joined_fr
        assert "Courriel : decanat@fmsp-douala.cm" in joined_fr

        uni_c, fac_c = format_contact_tier_lines("", "")
        assert uni_c == [] and fac_c == []
    finally:
        client.delete(f"/api/projects/{pid}")


if __name__ == "__main__":
    test_optional_contact_tiers_omitted_when_empty()
    print("[OK] test_optional_contact_tiers_omitted_when_empty")
    test_bilingual_translation_and_multi_university_cover_e2e()
    print("[OK] test_bilingual_translation_and_multi_university_cover_e2e")
    print("ALL MULTI-UNIVERSITY COVER TESTS PASSED")
