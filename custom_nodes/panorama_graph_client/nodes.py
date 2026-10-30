"""Graph-facing source chooser and cloud runner; imports have no runtime side effects."""
from pathlib import Path

from .adapter import CloudRunner, SDKTransport
from .connection import configured_endpoint
from .sources import select_source


class PanoramaOriginalPhoto:
    CATEGORY = 'photography/panorama/cloud client'
    FUNCTION = 'load'
    RETURN_TYPES = ('PANORAMA_SOURCE',)
    RETURN_NAMES = ('original_source',)
    DESCRIPTION = 'Select original JPEG/PNG bytes. The thumbnail is not the stitching input.'

    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths
        root = Path(folder_paths.get_input_directory())
        names = sorted(p.relative_to(root).as_posix() for p in root.rglob('*')
                       if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg', '.png'})
        return {'required': {'image': (names, {'image_upload': True})}}

    @classmethod
    def IS_CHANGED(cls, image):
        import folder_paths
        return select_source(folder_paths.get_input_directory(), image).sha256

    def load(self, image):
        import folder_paths
        return (select_source(folder_paths.get_input_directory(), image),)


def materialize_images(artifacts):
    """Runtime-only conversion of downloaded artifacts; originals are never decoded here.

    IMAGE is float32 [1,H,W,3]; coverage MASK is float32 [1,H,W], one=valid.
    This path requires separately approved image-processing/UI validation.
    """
    from PIL import Image
    import numpy as np
    import torch
    with Image.open(artifacts.panorama) as panorama, Image.open(artifacts.coverage) as coverage:
        width, height = panorama.size
        if (panorama.format != 'PNG' or coverage.format != 'PNG'
                or panorama.size != coverage.size or min(width, height) <= 0
                or max(width, height) > 16000 or width * height > 12_000_000):
            raise ValueError('Downloaded image dimensions or formats violate the output contract.')
        rgb = np.array(panorama.convert('RGB'), dtype=np.float32) / 255.0
        mask = np.array(coverage.convert('L'), dtype=np.uint8)
        if np.any((mask != 0) & (mask != 255)):
            raise ValueError('Coverage must be binary, with white indicating valid pixels.')
        return torch.from_numpy(rgb).unsqueeze(0), torch.from_numpy(mask.astype(np.float32) / 255.0).unsqueeze(0)


class PanoramaCloudRun:
    CATEGORY = 'photography/panorama/cloud client'
    FUNCTION = 'execute'
    RETURN_TYPES = ('IMAGE', 'MASK', 'STRING', 'PANORAMA_ARTIFACTS')
    RETURN_NAMES = ('panorama', 'coverage_white_is_valid', 'report_json', 'original_artifacts')
    OUTPUT_NODE = True
    DESCRIPTION = 'Runs managed PhotoPanorama. Reuse a run ID to recover results; new runs require approval.'

    def __init__(self, runner=None, materialize=None):
        self.runner = runner
        self.materialize = materialize or materialize_images

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'run_id': ('STRING', {'default': ''}),
            'allow_new_run': ('BOOLEAN', {'default': False}),
            'photo_1': ('PANORAMA_SOURCE',), 'photo_2': ('PANORAMA_SOURCE',),
        }, 'optional': {f'photo_{i}': ('PANORAMA_SOURCE',) for i in range(3, 7)}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Comfy cache is not a submission interlock. Persisted receipts are authoritative.
        return float('nan')

    def execute(self, run_id, allow_new_run, photo_1, photo_2, **optional):
        sources = [photo_1, photo_2]
        for index in range(3, 7):
            key = f'photo_{index}'
            if key in optional:
                # A connected invalid source must fail, never silently disappear.
                sources.append(optional[key])
        runner = self.runner
        if runner is None:
            endpoint = configured_endpoint()
            root = Path(__file__).resolve().parents[2] / '.graph_client' / 'runs'
            runner = CloudRunner(root, endpoint, lambda: SDKTransport(endpoint))
        artifacts = runner.run(run_id, sources, allow_new_run=allow_new_run)
        panorama, coverage = self.materialize(artifacts)
        return {'result': (panorama, coverage, artifacts.report_text, artifacts),
                'ui': {'text': ['Managed panorama retrieved. Coverage: white=valid. '
                                'Original downloaded files and report retained in the run receipt directory.']}}
