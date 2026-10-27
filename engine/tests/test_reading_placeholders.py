"""Regression cases for opaque formula markers, not translation quality scoring."""
import copy
import json
from pathlib import Path
import sys
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import structured_reading as reading


class FormulaPlaceholderTests(unittest.IsolatedAsyncioTestCase):
    async def translate(self, source, reply, **metadata):
        block = dict(id="p00001-b00001", kind="text", sourceText=source, status="preserved", **metadata)
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, request=request, json={
                "choices": [{"finish_reason": "stop", "message": {"content": reply}}]})
        service = dict(provider="custom", kind="openai", model="test", baseURL="https://example.test/v1",
                       apiKey="test-only", concurrency=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await reading.translate_block(client, block, service, "fr", "zh")
        self.assertEqual(len(calls), 1)
        return block, calls[0]

    async def test_observed_plain_text_leaks_keep_source_without_priming_markers(self):
        # Exact source/reply pairs from INRS physical p19 and NASA physical p8.
        for source, reply in (("- masse des colis ;", "- [[PBM_1]]"),
                              ("FY24 Flight Manifest", "[[PBM_...]]")):
            with self.subTest(source=source):
                block, payload = await self.translate(source, reply, translatedText="stale",
                                                     translatedTemplate="stale [[PBM_1]]")
                self.assertEqual(payload["messages"][1]["content"], source)
                self.assertNotIn("PBM", payload["messages"][0]["content"])
                self.assertEqual(block["status"], "source_fallback")
                self.assertEqual(block["sourceText"], source)
                self.assertNotIn("translatedText", block)
                self.assertNotIn("translatedTemplate", block)

    async def test_plain_text_translation_does_not_use_stale_template(self):
        block, payload = await self.translate("- masse des colis ;", "- 包裹重量；",
                                             sourceTemplate="stale [[PBM_1]]")
        self.assertEqual(payload["messages"][1]["content"], "- masse des colis ;")
        self.assertNotIn("PBM", payload["messages"][0]["content"])
        self.assertEqual(block["status"], "translated")
        self.assertEqual(block["translatedText"], "- 包裹重量；")
        self.assertNotIn("translatedTemplate", block)

    async def test_actual_math_uses_only_its_exact_markers_and_restores_readable_text(self):
        token = "[[PBM_abc123_0001]]"
        formulas = [dict(token=token, latex="x^2", bbox=[10, 20, 30, 40])]
        block, payload = await self.translate("Mean $x^2$ remains.", f"均值{token}保留。",
                                             inlineMath=copy.deepcopy(formulas), sourceTemplate=f"Mean {token} remains.")
        self.assertIn(token, payload["messages"][0]["content"])
        self.assertNotIn("[[PBM_...]]", payload["messages"][0]["content"])
        self.assertEqual(block["translatedTemplate"], f"均值{token}保留。")
        self.assertEqual(block["translatedText"], "均值$x^2$保留。")
        self.assertEqual(block["inlineMath"], formulas)

    async def test_unknown_marker_cannot_hide_beside_a_complete_real_formula(self):
        token = "[[PBM_abc123_0001]]"
        block, _ = await self.translate("Mean $x$.", f"均值{token}，以及[[PBM_2]]。",
                                       inlineMath=[dict(token=token, latex="x", bbox=[10, 20, 30, 40])],
                                       sourceTemplate=f"Mean {token}.")
        self.assertEqual(block["status"], "source_fallback")
        self.assertNotIn("translatedText", block)
        self.assertNotIn("translatedTemplate", block)

    async def test_unfinished_marker_is_not_displayed(self):
        for reply in ("未知[[PBM_2", "未知[[PBM_2]\n另一行"):
            with self.subTest(reply=reply):
                block, _ = await self.translate("Plain text.", reply)
                self.assertEqual(block["status"], "source_fallback")
                self.assertNotIn("translatedText", block)

    async def test_literal_marker_in_source_is_data_not_an_invented_attachment(self):
        block, _ = await self.translate("The literal string [[PBM_example]] appears twice: [[PBM_example]].",
                                       "字面字符串[[PBM_example]]出现两次：[[PBM_example]]。")
        self.assertEqual(block["status"], "translated")
        self.assertNotIn("translatedTemplate", block)
        self.assertNotIn("inlineMath", block)

    async def test_table_context_is_not_sent_with_the_target_fragment(self):
        context = "Previous row contains 3091 and a different description."
        block, payload = await self.translate("de lithium)", "锂的)", sourceType="table_cell", translationContext=context)
        self.assertEqual(block["status"], "translated")
        self.assertEqual(payload["messages"][-1]["content"], "<SOURCE>\nde lithium)\n</SOURCE>")
        self.assertEqual(len(payload["messages"]), 2)
        self.assertNotIn(context, json.dumps(payload))
        self.assertNotIn("3091", json.dumps(payload))
        self.assertIn("do not expand abbreviations", payload["messages"][0]["content"])
        self.assertIn("Month names", payload["messages"][0]["content"])
        self.assertNotIn("PBM", payload["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
