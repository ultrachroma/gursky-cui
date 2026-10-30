# Panorama Cloud

Original photographic panorama worker, ComfyUI editor graph client, and optional loopback app. This is a source-only release candidate, not an established working contest demo. Publication approval and copyright-owner assent remain pending. The root MIT license is unchanged.

The OpenCV worker registers 2-6 overlapping photographs, refines cameras, applies spherical warping, exposure gains, seams and multiband blending, and writes panorama, geometric coverage and provenance. No generative fill, HDR, manual alignment or seam editing. Black image pixels can be valid; white coverage means covered.

## Evidence and limits

Historical local validation recorded 14 processing/safety tests, synthetic geometry/exposure assessments and one public sample set. A managed Linux build imported the worker successfully. These results do not establish actual editor widgets, frontend rendering, tensor materialization, cloud original-asset binding, authenticated artifact publication or cloud stitching. The report fallback source and mock tests are included; independent review and live verification remain pending. No images, results, credentials, environments, operational account snapshots or Git history are included.

## Dependencies and setup

Use Python 3.12 and separate isolated environments. This candidate has not completed a clean-machine install. The exact compatible isolated-editor ComfyUI/frontend/torch versions are still unpinned: setup below is a preparation recipe, not a tested complete runtime. See [third-party references](THIRD_PARTY_NOTICES.md). Obtain external host/dependencies directly from their upstream sources; none are vendored.

Worker dependencies, in a new worker-only environment:

```powershell
py -3.12 -m venv .venv-worker
.\.venv-worker\Scripts\python.exe -m pip install -r custom_nodes/panorama/requirements.txt
```

Worker pins: opencv-python-headless 4.12.0.88, NumPy 2.2.6, Pillow 11.3.0. Register `custom_nodes/panorama` in a separately prepared ComfyUI worker, and verify node registration before use. [ComfyUI upstream setup](https://github.com/Comfy-Org/ComfyUI#installing) and [managed deployment documentation](https://docs.comfy.org/development/api-development/overview) describe external host setup; this package does not create a deployment or supply account-specific build/upload resources.

Editor/client: prepare a new isolated ComfyUI editor, register `custom_nodes/panorama_graph_client`, and install `comfy-sdk==0.5.0` into that editor's environment after resolving compatibility with its NumPy/Pillow/torch dependencies. Do not combine worker and app requirement sets blindly: Pillow pins differ. Verify the actual host/frontend versions and upload/preview node contracts; a validated version matrix is still missing.

Set `COMFY_BASE_URL` in the editor/backend environment to your own managed deployment HTTPS origin. The checked-in default record is blank and fails closed. Optional `PANORAMA_EXPECTED_BUILD_ID` and `PANORAMA_EXPECTED_RELEASE_ID`, set before process startup, label intended provenance only; they do not attest the live deployment. Supply your own `COMFY_API_KEY` through a backend-side secret mechanism; never put it in graphs, browser JavaScript, commits or chat. This repository includes no shared public deployment.

The remote interlock `PANORAMA_GRAPH_ALLOW_REMOTE=approved` and graph toggle `allow_new_run=true` enable a new cloud submission. Enable them only for an intentional run within your approved costs and photo-upload scope. They are not spending caps. Keep auto-queue off. Reuse durable run IDs to retrieve completed results; reconcile uncertain submissions instead of inventing another ID or deleting receipts.

## Workflows

Open [workflows/panorama_editor.json](workflows/panorama_editor.json) in the isolated editor for two photos, or the six-photo variant. Original-byte loaders feed the cloud caller; panorama, white-valid coverage and report feed in-graph previews. Optional slots support 3-5 photos by duplicating loaders. The editor Save/API Export round trip and actual previews remain unverified.

`workflows/*_api.json` are client graphs for the editor. They must not be submitted directly to the managed worker. Root `workflow_api.json` is the worker graph (`PhotoPanorama`, node 1); the client constructs that worker request internally. Read [workflow contracts](workflows/README.md) and [client behavior](custom_nodes/panorama_graph_client/README.md).

Sources must be static-scene, overlapping photos captured from approximately one viewpoint. Client accepts 2-6 JPEG/PNG originals, at most 12 MiB each / 32 MiB total. Worker permits 24 MP per source / 72 MP total and composes at bounded resolution (12 MP output maximum). Preserve downloaded originals, coverage and reports; preview derivatives are not authoritative. Parallax, motion, weak overlap and repeating textures can fail or ghost. No ICC/lens-calibration or full-resolution archival guarantees.

## Offline checks

From this candidate root, use an already available Python interpreter; no installation is necessary for the stdlib/mock suites:

```powershell
python -B static_check.py
python -B tools/graph_workflows.py
python -B -m unittest discover -s tests -p test_graph_client.py -v
python -B -m unittest discover -s tests -p test_editor_workflow.py -v
python -B -m unittest discover -s tests -p test_deployment_connection.py -v
python -B -m unittest discover -s tests -p test_graph_report_fallback.py -v
```

These checks use opaque bytes, fake transports and socket denial; they do not prove image processing, live credentials, editor UX or cloud operation. Other processing suites are included as source but are not run by this recipe.

Optional loopback app: [app/README.md](app/README.md). It is a separate scaffold, not in-graph demo evidence. A public demo video, social link, final reproducible editor setup and verified contest submission are not yet available.
