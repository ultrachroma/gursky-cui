"""Create deterministic synthetic camera-rotation fixtures; never reads real photos."""
import argparse
import json
from pathlib import Path
import cv2 as cv
import numpy as np
from PIL import Image


def texture(seed):
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 256, (1200, 2400, 3), dtype=np.uint8)
    image = cv.GaussianBlur(image, (5, 5), 1)
    for _ in range(2500):
        x, y = rng.integers([0, 0], [2400, 1200])
        color = tuple(int(c) for c in rng.integers(0, 256, 3))
        cv.circle(image, (int(x), int(y)), int(rng.integers(2, 14)), color, -1)
    # Include opaque black content; coverage must not derive from RGB intensity.
    cv.rectangle(image, (1100, 550), (1220, 650), (0, 0, 0), -1)
    return image


def render(world, yaw, width=800, height=600, focal=700):
    yy, xx = np.indices((height, width), dtype=np.float32)
    rays = np.stack(((xx-width/2)/focal, (yy-height/2)/focal, np.ones_like(xx)), axis=-1)
    angle = np.deg2rad(yaw)
    rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
    rays = rays @ rotation.T
    longitude = np.arctan2(rays[..., 0], rays[..., 2])
    latitude = np.arctan2(rays[..., 1], np.hypot(rays[..., 0], rays[..., 2]))
    mx = ((longitude/(2*np.pi)+.5)*world.shape[1]).astype(np.float32)
    my = ((latitude/np.pi+.5)*world.shape[0]).astype(np.float32)
    return cv.remap(world, mx, my, cv.INTER_LINEAR, borderMode=cv.BORDER_WRAP)


def generate(root):
    root.mkdir(parents=True, exist_ok=False)
    for case in ('overlap', 'mixed', 'nonoverlap', 'disconnected'):
        (root/case).mkdir()
    world = texture(391)
    for index, yaw in enumerate((-18, 0, 18)):
        Image.fromarray(render(world, yaw)).save(root/'overlap'/f'{index}.png')
    Image.fromarray(render(world, -18)).save(root/'mixed'/'0.png')
    # Stored rotated counterclockwise, orientation=6 rotates it back clockwise.
    oriented = Image.fromarray(render(world, 0, 900, 650, 760))
    exif = Image.Exif(); exif[274] = 6
    oriented.transpose(Image.Transpose.ROTATE_90).save(root/'mixed'/'1.jpg', exif=exif, quality=98)
    rgba = Image.fromarray(render(world, 18, 840, 620, 730)).convert('RGBA')
    alpha = np.full((620, 840), 255, np.uint8)
    alpha[:15, :] = 0; alpha[:, :15] = 0
    rgba.putalpha(Image.fromarray(alpha)); rgba.save(root/'mixed'/'2.png')
    for index in range(2):
        Image.fromarray(render(texture(800+index), 0)).save(root/'nonoverlap'/f'{index}.png')
    for index, yaw in enumerate((-18, 0)):
        Image.fromarray(render(world, yaw)).save(root/'disconnected'/f'{index}.png')
    Image.fromarray(render(texture(912), 0)).save(root/'disconnected'/'2.png')
    (root/'fixture_provenance.json').write_text(json.dumps({'kind':'synthetic only', 'seed':391,
        'projection':'pinhole views of a procedural equirectangular texture', 'yaw_degrees':[-18,0,18],
        'not_evidence':'No real photographs or cloud runtime tested.'}, indent=2))
    print(root)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('destination', type=Path)
    generate(parser.parse_args().destination)
