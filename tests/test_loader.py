"""Synthetic loader regressions: original bytes, EXIF and PNG transparency."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import numpy as np
from PIL import Image

SPEC = importlib.util.spec_from_file_location('panorama_loader', Path(__file__).resolve().parents[1]/'custom_nodes/panorama/node.py')
node = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(node)

class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_duplicate_content_rejected_despite_different_names(self):
        first, second = self.root/'a.png', self.root/'b.png'
        Image.new('RGB', (100, 100), 'white').save(first)
        second.write_bytes(first.read_bytes())
        with self.assertRaisesRegex(node.PanoramaError, 'Duplicate source content'):
            node.load_sources([first, second])

    def test_palette_and_rgb_transparency_and_valid_black(self):
        paths = []
        for index, mode in enumerate(('P', 'RGB')):
            image = Image.new(mode, (100, 100), 0)
            if mode == 'P':
                image.putpalette([0,0,0, 255,0,0] + [0]*762)
                image.putpixel((50, 50), 1); transparency = 1
            else:
                image.putpixel((50, 50), (255,0,0)); transparency = (255,0,0)
            path = self.root/f'{index}.png'; image.save(path, transparency=transparency); paths.append(path)
        images, masks, records = node.load_sources(paths)
        for image, mask in zip(images, masks):
            self.assertEqual(int(mask[50,50]), 0)
            self.assertEqual(int(mask[10,10]), 255)
            self.assertEqual(int(image[10,10].sum()), 0)
        self.assertEqual(len(records), 2)

    def test_exif_rotation_preserves_source_bytes(self):
        path = self.root/'rotated.jpg'
        exif = Image.Exif(); exif[274] = 6
        Image.new('RGB', (100, 140), 'white').save(path, exif=exif)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        images, masks, records = node.load_sources([path])
        self.assertEqual(images[0].shape[:2], (100, 140))
        self.assertEqual(records[0]['exif_orientation'], 6)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

if __name__ == '__main__':
    unittest.main()
