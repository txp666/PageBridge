import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from pagebridge import assistant
from pagebridge.core import AppError, DocumentStore, atomic_json
from pagebridge.services import ServiceStore, new_profile


class MemoryVault:
    def __init__(self):
        self.values = {}

    def get(self, identifier):
        return self.values.get(identifier, "")

    def set(self, identifier, value):
        self.values[identifier] = value

    def delete(self, identifier):
        self.values.pop(identifier, None)


def pdf_file(path, texts):
    with pymupdf.open() as pdf:
        for text in texts:
            pdf.new_page().insert_textbox(pymupdf.Rect(20, 20, 575, 810), text, fontsize=9)
        pdf.save(path)
    return path


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.documents = DocumentStore(self.root / "library")
        self.doc = self.documents.import_pdf(pdf_file(self.root / "manual.pdf", [
            "Overview\nThe pump transfers cooling water. Safety limits apply.",
            "Operation\nThe pressure limit is 4 bar. Close the valve before disconnecting.",
            "Conclusion\nVerify the seal and record the inspection outcome."
        ]))
        self.services = ServiceStore(self.documents.root, vault=MemoryVault())
        self.profile = new_profile("ollama")
        self.services.save(self.profile)
        self.request = dict(document_id=self.doc["id"], service_id=self.profile["id"],
                            mode="paper", page=1, variant="original", question="What is the pressure limit?")

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, **overrides):
        return assistant.prepare(self.documents, self.services, self.request | overrides)

    def history(self):
        return assistant.AssistantStore(self.documents).history(self.doc["id"])

    def request_response(self, prepared, response):
        raw = response if isinstance(response, bytes) else json.dumps(response).encode("utf-8")
        opener = Mock()
        opener.open.return_value = io.BytesIO(raw)
        with patch("pagebridge.assistant.urllib.request.build_opener", return_value=opener):
            result = assistant.request_answer(prepared)
        return result, opener.open.call_args.args[0]

    def test_document_evidence_records_physical_page_and_original_variant(self):
        context = assistant.build_context(self.documents, self.request | {"variant": "translated"})
        self.assertEqual(context["scanned_pages"], [0, 1, 2])
        self.assertEqual(context["evidence"][0]["page"], 1)
        self.assertEqual({e["page"] for e in context["evidence"]}, {0, 1, 2})
        self.assertTrue(all(e["variant"] == "original" for e in context["evidence"]))
        self.assertIn("模型未通读全文", context["scope"])

    def test_current_page_uses_translated_text_not_original(self):
        translated = pdf_file(Path(self.doc["input_path"]).parent / "translated.pdf", ["Translated A", "Translated pressure four bar", "Translated C"])
        state = copy.deepcopy(self.documents._state)
        state["documents"][0]["translated_path"] = str(translated)
        self.documents._write(state)
        context = assistant.build_context(self.documents, self.request | {"mode": "currentPage", "variant": "translated"})
        self.assertEqual(context["scanned_pages"], [1])
        self.assertEqual(len(context["evidence"]), 1)
        self.assertIn("Translated pressure", context["evidence"][0]["text"])
        self.assertEqual(context["evidence"][0]["variant"], "translated")

    def test_selection_is_bounded_without_extracting_pdf(self):
        with patch("pagebridge.assistant.pymupdf.open", side_effect=AssertionError("Selection must not extract PDF")):
            context = assistant.build_context(self.documents, self.request | {"mode": "selection", "selection": "x" * 7000, "variant": "translated"})
        self.assertEqual(len(context["evidence"][0]["text"]), 6000)
        self.assertTrue(context["evidence"][0]["truncated"])
        self.assertEqual(context["evidence"][0]["variant"], "translated")
        with self.assertRaisesRegex(AppError, "选择文字"):
            assistant.build_context(self.documents, self.request | {"mode": "selection", "selection": " "})

    def test_free_question_does_not_read_pdf_and_explains_history(self):
        with patch("pagebridge.assistant.pymupdf.open", side_effect=AssertionError("Free question must not extract PDF")):
            context = assistant.build_context(self.documents, self.request | {"mode": "freeQuestion"})
        self.assertEqual(context["evidence"], [])
        self.assertIn("此前文档内容", context["scope"])

    def test_context_budget_and_scan_limit(self):
        class Page:
            def get_text(self):
                return "pressure test " * 1000
        class PDF:
            needs_pass = False
            page_count = 600
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __getitem__(self, index): return Page()
        document = self.doc | {"page_count": 600}
        with patch.object(self.documents, "get", return_value=document), patch("pagebridge.assistant.pymupdf.open", return_value=PDF()):
            context = assistant.build_context(self.documents, self.request)
        self.assertLessEqual(len(context["scanned_pages"]), 500)
        self.assertLessEqual(len(context["evidence"]), 6)
        self.assertLessEqual(sum(len(e["text"]) for e in context["evidence"]), 12000)
        self.assertTrue(all(len(e["text"]) <= 2400 for e in context["evidence"]))
        self.assertTrue(context["extraction_limited"])

    def test_scope_validation_and_missing_translation(self):
        for change in ({"mode": "bad"}, {"mode": []}, {"page": True}, {"page": -1}, {"page": 3}, {"variant": {}}):
            with self.subTest(change=change), self.assertRaises(AppError):
                assistant.build_context(self.documents, self.request | change)
        with self.assertRaisesRegex(AppError, "译文"):
            assistant.build_context(self.documents, self.request | {"mode": "currentPage", "variant": "translated"})

    def test_prepare_persists_user_and_keeps_key_out_of_history_and_active_choice(self):
        cloud = new_profile("custom")
        cloud["model"] = "fixture-only"
        self.services.save(cloud, "fake-test-secret")
        prepared = self.prepare(service_id=cloud["id"])
        self.assertEqual(prepared.api_key, "fake-test-secret")
        self.assertEqual(self.history()["messages"][0]["role"], "user")
        self.assertEqual(self.services.active_id, self.profile["id"])
        raw = (self.documents.root / "assistant" / (self.doc["id"] + ".json")).read_text()
        self.assertNotIn("fake-test-secret", raw)
        self.assertNotIn("api_key", raw)
        self.assertEqual(self.history()["service_id"], cloud["id"])

    def test_unconfigured_key_and_oversize_question_do_not_change_history(self):
        cloud = new_profile("custom"); cloud["model"] = "fixture"
        self.services.save(cloud)
        with self.assertRaisesRegex(AppError, "API Key"):
            self.prepare(service_id=cloud["id"])
        with self.assertRaisesRegex(AppError, "2,000"):
            self.prepare(question="x" * 2001)
        self.assertEqual(self.history()["messages"], [])

    def test_payload_bounds_prior_messages_and_keeps_evidence_untrusted(self):
        prepared = self.prepare()
        prepared.history = [dict(role="assistant" if i % 2 else "user", text="x" * 2000) for i in range(40)]
        body = assistant.request_payload(prepared)
        self.assertFalse(body["stream"])
        self.assertFalse(body["think"])
        self.assertEqual(body["options"]["num_predict"], 3072)
        self.assertLessEqual(sum(len(m["content"]) for m in body["messages"][1:-1]), 8000)
        self.assertIn("untrusted evidence", body["messages"][0]["content"])
        self.assertIn("[P2]", body["messages"][0]["content"])
        self.assertIn("UNTRUSTED DOCUMENT EVIDENCE", body["messages"][-1]["content"])

    def test_openai_compatible_payload_model_options(self):
        prepared = self.prepare()
        for provider, model, field, value in [
            ("openAI", "gpt-6", "max_completion_tokens", 3072),
            ("deepSeek", "deepseek-chat", "thinking", {"type": "disabled"}),
            ("alibaba", "qwen-plus", "enable_thinking", False),
            ("custom", "some-model", "max_tokens", 3072),
        ]:
            prepared.profile.update(kind="openai", provider=provider, model=model)
            with self.subTest(provider=provider):
                self.assertEqual(assistant.request_payload(prepared)[field], value)

    def test_real_request_shape_and_response_checks(self):
        prepared = self.prepare()
        answer, request = self.request_response(prepared, {"done": True, "done_reason": "stop", "message": {"content": "Four bar [P2]"}})
        self.assertEqual(answer, "Four bar [P2]")
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertNotIn("Authorization", request.headers)
        prepared.profile.update(kind="openai", provider="custom", baseURL="https://example.invalid/v1")
        prepared.api_key = "fake-test-secret"
        answer, request = self.request_response(prepared, {"choices": [{"finish_reason": "stop", "message": {"content": "Four bar"}}]})
        self.assertEqual(answer, "Four bar")
        self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer fake-test-secret")
        for response in [b"x" * (assistant.RESPONSE_BYTES + 1), [], {"choices": []}, {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}, {"choices": [{"message": {"content": "x" * 20001}}]}]:
            with self.subTest(response_type=type(response)), self.assertRaises(AppError):
                self.request_response(prepared, response)

    def test_provider_errors_hide_body_and_credentials(self):
        prepared = self.prepare()
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://secret.invalid", 401, "fake-test-secret", {}, None)
        with patch("pagebridge.assistant.urllib.request.build_opener", return_value=opener), self.assertRaises(AppError) as raised:
            assistant.request_answer(prepared)
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("fake-test-secret", str(raised.exception))

    def test_citations_only_navigate_offered_pages_and_persist(self):
        prepared = self.prepare(mode="currentPage")
        result = assistant.finish(self.documents, prepared, "The limit is four bar [P2]. Also [P99] and [P1,P2].")
        message = result["messages"][-1]
        self.assertEqual([e["page"] for e in message["citations"]], [1])
        self.assertEqual(message["unverified_pages"], [0, 98])
        self.assertIn("4 bar", message["citations"][0]["text"])
        reloaded = assistant.AssistantStore(DocumentStore(self.documents.root)).history(self.doc["id"])
        self.assertEqual(reloaded["messages"], result["messages"])
        with self.assertRaisesRegex(AppError, "迟到"):
            assistant.finish(self.documents, prepared, "duplicate")

    def test_clear_and_new_question_reject_late_answer(self):
        old = self.prepare()
        assistant.AssistantStore(self.documents).clear(self.doc["id"])
        with self.assertRaisesRegex(AppError, "迟到"):
            assistant.finish(self.documents, old, "late")
        self.assertEqual(self.history()["messages"], [])
        old = self.prepare()
        latest = self.prepare(question="New question")
        with self.assertRaisesRegex(AppError, "迟到"):
            assistant.finish(self.documents, old, "late")
        result = assistant.finish(self.documents, latest, "Current answer [P1]")
        self.assertEqual(result["messages"][-1]["text"], "Current answer [P1]")

    def test_per_document_isolation(self):
        prepared = self.prepare()
        other = self.documents.import_pdf(pdf_file(self.root / "other.pdf", ["Another document"]))
        assistant.finish(self.documents, prepared, "First document answer [P2]")
        self.assertEqual(assistant.AssistantStore(self.documents).history(other["id"])["messages"], [])

    def test_corrupt_history_preserved_until_explicit_clear(self):
        self.prepare()
        path = self.documents.root / "assistant" / (self.doc["id"] + ".json")
        path.write_text('{"version":99}')
        with self.assertRaisesRegex(AppError, "损坏"):
            self.prepare()
        self.assertEqual(path.read_text(), '{"version":99}')
        assistant.AssistantStore(self.documents).clear(self.doc["id"])
        self.assertEqual(self.history()["messages"], [])

    def test_history_stays_bounded_including_large_evidence(self):
        for _ in range(24):
            prepared = self.prepare(mode="selection", selection="文" * 6000)
            assistant.finish(self.documents, prepared, "答" * 19000 + " [P2]")
        messages = self.history()["messages"]
        self.assertLessEqual(len(messages), 40)
        self.assertLessEqual(sum(len(m["text"]) + sum(len(c["text"]) for c in m["citations"]) for m in messages), 160000)
        self.assertLessEqual(len(json.dumps(messages, ensure_ascii=False).encode()), 760000)


if __name__ == "__main__":
    unittest.main()
