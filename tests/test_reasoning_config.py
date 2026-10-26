import os
import unittest
from unittest.mock import patch

from evidence_detective.config import model_key, reasoning_config
from evidence_detective.reasoning import Reasoner
from evidence_detective.transport import ServiceError


class ReasoningConfigurationTests(unittest.TestCase):
    def test_ambient_provider_key_never_selects_a_model_service(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-unused-secret"}, clear=True):
            self.assertEqual(model_key(), "")
            self.assertIsNone(reasoning_config())
            with self.assertRaises(ServiceError):
                Reasoner("test-generic-key")

    def test_service_stays_off_until_explicitly_enabled(self):
        with patch.dict(os.environ, {
            "REASONING_API_KEY": "test-key", "REASONING_BASE_URL": "https://model.example/v1",
            "REASONING_MODEL": "explicit-test-model"
        }, clear=True):
            self.assertIsNone(reasoning_config())
            self.assertEqual(model_key(), "")

    def test_partial_configuration_does_not_enable_service(self):
        with patch.dict(os.environ, {"REASONING_ENABLED": "1", "REASONING_API_KEY": "test-key"}, clear=True):
            self.assertIsNone(reasoning_config())

    def test_explicit_generic_model_configuration_has_no_provider_defaults(self):
        with patch.dict(os.environ, {
            "REASONING_ENABLED": "1", "REASONING_API_KEY": "test-key",
            "REASONING_BASE_URL": "https://model.example/v1/", "REASONING_MODEL": "explicit-test-model"
        }, clear=True):
            configuration = reasoning_config()
            self.assertEqual(configuration["base_url"], "https://model.example/v1")
            self.assertEqual(configuration["model"], "explicit-test-model")
            reasoner = Reasoner(model_key())
            self.assertEqual(reasoner.base, "https://model.example/v1")
            self.assertEqual(reasoner.model, "explicit-test-model")

    def test_generic_request_does_not_include_provider_specific_thinking_fields(self):
        with patch.dict(os.environ, {
            "REASONING_ENABLED": "1", "REASONING_API_KEY": "test-key",
            "REASONING_BASE_URL": "https://model.example/v1", "REASONING_MODEL": "explicit-test-model"
        }, clear=True):
            with patch("evidence_detective.reasoning.post_json", return_value={
                "choices": [{"message": {"content": "{}"}}]
            }) as service:
                Reasoner(model_key()).plan("公开问题")
            self.assertNotIn("thinking", service.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
