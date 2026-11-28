from unittest.mock import patch

from app.schemas.protocol import (
    ProtocolProject,
    SectionState,
    SectionWritingPlan,
    SectionWritingPlanItem,
    StepStatus,
)
from app.services import pedagogy_service
from app.services.coherence_sentinel import extract_canonical_protocol_ledger
from app.services.section_precision_engine import (
    _GeneratedPlanItemDTO,
    _GeneratedSectionPlanDTO,
    _PlanExecutionAuditDTO,
    _RectifiedDraftDTO,
    _ValidationItemScoreDTO,
    generate_section_writing_plan,
    run_module3_validation_loop,
)
from app.services.selective_reformulation import (
    SelectionTransformOutput,
    identify_plan_item_for_selection,
    transform_selected_text,
)


def test_builtin_pedagogie_notes_loaded_and_crud(tmp_path):
    from app.data.fmsb_canvas import FMSB_SECTIONS

    valid_ids = {s.id for s in FMSB_SECTIONS}
    for fname, sids in pedagogy_service.BUILTIN_PEDAGOGY_SECTION_MAP.items():
        for sid in sids:
            assert sid in valid_ids, f"Invalid FMSB section_id {sid!r} in BUILTIN_PEDAGOGY_SECTION_MAP[{fname!r}]"

    notes = pedagogy_service.list_course_notes(include_disabled=True)
    builtin = [n for n in notes if n.source == "builtin"]
    assert len(builtin) == 12, f"Expected 12 builtin pedagogie files, got {len(builtin)}"

    intro_notes = pedagogy_service.list_course_notes(section_id="intro_contexte", include_disabled=False)
    assert any("introduction" in n.filename.lower() for n in intro_notes)

    revue_digest = pedagogy_service.format_pedagogy_notes_for_section("chap2_etat_connaissances")
    assert "NOTE DE COURS" in revue_digest

    # Isolate admin store to tmp_path for CRUD test
    fake_store = tmp_path / "pedagogy_course_notes.json"
    with patch.object(pedagogy_service, "_get_firestore_collection", return_value=None), patch.object(
        pedagogy_service, "_local_store_path", return_value=fake_store
    ):
        created = pedagogy_service.create_course_note(
            title="Note Schwartz personnalisée",
            content="Toujours vérifier p, d et Z=1.96.",
            section_ids=["chap3_population_echantillon"],
        )
        assert created.source == "admin"
        pop_notes = pedagogy_service.list_course_notes(section_id="chap3_population_echantillon", include_disabled=False)
        assert any(n.note_id == created.note_id for n in pop_notes)

        updated = pedagogy_service.update_course_note(
            created.note_id,
            section_ids=["chap3_population_echantillon", "chap3_analyse_donnees"],
            enabled=False,
        )
        assert "chap3_analyse_donnees" in updated.section_ids
        assert updated.enabled is False

        pop_active = pedagogy_service.list_course_notes(section_id="chap3_population_echantillon", include_disabled=False)
        assert not any(n.note_id == created.note_id for n in pop_active)

        assert pedagogy_service.delete_course_note(created.note_id) is True


def test_module1_module2_module3_loop_and_role_hierarchy():
    proj = ProtocolProject(name="Test 4 Modules", study_type="Étude transversale analytique")
    proj.cover.title = "Facteurs associés aux dermatoses chez les PVVIH"
    proj.sections["intro_contexte"] = SectionState(section_id="intro_contexte")

    roles_called = []
    audit_calls = {"n": 0}

    def fake_generate_structured(*, role, contents, schema, **kwargs):
        roles_called.append(role)
        name = schema.__name__
        if name == "_GeneratedSectionPlanDTO":
            return {
                "model_used": "user-chain-reasoning",
                "data": _GeneratedSectionPlanDTO(
                    summary="Plan d'introduction en entonnoir",
                    items=[
                        _GeneratedPlanItemDTO(
                            item_id="item_1",
                            title="Entonnoir épidémiologique",
                            objective="Partir du contexte mondial vers le site local",
                            required_elements=["Données mondiales", "Données africaines", "Données locales"],
                            pedagogical_rules=["Citer des ordres de grandeur sourcés"],
                            target_words=100,
                            data_needs=["Prévalence mondiale et africaine"],
                            weight=50.0,
                        ),
                        _GeneratedPlanItemDTO(
                            item_id="item_2",
                            title="Problème scientifique",
                            objective="Formuler l'écart de connaissances",
                            required_elements=["Lacune locale explicite"],
                            pedagogical_rules=["Ne pas confondre sujet et problème"],
                            target_words=100,
                            data_needs=["Absence de données locales"],
                            weight=50.0,
                        ),
                    ],
                ),
            }
        if name == "_PlanExecutionAuditDTO":
            audit_calls["n"] += 1
            if audit_calls["n"] == 1:
                # First pass < 97% -> triggers Module 2 rectification
                return {
                    "model_used": "user-chain-audit",
                    "data": _PlanExecutionAuditDTO(
                        item_results=[
                            _ValidationItemScoreDTO(item_id="item_1", status="passed", score_pct=98.0),
                            _ValidationItemScoreDTO(
                                item_id="item_2",
                                status="partial",
                                score_pct=80.0,
                                rectification_note="Expliciter l'écart de connaissances locales.",
                            ),
                        ]
                    ),
                }
            # Second pass after Module 2 rectification -> >= 97%
            return {
                "model_used": "user-chain-audit",
                "data": _PlanExecutionAuditDTO(
                    item_results=[
                        _ValidationItemScoreDTO(item_id="item_1", status="passed", score_pct=100.0),
                        _ValidationItemScoreDTO(item_id="item_2", status="passed", score_pct=99.0),
                    ]
                ),
            }
        if name == "_RectifiedDraftDTO":
            # Rectified text with >= 150 words so min_words passes too
            words = " ".join(["données épidémiologiques précises [1] lacune locale documentée"] * 22)
            return {
                "model_used": "user-chain-drafting",
                "data": _RectifiedDraftDTO(content_markdown=words, rectification_summary="Lacune ajoutée"),
            }
        raise AssertionError(f"Unexpected schema {name}")

    with patch("app.services.section_precision_engine.gemini_router.generate_structured", side_effect=fake_generate_structured):
        plan = generate_section_writing_plan(proj, "intro_contexte")
        assert plan.model_used == "user-chain-reasoning"
        assert len(plan.items) == 2
        assert proj.sections["intro_contexte"].writing_plan is not None

        # Artificially use lower word/citation floor to isolate loop scoring behavior
        plan.min_words = 10
        plan.max_words = 500
        plan.min_citations = 0

        final_md, report = run_module3_validation_loop(
            project=proj,
            section_id="intro_contexte",
            plan=plan,
            initial_draft_md="Premier brouillon mondial sans lacune locale.",
        )

    assert report.passed is True
    assert report.execution_score_pct >= 97.0
    assert report.attempts == 2
    assert "lacune locale documentée" in final_md
    assert roles_called == ["reasoning", "audit", "drafting", "audit"]


def test_module4_identifies_plan_item_for_selection():
    plan = SectionWritingPlan(
        section_id="chap1_hypotheses",
        items=[
            SectionWritingPlanItem(
                item_id="hyp_principale",
                title="Hypothèse générale de recherche",
                objective="Relier immunodépression CD4 et dermatoses",
                required_elements=["Relation testable entre taux de CD4 et lésions cutanées"],
                weight=50.0,
            ),
            SectionWritingPlanItem(
                item_id="hyp_secondaires",
                title="Hypothèses spécifiques pénitentiaires",
                objective="Tester promiscuité et partage de literie",
                required_elements=["Promiscuité cellulaire", "Partage de literie et d'effets personnels"],
                weight=50.0,
            ),
        ],
    )
    matched = identify_plan_item_for_selection(
        plan,
        "La promiscuité cellulaire et le partage de literie favorisent la transmission des dermatoses.",
        use_llm_fallback=False,
    )
    assert matched is not None
    assert matched.item_id == "hyp_secondaires"

    proj = ProtocolProject(name="Test Module 4")
    proj.sections["chap1_hypotheses"] = SectionState(
        section_id="chap1_hypotheses",
        content_markdown="La promiscuité cellulaire et le partage de literie favorisent la gale.",
        writing_plan=plan,
    )

    captured = {}

    def fake_sel_gen(*, role, contents, schema, **kwargs):
        captured["role"] = role
        captured["prompt"] = contents
        return {
            "model_used": "user-chain-drafting",
            "data": SelectionTransformOutput(
                replacement_text="La promiscuité et le partage de literie augmentent significativement le risque de gale.",
                summary="Reformulé selon le bloc hypothèses pénitentiaires.",
            ),
        }

    with patch("app.services.selective_reformulation.gemini_router.generate_structured", side_effect=fake_sel_gen):
        res = transform_selected_text(
            proj,
            "chap1_hypotheses",
            "La promiscuité cellulaire et le partage de literie favorisent la gale.",
            action="reformulate",
        )

    assert captured["role"] == "drafting"
    assert "hyp_secondaires" in captured["prompt"]
    assert res["matched_plan_item_id"] == "hyp_secondaires"


def test_canonical_protocol_ledger_extracts_facts():
    proj = ProtocolProject(name="Test Ledger", study_type="Étude transversale analytique")
    proj.cover.title = "Dermatoses et VIH en prison"
    proj.sections["chap3_type_site_duree"] = SectionState(
        section_id="chap3_type_site_duree",
        content_markdown="Étude menée sur une période de 8 mois dans 6 prisons du Sud-Cameroun.",
    )
    proj.sections["chap3_population_echantillon"] = SectionState(
        section_id="chap3_population_echantillon",
        content_markdown="La taille minimale de l'échantillon est de 330 participants.",
    )
    ledger = extract_canonical_protocol_ledger(proj)
    assert ledger["type_etude"] == "Étude transversale analytique"
    assert "8 mois" in ledger["duree_detention_ou_etude"]
    assert ledger["taille_echantillon_n"] == "330"
