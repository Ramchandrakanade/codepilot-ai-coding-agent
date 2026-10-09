import io
import zipfile
from unittest.mock import patch

from flask import Flask, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import app.main as main


def make_project_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project/app.py", "VALUE = 1")
    buffer.seek(0)
    return buffer


def test_agent_endpoint_enforces_rate_limit(monkeypatch):
    limiter = main.limiter
    limiter.reset()
    monkeypatch.setattr(limiter, "enabled", True)
    monkeypatch.setattr(main, "run_agent", lambda *a, **kw: {"ok": True})

    try:
        client = main.app.test_client()
        statuses = [
            client.post(
                "/api/agent/run",
                json={"task": "please update this project"},
            ).status_code
            for _ in range(6)
        ]

        assert statuses[:5] == [200] * 5
        assert statuses[5] == 429
    finally:
        limiter.reset()


def test_upload_endpoint_enforces_rate_limit(monkeypatch, tmp_path):
    limiter = main.limiter
    limiter.reset()
    monkeypatch.setattr(limiter, "enabled", True)
    monkeypatch.setattr(main, "UPLOAD_ROOT", tmp_path)

    try:
        client = main.app.test_client()
        statuses = []

        for _ in range(6):
            response = client.post(
                "/api/project/upload",
                data={"project": (make_project_zip(), "project.zip")},
                content_type="multipart/form-data",
            )
            statuses.append(response.status_code)

        assert statuses[:5] == [200] * 5
        assert statuses[5] == 429
    finally:
        limiter.reset()


def test_in_memory_fallback_enforces_limit_when_redis_is_unavailable():
    app = Flask(__name__)
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        storage_uri="redis://127.0.0.1:1",
        default_limits=[],
        in_memory_fallback=["3 per minute"],
        in_memory_fallback_enabled=True,
        storage_options={
            "socket_connect_timeout": 0.2,
            "socket_timeout": 0.2,
        },
        swallow_errors=False,
    )

    @app.get("/probe")
    @limiter.limit("100 per minute")
    def probe():
        return jsonify(ok=True)

    client = app.test_client()
    statuses = [client.get("/probe").status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]
