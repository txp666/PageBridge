import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pagebridge.core import AppError
from pagebridge.services import CredentialVault, ServiceStore, chat_completion, list_models, new_profile, validate_profile, worker_service


class MemoryVault:
    """Test dependency, never selected by the production Windows application."""
    persistent = False
    storage_description = "test memory"

    def __init__(self):
        self.values = {}

    def get(self, identifier):
        return self.values.get(identifier, "")

    def set(self, identifier, secret):
        if secret:
            self.values[identifier] = secret
        else:
            self.delete(identifier)

    def delete(self, identifier):
        self.values.pop(identifier, None)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ServiceStore(Path(self.temp.name), vault=MemoryVault())
        self.ids = []

    def tearDown(self):
        for identifier in self.ids:
            self.store.vault.delete(identifier)
        self.temp.cleanup()

    def profile(self, provider="custom"):
        profile = new_profile(provider)
        profile["model"] = "test-model"
        self.ids.append(profile["id"])
        return profile

    def test_secret_only_in_vault_and_stdin_service(self):
        profile = self.profile()
        profile["apiKey"] = "must-not-persist"
        self.store.save(profile, "fake-test-secret")
        raw = self.store.path.read_text()
        self.assertNotIn("fake-test-secret", raw)
        self.assertNotIn("apiKey", raw)
        self.assertNotIn("must-not-persist", raw)
        self.assertEqual(self.store.key(profile["id"]), "fake-test-secret")
        self.assertEqual(worker_service(self.store.get(profile["id"]), self.store.key(profile["id"]))["apiKey"], "fake-test-secret")

    def test_destination_change_clears_old_secret(self):
        profile = self.profile()
        self.store.save(profile, "test-secret")
        profile["baseURL"] = "https://another.example.com/v1"
        self.store.save(profile)
        self.assertEqual(self.store.key(profile["id"]), "")

    def test_same_destination_keeps_secret_and_failed_save_rolls_back(self):
        profile = self.profile()
        self.store.save(profile, "old-secret")
        profile["model"] = "different-model"
        self.store.save(profile)
        self.assertEqual(self.store.key(profile["id"]), "old-secret")
        with patch("pagebridge.services.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.save(profile, "new-secret")
        self.assertEqual(self.store.key(profile["id"]), "old-secret")

    def test_remote_http_and_embedded_credentials_rejected(self):
        profile = self.profile()
        for url in ["http://192.168.1.2/v1", "https://user:secret@example.com/v1", "https://example.com/v1?api_key=bad", "https://example.com/v1/chat/completions"]:
            profile["baseURL"] = url
            with self.assertRaises(AppError):
                validate_profile(profile)

    def test_provider_endpoint_and_ollama_loopback_only(self):
        profile = self.profile("deepSeek")
        profile["baseURL"] = "https://arbitrary.example.com"
        with self.assertRaises(AppError):
            validate_profile(profile)
        profile = self.profile("ollama")
        profile["baseURL"] = "https://remote.example.com"
        with self.assertRaises(AppError):
            validate_profile(profile)

    def test_model_list_and_truncation_validation(self):
        profile = self.profile()
        with patch("pagebridge.services._request", return_value={"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}]}):
            self.assertEqual(list_models(profile), ["a", "b"])
        with patch("pagebridge.services._request", return_value={"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}):
            with self.assertRaises(AppError):
                chat_completion(profile, "", [])

    def test_delete_clears_credential_and_active_selection(self):
        first, second = self.profile(), self.profile()
        self.store.save(first, "secret")
        self.store.save(second)
        self.store.delete(first["id"])
        self.assertEqual(self.store.active_id, second["id"])
        self.assertEqual(self.store.vault.get(first["id"]), "")

    def test_corrupt_state_does_not_get_overwritten(self):
        self.store.path.write_text('{"version":3}')
        with self.assertRaises(AppError):
            ServiceStore(self.store.root)
        self.assertEqual(self.store.path.read_text(), '{"version":3}')

    def test_nonwindows_explicitly_session_only(self):
        if os.name == "nt":
            self.assertTrue(CredentialVault.persistent)
        else:
            self.assertFalse(CredentialVault.persistent)
            self.assertIn("内存", CredentialVault.storage_description)


@unittest.skipUnless(os.name == "nt" and os.environ.get("PAGEBRIDGE_TEST_WINDOWS_CREDENTIALS") == "1",
                     "Explicit logged-in Windows Credential Manager integration")
class WindowsCredentialIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = CredentialVault()
        self.identifier = str(uuid.uuid4())

    def tearDown(self):
        self.vault.delete(self.identifier)
        self.temp.cleanup()

    def test_real_vault_roundtrip_and_delete(self):
        self.assertEqual(self.vault.get(self.identifier), "")
        self.vault.set(self.identifier, "fake-integration-密钥")
        self.assertEqual(CredentialVault().get(self.identifier), "fake-integration-密钥")
        self.vault.delete(self.identifier)
        self.assertEqual(CredentialVault().get(self.identifier), "")

    def test_real_service_key_preservation_destination_change_and_rollback(self):
        store = ServiceStore(Path(self.temp.name))
        profile = new_profile("custom")
        profile.update(id=self.identifier, model="integration-fixture")
        store.save(profile, "fake-integration-original")
        store.save(profile)
        self.assertEqual(store.key(self.identifier), "fake-integration-original")
        with patch("pagebridge.services.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.save(profile, "fake-integration-replacement")
        self.assertEqual(store.key(self.identifier), "fake-integration-original")
        profile["baseURL"] = "https://other.example.com/v1"
        store.save(profile)
        self.assertEqual(store.key(self.identifier), "")
        self.assertNotIn("fake-integration", store.path.read_text())


if __name__ == "__main__":
    unittest.main()
