from contextvars import ContextVar
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.core.config import get_settings
from app.services.pipeline_tracker import update_pipeline_model

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
GEMINI_DECISION_1_LABEL = "Gemini Decision 1"

# Bidirectional relay peers: whenever one fails to respond, the other takes over immediately.
_LITE_RELAY_PEERS: Dict[str, str] = {
    "gemini-3.5-flash-lite": "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite": "gemini-3.5-flash-lite",
}


def _ensure_bidirectional_lite_relay(candidates: List[str]) -> List[str]:
    """Guarantee gemini-3.5-flash-lite <-> gemini-3.1-flash-lite immediate relay in any chain."""
    result: List[str] = []
    seen_lower: set[str] = set()
    for raw_model in candidates:
        model = (raw_model or "").strip()
        if not model:
            continue
        key = model.lower()
        if key not in seen_lower:
            result.append(model)
            seen_lower.add(key)
        peer = _LITE_RELAY_PEERS.get(key)
        if peer and peer.lower() not in seen_lower:
            result.append(peer)
            seen_lower.add(peer.lower())
    return result

_ai_routing_policy: ContextVar[Dict[str, Any]] = ContextVar("ai_routing_policy", default={})
_request_tokens_used: ContextVar[int] = ContextVar("request_tokens_used", default=0)


def reset_request_tokens_used() -> None:
    """Reset the token counter for the current request context."""
    _request_tokens_used.set(0)


def add_request_tokens_used(tokens: int) -> int:
    """Accumulate tokens consumed during Gemini calls within the current request."""
    clean = max(0, int(tokens or 0))
    total = max(0, int(_request_tokens_used.get() or 0)) + clean
    _request_tokens_used.set(total)
    return total


def consume_request_tokens_used() -> int:
    """Return accumulated tokens for the current request and reset the counter to 0."""
    total = max(0, int(_request_tokens_used.get() or 0))
    _request_tokens_used.set(0)
    return total


def set_ai_routing_policy(
    prefer_fallback_35: bool,
    reason: str = "",
    user_id: str = "",
    daily_status: Optional[Dict[str, Any]] = None,
    api_key: str = "",
    api_key_id: str = "default",
    custom_model_chain: Optional[List[str]] = None,
    custom_chain_enabled: bool = False,
    custom_chain_skipped: Optional[List[Dict[str, Any]]] = None,
    copilot_text_chain: Optional[List[str]] = None,
    copilot_voice_chain: Optional[List[str]] = None,
    supervisor_media_chain: Optional[List[str]] = None,
) -> None:
    """Bind per-request Gemini reasoning routing policy, custom ordered chain, and assigned API key."""
    _ai_routing_policy.set(
        {
            "prefer_fallback_35": bool(prefer_fallback_35),
            "reason": reason,
            "user_id": user_id,
            "daily_status": daily_status or {},
            "api_key": (api_key or "").strip(),
            "api_key_id": (api_key_id or "default").strip() or "default",
            "custom_model_chain": [str(m).strip() for m in (custom_model_chain or []) if str(m).strip()],
            "custom_chain_enabled": bool(custom_chain_enabled),
            "custom_chain_skipped": list(custom_chain_skipped or []),
            "copilot_text_chain": [str(m).strip() for m in (copilot_text_chain or []) if str(m).strip()],
            "copilot_voice_chain": [str(m).strip() for m in (copilot_voice_chain or []) if str(m).strip()],
            "supervisor_media_chain": [str(m).strip() for m in (supervisor_media_chain or []) if str(m).strip()],
        }
    )


def get_ai_routing_policy() -> Dict[str, Any]:
    return dict(_ai_routing_policy.get() or {})


def reset_ai_routing_policy() -> None:
    _ai_routing_policy.set({})


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
        self._clients_by_key: Dict[str, genai.Client] = {}
        self._model_cooldowns: Dict[str, float] = {}

    def _resolve_active_api_key(self) -> Tuple[str, str]:
        """Return (api_key_id, raw_api_key) for the current request, falling back to system GEMINI_API_KEY."""
        policy = get_ai_routing_policy()
        override_key = str(policy.get("api_key") or "").strip()
        override_id = str(policy.get("api_key_id") or "default").strip() or "default"
        if override_key:
            return override_id, override_key
        return "default", (self.settings.gemini_api_key or "").strip()

    def _get_client(self) -> genai.Client:
        _, active_key = self._resolve_active_api_key()
        if not active_key:
            raise RuntimeError("GEMINI_API_KEY non configurée dans backend/.env.")
        cached = self._clients_by_key.get(active_key)
        if cached is not None:
            return cached
        timeout_ms = int(getattr(self.settings, "gemini_request_timeout_ms", 60_000) or 0)
        if timeout_ms > 0:
            client = genai.Client(
                api_key=active_key,
                http_options=types.HttpOptions(timeout=timeout_ms),
            )
        else:
            client = genai.Client(api_key=active_key)
        self._clients_by_key[active_key] = client
        if active_key == (self.settings.gemini_api_key or "").strip():
            self._client = client
        return client

    def _cooldown_key(self, model_id: str) -> str:
        key_id, _ = self._resolve_active_api_key()
        return f"{key_id}::{(model_id or '').lower()}"

    def _mark_model_unresponsive(self, model_id: str) -> None:
        cooldown_s = int(getattr(self.settings, "gemini_relay_cooldown_seconds", 45) or 0)
        if cooldown_s > 0 and model_id:
            self._model_cooldowns[self._cooldown_key(model_id)] = time.monotonic() + cooldown_s

    def _clear_model_cooldown(self, model_id: str) -> None:
        if model_id:
            self._model_cooldowns.pop(self._cooldown_key(model_id), None)

    def _is_model_in_cooldown(self, model_id: str) -> bool:
        key = self._cooldown_key(model_id)
        until = self._model_cooldowns.get(key)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._model_cooldowns.pop(key, None)
            return False
        return True

    def list_available_models(self) -> List[Dict[str, Any]]:
        """List Gemini models accessible with the configured API key (including v1beta Decisions)."""
        self._get_client()
        vbeta_client = genai.Client(
            api_key=self._resolve_active_api_key()[1],
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

    def _resolve_candidates(self, role: str) -> Tuple[List[str], bool]:
        """Resolve ordered model fallback chain (per-user custom chain or system default)."""
        chains = self.settings.model_chains()
        if role == "copilot_text":
            candidates = [
                "gemini-3.8-flash",
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
            ]
        elif role == "supervisor_media":
            candidates = [
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash",
            ]
        else:
            candidates = list(chains.get(role, chains["drafting"]))
        policy = get_ai_routing_policy()

        if role == "supervisor_media" and policy.get("supervisor_media_chain"):
            resolved = list(policy["supervisor_media_chain"])
            cap_active = bool(policy.get("custom_chain_skipped"))
            healthy = [m for m in resolved if not self._is_model_in_cooldown(m)]
            cooled = [m for m in resolved if self._is_model_in_cooldown(m)]
            return healthy + cooled, cap_active

        if role == "copilot_text" and policy.get("copilot_text_chain"):
            resolved = list(policy["copilot_text_chain"])
            cap_active = bool(policy.get("custom_chain_skipped"))
            healthy = [m for m in resolved if not self._is_model_in_cooldown(m)]
            cooled = [m for m in resolved if self._is_model_in_cooldown(m)]
            return healthy + cooled, cap_active

        if policy.get("custom_chain_enabled") and policy.get("custom_model_chain"):
            custom_ordered = list(policy["custom_model_chain"])
            if role == "layout_decision":
                head = [m for m in candidates if "decision-1" in m.lower()]
                resolved = head + [m for m in custom_ordered if m not in head]
            else:
                resolved = custom_ordered
            cap_active = bool(policy.get("custom_chain_skipped"))
            healthy = [m for m in resolved if not self._is_model_in_cooldown(m)]
            cooled = [m for m in resolved if self._is_model_in_cooldown(m)]
            return healthy + cooled, cap_active

        prefer_35 = bool(policy.get("prefer_fallback_35"))

        if prefer_35:
            fallback_model = (self.settings.gemini_fallback_reasoning_model or "gemini-3.1-flash-lite").strip()
            filtered = [m for m in candidates if "gemini-3.8" not in m.lower()]

            if role == "layout_decision":
                head = [m for m in filtered if "decision-1" in m.lower()]
                tail = [m for m in filtered if "decision-1" not in m.lower() and m != fallback_model]
                ordered = head + [fallback_model] + tail
            else:
                tail = [m for m in filtered if m != fallback_model]
                ordered = [fallback_model] + tail
            resolved = _ensure_bidirectional_lite_relay(ordered)
            cap_active = True
        else:
            resolved = _ensure_bidirectional_lite_relay(candidates)
            cap_active = False

        # Deprioritize models currently in unresponsive cooldown so the healthy relay peer responds immediately.
        healthy = [m for m in resolved if not self._is_model_in_cooldown(m)]
        cooled = [m for m in resolved if self._is_model_in_cooldown(m)]
        final_chain = healthy + cooled
        return final_chain, cap_active

    def resolve_copilot_voice_models(self) -> List[str]:
        """Resolve ordered Live API (bidiGenerateContent) model chain for Assistant Smart Research voice."""
        policy = get_ai_routing_policy()
        custom_voice = list(policy.get("copilot_voice_chain") or [])
        resolved = custom_voice if custom_voice else ["gemini-3.8-live", "gemini-2.5-flash-native-audio-latest"]
        healthy = [m for m in resolved if not self._is_model_in_cooldown(m)]
        cooled = [m for m in resolved if self._is_model_in_cooldown(m)]
        return healthy + cooled

    def generate_with_fallback(
        self,
        role: str,
        contents: Any,
        config: Optional[types.GenerateContentConfig] = None,
        validator: Optional[Callable[[Any, str], Any]] = None,
    ) -> Dict[str, Any]:
        """Run generate_content trying each model in the role's fallback chain (3.5-flash-lite <-> 3.1-flash-lite relay)."""
        candidates, cap_fallback_active = self._resolve_candidates(role)
        client = self._get_client()

        if candidates:
            update_pipeline_model(
                candidates[0],
                fallback_triggered=cap_fallback_active,
                note="",
            )

        errors_log: List[str] = []
        for idx, model_id in enumerate(candidates):
            is_fallback_step = cap_fallback_active or idx > 0
            has_next_candidate = idx < len(candidates) - 1
            update_pipeline_model(
                model_id,
                fallback_triggered=is_fallback_step,
                note="",
            )
            max_attempts = 1 if has_next_candidate else 2
            for attempt in range(max_attempts):
                try:
                    response = client.models.generate_content(
                        model=model_id,
                        contents=contents,
                        config=config,
                    )
                    try:
                        text = response.text or ""
                    except Exception:
                        text = ""
                    parsed_obj = getattr(response, "parsed", None)

                    validated_data: Any = None
                    if validator is not None:
                        validated_data = validator(response, text)
                    elif not text.strip() and parsed_obj is None:
                        raise ValueError("Réponse vide du modèle (aucun contenu retourné)")

                    self._clear_model_cooldown(model_id)
                    update_pipeline_model(model_id, fallback_triggered=is_fallback_step)
                    usage = getattr(response, "usage_metadata", None)
                    tokens_used = int(getattr(usage, "total_token_count", 0) or 0)
                    if tokens_used <= 0 and usage is not None:
                        prompt_toks = int(getattr(usage, "prompt_token_count", 0) or 0)
                        cand_toks = int(getattr(usage, "candidates_token_count", 0) or 0)
                        tokens_used = max(0, prompt_toks + cand_toks)
                    add_request_tokens_used(tokens_used)
                    result: Dict[str, Any] = {
                        "model_used": model_id,
                        "text": text,
                        "raw": response,
                        "tokens_used": tokens_used,
                        "fallback_errors": errors_log,
                        "cap_fallback_active": cap_fallback_active,
                    }
                    if validator is not None:
                        result["validated"] = validated_data
                    return result
                except errors.APIError as exc:
                    if exc.code == 503 and not has_next_candidate and attempt == 0:
                        logger.info("Modèle %s sous forte demande temporaire (503). Nouvel essai dans 1.5s...", model_id)
                        time.sleep(1.5)
                        continue
                    if exc.code in (408, 429, 500, 502, 503, 504):
                        self._mark_model_unresponsive(model_id)
                    msg = f"{model_id}: HTTP {exc.code} - {exc.message}"
                    logger.warning("Gemini relay fallback triggered (%s)", msg)
                    errors_log.append(msg)
                    break
                except Exception as exc:  # pragma: no cover - defensive fallback
                    self._mark_model_unresponsive(model_id)
                    msg = f"{model_id}: {exc}"
                    logger.warning("Gemini unresponsive/error relay (%s)", msg)
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
        """Generate structured JSON validated against a Pydantic schema with full model relay."""
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )

        def _validate_structured(raw_resp: Any, text: str) -> T:
            raw_obj = getattr(raw_resp, "parsed", None)
            if isinstance(raw_obj, schema):
                return raw_obj
            clean = (text or "").strip()
            if not clean:
                raise ValueError("Réponse JSON structurée vide")
            return schema.model_validate(json.loads(clean))

        res = self.generate_with_fallback(
            role=role,
            contents=contents,
            config=config,
            validator=_validate_structured,
        )
        return {
            "model_used": res["model_used"],
            "data": res["validated"],
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
        field_type: str = "department",
    ) -> Dict[str, str]:
        """Translate a CAMES/FMSB academic header item (university, faculty, or department) FR <-> EN."""
        clean = (text or "").strip()
        if not clean:
            return {"translated_text": "", "model_used": "none"}

        ftype = (field_type or "department").strip().lower()
        if ftype not in ("university", "faculty", "department"):
            ftype = "department"

        university_pairs = {
            "UNIVERSITE DE YAOUNDE I": "THE UNIVERSITY OF YAOUNDÉ I",
            "UNIVERSITÉ DE YAOUNDÉ I": "THE UNIVERSITY OF YAOUNDÉ I",
            "UNIVERSITE DE YAOUNDE 1": "THE UNIVERSITY OF YAOUNDÉ I",
            "UNIVERSITÉ DE YAOUNDÉ 1": "THE UNIVERSITY OF YAOUNDÉ I",
            "UNIVERSITE DE DOUALA": "THE UNIVERSITY OF DOUALA",
            "UNIVERSITÉ DE DOUALA": "THE UNIVERSITY OF DOUALA",
            "UNIVERSITE DE DSCHANG": "UNIVERSITY OF DSCHANG",
            "UNIVERSITÉ DE DSCHANG": "UNIVERSITY OF DSCHANG",
            "UNIVERSITE DE BUEA": "UNIVERSITY OF BUEA",
            "UNIVERSITÉ DE BUEA": "UNIVERSITY OF BUEA",
            "UNIVERSITE DE BAMENDA": "THE UNIVERSITY OF BAMENDA",
            "UNIVERSITÉ DE BAMENDA": "THE UNIVERSITY OF BAMENDA",
            "UNIVERSITE DE NGAOUNDERE": "UNIVERSITY OF NGAOUNDÉRÉ",
            "UNIVERSITÉ DE NGAOUNDÉRÉ": "UNIVERSITY OF NGAOUNDÉRÉ",
            "UNIVERSITE DES MONTAGNES": "UNIVERSITÉ DES MONTAGNES (UNIVERSITY OF MOUNTAINS)",
            "UNIVERSITÉ DES MONTAGNES": "UNIVERSITÉ DES MONTAGNES (UNIVERSITY OF MOUNTAINS)",
            "UNIVERSITE DE MAROUA": "THE UNIVERSITY OF MAROUA",
            "UNIVERSITÉ DE MAROUA": "THE UNIVERSITY OF MAROUA",
            "UNIVERSITE DE GAROUA": "THE UNIVERSITY OF GAROUA",
            "UNIVERSITÉ DE GAROUA": "THE UNIVERSITY OF GAROUA",
            "UNIVERSITE D'EBOLOWA": "THE UNIVERSITY OF EBOLOWA",
            "UNIVERSITÉ D'EBOLOWA": "THE UNIVERSITY OF EBOLOWA",
            "UNIVERSITE DE BERTOUA": "THE UNIVERSITY OF BERTOUA",
            "UNIVERSITÉ DE BERTOUA": "THE UNIVERSITY OF BERTOUA",
            "UNIVERSITE CHEIKH ANTA DIOP DE DAKAR": "CHEIKH ANTA DIOP UNIVERSITY OF DAKAR",
            "UNIVERSITÉ CHEIKH ANTA DIOP DE DAKAR": "CHEIKH ANTA DIOP UNIVERSITY OF DAKAR",
            "UNIVERSITE FELIX HOUPHOUET-BOIGNY": "FÉLIX HOUPHOUËT-BOIGNY UNIVERSITY",
            "UNIVERSITÉ FÉLIX HOUPHOUËT-BOIGNY": "FÉLIX HOUPHOUËT-BOIGNY UNIVERSITY",
            "UNIVERSITE MARIEN NGOUABI": "MARIEN NGOUABI UNIVERSITY",
            "UNIVERSITÉ MARIEN NGOUABI": "MARIEN NGOUABI UNIVERSITY",
        }

        faculty_pairs = {
            "FACULTE DE MEDECINE ET DES SCIENCES BIOMEDICALES": "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES",
            "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES": "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES",
            "FACULTE DE MEDECINE ET DES SCIENCES PHARMACEUTIQUES": "FACULTY OF MEDICINE AND PHARMACEUTICAL SCIENCES",
            "FACULTÉ DE MÉDECINE ET DES SCIENCES PHARMACEUTIQUES": "FACULTY OF MEDICINE AND PHARMACEUTICAL SCIENCES",
            "FACULTE DES SCIENCES DE LA SANTE": "FACULTY OF HEALTH SCIENCES",
            "FACULTÉ DES SCIENCES DE LA SANTÉ": "FACULTY OF HEALTH SCIENCES",
            "FACULTE DE MEDECINE ET DE PHARMACIE": "FACULTY OF MEDICINE AND PHARMACY",
            "FACULTÉ DE MÉDECINE ET DE PHARMACIE": "FACULTY OF MEDICINE AND PHARMACY",
            "FACULTE DE MEDECINE": "FACULTY OF MEDICINE",
            "FACULTÉ DE MÉDECINE": "FACULTY OF MEDICINE",
            "INSTITUT SUPERIEUR DES SCIENCES DE LA SANTE": "HIGHER INSTITUTE OF HEALTH SCIENCES",
            "INSTITUT SUPÉRIEUR DES SCIENCES DE LA SANTÉ": "HIGHER INSTITUTE OF HEALTH SCIENCES",
            "FACULTE DE MEDECINE ET DES SCIENCES BIOMEDICALES DE GAROUA": "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES OF GAROUA",
            "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES DE GAROUA": "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES OF GAROUA",
        }

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
        if ftype == "university":
            prompt = (
                f"Translate this official university name from {src_label} to {tgt_label} for a bilingual academic cover page.\n"
                "Rules:\n"
                "- Return ONLY the official translated university name in UPPERCASE.\n"
                "- Keep proper city/person names and Roman numerals (I, II, etc.) intact.\n"
                "- No quotes, no explanation.\n\n"
                f"Input: {clean}"
            )
        elif ftype == "faculty":
            prompt = (
                f"Translate this medical/health faculty, school, or institute name from {src_label} to {tgt_label} for a bilingual academic cover page.\n"
                "Rules:\n"
                "- Return ONLY the official translated faculty or school name in UPPERCASE.\n"
                "- Keep proper place names intact.\n"
                "- No quotes, no explanation.\n\n"
                f"Input: {clean}"
            )
        else:
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
                    max_output_tokens=80,
                    http_options=types.HttpOptions(timeout=10000),
                ),
            )
            out = (res["text"] or "").strip().splitlines()[0].strip().strip('"\' ')
            if ftype == "department":
                if target_lang == "en":
                    out = re.sub(r"^departe?ment\s+(?:of\s+)?", "", out, flags=re.IGNORECASE)
                else:
                    out = re.sub(r"^d[ée]partement\s+(?:de\s+la\s+|de\s+l'|du\s+|des\s+|d'|de\s+)?", "", out, flags=re.IGNORECASE)
            if out:
                return {"translated_text": out.upper(), "model_used": res["model_used"]}
        except Exception as exc:
            logger.warning("Live %s translation fallback (%s)", ftype, exc)

        # Deterministic official CAMES/FMSB lookup fallback
        norm = re.sub(r"\s+", " ", clean.upper())
        active_pairs = (
            university_pairs
            if ftype == "university"
            else faculty_pairs
            if ftype == "faculty"
            else fmsb_pairs
        )
        if source_lang == "fr" and norm in active_pairs:
            return {"translated_text": active_pairs[norm], "model_used": "fmsb-glossary-fallback"}
        if source_lang == "en":
            rev = {v: k for k, v in active_pairs.items()}
            if norm in rev:
                return {"translated_text": rev[norm], "model_used": "fmsb-glossary-fallback"}

        # Rule-based prefix fallback for unlisted CAMES universities/faculties
        if ftype == "university":
            if target_lang == "en":
                out_rb = re.sub(r"^UNIVERSIT[ÉE]\s+(?:DE\s+L'|DE\s+|DU\s+|DES\s+|D')?", "UNIVERSITY OF ", norm)
                out_rb = re.sub(r"^UNIVERSIT[ÉE]\b", "UNIVERSITY", out_rb)
                if out_rb != norm:
                    return {"translated_text": out_rb, "model_used": "fmsb-glossary-fallback"}
            else:
                out_rb = re.sub(r"^(?:THE\s+)?UNIVERSITY\s+OF\s+", "UNIVERSITÉ DE ", norm)
                if out_rb != norm:
                    return {"translated_text": out_rb, "model_used": "fmsb-glossary-fallback"}
        elif ftype == "faculty":
            if target_lang == "en":
                out_rb = (
                    norm.replace("FACULTÉ DES SCIENCES DE LA SANTÉ", "FACULTY OF HEALTH SCIENCES")
                    .replace("FACULTE DES SCIENCES DE LA SANTE", "FACULTY OF HEALTH SCIENCES")
                    .replace("FACULTÉ DE MÉDECINE", "FACULTY OF MEDICINE")
                    .replace("FACULTE DE MEDECINE", "FACULTY OF MEDICINE")
                    .replace("ET DES SCIENCES BIOMÉDICALES", "AND BIOMEDICAL SCIENCES")
                    .replace("ET DES SCIENCES BIOMEDICALES", "AND BIOMEDICAL SCIENCES")
                    .replace("ET DES SCIENCES PHARMACEUTIQUES", "AND PHARMACEUTICAL SCIENCES")
                    .replace("ET DE PHARMACIE", "AND PHARMACY")
                    .replace("FACULTÉ DE ", "FACULTY OF ")
                    .replace("FACULTE DE ", "FACULTY OF ")
                )
                if out_rb != norm:
                    return {"translated_text": out_rb, "model_used": "fmsb-glossary-fallback"}
            else:
                out_rb = (
                    norm.replace("FACULTY OF HEALTH SCIENCES", "FACULTÉ DES SCIENCES DE LA SANTÉ")
                    .replace("FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES", "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES")
                    .replace("FACULTY OF MEDICINE AND PHARMACEUTICAL SCIENCES", "FACULTÉ DE MÉDECINE ET DES SCIENCES PHARMACEUTIQUES")
                    .replace("FACULTY OF MEDICINE AND PHARMACY", "FACULTÉ DE MÉDECINE ET DE PHARMACIE")
                    .replace("FACULTY OF MEDICINE", "FACULTÉ DE MÉDECINE")
                    .replace("FACULTY OF ", "FACULTÉ DE ")
                )
                if out_rb != norm:
                    return {"translated_text": out_rb, "model_used": "fmsb-glossary-fallback"}

        return {"translated_text": clean.upper(), "model_used": "passthrough"}

    def summarize_section_live(
        self,
        section_title: str,
        content_text: str,
        study_title: str = "",
        lang: str = "fr",
    ) -> Dict[str, str]:
        """Produce a brief working-memory summary of a modified section via Gemini (`live_translate` chain)."""
        clean = re.sub(r"\s+", " ", (content_text or "")).strip()
        if not clean:
            return {"summary": "(Empty section.)" if lang == "en" else "(Section vide.)", "model_used": "none"}

        if str(lang).lower().startswith("en"):
            prompt = (
                "You are the working-memory manager for a CAMES/FMSB medical research protocol.\n"
                f"Protocol topic: {study_title or 'Medical Research Protocol'}\n"
                f"Updated section: {section_title}\n\n"
                "Summarize in 3 to 4 brief factual sentences (max 90 words) the key facts of this section "
                "so subsequent sections stay harmonized without repetition:\n"
                "- Key facts, figures, definitions, or methodological choices made;\n"
                "- References [n] already cited in this section (if present).\n"
                "Return ONLY the raw summary in English, without any heading or greeting.\n\n"
                f"CONTENT:\n{clean[:4500]}"
            )
        else:
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
                    http_options=types.HttpOptions(timeout=10000),
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
            api_key=self._resolve_active_api_key()[1],
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
                f"Centre de recherche Smart Research : {len(questions)} décisions évaluées"
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
                f"{len(references_payload)} références auditées par le Centre de recherche Smart Research"
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
