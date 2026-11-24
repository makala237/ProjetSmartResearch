import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.data.i18n import normalize_lang
from app.schemas.protocol import ProtocolProject
from app.services.gemini_client import gemini_router
from app.services.humanizer import sanitize_llm_typography


def _count_words(text: str) -> int:
    return len(re.findall(r"\S+", (text or "").strip()))


def _tokenize(text: str) -> List[str]:
    return [
        tok
        for tok in re.findall(r"[a-z0-9àâäéèêëîïôöùûüç]{3,}", (text or "").lower())
        if tok not in {"les", "des", "une", "pour", "dans", "avec", "sur", "par", "que", "qui", "the", "and", "for", "with"}
    ]


def tool_inspect_internal_library(
    project: ProtocolProject,
    query: str = "",
    active_section_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Read-only search across internal library entries, uploaded sources, and bibliography."""
    keywords = set(_tokenize(query))
    matches: List[Dict[str, Any]] = []

    section_ids_ordered: List[str] = []
    if active_section_id and active_section_id in project.sections:
        section_ids_ordered.append(active_section_id)
    for sid in project.sections:
        if sid not in section_ids_ordered:
            section_ids_ordered.append(sid)

    for sid in section_ids_ordered:
        sec = project.sections[sid]
        for entry in sec.internal_library or []:
            haystack = " ".join(
                [
                    entry.source_filename or "",
                    entry.vancouver_citation or "",
                    entry.diagnostic_note or "",
                    entry.content_markdown or "",
                ]
            ).lower()
            overlap = sum(1 for kw in keywords if kw in haystack) if keywords else 1
            if keywords and overlap == 0 and sid != active_section_id:
                continue
            score = overlap + (2 if sid == active_section_id else 0)
            snippet = re.sub(r"\s+", " ", (entry.content_markdown or "").strip())[:420]
            matches.append(
                {
                    "kind": "internal_library",
                    "section_id": sid,
                    "filename": entry.source_filename,
                    "source_kind": entry.source_kind,
                    "citable": bool(entry.citable),
                    "citation": entry.vancouver_citation or entry.source_kind,
                    "snippet": snippet,
                    "score": score,
                }
            )

    for src in project.internal_sources or []:
        haystack = " ".join(
            [
                src.filename or "",
                src.title or "",
                src.vancouver_citation or "",
                src.institution_or_journal or "",
            ]
        ).lower()
        overlap = sum(1 for kw in keywords if kw in haystack) if keywords else 1
        if keywords and overlap == 0:
            continue
        matches.append(
            {
                "kind": "internal_source_doc",
                "section_id": ", ".join(src.mapped_sections[:4]) or "global",
                "filename": src.filename,
                "source_kind": src.source_kind,
                "citable": bool(src.citable),
                "citation": src.vancouver_citation or src.title or src.filename,
                "snippet": f"Sections cibles : {', '.join(src.mapped_sections) or 'aucune'}",
                "score": overlap,
            }
        )

    for ref in project.references or []:
        haystack = f"{ref.vancouver_citation or ''} {ref.title or ''}".lower()
        overlap = sum(1 for kw in keywords if kw in haystack)
        if not keywords or overlap == 0:
            continue
        matches.append(
            {
                "kind": "reference",
                "section_id": "references",
                "filename": f"Réf [{ref.order_index or ref.ref_id}]",
                "source_kind": "bibliography",
                "citable": True,
                "citation": ref.vancouver_citation or ref.title,
                "snippet": ref.vancouver_citation or ref.title,
                "score": overlap,
            }
        )

    matches.sort(key=lambda m: (-int(m.get("score", 0)), m.get("filename", "")))
    top = matches[:8]

    summary_lines: List[str] = []
    for m in top:
        summary_lines.append(
            f"- [{m['kind']}] ({m['section_id']}) {m['filename']} | citable={m['citable']} | {m['citation']}: {m['snippet'][:220]}"
        )

    return {
        "tool": "inspect_internal_library",
        "matches": top,
        "summary_text": "\n".join(summary_lines)
        if summary_lines
        else "Aucune correspondance trouvée dans la bibliothèque interne ou les références.",
    }


def tool_run_section_audit(
    project: ProtocolProject,
    active_section_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run read-only FMSB/CAMES audit report including persisted AI audit notes, waivers, and coherence alerts."""
    from app.services.drafting import deterministic_section_checks

    issues: List[str] = []
    ai_audit_notes: List[str] = []
    audit_waivers: List[Dict[str, str]] = []
    section_status = "pending"
    word_count = 0
    if active_section_id and active_section_id in project.sections:
        sec = project.sections[active_section_id]
        word_count = _count_words(sec.content_markdown)
        section_status = (
            sec.status.value if hasattr(sec.status, "value") else str(sec.status)
        )
        ai_audit_notes = [str(n) for n in (sec.audit_notes or []) if str(n).strip()]
        audit_waivers = [
            {
                "waiver_id": w.waiver_id,
                "issue_text": w.issue_text,
                "justification": w.justification,
            }
            for w in (getattr(sec, "audit_waivers", None) or [])
        ]
        issues = deterministic_section_checks(
            project, active_section_id, sec.content_markdown or ""
        )

    cover = getattr(project, "cover", None)
    host_institutions = list(getattr(cover, "host_institutions", None) or []) if cover else []

    alerts = [
        {
            "alert_id": a.alert_id,
            "axis": a.axis,
            "title": a.title,
            "message": a.message,
        }
        for a in (project.coherence_alerts or [])
        if not active_section_id
        or a.source_section_id == active_section_id
        or a.target_section_id == active_section_id
    ]

    parts: List[str] = [
        f"Statut section : {section_status} | Nombre de mots actuel : {word_count}.",
        f"Sites/institutions déclarés (page de garde) : {', '.join(host_institutions) if host_institutions else 'Aucun'}."
    ]
    if ai_audit_notes:
        parts.append(
            "Remarques du dernier audit IA CAMES/FMSB enregistrées :\n"
            + "\n".join(f"- {n}" for n in ai_audit_notes)
        )
    if issues:
        parts.append("Points de conformité déterministes FMSB/CAMES détectés :\n" + "\n".join(f"- {i}" for i in issues))
    elif not ai_audit_notes:
        parts.append("Aucune anomalie déterministe ni remarque d'audit IA enregistrée sur cette section.")
    if audit_waivers:
        parts.append(
            "Dérogations tracées actives :\n"
            + "\n".join(f"- [{w['issue_text']}] {w['justification']}" for w in audit_waivers)
        )
    if alerts:
        parts.append(
            "Alertes de cohérence inter-chapitres :\n"
            + "\n".join(f"- {a['title']} : {a['message']}" for a in alerts[:5])
        )

    return {
        "tool": "run_section_audit",
        "section_id": active_section_id,
        "section_status": section_status,
        "word_count": word_count,
        "issues": issues,
        "ai_audit_notes": ai_audit_notes,
        "audit_waivers": audit_waivers,
        "host_institutions": host_institutions,
        "coherence_alerts": alerts,
        "summary_text": "\n".join(parts),
    }


def tool_search_protocol_versions(
    project: ProtocolProject,
    active_section_id: Optional[str],
    query: str = "",
) -> Dict[str, Any]:
    """Search archived versions in the active section to locate lost passages or earlier snapshots."""
    if not active_section_id or active_section_id not in project.sections:
        return {
            "tool": "search_protocol_versions",
            "matches": [],
            "best_version_id": None,
            "summary_text": "Aucune section active sélectionnée pour rechercher dans l'historique.",
        }

    sec = project.sections[active_section_id]
    versions = list(reversed(sec.versions or []))
    if not versions:
        return {
            "tool": "search_protocol_versions",
            "matches": [],
            "best_version_id": None,
            "summary_text": "Aucune version archivée disponible pour cette section.",
        }

    keywords = set(_tokenize(query))
    ranked: List[Dict[str, Any]] = []
    for idx, v in enumerate(versions):
        hay = f"{v.note or ''} {v.content_markdown or ''}".lower()
        overlap = sum(1 for kw in keywords if kw in hay) if keywords else 0
        # Slight recency bonus when scores tie
        score = overlap * 10 + max(0, 10 - idx)
        if keywords and overlap == 0:
            continue
        excerpt = re.sub(r"\s+", " ", (v.content_markdown or "").strip())[:180]
        ranked.append(
            {
                "version_id": v.version_id,
                "created_at": v.created_at.isoformat(),
                "source": v.source,
                "word_count": v.word_count,
                "note": v.note or "",
                "excerpt": excerpt,
                "overlap": overlap,
                "score": score,
            }
        )

    if not ranked:
        # Return latest 3 versions as fallback so user can always restore recent state
        for v in versions[:3]:
            ranked.append(
                {
                    "version_id": v.version_id,
                    "created_at": v.created_at.isoformat(),
                    "source": v.source,
                    "word_count": v.word_count,
                    "note": v.note or "",
                    "excerpt": re.sub(r"\s+", " ", (v.content_markdown or "").strip())[:180],
                    "overlap": 0,
                    "score": 0,
                }
            )
    else:
        ranked.sort(key=lambda item: -int(item["score"]))

    best_vid = ranked[0]["version_id"] if ranked else None
    lines = [
        f"- version_id={m['version_id']} ({m['source']}, {m['word_count']} mots, {m['note'] or '—'}): \"{m['excerpt']}\""
        for m in ranked[:5]
    ]
    return {
        "tool": "search_protocol_versions",
        "matches": ranked[:5],
        "best_version_id": best_vid,
        "summary_text": "\n".join(lines),
    }


class CompareMergeOutput(BaseModel):
    merged_markdown: str = Field(
        ...,
        description="Synthesized best-of-both Markdown preserving strong definitions, citations [n], and supervisor directives.",
    )
    comparison_summary: str = Field(
        ...,
        description="Concise explanation of what was kept from the original draft, what came from the new draft, and what redundancy was removed.",
    )


def tool_draft_compare_and_merge(
    project: ProtocolProject,
    section_id: str,
    instruction: str = "",
) -> Dict[str, Any]:
    """Generate a sandboxed candidate draft internally, compare with original, and propose the merged best."""
    if section_id not in project.sections:
        raise ValueError(f"Section introuvable : {section_id}")

    from app.services.copilot_service import format_supervisor_directives_block
    from app.services.drafting import draft_section_with_ai

    lang = normalize_lang(getattr(project, "language", "fr"))
    original_md = (project.sections[section_id].content_markdown or "").strip()
    directives_block = format_supervisor_directives_block(project)

    # 1. Run drafting on an isolated deep copy so the live project is NEVER modified
    sandbox = project.model_copy(deep=True)
    candidate_md = ""
    draft_model = ""
    try:
        draft_res = draft_section_with_ai(
            sandbox,
            section_id=section_id,
            user_instruction=(instruction or "").strip() or None,
        )
        candidate_md = str(draft_res.get("content_markdown") or "").strip()
        draft_model = str(draft_res.get("model_used") or "")
    except Exception:
        candidate_md = ""

    # 2. If original was empty and sandbox succeeded, propose the fresh sandbox draft cleanly
    if not original_md and candidate_md:
        summary_msg = (
            "Section initially empty: generated a complete draft in background sandbox ready to review and apply."
            if lang == "en"
            else "Section initialement vide : une proposition complète a été générée en coulisses, prête à être appliquée."
        )
        return {
            "tool": "draft_compare_and_merge",
            "section_id": section_id,
            "original_word_count": 0,
            "candidate_word_count": _count_words(candidate_md),
            "merged_word_count": _count_words(candidate_md),
            "proposed_markdown": candidate_md,
            "summary": summary_msg,
            "model_used": draft_model,
        }

    # 3. Compare original vs candidate (or polish original directly if candidate empty) and merge best parts
    refs_ctx = "\n".join(
        f"[{r.order_index}] {r.vancouver_citation}"
        for r in (project.references or [])[:15]
        if r.order_index
    )

    merge_prompt = f"""
Tu es un éditeur scientifique senior (FMSB / CAMES).
Mission : comparer la VERSION ACTUELLE de la section `{section_id}` avec une NOUVELLE PROPOSITION GÉNÉRÉE EN COULISSES, garder le meilleur des deux (en particulier les définitions précises et formulations rigoureuses de la version actuelle), supprimer les redondances et le remplissage, et produire une version unifiée optimale.

LANGUE ATTENDUE : {"English" if lang == "en" else "Français"}.
CONSIGNE SPÉCIFIQUE DE L'UTILISATEUR : {(instruction or 'Comparer, conserver les meilleurs passages et améliorer la qualité académique.').strip()}

{directives_block if directives_block else ''}

VERSION ACTUELLE (À PRÉSERVER SI MEILLEURE) :
{original_md or '(Vide)'}

NOUVELLE PROPOSITION INTERNE :
{candidate_md or '(Aucune nouvelle génération interne — améliore directement la version actuelle selon la consigne.)'}

RÉFÉRENCES DISPONIBLES (n'invente aucun numéro hors liste) :
{refs_ctx or '(Aucune)'}
"""

    model_used = draft_model
    try:
        res = gemini_router.generate_structured(
            role="copilot_text",
            contents=merge_prompt,
            schema=CompareMergeOutput,
            temperature=0.25,
        )
        dto: CompareMergeOutput = res["data"]
        merged_md, _ = sanitize_llm_typography(dto.merged_markdown.strip(), lang=lang)
        summary = dto.comparison_summary.strip()
        model_used = str(res.get("model_used") or draft_model)
    except Exception:
        merged_md = candidate_md or original_md
        summary = (
            "Comparaison interne effectuée (repli sur la meilleure version disponible)."
            if lang != "en"
            else "Internal comparison completed (fallback to best available draft)."
        )

    return {
        "tool": "draft_compare_and_merge",
        "section_id": section_id,
        "original_word_count": _count_words(original_md),
        "candidate_word_count": _count_words(candidate_md),
        "merged_word_count": _count_words(merged_md),
        "proposed_markdown": merged_md,
        "summary": summary,
        "model_used": model_used,
    }


def apply_surgical_text_patches(
    original_markdown: str,
    patches: List[Any],
) -> Dict[str, Any]:
    """Deterministically replace ONLY targeted passages, keeping the rest byte-for-byte intact."""
    result = original_markdown or ""
    applied_patches: List[Dict[str, str]] = []
    unmatched_patches: List[Dict[str, str]] = []

    for raw in patches or []:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        if not isinstance(raw, dict):
            continue
        target = str(raw.get("target_text") or "")
        replacement = str(raw.get("replacement_text") or "")
        reason = str(raw.get("reason") or "").strip()
        if not target.strip():
            continue

        idx = result.find(target)
        if idx >= 0:
            result = result[:idx] + replacement + result[idx + len(target):]
            applied_patches.append(
                {
                    "target_text": target,
                    "replacement_text": replacement,
                    "reason": reason,
                }
            )
            continue

        # Fallback: whitespace-tolerant single occurrence match
        parts = [p for p in re.split(r"\s+", target.strip()) if p]
        if parts:
            pattern = r"\s+".join(re.escape(p) for p in parts)
            m = re.search(pattern, result)
            if m is not None:
                matched_exact = result[m.start():m.end()]
                result = result[:m.start()] + replacement + result[m.end():]
                applied_patches.append(
                    {
                        "target_text": matched_exact,
                        "replacement_text": replacement,
                        "reason": reason,
                    }
                )
                continue

        unmatched_patches.append(
            {
                "target_text": target,
                "replacement_text": replacement,
                "reason": reason,
            }
        )

    return {
        "patched_markdown": result,
        "applied_patches": applied_patches,
        "unmatched_patches": unmatched_patches,
        "applied_count": len(applied_patches),
    }


class SurgicalPatchItem(BaseModel):
    target_text: str = Field(
        ...,
        description="Exact substring copied verbatim from the original section text to replace or update.",
    )
    replacement_text: str = Field(
        ...,
        description="Updated passage replacing ONLY target_text (or empty string to delete target_text).",
    )
    reason: str = Field(
        default="",
        description="Brief reason why this specific passage was modified.",
    )


class SurgicalPatchOutput(BaseModel):
    patches: List[SurgicalPatchItem] = Field(
        default_factory=list,
        description="Minimal list of exact target->replacement edits touching ONLY requested lines/sentences.",
    )
    summary: str = Field(
        ...,
        description="Short explanation of the targeted changes.",
    )


def tool_patch_section_text(
    project: ProtocolProject,
    section_id: str,
    instruction: str,
) -> Dict[str, Any]:
    """Generate surgical targeted replacements leaving all surrounding lines strictly untouched."""
    if section_id not in project.sections:
        raise ValueError(f"Section introuvable : {section_id}")

    from app.services.copilot_service import format_supervisor_directives_block

    lang = normalize_lang(getattr(project, "language", "fr"))
    original_md = project.sections[section_id].content_markdown or ""
    directives_block = format_supervisor_directives_block(project)

    refs_ctx = "\n".join(
        f"[{r.order_index}] {r.vancouver_citation}"
        for r in (project.references or [])[:15]
        if r.order_index
    )

    patch_prompt = f"""
Tu es un éditeur scientifique chirurgical (FMSB / CAMES).
RÈGLE ABSOLUE : NE RÉÉCRIS PAS toute la section ! Identifie UNIQUEMENT la ou les phrases/lignes exactes concernées par la demande et fournis une liste minimale d'opérations `patches`.
Tout le reste du texte original restera 100 % identique au caractère près.

CONTRAT STRICT POUR CHAQUE PATCH :
- `target_text` DOIT être copié MOT POUR MOT (caractère pour caractère) depuis le TEXTE ORIGINAL ci-dessous.
- Si l'utilisateur demande d'ajouter une précision après une phrase, copie cette phrase exacte dans `target_text` et écris `<phrase exacte> <ajout>` dans `replacement_text`.
- Si l'utilisateur demande de supprimer un passage, mets ce passage exact dans `target_text` et `""` dans `replacement_text`.
- N'invente jamais de chiffres ou de références `[n]` hors liste.

LANGUE ATTENDUE : {"English" if lang == "en" else "Français"}.
DEMANDE CIBLÉE : {(instruction or '').strip()}

{directives_block if directives_block else ''}

TEXTE ORIGINAL DE LA SECTION :
{original_md or '(Section vide)'}

RÉFÉRENCES DISPONIBLES :
{refs_ctx or '(Aucune)'}
"""

    model_used = ""
    raw_patches: List[Dict[str, Any]] = []
    summary = ""
    try:
        res = gemini_router.generate_structured(
            role="copilot_text",
            contents=patch_prompt,
            schema=SurgicalPatchOutput,
            temperature=0.15,
        )
        dto: SurgicalPatchOutput = res["data"]
        model_used = str(res.get("model_used") or "")
        summary = (dto.summary or "").strip()
        raw_patches = [p.model_dump() for p in (dto.patches or [])]
    except Exception as exc:
        summary = (
            f"Impossible de générer les correctifs ciblés ({exc})."
            if lang != "en"
            else f"Could not generate targeted patches ({exc})."
        )

    applied = apply_surgical_text_patches(original_md, raw_patches)
    if not summary:
        summary = (
            f"{applied['applied_count']} modification(s) ciblée(s) préparée(s) ; le reste de la section est intact."
            if lang != "en"
            else f"{applied['applied_count']} targeted edit(s) prepared; the rest of the section stays untouched."
        )

    return {
        "tool": "patch_section_text",
        "section_id": section_id,
        "patches": applied["applied_patches"],
        "unmatched_count": len(applied["unmatched_patches"]),
        "proposed_markdown": applied["patched_markdown"],
        "summary": summary,
        "model_used": model_used,
    }
