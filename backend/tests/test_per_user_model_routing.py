from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.core.model_catalog import GEMINI_MODEL_CATALOG, normalize_model_routing_config
import app.db.database as db_module
from app.db.database import get_store, init_db, reset_db_state, set_firestore_client_override
from app.services.auth_service import create_user_account
from app.services.blob_storage import LocalBlobStorage, reset_blob_storage_cache, set_blob_storage_override
from app.services.gemini_client import GeminiRouter, reset_ai_routing_policy
from app.services.licensing_service import assert_ai_quota_available, record_usage_and_debit
from tests.test_firestore_gcs_backend import FakeFirestoreClient


class TestPerUserModelRouting(unittest.TestCase):
    def _run_flow(self, backend_mode: str, fake_client=None, tmp_dir: str = ""):
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
                settings.database_url = f"sqlite:///{(Path(tmp_dir) / 'routing_test.db').as_posix()}"
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
            vip_cfg = normalize_model_routing_config(
                {
                    "enabled": True,
                    "models": [
                        {"model": "gemini-3.8-flash", "max_rpd": 2, "max_tpm": 20_000},
                        {"model": "gemini-3.5-flash-lite", "max_rpd": 5, "max_tpm": 10_000},
                        {"model": "gemini-3.1-flash-lite", "max_rpd": 1, "max_tpm": 50_000},
                    ],
                }
            )
            self.assertIsNotNone(vip_cfg)

            u_vip = create_user_account(
                store,
                email=f"vip.{suffix}@test.local",
                full_name="VIP Student",
                password="TempPassword123!",
                role="admin",
                model_routing_config=vip_cfg,
            )
            u_std = create_user_account(
                store,
                email=f"std.{suffix}@test.local",
                full_name="Standard Student",
                password="TempPassword123!",
                role="admin",
            )

            router = GeminiRouter()

            # 1. Standard user uses default system chain
            assert_ai_quota_available(store, u_std, module_key="module1", action="draft_section")
            std_chain, _ = router._resolve_candidates("drafting")
            self.assertEqual(std_chain[0], "gemini-3.5-flash-lite")

            # 2. VIP user starts with gemini-3.8-flash as Primary
            assert_ai_quota_available(store, u_vip, module_key="module1", action="draft_section")
            vip_chain, cap_active = router._resolve_candidates("drafting")
            self.assertEqual(
                vip_chain,
                ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
            )
            self.assertFalse(cap_active)

            # Record 2 calls on gemini-3.8-flash (reaches max_rpd=2)
            for _ in range(2):
                record_usage_and_debit(
                    store,
                    u_vip,
                    module_key="module1",
                    action="draft_section",
                    credits_cost=1,
                    model_used="gemini-3.8-flash",
                    tokens_used=2_000,
                )

            # 3. Next call automatically cascades to Secondary (gemini-3.5-flash-lite)
            assert_ai_quota_available(store, u_vip, module_key="module1", action="draft_section")
            vip_chain_2, cap_active_2 = router._resolve_candidates("drafting")
            self.assertEqual(vip_chain_2, ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"])
            self.assertTrue(cap_active_2)

            # Record 12_000 tokens on gemini-3.5-flash-lite in the last 60s (exceeds max_tpm=10_000)
            record_usage_and_debit(
                store,
                u_vip,
                module_key="module1",
                action="draft_section",
                credits_cost=1,
                model_used="gemini-3.5-flash-lite",
                tokens_used=12_000,
            )

            # 4. Next call automatically cascades to 3rd Backup (gemini-3.1-flash-lite)
            assert_ai_quota_available(store, u_vip, module_key="module1", action="draft_section")
            vip_chain_3, _ = router._resolve_candidates("drafting")
            self.assertEqual(vip_chain_3, ["gemini-3.1-flash-lite"])

            # Exhaust 3rd Backup RPD (max_rpd=1) -> all models exhausted -> HTTP 429 TPM
            record_usage_and_debit(
                store,
                u_vip,
                module_key="module1",
                action="draft_section",
                credits_cost=1,
                model_used="gemini-3.1-flash-lite",
                tokens_used=1_000,
            )
            with self.assertRaises(HTTPException) as ctx:
                assert_ai_quota_available(store, u_vip, module_key="module1", action="draft_section")
            self.assertEqual(ctx.exception.status_code, 429)
            self.assertEqual(ctx.exception.detail["code"], "AI_MODEL_CHAIN_TPM_EXHAUSTED")

            # 5. Disable custom chain -> user returns to system default seamlessly
            u_vip_reset = store.update_user(u_vip.id, model_routing_config=None)
            assert_ai_quota_available(store, u_vip_reset, module_key="module1", action="draft_section")
            reset_chain, _ = router._resolve_candidates("drafting")
            self.assertEqual(reset_chain[0], "gemini-3.5-flash-lite")

            # Verify catalog includes 3.8-flash and 3.6-flash
            ids = {m["id"] for m in GEMINI_MODEL_CATALOG}
            self.assertIn("gemini-3.8-flash", ids)
            self.assertIn("gemini-3.6-flash", ids)
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
            set_firestore_client_override(None)
            set_blob_storage_override(None)
            reset_blob_storage_cache()
            reset_db_state()

    def test_per_user_model_routing_sql(self):
        with tempfile.TemporaryDirectory() as td:
            self._run_flow("sql", fake_client=None, tmp_dir=td)

    def test_per_user_model_routing_firestore(self):
        with tempfile.TemporaryDirectory() as td:
            self._run_flow("firestore", fake_client=FakeFirestoreClient(), tmp_dir=td)


if __name__ == "__main__":
    unittest.main()
