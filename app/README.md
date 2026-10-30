# Optional loopback app scaffold

This original external UI is separate from the ComfyUI editor graph. It has not been launched or verified against a live deployment. It is not a working-demo claim.

In a separate new Python 3.12 environment, install `app/requirements.txt` (comfy-sdk 0.5.0 and Pillow 12.0.0), set `COMFY_BASE_URL` to your own managed deployment origin, and provide your own backend `COMFY_API_KEY` through a secret mechanism. The checked-in target is blank and fails closed. `app/.env.example` contains placeholders only; do not commit a real `.env`.

Run `python app/server.py` from the repository root and open http://127.0.0.1:8765. Remote runs remain disabled unless `PANORAMA_ALLOW_REMOTE_RUN=approved`. That flag is not a spending cap. Use only deliberately approved photographs and run costs. Keep the app on loopback; this scaffold is not a multi-user or publicly hosted service. Preserve run/job IDs and reconcile ambiguous submission errors rather than retrying under a new ID.

The app preserves original-file bytes for the worker, retrieves artifacts backend-side, and has PNG embedded-report fallback logic. Original-asset binding, authenticated downloads and actual previews remain unverified. Do not send backend credentials to browser code or expose environment files. See the root README for limits and evidence boundaries.
