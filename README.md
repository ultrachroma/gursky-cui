# Panorama Cloud

Original photographic panorama worker, ComfyUI editor graph client, and optional loopback app. Source is published at [ultrachroma/gursky-cui](https://github.com/ultrachroma/gursky-cui) under the root MIT license. One private six-photo managed cloud run and its in-graph results have been verified. A public demo recording and verified contest submission remain pending.

The OpenCV worker registers 2-6 overlapping photographs, refines cameras, applies spherical warping, exposure gains, seams and multiband blending, and writes panorama, geometric coverage and provenance. No generative fill, HDR, manual alignment or seam editing. Black image pixels can be valid; white coverage means covered.

## Evidence and limits

Historical local validation recorded 14 processing/safety tests, synthetic geometry/exposure assessments and one public sample set. A managed Linux build imported the worker successfully. An isolated CPU editor loaded both blank graph sizes, rendered upload widgets and expanded output panels, and passed Save, reload and API Export checks; exported classes and inputs matched the authored API graphs.

On October 30, 2026, exactly one private six-photo managed cloud run completed. Original-asset binding, cloud stitching, authenticated output retrieval and tensor materialization were verified for that run. All six ordered original SHA256 values matched the retained source snapshots, receipt and direct JSON report; every source was marked used. All three authoritative output hashes matched the receipt. Panorama and binary white-valid coverage both decoded at 3692 x 922; geometric coverage was 93.0262536%. The direct JSON report status was completed.

The actual local frontend decoded both panorama and coverage previews at 3692 x 922 and populated the completed report in the graph. The existing history Load action did not restore the recorded graph correctly; restoring the same saved history workflow and outputs directly succeeded without execution. A private overview found a coherent photographic result; this is not full-resolution seam certification or a public demo recording. This verifies one run, not general reliability or future service availability.

Ordered source SHA256 provenance (the example photos are not bundled; their public redistribution license has not been established):

| Source order | SHA256 |
| --- | --- |
| 1 | `c084dab5e87d0c7a018fc6ab75e847621f0809b20a968c42bfb5d58b81303f2e` |
| 2 | `3bb4e222a5879d2555c3f1109e6b77284b418f378db02e6e86cc414f46fb33ad` |
| 3 | `dd3c5c15bff43782039aeeff19361251ab9d60b3fb6d1195df6b7dc357e33e65` |
| 4 | `be8392f53a624426de2ce918dd803e55236e79cf7bb573338d7b684ba950848f` |
| 5 | `076fcf028c3078e9f7b612c380a6c75d6e08109e74835ac5147a5a1960e5fc68` |
| 6 | `48f20d18f28c9156bf516e7f8726fe3ea60ac378fa1b198c71bc2d4edf5c4e34` |

| Authoritative output | SHA256 |
| --- | --- |
| panorama.png | `fc7f973afc515ca617823813426fdcc708444e4b7f74c149f97ee4fd4f9121b6` |
| coverage.png | `71c76a57a3f157786daf30c969facb60d3bba5c0c1083a7965f82ae3b7a4090a` |
| report.json | `8fbda0d8db9f77da99321112dfe80b6efec0a7b3714023c5d71cecd5fedd8264` |

Hashes record provenance; the corresponding private photos, outputs and report are not published here. The report fallback source and mock tests are included. No images, results, credentials, environments, operational account snapshots or private Git history are included.

## Dependencies and setup

Use Python 3.12 and separate isolated environments. This published setup recipe has not completed a clean-machine install. Its isolated-editor ComfyUI/frontend/torch versions remain unpinned: setup below is a preparation recipe, not a verified reproducible runtime. See [third-party references](THIRD_PARTY_NOTICES.md). Obtain external host/dependencies directly from their upstream sources; none are vendored.

Worker dependencies, in a new worker-only environment:

```powershell
py -3.12 -m venv .venv-worker
.\.venv-worker\Scripts\python.exe -m pip install -r custom_nodes/panorama/requirements.txt
```

Worker pins: opencv-python-headless 4.12.0.88, NumPy 2.2.6, Pillow 11.3.0. Register `custom_nodes/panorama` in a separately prepared ComfyUI worker, and verify node registration before use. [ComfyUI upstream setup](https://github.com/Comfy-Org/ComfyUI#installing) and [managed deployment documentation](https://docs.comfy.org/development/api-development/overview) describe external host setup; this package does not create a deployment or supply account-specific build/upload resources.

Editor/client: prepare a new isolated ComfyUI editor, register `custom_nodes/panorama_graph_client`, and install `comfy-sdk==0.5.0` into that editor's environment after resolving compatibility with its NumPy/Pillow/torch dependencies. Do not combine worker and app requirement sets blindly: Pillow pins differ. Verify the actual host/frontend versions and upload/preview node contracts; a validated version matrix is still missing.

Set `COMFY_BASE_URL` in the editor/backend environment to your own managed deployment HTTPS origin. The checked-in default record is blank and fails closed. Optional `PANORAMA_EXPECTED_BUILD_ID` and `PANORAMA_EXPECTED_RELEASE_ID`, set before process startup, label intended provenance only; they do not attest the live deployment. Supply your own `COMFY_API_KEY` through a backend-side secret mechanism; never put it in graphs, browser JavaScript, commits or chat. This repository includes no shared public deployment.

The remote interlock `PANORAMA_GRAPH_ALLOW_REMOTE=approved` and graph toggle `allow_new_run=true` enable a new cloud submission. Enable them only for an intentional run within your approved costs and photo-upload scope. They are not spending caps. Keep auto-queue off and retain the default `allow_new_run=false` for review. No new run is needed to review an existing completed run with its retained receipt and artifacts. Reuse durable run IDs to retrieve completed results; reconcile uncertain submissions instead of inventing another ID or deleting receipts.

## Workflows

Open [workflows/panorama_editor.json](workflows/panorama_editor.json) in the isolated editor for two photos, or the six-photo variant. Original-byte loaders feed the cloud caller; panorama, white-valid coverage and report feed in-graph previews. Optional slots support 3-5 photos by duplicating loaders. Both blank templates passed editor Save, reload and API Export checks without execution. Actual panorama/coverage frontend decoding and completed-report population were verified for the single six-photo run described above.

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
