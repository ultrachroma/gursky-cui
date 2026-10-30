"""Bounded rotation panorama pipeline; authored for this project.

All input files are cloud-worker input assets, never client filesystem paths.
OpenCV detail APIs provide registration, projection, exposure, seams and blending.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import uuid

import cv2 as cv
import numpy as np
from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo


class PanoramaError(ValueError):
    """Actionable input or geometry failure."""


MAX_PHOTOS = 6
MAX_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_INPUT_PIXELS = 24_000_000
MAX_TOTAL_PIXELS = 72_000_000
WORK_PIXELS = 1_200_000
MAX_OUTPUT_PIXELS = 12_000_000
MAX_SIDE = 16_000
MAX_ESTIMATED_BYTES = 1_500_000_000


def resolve_inputs(names, input_root):
    """Resolve only worker input files; reject output/temp annotations and links out."""
    root = Path(input_root).resolve()
    selected = [s for s in names if s and s.strip()]
    if not 2 <= len(selected) <= MAX_PHOTOS:
        raise PanoramaError("Select 2 to 6 overlapping photographs.")
    paths = []
    total = 0
    for name in selected:
        name = name.strip()
        if name.endswith(" [input]"):
            name = name[:-8]
        if name.endswith((" [output]", " [temp]")) or "\x00" in name:
            raise PanoramaError("Only uploaded input assets are accepted.")
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise PanoramaError("Input asset is missing or outside the worker input directory.")
        size = path.stat().st_size
        if size <= 0 or size > MAX_BYTES:
            raise PanoramaError("Each source must contain 1 byte to 25 MiB.")
        total += size
        paths.append(path)
    if total > MAX_TOTAL_BYTES:
        raise PanoramaError("Selected files exceed the 100 MiB total limit.")
    if len(set(paths)) != len(paths):
        raise PanoramaError("Duplicate source files are not a panorama set.")
    return paths


def load_sources(paths):
    """Header checks for every file precede expensive pixel decode."""
    headers = []
    total = 0
    for path in paths:
        with Image.open(path) as im:
            width, height = im.size
            if im.format not in {"JPEG", "PNG", "WEBP", "TIFF"}:
                raise PanoramaError("Supported formats: JPEG, PNG, WebP, single-frame TIFF.")
            if getattr(im, "n_frames", 1) != 1:
                raise PanoramaError("Animated and multi-frame files are not supported.")
            pixels = width * height
            if min(width, height) < 64 or pixels > MAX_INPUT_PIXELS:
                raise PanoramaError("Each image must be at least 64 pixels per side and at most 24 MP.")
            total += pixels
            headers.append((width, height))
    if total > MAX_TOTAL_PIXELS:
        raise PanoramaError("Selected images exceed 72 million decoded pixels.")
    images, masks, records = [], [], []
    for path, dimensions in zip(paths, headers):
        with Image.open(path) as source:
            orientation = int(source.getexif().get(274, 1))
            icc_present = bool(source.info.get("icc_profile"))
            # Always RGBA: also respects palette and RGB transparency metadata.
            oriented = ImageOps.exif_transpose(source).convert("RGBA")
            scale = min(1.0, math.sqrt(WORK_PIXELS / (oriented.width * oriented.height)))
            if scale < 1:
                oriented = oriented.resize((max(1, round(oriented.width * scale)),
                                            max(1, round(oriented.height * scale))),
                                           Image.Resampling.LANCZOS)
            rgba = np.asarray(oriented)
            alpha = np.where(rgba[:, :, 3] == 255, 255, 0).astype(np.uint8)
            # Erosion protects interpolated alpha edges and feature descriptors.
            alpha = cv.erode(alpha, np.ones((3, 3), np.uint8))
            if np.count_nonzero(alpha) < 4096:
                raise PanoramaError("A source has insufficient fully opaque image area.")
            images.append(cv.cvtColor(rgba[:, :, :3], cv.COLOR_RGB2BGR))
            masks.append(alpha)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            records.append({"filename": path.name, "sha256": digest.hexdigest(),
                            "original_size": dimensions, "oriented_work_size": oriented.size,
                            "exif_orientation": orientation, "icc_present": icc_present,
                            "status": "used", "working_scale": scale})
    if len({record['sha256'] for record in records}) != len(records):
        raise PanoramaError("Duplicate source content is not a panorama set.")
    return images, masks, records


def validate_camera(camera, image_shape, refined=True):
    h, w = image_shape[:2]
    R = np.asarray(camera.R, np.float32)
    K = np.asarray(camera.K(), np.float32)
    if R.shape != (3, 3) or K.shape != (3, 3) or not np.isfinite(R).all() or not np.isfinite(K).all():
        raise PanoramaError("Camera estimation produced nonfinite geometry.")
    if not .15 * max(w, h) <= camera.focal <= 10 * max(w, h):
        raise PanoramaError("Estimated focal length is unstable; use stronger overlap.")
    if not .5 <= camera.aspect <= 2 or not -w <= camera.ppx <= 2*w or not -h <= camera.ppy <= 2*h:
        raise PanoramaError("Estimated camera intrinsics are implausible.")
    if abs(np.linalg.det(R)) < 1e-8 or np.linalg.cond(R) > 100:
        raise PanoramaError("Estimated camera transform is singular or ill-conditioned.")
    if refined and (not np.allclose(R.T @ R, np.eye(3), atol=.04) or not .9 <= np.linalg.det(R) <= 1.1):
        raise PanoramaError("Estimated camera rotation is invalid.")
    return K, R


def checked_bounds(rois):
    """Preflight all per-image and union warps before allocating any warp."""
    for x, y, w, h in rois:
        if min(w, h) <= 0 or max(w, h) > MAX_SIDE or w*h > MAX_OUTPUT_PIXELS:
            raise PanoramaError("Warp size exceeds safe limits; geometry may be unstable.")
        if max(abs(x), abs(y)) > 4 * MAX_SIDE:
            raise PanoramaError("Warp offset is unstable.")
    x0 = min(r[0] for r in rois)
    y0 = min(r[1] for r in rois)
    x1 = max(r[0]+r[2] for r in rois)
    y1 = max(r[1]+r[3] for r in rois)
    w, h = x1-x0, y1-y0
    estimated = sum(r[2]*r[3]*40 for r in rois) + w*h*80
    if max(w, h) > MAX_SIDE or w*h > MAX_OUTPUT_PIXELS or estimated > MAX_ESTIMATED_BYTES:
        raise PanoramaError("Combined warp exceeds output or conservative memory budget.")
    return x0, y0, w, h


def largest_valid_rectangle(mask):
    """Largest all-covered axis-aligned rectangle, independent of image brightness."""
    heights = [0] * mask.shape[1]
    best = (0, 0, 0, 0)
    area = 0
    for y, row in enumerate(mask):
        heights = [height+1 if value else 0 for height, value in zip(heights, row)]
        stack = []
        for x in range(len(heights)+1):
            height = heights[x] if x < len(heights) else 0
            left = x
            while stack and stack[-1][1] > height:
                start, old = stack.pop()
                if old*(x-start) > area:
                    area = old*(x-start)
                    best = (start, y-old+1, x-start, old)
                left = start
            if not stack or stack[-1][1] < height:
                stack.append((left, height))
    if area == 0:
        raise PanoramaError("No fully covered crop is available.")
    return best


def stitch(images, source_masks, projection="spherical"):
    if projection not in {"spherical", "cylindrical"}:
        raise PanoramaError("Projection must be spherical or cylindrical.")
    finder = cv.SIFT_create(nfeatures=4000)
    features = [cv.detail.computeImageFeatures2(finder, image, mask)
                for image, mask in zip(images, source_masks)]
    for index, feature in enumerate(features):
        feature.img_idx = index
    if any(len(f.keypoints) < 40 for f in features):
        raise PanoramaError("Insufficient features; use sharp textured photographs with overlap.")
    # OpenCV's default upper-confidence duplicate heuristic also drops excellent
    # overlaps. Keep strong edges; reject duplicate content and invalid geometry
    # explicitly instead. Confidence is bounded above by 1 / 0.3.
    matcher = cv.detail.BestOf2NearestMatcher_create(False, .65, 6, 6, 10.0)
    matches = matcher.apply2(features)
    matcher.collectGarbage()
    n = len(images)
    graph = {i: set() for i in range(n)}
    evidence = []
    for match in matches:
        i, j = match.src_img_idx, match.dst_img_idx
        if i < 0 or j < 0 or i == j:
            continue
        good = match.num_inliers >= 20 and match.confidence >= 1.0
        if good:
            H = np.asarray(match.H)
            if H.shape != (3, 3) or not np.isfinite(H).all() or abs(np.linalg.det(H)) < 1e-8:
                raise PanoramaError("Overlap yielded invalid homography.")
            graph[i].add(j)
        if i < j:
            evidence.append({"a": i, "b": j, "inliers": match.num_inliers,
                             "confidence": match.confidence, "accepted": good})
        if not good:
            match.confidence = 0.0
            match.num_inliers = 0
    seen, pending = set(), [0]
    while pending:
        i = pending.pop()
        if i not in seen:
            seen.add(i)
            pending.extend(graph[i]-seen)
    if len(seen) != n:
        raise PanoramaError("Disconnected photo set or insufficient overlap; every selected photo must connect. No inputs were silently omitted.")
    ok, cameras = cv.detail_HomographyBasedEstimator().apply(features, matches, None)
    if not ok or cameras is None or len(cameras) != n:
        raise PanoramaError("Camera estimation failed; try a stronger-overlap rotation set.")
    for camera, image in zip(cameras, images):
        camera.R = np.asarray(camera.R, np.float32)
        validate_camera(camera, image.shape, refined=False)
    adjuster = cv.detail_BundleAdjusterRay()
    adjuster.setConfThresh(1.0)
    adjuster.setRefinementMask(np.array([[1, 1, 1], [0, 1, 1], [0, 0, 0]], np.uint8))
    ok, cameras = adjuster.apply(features, matches, cameras)
    if not ok or cameras is None or len(cameras) != n:
        raise PanoramaError("Camera refinement failed; geometry is unstable.")
    matrices = [validate_camera(c, image.shape) for c, image in zip(cameras, images)]
    residuals = []
    for match in matches:
        i, j = match.src_img_idx, match.dst_img_idx
        if i < 0 or j <= i or j not in graph[i]:
            continue
        Ki, Ri = matrices[i]
        Kj, Rj = matrices[j]
        inverse_i, inverse_j = np.linalg.inv(Ki), np.linalg.inv(Kj)
        for pair, inlier in zip(match.getMatches(), match.getInliers()):
            if not inlier:
                continue
            a = features[i].keypoints[pair.queryIdx].pt
            b = features[j].keypoints[pair.trainIdx].pt
            ray_a = Ri @ inverse_i @ np.array([a[0], a[1], 1.0])
            ray_b = Rj @ inverse_j @ np.array([b[0], b[1], 1.0])
            cosine = float(np.dot(ray_a, ray_b) / (np.linalg.norm(ray_a)*np.linalg.norm(ray_b)))
            residuals.append(math.degrees(math.acos(max(-1.0, min(1.0, cosine)))))
    if not residuals or not np.isfinite(residuals).all() or np.median(residuals) > 3 or np.percentile(residuals, 90) > 6:
        raise PanoramaError("Refined cameras disagree with observed features; unstable geometry or parallax.")
    focal = float(np.median([c.focal for c in cameras]))
    warper = cv.PyRotationWarper(projection, focal)
    rois = [warper.warpRoi((image.shape[1], image.shape[0]), K, R)
            for image, (K, R) in zip(images, matrices)]
    union = checked_bounds(rois)
    corners, warped, masks = [], [], []
    for image, mask, (K, R), roi in zip(images, source_masks, matrices, rois):
        corner, out = warper.warp(image, K, R, cv.INTER_LINEAR, cv.BORDER_CONSTANT)
        mask_corner, outmask = warper.warp(mask, K, R, cv.INTER_LINEAR, cv.BORDER_CONSTANT)
        outmask = np.where(outmask == 255, 255, 0).astype(np.uint8)
        if corner != mask_corner or out.shape[:2] != outmask.shape or out.shape[1::-1] != tuple(roi[2:]):
            raise PanoramaError("Warp output disagrees with checked bounds.")
        corners.append(corner)
        warped.append(out)
        masks.append(outmask)
    x0, y0, width, height = union
    coverage = np.zeros((height, width), np.uint8)
    for (x, y), mask in zip(corners, masks):
        target = coverage[y-y0:y-y0+mask.shape[0], x-x0:x-x0+mask.shape[1]]
        np.maximum(target, mask, out=target)
    compensator = cv.detail.ExposureCompensator_createDefault(cv.detail.ExposureCompensator_GAIN_BLOCKS)
    compensator.feed(corners, warped, masks)
    for i, (corner, image, mask) in enumerate(zip(corners, warped, masks)):
        compensator.apply(i, corner, image, mask)
    seam_masks = [cv.UMat(mask.copy()) for mask in masks]
    seam = cv.detail_DpSeamFinder("COLOR_GRAD")
    seam.find([image.astype(np.float32) for image in warped], corners, seam_masks)
    blender = cv.detail_MultiBandBlender()
    blender.setNumBands(5)
    blender.prepare(union)
    for image, seam_mask, original_mask, corner in zip(warped, seam_masks, masks, corners):
        selected = cv.dilate(seam_mask.get(), np.ones((3, 3), np.uint8))
        selected = cv.bitwise_and(selected, original_mask)
        blender.feed(image.astype(np.int16), selected, corner)
    result, blend_mask = blender.blend(None, None)
    # Coverage is geometric; blend support must cover every claimed valid pixel.
    if np.any((coverage != 0) & (blend_mask == 0)):
        raise PanoramaError("Seam/blending left uncovered source coverage; no incomplete result exported.")
    result = np.clip(result, 0, 255).astype(np.uint8)
    result[coverage == 0] = 0
    diagnostic = {"matches": evidence, "warp_roi": union, "estimated_focal_work_pixels": focal,
                  "angular_residual_median_degrees": float(np.median(residuals)),
                  "angular_residual_p90_degrees": float(np.percentile(residuals, 90)),
                  "cameras": [{"K": K.tolist(), "R": R.tolist()} for K, R in matrices]}
    return result, coverage, diagnostic


class PhotoPanorama:
    CATEGORY = "photography/panorama"
    FUNCTION = "execute"
    RETURN_TYPES = ()
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            **{f"photo_{i}": ("STRING", {"default": ""}) for i in range(1, 7)},
            "crop": ("BOOLEAN", {"default": True}),
        }}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def execute(self, crop=True, **kwargs):
        import folder_paths
        projection = "spherical"
        names = [kwargs.get(f"photo_{i}", "") for i in range(1, 7)]
        paths = resolve_inputs(names, folder_paths.get_input_directory())
        job_id = uuid.uuid4().hex
        output_root = Path(folder_paths.get_output_directory())
        directory = output_root / "panorama" / job_id
        directory.mkdir(parents=True, exist_ok=False)
        report = {"job_id": job_id, "status": "failed", "pipeline": "SDR photographic panorama",
                  "projection": projection, "selected": [{"filename": p.name, "status": "omitted",
                  "reason": "Job has not completed"} for p in paths],
                  "lens_calibration_applied": False, "icc_conversion_applied": False,
                  "warnings": ["Gain compensation does not correct white balance or ICC differences.",
                               "Rotation model may fail with parallax, moving subjects or viewpoint translation.",
                               "Spherical/cylindrical projections may bend straight lines.",
                               "Partial alpha pixels excluded conservatively; processing uses reduced SDR working images."],
                  "versions": {"opencv": cv.__version__, "numpy": np.__version__}}
        try:
            images, masks, records = load_sources(paths)
            panorama, mask, diagnostic = stitch(images, masks, projection)
            Image.fromarray(cv.cvtColor(panorama, cv.COLOR_BGR2RGB)).save(directory / "panorama.png")
            Image.fromarray(mask).save(directory / "coverage.png")
            artifacts = ["panorama.png", "coverage.png"]
            if crop:
                x, y, w, h = largest_valid_rectangle(mask)
                if min(w, h) <= 0 or not np.all(mask[y:y+h, x:x+w]):
                    raise PanoramaError("Computed crop is not fully covered.")
                Image.fromarray(cv.cvtColor(panorama[y:y+h, x:x+w], cv.COLOR_BGR2RGB)).save(directory / "crop.png")
                report["crop_xywh"] = [x, y, w, h]
                artifacts.append("crop.png")
            report.update(status="completed", selected=records, geometry=diagnostic,
                          artifacts=artifacts + ["report.json"])
            metadata = PngInfo()
            metadata.add_text("panorama_report", json.dumps(report))
            Image.fromarray(cv.cvtColor(panorama, cv.COLOR_BGR2RGB)).save(
                directory / "panorama.png", pnginfo=metadata)
        except Exception as exc:
            report["error"] = str(exc)
            (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            raise PanoramaError(f"Panorama failed: {exc}. Diagnostic: panorama/{job_id}/report.json") from exc
        (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        subfolder = f"panorama/{job_id}"
        # File publication on managed Comfy API must be checked in the cloud smoke test.
        return {"ui": {"images": [{"filename": name, "subfolder": subfolder, "type": "output"}
                                   for name in artifacts],
                       "files": [{"filename": "report.json", "subfolder": subfolder, "type": "output"}],
                       "text": [json.dumps(report)]}}



