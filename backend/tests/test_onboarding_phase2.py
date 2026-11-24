from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
import app.db.database as db_module
from app.db.database import (
    get_store,
    init_db,
    reset_db_state,
    set_firestore_client_override,
)
from app.services.auth_service import (
    build_user_me_out,
    create_user_account,
    normalize_onboarding_state,
)
from app.services.blob_storage import (
    LocalBlobStorage,
    reset_blob_storage_cache,
    set_blob_storage_override,
)
from tests.test_firestore_gcs_backend import FakeFirestoreClient


class TestOnboardingPhase2(unittest.TestCase):
    def test_normalize_onboarding_state_defaults_and_dedup(self) -> None:
        default_state = normalize_onboarding_state(None)
        self.assertEqual(
            default_state,
            {
                "welcome_tour_completed": False,
                "welcome_tour_dismissed": False,
                "seen_features": [],
                "tips_enabled": True,
            },
        )

        # Resilience against noisy / duplicate / invalid inputs
        cleaned = normalize_onboarding_state(
            {
                "welcome_tour_completed": 1,
                "welcome_tour_dismissed": False,
                "seen_features": ["module1", "MODULE1", "", "import_draft", "humanizer"],
                "tips_enabled": False,
            }
        )
        self.assertTrue(cleaned["welcome_tour_completed"])
        self.assertFalse(cleaned["welcome_tour_dismissed"])
        self.assertFalse(cleaned["tips_enabled"])
        self.assertEqual(cleaned["seen_features"], ["module1", "import_draft", "humanizer"])

    def _run_persistence_flow(
        self, backend_mode: str, fake_client=None, tmp_dir: str = ""
    ) -> None:
        get_settings.cache_clear()
        settings = get_settings()
        orig_backend = settings.db_backend
        orig_url = settings.database_url
        orig_engine = db_module.engine
        orig_session_local = db_module.SessionLocal
        orig_db_settings = db_module.settings
        store = None
        try:
            settings.db_backend = backend_mode
            db_module.settings = settings
            if backend_mode == "sql":
                settings.database_url = f"sqlite:///{(Path(tmp_dir) / 'onboard.db').as_posix()}"
                db_module.engine = db_module._build_engine()
                db_module.SessionLocal = sessionmaker(
                    bind=db_module.engine,
                    autoflush=False,
                    autocommit=False,
                    expire_on_commit=False,
                )

            reset_db_state()
            set_firestore_client_override(fake_client)
            set_blob_storage_override(
                LocalBlobStorage(Path(tmp_dir) / "blobs") if tmp_dir else None
            )
            reset_blob_storage_cache()
            init_db(force=True)
            store = get_store()

            suffix = uuid4().hex[:6]
            user = create_user_account(
                store,
                email=f"onboard.{suffix}@test.local",
                full_name="Etudiant Onboarding",
                password="TempPassword123!",
                role="student",
            )

            # Default onboarding state on first login
            me0 = build_user_me_out(store, user)
            self.assertFalse(me0.onboarding_state.welcome_tour_completed)
            self.assertFalse(me0.onboarding_state.welcome_tour_dismissed)
            self.assertEqual(me0.onboarding_state.seen_features, [])
            self.assertTrue(me0.onboarding_state.tips_enabled)

            # Update onboarding state with tour completed, first-click features & tips disabled
            next_state = normalize_onboarding_state(
                {
                    "welcome_tour_completed": True,
                    "welcome_tour_dismissed": False,
                    "seen_features": ["module1", "import_draft"],
                    "tips_enabled": False,
                }
            )
            updated = store.update_user(user.id, onboarding_state=next_state)
            self.assertIsNotNone(updated)
            reloaded = store.get_users_by_ids([user.id]).get(user.id)
            me1 = build_user_me_out(store, reloaded)
            self.assertTrue(me1.onboarding_state.welcome_tour_completed)
            self.assertEqual(
                me1.onboarding_state.seen_features, ["module1", "import_draft"]
            )
            self.assertFalse(me1.onboarding_state.tips_enabled)

            # Reset restores clean initial onboarding state
            reset_state = normalize_onboarding_state({})
            store.update_user(user.id, onboarding_state=reset_state)
            me2 = build_user_me_out(
                store, store.get_users_by_ids([user.id]).get(user.id)
            )
            self.assertFalse(me2.onboarding_state.welcome_tour_completed)
            self.assertEqual(me2.onboarding_state.seen_features, [])
            self.assertTrue(me2.onboarding_state.tips_enabled)
        finally:
            if store is not None:
                store.close()
            if backend_mode == "sql" and db_module.engine is not orig_engine:
                db_module.engine.dispose()
            db_module.engine = orig_engine
            db_module.SessionLocal = orig_session_local
            db_module.settings = orig_db_settings
            settings.db_backend = orig_backend
            settings.database_url = orig_url
            set_firestore_client_override(None)
            set_blob_storage_override(None)
            reset_blob_storage_cache()
            reset_db_state()

    def test_onboarding_persistence_sql(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._run_persistence_flow("sql", fake_client=None, tmp_dir=td)

    def test_onboarding_persistence_firestore(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._run_persistence_flow(
                "firestore", fake_client=FakeFirestoreClient(), tmp_dir=td
            )


if __name__ == "__main__":
    unittest.main()
