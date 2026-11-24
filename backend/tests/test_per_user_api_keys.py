from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.admin_routes import _compute_api_keys_stats
from app.core.config import get_settings
import app.db.database as db_module
from app.db.database import get_store, init_db, reset_db_state, set_firestore_client_override
from sqlalchemy.orm import sessionmaker
from app.services.auth_service import create_user_account
from app.services.blob_storage import LocalBlobStorage, reset_blob_storage_cache, set_blob_storage_override
from app.services.gemini_client import GeminiRouter, reset_ai_routing_policy
from app.services.licensing_service import (
    assert_ai_quota_available,
    record_usage_and_debit,
    resolve_user_gemini_api_key,
)
from tests.test_firestore_gcs_backend import FakeFirestoreClient


class TestPerUserApiKeys(unittest.TestCase):
    def _run_flow(self, backend_mode: str, fake_client=None, tmp_dir: str = ""):
        get_settings.cache_clear()
        settings = get_settings()
        orig_backend = settings.db_backend
        orig_url = settings.database_url
        orig_default_key = settings.gemini_api_key
        orig_engine = db_module.engine
        orig_session_local = db_module.SessionLocal
        orig_db_settings = db_module.settings
        store = None
        try:
            settings.db_backend = backend_mode
            settings.gemini_api_key = "DEFAULT_SYSTEM_KEY_123456"
            db_module.settings = settings
            if backend_mode == "sql":
                settings.database_url = f"sqlite:///{(Path(tmp_dir) / 'keys_test.db').as_posix()}"
                db_module.engine = db_module._build_engine()
                db_module.SessionLocal = sessionmaker(
                    bind=db_module.engine,
                    autoflush=False,
                    autocommit=False,
                    expire_on_commit=False,
                )

            reset_db_state()
            set_firestore_client_override(fake_client)
            set_blob_storage_override(LocalBlobStorage(Path(tmp_dir) / "blobs") if tmp_dir else None)
            reset_blob_storage_cache()
            init_db(force=True)
            store = get_store()

            suffix = uuid4().hex[:6]
            # 1. Create custom named key
            key_a = store.create_gemini_api_key(
                name="Clé Promo 2026",
                owner_label="Dr. Test",
                api_key="CUSTOM_KEY_ALPHA_999999",
                is_active=True,
            )

            # 2. Create 2 users: u_default on default key, u_custom on key_a
            u_default = create_user_account(
                store,
                email=f"def.{suffix}@test.local",
                full_name="User Default",
                password="TempPassword123!",
                role="admin",
            )
            u_custom = create_user_account(
                store,
                email=f"cus.{suffix}@test.local",
                full_name="User Custom",
                password="TempPassword123!",
                role="admin",
                gemini_api_key_id=key_a.id,
            )

            # 3. Verify key resolution & GeminiRouter routing
            self.assertEqual(resolve_user_gemini_api_key(store, u_default), ("default", "DEFAULT_SYSTEM_KEY_123456"))
            self.assertEqual(resolve_user_gemini_api_key(store, u_custom), (key_a.id, "CUSTOM_KEY_ALPHA_999999"))

            router = GeminiRouter()
            assert_ai_quota_available(store, u_custom, module_key="module1", action="draft_section")
            self.assertEqual(router._resolve_active_api_key(), (key_a.id, "CUSTOM_KEY_ALPHA_999999"))
            # Cooldown isolation: mark 3.5-flash-lite unresponsive on key_a only
            router._mark_model_unresponsive("gemini-3.5-flash-lite")
            self.assertTrue(router._is_model_in_cooldown("gemini-3.5-flash-lite"))
            record_usage_and_debit(
                store,
                u_custom,
                module_key="module1",
                action="draft_section",
                credits_cost=2,
                model_used="gemini-3.5-flash-lite",
                tokens_used=12_500,
            )

            # Switch to u_default -> cooldown from key_a MUST NOT affect default key
            assert_ai_quota_available(store, u_default, module_key="module1", action="draft_section")
            self.assertEqual(router._resolve_active_api_key(), ("default", "DEFAULT_SYSTEM_KEY_123456"))
            self.assertFalse(router._is_model_in_cooldown("gemini-3.5-flash-lite"))
            record_usage_and_debit(
                store,
                u_default,
                module_key="module1",
                action="draft_section",
                credits_cost=2,
                model_used="gemini-3.5-flash-lite",
                tokens_used=4_000,
            )

            # 4. Verify per-key stats & per-user consumption breakdown
            stats = _compute_api_keys_stats(store)
            by_id = {s.id: s for s in stats}
            self.assertIn("default", by_id)
            self.assertIn(key_a.id, by_id)

            st_a = by_id[key_a.id]
            self.assertEqual(st_a.assigned_users_count, 1)
            self.assertEqual(st_a.total_calls, 1)
            self.assertEqual(st_a.total_tokens, 12_500)
            self.assertEqual(st_a.users[0].user_id, u_custom.id)
            self.assertEqual(st_a.users[0].tokens_total, 12_500)

            st_def = by_id["default"]
            self.assertGreaterEqual(st_def.assigned_users_count, 1)
            self.assertEqual(st_def.total_tokens, 4_000)

            # 5. Delete custom key -> u_custom seamlessly falls back to default
            self.assertTrue(store.delete_gemini_api_key(key_a.id))
            u_custom_after = store.get_user(u_custom.id)
            self.assertIsNone(u_custom_after.gemini_api_key_id)
            self.assertEqual(
                resolve_user_gemini_api_key(store, u_custom_after),
                ("default", "DEFAULT_SYSTEM_KEY_123456"),
            )
        finally:
            reset_ai_routing_policy()
            if store is not None:
                store.close()
            if backend_mode == "sql" and db_module.engine is not orig_engine:
                db_module.engine.dispose()
            db_module.engine = orig_engine
            db_module.SessionLocal = orig_session_local
            db_module.settings = orig_db_settings
            settings.db_backend = orig_backend
            settings.database_url = orig_url
            settings.gemini_api_key = orig_default_key
            set_firestore_client_override(None)
            set_blob_storage_override(None)
            reset_blob_storage_cache()
            reset_db_state()

    def test_per_user_api_keys_sql(self):
        with tempfile.TemporaryDirectory() as td:
            self._run_flow("sql", fake_client=None, tmp_dir=td)

    def test_per_user_api_keys_firestore(self):
        with tempfile.TemporaryDirectory() as td:
            self._run_flow("firestore", fake_client=FakeFirestoreClient(), tmp_dir=td)


if __name__ == "__main__":
    unittest.main()
