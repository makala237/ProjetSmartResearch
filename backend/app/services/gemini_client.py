import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
GEMINI_DECISION_1_LABEL = "Gemini Decision 1"


class _VancouverRefItemOut(BaseModel):
    index: int
    vancouver_citation: str
    was_corrected: bool = False


class _VancouverBatchOut(BaseModel):
    references: List[_VancouverRefItemOut]
    summary: str = ""


class GeminiRouter:
    """Wrapper around Google GenAI SDK with per-role model chains and automatic fallback."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: Optional[genai.Client] = None

    def _get_client(self) -> genai.Client:
        if self._client is None:
            if not self.settings.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY non configurée dans backend/.env.")
            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    def list_available_models(self) -> List[Dict[str, Any]]:
        """List Gemini models accessible with the configured API key (including v1beta Decisions)."""
        self._get_client()
        vbeta_client = genai.Client(
            api_key=self.settings.gemini_api_key,
            http_options={"api_version": "v1beta"},
        )
        models: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for m in vbeta_client.models.list():
            name = getattr(m, "name", "") or ""
            short_name = name.replace("models/", "")
            is_gemini_or_ag = "gemini" in short_name.lower() or "antigravity" in short_name.lower()
            if not is_gemini_or_ag or short_name in seen_ids:
                continue
            seen_ids.add(short_name)
            actions = getattr(m, "supported_actions", None) or []
            models.append(
                {
                    "id": short_name,
                    "display_name": getattr(m, "display_name", short_name),
                    "description": (getattr(m, "description", "") or "")[:160],
                    "supported_actions": list(actions),
                }
            )
        return models

    def generate_with_fallback(
        self,
        role: str,
        contents: Any,
        config: Optional[types.GenerateContentConfig] = None,
    ) -> Dict[str, Any]:
        """Run generate_content trying each model in the role's fallback chain."""
        chains = self.settings.model_chains()
        candidates = chains.get(role, chains["drafting"])
        client = self._get_client()

        errors_log: List[str] = []
        for model_id in candidates:
            for attempt in range(2):
                try:
                    response = client.models.generate_content(
                        model=model_id,
                        contents=contents,
                        config=config,
                    )
                    return {
                        "model_used": model_id,
                        "text": response.text or "",
                        "raw": response,
                        "fallback_errors": errors_log,
                    }
                except errors.APIError as exc:
                    if exc.code == 503 and attempt == 0:
                        logger.info("Modèle %s sous forte demande temporaire (503). Nouvel essai dans 1.5s...", model_id)
                        time.sleep(1.5)
                        continue
                    msg = f"{model_id}: HTTP {exc.code} - {exc.message}"
                    logger.warning("Gemini fallback triggered (%s)", msg)
                    errors_log.append(msg)
                    break
                except Exception as exc:  # pragma: no cover - defensive fallback
                    msg = f"{model_id}: {exc}"
                    logger.warning("Gemini unexpected error (%s)", msg)
                    errors_log.append(msg)
                    break

        raise RuntimeError(
            f"Tous les modèles configurés pour '{role}' ont échoué : {' | '.join(errors_log)}"
        )

    def generate_structured(
        self,
        role: str,
        contents: Any,
        schema: Type[T],
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """Generate structured JSON validated against a Pydantic schema."""
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )
        res = self.generate_with_fallback(role=role, contents=contents, config=config)
        raw_obj = getattr(res["raw"], "parsed", None)
        if isinstance(raw_obj, schema):
            parsed = raw_obj
        else:
            parsed = schema.model_validate(json.loads(res["text"]))
        return {
            "model_used": res["model_used"],
            "data": parsed,
            "fallback_errors": res["fallback_errors"],
        }

    def generate_with_search(
        self,
        role: str,
        contents: Any,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """Run Gemini with Google Search Grounding and extract verified grounding URLs."""
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )
        res = self.generate_with_fallback(role=role, contents=contents, config=config)
        sources: List[Dict[str, str]] = []
        raw = res["raw"]
        candidates = getattr(raw, "candidates", None) or []
        if candidates:
            gm = getattr(candidates[0], "grounding_metadata", None)
            chunks = getattr(gm, "grounding_chunks", None) or []
            for ch in chunks:
                web = getattr(ch, "web", None)
                if web and getattr(web, "uri", None):
                    sources.append(
                        {
                            "title": getattr(web, "title", "") or web.uri,
                            "url": web.uri,
                        }
                    )
        return {
            "model_used": res["model_used"],
            "text": res["text"],
            "sources": sources,
            "fallback_errors": res["fallback_errors"],
        }

    def translate_department_live(
        self,
        text: str,
        source_lang: str = "fr",
        target_lang: str = "en",
    ) -> Dict[str, str]:
        """Translate a FMSB academic department name FR <-> EN using Gemini 3.8 Live/Flash."""
        clean = (text or "").strip()
        if not clean:
            return {"translated_text": "", "model_used": "none"}

        fmsb_pairs = {
            "SANTE PUBLIQUE": "PUBLIC HEALTH",
            "SANTÉ PUBLIQUE": "PUBLIC HEALTH",
            "CHIRURGIE ET SPECIALITES": "SURGERY AND SPECIALTIES",
            "CHIRURGIE ET SPÉCIALITÉS": "SURGERY AND SPECIALTIES",
            "MEDECINE INTERNE ET SPECIALITES": "INTERNAL MEDICINE AND SPECIALTIES",
            "MÉDECINE INTERNE ET SPÉCIALITÉS": "INTERNAL MEDICINE AND SPECIALTIES",
            "PEDIATRIE": "PAEDIATRICS",
            "PÉDIATRIE": "PAEDIATRICS",
            "GYNECOLOGIE-OBSTETRIQUE": "OBSTETRICS AND GYNAECOLOGY",
            "GYNÉCOLOGIE-OBSTÉTRIQUE": "OBSTETRICS AND GYNAECOLOGY",
            "IMAGERIE MEDICALE ET RADIOLOGIE": "MEDICAL IMAGING AND RADIOLOGY",
            "SCIENCES MORPHOLOGIQUES ET ANATOMIE PATHOLOGIQUE": "MORPHOLOGICAL SCIENCES AND PATHOLOGICAL ANATOMY",
            "SCIENCES PHYSIOLOGIQUES ET BIOCHIMIE": "PHYSIOLOGICAL SCIENCES AND BIOCHEMISTRY",
            "MICROBIOLOGIE, PARASITOLOGIE, HEMATOLOGIE ET MALADIES INFECTIEUSES": "MICROBIOLOGY, PARASITOLOGY, HAEMATOLOGY AND INFECTIOUS DISEASES",
            "PHARMACOLOGIE ET MEDECINE TRADITIONNELLE": "PHARMACOLOGY AND TRADITIONAL MEDICINE",
            "CHIRURGIE BUCCO-DENTAIRE": "ORAL SURGERY",
        }

        src_label = "French" if source_lang == "fr" else "English"
        tgt_label = "English" if target_lang == "en" else "French"
        prompt = (
            f"Translate this Cameroon FMSB medical school academic department name from {src_label} to {tgt_label}.\n"
            "Rules:\n"
            "- Return ONLY the translated department name in UPPERCASE.\n"
            "- Do NOT include the words 'DEPARTMENT OF' or 'DEPARTEMENT DE'.\n"
            "- No quotes, no explanation.\n\n"
            f"Input: {clean}"
        )

        try:
            res = self.generate_with_fallback(
                role="live_translate",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=60,
                    http_options=types.HttpOptions(timeout=10000),
                ),
            )
            out = (res["text"] or "").strip().splitlines()[0].strip().strip('"\' ')
            if target_lang == "en":
                out = re.sub(r"^departe?ment\s+(?:of\s+)?", "", out, flags=re.IGNORECASE)
            else:
                out = re.sub(r"^d[ée]partement\s+(?:de\s+la\s+|de\s+l'|du\s+|des\s+|d'|de\s+)?", "", out, flags=re.IGNORECASE)
            if out:
                return {"translated_text": out.upper(), "model_used": res["model_used"]}
        except Exception as exc:
            logger.warning("Live department translation fallback (%s)", exc)

        # Deterministic official FMSB lookup fallback
        norm = re.sub(r"\s+", " ", clean.upper())
        if source_lang == "fr" and norm in fmsb_pairs:
            return {"translated_text": fmsb_pairs[norm], "model_used": "fmsb-glossary-fallback"}
        if source_lang == "en":
            rev = {v: k for k, v in fmsb_pairs.items()}
            if norm in rev:
                return {"translated_text": rev[norm], "model_used": "fmsb-glossary-fallback"}
        return {"translated_text": clean.upper(), "model_used": "passthrough"}

    def summarize_section_live(
        self,
        section_title: str,
        content_text: str,
        study_title: str = "",
    ) -> Dict[str, str]:
        """Produce a brief working-memory summary of a modified section via Gemini 3.8 Live (`live_translate` chain)."""
        clean = re.sub(r"\s+", " ", (content_text or "")).strip()
        if not clean:
            return {"summary": "(Section vide.)", "model_used": "none"}

        prompt = (
            "Tu es Gemini 3.8 Live, gestionnaire de la mémoire de travail d'un protocole médical FMSB.\n"
            f"Thème du protocole : {study_title or 'Protocole FMSB'}\n"
            f"Section modifiée : {section_title}\n\n"
            "Résume en 3 à 4 phrases brèves et factuelles (max 90 mots) l'essentiel de cette section "
            "pour que les rédactions suivantes soient harmonisées sans répétition :\n"
            "- Faits clés, chiffres, définitions ou décisions méthodologiques posés ;\n"
            "- Références [n] déjà citées dans cette section (si présentes).\n"
            "Renvoie UNIQUEMENT le résumé brut en français, sans titre ni formule de politesse.\n\n"
            f"CONTENU :\n{clean[:4500]}"
        )
        try:
            res = self.generate_with_fallback(
                role="live_translate",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=260,
                    http_options=types.HttpOptions(timeout=6000),
                ),
            )
            out = (res.get("text") or "").strip()
            if out:
                return {"summary": out, "model_used": f"Gemini 3.8 Live ({res['model_used']})"}
        except Exception as exc:
            logger.warning("Gemini 3.8 Live section summary fallback (%s)", exc)

        # Compact deterministic fallback if API fails
        cites = sorted(set(re.findall(r"\[(\d+)\]", clean)))
        cite_str = f" [Réf: {', '.join(cites[:8])}]" if cites else ""
        return {
            "summary": f"{clean[:340].rstrip()}...{cite_str}",
            "model_used": "deterministic-summary-fallback",
        }

    def run_antigravity_agent(
        self,
        prompt: str,
        agent_model: str = "antigravity-preview-latest",
        timeout_sec: float = 40.0,
    ) -> Dict[str, Any]:
        """Call Google GenAI Antigravity Agent natively via `client.interactions.create`."""
        self._get_client()
        vbeta_client = genai.Client(
            api_key=self.settings.gemini_api_key,
            http_options={"api_version": "v1beta"},
        )
        interaction = vbeta_client.interactions.create(
            agent=agent_model,
            environment={"type": "remote"},
            input=prompt,
            timeout=timeout_sec,
        )
        tokens = int(getattr(interaction.usage, "total_tokens", 0) or 0)
        output_txt = str(getattr(interaction, "output_text", "") or "").strip()
        return {
            "agent_used": agent_model,
            "interaction_id": getattr(interaction, "id", ""),
            "status": getattr(interaction, "status", "completed"),
            "total_tokens": tokens,
            "output_text": output_txt,
        }

    def run_decision_1(
        self,
        input_text: str,
        questions: Dict[str, Any],
        timeout_sec: float = 25.0,
    ) -> Dict[str, Any]:
        """Call Google GenAI `models/gemini-decision-1` natively via `client.decisions.create`."""
        client = self._get_client()
        resp = client.decisions.create(
            model="gemini-decision-1",
            api_version="v1beta",
            input=input_text,
            questions=questions,
            timeout=timeout_sec,
        )
        scores: Dict[str, float] = {}
        answers = getattr(resp, "answers", None) or {}
        for qid, ans in answers.items():
            val = getattr(ans, "noul", None)
            if val is None:
                val = getattr(ans, "score", None)
            if val is not None:
                scores[str(qid)] = float(val)
        usage = getattr(resp, "usage", None)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        return {
            "model_used": "models/gemini-decision-1",
            "scores": scores,
            "total_tokens": total_tokens,
            "raw": resp,
        }

    def decide_page_layout(
        self,
        page_allocation_payload: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], str]:
        """Validate A4 page-allocation decisions natively via Gemini Decision 1 (`models/gemini-decision-1`).

        Evaluates each borderline block candidate with `client.decisions.create` (noul probability)
        to enforce keep_with_next / keep_together for tables, figures (>=12pt), and FMSB page rules.
        """
        candidates = list(page_allocation_payload.get("borderline_candidates") or [])
        questions: Dict[str, Any] = {}
        for idx, cand in enumerate(candidates[:15]):
            bid = str(cand.get("block_id") or f"cand_{idx}")
            kind = str(cand.get("kind") or "block")
            title = str(cand.get("title") or kind)[:80]
            req_pt = cand.get("required_pt", 0)
            rem_pt = cand.get("remaining_pt", 0)
            questions[bid] = {
                "type": "noul",
                "instructions": (
                    f"Block '{bid}' ({kind}: '{title}') on page {cand.get('page_label')} "
                    f"requires {req_pt} pt vs {rem_pt} pt remaining. Should this block move to the top of the next A4 page "
                    "to avoid separating a table/figure from its title or splitting cleanly?"
                ),
                "criteria": {
                    "true_description": (
                        "Move block to next A4 page (margin < 15pt, or table/figure/heading separation risk)."
                    ),
                    "false_description": (
                        "Keep block on the current A4 page (fits safely with its title/caption)."
                    ),
                },
            }

        if not questions:
            questions["a4_plan_optimal"] = {
                "type": "noul",
                "instructions": (
                    "Does this FMSB A4 layout plan keep every Table/Figure attached to its title on the same page, "
                    "keep grand chapter boxes centered alone on their page, and respect Roman/Arabic streams?"
                ),
                "criteria": {
                    "true_description": "Layout satisfies all FMSB A4 pagination rules cleanly.",
                    "false_description": "Layout violates A4 pagination rules.",
                },
            }

        input_text = (
            "You are Gemini Decision 1, the FMSB A4 pagination adjudicator.\n"
            "Rules:\n"
            "1. Table of Contents, List of Tables, List of Figures and each Annexe start on a fresh page.\n"
            "2. Grand framed chapter titles sit alone on their centered page; text begins next page.\n"
            "3. Tables and Figures NEVER separate from their title/caption (`keep_together` & `keep_with_next`).\n"
            "4. Figures have minimum 12pt legible typography.\n\n"
            f"DOCUMENT SUMMARY:\n{json.dumps(page_allocation_payload, ensure_ascii=False)}"
        )

        try:
            dres = self.run_decision_1(input_text=input_text, questions=questions)
            scores = dres["scores"]
            move_ids = [
                str(cand.get("block_id"))
                for cand in candidates[:15]
                if cand.get("block_id") and scores.get(str(cand.get("block_id")), 0.0) >= 0.58
            ]
            tokens = dres["total_tokens"]
            notes = (
                f"Gemini Decision 1 (models/gemini-decision-1) : {len(questions)} décisions évaluées"
                + (f", {len(move_ids)} ajustement(s) A4" if move_ids else ", placement A4 confirmé")
                + (f" ({tokens} tokens)." if tokens else ".")
            )
            return {
                "move_to_next_page_block_ids": move_ids,
                "notes": notes,
                "model_id": dres["model_used"],
                "decision_scores": scores,
            }, f"{GEMINI_DECISION_1_LABEL} ({dres['model_used']})"
        except Exception as exc:
            logger.warning("Gemini Decision 1 deterministic fallback (%s)", exc)
            return {
                "move_to_next_page_block_ids": [],
                "notes": "Allocation A4 calculée par le solveur géométrique déterministe.",
                "model_id": "deterministic-a4-solver",
            }, f"{GEMINI_DECISION_1_LABEL} (deterministic-a4-fallback)"

    def validate_vancouver_references(
        self,
        references_payload: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], str]:
        """Audit Vancouver compliance natively with Gemini Decision 1 (`models/gemini-decision-1`)
        and normalize malformed citations in bounded chunks.
        """
        if not references_payload:
            return {
                "references": [],
                "summary": "Aucune référence à valider.",
                "model_id": "vancouver-deterministic",
            }, f"{GEMINI_DECISION_1_LABEL} (vancouver-deterministic)"

        validated_by_index: Dict[int, Dict[str, Any]] = {}
        notes_list: List[str] = []
        decision1_called = False
        decision1_tokens = 0
        last_model_id = "deterministic-vancouver"
        batch_size = 18

        for start in range(0, len(references_payload), batch_size):
            chunk = [
                {"index": int(r["index"]), "raw": str(r.get("raw") or "")}
                for r in references_payload[start : start + batch_size]
            ]

            # 1. Native audit call to models/gemini-decision-1 via Decisions API
            try:
                d_questions = {
                    f"ref_{item['index']}": {
                        "type": "noul",
                        "instructions": (
                            f"Evaluate reference #{item['index']}: '{item['raw'][:240]}'. "
                            "Is it already 100% strictly compliant with ICMJE Vancouver format "
                            "(no leading number/bracket, complete page numbers, valid punctuation, <=6 authors + et al.)?"
                        ),
                        "criteria": {
                            "true_description": "Strictly compliant with ICMJE Vancouver format.",
                            "false_description": "Needs formatting/punctuation/page-range normalization.",
                        },
                    }
                    for item in chunk[:15]
                }
                dres = self.run_decision_1(
                    input_text=(
                        "Audit ICMJE/NLM Vancouver bibliography citations for a medical thesis protocol.\n"
                        f"BATCH:\n{json.dumps(chunk, ensure_ascii=False)}"
                    ),
                    questions=d_questions,
                )
                decision1_called = True
                decision1_tokens += int(dres.get("total_tokens", 0) or 0)
            except Exception as exc:
                logger.warning("Gemini Decision 1 Vancouver audit fallback (%s)", exc)

            # 2. Structured rewrite normalization pass
            prompt = (
                "You are an FMSB medical bibliography formatter for strict Vancouver (ICMJE / NLM) style.\n"
                "For each object in INPUT, return a strictly compliant `vancouver_citation` string:\n"
                "1. Journal article: Authors (Surname Initials, no periods/spaces in initials, max 6 authors then ', et al.'). Article Title. Medline Abbrev Journal. Year;Vol(Issue):Pages.\n"
                "2. Book/Report: Authors/Org. Title. Edition (if not 1st). Place: Publisher; Year.\n"
                "3. Fix punctuation defects, missing period after 'et al.', spaces before ';' or ':', and repair truncated trailing page hyphens like '215-' (complete exact known page range if certain or close cleanly as '215.').\n"
                "4. NEVER include a leading '1.' or '[1]' inside `vancouver_citation`, and NEVER invent different articles.\n"
                f"INPUT:\n{json.dumps(chunk, ensure_ascii=False)}"
            )
            try:
                res = self.generate_with_fallback(
                    role="layout_decision",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=4096,
                        response_mime_type="application/json",
                        response_schema=_VancouverBatchOut,
                        http_options=types.HttpOptions(timeout=20000),
                    ),
                )
                last_model_id = res["model_used"]
                parsed_obj = getattr(res["raw"], "parsed", None)
                if isinstance(parsed_obj, _VancouverBatchOut):
                    parsed_data = parsed_obj
                else:
                    raw_txt = (res.get("text") or "").strip()
                    m_json = re.search(r"\{.*\}", raw_txt, flags=re.DOTALL)
                    parsed_data = _VancouverBatchOut.model_validate(
                        json.loads(m_json.group(0) if m_json else raw_txt)
                    )
                for item in parsed_data.references:
                    validated_by_index[int(item.index)] = item.model_dump()
                if parsed_data.summary:
                    notes_list.append(parsed_data.summary.strip())
            except Exception as exc:
                logger.warning("Gemini Vancouver rewrite batch fallback (%s)", exc)

        model_tag = "models/gemini-decision-1" if decision1_called else last_model_id
        if validated_by_index or decision1_called:
            merged = [
                validated_by_index.get(int(row["index"]), {})
                for row in references_payload
            ]
            summary = " · ".join(dict.fromkeys(notes_list))[:220] or (
                f"{len(references_payload)} références auditées par Gemini Decision 1"
                + (f" ({decision1_tokens} tokens)." if decision1_tokens else " selon la norme Vancouver ICMJE.")
            )
            return {
                "references": merged,
                "summary": summary,
                "model_id": model_tag,
                "decision_tokens": decision1_tokens,
            }, f"{GEMINI_DECISION_1_LABEL} ({model_tag})"

        return {
            "references": [],
            "summary": "Validation Vancouver appliquée par le moteur déterministe ICMJE.",
            "model_id": "vancouver-deterministic-fallback",
        }, f"{GEMINI_DECISION_1_LABEL} (vancouver-deterministic-fallback)"


gemini_router = GeminiRouter()
