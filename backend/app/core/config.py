from functools import lru_cache
from pathlib import Path
from typing import Dict, List

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = BASE_DIR.parent


class Settings(BaseSettings):
    """Application settings loaded from backend/.env."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SmartResearch FMSB - Assistant Protocole"
    app_version: str = "0.1.0"

    gemini_api_key: str = ""

    gemini_models_research: str = "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3.5-flash"
    gemini_models_segmentation: str = "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3.6-flash"
    gemini_models_drafting: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash,gemini-3.6-flash,gemini-3.8-flash"
    gemini_models_audit: str = "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3.5-flash"
    gemini_models_live_translate: str = "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3.8-flash"
    gemini_models_layout_decision: str = "gemini-decision-1,gemini-3.5-flash-lite,gemini-3.8-flash,gemini-3.7-flash"

    ncbi_api_key: str = ""
    openalex_api_key: str = ""
    openalex_email: str = ""

    storage_dir: Path = BASE_DIR / "storage"
    templates_dir: Path = BASE_DIR / "templates"

    def model_chains(self) -> Dict[str, List[str]]:
        """Return ordered model fallback chains per pipeline role."""
        def parse(raw: str) -> List[str]:
            return [m.strip() for m in raw.split(",") if m.strip()]

        return {
            "research": parse(self.gemini_models_research),
            "segmentation": parse(self.gemini_models_segmentation),
            "drafting": parse(self.gemini_models_drafting),
            "audit": parse(self.gemini_models_audit),
            "live_translate": parse(self.gemini_models_live_translate),
            "layout_decision": parse(self.gemini_models_layout_decision),
        }


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.templates_dir.mkdir(parents=True, exist_ok=True)
    return settings
