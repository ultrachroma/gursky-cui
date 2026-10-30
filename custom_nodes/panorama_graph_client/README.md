# Panorama graph client

Source-only ComfyUI custom nodes; actual editor widgets, preview tensors and cloud operation remain unverified. Exact compatible editor/frontend versions are not pinned. No third-party code or runtime is vendored.

`PanoramaOriginalPhoto` uses the core filename combo/upload contract. Its `PANORAMA_SOURCE` socket preserves original JPEG/PNG bytes, hashes and per-source dimensions rather than re-encoding image tensors. Two sources are required; optional sockets 3-6 preserve differing dimensions. Worker validation handles image container, EXIF orientation and alpha. Thumbnail behavior still needs real editor testing.

`PanoramaCloudRun` returns panorama IMAGE `[1,H,W,3]`, white-valid coverage MASK `[1,H,W]`, report STRING, and original-artifact paths. Source selection/hashing is not image processing. Runtime materialization decodes downloaded PNGs; static/mock checks do not exercise it.

Set `COMFY_BASE_URL` to your own HTTPS managed-deployment origin in the backend/editor environment. Blank checked-in defaults fail closed. Provide a backend-only `COMFY_API_KEY` and deliberately enable `PANORAMA_GRAPH_ALLOW_REMOTE=approved` only for approved remote work. Graph `allow_new_run` defaults false; saved run ID and source fields are blank. No account/deployment endpoint or key is supplied.

Optional `PANORAMA_EXPECTED_BUILD_ID` and `PANORAMA_EXPECTED_RELEASE_ID`, set before startup, label intended provenance only; no actual live release attestation occurs. Changing these values changes receipt expectations; retain the same configuration when recovering a run.

Run receipts persist under `.graph_client/runs/<run_id>/`. A completed receipt validates artifact hashes and reuses downloads. A submitted receipt resumes by remote job ID. An uncertain submission, stale lock or mismatched input/endpoint stops for manual reconciliation. Do not delete receipts/locks or use new IDs to bypass ambiguity. SDK idempotency rejects duplicates; it does not replay the first result. Keep auto-queue off; flags are not spending caps.

The adapter submits `PhotoPanorama` node 1 with original-byte file handles and six filename slots; root `workflow_api.json` describes the worker. Editor client graphs must not be sent directly to that worker. Required authoritative artifacts are panorama PNG, coverage PNG and report JSON. PNG-embedded report fallback source and mock tests are included; independent review and cloud preservation/publication validation remain pending. Missing/inconsistent report provenance fails closed.

Read `../../workflows/README.md` for graphs and root README for source limits, separate environments and offline checks. Preserve downloaded authoritative artifacts rather than saving only preview derivatives. Crop, control-point and seam-editing tools are not included.
