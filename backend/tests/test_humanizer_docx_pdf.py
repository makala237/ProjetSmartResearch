from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.main import app
from app.services.docx_template import compile_protocol_docx
from app.services.humanizer import (
    apply_human_author_metadata_pdf,
    compute_humanization_metrics,
    sanitize_llm_french_typography,
    verify_and_restore_scientific_invariants,
)
from app.services.storage import project_storage
from tests._auth_helper import login_as_admin


def test_sanitize_llm_typography_and_cliches() -> None:
    raw = (
        "Il convient de noter que l'hypertension artérielle\u200b — un facteur majeur — "
        "touche 32,4 % des adultes [1].\n"
        "Force est de constater que ce phénomène joue un rôle crucial dans la morbi-mortalité [2].\n"
        "Notons toutefois que HTA reste sous-diagnostiquée sur la période 2025—2026.\n"
        "Formule : $$n = \\frac{Z^2 \\cdot p(1-p)}{d^2}$$\n"
        "| Variable | Valeur |\n"
        "| --- | --- |\n"
        "| Âge | 45 ans |"
    )
    cleaned, n_changes = sanitize_llm_french_typography(raw)
    assert n_changes >= 4, f"Expected >= 4 cleanups, got {n_changes}"
    assert "\u200b" not in cleaned
    assert "Il convient de noter" not in cleaned
    assert "Force est de constater" not in cleaned
    assert "joue un rôle crucial" not in cleaned
    assert "intervient directement dans" in cleaned
    assert "Toutefois, HTA reste" in cleaned
    assert "2025–2026" in cleaned
    assert "un facteur majeur" in cleaned
    assert " — " not in cleaned
    # Protected LaTeX & Markdown table dividers remain intact
    assert "$$n = \\frac{Z^2 \\cdot p(1-p)}{d^2}$$" in cleaned
    assert "| --- | --- |" in cleaned
    assert "[1]" in cleaned and "[2]" in cleaned

    m_before = compute_humanization_metrics(raw)
    m_after = compute_humanization_metrics(cleaned)
    assert m_after["human_style_score"] > m_before["human_style_score"]


def test_scientific_invariants_guard() -> None:
    original = (
        "La prévalence était de 28,5 % (p < 0,01) à Yaoundé [1]. "
        "Les complications cardiaques atteignaient 14,2 % chez les patients suivis [2].\n\n"
        "Le suivi hospitalier a porté sur n = 120 patients [3]."
    )
    # Simulate an AI rewrite that accidentally dropped [2], dropped p < 0,01, and hallucinated [9]
    bad_candidate = (
        "À Yaoundé, la fréquence atteignait 28,5 % chez les sujets inclus [1] [9]. "
        "Les complications cardiaques s'élevaient à 14,2 % dans la cohorte.\n\n"
        "Au total, n = 120 patients ont été suivis à l'hôpital [3]."
    )
    verified, report = verify_and_restore_scientific_invariants(original, bad_candidate)
    assert report["citations_preserved"] is True
    assert 9 in report["invented_removed"]
    assert "[1]" in verified and "[2]" in verified and "[3]" in verified
    assert "[9]" not in verified
    assert "28,5 %" in verified


def test_humanize_routes_and_docx_pdf_author_metadata() -> None:
    created_project_id = None
    client = TestClient(app)
    login_as_admin(client)

    try:
        r_proj = client.post(
            "/api/projects",
            json={
                "name": f"Test Humanizer {uuid4().hex[:6]}",
                "entry_mode": "new_topic",
                "research_idea": "Prévalence de l'HTA chez l'adulte au CHU de Yaoundé",
                "study_type": "Étude transversale descriptive",
            },
        )
        assert r_proj.status_code == 200, r_proj.text
        proj_data = r_proj.json()
        created_project_id = proj_data["project_id"]

        # Update cover metadata with real student name
        cover = dict(proj_data["cover"])
        cover["author_name"] = "Dr MBALLA Jean-Paul"
        cover["title"] = "Facteurs associés à l'hypertension artérielle à Yaoundé"
        cover["department_fr"] = "MÉDECINE INTERNE"
        r_cov = client.patch(f"/api/projects/{created_project_id}/cover", json=cover)
        assert r_cov.status_code == 200, r_cov.text

        # Seed a narrative section containing LLM clichés, em-dashes, invisible char, and citations
        raw_md = (
            "Il convient de noter que l'hypertension artérielle\u200b — pathologie chronique silencieuse — "
            "affecte 31,5 % des adultes au Cameroun [1].\n\n"
            "Force est de constater que ce fardeau joue un rôle crucial dans la survenue des AVC [2]."
        )
        r_sec = client.patch(
            f"/api/projects/{created_project_id}/sections/intro_contexte",
            json={
                "content_markdown": raw_md,
                "status": "in_progress",
                "user_notes": "",
            },
        )
        assert r_sec.status_code == 200, r_sec.text

        # Test section humanize route (use_ai=false exercises deterministic pipeline & DB persistence)
        r_hum_sec = client.post(
            f"/api/projects/{created_project_id}/sections/intro_contexte/humanize",
            json={"use_ai": False},
        )
        assert r_hum_sec.status_code == 200, r_hum_sec.text
        hum_res = r_hum_sec.json()["result"]
        assert hum_res["skipped"] is False
        assert hum_res["citations_preserved"] is True
        assert "Il convient de noter" not in hum_res["content_markdown"]
        assert "\u200b" not in hum_res["content_markdown"]
        assert "[1]" in hum_res["content_markdown"] and "[2]" in hum_res["content_markdown"]

        # Test protocol-wide humanize route
        r_hum_all = client.post(
            f"/api/projects/{created_project_id}/humanize-all",
            json={"use_ai": False},
        )
        assert r_hum_all.status_code == 200, r_hum_all.text
        report = r_hum_all.json()["report"]
        assert report["sections_humanized"] >= 1
        assert report["citations_preserved"] is True
        assert report["author_signature"] == "Dr MBALLA Jean-Paul"

        # Compile DOCX and verify real student author metadata & clean body text
        proj_obj = project_storage.get(created_project_id)
        assert proj_obj is not None
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            docx_out = tmp_path / "protocole_test.docx"
            compile_protocol_docx(proj_obj, docx_out)
            assert docx_out.exists()

            doc = Document(str(docx_out))
            assert doc.core_properties.author == "Dr MBALLA Jean-Paul"
            assert doc.core_properties.last_modified_by == "Dr MBALLA Jean-Paul"
            assert "python-docx" not in (doc.core_properties.comments or "").lower()
            assert "Facteurs associés" in (doc.core_properties.title or "")

            full_docx_text = "\n".join(p.text for p in doc.paragraphs)
            assert "Il convient de noter" not in full_docx_text
            assert "Force est de constater" not in full_docx_text
            assert "\u200b" not in full_docx_text

            # Verify PDF author/producer metadata rewriter directly via pypdf
            pdf_out = tmp_path / "protocole_test.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with pdf_out.open("wb") as fh:
                writer.write(fh)

            apply_human_author_metadata_pdf(pdf_out, proj_obj)
            reader = PdfReader(str(pdf_out))
            meta = reader.metadata
            assert meta is not None
            assert meta.get("/Author") == "Dr MBALLA Jean-Paul"
            assert meta.get("/Creator") == "Microsoft® Word"
            assert meta.get("/Producer") == "Microsoft® Word"
            assert "Facteurs associés" in (meta.get("/Title") or "")

    finally:
        if created_project_id:
            try:
                client.delete(f"/api/projects/{created_project_id}")
            except Exception:
                pass
            export_docx = get_settings().storage_dir / "exports" / f"{created_project_id}_protocole.docx"
            export_docx.unlink(missing_ok=True)


if __name__ == "__main__":
    test_sanitize_llm_typography_and_cliches()
    test_scientific_invariants_guard()
    test_humanize_routes_and_docx_pdf_author_metadata()
    print("ALL HUMANIZER & DOCX/PDF METADATA TESTS PASSED.")
