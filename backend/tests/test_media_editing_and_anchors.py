import io
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from docx import Document
from fastapi.testclient import TestClient
from PIL import Image as PILImage

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.main import app
from app.services.docx_template import compile_protocol_docx
from app.services.storage import project_storage
from tests._auth_helper import login_as_admin


def _make_tiny_png_bytes() -> bytes:
    img = PILImage.new("RGB", (320, 180), color=(108, 40, 38))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_media_editing_and_exhaustive_anchor_placement() -> None:
    created_project_id = None
    client = TestClient(app)
    login_as_admin(client)

    try:
        r_proj = client.post(
            "/api/projects",
            json={
                "name": f"Test Media Edit {uuid4().hex[:6]}",
                "entry_mode": "new_topic",
                "research_idea": "Complications cardiovasculaires et facteurs associés",
                "study_type": "Étude transversale analytique",
            },
        )
        assert r_proj.status_code == 200, r_proj.text
        created_project_id = r_proj.json()["project_id"]

        # Build a dense section with 3 Markdown headings + 30 distinct sentences inside 3 multi-sentence paragraphs
        para1 = " ".join(
            [f"Phrase initiale numéro {i} décrivant la revue mondiale de la pathologie." for i in range(1, 11)]
        )
        para2 = " ".join(
            [f"Phrase régionale numéro {i} analysant les données hospitalières africaines." for i in range(11, 21)]
        )
        para3 = " ".join(
            [f"Phrase nationale numéro {i} détaillant les observations cliniques au Cameroun." for i in range(21, 32)]
        )
        heading_1 = "Contexte épidémiologique international"
        heading_2 = "Profil épidémiologique régional"
        heading_3 = "Données locales camerounaises"

        long_markdown = (
            f"## {heading_1}\n\n"
            f"{para1}\n\n"
            f"### {heading_2}\n\n"
            f"{para2}\n\n"
            f"### {heading_3}\n\n"
            f"{para3}"
        )

        r_sec = client.patch(
            f"/api/projects/{created_project_id}/sections/chap2_etat_connaissances",
            json={"content_markdown": long_markdown, "status": "in_progress"},
        )
        assert r_sec.status_code == 200, r_sec.text

        # Seed structured variable so __structured_variables__ table exists
        r_struct = client.patch(
            f"/api/projects/{created_project_id}/structured",
            json={
                "variables": [
                    {
                        "name": "Pression artérielle systolique",
                        "category": "Clinique",
                        "role": "Dépendante",
                        "definition": "Mesure en mmHg au repos",
                        "measurement_scale": "Quantitative",
                        "modalities": "mmHg",
                        "collection_tool": "Tensiomètre",
                    }
                ]
            },
        )
        assert r_struct.status_code == 200, r_struct.text

        # 1. Create custom table & then edit its name + move anchor to sentence #25 (middle of para3)
        r_tbl = client.post(
            f"/api/projects/{created_project_id}/sections/chap2_etat_connaissances/tables",
            json={
                "title": "Tableau Initial Littérature",
                "anchor_hint": "",
                "headers": ["Étude", "Résultat"],
                "rows": [["Ndam 2024", "31,2 %"]],
            },
        )
        assert r_tbl.status_code == 200, r_tbl.text
        table_id = r_tbl.json()["sections"]["chap2_etat_connaissances"]["custom_tables"][0]["table_id"]

        sentence_25 = "Phrase nationale numéro 25 détaillant les observations cliniques au Cameroun."
        r_tbl_patch = client.patch(
            f"/api/projects/{created_project_id}/sections/chap2_etat_connaissances/tables/{table_id}",
            json={
                "title": "Tableau Édité Facteurs Locaux",
                "anchor_hint": sentence_25,
            },
        )
        assert r_tbl_patch.status_code == 200, r_tbl_patch.text
        updated_tbl = r_tbl_patch.json()["sections"]["chap2_etat_connaissances"]["custom_tables"][0]
        assert updated_tbl["title"] == "Tableau Édité Facteurs Locaux"
        assert updated_tbl["anchor_hint"] == sentence_25

        # 2. Edit structured table (__structured_variables__) via PATCH
        r_struct_patch = client.patch(
            f"/api/projects/{created_project_id}/sections/chap1_variables/tables/__structured_variables__",
            json={
                "title": "Variables Opérationnelles Modifiées",
                "anchor_hint": "",
            },
        )
        assert r_struct_patch.status_code == 200, r_struct_patch.text
        var_tables = r_struct_patch.json()["sections"]["chap1_variables"]["custom_tables"]
        assert len(var_tables) == 1
        assert var_tables[0]["title"] == "Variables Opérationnelles Modifiées"

        # 3. Create diagram & then edit its name, layout, and move anchor to sentence #15 (middle of para2)
        r_dg = client.post(
            f"/api/projects/{created_project_id}/sections/chap2_etat_connaissances/diagrams",
            json={
                "title": "Schéma Initial",
                "caption": "",
                "anchor_hint": "",
                "layout": "vertical",
                "nodes": [
                    {"id": "n1", "shape": "rounded", "title": "Exposition", "items": ["HTA"]},
                    {"id": "n2", "shape": "rounded", "title": "Issue", "items": ["AVC"]},
                ],
                "connections": [{"from_id": "n1", "to_id": "n2", "label": ""}],
            },
        )
        assert r_dg.status_code == 200, r_dg.text
        dg_id = r_dg.json()["sections"]["chap2_etat_connaissances"]["custom_diagrams"][0]["diagram_id"]

        sentence_15 = "Phrase régionale numéro 15 analysant les données hospitalières africaines."
        r_dg_patch = client.patch(
            f"/api/projects/{created_project_id}/sections/chap2_etat_connaissances/diagrams/{dg_id}",
            json={
                "title": "Schéma Édité Flux Clinique",
                "layout": "horizontal",
                "anchor_hint": sentence_15,
            },
        )
        assert r_dg_patch.status_code == 200, r_dg_patch.text
        updated_dg = r_dg_patch.json()["sections"]["chap2_etat_connaissances"]["custom_diagrams"][0]
        assert updated_dg["title"] == "Schéma Édité Flux Clinique"
        assert updated_dg["layout"] == "horizontal"
        assert updated_dg["anchor_hint"] == sentence_15

        # 4. Upload figure image & then edit its title, caption & anchor to heading_2
        r_img = client.post(
            f"/api/projects/{created_project_id}/images/upload",
            files={"file": ("fig_test.png", _make_tiny_png_bytes(), "image/png")},
            data={
                "target_section_id": "chap2_etat_connaissances",
                "title": "Figure Initiale",
                "caption": "Légende initiale",
                "anchor_hint": "",
            },
        )
        assert r_img.status_code == 200, r_img.text
        asset_id = r_img.json()["images"][0]["asset_id"]

        r_img_patch = client.patch(
            f"/api/projects/{created_project_id}/images/{asset_id}",
            json={
                "title": "Figure Éditée Épidémiologie",
                "caption": "Source adaptée FMSB 2026",
                "anchor_hint": heading_2,
            },
        )
        assert r_img_patch.status_code == 200, r_img_patch.text
        updated_img = r_img_patch.json()["images"][0]
        assert updated_img["title"] == "Figure Éditée Épidémiologie"
        assert updated_img["caption"] == "Source adaptée FMSB 2026"
        assert updated_img["anchor_hint"] == heading_2

        # 5. Compile DOCX and verify exact placement relative to anchored heading & deep paragraph sentences
        proj_obj = project_storage.get(created_project_id)
        assert proj_obj is not None
        with tempfile.TemporaryDirectory() as tmp_dir:
            docx_out = Path(tmp_dir) / "anchors_test.docx"
            compile_protocol_docx(proj_obj, docx_out)
            assert docx_out.exists()

            doc = Document(str(docx_out))
            all_texts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
            idx_h1 = next(i for i, t in enumerate(all_texts) if heading_1 in t)
            texts = all_texts[idx_h1:]

            idx_h2 = next(i for i, t in enumerate(texts) if heading_2 in t)
            idx_para2 = next(i for i, t in enumerate(texts) if sentence_15 in t)
            idx_para3 = next(i for i, t in enumerate(texts) if sentence_25 in t)

            idx_fig_cap = next(i for i, t in enumerate(texts) if "Figure Éditée Épidémiologie" in t)
            idx_dg_cap = next(i for i, t in enumerate(texts) if "Schéma Édité Flux Clinique" in t)
            idx_tbl_cap = next(i for i, t in enumerate(texts) if "Tableau Édité Facteurs Locaux" in t)

            assert idx_h2 < idx_fig_cap < idx_para2, (
                f"Figure must appear right after heading_2 ({idx_h2}) before para2 ({idx_para2}), got {idx_fig_cap}"
            )
            assert idx_para2 < idx_dg_cap < idx_para3, (
                f"Diagram must appear right after para2 ({idx_para2}) before para3 ({idx_para3}), got {idx_dg_cap}"
            )
            assert idx_para3 < idx_tbl_cap, (
                f"Table must appear right after para3 ({idx_para3}), got {idx_tbl_cap}"
            )

    finally:
        if created_project_id:
            try:
                client.delete(f"/api/projects/{created_project_id}")
            except Exception:
                pass
            export_docx = get_settings().storage_dir / "exports" / f"{created_project_id}_protocole.docx"
            export_docx.unlink(missing_ok=True)


if __name__ == "__main__":
    test_media_editing_and_exhaustive_anchor_placement()
    print("ALL MEDIA EDITING & EXHAUSTIVE ANCHOR TESTS PASSED.")
