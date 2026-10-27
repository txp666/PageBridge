"""Native paths and parser boxes must share the displayed page's local origin."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import structured_reading as reading


class DecorationCoordinatesTests(unittest.TestCase):
    def test_native_paths_remove_visible_origin_after_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            for rotation in (0, 90, 180, 270):
                for cropped in (False, True):
                    with self.subTest(rotation=rotation, cropped=cropped):
                        path = Path(folder) / f"source-{rotation}-{cropped}.pdf"
                        with pymupdf.open() as pdf:
                            page = pdf.new_page(width=630, height=810)
                            page.draw_line((80, 120), (260, 120), color=(1, 0, 0), width=2)
                            page.draw_rect((310, 300, 370, 360), color=None, fill=(0, 0, 1))
                            if cropped:
                                page.set_cropbox(pymupdf.Rect(30, 40, 580, 730))
                            else:
                                pdf.xref_set_key(page.xref, "MediaBox", "[18 18 612 792]")
                                pdf.xref_set_key(page.xref, "CropBox", "[0 0 630 810]")
                            page = pdf.reload_page(page)
                            page.set_rotation(rotation)
                            pdf.save(path)
                        digest = hashlib.sha256(path.read_bytes()).hexdigest()
                        result = {}
                        reading.add_decorations(path, 1, result)
                        self.assertEqual(result["decorationsCoordinateSpace"], "page")
                        with pymupdf.open(path) as pdf:
                            page = pdf[0]
                            origin = page.rect.tl
                            line = page.get_drawings()[0]["items"][0]
                            expected = [point * page.rotation_matrix - origin for point in line[1:3]]
                        commands = result["decorations"][0]["commands"]
                        self.assertEqual(commands, [dict(op="M", points=list(expected[0])),
                                                    dict(op="L", points=list(expected[1]))])
                        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
