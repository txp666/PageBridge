"""Focused geometric regressions for the read-only script-baseline audit."""
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "script_baselines", Path(__file__).with_name("audit-script-baselines.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class Page:
    def __init__(self, glyphs):
        self.glyphs = glyphs

    def get_text(self, kind):
        assert kind == "rawdict"
        return {"blocks": [{"lines": [{"dir": (1, 0), "spans": [
            {"font": "Fixture", "size": size, "chars": [
                {"c": char, "origin": (x, y), "bbox": (x, y - size, x + width, y)}]}
            for char, x, y, size, width in self.glyphs]}]}]}


def observed(delta=3.3, base="L", script="o", script_size=5.8, x=10):
    return audit.observations(Page([(base, x, 50, 10, 5),
                                    (script, x + 5.1, 50 + delta, script_size, 3)]), source=False)


def combine(*items):
    return {"pairs": [pair for item in items for pair in item["pairs"]],
            "ambiguous": [entry for item in items for entry in item["ambiguous"]],
            "excluded": {}}


class ScriptBaselineTests(unittest.TestCase):
    def compare(self, before, after, identical=False):
        return audit.compare_page(1, before, after, identical_file=identical)["groups"]

    def test_subscript_and_superscript_direction(self):
        self.assertEqual(observed()["pairs"][0]["direction"], "subscript")
        self.assertEqual(observed(-3.3)["pairs"][0]["direction"], "superscript")

    def test_known_reversal_is_failure_even_when_count_matches(self):
        group = self.compare(observed(), observed(-2.54))[0]
        self.assertEqual(group["status"], "fail")
        self.assertIn("script-direction-changed", group["issues"])
        self.assertIn("baseline-magnitude-changed", group["issues"])

    def test_flat_target_detected(self):
        group = self.compare(observed(), observed(0))[0]
        self.assertEqual(group["status"], "fail")
        self.assertEqual(group["translatedCount"], 1)

    def test_scale_normalization(self):
        scaled = audit.observations(Page([("L", 20, 100, 20, 10),
                                          ("o", 30.2, 106.6, 11.6, 6)]), source=False)
        self.assertEqual(self.compare(observed(), scaled)[0]["status"], "pass")

    def test_identical_label_occurrences_can_reorder(self):
        first, second = observed(3.3), observed(-3.3, x=40)
        self.assertEqual(self.compare(combine(first, second), combine(second, first))[0]["status"], "pass")

    def test_missing_and_relative_size_changes_detected(self):
        empty = {"pairs": [], "ambiguous": [], "excluded": {}}
        self.assertIn("count-mismatch", self.compare(observed(), empty)[0]["issues"])
        self.assertIn("relative-script-size-changed", self.compare(observed(), observed(script_size=8))[0]["issues"])

    def test_word_endings_and_multichar_scripts_are_not_scored(self):
        word = Page([("a", 5, 50, 10, 5), ("L", 10, 50, 10, 5), ("o", 15.1, 53.3, 5.8, 3)])
        descriptor = Page([("L", 10, 50, 10, 5), ("c", 15.1, 53.3, 5.8, 3), ("a", 18.1, 53.3, 5.8, 3)])
        self.assertEqual(audit.observations(word, source=True)["pairs"], [])
        self.assertEqual(audit.observations(descriptor, source=True)["pairs"], [])

    def test_source_flat_glyph_is_explicitly_ambiguous(self):
        page = Page([("L", 10, 50, 10, 5), ("o", 15.1, 50, 5.8, 3)])
        before = audit.observations(page, source=True)
        after = audit.observations(page, source=False)
        self.assertEqual(len(before["ambiguous"]), 1)
        self.assertEqual(self.compare(before, after)[0]["status"], "review")
        self.assertEqual(self.compare(before, after, identical=True)[0]["status"], "identity-unscored")

    def test_identical_file_flag_does_not_hide_nonambiguous_inconsistency(self):
        self.assertEqual(self.compare(observed(), observed(-3.3), identical=True)[0]["status"], "fail")

    def test_square_millimetre_unit_detects_flattened_power(self):
        source = Page([("m",10,50,10,8),("m",18.1,50,10,8),("2",26.9,46.7,5.8,3)])
        flattened = Page([("m",10,50,10,8),("m",18.1,50,10,8),("2",26.9,50,5.8,3)])
        before=audit.observations(source,source=True);after=audit.observations(flattened,source=False)
        self.assertEqual(len(before["pairs"]),1)
        self.assertEqual(before["pairs"][0]["base"]["text"],"mm")
        self.assertEqual(len(before["pairs"][0]["base"]["componentGlyphs"]),2)
        group=self.compare(before,after)[0]
        self.assertEqual(group["status"],"fail")
        self.assertIn("script-direction-changed",group["issues"])

    def test_mm_unit_does_not_match_a_word_or_non_square_suffix(self):
        for glyphs in ([('h',5,50,10,5),('m',10,50,10,8),('m',18.1,50,10,8),('2',26.9,46.7,5.8,3)],
                       [('m',10,50,10,8),('m',18.1,50,10,8),('o',26.9,46.7,5.8,3)]):
            self.assertEqual(audit.observations(Page(glyphs),source=True)["pairs"],[])

    def test_page_ranges_are_one_based_and_validated(self):
        self.assertEqual(audit.selected_pages("1,3-5,3", 5), [1, 3, 4, 5])
        for invalid in ("0", "2-1", "1,6", ""):
            with self.assertRaises(ValueError):
                audit.selected_pages(invalid, 5)


if __name__ == "__main__":
    unittest.main()
