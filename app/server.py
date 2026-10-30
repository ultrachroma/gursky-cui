"""Loopback demonstration app. No image stitching runs in this process."""
import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
JOBS = {}
LOCK = threading.Lock()
MAX_FILE = 12 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_BODY = 46 * 1024 * 1024
TOKEN = secrets.token_urlsafe(32)


def deployment_endpoint():
    """Use the same non-secret deployment target as the graph client."""
    override = os.environ.get("COMFY_BASE_URL", "").strip()
    if override:
        return override
    target = ROOT.parent / "custom_nodes" / "panorama_graph_client" / "deployment_target.json"
    return json.loads(target.read_text(encoding="utf-8"))["base_url"]


def readiness():
    try:
        url = urlparse(deployment_endpoint())
        port = url.port
    except ValueError:
        return "Set COMFY_BASE_URL to the approved managed deployment HTTPS origin."
    if (url.scheme != "https" or not re.fullmatch(r"[a-zA-Z0-9-]+\.run\.comfy\.app", url.hostname or "")
            or url.username or url.password or port not in (None, 443)
            or url.path not in ("", "/") or url.query or url.fragment):
        return "Set COMFY_BASE_URL to the approved managed deployment HTTPS origin."
    if not os.environ.get("COMFY_API_KEY"):
        return "Configure an approved backend API key."
    if os.environ.get("PANORAMA_ALLOW_REMOTE_RUN") != "approved":
        return "Remote upload/run disabled. Obtain approval and set PANORAMA_ALLOW_REMOTE_RUN=approved."
    return None


def validate_photos(items):
    """Inspect container headers only; preserve original bytes, metadata and alpha."""
    from PIL import Image
    if not isinstance(items, list) or not 2 <= len(items) <= 6:
        raise ValueError("Select 2–6 overlapping photos.")
    result, pixels, size = [], 0, 0
    for number, item in enumerate(items, 1):
        if not isinstance(item, dict) or not isinstance(item.get("base64"), str):
            raise ValueError("Invalid photo payload.")
        if len(item["base64"]) > (MAX_FILE * 4 // 3 + 8):
            raise ValueError("Each photo must be at most 12 MiB.")
        try:
            content = base64.b64decode(item["base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid photo encoding.") from exc
        size += len(content)
        if not content or len(content) > MAX_FILE or size > MAX_TOTAL:
            raise ValueError("Photo limit: 12 MiB each, 32 MiB total.")
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {"JPEG", "PNG"} or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Only single-frame JPEG and PNG inputs are supported.")
            width, height = image.size
            pixels += width * height
            if min(width, height) < 64 or max(width, height) > 12000 or width * height > 24_000_000 or pixels > 72_000_000:
                raise ValueError("Photo dimensions exceed input safety limits.")
            suffix = ".jpg" if image.format == "JPEG" else ".png"
        result.append((f"photo_{number}{suffix}", content))
    return result


def update(job_id, **values):
    with LOCK:
        JOBS[job_id].update(values)
        state = dict(JOBS[job_id])
    path = DATA / job_id / "state.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(path)


def execute(job_id, names):
    """Only the official managed Comfy deployment processes photographs."""
    try:
        from comfy_sdk import Comfy
        os.environ["COMFY_BASE_URL"] = deployment_endpoint()
        client = Comfy(api_key=os.environ["COMFY_API_KEY"])
        workflow = client.workflows.from_file(str(ROOT.parent / "workflow_api.json"))
        for index, name in enumerate(names, 1):
            asset = client.assets.from_file(str(DATA / job_id / name))
            workflow.set_input("1", f"photo_{index}", asset)
        update(job_id, state="uploading", message="Uploading original photo bytes to the approved managed deployment.")
        # Exactly one submit. Ambiguous network failures must be reconciled manually.
        job = client.submit(workflow, idempotency_key=job_id)
        update(job_id, state="running", remote_job_id=job.id, message="Cloud stitch running.")
        job.result()
        update(job_id, state="downloading", message="Retrieving authenticated job artifacts.")
        artifacts = []
        for index, output in enumerate(job.get_outputs("1")):
            original = Path(output.name).name
            if Path(original).suffix.lower() not in {".png", ".json"}:
                continue
            safe = f"{index}_{re.sub(r'[^A-Za-z0-9_.-]', '_', original)}"
            output.to_file(str(DATA / job_id / safe))
            artifacts.append({"name": original, "url": f"/artifacts/{job_id}/{safe}"})
        # PNG text survives authenticated retrieval even if ui.files is not indexed.
        if not any(a["name"] == "report.json" for a in artifacts):
            from PIL import Image
            for artifact in artifacts:
                if artifact["name"] == "panorama.png":
                    with Image.open(DATA / job_id / artifact["url"].split("/")[-1]) as image:
                        if image.width * image.height > 24_000_000 or max(image.size) > 16000:
                            raise ValueError("Downloaded panorama exceeds expected output bounds.")
                        report = image.info.get("panorama_report")
                    if report:
                        parsed = json.loads(report)
                        (DATA / job_id / "report.json").write_text(json.dumps(parsed, indent=2), encoding="utf-8")
                        artifacts.append({"name": "report.json", "url": f"/artifacts/{job_id}/report.json"})
                    break
        required = {"panorama.png", "coverage.png", "report.json"}
        present = {a["name"] for a in artifacts}
        missing = sorted(required - present)
        if missing:
            update(job_id, state="incomplete", artifacts=artifacts,
                   message="Cloud finished, but required artifacts were not published: " + ", ".join(missing)
                   + ". Verify custom output publication with the deployment; do not resubmit blindly.")
        else:
            update(job_id, state="succeeded", artifacts=artifacts, message="Cloud artifacts retrieved.")
    except Exception as exc:
        # Only surface known actionable categories; arbitrary transport messages may
        # contain signed URLs or provider state and must not reach the browser.
        category = type(exc).__name__
        hints = {"Unauthorized": "Check the approved backend key and account eligibility.",
                 "InsufficientCredits": "Review available credit and the approved spending cap.",
                 "QueueFull": "Cloud queue is full; verify that no job was created before retrying.",
                 "JobFailed": "Cloud node failed. Inspect node-level details in the platform for overlap, disconnected inputs, geometry or safety-limit errors.",
                 "ModuleNotFoundError": "Install the approved app requirements into an isolated environment first."}
        update(job_id, state="failed", message=f"{category}: {hints.get(category, 'Submission/execution/retrieval failed.')} Inspect the recorded remote job in the platform before retrying; no automatic resubmit.")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_bytes(self, status, content, kind):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def reply(self, status, value):
        self.send_bytes(status, json.dumps(value).encode(), "application/json")

    def valid_host(self):
        return self.headers.get("Host") in {"127.0.0.1:8765", "localhost:8765"}

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {"error": "Invalid host."})
        path = urlparse(self.path).path
        if path == "/api/config":
            return self.reply(200, {"disabled_reason": readiness(), "token": TOKEN})
        if path.startswith("/api/jobs/"):
            with LOCK:
                state = JOBS.get(path.split("/")[-1])
                state = dict(state) if state else None
            return self.reply(200 if state else 404, state or {"error": "Unknown job; restart recovery is manual using app/data/*/state.json."})
        if path.startswith("/artifacts/"):
            parts = path.split("/")
            if len(parts) != 4 or not re.fullmatch(r"[a-f0-9]{32}", parts[2]) or not re.fullmatch(r"[A-Za-z0-9_.-]+", parts[3]):
                return self.reply(404, {"error": "Unknown artifact."})
            with LOCK:
                job = JOBS.get(parts[2], {})
                allowed = {a["url"] for a in job.get("artifacts", [])}
            if path not in allowed:
                return self.reply(404, {"error": "Unknown artifact."})
            target = DATA / parts[2] / parts[3]
            return self.send_bytes(200, target.read_bytes(), "image/png" if target.suffix == ".png" else "application/json")
        files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if path not in files:
            return self.reply(404, {"error": "Not found."})
        name, kind = files[path]
        self.send_bytes(200, (ROOT / "static" / name).read_bytes(), kind)

    def do_POST(self):
        if not self.valid_host() or self.headers.get("X-Panorama-Token") != TOKEN:
            return self.reply(403, {"error": "Invalid local session."})
        if self.headers.get("Origin") not in {"http://127.0.0.1:8765", "http://localhost:8765"}:
            return self.reply(403, {"error": "Invalid origin."})
        if self.path != "/api/jobs":
            return self.reply(404, {"error": "Not found."})
        reason = readiness()
        if reason:
            return self.reply(503, {"error": reason})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("Request exceeds 46 MiB limit.")
            payload = json.loads(self.rfile.read(length))
            photos = validate_photos(payload.get("photos"))
            with LOCK:
                if any(j["state"] in {"accepted", "uploading", "running", "downloading"} for j in JOBS.values()):
                    return self.reply(409, {"error": "One job already active. Wait for it to finish."})
                job_id = secrets.token_hex(16)
                target = DATA / job_id
                target.mkdir(parents=True, exist_ok=False)
                for name, content in photos:
                    (target / name).write_bytes(content)
                JOBS[job_id] = {"id": job_id, "state": "accepted", "message": "Validated original photos.", "artifacts": [], "inputs": [{"name": n, "original_name": str(payload["photos"][i].get("name", ""))[:256], "sha256": hashlib.sha256(c).hexdigest()} for i, (n, c) in enumerate(photos)]}
            update(job_id)
            threading.Thread(target=execute, args=(job_id, [name for name, _ in photos]), daemon=True).start()
            self.reply(202, {"id": job_id})
        except Exception as exc:
            self.reply(400, {"error": str(exc) if isinstance(exc, ValueError) else "Input validation failed. Use valid single-frame JPEG/PNG photographs."})


if __name__ == "__main__":
    print("Panorama scaffold: http://127.0.0.1:8765 — cloud execution disabled unless explicitly approved.")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()

