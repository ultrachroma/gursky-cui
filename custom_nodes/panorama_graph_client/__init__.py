"""Isolated graph-client source. Not installed into any ComfyUI environment."""
from .nodes import PanoramaCloudRun, PanoramaOriginalPhoto

NODE_CLASS_MAPPINGS = {
    'PanoramaOriginalPhoto': PanoramaOriginalPhoto,
    'PanoramaCloudRun': PanoramaCloudRun,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    'PanoramaOriginalPhoto': 'Original photograph (preserve bytes)',
    'PanoramaCloudRun': 'Photographic panorama (managed endpoint)',
}
