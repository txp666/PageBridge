import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from pagebridge.core import AppError, DocumentStore, atomic_json, sha256
from pagebridge.reading import dispatch_reading


def make_pdf(path, rotation=0, crop=False):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((100, 140), "Select text for a reading annotation.", fontsize=16)
        page.insert_text((100, 175), "A second line stays selectable.", fontsize=16)
        page.add_text_annot((440, 140), "Imported annotation is preserved")
        if crop:
            page.set_cropbox(pymupdf.Rect(70, 80, 540, 730))
        page.set_rotation(rotation)
        pdf.new_page(width=400, height=500).insert_text((40, 60), "Second page")
        pdf.set_toc([[1, "Chapter One", 1], [2, "Details", 2]])
        pdf.save(path)
    return path


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = DocumentStore(self.root / "library")
        self.source = make_pdf(self.root / "public fixture.pdf")
        self.doc = self.store.import_pdf(self.source)
        self.variant = "original"

    def test_search_batches_and_rotation(self):
        for rotation in (0, 90, 180, 270):
            source = make_pdf(self.root / f"rotation-{rotation}.pdf", rotation=rotation, crop=True)
            doc = self.store.import_pdf(source)
            data = dispatch_reading(self.store, dict(action="search_document", document_id=doc["id"], query="reading annotation"))
            self.assertEqual([h["page"] for h in data["hits"]], [0])
            with pymupdf.open(source) as pdf:
                expected = pdf[0].search_for("reading annotation")[0] * pdf[0].rotation_matrix
                actual = data["hits"][0]["rects"][0]
                self.assertAlmostEqual(actual["x"], expected.x0, places=3)
                self.assertAlmostEqual(actual["y"], expected.y0, places=3)
        source = self.root / "long.pdf"
        with pymupdf.open() as pdf:
            for _ in range(30): pdf.new_page().insert_text((20, 30), "search-batch")
            pdf.save(source)
        doc = self.store.import_pdf(source)
        first = dispatch_reading(self.store, dict(action="search_document", document_id=doc["id"], query="search-batch"))
        self.assertEqual(len(first["hits"]), 24)
        self.assertEqual(first["next_page"], 24)
        last = dispatch_reading(self.store, dict(action="search_document", document_id=doc["id"], query="search-batch", start_page=24))
        self.assertEqual(len(last["hits"]), 6)
        self.assertIsNone(last["next_page"])
        with self.assertRaises(AppError): self.call("search_document", query=" ")

    def test_pdf_links_are_display_coordinates_and_safe(self):
        for rotation in (0, 90, 180, 270):
            source = self.root / f"links-{rotation}.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=600, height=800)
                pdf.new_page()
                page = pdf[0]
                for n, uri in enumerate(("https://example.com/", "javascript:alert(1)", "file:///etc/passwd")):
                    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(50, 60+n*25, 150, 80+n*25), "uri": uri})
                page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(50, 160, 150, 180), "page": 1})
                page.set_rotation(rotation); pdf.save(source)
            doc = self.store.import_pdf(source)
            data = dispatch_reading(self.store, dict(action="page_text", document_id=doc["id"], page=0))
            self.assertEqual(len(data["links"]), 2)
            self.assertEqual(data["links"][0]["url"], "https://example.com/")
            self.assertEqual(data["links"][1]["page"], 1)
            with pymupdf.open(source) as pdf:
                expected = pdf[0].get_links()[0]["from"]
                self.assertAlmostEqual(data["links"][0]["rect"]["x"], expected.x0, places=3)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, action, **values):
        request = dict(action=action, document_id=self.doc["id"], variant=self.variant)
        request.update(values)
        return dispatch_reading(self.store, request)

    def bind(self):
        return self.call("annotations")["source_sha256"]

    def add(self, kind="note", page=0, note="My note", **values):
        annotation = dict(kind=kind, page=page, note=note)
        annotation.update(values)
        return self.call("save_annotation", source_sha256=self.bind(), annotation=annotation)

    def translated(self):
        attempt, work = self.store.new_attempt(self.doc["id"])
        self.store.mark_running(self.doc["id"], attempt)
        folder = work / "output"
        folder.mkdir()
        mono = make_pdf(folder / "translated.pdf", rotation=90)
        dual = folder / "bilingual.pdf"
        with pymupdf.open(mono) as source, pymupdf.open() as combined:
            combined.insert_pdf(source)
            combined.insert_pdf(source)
            combined.save(dual)
        atomic_json(folder / "manifest.json", dict(protocolVersion=1, jobId="reading-test", attemptId=attempt,
            sourceSHA256=self.doc["input_sha256"], sourcePages=2, translatedSHA256=sha256(mono), bilingualSHA256=sha256(dual),
            qualityGuard=dict(guardInstalled=True, layoutGuard=dict(guardInstalled=True), unresolvedCount=0,
                              preparedParagraphs=1, validatedParagraphs=1)))
        self.doc = self.store.commit_result(self.doc["id"], attempt,
            dict(type="result", attemptId=attempt, jobId="reading-test", translatedPath=str(mono), bilingualPath=str(dual)))

    def test_navigation_has_depth_and_zero_based_destinations(self):
        navigation = self.call("document_navigation")
        self.assertEqual(navigation["entries"], [dict(id=0, title="Chapter One", page=0, depth=0),
                                                 dict(id=1, title="Details", page=1, depth=1)])
        self.assertEqual(navigation["page_count"], 2)
        self.assertFalse(navigation["truncated"])
        self.assertEqual(navigation["source_sha256"], self.bind())

    def test_plain_pdf_has_explicit_empty_outline(self):
        with pymupdf.open() as pdf:
            pdf.new_page()
            path = self.root / "no-outline.pdf"
            pdf.save(path)
        self.doc = self.store.import_pdf(path)
        self.assertEqual(self.call("document_navigation")["entries"], [])
        self.assertEqual(self.call("page_text")["tokens"], [])

    def test_large_outline_remains_within_bridge_response_limit(self):
        path = self.root / "large-outline.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.set_toc([[1, "章节" * 120, 1] for _ in range(2000)])
            pdf.save(path)
        self.doc = self.store.import_pdf(path)
        response = self.call("document_navigation")
        self.assertTrue(response["truncated"])
        self.assertGreater(len(response["entries"]), 500)
        self.assertLess(len(json.dumps(response, ensure_ascii=False).encode()), 1_000_000)

    def test_character_selection_matches_rotated_and_cropped_preview(self):
        for rotation in (0, 90, 180, 270):
            path = make_pdf(self.root / f"rotation-{rotation}.pdf", rotation, crop=True)
            self.doc = self.store.import_pdf(path)
            response = self.call("page_text", page=0)
            with pymupdf.open(path) as pdf:
                page = pdf[0]
                self.assertEqual((response["width"], response["height"]), (page.rect.width, page.rect.height))
                rect = pymupdf.Rect(page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]["chars"][0]["bbox"])
                expected = rect * page.rotation_matrix
                actual = response["tokens"][0]["rect"]
                for found, wanted in zip([actual["x"], actual["y"], actual["width"], actual["height"]],
                                         [expected.x0, expected.y0, expected.width, expected.height]):
                    self.assertAlmostEqual(found, wanted, places=3)
            text = "".join(t["text"] for t in response["tokens"])
            self.assertIn("Select text", text)
            self.assertFalse(response["truncated"])
            self.assertGreater(len({t["line"] for t in response["tokens"]}), 1)

    def test_text_payload_has_explicit_budget_and_truncation(self):
        with patch("pagebridge.reading.MAX_TOKENS", 5):
            response = self.call("page_text", page=0)
        self.assertEqual(len(response["tokens"]), 5)
        self.assertTrue(response["truncated"])
        self.assertLess(len(json.dumps(response, ensure_ascii=False).encode()), 1_000_000)

    def test_chinese_text_is_selectable_as_individual_characters(self):
        path = self.root / "chinese.pdf"
        sentence = "中文可逐字选择"
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 80), sentence, fontname="china-s", fontsize=16)
            pdf.save(path)
        self.doc = self.store.import_pdf(path)
        response = self.call("page_text", page=0)
        self.assertEqual([token["text"] for token in response["tokens"]], list(sentence))
        self.assertEqual(len({token["line"] for token in response["tokens"]}), 1)
        self.assertTrue(all(token["rect"]["width"] > 0 for token in response["tokens"]))

    def test_note_create_edit_delete_and_reopen_preserve_source(self):
        before = sha256(self.doc["input_path"])
        result = self.add(note="我的第一条笔记")
        note = result["annotations"][0]
        self.assertEqual(note["page"], 0)
        self.store = DocumentStore(self.store.root)
        self.assertEqual(self.call("annotations"), result)
        change = copy.deepcopy(note)
        change["note"] = "修改后的笔记"
        edited = self.call("save_annotation", source_sha256=result["source_sha256"], annotation=change)
        self.assertEqual(edited["annotations"][0]["note"], "修改后的笔记")
        self.assertEqual(edited["annotations"][0]["created_at"], note["created_at"])
        self.assertGreaterEqual(edited["annotations"][0]["modified_at"], note["modified_at"])
        deleted = self.call("delete_annotation", source_sha256=result["source_sha256"], annotation_id=note["id"])
        self.assertEqual(deleted["annotations"], [])
        self.assertEqual(sha256(self.doc["input_path"]), before)

    def test_bookmark_summary_and_duplicate_guard(self):
        result = self.add("bookmark", page=1)
        self.assertEqual(result["bookmarks"], [1])
        with self.assertRaisesRegex(AppError, "已有书签"):
            self.add("bookmark", page=1)
        self.assertEqual(len(self.call("annotations")["annotations"]), 1)

    def test_original_translation_and_retranslation_keep_separate_revisions(self):
        original = self.add(note="Original note")
        self.translated()
        self.variant = "translated"
        self.assertEqual(self.call("annotations")["annotations"], [])
        translated = self.add(note="Translation note")
        self.assertNotEqual(translated["source_sha256"], original["source_sha256"])
        self.translated()
        self.assertEqual(self.call("annotations")["annotations"], [])
        with self.assertRaisesRegex(AppError, "内容已变化"):
            self.call("save_annotation", source_sha256=translated["source_sha256"], annotation=dict(kind="note", page=0, note="Stale"))
        self.variant = "original"
        self.assertEqual(self.call("annotations")["annotations"][0]["note"], "Original note")
        old = self.store.root / "annotations" / self.doc["id"] / "translated" / (translated["source_sha256"] + ".json")
        self.assertTrue(old.is_file())

    def test_invalid_geometry_types_pages_and_stale_hash_do_not_write(self):
        base = dict(kind="highlight", page=0, regions=[dict(page=0, rect=dict(x=30, y=30, width=20, height=15))])
        cases = [dict(base, page=-1), dict(base, page=True), dict(base, regions=[]), dict(base, kind="../../outside"), dict(base, kind=[]),
                 dict(base, regions=[dict(page=2, rect=dict(x=1, y=1, width=4, height=4))]),
                 dict(base, regions=[dict(page=0, rect=dict(x=590, y=10, width=50, height=4))]),
                 dict(base, regions=[dict(page=0, rect=dict(x=1, y=1, width=float("nan"), height=4))]),
                 dict(base, regions=[dict(page=0, rect=dict(x=True, y=1, width=4, height=4))])]
        for value in cases:
            with self.assertRaises(AppError):
                self.call("save_annotation", source_sha256=self.bind(), annotation=value)
        with self.assertRaisesRegex(AppError, "内容已变化"):
            self.call("save_annotation", source_sha256="0" * 64, annotation=dict(kind="note", page=0))
        self.assertEqual(self.call("annotations")["annotations"], [])
        for number in (-1, True, 2):
            with self.assertRaises(AppError): self.call("page_text", page=number)
        with self.assertRaises(AppError): self.call("annotations", variant="../original")
        with self.assertRaises(AppError): self.call("annotations", variant=[])
        with self.assertRaises(AppError): self.call([])

    def test_full_snapshot_size_guard_is_atomic(self):
        self.add(note="Keep")
        path = next((self.store.root / "annotations").rglob("*.json"))
        before = path.read_bytes()
        with patch("pagebridge.reading.MAX_ANNOTATION_BYTES", len(before) + 10):
            with self.assertRaisesRegex(AppError, "批注较多"):
                self.add(note="x" * 8000)
        self.assertEqual(path.read_bytes(), before)

    def test_corrupt_store_is_not_silently_overwritten(self):
        self.add()
        path = next((self.store.root / "annotations").rglob("*.json"))
        path.write_text('{"version":99}', encoding="utf-8")
        with self.assertRaisesRegex(AppError, "未覆盖"):
            self.add(note="Must not overwrite")
        self.assertEqual(path.read_text(), '{"version":99}')

    def test_annotated_export_preserves_source_and_imported_annotations_at_all_rotations(self):
        for rotation in (0, 90, 180, 270):
            path = make_pdf(self.root / f"export-rotation-{rotation}.pdf", rotation, crop=True)
            self.doc = self.store.import_pdf(path)
            text = self.call("page_text", page=0)
            rect = text["tokens"][0]["rect"]
            regions = [dict(page=0, rect=rect)]
            self.add("highlight", regions=regions, selected_text="S", note="Highlight note")
            self.add("underline", regions=regions, note="Underline note")
            result = self.add("note", regions=regions, note="Editable popup")
            self.add("bookmark", page=1)
            before = sha256(self.doc["input_path"])
            destination = self.root / f"annotated-{rotation}.pdf"
            export = self.call("export_annotations", source_sha256=result["source_sha256"], destination=str(destination))
            self.assertEqual(export["count"], 4)
            self.assertEqual(sha256(self.doc["input_path"]), before)
            with pymupdf.open(path) as original:
                original_pixels = original[0].get_pixmap().samples
            with pymupdf.open(destination) as pdf:
                self.assertEqual(pdf.page_count, 2)
                page = pdf[0]
                annotations = list(page.annots())
                self.assertEqual(len(annotations), 4)
                self.assertEqual([a.type[1] for a in annotations], ["Text", "Highlight", "Underline", "Text"])
                self.assertEqual(annotations[0].info["content"], "Imported annotation is preserved")
                expected = pymupdf.Rect(rect["x"], rect["y"], rect["x"] + rect["width"], rect["y"] + rect["height"]) * page.derotation_matrix
                vertices = annotations[1].vertices
                self.assertAlmostEqual(min(v[0] for v in vertices), expected.x0, places=3)
                self.assertAlmostEqual(max(v[1] for v in vertices), expected.y1, places=3)
                self.assertNotEqual(page.get_pixmap().samples, original_pixels)
                self.assertEqual(list(pdf[1].annots() or []), [])

    def test_markdown_export_keeps_quote_note_page_and_variant(self):
        result = self.add("note", page=1, note="Interpretation\nNext line", selected_text="Quoted\nparagraph")
        destination = self.root / "notes.md"
        self.call("export_notes", source_sha256=result["source_sha256"], destination=str(destination))
        text = destination.read_text(encoding="utf-8")
        for phrase in ("来源：原文", "第 2 页", "> Quoted\n> paragraph", "Interpretation\nNext line", result["source_sha256"]):
            self.assertIn(phrase, text)

    def test_export_protects_managed_paths_symlinks_and_hardlinks(self):
        digest = self.bind()
        for destination in (self.store.root / "override.pdf", Path(self.doc["input_path"]), self.root / "bad.md"):
            with self.assertRaises(AppError):
                self.call("export_annotations", source_sha256=digest, destination=str(destination))
        with self.assertRaises(AppError): self.call("export_annotations", source_sha256=digest, destination="relative.pdf")
        link = self.root / "hardlink.pdf"
        try:
            os.link(self.doc["input_path"], link)
        except OSError:
            self.skipTest("Hard link unsupported")
        with self.assertRaisesRegex(AppError, "不能覆盖"):
            self.call("export_annotations", source_sha256=digest, destination=str(link))

    @unittest.skipIf(os.name == "nt", "Windows symlink privileges differ")
    def test_redirected_annotation_directory_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.store.root / "annotations").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(AppError, "重定向"):
            self.add()
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
