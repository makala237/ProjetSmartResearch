from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings, get_settings
from app.db.database import get_store, init_db, reset_db_state, set_firestore_client_override
from app.services.auth_service import create_user_account
from app.services.blob_storage import LocalBlobStorage, reset_blob_storage_cache, set_blob_storage_override
from app.services.gemini_client import (
    GeminiRouter,
    consume_request_tokens_used,
    reset_request_tokens_used,
)
from app.services.licensing_service import assert_ai_quota_available, record_usage_and_debit
from tests.test_firestore_gcs_backend import FakeFirestoreClient


class TestTokenLimitAndModels(unittest.TestCase):
    def test_default_and_env_model_chains_and_token_limit(self):
        default_cfg = Settings(_env_file=None)
        self.assertEqual(default_cfg.gemini_primary_reasoning_model, "gemini-3.5-flash-lite")
        self.assertEqual(default_cfg.gemini_fallback_reasoning_model, "gemini-3.1-flash-lite")
        self.assertEqual(default_cfg.ai_user_max_tpm, 100_000)
        self.assertEqual(default_cfg.ai_user_max_rpd, 250)
        self.assertEqual(default_cfg.ai_user_max_tokens_per_day, 0)

        chains = default_cfg.model_chains()
        self.assertEqual(chains["drafting"][0], "gemini-3.5-flash-lite")
        self.assertEqual(chains["segmentation"][0], "gemini-3.5-flash-lite")
        self.assertEqual(chains["research"][0], "gemini-3.1-flash-lite")
        self.assertEqual(chains["audit"][0], "gemini-3.1-flash-lite")
        self.assertEqual(chains["live_translate"][0], "gemini-3.1-flash-lite")

        for role, models in chains.items():
            for forbidden in ("gemini-3.8-flash", "gemini-3.6-flash"):
                self.assertNotIn(forbidden, models, f"Unexpected {forbidden} in role {role}")

        get_settings.cache_clear()
        loaded_cfg = get_settings()
        self.assertEqual(loaded_cfg.ai_user_max_tpm, 100_000)
        self.assertEqual(loaded_cfg.ai_user_max_rpd, 250)
        self.assertEqual(loaded_cfg.ai_user_max_tokens_per_day, 0)
        self.assertEqual(loaded_cfg.gemini_primary_reasoning_model, "gemini-3.5-flash-lite")
        self.assertEqual(loaded_cfg.gemini_fallback_reasoning_model, "gemini-3.1-flash-lite")
        loaded_chains = loaded_cfg.model_chains()
        self.assertEqual(loaded_chains["drafting"][0], "gemini-3.5-flash-lite")
        self.assertEqual(loaded_chains["research"][0], "gemini-3.1-flash-lite")
        self.assertEqual(loaded_chains["audit"][0], "gemini-3.1-flash-lite")

    def test_gemini_router_tracks_tokens_from_usage_metadata(self):
        reset_request_tokens_used()
        router = GeminiRouter()

        fake_client = MagicMock()
        fake_response_total = SimpleNamespace(
            text="Réponse test 1",
            usage_metadata=SimpleNamespace(total_token_count=42_500, prompt_token_count=30_000, candidates_token_count=12_500),
        )
        fake_response_split = SimpleNamespace(
            text="Réponse test 2",
            usage_metadata=SimpleNamespace(total_token_count=0, prompt_token_count=5_000, candidates_token_count=2_500),
        )
        fake_client.models.generate_content.side_effect = [fake_response_total, fake_response_split]

        with patch.object(router, "_get_client", return_value=fake_client):
            res1 = router.generate_with_fallback("drafting", "Prompt 1")
            res2 = router.generate_with_fallback("audit", "Prompt 2")

        self.assertEqual(res1["tokens_used"], 42_500)
        self.assertEqual(res2["tokens_used"], 7_500)
        self.assertEqual(consume_request_tokens_used(), 50_000)
        self.assertEqual(consume_request_tokens_used(), 0)

    def _run_daily_token_cap_flow(self, backend_mode: str, fake_client=None, tmp_dir: str = ""):
        get_settings.cache_clear()
        settings = get_settings()
        orig_db_backend = settings.db_backend
        orig_db_url = settings.database_url
        orig_rpm = settings.ai_user_max_rpm
        orig_rpd = settings.ai_user_max_rpd
        orig_tok = settings.ai_user_max_tokens_per_day

        store = None
        created_ids = []
        try:
            settings.db_backend = backend_mode
            settings.ai_user_max_rpm = 100
            settings.ai_user_max_rpd = 500
            settings.ai_user_max_tpm = 0
            settings.ai_user_max_tokens_per_day = 100_000

            reset_db_state()
            set_firestore_client_override(fake_client)
            set_blob_storage_override(LocalBlobStorage(Path(tmp_dir) / "blobs") if tmp_dir else None)
            reset_blob_storage_cache()
            init_db(force=True)

            store = get_store()
            now = datetime.now(timezone.utc)
            suffix = uuid4().hex[:6]

            # 1. Student profile enforcement
            stu = create_user_account(
                db=store,
                email=f"tok.stu.{suffix}@test.local",
                full_name="Token Student",
                password="TempPassword123!",
                role="student",
            )
            created_ids.append(stu.id)
            store.upsert_entitlement(
                user_id=stu.id,
                module_key="module1",
                starts_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=30),
                updated_at=now,
            )
            store.add_quota_credits_atomic(user_id=stu.id, add_credits=200, now=now)

            # Simulate Gemini router extracting 60k then 40k tokens via ContextVar
            reset_request_tokens_used()
            from app.services.gemini_client import add_request_tokens_used

            assert_ai_quota_available(store, stu, module_key="module1", action="draft_section")
            add_request_tokens_used(60_000)
            record_usage_and_debit(
                store,
                stu,
                module_key="module1",
                action="draft_section",
                credits_cost=2,
                model_used="gemini-3.5-flash",
                status="ok",
            )
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            self.assertEqual(store.sum_user_tokens_today(stu.id, day_start), 60_000)

            assert_ai_quota_available(store, stu, module_key="module1", action="audit_section")
            add_request_tokens_used(40_000)
            record_usage_and_debit(
                store,
                stu,
                module_key="module1",
                action="audit_section",
                credits_cost=1,
                model_used="gemini-3.1-flash-lite",
                status="ok",
            )
            self.assertEqual(store.sum_user_tokens_today(stu.id, day_start), 100_000)

            with self.assertRaises(HTTPException) as ctx_stu:
                assert_ai_quota_available(store, stu, module_key="module1", action="research")
            self.assertEqual(ctx_stu.exception.status_code, 429)
            self.assertEqual(ctx_stu.exception.detail["code"], "AI_RATE_LIMIT_TOKENS_DAY")
            self.assertEqual(ctx_stu.exception.detail["limit_tokens_day"], 100_000)
            self.assertEqual(ctx_stu.exception.detail["used_tokens_today"], 100_000)

            # 2. Admin profile enforcement (must also obey 100,000 tokens/day)
            adm = create_user_account(
                db=store,
                email=f"tok.adm.{suffix}@test.local",
                full_name="Token Admin",
                password="TempPassword123!",
                role="admin",
            )
            created_ids.append(adm.id)
            assert_ai_quota_available(store, adm, module_key="module1", action="draft_section")
            record_usage_and_debit(
                store,
                adm,
                module_key="module1",
                action="draft_section",
                credits_cost=2,
                model_used="gemini-3.5-flash",
                status="ok",
                tokens_used=100_500,
            )
            with self.assertRaises(HTTPException) as ctx_adm:
                assert_ai_quota_available(store, adm, module_key="module1", action="audit_section")
            self.assertEqual(ctx_adm.exception.status_code, 429)
            self.assertEqual(ctx_adm.exception.detail["code"], "AI_RATE_LIMIT_TOKENS_DAY")
            self.assertEqual(ctx_adm.exception.detail["used_tokens_today"], 100_500)
        finally:
            if store is not None:
                for uid in created_ids:
                    try:
                        store.delete_user_cascade(uid)
                    except Exception:
                        pass
                store.close()
            settings.db_backend = orig_db_backend
            settings.database_url = orig_db_url
            settings.ai_user_max_rpm = orig_rpm
            settings.ai_user_max_rpd = orig_rpd
            settings.ai_user_max_tokens_per_day = orig_tok
            set_firestore_client_override(None)
            set_blob_storage_override(None)
            reset_blob_storage_cache()
            reset_db_state()
            reset_request_tokens_used()

    def test_daily_token_limit_sql_backend(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            self._run_daily_token_cap_flow("sql", fake_client=None, tmp_dir=tmp_dir)

    def test_daily_token_limit_firestore_backend(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_fs = FakeFirestoreClient()
            self._run_daily_token_cap_flow("firestore", fake_client=fake_fs, tmp_dir=tmp_dir)

    def test_bidirectional_flash_lite_relay(self):
        from pydantic import BaseModel
        from app.services.gemini_client import reset_ai_routing_policy, set_ai_routing_policy

        class _SampleOut(BaseModel):
            answer: str

        # 1) 3.5-flash-lite times out -> 3.1-flash-lite takes the relay immediately
        router = GeminiRouter()
        called_models = []

        def fake_gen_35_fails(model, contents, config=None):
            called_models.append(model)
            if model == "gemini-3.5-flash-lite":
                raise TimeoutError("Read timed out")
            return SimpleNamespace(
                text='{"answer": "ok from 3.1"}',
                parsed=None,
                usage_metadata=SimpleNamespace(total_token_count=120),
            )

        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = fake_gen_35_fails

        with patch.object(router, "_get_client", return_value=fake_client):
            res = router.generate_structured("drafting", "Test prompt", _SampleOut)
            self.assertEqual(res["model_used"], "gemini-3.1-flash-lite")
            self.assertEqual(res["data"].answer, "ok from 3.1")
            self.assertEqual(called_models, ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"])

            # Next call while 3.5-flash-lite is in cooldown routes directly to 3.1-flash-lite first
            called_models.clear()
            res2 = router.generate_structured("drafting", "Follow-up prompt", _SampleOut)
            self.assertEqual(res2["model_used"], "gemini-3.1-flash-lite")
            self.assertEqual(called_models, ["gemini-3.1-flash-lite"])

        # 2) Vice versa: 3.1-flash-lite returns empty/broken output -> 3.5-flash-lite takes the relay
        router2 = GeminiRouter()
        called_models2 = []

        def fake_gen_31_fails(model, contents, config=None):
            called_models2.append(model)
            if model == "gemini-3.1-flash-lite":
                return SimpleNamespace(text="", parsed=None, usage_metadata=None)
            return SimpleNamespace(
                text='{"answer": "ok from 3.5"}',
                parsed=None,
                usage_metadata=SimpleNamespace(total_token_count=140),
            )

        fake_client2 = MagicMock()
        fake_client2.models.generate_content.side_effect = fake_gen_31_fails

        try:
            with patch.object(router2, "_get_client", return_value=fake_client2):
                res3 = router2.generate_structured("research", "Research prompt", _SampleOut)
                self.assertEqual(res3["model_used"], "gemini-3.5-flash-lite")
                self.assertEqual(res3["data"].answer, "ok from 3.5")
                self.assertEqual(called_models2, ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"])

                # Even when prefer_fallback_35 is active, 3.5-flash-lite still relays if 3.1 fails
                router3 = GeminiRouter()
                called_models3 = []
                fake_client3 = MagicMock()

                def fake_gen_31_down(model, contents, config=None):
                    called_models3.append(model)
                    if model == "gemini-3.1-flash-lite":
                        raise TimeoutError("3.1 unresponsive")
                    return SimpleNamespace(
                        text="Relay text from 3.5",
                        parsed=None,
                        usage_metadata=SimpleNamespace(total_token_count=80),
                    )

                fake_client3.models.generate_content.side_effect = fake_gen_31_down
                set_ai_routing_policy(prefer_fallback_35=True, reason="test")
                with patch.object(router3, "_get_client", return_value=fake_client3):
                    res4 = router3.generate_with_fallback("drafting", "Draft prompt")
                    self.assertEqual(res4["model_used"], "gemini-3.5-flash-lite")
                    self.assertEqual(called_models3, ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"])
        finally:
            reset_ai_routing_policy()


if __name__ == "__main__":
    unittest.main()
