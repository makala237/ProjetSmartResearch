from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.services.annexes_generator import build_annexes_pack
from app.services.docx_template import compile_protocol_docx
from app.services.storage import project_storage
from tests._auth_helper import login_as_admin


def run_phase3_tests() -> None:
    client = TestClient(app)
    login_as_admin(client)

    # 1. Create a project
    res = client.post(
        "/api/projects",
        json={
            "name": "Étude laparoscopie testiculaire FMSB",
            "entry_mode": "new_topic",
            "research_idea": "Intérêt de l'abord laparoscopique dans la prise en charge des testicules non palpables chez l'enfant",
            "study_type": "Étude transversale descriptive et analytique",
        },
    )
    assert res.status_code == 200, res.text
    project_id = res.json()["project_id"]
    print(f"[OK] Project created: {project_id}")

    try:
        # 2. Update cover with student details (for annexes pre-filling)
        cover_res = client.patch(
            f"/api/projects/{project_id}/cover",
            json={
                "title": "Abord laparoscopique dans les testicules non palpables à Yaoundé",
                "degree_label": "Doctorat en Médecine",
                "department_fr": "CHIRURGIE ET SPÉCIALITÉS",
                "department_en": "SURGERY AND SPECIALTIES",
                "author_name": "Kouayi Ngnamfa Duchelle",
                "matricule": "20M1493",
                "student_level": "Étudiante en 6ème année de médecine générale",
                "student_phone": "6 91 38 46 52",
                "student_email": "ngnamfaduchelle04@icloud.com",
                "director": "Pr. Bang Guy Aristide (Chirurgie pédiatrique)",
                "co_directors": ["Dr. Nwaha Makon Axel"],
                "academic_year": "2026-2027",
                "study_period_label": "1er Juillet 2026 au 31 Mars 2027",
                "host_institutions": [
                    "Hôpital Général de Yaoundé",
                    "Hôpital Gynéco-Obstétrique et Pédiatrique de Yaoundé",
                ],
                "ethics_committees": [
                    "Comité Institutionnel d'Éthique de la Recherche de la FMSB",
                    "Comité d'Éthique de l'Hôpital Général de Yaoundé",
                ],
            },
        )
        assert cover_res.status_code == 200, cover_res.text
        print("[OK] Cover & Student metadata updated")

        # 3. Update structured data (tables & annexes config)
        struct_res = client.patch(
            f"/api/projects/{project_id}/structured",
            json={
                "abbreviations": [
                    {"acronym": "TNP", "full_form": "Testicule Non Palpable"},
                    {"acronym": "HGY", "full_form": "Hôpital Général de Yaoundé"},
                    {"acronym": "HGOPY", "full_form": "Hôpital Gynéco-Obstétrique et Pédiatrique de Yaoundé"},
                ],
                "variables": [
                    {
                        "name": "Âge au diagnostic",
                        "category": "Données sociodémographiques",
                        "role": "Indépendante",
                        "definition": "Âge en mois ou années révolues",
                        "measurement_scale": "Quantitative",
                        "modalities": "____ mois",
                        "collection_tool": "Fiche de collecte",
                    },
                    {
                        "name": "Siège laparoscopique",
                        "category": "Données opératoires",
                        "role": "Dépendante",
                        "definition": "Position anatomique intra-abdominale",
                        "measurement_scale": "Qualitative nominale",
                        "modalities": "[ ] Haut   [ ] Bas   [ ] Anneau inguinal profond",
                        "collection_tool": "Protocole opératoire",
                    },
                ],
                "literature_synthesis": [
                    {
                        "authors_country_year": "Kouayi et al., Cameroun (2024)",
                        "study_design": "Série de cas prospective",
                        "population": "N=42 enfants avec TNP",
                        "main_results": "Succès laparoscopique dans 88% des cas, morbidité faible",
                        "limits": "Effectif modéré",
                    }
                ],
                "budget": [
                    {"category": "Matériel", "designation": "Impression protocole", "quantity": 10, "unit_cost_fcfa": 5000, "total_fcfa": 50000},
                    {"category": "Éthique", "designation": "Frais clairance CIER-FMSB", "quantity": 1, "unit_cost_fcfa": 25000, "total_fcfa": 25000},
                    {"category": "Logistique", "designation": "Autorisations HGY & HGOPY", "quantity": 2, "unit_cost_fcfa": 20000, "total_fcfa": 40000},
                ],
            },
        )
        assert struct_res.status_code == 200, struct_res.text
        print("[OK] Structured tables updated (abbreviations, variables, synthesis, budget)")

        # 4. Preview annexes pack
        ann_res = client.get(f"/api/projects/{project_id}/annexes/preview")
        assert ann_res.status_code == 200, ann_res.text
        ann_list = ann_res.json()["annexes"]
        print(f"[OK] Generated Annexes Pack ({len(ann_list)} annexes): {[a['title'] for a in ann_list[:4]]}")
        assert len(ann_list) >= 5

        # 5. Export comprehensive docx with Phase 3 elements
        project = project_storage.get(project_id)
        assert project is not None
        out_path = Path(f"storage/exports/{project_id}_phase3.docx")
        compile_protocol_docx(project, out_path)
        assert out_path.exists()
        size = out_path.stat().st_size
        print(f"[OK] Compiled complete Phase 3 Word document: {size} bytes ({out_path})")
        assert size > 25000
    finally:
        client.delete(f"/api/projects/{project_id}")
        print("[OK] Cleaned up Phase 3 test project")


if __name__ == "__main__":
    run_phase3_tests()
