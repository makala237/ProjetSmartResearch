import logging
import threading
from typing import Any, Dict, Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
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


def get_db() -> Generator[Session, None, None]:
    """Yield a transactional SQLAlchemy session."""
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _seed_starter_license_plans(db: Session) -> None:
    if db.query(LicensePlanORM).count() > 0:
        return
    plans = [
        LicensePlanORM(
            name="Pack Protocole + Collecte 6 mois",
            description="Accès complet Modules 1, 2 et 4 pendant 1 mois + Masque de Saisie Patients (Module 3) pendant 6 mois.",
            price_fcfa=15000,
            module1_days=30,
            module2_days=30,
            module3_days=180,
            module4_days=30,
            ai_quota_included=150,
            is_active=True,
        ),
        LicensePlanORM(
            name="Pack Soutenance Express",
            description="Accès rédaction/édition 15 jours + Module 4 PowerPoint CAMES 30 jours.",
            price_fcfa=7500,
            module1_days=15,
            module2_days=15,
            module3_days=0,
            module4_days=30,
            ai_quota_included=60,
            is_active=True,
        ),
    ]
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


def init_db(force: bool = False) -> None:
    """Create schema tables if missing, bootstrap admin/plans, and migrate legacy JSONs once."""
    global _initialized
    if _initialized and not force:
        return

    with _init_lock:
        if _initialized and not force:
            return

        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            admin = db.query(UserORM).filter(UserORM.role == "admin").first()
            if admin is None:
                admin = UserORM(
                    email=settings.bootstrap_admin_email.strip().lower(),
                    full_name=settings.bootstrap_admin_name.strip() or "Administrateur Principal",
                    role="admin",
                    password_hash=hash_password(settings.bootstrap_admin_password),
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
