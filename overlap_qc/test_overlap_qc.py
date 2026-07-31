import tempfile
import unittest
from pathlib import Path

from PIL import Image

from overlap_qc import Element, canvas_coverage, pixel_collision_ratio


class PixelCollisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.png = Path(self.tmp.name) / "offset-alpha.png"
        image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        for y in range(2, 7):
            for x in range(5, 10):
                image.putpixel((x, y), (255, 255, 255, 255))
        image.save(self.png)

    def tearDown(self):
        self.tmp.cleanup()

    def test_visible_bbox_offset_is_applied_to_alpha_slice(self):
        a = Element("v", "a", "test", str(self.png), 0, 1, 5, 2, 5, 5)
        b = Element("v", "b", "test", str(self.png), 0, 1, 5, 2, 5, 5)
        self.assertEqual(pixel_collision_ratio(a, b, (5, 2, 10, 7)), 1.0)


class CanvasCoverageTests(unittest.TestCase):
    def test_off_canvas_area_does_not_count_as_coverage(self):
        el = Element("v", "full", "test", None, 0, 1, 100, 0, 1920, 1080)
        self.assertAlmostEqual(canvas_coverage(el), 1820 / 1920)

    def test_exact_canvas_is_full_coverage(self):
        el = Element("v", "full", "test", None, 0, 1, 0, 0, 1920, 1080)
        self.assertEqual(canvas_coverage(el), 1.0)


if __name__ == "__main__":
    unittest.main()