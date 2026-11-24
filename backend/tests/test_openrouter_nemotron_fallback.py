from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

from pydantic import BaseModel

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.core.model_catalog import GEMINI_MODEL_CATALOG
from app.services.gemini_client import GeminiRouter, reset_ai_routing_policy


class _SampleOut(BaseModel):
    title: str
    ok: bool


class TestOpenRouterNemotronFallback(unittest.TestCase):
    def tearDown(self):
        reset_ai_routing_policy()

    def test_catalog_contains_nemotron_free_models(self):
        ids = {m["id"] for m in GEMINI_MODEL_CATALOG}
        self.assertIn("nvidia/nemotron-3.5-super-vl:free", ids)
        self.assertIn("nvidia/nemotron-3.5-lightning:free", ids)
        self.assertIn("nvidia/nemotron-3-ultra-550b-a55b:free", ids)

    def test_fourth_emergency_backup_catches_total_gemini_503_spike(self):
        get_settings.cache_clear()
        settings = get_settings()
        orig_or_key = settings.openrouter_api_key
        try:
            settings.openrouter_api_key = "sk-or-test-key"
            chains = settings.model_chains()
            self.assertEqual(
                chains["drafting"],
                [
                    "gemini-3.5-flash-lite",
                    "gemini-3.1-flash-lite",
                    "gemini-3.5-flash",
                    "nvidia/nemotron-3.5-super-vl:free",
                ],
            )

            router = GeminiRouter()
            router.settings = settings

            fake_gemini_client = MagicMock()
            fake_gemini_client.models.generate_content.side_effect = RuntimeError(
                "HTTP 503 - This model is currently experiencing high demand."
            )

            fake_http_resp = MagicMock()
            fake_http_resp.ok = True
            fake_http_resp.status_code = 200
            fake_http_resp.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"title": "Protocole FMSB", "ok": true}\n```'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165},
            }

            with patch.object(router, "_get_client", return_value=fake_gemini_client), patch(
                "app.services.gemini_client.requests.post", return_value=fake_http_resp
            ) as mock_post:
                res = router.generate_structured(
                    role="drafting",
                    contents="Génère un titre FMSB.",
                    schema=_SampleOut,
                )

            self.assertEqual(res["model_used"], "nvidia/nemotron-3.5-super-vl:free")
            self.assertEqual(res["data"].title, "Protocole FMSB")
            self.assertTrue(res["data"].ok)
            self.assertEqual(len(res["fallback_errors"]), 3)
            mock_post.assert_called_once()
        finally:
            settings.openrouter_api_key = orig_or_key
            get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
