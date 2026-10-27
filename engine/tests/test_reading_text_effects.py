import hashlib
from pathlib import Path
import sys
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reading_text_effects import remove_text_shadows


def masked_image(*, multicolor=False, uniform_alpha=False, mask_alpha=128, width=40, height=20):
    samples = bytearray()
    for y in range(height):
        for x in range(width):
            color = (x * 5, y * 9, 40) if multicolor else (0, 0, 0)
            alpha = mask_alpha if uniform_alpha else (mask_alpha if 2 <= x < width - 2 and 2 <= y < height - 2 else 0)
            samples.extend((*color, alpha))
    return pymupdf.Pixmap(pymupdf.csRGB, width, height, samples, True).tobytes("png")


class TextShadowTests(unittest.TestCase):
    def test_paired_shadow_removed_but_photo_and_unpaired_logo_shadow_remain(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=360, height=240)
            page.insert_text((40, 60), "Title", fontsize=20)
            page.insert_text((40, 140), "Photo", fontsize=20)
            spans = page.get_texttrace()
            first, second = [pymupdf.Rect(s["bbox"]) for s in spans]
            shadow = page.insert_image(first + (-2, -2, 2, 2), stream=masked_image(), keep_proportion=False)
            logo = page.insert_image(pymupdf.Rect(230, 40, 290, 64), stream=masked_image(mask_alpha=127, height=21), keep_proportion=False)
            photo = page.insert_image(second + (-2, -2, 2, 2), stream=masked_image(multicolor=True), keep_proportion=False)
            self.assertNotEqual(logo, shadow)
            before = page.get_pixmap(clip=pymupdf.Rect(225, 35, 295, 70)).samples
            photo_before = page.get_pixmap(clip=second + (-2, -2, 2, 2)).samples
            self.assertEqual(remove_text_shadows(page, [list(first), list(second)]), [shadow])
            self.assertIn(photo, [i[0] for i in page.get_images(full=True)])
            self.assertEqual(page.get_pixmap(clip=pymupdf.Rect(225, 35, 295, 70)).samples, before)
            self.assertEqual(page.get_pixmap(clip=second + (-2, -2, 2, 2)).samples, photo_before)

    def test_uniform_translucent_panel_is_not_a_shadow(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=120)
            page.insert_image(pymupdf.Rect(30, 40, 130, 70), stream=masked_image(uniform_alpha=True), keep_proportion=False)
            before = page.get_pixmap().samples
            self.assertEqual(remove_text_shadows(page, [[30, 40, 130, 70]]), [])
            self.assertEqual(page.get_pixmap().samples, before)

    def test_reused_image_is_kept_when_only_one_occurrence_is_selected(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=180)
            xref = page.insert_image(pymupdf.Rect(30, 40, 130, 70), stream=masked_image(), keep_proportion=False)
            page.insert_image(pymupdf.Rect(160, 100, 260, 130), xref=xref, keep_proportion=False)
            before = page.get_pixmap().samples
            self.assertEqual(remove_text_shadows(page, [[30, 40, 130, 70]]), [])
            self.assertEqual(page.get_pixmap().samples, before)

    def test_identical_base_pixels_with_unpaired_different_mask_are_kept(self):
        # PyMuPDF resolves image placements by their base-pixel digest. If two
        # resources differ only in softmask, ambiguous unpaired pixels must stay.
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=180)
            first = page.insert_image(pymupdf.Rect(30, 40, 130, 70), stream=masked_image(), keep_proportion=False)
            second = page.insert_image(pymupdf.Rect(160, 100, 260, 130), stream=masked_image(mask_alpha=127), keep_proportion=False)
            self.assertNotEqual(first, second)
            before = page.get_pixmap().samples
            self.assertEqual(remove_text_shadows(page, [[30, 40, 130, 70]]), [])
            self.assertEqual(page.get_pixmap().samples, before)

    def test_rotated_page_uses_unrotated_native_boxes(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=120)
            xref = page.insert_image(pymupdf.Rect(30, 40, 130, 70), stream=masked_image(), keep_proportion=False)
            page.set_rotation(90)
            self.assertEqual(remove_text_shadows(page, [[30, 40, 130, 70]]), [xref])

    def test_no_selected_native_text_keeps_all_images(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=120)
            page.insert_image(pymupdf.Rect(30, 40, 130, 70), stream=masked_image(), keep_proportion=False)
            before = page.get_pixmap().samples
            self.assertEqual(remove_text_shadows(page, []), [])
            self.assertEqual(page.get_pixmap().samples, before)

    def test_actual_nasa_title_shadows_leave_photo_and_logo_pixels_unchanged(self):
        source = Path(__file__).resolve().parents[2] / "validation/representative20-20261027/sources/nasa-balloon-2024-slides.pdf"
        if not source.is_file():
            self.skipTest("Public NASA regression corpus not downloaded")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        with pymupdf.open(source) as doc:
            page = doc[0]
            title_boxes = [list(s["bbox"]) for s in page.get_texttrace()
                           if "NASA Balloon Program" in "".join(chr(c[0]) for c in s["chars"])
                           or "".join(chr(c[0]) for c in s["chars"]) == "Overview"]
            logo = page.get_pixmap(clip=pymupdf.Rect(0, 0, 125, 320)).samples
            photo = page.get_pixmap(clip=pymupdf.Rect(130, 200, 950, 520)).samples
            removed = remove_text_shadows(page, title_boxes)
            self.assertEqual(set(removed), {2390, 2392})
            self.assertEqual(page.get_pixmap(clip=pymupdf.Rect(0, 0, 125, 320)).samples, logo)
            self.assertEqual(page.get_pixmap(clip=pymupdf.Rect(130, 200, 950, 520)).samples, photo)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)


if __name__ == "__main__":
    unittest.main()
