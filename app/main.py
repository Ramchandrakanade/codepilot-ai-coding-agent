from pathlib import Path
import stat
import shutil
import tempfile
import uuid
import zipfile

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

from app.agent.orchestrator import run_agent

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

UPLOAD_ROOT = Path(tempfile.gettempdir()) / "codepilot_uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "CodePilot AI"})


@app.post("/api/project/upload")
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
def agent_run():
    payload = request.get_json(silent=True) or {}
    try:
        task = payload.get("task", "")
        upload_id = payload.get("upload_id")
        project_root = "sample_project"

        if upload_id:
            candidate = (UPLOAD_ROOT / str(upload_id) / "project").resolve()
            expected_parent = (UPLOAD_ROOT / str(upload_id)).resolve()
            if expected_parent not in candidate.parents or not candidate.is_dir():
                return jsonify({"error": "Uploaded project was not found."}), 400

            entries = list(candidate.iterdir())
            project_root = str(entries[0] if len(entries) == 1 and entries[0].is_dir() else candidate)

        return jsonify(run_agent(task, project_root))
    except Exception:
        import logging
        logging.exception("Agent run failed")
        return jsonify({"error": "Agent run failed. Please check the task and try again."}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
