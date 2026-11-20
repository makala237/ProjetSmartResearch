import logging
import threading
from typing import Any, Dict, Generator, List, Optional

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.datastore import BaseDataStore, FirestoreDataStore, SQLDataStore
from app.db.models import (
    Base,
    LicensePlanORM,
    PresentationORM,
    ProjectORM,
    UserAIQuotaORM,
    UserORM,
    utcnow,
)

logger = logging.getLogger(__name__)
settings = get_settings()

STARTER_LICENSE_PLANS: List[Dict[str, Any]] = [
    {
        "name": "Pack Protocole + Collecte 6 mois",
        "description": "Accès complet Modules 1, 2 et 4 pendant 1 mois + Masque de Saisie Patients (Module 3) pendant 6 mois.",
        "price_fcfa": 15000,
        "module1_days": 30,
        "module2_days": 30,
        "module3_days": 180,
        "module4_days": 30,
        "ai_quota_included": 150,
        "is_active": True,
    },
    {
        "name": "Pack Soutenance Express",
        "description": "Accès rédaction/édition 15 jours + Module 4 PowerPoint CAMES 30 jours.",
        "price_fcfa": 7500,
        "module1_days": 15,
        "module2_days": 15,
        "module3_days": 0,
        "module4_days": 30,
        "ai_quota_included": 60,
        "is_active": True,
    },
]


def _build_engine():
    db_url = settings.effective_database_url()
    is_sqlite = db_url.startswith("sqlite")
    connect_args: Dict[str, Any] = {"check_same_thread": False} if is_sqlite else {}
    engine_kwargs: Dict[str, Any] = {"connect_args": connect_args}
    if not is_sqlite:
        engine_kwargs["pool_pre_ping"] = True

    eng = create_engine(db_url, **engine_kwargs)
    if is_sqlite:
        @event.listens_for(eng, "connect")
        def _enable_sqlite_fk(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return eng


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

_init_lock = threading.Lock()
_initialized = False
_FIRESTORE_CLIENT_OVERRIDE: Optional[Any] = None


def is_firestore_backend() -> bool:
    """Return True when DB_BACKEND is configured for Cloud Firestore."""
    cfg = get_settings()
    return (getattr(cfg, "db_backend", "sql") or "sql").strip().lower() == "firestore"


def get_database_dialect() -> str:
    """Return active database dialect name ('sqlite', 'postgresql', or 'firestore')."""
    if is_firestore_backend():
        return "firestore"
    return engine.dialect.name


def set_firestore_client_override(client: Optional[Any]) -> None:
    """Set or clear a custom Firestore Client instance (for automated tests)."""
    global _FIRESTORE_CLIENT_OVERRIDE
    _FIRESTORE_CLIENT_OVERRIDE = client


def reset_db_state() -> None:
    """Reset initialization flag and Firestore test client override."""
    global _initialized, _FIRESTORE_CLIENT_OVERRIDE
    _initialized = False
    _FIRESTORE_CLIENT_OVERRIDE = None


def get_store() -> BaseDataStore:
    """Return a DataStore bound to the configured backend (Firestore or SQLAlchemy)."""
    if is_firestore_backend():
        cfg = get_settings()
        return FirestoreDataStore(
            client=_FIRESTORE_CLIENT_OVERRIDE,
            project_id=(cfg.firestore_project_id or "").strip() or None,
            database=(cfg.firestore_database or "").strip() or None,
        )
    return SQLDataStore(SessionLocal(), owns_session=True)


def get_db() -> Generator[BaseDataStore, None, None]:
    """Yield a dual-mode DataStore session."""
    init_db()
    store = get_store()
    try:
        yield store
    finally:
        store.close()


def _seed_starter_license_plans(db: Session) -> None:
    if db.query(LicensePlanORM).count() > 0:
        return
    plans = [LicensePlanORM(**p) for p in STARTER_LICENSE_PLANS]
    db.add_all(plans)
    db.flush()


def _migrate_legacy_json_to_db(db: Session, default_owner_id: str) -> None:
    """Idempotently import existing local JSON protocol projects & presentations into DB."""
    from app.schemas.presentation import PresentationDeck
    from app.schemas.protocol import ProtocolProject

    projects_dir = settings.storage_dir / "projects"
    presentations_dir = settings.storage_dir / "presentations"

    if projects_dir.exists():
        for json_path in sorted(projects_dir.glob("*.json")):
            try:
                proj = ProtocolProject.model_validate_json(json_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Skipping invalid legacy project JSON %s: %s", json_path.name, exc)
                continue

            existing = db.get(ProjectORM, proj.project_id)
            if existing is None:
                owner_id = proj.owner_user_id or default_owner_id
                proj.owner_user_id = owner_id
                validated_count = sum(
                    1
                    for sec in proj.sections.values()
                    if getattr(sec.status, "value", str(sec.status)) == "validated"
                )
                row = ProjectORM(
                    project_id=proj.project_id,
                    owner_user_id=owner_id,
                    name=proj.name,
                    locked_title=proj.locked_title or (proj.cover.title or proj.name or ""),
                    locked_author_name=proj.locked_author_name or (proj.cover.author_name or ""),
                    locked_matricule=proj.locked_matricule or (proj.cover.matricule or ""),
                    topic_locked=bool(proj.topic_locked),
                    data_json=proj.model_dump(mode="json"),
                    sections_validated_count=validated_count,
                    patients_count=len(proj.patient_records),
                    created_at=proj.created_at or utcnow(),
                    updated_at=proj.updated_at or utcnow(),
                )
                db.add(row)
                db.flush()

    if presentations_dir.exists():
        for pres_path in sorted(presentations_dir.glob("*.json")):
            try:
                deck = PresentationDeck.model_validate_json(pres_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Skipping invalid legacy presentation JSON %s: %s", pres_path.name, exc)
                continue

            proj_row = db.get(ProjectORM, deck.project_id)
            if proj_row is None:
                continue
            existing_pres = db.get(PresentationORM, deck.project_id)
            if existing_pres is None:
                db.add(
                    PresentationORM(
                        project_id=deck.project_id,
                        owner_user_id=proj_row.owner_user_id,
                        version=deck.version or "3.1.0",
                        slides_count=len(deck.slides),
                        data_json=deck.model_dump(mode="json"),
                        updated_at=utcnow(),
                    )
                )


def _ensure_schema_columns(eng) -> None:
    """Idempotently add newly introduced columns on existing SQLite / PostgreSQL tables."""
    try:
        insp = inspect(eng)
        if "user_ai_quotas" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("user_ai_quotas")}
            if "daily_38_limit" not in cols:
                with eng.begin() as conn:
                    conn.execute(text("ALTER TABLE user_ai_quotas ADD COLUMN daily_38_limit INTEGER"))
    except Exception as exc:
        logger.warning("Schema column check failed: %s", exc)


def init_db(force: bool = False) -> None:
    """Create schema/bootstrap admin & plans cleanly for either Firestore or SQL mode."""
    global _initialized
    if _initialized and not force:
        return

    with _init_lock:
        if _initialized and not force:
            return

        cfg = get_settings()
        if is_firestore_backend():
            store = get_store()
            try:
                store.bootstrap(
                    admin_email=cfg.bootstrap_admin_email.strip().lower(),
                    admin_password_hash=hash_password(cfg.bootstrap_admin_password),
                    admin_name=cfg.bootstrap_admin_name.strip() or "Administrateur Principal",
                    starter_plans=STARTER_LICENSE_PLANS,
                )
            finally:
                store.close()
            _initialized = True
            return

        Base.metadata.create_all(bind=engine)
        _ensure_schema_columns(engine)
        db = SessionLocal()
        try:
            admin = db.query(UserORM).filter(UserORM.role == "admin").first()
            if admin is None:
                admin = UserORM(
                    email=cfg.bootstrap_admin_email.strip().lower(),
                    full_name=cfg.bootstrap_admin_name.strip() or "Administrateur Principal",
                    role="admin",
                    password_hash=hash_password(cfg.bootstrap_admin_password),
                    is_active=True,
                    must_change_password=False,
                )
                db.add(admin)
                db.flush()
                db.add(
                    UserAIQuotaORM(
                        user_id=admin.id,
                        credits_total=100000,
                        credits_used=0,
                    )
                )
                db.flush()

            _seed_starter_license_plans(db)
            _migrate_legacy_json_to_db(db, admin.id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        _initialized = True
