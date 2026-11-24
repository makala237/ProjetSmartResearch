from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from app.schemas.protocol import (
    AbbreviationItem,
    AnnexesConfig,
    AuditWaiverEntry,
    BudgetItem,
    CoverMetadata,
    CRFFieldSpec,
    DiagramConnection,
    DiagramNode,
    FormattingRules,
    ProtocolProject,
    SectionDiagram,
    SectionTable,
    StepStatus,
    SupervisorDirectiveItem,
    TimelineGrid,
    VariableItem,
)

ACTION_MODULE_MAP: Dict[str, str] = {
    "add_directive": "module1",
    "waive_audit_and_validate": "module1",
    "run_ai_audit": "module1",
    "fix_section_issues": "module1",
    "humanize_section": "module1",
    "update_cover": "module1",
    "update_project_meta": "module1",
    "generate_sub_annex": "module1",
    "generate_section_table": "module1",
    "upsert_section_table": "module1",
    "generate_section_diagram": "module1",
    "assign_project_image": "module1",
    "run_literature_research": "module1",
    "draft_section": "module1",
    "run_global_audit": "module2",
    "update_formatting": "module2",
    "update_structured_data": "module2",
    "sync_structured": "module2",
    "generate_toc_and_lists": "module2",
    "validate_references": "module2",
    "update_crf_fields": "module3",
    "update_presentation_slide": "module4",
    "generate_presentation_slide": "module4",
}

VALID_COPILOT_ACTION_TYPES = frozenset(ACTION_MODULE_MAP.keys())

AI_ACTION_CREDIT_COSTS: Dict[str, int] = {
    "run_ai_audit": 1,
    "fix_section_issues": 2,
    "humanize_section": 1,
    "generate_toc_and_lists": 1,
    "validate_references": 1,
    "generate_sub_annex": 2,
    "generate_presentation_slide": 2,
    "generate_section_table": 1,
    "generate_section_diagram": 1,
    "run_literature_research": 2,
    "run_global_audit": 2,
    "draft_section": 2,
}


def _require_section(project: ProtocolProject, params: Dict[str, Any]):
    section_id = str(params.get("section_id") or "").strip()
    if not section_id or section_id not in project.sections:
        raise ValueError(f"Section introuvable ou non précisée : {section_id!r}")
    return section_id, project.sections[section_id]


def _sync_module2_media(project: ProtocolProject) -> None:
    from app.services.media_transport import (
        synchronize_module1_to_module2_tables,
        transport_and_repair_project_images,
    )
    from app.services.storage import sync_special_sections

    transport_and_repair_project_images(project)
    synchronize_module1_to_module2_tables(project)
    sync_special_sections(project, force_section_id="list_of_tables")
    sync_special_sections(project, force_section_id="list_of_figures")


def _action_add_directive(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    text = str(params.get("text") or params.get("directive") or "").strip()
    if not text:
        raise ValueError("Le texte de la directive est requis.")
    source = str(params.get("source") or "copilot").strip() or "copilot"
    existing = {
        (d.text or "").strip().casefold()
        for d in (project.supervisor_directives or [])
        if d.active
    }
    if text.casefold() in existing:
        return {"summary": f"Directive déjà active : « {text} »."}
    project.supervisor_directives.append(
        SupervisorDirectiveItem(text=text, source=source, active=True)
    )
    return {
        "summary": f"Directive méthodologique enregistrée et activée pour l'audit et la rédaction : « {text} »."
    }


def _action_waive_audit_and_validate(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    section_id, sec = _require_section(project, params)
    justification = str(params.get("justification") or "").strip()
    if not justification:
        raise ValueError("Une justification explicite est requise pour lever une remarque d'audit.")
    issue_text = str(params.get("issue_text") or "*").strip() or "*"

    waivers = list(getattr(sec, "audit_waivers", None) or [])
    waivers.append(
        AuditWaiverEntry(
            issue_text=issue_text,
            justification=justification,
            created_by_user_id=getattr(current_user, "id", None),
        )
    )
    sec.audit_waivers = waivers

    if issue_text == "*":
        removed_count = len(sec.audit_notes or [])
        sec.audit_notes = []
    else:
        w_low = issue_text.lower()
        kept: List[str] = []
        removed_count = 0
        for note in sec.audit_notes or []:
            n_low = (note or "").strip().lower()
            if w_low in n_low or n_low in w_low:
                removed_count += 1
            else:
                kept.append(note)
        sec.audit_notes = kept

    if not sec.audit_notes:
        sec.status = StepStatus.VALIDATED
    sec.updated_at = datetime.now(timezone.utc)

    directive_txt = str(params.get("also_add_directive") or "").strip()
    if directive_txt:
        _action_add_directive(
            project, {"text": directive_txt, "source": "copilot"}, current_user
        )

    status_lbl = sec.status.value if hasattr(sec.status, "value") else str(sec.status)
    return {
        "summary": (
            f"Dérogation d'audit enregistrée sur `{section_id}` "
            f"({removed_count} remarque(s) levée(s), statut={status_lbl}). Justification : {justification}"
        )
    }


def _action_run_ai_audit(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.drafting import audit_section_with_ai

    section_id, _ = _require_section(project, params)
    res = audit_section_with_ai(project, section_id=section_id)
    issues = res.get("issues") or []
    compliant = bool(res.get("compliant"))
    summary = (
        f"Audit CAMES/FMSB terminé sur `{section_id}` : section conforme et validée."
        if compliant
        else f"Audit CAMES/FMSB terminé sur `{section_id}` : {len(issues)} point(s) restant(s) : "
        + " ; ".join(str(i) for i in issues[:3])
    )
    return {"summary": summary, "model_used": str(res.get("model_used") or "")}


def _action_fix_section_issues(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.coherence_sentinel import evaluate_project_coherence
    from app.services.drafting import fix_section_issues_with_ai

    section_id, _ = _require_section(project, params)
    res = fix_section_issues_with_ai(project, section_id=section_id)
    _sync_module2_media(project)
    evaluate_project_coherence(project, changed_section_id=section_id)
    return {
        "summary": f"Correction guidée par l'audit appliquée sur `{section_id}`.",
        "model_used": str(res.get("model_used") or ""),
    }


def _action_humanize_section(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.coherence_sentinel import evaluate_project_coherence
    from app.services.humanizer import humanize_section_with_ai

    section_id, _ = _require_section(project, params)
    use_ai = bool(params.get("use_ai", True))
    res = humanize_section_with_ai(project, section_id=section_id, use_ai=use_ai)
    _sync_module2_media(project)
    evaluate_project_coherence(project, changed_section_id=section_id)
    return {
        "summary": (
            f"Humanisation ignorée sur `{section_id}` (section non narrative)."
            if res.get("skipped")
            else f"Style académique humanisé sur `{section_id}`."
        ),
        "model_used": str(res.get("model_used") or ""),
    }


def _action_update_cover(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.working_memory import update_section_working_memory

    cover_data = project.cover.model_dump()
    allowed_fields = set(CoverMetadata.model_fields.keys())
    changed_fields: List[str] = []
    for k, v in (params or {}).items():
        if k in allowed_fields and v is not None:
            cover_data[k] = v
            changed_fields.append(k)
    if not changed_fields:
        raise ValueError("Aucun champ valide de page de garde fourni.")

    new_cover = CoverMetadata(**cover_data)
    if project.topic_locked:
        project.locked_title = (new_cover.title or project.locked_title or "").strip()
        project.locked_author_name = (
            new_cover.author_name or project.locked_author_name or ""
        ).strip()
        project.locked_matricule = (
            new_cover.matricule or project.locked_matricule or ""
        ).strip()

    project.cover = new_cover
    if (new_cover.title or "").strip():
        project.name = new_cover.title.strip()
    if "cover_page" in project.sections:
        cov_sec = project.sections["cover_page"]
        cov_sec.status = StepStatus.VALIDATED
        if (new_cover.title or "").strip():
            cov_sec.content_markdown = new_cover.title.strip()
    update_section_working_memory(project, "cover_page")
    return {"summary": f"Page de garde mise à jour ({', '.join(changed_fields)})."}


def _action_update_project_meta(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.data.i18n import normalize_lang

    changed: List[str] = []
    if "study_type" in params and params["study_type"] is not None:
        project.study_type = str(params["study_type"]).strip()
        changed.append("study_type")
    if "research_idea" in params and params["research_idea"] is not None:
        project.research_idea = str(params["research_idea"]).strip()
        changed.append("research_idea")
    if "language" in params and params["language"] is not None:
        project.language = normalize_lang(str(params["language"]))
        changed.append("language")
    if not changed:
        raise ValueError("Aucune métadonnée de projet valide fournie.")
    return {"summary": f"Métadonnées du protocole mises à jour ({', '.join(changed)})."}


def _action_update_formatting(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    allowed = set(FormattingRules.model_fields.keys())
    updates = {k: v for k, v in (params or {}).items() if k in allowed and v is not None}
    if not updates:
        raise ValueError("Aucun paramètre de mise en forme valide fourni.")
    merged = project.formatting.model_dump()
    merged.update(updates)
    project.formatting = FormattingRules(**merged)
    return {
        "summary": "Mise en forme Word/PDF mise à jour : "
        + ", ".join(f"{k}={v}" for k, v in updates.items())
    }


def _action_update_structured_data(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    changed: List[str] = []

    if isinstance(params.get("abbreviations"), list):
        project.abbreviations = [
            AbbreviationItem(**item) if isinstance(item, dict) else item
            for item in params["abbreviations"]
        ]
        changed.append("abbreviations")
    elif isinstance(params.get("add_abbreviations"), list):
        by_acr = {a.acronym.strip().upper(): a for a in project.abbreviations}
        for raw in params["add_abbreviations"]:
            item = AbbreviationItem(**raw) if isinstance(raw, dict) else raw
            key = item.acronym.strip().upper()
            if key:
                by_acr[key] = item
        project.abbreviations = list(by_acr.values())
        changed.append("add_abbreviations")

    if isinstance(params.get("variables"), list):
        project.variables = [
            VariableItem(**item) if isinstance(item, dict) else item
            for item in params["variables"]
        ]
        if project.variables and "chap1_variables" in project.sections:
            project.sections["chap1_variables"].suppress_structured_table = False
        changed.append("variables")
    elif isinstance(params.get("add_variables"), list):
        by_name = {v.name.strip().lower(): v for v in project.variables}
        for raw in params["add_variables"]:
            item = VariableItem(**raw) if isinstance(raw, dict) else raw
            key = item.name.strip().lower()
            if key:
                by_name[key] = item
        project.variables = list(by_name.values())
        if project.variables and "chap1_variables" in project.sections:
            project.sections["chap1_variables"].suppress_structured_table = False
        changed.append("add_variables")

    if isinstance(params.get("budget"), list) or isinstance(params.get("add_budget_items"), list):
        from app.services.budget_engine import normalize_budget_items, sync_section_from_budget
        from app.services.presentation_orchestrator import refresh_budget_slide_if_present

        if isinstance(params.get("budget"), list):
            raw_items = [BudgetItem(**b) if isinstance(b, dict) else b for b in params["budget"]]
        else:
            raw_items = list(project.budget or []) + [
                BudgetItem(**b) if isinstance(b, dict) else b for b in params["add_budget_items"]
            ]
        project.budget = normalize_budget_items(raw_items)
        if project.budget and "chap3_budget" in project.sections:
            project.sections["chap3_budget"].suppress_structured_table = False
            sync_section_from_budget(project)
        refresh_budget_slide_if_present(project)
        changed.append("budget")

    if isinstance(params.get("timeline_grid"), dict):
        project.timeline_grid = TimelineGrid(**params["timeline_grid"])
        if project.timeline_grid.rows and "chap3_chronogramme" in project.sections:
            project.sections["chap3_chronogramme"].suppress_structured_table = False
        changed.append("timeline_grid")

    if isinstance(params.get("annexes_config"), dict):
        merged_ann = project.annexes_config.model_dump()
        for k, v in params["annexes_config"].items():
            if k in AnnexesConfig.model_fields and v is not None:
                merged_ann[k] = v
        project.annexes_config = AnnexesConfig(**merged_ann)
        changed.append("annexes_config")

    if not changed:
        raise ValueError("Aucune donnée structurée valide fournie.")

    _sync_module2_media(project)
    return {"summary": f"Données structurées mises à jour ({', '.join(changed)})."}


def _action_sync_structured(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.global_audit import sync_structured_from_sections
    from app.services.presentation_orchestrator import refresh_budget_slide_if_present

    report = sync_structured_from_sections(project)
    refresh_budget_slide_if_present(project)
    return {
        "summary": (
            f"Données structurées synchronisées depuis les sections rédigées "
            f"({report.get('variables_count', len(project.variables))} variables, "
            f"{report.get('budget_count', len(project.budget))} lignes budgétaires)."
        )
    }


def _action_generate_toc_and_lists(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.list_builder import generate_all_lists_and_toc

    use_ai = bool(params.get("use_ai", False))
    res = generate_all_lists_and_toc(project, use_gemini_decision=use_ai)
    plan = res.get("plan")
    return {
        "summary": (
            f"Sommaire et listes générés ({res.get('entries_count', 0)} entrées TDM, "
            f"{res.get('abbreviations_count', 0)} abréviations, "
            f"{res.get('tables_count', 0)} tableaux, {res.get('figures_count', 0)} figures)."
        ),
        "model_used": str(getattr(plan, "model_used", "") or ""),
    }


def _action_validate_references(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.list_builder import generate_all_lists_and_toc
    from app.services.vancouver import build_and_validate_vancouver_references

    use_ai = bool(params.get("use_ai", False))
    ref_report = build_and_validate_vancouver_references(
        project, use_gemini_decision=use_ai, mark_validated=True
    )
    generate_all_lists_and_toc(project, use_gemini_decision=False)
    return {
        "summary": (
            f"Références Vancouver validées et synchronisées "
            f"({len(project.references or [])} référence(s) au total)."
        ),
        "model_used": str(ref_report.get("decision_model", "") or ""),
    }


def _action_update_crf_fields(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.crf_builder import sync_crf_to_project

    updated_any = False
    if isinstance(params.get("replace_fields"), list):
        project.crf_fields = [
            CRFFieldSpec(**f) if isinstance(f, dict) else f for f in params["replace_fields"]
        ]
        updated_any = True
    else:
        fields = list(project.crf_fields or [])
        remove_codes = {
            str(c).strip().lower()
            for c in (params.get("remove_field_codes") or [])
            if str(c).strip()
        }
        if remove_codes:
            fields = [
                f
                for f in fields
                if (f.code or "").strip().lower() not in remove_codes
                and (f.label or "").strip().lower() not in remove_codes
            ]
            updated_any = True
        if isinstance(params.get("add_fields"), list) and params["add_fields"]:
            for raw in params["add_fields"]:
                spec = CRFFieldSpec(**raw) if isinstance(raw, dict) else raw
                fields.append(spec)
            updated_any = True
        project.crf_fields = fields

    if not updated_any:
        raise ValueError("Aucune modification CRF fournie (add_fields / remove_field_codes / replace_fields).")

    sync_crf_to_project(project, sync_annexe1_markdown=True)
    return {
        "summary": f"Masque de saisie (Module 3) et Annexe 1 mis à jour ({len(project.crf_fields)} champ(s))."
    }


def _action_generate_sub_annex(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.annexe_engine import generate_sub_annex_content

    section_id, _ = _require_section(project, params)
    res = generate_sub_annex_content(
        project,
        section_id=section_id,
        user_instruction=str(params.get("user_instruction") or ""),
        bilingual=bool(params.get("bilingual", False)),
        use_ai=bool(params.get("use_ai", True)),
    )
    return {
        "summary": f"Sous-annexe `{section_id}` générée / actualisée et synchronisée.",
        "model_used": str(res.get("model_used") or ""),
    }


def _load_or_init_deck(project: ProtocolProject):
    from app.services.presentation_orchestrator import PresentationOrchestrator
    from app.services.storage import project_storage

    orch = PresentationOrchestrator()
    cached = project_storage.get_presentation(project.project_id)
    deck, _ = orch.ensure_fresh_valid_deck(project, cached)
    return orch, deck, project_storage


def _action_update_presentation_slide(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.schemas.presentation import SlideElement
    from app.services.slide_layout_engine import compute_deck_layout

    _, deck, store = _load_or_init_deck(project)
    slide_index = int(params.get("slide_index") or deck.current_slide_index or 1)
    slide = next((s for s in deck.slides if s.slide_index == slide_index), None)
    if slide is None:
        raise ValueError(f"Diapositive {slide_index} introuvable dans le diaporama.")

    changed: List[str] = []
    if params.get("title") is not None:
        slide.title = str(params["title"])
        changed.append("title")
    if params.get("subtitle") is not None:
        slide.subtitle = str(params["subtitle"])
        changed.append("subtitle")
    if params.get("presenter_notes") is not None:
        slide.presenter_notes = str(params["presenter_notes"])
        changed.append("presenter_notes")
    if params.get("layout_mode") is not None:
        slide.layout_mode = str(params["layout_mode"])
        changed.append("layout_mode")
    if isinstance(params.get("bullets"), list):
        non_bullets = [e for e in slide.elements if e.element_type != "bullet_main"]
        new_bullets = [
            SlideElement(
                id=f"el-copilot-{slide_index}-{idx + 1}",
                element_type="bullet_main",
                content=str(txt),
                template_id="tpl-bullet-main",
                order=idx + 1,
            )
            for idx, txt in enumerate(params["bullets"])
            if str(txt).strip()
        ]
        slide.elements = non_bullets + new_bullets
        changed.append("bullets")

    if not changed:
        raise ValueError("Aucune modification de diapositive fournie.")

    compute_deck_layout(deck, project=project)
    store.save_presentation(deck)
    return {
        "summary": f"Diapositive {slide_index} (« {slide.title} ») mise à jour ({', '.join(changed)})."
    }


def _action_generate_presentation_slide(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    orch, deck, store = _load_or_init_deck(project)
    slide_index = int(params.get("slide_index") or deck.current_slide_index or 1)
    res = orch.generate_slide_ai_summary(
        project=project,
        deck=deck,
        slide_index=slide_index,
        user_instruction=str(params.get("instruction") or ""),
        preferred_layout=params.get("layout_mode"),
    )
    store.save_presentation(res["deck"])
    return {
        "summary": f"Diapositive {slide_index} régénérée avec l'IA pour la soutenance (Module 4).",
        "model_used": str(res.get("model_used") or ""),
    }


def _action_generate_section_table(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.drafting import suggest_section_table_with_ai

    section_id, sec = _require_section(project, params)
    title = str(params.get("title") or "Tableau synthétique").strip() or "Tableau synthétique"
    goal = str(params.get("goal") or "").strip()
    res = suggest_section_table_with_ai(
        project,
        section_id=section_id,
        table_title=title,
        table_goal=goal,
    )
    tbl = SectionTable(
        table_id=f"tbl_{uuid4().hex[:8]}",
        title=str(res.get("title") or title).strip(),
        anchor_hint=str(params.get("anchor_hint") or "").strip(),
        headers=[str(h) for h in (res.get("headers") or [])],
        rows=[[str(c) for c in r] for r in (res.get("rows") or [])],
    )
    sec.custom_tables.append(tbl)
    _sync_module2_media(project)
    return {
        "summary": (
            f"Tableau « {tbl.title} » généré par l'IA et inséré dans `{section_id}` "
            f"({len(tbl.headers)} colonne(s), {len(tbl.rows)} ligne(s))."
        ),
        "model_used": str(res.get("model_used") or ""),
    }


def _action_upsert_section_table(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    section_id, sec = _require_section(project, params)
    table_id = str(params.get("table_id") or "").strip()
    target = (
        next((t for t in sec.custom_tables if t.table_id == table_id), None)
        if table_id
        else None
    )
    if target is not None:
        if params.get("title") is not None:
            target.title = str(params["title"]).strip() or target.title
        if params.get("anchor_hint") is not None:
            target.anchor_hint = str(params["anchor_hint"]).strip()
        if isinstance(params.get("headers"), list) and params["headers"]:
            target.headers = [str(h) for h in params["headers"]]
        if isinstance(params.get("rows"), list):
            target.rows = [[str(c) for c in r] for r in params["rows"]]
        _sync_module2_media(project)
        return {
            "summary": f"Tableau `{target.table_id}` (« {target.title} ») mis à jour dans `{section_id}`."
        }

    headers = [str(h).strip() for h in (params.get("headers") or []) if str(h).strip()]
    rows = [[str(c) for c in r] for r in (params.get("rows") or [])]
    if not headers and not rows:
        raise ValueError("Les colonnes (`headers`) ou lignes (`rows`) du tableau sont requises.")
    if not headers and rows:
        headers = [f"Colonne {i + 1}" for i in range(len(rows[0]))]
    tbl = SectionTable(
        table_id=table_id or f"tbl_{uuid4().hex[:8]}",
        title=str(params.get("title") or "Tableau personnalisé").strip() or "Tableau personnalisé",
        anchor_hint=str(params.get("anchor_hint") or "").strip(),
        headers=headers,
        rows=rows,
    )
    sec.custom_tables.append(tbl)
    _sync_module2_media(project)
    return {
        "summary": (
            f"Tableau « {tbl.title} » inséré dans `{section_id}` "
            f"({len(tbl.headers)} colonne(s), {len(tbl.rows)} ligne(s))."
        )
    }


def _action_generate_section_diagram(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.drafting import suggest_section_diagram_with_ai

    section_id, sec = _require_section(project, params)
    title = str(params.get("title") or "Schéma conceptuel").strip() or "Schéma conceptuel"
    goal = str(params.get("goal") or "").strip()
    res = suggest_section_diagram_with_ai(
        project,
        section_id=section_id,
        diagram_title=title,
        diagram_goal=goal,
    )
    nodes = [
        DiagramNode(**n) if isinstance(n, dict) else n for n in (res.get("nodes") or [])
    ]
    connections = [
        DiagramConnection(**c) if isinstance(c, dict) else c
        for c in (res.get("connections") or [])
    ]
    layout_raw = str(params.get("layout") or res.get("layout") or "vertical").strip().lower()
    layout = layout_raw if layout_raw in ("vertical", "horizontal") else "vertical"
    dg = SectionDiagram(
        diagram_id=f"diag_{uuid4().hex[:8]}",
        title=str(res.get("title") or title).strip(),
        caption=str(params.get("caption") or res.get("caption") or "").strip(),
        anchor_hint=str(params.get("anchor_hint") or "").strip(),
        layout=layout,
        nodes=nodes,
        connections=connections,
    )
    sec.custom_diagrams.append(dg)
    _sync_module2_media(project)
    return {
        "summary": (
            f"Figure/Schéma « {dg.title} » généré par l'IA et inséré dans `{section_id}` "
            f"({len(dg.nodes)} nœud(s), {len(dg.connections)} connexion(s))."
        ),
        "model_used": str(res.get("model_used") or ""),
    }


def _action_assign_project_image(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    images = list(project.images or [])
    if not images:
        raise ValueError("Aucune image ou figure disponible dans le projet.")

    asset_id = str(params.get("asset_id") or "").strip()
    title_q = str(params.get("title_query") or "").strip().lower()
    target = (
        next((img for img in images if img.asset_id == asset_id), None)
        if asset_id
        else None
    )
    if target is None and title_q:
        target = next(
            (
                img
                for img in images
                if title_q in (img.title or "").lower()
                or title_q in (img.caption or "").lower()
            ),
            None,
        )
    if target is None:
        raise ValueError(
            "Image/figure introuvable (précisez `asset_id` ou un `title_query` correspondant)."
        )

    if params.get("target_section_id") is not None:
        sec_id = str(params["target_section_id"]).strip()
        if sec_id and sec_id not in project.sections:
            raise ValueError(f"Section cible introuvable : {sec_id!r}")
        if sec_id:
            target.target_section_id = sec_id
    if params.get("approved") is not None:
        target.approved = bool(params["approved"])
    elif params.get("target_section_id"):
        target.approved = True
    if params.get("caption") is not None:
        target.caption = str(params["caption"]).strip()
    if params.get("title") is not None:
        target.title = str(params["title"]).strip() or target.title

    _sync_module2_media(project)
    return {
        "summary": (
            f"Figure « {target.title} » (`{target.asset_id}`) associée à "
            f"`{target.target_section_id}` (approuvée={target.approved})."
        )
    }


def _action_run_literature_research(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.literature import run_research_pipeline
    from app.services.segmentation import segment_project_articles

    custom_query = str(params.get("custom_query") or "").strip() or None
    max_per_source = max(1, min(10, int(params.get("max_per_source") or 4)))
    include_web = bool(params.get("include_web_grounding", True))
    search_scope = str(params.get("search_scope") or "hybrid_africa").strip() or "hybrid_africa"

    before_refs = len(project.references or [])
    report = run_research_pipeline(
        project,
        custom_query=custom_query,
        max_per_source=max_per_source,
        include_web_grounding=include_web,
        search_scope=search_scope,
    )
    after_refs = len(project.references or [])
    seg_info = ""
    if bool(params.get("also_segment", False)) and project.references:
        seg_limit = max(1, min(25, int(params.get("segment_limit") or 8)))
        seg_rep = segment_project_articles(
            project, limit=seg_limit, only_unsegmented=True
        )
        seg_count = seg_rep.get("added_segments", len(project.article_segments or []))
        seg_info = f" ; segmentation lancée ({seg_count} segment(s))"

    return {
        "summary": (
            f"Recherche documentaire exécutée (portée={search_scope}) : "
            f"{after_refs} référence(s) au projet (+{max(0, after_refs - before_refs)} nouvelle(s)){seg_info}."
        ),
        "model_used": str(report.get("planner_model") or ""),
    }


def _action_run_global_audit(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.global_audit import run_global_protocol_audit

    res = run_global_protocol_audit(project)
    score = int(res.get("score_percent") or 0)
    ready = bool(res.get("ready_for_print"))
    validated = int(res.get("validated_sections_count") or 0)
    total = int(res.get("total_sections_count") or len(project.sections))
    warnings_or_errors = [
        f"{c.get('label')}: {c.get('summary')}"
        for c in (res.get("checks") or [])
        if c.get("status") in ("warning", "error")
    ]
    alerts_str = " | ".join(warnings_or_errors[:3]) if warnings_or_errors else "Aucune alerte critique"
    return {
        "summary": (
            f"Audit global FMSB/CAMES terminé : score {score} %, "
            f"{validated}/{total} section(s) validée(s), prêt pour export={'oui' if ready else 'non'}. "
            f"Points clés : {alerts_str}."
        ),
        "model_used": str(res.get("model_used") or ""),
    }


def _action_draft_section(
    project: ProtocolProject, params: Dict[str, Any], current_user: Any
) -> Dict[str, Any]:
    from app.services.coherence_sentinel import evaluate_project_coherence
    from app.services.drafting import draft_section_with_ai
    from app.services.list_builder import generate_all_lists_and_toc

    section_id, _ = _require_section(project, params)
    if section_id in ("toc", "abbreviations", "list_of_tables", "list_of_figures"):
        res_lists = generate_all_lists_and_toc(project, use_gemini_decision=True)
        plan = res_lists.get("plan")
        return {
            "summary": f"Section `{section_id}` générée automatiquement ({res_lists.get('entries_count', 0)} entrée(s)).",
            "model_used": str(getattr(plan, "model_used", "") or ""),
        }

    instruction = str(params.get("instruction") or params.get("user_instruction") or "").strip() or None
    res = draft_section_with_ai(
        project,
        section_id=section_id,
        user_instruction=instruction,
    )
    _sync_module2_media(project)
    evaluate_project_coherence(project, changed_section_id=section_id)
    words_cnt = len(str(res.get("content_markdown") or "").split())
    return {
        "summary": f"Section `{section_id}` rédigée dans son espace de travail ({words_cnt} mots).",
        "model_used": str(res.get("model_used") or ""),
    }


_ACTION_HANDLERS: Dict[str, Callable[[ProtocolProject, Dict[str, Any], Any], Dict[str, Any]]] = {
    "add_directive": _action_add_directive,
    "waive_audit_and_validate": _action_waive_audit_and_validate,
    "run_ai_audit": _action_run_ai_audit,
    "fix_section_issues": _action_fix_section_issues,
    "humanize_section": _action_humanize_section,
    "update_cover": _action_update_cover,
    "update_project_meta": _action_update_project_meta,
    "update_formatting": _action_update_formatting,
    "update_structured_data": _action_update_structured_data,
    "sync_structured": _action_sync_structured,
    "generate_toc_and_lists": _action_generate_toc_and_lists,
    "validate_references": _action_validate_references,
    "update_crf_fields": _action_update_crf_fields,
    "generate_sub_annex": _action_generate_sub_annex,
    "generate_section_table": _action_generate_section_table,
    "upsert_section_table": _action_upsert_section_table,
    "generate_section_diagram": _action_generate_section_diagram,
    "assign_project_image": _action_assign_project_image,
    "run_literature_research": _action_run_literature_research,
    "run_global_audit": _action_run_global_audit,
    "draft_section": _action_draft_section,
    "update_presentation_slide": _action_update_presentation_slide,
    "generate_presentation_slide": _action_generate_presentation_slide,
}


def execute_copilot_app_action(
    project: ProtocolProject,
    action_type: str,
    params: Optional[Dict[str, Any]] = None,
    current_user: Any = None,
) -> Dict[str, Any]:
    """Execute one explicit, user-confirmed Smart Research app action across any module."""
    clean_type = (action_type or "").strip()
    if clean_type not in VALID_COPILOT_ACTION_TYPES:
        raise ValueError(f"Action applicative inconnue ou non autorisée : {clean_type!r}")

    clean_params = dict(params or {})
    sec_id = str(clean_params.get("section_id") or "").strip()
    is_cover_mutation = clean_type == "update_cover" or (
        sec_id == "cover_page"
        and clean_type
        in (
            "waive_audit_and_validate",
            "fix_section_issues",
            "humanize_section",
            "draft_section",
            "generate_section_table",
            "upsert_section_table",
            "generate_section_diagram",
        )
    )
    if (
        is_cover_mutation
        and current_user is not None
        and getattr(current_user, "role", "") != "admin"
    ):
        raise PermissionError(
            "COVER_ADMIN_ONLY: La page de garde est définie et verrouillée par l'administrateur. Seul un administrateur peut la modifier."
        )

    handler = _ACTION_HANDLERS[clean_type]
    res = handler(project, clean_params, current_user)
    project.updated_at = datetime.now(timezone.utc)
    return {
        "ok": True,
        "action_type": clean_type,
        "module": ACTION_MODULE_MAP[clean_type],
        "model_used": str(res.get("model_used") or ""),
        "summary": str(res.get("summary") or "Action exécutée avec succès."),
    }
