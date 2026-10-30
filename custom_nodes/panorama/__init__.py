"""Original photographic panorama node. No image generation or gap filling."""
from .node import PhotoPanorama

NODE_CLASS_MAPPINGS = {"PhotoPanorama": PhotoPanorama}
NODE_DISPLAY_NAME_MAPPINGS = {"PhotoPanorama": "Photographic panorama (SDR)"}
