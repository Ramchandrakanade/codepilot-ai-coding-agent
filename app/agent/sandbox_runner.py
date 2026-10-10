from __future__ import annotations

import os
from pathlib import Path

MAX_FILES = 500
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
SANDBOX_ROOT = "/tmp/codepilot_project"

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".ssh",
    ".aws",
    ".azure",
    ".gnupg",
}

IGNORED_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    ".ppk",
}

IGNORED_NAMES = {
    "credentials",
    "credentials.json",
    "credential.json",
    "secrets",
    "secrets.json",
    "secret.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "authorized_keys",
    "known_hosts",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".htpasswd",
}


def _should_ignore_file(path: Path) -> bool:
    name = path.name.lower()

    # Exclude all environment files, including .env.example.
    if name == ".env" or name.startswith(".env."):
        return True

    if name in IGNORED_NAMES or path.suffix.lower() in IGNORED_SUFFIXES:
        return True

    # Exclude common credential and private-key naming patterns.
    if any(term in name for term in (
        "credential",
        "secret",
        "private_key",
        "private-key",
    )):
        return True

    if name.endswith(("_token", "_token.txt", ".token")):
        return True

    if name.startswith(("id_rsa.", "id_ed25519.", "id_ecdsa.")):
        return True

    return False


def run_pytest_in_sandbox(
    project_dir: str | Path,
    timeout_seconds: int = 45,
) -> dict:
    """Run pytest on a filtered project copy inside an isolated E2B sandbox."""
    enabled = os.getenv("CODEPILOT_ENABLE_E2B", "").strip().lower() in {
        "1", "true", "yes"
    }
    api_key = os.getenv("E2B_API_KEY", "").strip()
    template = os.getenv(
        "E2B_TEMPLATE_ID", "codepilot-python-tests"
    ).strip()

    if not enabled or not api_key or not template:
        return {
            "passed": None,
            "status": "skipped",
            "return_code": None,
            "test_command_executed": False,
            "output": (
                "E2B is not configured. Static validation completed; "
                "tests were skipped."
            ),
        }

    sandbox = None
    execution_started = False

    try:
        from e2b import Sandbox

        source = Path(project_dir).resolve()
        if not source.is_dir():
            raise ValueError("Validation project directory does not exist.")

        files = []
        total_bytes = 0

        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)

            if any(part.lower() in IGNORED_DIRS for part in relative.parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            if _should_ignore_file(path):
                continue

            size = path.stat().st_size

            if size > MAX_FILE_BYTES:
                raise ValueError(
                    "A project file exceeds the 2 MB sandbox file limit."
                )

            total_bytes += size
            files.append((path, size))

            if len(files) > MAX_FILES:
                raise ValueError("Project exceeds the sandbox file-count limit.")
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError("Project exceeds the sandbox upload-size limit.")

        if not any(path.name.startswith("test_") or path.name.endswith("_test.py")
                   for path, _ in files):
            return {
                "passed": None,
                "status": "skipped",
                "return_code": None,
                "test_command_executed": False,
                "output": (
                    "No Python test files were found. "
                    "Static validation completed; tests were skipped."
                ),
            }

        sandbox = Sandbox.create(
            template=template,
            timeout=timeout_seconds + 30,
            secure=True,
            allow_internet_access=False,
        )

        sandbox.files.make_dir(SANDBOX_ROOT)

        directories = {
            SANDBOX_ROOT + "/" + path.relative_to(source).parent.as_posix()
            for path, _ in files
            if path.relative_to(source).parent != Path(".")
        }

        for directory in sorted(
            directories, key=lambda item: (item.count("/"), item)
        ):
            sandbox.files.make_dir(directory)

        for path, _ in files:
            relative = path.relative_to(source).as_posix()
            remote_path = SANDBOX_ROOT + "/" + relative
            sandbox.files.write(remote_path, path.read_bytes())

        execution_started = True
        result = sandbox.commands.run(
            "python -m pytest -q",
            cwd=SANDBOX_ROOT,
            timeout=timeout_seconds,
        )

        output_parts = [
            part.strip()
            for part in (result.stdout, result.stderr)
            if part and part.strip()
        ]
        output = "\n".join(output_parts) or "Pytest finished without output."
        passed = result.exit_code == 0

        return {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "return_code": result.exit_code,
            "test_command_executed": True,
            "output": output,
        }

    except Exception as exc:
        return {
            "passed": False if execution_started else None,
            "status": "failed" if execution_started else "skipped",
            "return_code": 124 if execution_started else None,
            "test_command_executed": execution_started,
            "output": (
                "Sandbox test execution failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    finally:
        if sandbox is not None:
            try:
                sandbox.kill()
            except Exception:
                pass
