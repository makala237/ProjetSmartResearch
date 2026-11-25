"""Curated Gemini model catalog, Free-Tier reference limits, and per-user routing presets."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# Reference Free-Tier limits from Google AI Studio (current standard Free-Tier ceilings:
# 20 RPD / 250,000 TPM per model; actual per-project limits can be verified at
# https://aistudio.google.com/rate-limit).
GEMINI_MODEL_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "gemini-3.8-flash",
        "label": "Gemini 3.8 Flash",
        "tier": "vip",
        "in_use": False,
        "free_tier_rpm": 5,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 20,
        "description": "Modèle Flash le plus intelligent — idéal en Primaire VIP (ex. 5–10 req/j).",
    },
    {
        "id": "gemini-3.7-flash",
        "label": "Gemini 3.7 Flash",
        "tier": "vip",
        "in_use": False,
        "free_tier_rpm": 5,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 20,
        "description": "Flash haute qualité (génération 3.7) — excellent Primaire ou Secondaire VIP.",
    },
    {
        "id": "gemini-3.6-flash",
        "label": "Gemini 3.6 Flash",
        "tier": "vip",
        "in_use": False,
        "free_tier_rpm": 5,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 20,
        "description": "Flash rapide et équilibré (génération 3.6) — bon compromis qualité/vitesse.",
    },
    {
        "id": "gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "tier": "standard",
        "in_use": True,
        "free_tier_rpm": 5,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 20,
        "description": "Actuellement utilisé — backup haute qualité dans la chaîne système.",
    },
    {
        "id": "gemini-3.5-flash-lite",
        "label": "Gemini 3.5 Flash-Lite",
        "tier": "lite",
        "in_use": True,
        "free_tier_rpm": 15,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 20,
        "description": "Actuellement utilisé — rapide (chaîne système par défaut).",
    },
    {
        "id": "gemini-3.1-flash-lite",
        "label": "Gemini 3.1 Flash-Lite",
        "tier": "lite",
        "in_use": True,
        "free_tier_rpm": 15,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 20,
        "description": "Actuellement utilisé — relais rapide Flash-Lite.",
    },
    {
        "id": "gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "tier": "vip",
        "in_use": False,
        "free_tier_rpm": 5,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 20,
        "description": "Raisonnement approfondi — idéal pour les 5 premières requêtes VIP.",
    },
    {
        "id": "gemini-2.5-flash",
        "label": "Gemini 2.5 Flash",
        "tier": "standard",
        "in_use": False,
        "free_tier_rpm": 5,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 20,
        "description": "Backup stable de la famille 2.5 lorsque la famille 3.x est saturée.",
    },
    {
        "id": "gemini-2.5-flash-lite",
        "label": "Gemini 2.5 Flash-Lite",
        "tier": "lite",
        "in_use": False,
        "free_tier_rpm": 15,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 20,
        "description": "Filet de sécurité 2.5 Flash-Lite.",
    },
    {
        "id": "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "label": "NVIDIA Nemotron 3.5 Super VL 120B (Direct NIM)",
        "tier": "vip",
        "in_use": True,
        "free_tier_rpm": 40,
        "free_tier_tpm": 500_000,
        "free_tier_rpd": 5000,
        "description": "4e secours global direct NVIDIA NIM (sans limite OpenRouter 50/jour) — 120B MoE Vision-Language.",
    },
    {
        "id": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "label": "NVIDIA Nemotron 3.5 Lightning 30B (Direct NIM)",
        "tier": "lite",
        "in_use": False,
        "free_tier_rpm": 40,
        "free_tier_tpm": 500_000,
        "free_tier_rpd": 5000,
        "description": "Direct NVIDIA NIM — variante ultra-rapide 30B MoE pour rédaction et reformulation.",
    },
    {
        "id": "nvidia/nemotron-3-ultra-550b-a55b",
        "label": "NVIDIA Nemotron 3 Ultra 550B (Direct NIM)",
        "tier": "vip",
        "in_use": False,
        "free_tier_rpm": 40,
        "free_tier_tpm": 500_000,
        "free_tier_rpd": 5000,
        "description": "Direct NVIDIA NIM — 550B MoE haute capacité pour raisonnement méthodologique complexe.",
    },
    {
        "id": "nvidia/nemotron-3.5-super-vl:free",
        "label": "NVIDIA Nemotron 3.5 Super VL (OpenRouter Free)",
        "tier": "vip",
        "in_use": True,
        "free_tier_rpm": 20,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 50,
        "description": "5e secours global via OpenRouter Free — 120B MoE Vision-Language.",
    },
    {
        "id": "nvidia/nemotron-3.5-lightning:free",
        "label": "NVIDIA Nemotron 3.5 Lightning (OpenRouter Free)",
        "tier": "lite",
        "in_use": False,
        "free_tier_rpm": 20,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 50,
        "description": "OpenRouter Free — variante rapide Nemotron 3.5 pour rédaction et reformulation.",
    },
    {
        "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "label": "NVIDIA Nemotron 3 Ultra 550B (OpenRouter Free)",
        "tier": "vip",
        "in_use": False,
        "free_tier_rpm": 20,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 50,
        "description": "OpenRouter Free — 550B MoE haute capacité pour raisonnement méthodologique complexe.",
    },
]

GEMINI_MODEL_CATALOG_BY_ID: Dict[str, Dict[str, Any]] = {
    item["id"]: item for item in GEMINI_MODEL_CATALOG
}

# Dedicated Live API (bidiGenerateContent) models for real-time voice/micro & video mode
GEMINI_LIVE_MODEL_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "gemini-3.8-live",
        "label": "Gemini 3.8 Live (Audio & Vidéo temps réel)",
        "tier": "vip",
        "in_use": True,
        "free_tier_rpm": 5,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 50,
        "description": "Recommandé pour le micro de l'Assistant Smart Research : voix fluide, rapide et compréhension contextuelle avancée.",
    },
    {
        "id": "gemini-3.8-live-extended-thinking",
        "label": "Gemini 3.8 Live Extended Thinking",
        "tier": "vip",
        "in_use": False,
        "free_tier_rpm": 5,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 25,
        "description": "Mode vocal avec réflexion approfondie — utile pour les débats méthodologiques complexes (latence légèrement supérieure).",
    },
    {
        "id": "gemini-2.5-flash-native-audio-latest",
        "label": "Gemini 2.5 Flash Native Audio",
        "tier": "standard",
        "in_use": False,
        "free_tier_rpm": 10,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 50,
        "description": "Relais audio natif très stable à faible latence.",
    },
    {
        "id": "gemini-3.1-flash-live-preview",
        "label": "Gemini 3.1 Flash Live Preview",
        "tier": "lite",
        "in_use": False,
        "free_tier_rpm": 10,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 50,
        "description": "Option légère pour le mode vocal temps réel.",
    },
]

GEMINI_LIVE_MODEL_CATALOG_BY_ID: Dict[str, Dict[str, Any]] = {
    item["id"]: item for item in GEMINI_LIVE_MODEL_CATALOG
}

MODEL_ROUTING_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "vip_38_10rpd",
        "label": "VIP 3.8 Flash (10 req/j → 3.6 → Lite)",
        "description": "10 premières requêtes/jour sur Gemini 3.8 Flash, puis 10 sur 3.6 Flash, puis Flash-Lite.",
        "config": {
            "enabled": True,
            "models": [
                {"model": "gemini-3.8-flash", "max_rpd": 10, "max_tpm": 250_000},
                {"model": "gemini-3.6-flash", "max_rpd": 10, "max_tpm": 250_000},
                {"model": "gemini-3.5-flash-lite", "max_rpd": 20, "max_tpm": 250_000},
                {"model": "gemini-3.1-flash-lite", "max_rpd": 20, "max_tpm": 250_000},
                {"model": "gemini-3.5-flash", "max_rpd": 20, "max_tpm": 250_000},
            ],
        },
    },
    {
        "id": "vip_36_10rpd",
        "label": "VIP 3.6 Flash (10 req/j → Lite)",
        "description": "10 premières requêtes/jour sur Gemini 3.6 Flash, puis relais Flash-Lite.",
        "config": {
            "enabled": True,
            "models": [
                {"model": "gemini-3.6-flash", "max_rpd": 10, "max_tpm": 250_000},
                {"model": "gemini-3.5-flash-lite", "max_rpd": 20, "max_tpm": 250_000},
                {"model": "gemini-3.1-flash-lite", "max_rpd": 20, "max_tpm": 250_000},
            ],
        },
    },
    {
        "id": "vip_25pro_5rpd",
        "label": "VIP 2.5 Pro (5 req/j → 3.8 → Lite)",
        "description": "5 requêtes/jour sur 2.5 Pro, puis 10 sur 3.8 Flash, puis relais Flash-Lite.",
        "config": {
            "enabled": True,
            "models": [
                {"model": "gemini-2.5-pro", "max_rpd": 5, "max_tpm": 250_000},
                {"model": "gemini-3.8-flash", "max_rpd": 10, "max_tpm": 250_000},
                {"model": "gemini-3.5-flash-lite", "max_rpd": 20, "max_tpm": 250_000},
                {"model": "gemini-3.1-flash-lite", "max_rpd": 20, "max_tpm": 250_000},
            ],
        },
    },
    {
        "id": "resilient_6",
        "label": "Résilient 6 modèles (3.x + 2.5)",
        "description": "Cumule les quotas Free Tier (20 req/j par modèle) sur 6 modèles en cascade.",
        "config": {
            "enabled": True,
            "models": [
                {"model": "gemini-3.5-flash-lite", "max_rpd": 20, "max_tpm": 250_000},
                {"model": "gemini-3.1-flash-lite", "max_rpd": 20, "max_tpm": 250_000},
                {"model": "gemini-3.5-flash", "max_rpd": 20, "max_tpm": 250_000},
                {"model": "gemini-3.6-flash", "max_rpd": 20, "max_tpm": 250_000},
                {"model": "gemini-2.5-flash", "max_rpd": 20, "max_tpm": 250_000},
                {"model": "gemini-2.5-flash-lite", "max_rpd": 20, "max_tpm": 250_000},
            ],
        },
    },
]

FREE_TIER_REFERENCE_NOTE = (
    "Plafonds de référence Free Tier Gemini : 20 RPD · 250 000 TPM par modèle "
    "(vérifiez vos quotas exacts sur aistudio.google.com/rate-limit). "
    "Lorsqu'un modèle atteint son plafond RPD ou TPM pour l'utilisateur, le routeur bascule "
    "automatiquement sur le modèle suivant de sa chaîne."
)


def _clean_model_entries(raw_models: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_models, list) or not raw_models:
        return []

    cleaned: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_models:
        if hasattr(entry, "model_dump"):
            entry = entry.model_dump()
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("model") or "").strip()
        if not model_id:
            continue
        key = model_id.lower()
        if key in seen:
            continue
        seen.add(key)

        raw_rpd = entry.get("max_rpd")
        max_rpd: Optional[int] = None
        if raw_rpd is not None and str(raw_rpd).strip() != "":
            try:
                max_rpd = max(0, int(raw_rpd))
            except (TypeError, ValueError):
                max_rpd = None

        raw_tpm = entry.get("max_tpm")
        max_tpm: Optional[int] = None
        if raw_tpm is not None and str(raw_tpm).strip() != "":
            try:
                max_tpm = max(0, int(raw_tpm))
            except (TypeError, ValueError):
                max_tpm = None

        cleaned.append(
            {
                "model": model_id,
                "max_rpd": max_rpd,
                "max_tpm": max_tpm,
            }
        )
    return cleaned


def normalize_model_routing_config(raw: Any) -> Optional[Dict[str, Any]]:
    """Validate and normalize a per-user model routing config (main + optional Assistant text/voice chains).

    Returns None when disabled or all chains are empty (meaning: use system default chain).
    """
    if raw is None:
        return None
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        return None
    if not bool(raw.get("enabled")):
        return None

    cleaned = _clean_model_entries(raw.get("models"))
    copilot_text = _clean_model_entries(raw.get("copilot_text_models"))
    copilot_voice = _clean_model_entries(raw.get("copilot_voice_models"))
    supervisor_media = _clean_model_entries(raw.get("supervisor_media_models"))

    if not cleaned and not copilot_text and not copilot_voice and not supervisor_media:
        return None

    out: Dict[str, Any] = {
        "enabled": True,
        "models": cleaned,
    }
    if copilot_text:
        out["copilot_text_models"] = copilot_text
    if copilot_voice:
        out["copilot_voice_models"] = copilot_voice
    if supervisor_media:
        out["supervisor_media_models"] = supervisor_media
    return out
