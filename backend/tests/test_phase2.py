import io
from docx import Document
from fastapi.testclient import TestClient

from app.main import app
from tests._auth_helper import login_as_admin


def run_phase2_tests() -> None:
    client = TestClient(app)
    login_as_admin(client)

    # 1. Create a real medical research project
    created = client.post(
        "/api/projects",
        json={
            "name": "Facteurs associés à l'hypertension artérielle à Yaoundé",
            "entry_mode": "new_topic",
            "research_idea": "Prévalence et facteurs associés à l'hypertension artérielle chez l'adulte à Yaoundé, Cameroun",
            "study_type": "Étude transversale analytique",
        },
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]
    print(f"[OK] Project created: {project_id}")

    try:
        # 2. Run literature search (Gemini 3.8-flash planner + PubMed/EuropePMC/OpenAlex + Web)
        res_search = client.post(
            f"/api/projects/{project_id}/research",
            json={"max_per_source": 2, "include_web_grounding": True},
        )
        assert res_search.status_code == 200, res_search.text
        search_data = res_search.json()
        rep = search_data["report"]
        print(
            f"[OK] Research pipeline: planner={rep['planner_model']}, "
            f"added_refs={rep['added_references_count']}, "
            f"total_refs={rep['total_references_count']}, "
            f"images={rep['added_images_count']}"
        )
        if rep["source_errors"]:
            print(f"[INFO] Source warnings: {rep['source_errors']}")
        assert rep["total_references_count"] > 0, "Expected at least 1 reference"

        # 3. Segment retrieved articles using gemini-3.1-flash-lite
        res_seg = client.post(
            f"/api/projects/{project_id}/segment",
            json={"limit": 3, "only_unsegmented": True},
        )
        assert res_seg.status_code == 200, res_seg.text
        seg_rep = res_seg.json()["report"]
        print(
            f"[OK] Segmentation: processed={seg_rep['processed_articles']}, "
            f"added_segments={seg_rep['added_segments']}, models={seg_rep['models_used']}"
        )
        assert seg_rep["added_segments"] > 0, "Expected at least 1 segmented extract"

        # 4. Draft intro_contexte with AI grounded in segments
        res_draft = client.post(
            f"/api/projects/{project_id}/sections/intro_contexte/draft",
            json={"instruction": "Rédige un contexte concis (Monde, Afrique, Cameroun) avec maximum 3 citations."},
        )
        assert res_draft.status_code == 200, res_draft.text
        draft_res = res_draft.json()["result"]
        print(
            f"[OK] Drafted intro_contexte with {draft_res['model_used']}: "
            f"{len(draft_res['content_markdown'])} chars, cited={len(draft_res['cited_ref_ids'])}"
        )
        assert len(draft_res["content_markdown"]) > 100

        # 5. Audit intro_contexte
        res_audit = client.post(
            f"/api/projects/{project_id}/sections/intro_contexte/audit"
        )
        assert res_audit.status_code == 200, res_audit.text
        audit_res = res_audit.json()["result"]
        print(
            f"[OK] Audit intro_contexte ({audit_res['model_used']}): "
            f"compliant={audit_res['compliant']}, issues={len(audit_res['issues'])}"
        )

        # 6. Test Mode B import with an in-memory .docx draft
        doc = Document()
        doc.add_heading("INTRODUCTION", level=1)
        doc.add_paragraph(
            "L'hypertension artérielle constitue un problème majeur de santé publique au Cameroun."
        )
        doc.add_heading("OBJECTIFS", level=1)
        doc.add_paragraph("Objectif général : Déterminer la prévalence de l'HTA à Yaoundé.")
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)

        res_imp = client.post(
            f"/api/projects/{project_id}/import",
            files={
                "file": (
                    "brouillon_test.docx",
                    buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert res_imp.status_code == 200, res_imp.text
        imp_rep = res_imp.json()["report"]
        print(
            f"[OK] Imported .docx via {imp_rep['model_used']}: "
            f"mapped_sections={imp_rep['mapped_sections_count']}"
        )
        assert imp_rep["mapped_sections_count"] >= 1
    finally:
        client.delete(f"/api/projects/{project_id}")
        print("[OK] Cleaned up Phase 2 test project")


if __name__ == "__main__":
    run_phase2_tests()
