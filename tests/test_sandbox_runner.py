from unittest.mock import MagicMock, patch

from app.agent import sandbox_runner


def test_runner_skips_when_e2b_is_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEPILOT_ENABLE_E2B", raising=False)
    monkeypatch.delenv("E2B_API_KEY", raising=False)

    result = sandbox_runner.run_pytest_in_sandbox(tmp_path)

    assert result["status"] == "skipped"
    assert result["passed"] is None
    assert result["test_command_executed"] is False


def test_runner_reports_actual_pytest_success(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEPILOT_ENABLE_E2B", "1")
    monkeypatch.setenv("E2B_API_KEY", "test-key")
    monkeypatch.setenv("E2B_TEMPLATE_ID", "codepilot-python-tests")
    (tmp_path / "test_example.py").write_text(
        "def test_example(): assert True\n",
        encoding="utf-8",
    )

    sandbox = MagicMock()
    sandbox.commands.run.return_value = MagicMock(
        exit_code=0,
        stdout="1 passed",
        stderr="",
    )

    with patch("e2b.Sandbox.create", return_value=sandbox):
        result = sandbox_runner.run_pytest_in_sandbox(tmp_path)

    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["return_code"] == 0
    assert result["test_command_executed"] is True
    sandbox.kill.assert_called_once()


def test_runner_does_not_report_failure_as_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEPILOT_ENABLE_E2B", "1")
    monkeypatch.setenv("E2B_API_KEY", "test-key")
    monkeypatch.setenv("E2B_TEMPLATE_ID", "codepilot-python-tests")
    (tmp_path / "test_example.py").write_text(
        "def test_example(): assert False\n",
        encoding="utf-8",
    )

    sandbox = MagicMock()
    sandbox.commands.run.return_value = MagicMock(
        exit_code=1,
        stdout="1 failed",
        stderr="",
    )

    with patch("e2b.Sandbox.create", return_value=sandbox):
        result = sandbox_runner.run_pytest_in_sandbox(tmp_path)

    assert result["status"] == "failed"
    assert result["passed"] is False
    assert result["return_code"] == 1
    sandbox.kill.assert_called_once()


def test_runner_skips_if_sandbox_cannot_be_created(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEPILOT_ENABLE_E2B", "1")
    monkeypatch.setenv("E2B_API_KEY", "test-key")

    with patch("e2b.Sandbox.create", side_effect=RuntimeError("unavailable")):
        result = sandbox_runner.run_pytest_in_sandbox(tmp_path)

    assert result["status"] == "skipped"
    assert result["passed"] is None
    assert result["test_command_executed"] is False

def test_runner_does_not_upload_sensitive_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEPILOT_ENABLE_E2B", "1")
    monkeypatch.setenv("E2B_API_KEY", "test-key")
    monkeypatch.setenv("E2B_TEMPLATE_ID", "codepilot-python-tests")

    (tmp_path / "test_example.py").write_text(
        "def test_example(): assert True\n", encoding="utf-8"
    )
    (tmp_path / ".env.production").write_text(
        "SECRET=value", encoding="utf-8"
    )
    (tmp_path / ".env.example").write_text(
        "E2B_API_KEY=placeholder", encoding="utf-8"
    )
    (tmp_path / "credentials.json").write_text(
        '{"password":"placeholder"}', encoding="utf-8"
    )
    (tmp_path / "id_rsa").write_text(
        "private-key-placeholder", encoding="utf-8"
    )
    (tmp_path / "certificate.pem").write_text(
        "certificate-placeholder", encoding="utf-8"
    )

    sandbox = MagicMock()
    sandbox.commands.run.return_value = MagicMock(
        exit_code=0, stdout="1 passed", stderr=""
    )

    with patch("e2b.Sandbox.create", return_value=sandbox):
        result = sandbox_runner.run_pytest_in_sandbox(tmp_path)

    assert result["status"] == "passed"

    uploaded_paths = [
        call.args[0] for call in sandbox.files.write.call_args_list
    ]
    assert uploaded_paths == [
        "/tmp/codepilot_project/test_example.py"
    ]


def test_runner_rejects_oversized_file_before_creating_sandbox(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEPILOT_ENABLE_E2B", "1")
    monkeypatch.setenv("E2B_API_KEY", "test-key")
    monkeypatch.setenv("E2B_TEMPLATE_ID", "codepilot-python-tests")

    (tmp_path / "test_example.py").write_text(
        "def test_example(): assert True\n", encoding="utf-8"
    )
    (tmp_path / "large.bin").write_bytes(
        b"x" * (sandbox_runner.MAX_FILE_BYTES + 1)
    )

    with patch("e2b.Sandbox.create") as create_sandbox:
        result = sandbox_runner.run_pytest_in_sandbox(tmp_path)

    assert result["status"] == "skipped"
    assert result["passed"] is None
    assert result["test_command_executed"] is False
    assert "2 MB" in result["output"]
    create_sandbox.assert_not_called()


def test_runner_skips_when_no_python_tests_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEPILOT_ENABLE_E2B", "1")
    monkeypatch.setenv("E2B_API_KEY", "test-key")
    monkeypatch.setenv("E2B_TEMPLATE_ID", "codepilot-python-tests")
    (tmp_path / "app.py").write_text("print('hello')", encoding="utf-8")

    with patch("e2b.Sandbox.create") as create_sandbox:
        result = sandbox_runner.run_pytest_in_sandbox(tmp_path)

    assert result["status"] == "skipped"
    assert result["test_command_executed"] is False
    create_sandbox.assert_not_called()
