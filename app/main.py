import os
import re
from pathlib import Path
import stat
import shutil
import tempfile
import uuid
import zipfile

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

from app.agent.orchestrator import run_agent

app = Flask(__name__)

# Require shared storage on Render; allow memory storage for local development.
REDIS_URL = os.getenv("REDIS_URL", "").strip()
IS_RENDER = os.getenv("RENDER", "").lower() == "true"

if IS_RENDER and not REDIS_URL:
    raise RuntimeError(
        "REDIS_URL must be configured on Render before the application can start."
    )

RATE_LIMIT_STORAGE = REDIS_URL or "memory://"
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=RATE_LIMIT_STORAGE,
    default_limits=[],
    headers_enabled=True,
    in_memory_fallback=["3 per minute"],
    in_memory_fallback_enabled=True,
    swallow_errors=False,
)
if not REDIS_URL:
    app.logger.warning(
        "REDIS_URL is not configured; rate limits use per-process memory storage."
    )

app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

UPLOAD_ROOT = Path(tempfile.gettempdir()) / "codepilot_uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    return response


@app.errorhandler(429)
def rate_limit_exceeded(_error):
    return jsonify({"error": "Too many requests. Please try again later."}), 429


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "Request is too large."}), 413



@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "CodePilot AI"})


@app.post("/api/project/upload")
@limiter.limit("5 per minute; 20 per hour")
def project_upload():
    uploaded = request.files.get("project")

    if not uploaded or not uploaded.filename:
        return jsonify({"error": "Please select a ZIP project file."}), 400
    if not uploaded.filename.lower().endswith(".zip"):
        return jsonify({"error": "Only .zip project files are supported."}), 400

    upload_id = uuid.uuid4().hex
    workspace = UPLOAD_ROOT / upload_id
    zip_path = workspace / "project.zip"
    extract_root = workspace / "project"

    try:
        workspace.mkdir(parents=True, exist_ok=False)
        uploaded.save(zip_path)
        extract_root.mkdir()

        with zipfile.ZipFile(zip_path, "r") as archive:
            members = archive.infolist()
            if len(members) > 1000:
                raise ValueError("ZIP contains too many entries.")

            total_size = sum(m.file_size for m in members if not m.is_dir())
            if total_size > 100 * 1024 * 1024:
                raise ValueError("ZIP expands beyond the 100 MB limit.")

            seen = set()
            root_resolved = extract_root.resolve()

            extracted_size = 0

            for member in members:
                name = member.filename.replace("\\", "/")
                parts = name.split("/")
                if member.is_dir() and name.endswith("/"):
                    parts = parts[:-1]

                if (
                    not name or name.startswith("/") or not parts
                    or ":" in parts[0]
                    or any(part in ("", "..") for part in parts)
                ):
                    raise ValueError("Unsafe ZIP path rejected.")

                target = (extract_root / Path(*parts)).resolve()
                if target != root_resolved and root_resolved not in target.parents:
                    raise ValueError("Unsafe ZIP path rejected.")

                key = str(Path(*parts)).casefold()
                if key in seen:
                    raise ValueError("ZIP contains duplicate paths.")
                seen.add(key)

                mode = (member.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise ValueError("ZIP symbolic links are not supported.")
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError("ZIP contains an unsupported file type.")


            for member in members:
                name = member.filename.replace("\\", "/")
                parts = name.split("/")
                if member.is_dir() and name.endswith("/"):
                    parts = parts[:-1]
                target = extract_root.joinpath(*parts)

                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as src, target.open("xb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        extracted_size += len(chunk)
                        if extracted_size > 100 * 1024 * 1024:
                            raise ValueError("ZIP expands beyond the 100 MB limit.")
                        dst.write(chunk)

        zip_path.unlink(missing_ok=True)
        entries = list(extract_root.iterdir())
        project_root = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root

        return jsonify({
            "upload_id": upload_id,
            "project_name": project_root.name,
            "message": "Project uploaded successfully."
        })

    except zipfile.BadZipFile:
        shutil.rmtree(workspace, ignore_errors=True)
        return jsonify({"error": "The uploaded file is not a valid ZIP."}), 400
    except ValueError as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        return jsonify({"error": str(exc)}), 400
    except Exception:
        import logging
        logging.exception("Project ZIP upload failed")
        shutil.rmtree(workspace, ignore_errors=True)
        return jsonify({"error": "Project upload failed. Please try again."}), 500


@app.post("/api/agent/run")
@limiter.limit("5 per minute; 30 per hour")
def agent_run():
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    upload_id = payload.get("upload_id")
    if upload_id is not None and (
        not isinstance(upload_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", upload_id)
    ):
        return jsonify({"error": "Uploaded project was not found."}), 400

    task = payload.get("task")
    if not isinstance(task, str):
        return jsonify({"error": "Task must be a string."}), 400

    task = task.strip()
    if not task:
        return jsonify({"error": "Please enter a task."}), 400
    if len(task) > 2000:
        return jsonify({"error": "Task must not exceed 2000 characters."}), 400

    try:
        project_root = "sample_project"

        if upload_id is not None:
            upload_root = UPLOAD_ROOT.resolve()
            workspace = (upload_root / upload_id).resolve()
            candidate = (workspace / "project").resolve()

            if (
                workspace.parent != upload_root
                or upload_root not in candidate.parents
                or not candidate.is_dir()
            ):
                return jsonify({"error": "Uploaded project was not found."}), 400

            entries = list(candidate.iterdir())
            project_root = str(
                entries[0]
                if len(entries) == 1 and entries[0].is_dir()
                else candidate
            )

        return jsonify(
            run_agent(
                task,
                project_root,
                trusted_project=upload_id is None,
            )
        )
    except Exception:
        import logging
        logging.exception("Agent run failed")
        return jsonify({
            "error": "Agent run failed. Please check the task and try again."
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
