from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.services.docx_template import ensure_master_template
from tests._auth_helper import login_as_admin


def run_phase1_smoke_tests() -> None:
    master_path = ensure_master_template()
    assert master_path.exists(), "Master template docx was not generated"
    print(f"[OK] Master template generated at: {master_path}")

    client = TestClient(app)
    login_as_admin(client)

    health = client.get("/api/health")
    assert health.status_code == 200
    print(f"[OK] /api/health -> {health.json()}")

    canvas = client.get("/api/canvas")
    assert canvas.status_code == 200
    canvas_data = canvas.json()
    assert canvas_data["total_steps"] >= 20
    print(f"[OK] /api/canvas -> {canvas_data['total_steps']} steps ({canvas_data['version']})")

    models = client.get("/api/models")
    assert models.status_code == 200
    models_data = models.json()
    print(f"[INFO] /api/models connected={models_data['connected']}")
    if models_data["connected"]:
        ids = [m["id"] for m in models_data["available_models"]]
        print(f"[OK] Available Gemini models ({len(ids)}): {ids[:15]}")
    else:
        print(f"[WARN] Gemini models error: {models_data.get('error')}")

    created = client.post(
        "/api/projects",
        json={
            "name": "Test Protocole HTA Yaoundé",
            "entry_mode": "new_topic",
            "research_idea": "Prévalence et facteurs associés à l'HTA chez l'adulte à Yaoundé",
            "study_type": "Étude transversale analytique",
        },
    )
    assert created.status_code == 200
    project_id = created.json()["project_id"]
    print(f"[OK] Created project: {project_id}")

    exported = client.post(f"/api/projects/{project_id}/export/docx")
    assert exported.status_code == 200
    assert len(exported.content) > 10000
    print(f"[OK] Exported .docx size: {len(exported.content)} bytes")

    deleted = client.delete(f"/api/projects/{project_id}")
    assert deleted.status_code == 200
    print("[OK] Cleaned up smoke-test project")


if __name__ == "__main__":
    run_phase1_smoke_tests()
