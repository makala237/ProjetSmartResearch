from functools import lru_cache
from pathlib import Path
import secrets
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

    app_name: str = "Smart Research"
    app_version: str = "1.0.0"

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

    # SaaS Database, Authentication & Bootstrap Admin
    database_url: str = ""
    jwt_secret_key: str = ""
    jwt_access_token_expire_minutes: int = 120
    jwt_refresh_token_expire_days: int = 14
    bootstrap_admin_email: str = "admin@smartresearch.local"
    bootstrap_admin_password: str = "AdminFMSB2026!"
    bootstrap_admin_name: str = "Administrateur Principal"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8001,http://127.0.0.1:8001"

    storage_dir: Path = BASE_DIR / "storage"
    templates_dir: Path = BASE_DIR / "templates"

    # Phase 5: Cloud Run Render Engine & Object Storage (GCS / Cloudflare R2 / S3)
    render_engine: str = "auto"  # auto | com | libreoffice | cloud_api
    cloud_render_api_url: str = ""
    cloud_render_api_key: str = ""
    storage_backend: str = "local"  # local | gcs | s3
    cloud_storage_bucket: str = ""
    cloud_storage_prefix: str = "smartresearch"
    cloud_storage_endpoint_url: str = ""
    cloud_storage_access_key: str = ""
    cloud_storage_secret_key: str = ""
    cloud_storage_region: str = "auto"

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

    def effective_database_url(self) -> str:
        """Return normalized PostgreSQL URL or fallback local SQLite URL."""
        raw = (self.database_url or "").strip()
        if raw:
            if raw.startswith("postgres://"):
                return "postgresql+psycopg://" + raw[len("postgres://") :]
            if raw.startswith("postgresql://") and "+psycopg" not in raw:
                return "postgresql+psycopg://" + raw[len("postgresql://") :]
            return raw
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        sqlite_path = (self.storage_dir / "smartresearch.db").resolve().as_posix()
        return f"sqlite:///{sqlite_path}"

    def effective_jwt_secret(self) -> str:
        """Return configured JWT secret or lazily persist a local random 64-char secret."""
        configured = (self.jwt_secret_key or "").strip()
        if configured:
            return configured
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        secret_file = self.storage_dir / ".jwt_secret"
        if secret_file.exists():
            existing = secret_file.read_text(encoding="utf-8").strip()
            if len(existing) >= 32:
                return existing
        generated = secrets.token_hex(32)
        secret_file.write_text(generated, encoding="utf-8")
        return generated

    def cors_origins_list(self) -> List[str]:
        """Return allowed CORS origins parsed from comma-separated setting."""
        return [o.strip() for o in (self.cors_origins or "").split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.templates_dir.mkdir(parents=True, exist_ok=True)
    return settings
