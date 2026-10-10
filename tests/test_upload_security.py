from unittest.mock import patch

import app.main as main


def test_agent_run_rejects_traversal_upload_id():
    client = main.app.test_client()

    response = client.post(
        "/api/agent/run",
        json={"task": "test", "upload_id": "..\\..\\outside"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Uploaded project was not found."


def test_agent_run_rejects_invalid_upload_id():
    client = main.app.test_client()

    response = client.post(
        "/api/agent/run",
        json={"task": "test", "upload_id": "not-a-valid-id"},
    )

    assert response.status_code == 400


def test_agent_run_rejects_missing_upload_workspace(tmp_path):
    client = main.app.test_client()
    missing_id = "a" * 32

    with patch.object(main, "UPLOAD_ROOT", tmp_path):
        response = client.post(
            "/api/agent/run",
            json={"task": "test", "upload_id": missing_id},
        )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Uploaded project was not found."

def test_uploaded_project_validation_is_skipped(tmp_path):
    from app.agent.validator import run_validation

    project = tmp_path / "uploaded"
    project.mkdir()
    (project / "test_untrusted.py").write_text(
        "raise RuntimeError('This test must never execute')",
        encoding="utf-8",
    )

    changes = [{"path": "app.py", "content": "VALUE = 1"}]

    with patch("app.agent.validator.subprocess.run") as run:
        result = run_validation(
            root=str(project),
            changes=changes,
            trusted_project=False,
        )

    run.assert_not_called()
    assert result["status"] == "skipped"
    assert result["passed"] is None
    assert "sandbox" in result["output"].lower()


def test_trusted_project_validation_skips_execution(tmp_path, monkeypatch):
    from unittest.mock import patch
    from app.agent.validator import run_validation

    monkeypatch.delenv("CODEPILOT_ENABLE_E2B", raising=False)

    project = tmp_path / "trusted"
    project.mkdir()
    (project / "test_ok.py").write_text("def test_ok(): assert True")

    with patch("app.agent.validator.subprocess.run") as run:
        result = run_validation(
            root=str(project),
            changes=[{"path": "app.py", "content": "VALUE = 1"}],
            trusted_project=True,
        )

    run.assert_not_called()
    assert result["status"] == "skipped"
    assert result["passed"] is None


def test_api_marks_uploaded_project_as_untrusted(tmp_path):
    client = main.app.test_client()
    upload_id = "b" * 32
    project = tmp_path / upload_id / "project"
    project.mkdir(parents=True)
    (project / "app.py").write_text("VALUE = 1", encoding="utf-8")

    with (
        patch.object(main, "UPLOAD_ROOT", tmp_path),
        patch.object(main, "run_agent", return_value={"ok": True}) as run_agent,
    ):
        response = client.post(
            "/api/agent/run",
            json={"task": "update app", "upload_id": upload_id},
        )

    assert response.status_code == 200
    assert run_agent.call_args.kwargs["trusted_project"] is False


def test_api_marks_bundled_project_as_sandbox_eligible():
    client = main.app.test_client()

    with patch.object(
        main, "run_agent", return_value={"ok": True}
    ) as run_agent:
        response = client.post(
            "/api/agent/run",
            json={"task": "update app"},
        )

    assert response.status_code == 200
    assert run_agent.call_args.kwargs["trusted_project"] is True


import io
import zipfile


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    buffer.seek(0)
    return buffer


def test_zip_upload_accepts_normal_project():
    client = main.app.test_client()
    archive = make_zip([
        ("project/app.py", "VALUE = 1"),
        ("project/test_app.py", "def test_ok():\n    assert True\n"),
    ])

    response = client.post(
        "/api/project/upload",
        data={"project": (archive, "project.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["upload_id"]


def test_zip_upload_rejects_path_traversal():
    client = main.app.test_client()
    archive = make_zip([("../outside.txt", "must not be written")])

    response = client.post(
        "/api/project/upload",
        data={"project": (archive, "malicious.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "Unsafe ZIP path" in response.get_json()["error"]


def test_zip_upload_rejects_duplicate_paths():
    client = main.app.test_client()
    archive = make_zip([
        ("project/app.py", "first"),
        ("project/app.py", "second"),
    ])

    response = client.post(
        "/api/project/upload",
        data={"project": (archive, "duplicate.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "duplicate paths" in response.get_json()["error"].lower()


def test_zip_upload_rejects_symbolic_links():
    import stat

    client = main.app.test_client()
    buffer = io.BytesIO()

    info = zipfile.ZipInfo("project/link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, "../outside.txt")

    buffer.seek(0)

    response = client.post(
        "/api/project/upload",
        data={"project": (buffer, "symlink.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "symbolic links" in response.get_json()["error"].lower()


def test_zip_upload_rejects_too_many_entries():
    client = main.app.test_client()
    archive = make_zip([
        (f"project/file_{i}.txt", "x")
        for i in range(1001)
    ])

    response = client.post(
        "/api/project/upload",
        data={"project": (archive, "many-files.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "too many entries" in response.get_json()["error"].lower()


def test_zip_upload_rejects_expansion_over_100_mb():
    client = main.app.test_client()
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project/large.txt", b"A" * (100 * 1024 * 1024 + 1))

    buffer.seek(0)

    response = client.post(
        "/api/project/upload",
        data={"project": (buffer, "large.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "100 mb limit" in response.get_json()["error"].lower()

def test_security_headers_are_present():
    client = main.app.test_client()

    for url in ("/", "/api/health"):
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp


def test_agent_run_rejects_invalid_task_payloads():
    client = main.app.test_client()

    cases = [
        [],
        {"task": ""},
        {"task": "   "},
        {"task": "x" * 2001},
        {"task": 123},
    ]

    for payload in cases:
        response = client.post("/api/agent/run", json=payload)
        assert response.status_code == 400


def test_codebase_scanner_excludes_likely_secrets_and_large_files(tmp_path):
    from app.agent.codebase import scan_codebase

    (tmp_path / "app.py").write_text("print('safe')", encoding="utf-8")
    (tmp_path / "credentials.json").write_text(
        '{"key":"placeholder"}', encoding="utf-8"
    )
    (tmp_path / "secrets.py").write_text(
        "EXAMPLE_SECRET = 'placeholder'", encoding="utf-8"
    )
    (tmp_path / ".env").write_text(
        "EXAMPLE_KEY=placeholder", encoding="utf-8"
    )
    (tmp_path / "large.py").write_text(
        "x" * (300 * 1024), encoding="utf-8"
    )

    scanned = scan_codebase(str(tmp_path))
    paths = {item["path"] for item in scanned}

    assert "app.py" in paths
    assert "credentials.json" not in paths
    assert "secrets.py" not in paths
    assert ".env" not in paths
    assert "large.py" not in paths

def test_uploaded_project_invalid_python_fails_static_check(tmp_path):
    from unittest.mock import patch
    from app.agent.validator import run_validation

    project = tmp_path / "uploaded_invalid"
    project.mkdir()

    changes = [
        {"path": "app.py", "content": "def broken(:\n    pass\n"}
    ]

    with patch("app.agent.validator.subprocess.run") as run:
        result = run_validation(
            root=str(project),
            changes=changes,
            trusted_project=False,
        )

    run.assert_not_called()
    assert result["status"] == "failed"
    assert result["passed"] is False
    assert "invalid Python syntax" in result["output"]


def test_uploaded_project_valid_python_still_skips_execution(tmp_path):
    from unittest.mock import patch
    from app.agent.validator import run_validation

    project = tmp_path / "uploaded_valid"
    project.mkdir()

    changes = [
        {"path": "app.py", "content": "VALUE = 1\n"}
    ]

    with patch("app.agent.validator.subprocess.run") as run:
        result = run_validation(
            root=str(project),
            changes=changes,
            trusted_project=False,
        )

    run.assert_not_called()
    assert result["status"] == "skipped"
    assert result["passed"] is None

def test_malformed_patch_path_returns_validation_error(tmp_path):
    from unittest.mock import patch
    from app.agent.validator import run_validation

    changes = [{"path": 7, "content": "VALUE = 1"}]

    with patch("app.agent.validator.subprocess.run") as run:
        result = run_validation(
            root=str(tmp_path),
            changes=changes,
            trusted_project=False,
        )

    run.assert_not_called()
    assert result["status"] == "failed"
    assert "string path" in result["output"]


def test_non_dictionary_patch_returns_validation_error(tmp_path):
    from unittest.mock import patch
    from app.agent.validator import run_validation

    with patch("app.agent.validator.subprocess.run") as run:
        result = run_validation(
            root=str(tmp_path),
            changes=["unexpected patch entry"],
            trusted_project=False,
        )

    run.assert_not_called()
    assert result["status"] == "failed"
    assert "must be an object" in result["output"]

def test_uploaded_non_python_patch_missing_content_fails(tmp_path):
    from unittest.mock import patch
    from app.agent.validator import run_validation

    changes = [{"path": "README.md"}]

    with patch("app.agent.validator.subprocess.run") as run:
        result = run_validation(
            root=str(tmp_path),
            changes=changes,
            trusted_project=False,
        )

    run.assert_not_called()
    assert result["status"] == "failed"
    assert "content must be supplied as text" in result["output"]


def test_uploaded_non_python_patch_none_content_fails(tmp_path):
    from unittest.mock import patch
    from app.agent.validator import run_validation

    changes = [{"path": "README.md", "content": None}]

    with patch("app.agent.validator.subprocess.run") as run:
        result = run_validation(
            root=str(tmp_path),
            changes=changes,
            trusted_project=False,
        )

    run.assert_not_called()
    assert result["status"] == "failed"
    assert "content must be supplied as text" in result["output"]

def test_patch_rejection_cleans_up_temporary_directory(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path
    from app.agent import validator

    project = tmp_path / "cleanup_project"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    created_dirs = []
    original_mkdtemp = tempfile.mkdtemp

    def tracked_mkdtemp(*args, **kwargs):
        path = original_mkdtemp(*args, **kwargs)
        created_dirs.append(Path(path))
        return path

    monkeypatch.setattr(validator.tempfile, "mkdtemp", tracked_mkdtemp)

    invalid_cases = [
        [{"path": "../outside.txt", "content": "test"}],
        ["unexpected patch entry"],
    ]

    for changes in invalid_cases:
        try:
            validator._apply_changes_to_copy(str(project), changes)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid patch was accepted: {changes!r}")

    assert len(created_dirs) == len(invalid_cases)
    assert all(not path.exists() for path in created_dirs)

def test_static_validation_rejects_unsafe_paths(tmp_path):
    from app.agent.validator import run_validation

    unsafe_paths = [
        "../outside.py",
        r"..\outside.py",
        r"C:\outside.py",
        "/outside.py",
        "//server/share/outside.py",
    ]

    for path in unsafe_paths:
        result = run_validation(
            root=str(tmp_path),
            changes=[{"path": path, "content": "VALUE = 1\\n"}],
            trusted_project=False,
        )

        assert result["status"] == "failed", path
        assert "Unsafe patch path rejected" in result["output"], path
