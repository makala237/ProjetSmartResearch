from pathlib import Path
import sys
from uuid import uuid4

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app
from tests._auth_helper import login_as_admin


def test_coherence_sentinel_detects_and_harmonizes_objectives_vs_study_type() -> None:
    created_project_id = None
    client = TestClient(app)
    login_as_admin(client)

    try:
        r_proj = client.post(
            "/api/projects",
            json={
                "name": f"Test Coherence Sentinel {uuid4().hex[:6]}",
                "entry_mode": "new_topic",
                "research_idea": "Hypertension artérielle et complications chez l'adulte à Yaoundé",
                "study_type": "Étude transversale descriptive",
            },
        )
        assert r_proj.status_code == 200, r_proj.text
        created_project_id = r_proj.json()["project_id"]

        # 1. Draft Type d'étude initially as purely descriptive
        r_design = client.patch(
            f"/api/projects/{created_project_id}/sections/chap3_type_site_duree",
            json={
                "content_markdown": (
                    "Il s'agira d'une étude transversale purement descriptive qui se déroulera "
                    "au Centre Hospitalier Universitaire de Yaoundé sur une durée totale de 6 mois."
                ),
                "status": "in_progress",
                "user_notes": "",
            },
        )
        assert r_design.status_code == 200, r_design.text

        # 2. Student adds an analytical objective ("Identifier les facteurs associés") later on
        r_obj = client.patch(
            f"/api/projects/{created_project_id}/sections/chap1_objectifs",
            json={
                "content_markdown": (
                    "Objectif général : Étudier l'hypertension artérielle au CHU de Yaoundé.\n"
                    "Objectifs spécifiques :\n"
                    "1. Décrire le profil sociodémographique et clinique des patients.\n"
                    "2. Identifier les facteurs associés aux complications cardiovasculaires."
                ),
                "status": "in_progress",
                "user_notes": "",
            },
        )
        assert r_obj.status_code == 200, r_obj.text
        proj_after_obj = r_obj.json()
        alerts = proj_after_obj.get("coherence_alerts") or []
        matching = [
            a
            for a in alerts
            if a["axis"] == "objectives_vs_study_type"
            and a["target_section_id"] == "chap3_type_site_duree"
        ]
        assert len(matching) == 1, f"Expected objectives_vs_study_type alert, got {alerts}"
        alert_id = matching[0]["alert_id"]
        assert matching[0]["suggested_study_type"] == "Étude transversale descriptive et analytique"

        # 3. Also test Axis 3: descriptive-only Data Analysis triggers missing analytical tests alert
        r_stats = client.patch(
            f"/api/projects/{created_project_id}/sections/chap3_analyse_donnees",
            json={
                "content_markdown": (
                    "Les données recueillies seront saisies et vérifiées soigneusement avant exportation. "
                    "L'analyse statistique consistera uniquement à calculer des effectifs, des fréquences en "
                    "pourcentage et des moyennes accompagnées de leurs écarts-types pour chaque variable."
                ),
                "status": "in_progress",
                "user_notes": "",
            },
        )
        assert r_stats.status_code == 200, r_stats.text
        stats_alerts = r_stats.json().get("coherence_alerts") or []
        assert any(
            a["axis"] == "objectives_vs_data_analysis" and a["target_section_id"] == "chap3_analyse_donnees"
            for a in stats_alerts
        ), f"Expected objectives_vs_data_analysis alert, got {stats_alerts}"

        # 4. Trigger 1-click harmonization on chap3_type_site_duree
        r_harm = client.post(
            f"/api/projects/{created_project_id}/sections/chap3_type_site_duree/harmonize",
            json={"alert_id": alert_id},
        )
        assert r_harm.status_code == 200, r_harm.text
        harm_payload = r_harm.json()
        res = harm_payload["result"]
        proj_after_harm = harm_payload["project"]

        assert res["harmonized"] is True
        assert proj_after_harm["study_type"] == "Étude transversale descriptive et analytique"
        new_design_md = proj_after_harm["sections"]["chap3_type_site_duree"]["content_markdown"]
        assert "analytique" in new_design_md.lower()
        assert "Yaoundé" in new_design_md
        assert "6 mois" in new_design_md

        # The resolved objectives_vs_study_type alert must no longer exist
        remaining_alerts = proj_after_harm.get("coherence_alerts") or []
        assert not any(
            a["axis"] == "objectives_vs_study_type" for a in remaining_alerts
        ), f"Alert should be cleared after harmonization: {remaining_alerts}"

    finally:
        if created_project_id:
            try:
                client.delete(f"/api/projects/{created_project_id}")
            except Exception:
                pass


if __name__ == "__main__":
    test_coherence_sentinel_detects_and_harmonizes_objectives_vs_study_type()
    print("ALL COHERENCE SENTINEL TESTS PASSED.")
