from pathlib import Path
from docx import Document

from app.schemas.protocol import ProtocolProject, SectionState, StepStatus
from app.services.docx_template import compile_protocol_docx
from app.services.importer import (
    extract_and_route_imported_tables,
    is_budget_table,
    is_timeline_table,
)
from app.services.page_layout_engine import plan_document_pagination
from app.main import _parse_single_html_table, _strip_nth_markdown_pipe_table


def _make_sample_project() -> ProtocolProject:
    proj = ProtocolProject(name="Protocole Test")
    proj.cover.title = "Facteurs associés aux affections dermatologiques"
    proj.sections["intro_contexte"] = SectionState(
        section_id="intro_contexte",
        status=StepStatus.VALIDATED,
        content_markdown=(
            "### I.1.1. Contexte mondial\n\n"
            "Texte de contexte mondial détaillé.\n\n"
            "a) Spécificité africaine\n\n"
            "Texte spécifique pour l'Afrique subsaharienne.\n\n"
            "**Objectif général**\n\n"
            "Décrire les facteurs associés.\n"
        ),
        content_html=(
            "<h3>I.1.1. Contexte mondial</h3>"
            "<p>Texte de contexte mondial détaillé.</p>"
            "<p>a) Spécificité africaine</p>"
            "<p>Texte spécifique pour l'Afrique subsaharienne.</p>"
            "<p><strong>Objectif général</strong></p>"
            "<p>Décrire les facteurs associés.</p>"
        ),
    )
    return proj


def test_no_orphan_headings_in_docx_and_layout_plan(tmp_path: Path):
    proj = _make_sample_project()
    plan = plan_document_pagination(proj, use_gemini_decision=False)
    assert "anti-titres orphelins" in plan.decision_notes.lower()

    out_docx = tmp_path / "test_protocol.docx"
    compile_protocol_docx(proj, out_docx)
    doc = Document(str(out_docx))

    checked = {}
    boxed_chapter_has_keep_next = []
    for p in doc.paragraphs:
        txt = (p.text or "").strip()
        if p._p.xpath(".//*[local-name()='txbxContent']"):
            if "CHAPITRE" in "".join(p._p.xpath(".//*[local-name()='t']/text()")):
                boxed_chapter_has_keep_next.append(bool(p.paragraph_format.keep_with_next))
            continue
        for target in ("I.1.1. Contexte mondial", "a) Spécificité africaine", "Objectif général"):
            if target in txt:
                checked[target] = (
                    bool(p.paragraph_format.keep_with_next),
                    bool(p.paragraph_format.keep_together),
                )

    assert len(checked) == 3, f"Missing expected headings in DOCX: {checked}"
    for title, (kn, kt) in checked.items():
        assert kn is True, f"Heading '{title}' missing keep_with_next=True"
        assert kt is True, f"Heading '{title}' missing keep_together=True"
    # Boxed full-page chapter dividers must NOT be forced to keep_with_next
    assert all(v is False for v in boxed_chapter_has_keep_next)


def test_import_routes_chronogramme_budget_and_generic_tables():
    proj = ProtocolProject(name="Import Test")

    # 1. Chronogramme table -> TimelineGrid without inline duplicate
    chrono_md = (
        "Voici le calendrier prévisionnel de l'étude.\n\n"
        "| Activité | Oct 2026 | Nov 2026 | Déc 2026 | Jan 2027 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| Finalisation du protocole | ■ | ■ | | |\n"
        "| Collecte des données | | | ■ | ■ |\n"
    )
    sec_chrono = SectionState(section_id="chap3_chronogramme")
    proj.sections["chap3_chronogramme"] = sec_chrono
    cleaned_chrono = extract_and_route_imported_tables(proj, sec_chrono, "chap3_chronogramme", chrono_md)
    assert "|" not in cleaned_chrono
    assert "Voici le calendrier" in cleaned_chrono
    assert proj.timeline_grid.periods == ["Oct 2026", "Nov 2026", "Déc 2026", "Jan 2027"]
    assert len(proj.timeline_grid.rows) == 2
    assert proj.timeline_grid.rows[0].activity == "Finalisation du protocole"
    assert proj.timeline_grid.rows[0].active_periods == ["Oct 2026", "Nov 2026"]
    assert proj.timeline_grid.rows[1].active_periods == ["Déc 2026", "Jan 2027"]

    # 2. Budget table -> project.budget without inline duplicate
    budget_md = (
        "Estimation budgétaire :\n\n"
        "| Désignation | Quantité | Prix unitaire (FCFA) | Total (FCFA) |\n"
        "| --- | --- | --- | --- |\n"
        "| Clairance éthique | 1 | 75 000 | 75 000 |\n"
        "| Impression fiches | 200 | 150 | 30 000 |\n"
        "| TOTAL GÉNÉRAL | | | 105 000 |\n"
    )
    sec_budget = SectionState(section_id="chap3_budget")
    proj.sections["chap3_budget"] = sec_budget
    cleaned_budget = extract_and_route_imported_tables(proj, sec_budget, "chap3_budget", budget_md)
    assert "|" not in cleaned_budget
    assert len(proj.budget) == 2
    assert proj.budget[0].designation == "Clairance éthique"
    assert proj.budget[0].total_fcfa == 75000.0
    assert proj.budget[1].quantity == 200.0

    # 3. Generic table in Ch II -> editable SectionTable in sec.custom_tables
    generic_md = (
        "### Classification étiologique\n\n"
        "| Catégorie | Exemples | Mécanisme |\n"
        "| --- | --- | --- |\n"
        "| Infectieuses | Candidose, Zona | CD4 bas |\n"
        "| Inflammatoires | Psoriasis | Dysrégulation |\n"
    )
    sec_ch2 = SectionState(section_id="chap2_rappels")
    proj.sections["chap2_rappels"] = sec_ch2
    cleaned_ch2 = extract_and_route_imported_tables(proj, sec_ch2, "chap2_rappels", generic_md)
    assert "|" not in cleaned_ch2
    assert len(sec_ch2.custom_tables) == 1
    assert sec_ch2.custom_tables[0].headers == ["Catégorie", "Exemples", "Mécanisme"]
    assert len(sec_ch2.custom_tables[0].rows) == 2


def test_inline_html_table_parsing_and_markdown_stripping():
    html_tbl = (
        "<table><thead><tr><th>Activité</th><th>Oct 2026</th><th>Nov 2026</th></tr></thead>"
        "<tbody><tr><td>Protocole</td><td>■</td><td></td></tr></tbody></table>"
    )
    parsed = _parse_single_html_table(html_tbl)
    assert parsed is not None
    headers, rows = parsed
    assert is_timeline_table(headers, rows, "chap3_chronogramme")
    assert not is_budget_table(headers, rows, "chap3_chronogramme")

    md = "Intro\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nMilieu\n\n| C | D |\n|---|---|\n| 3 | 4 |\n"
    stripped = _strip_nth_markdown_pipe_table(md, 0)
    assert "| 1 | 2 |" not in stripped
    assert "| 3 | 4 |" in stripped
