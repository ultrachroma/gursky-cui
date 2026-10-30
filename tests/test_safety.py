"""Prepared for later authorized execution. Imports perform no cloud submission."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("panorama_node", ROOT / "custom_nodes/panorama/node.py")
node = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(node)


class SafetyTests(unittest.TestCase):
    def test_count_rejected_before_filesystem_decode(self):
        for names in (["one.jpg"], [str(i) for i in range(7)]):
            with self.assertRaisesRegex(node.PanoramaError, "2 to 6"):
                node.resolve_inputs(names, ROOT)

    def test_escape_rejected(self):
        with self.assertRaisesRegex(node.PanoramaError, "outside"):
            node.resolve_inputs(["../../escape.jpg", "another.jpg"], ROOT)

    def test_bounds_rejected_before_warp_allocation(self):
        for rois in ([(0, 0, 0, 5)], [(0, 0, 16001, 10)],
                     [(0, 0, 4000, 4000)], [(999999, 0, 10, 10)],
                     [(0, 0, 10000, 1000), (15000, 0, 1000, 1000)]):
            with self.assertRaises(node.PanoramaError):
                node.checked_bounds(rois)

    def test_camera_rejects_nan_and_nonrotation(self):
        for rotation in (np.full((3, 3), np.nan), np.diag([2., 1., 1.])):
            camera = SimpleNamespace(R=rotation, focal=800., aspect=1., ppx=400., ppy=300.,
                                     K=lambda: np.array([[800., 0., 400.], [0., 800., 300.], [0., 0., 1.]]))
            with self.assertRaises(node.PanoramaError):
                node.validate_camera(camera, (600, 800, 3))

    def test_empty_coverage_has_no_crop(self):
        with self.assertRaisesRegex(node.PanoramaError, "No fully covered"):
            node.largest_valid_rectangle(np.zeros((10, 10), np.uint8))

    def test_crop_never_contains_holes(self):
        mask = np.ones((9, 12), np.uint8)
        mask[3:7, 4:8] = 0
        x, y, w, h = node.largest_valid_rectangle(mask)
        self.assertGreater(w * h, 0)
        self.assertTrue(mask[y:y+h, x:x+w].all())

    def test_dark_pixels_do_not_affect_coverage_crop(self):
        # A fully covered, completely black photograph remains valid content.
        black = np.zeros((8, 10, 3), np.uint8)
        mask = np.ones(black.shape[:2], np.uint8)
        self.assertEqual(node.largest_valid_rectangle(mask), (0, 0, 10, 8))


if __name__ == "__main__":
    unittest.main()
