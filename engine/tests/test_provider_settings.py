"""Offline provider routing/parameter regressions; never contact a cloud account."""
import json
from pathlib import Path
import pickle
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker


class ProviderSettingsTests(unittest.TestCase):
    def service(self, provider="deepSeek", **updates):
        defaults = {"deepSeek": ("https://api.deepseek.com", "deepseek-flash"),
                    "alibaba": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
                    "siliconFlow": ("https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V4-Flash"),
                    "openAI": ("https://api.openai.com/v1", "gpt-6-luna"),
                    "custom": ("https://gateway.example/v1", "model")}
        endpoint, model = defaults[provider]
        value = {"kind": "openai", "provider": provider, "baseURL": endpoint, "model": model,
                 "apiKey": "fixture-not-real", "timeoutSeconds": 120, "concurrency": 2}
        value.update(updates)
        return value

    def test_legacy_provider_inference_never_rewrites_destination(self):
        for provider in ("deepSeek", "alibaba", "siliconFlow", "openAI", "custom"):
            raw = self.service(provider); del raw["provider"]
            result = worker.normalized_service(raw)
            self.assertEqual(result["provider"], provider)
            self.assertEqual(result["baseURL"], raw["baseURL"])
        raw = self.service("custom", baseURL="https://api.deepseek.com.evil.example/v1")
        del raw["provider"]
        self.assertEqual(worker.normalized_service(raw)["provider"], "custom")

    def test_spoofed_provider_cannot_apply_to_other_endpoint(self):
        for raw in (self.service(baseURL="https://other.example/v1"), (self.service() | {"provider": "unknown"}),
                    self.service(kind="ollama", baseURL="http://localhost:11434")):
            with self.assertRaises(worker.WorkerError):
                worker.normalized_service(raw)

    def test_no_control_characters_in_model_or_key(self):
        for update in ({"model": "bad\nmodel"}, {"apiKey": "fixture\tkey"}, {"apiKey": "fixture\rkey"}):
            with self.assertRaises(worker.WorkerError):
                worker.normalized_service(self.service(**update))

    def test_probe_modern_openai_uses_completion_limit(self):
        service = worker.normalized_service(self.service("openAI"))
        options = worker.provider_request_options(service, probe=True)
        self.assertEqual(options, {"max_completion_tokens": 1024, "reasoning_effort": "none"})
        custom = worker.normalized_service(self.service("custom", model="gpt-6-luna"))
        self.assertEqual(worker.provider_request_options(custom, probe=True), {"max_tokens": 1024})

    def test_provider_options_survive_upstream_transform_and_worker_pickle(self):
        for provider, expected in [("deepSeek", {"thinking": {"type": "disabled"}}), ("alibaba", {"enable_thinking": False}), ("siliconFlow", {"enable_thinking": False})]:
            service = worker.normalized_service(self.service(provider))
            with tempfile.TemporaryDirectory() as directory:
                settings = worker.build_settings({"service": service, "sourceLanguage": "en", "targetLanguage": "zh"}, directory)
                engine = settings.translate_engine_settings
                self.assertEqual(engine.translate_engine_type, "OpenAI")
                self.assertEqual(engine._openai_extra_body, expected)
                cloned = pickle.loads(pickle.dumps(settings))
                self.assertEqual(cloned.translate_engine_settings._openai_extra_body, expected)
                engine.validate_settings()
                self.assertEqual(engine.openai_base_url, service["baseURL"])
                self.assertEqual(engine.openai_model, service["model"])

    def test_unknown_alibaba_models_keep_their_own_options(self):
        service = worker.normalized_service(self.service("alibaba", model="glm-5.3"))
        self.assertEqual(worker.provider_request_options(service), {})

    def test_ollama_disables_presence_penalty_and_custom_keeps_own_options(self):
        for kind in ("ollama", "openai"):
            service = worker.normalized_service({"kind": kind, "baseURL": "http://localhost:11434/v1", "model": "fixture", "apiKey": "fixture"})
            with tempfile.TemporaryDirectory() as directory:
                settings = worker.build_settings({"service": service, "sourceLanguage": "en", "targetLanguage": "zh"}, directory)
                engine = settings.translate_engine_settings
                if kind == 'ollama':
                    self.assertEqual(engine.translate_engine_type, 'OpenAI')
                    self.assertEqual(engine._openai_extra_body, {'presence_penalty': 0, 'max_tokens': 8192})
                    self.assertEqual(engine.openai_reasoning_effort, 'none')
                    self.assertEqual(engine.openai_temperature, '0')
                    cloned = pickle.loads(pickle.dumps(settings))
                    self.assertEqual(cloned.translate_engine_settings._openai_extra_body, {'presence_penalty': 0, 'max_tokens': 8192})
                else:
                    self.assertEqual(engine.translate_engine_type, 'OpenAICompatible')
                    self.assertIsNone(engine.openai_compatible_reasoning_effort)
                    self.assertIsNone(engine.openai_compatible_temperature)

    def test_local_probe_keeps_smaller_output_limit(self):
        service = worker.normalized_service({'kind': 'ollama', 'baseURL': 'http://localhost:11434', 'model': 'fixture'})
        self.assertEqual(worker.provider_request_options(service, probe=True)['max_tokens'], 1024)

if __name__ == "__main__":
    unittest.main()
