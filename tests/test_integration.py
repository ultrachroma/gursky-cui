"""Opt-in processing tests, prepared only. Run in an approved isolated environment.

PANORAMA_TEST_FIXTURES must name approved fixture directories overlap, nonoverlap,
mixed and disconnected. mixed must include differing dimensions, EXIF orientation
and transparency. These tests execute processing; do not run without approval.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("panorama_node", ROOT / "custom_nodes/panorama/node.py")
node = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(node)
FIXTURES = os.environ.get("PANORAMA_TEST_FIXTURES")


@unittest.skipUnless(FIXTURES, "Requires approved processing and PANORAMA_TEST_FIXTURES")
class PhotographicIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.inputs, self.outputs = self.base / "input", self.base / "output"
        self.inputs.mkdir()
        self.outputs.mkdir()
        self.folder_stub = SimpleNamespace(get_input_directory=lambda: str(self.inputs),
                                           get_output_directory=lambda: str(self.outputs))

    def stage(self, case):
        files = sorted(p for p in (Path(FIXTURES) / case).iterdir()
                       if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"})
        self.assertTrue(2 <= len(files) <= 6, "Fixture must contain 2–6 photos")
        names, hashes = {}, {}
        for i, path in enumerate(files, 1):
            target = self.inputs / f"{case}_{i}{path.suffix.lower()}"
            shutil.copyfile(path, target)
            hashes[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
            names[f"photo_{i}"] = target.name
        return names, hashes

    def run_node(self, names):
        with patch.dict(sys.modules, {"folder_paths": self.folder_stub}):
            return node.PhotoPanorama().execute(crop=True, **names)

    def assert_success_artifacts(self, result, hashes):
        images = result["ui"]["images"]
        directory = self.outputs / images[0]["subfolder"]
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "completed")
        self.assertEqual({r["filename"]: r["sha256"] for r in report["selected"]}, hashes)
        self.assertTrue(all(r["status"] == "used" for r in report["selected"]))
        for filename, expected in hashes.items():
            self.assertEqual(hashlib.sha256((self.inputs / filename).read_bytes()).hexdigest(), expected)
        with Image.open(directory / "panorama.png") as panorama, Image.open(directory / "coverage.png") as coverage:
            panorama.load()
            coverage.load()
            self.assertEqual(panorama.size, coverage.size)
            self.assertLessEqual(panorama.width * panorama.height, node.MAX_OUTPUT_PIXELS)
            self.assertEqual(json.loads(panorama.info["panorama_report"]), report)
            mask = np.asarray(coverage)
            self.assertTrue(set(np.unique(mask)).issubset({0, 255}))
            self.assertGreater(np.count_nonzero(mask), 0)
        x, y, w, h = report["crop_xywh"]
        self.assertGreater(w * h, 0)
        self.assertTrue(mask[y:y+h, x:x+w].all())
        with Image.open(directory / "crop.png") as cropped:
            cropped.load()
            self.assertEqual(cropped.size, (w, h))
        return directory, report

    def test_overlap_and_unique_export_reload(self):
        names, hashes = self.stage("overlap")
        first, _ = self.assert_success_artifacts(self.run_node(names), hashes)
        second, _ = self.assert_success_artifacts(self.run_node(names), hashes)
        self.assertNotEqual(first, second)
        self.assertTrue((first / "panorama.png").is_file())

    def test_mixed_sizes_orientation_and_transparency(self):
        names, hashes = self.stage("mixed")
        _, report = self.assert_success_artifacts(self.run_node(names), hashes)
        self.assertGreater(len({tuple(r["original_size"]) for r in report["selected"]}), 1)
        self.assertTrue(any(r["exif_orientation"] in (6, 8) for r in report["selected"]))
        # Fixture author must include transparent palette/RGBA content; inspect it
        # directly rather than asserting that the output's dark pixels are holes.
        alpha_found = False
        for name in hashes:
            with Image.open(self.inputs / name) as source:
                alpha_found |= source.convert("RGBA").getextrema()[3][0] < 255
        self.assertTrue(alpha_found, "mixed fixture must exercise transparent source pixels")

    def test_nonoverlap_fails(self):
        names, _ = self.stage("nonoverlap")
        with self.assertRaisesRegex(node.PanoramaError, "features|overlap|Disconnected|disconnected|geometry|Camera"):
            self.run_node(names)

    def test_disconnected_inputs_never_silently_dropped(self):
        names, hashes = self.stage("disconnected")
        self.assertGreaterEqual(len(names), 3)
        with self.assertRaises(node.PanoramaError):
            self.run_node(names)
        reports = list(self.outputs.glob("panorama/*/report.json"))
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "failed")
        self.assertEqual({r["filename"] for r in report["selected"]}, set(hashes))
        self.assertTrue(all(r["status"] == "omitted" for r in report["selected"]))


if __name__ == "__main__":
    unittest.main()
