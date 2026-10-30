# Panorama workflows

Prepared source graphs, not yet opened/rendered in an actual editor. Upload-widget behavior, runtime tensor/preview values and the editor Save/API Export round trip remain unverified.

| File | Purpose |
| --- | --- |
| panorama_editor.json | Two-photo editor graph, optional sockets 3-6 left open |
| panorama_editor_api.json | Matching isolated-editor client API graph |
| panorama_editor_six_photos.json | Six-photo editor graph |
| panorama_editor_six_photos_api.json | Matching six-photo client API graph |

Install/register the original graph-client nodes in a new compatible isolated ComfyUI editor; see the root README for the incomplete runtime setup boundary. Choose original JPEG/PNG inputs inside the graph. For 3-5 photographs, duplicate source-loader nodes and connect additional optional sockets. Every connected source participates. Source/run fields are blank; `allow_new_run=false`. Keep auto-queue off.

The cloud caller submits root `workflow_api.json` (`PhotoPanorama`, node 1) internally. **Do not submit editor client API graphs to the managed worker.** Configure your own backend endpoint/key; no shared deployment or credential is included.

Panorama IMAGE connects to core PreviewImage. White-valid coverage MASK passes through MaskToImage to PreviewImage. Report STRING connects to PreviewAny. Black image pixels can be valid. Preserve authoritative downloaded artifacts/reports; previews may be display derivatives. Graph groups/layout and original-byte sockets are source contracts, not demonstrated manual alignment or seam controls.

The stdlib-only `tools/graph_workflows.py` checks these templates for links, socket types, widgets, IDs and cycles. Run it without `--write` to validate. It is not official JSON Schema validation or a real frontend export test. `--write` regenerates the four JSON files and can replace manual graph edits; do not use it on a manually tuned demo graph.

Official references: [Workflow JSON](https://docs.comfy.org/specs/workflow_json), [API format](https://docs.comfy.org/development/api-development/workflow-api-format), and [ComfyUI source](https://github.com/Comfy-Org/ComfyUI). Host/frontend compatibility must be established before claiming a working demo.
