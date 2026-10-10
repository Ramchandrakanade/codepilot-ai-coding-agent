from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .sandbox_runner import run_pytest_in_sandbox


def _is_safe_patch_path(path_value: object) -> bool:
    """Reject absolute paths and traversal on POSIX and Windows."""
    from pathlib import PurePosixPath, PureWindowsPath

    if not isinstance(path_value, str) or not path_value.strip():
        return False
    if chr(0) in path_value:
        return False

    normalized = path_value.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(path_value)

    if posix_path.is_absolute() or windows_path.is_absolute():
        return False
    if windows_path.drive:
        return False
    if ".." in posix_path.parts:
        return False
    if normalized in ("", "."):
        return False

    return True


def _apply_changes_to_copy(
    root: str,
    changes: list[dict],
) -> tuple[Path, list[str]]:
    source = Path(root).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="codepilot_validation_"))

    try:
        destination = temp_dir / source.name

        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(
                ".git",
                "__pycache__",
                ".pytest_cache",
                "*.pyc",
            ),
        )

        changed_files = []

        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                raise ValueError(
                    f"Patch entry {index + 1} must be an object."
                )

            path_value = change.get("path")
            new_content = change.get("content")

            if not isinstance(path_value, str) or not path_value.strip():
                raise ValueError(
                    f"Patch entry {index + 1} must have a non-empty string path."
                )

            if not _is_safe_patch_path(path_value):
                raise ValueError(f"Unsafe patch path rejected: {path_value}")

            if not isinstance(new_content, str):
                raise ValueError(
                    f"{path_value}: file content must be supplied as text."
                )

            target = (destination / path_value).resolve()

            if destination not in target.parents and target != destination:
                raise ValueError(f"Unsafe patch path rejected: {path_value}")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content, encoding="utf-8")
            changed_files.append(path_value)

        return destination, changed_files

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _check_python_syntax(changes: list[dict]) -> list[str]:
    """Validate patch structure, paths, and Python syntax without execution."""
    errors = []

    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            errors.append(f"Patch entry {index + 1} must be an object.")
            continue

        path = change.get("path")

        if not isinstance(path, str) or not path.strip():
            errors.append(
                f"Patch entry {index + 1} must have a non-empty string path."
            )
            continue

        if not _is_safe_patch_path(path):
            errors.append(f"Unsafe patch path rejected: {path}")
            continue

        source = change.get("content")

        if not isinstance(source, str):
            errors.append(f"{path}: file content must be supplied as text.")
            continue

        if not path.lower().endswith(".py"):
            continue

        try:
            ast.parse(source, filename=path)
        except SyntaxError as exc:
            errors.append(
                f"{path}: invalid Python syntax at line "
                f"{exc.lineno}: {exc.msg}"
            )

    return errors


def run_validation(
    root: str = "sample_project",
    changes: list[dict] | None = None,
    trusted_project: bool = False,
) -> dict:

    changes = changes or []

    if not changes:
        return {
            "passed": False,
            "status": "failed",
            "return_code": 1,
            "output": (
                "No AI patch was generated, so the "
                "proposed change could not be validated."
            ),
            "changed_files": [],
        }

    syntax_errors = _check_python_syntax(changes)

    if syntax_errors:
        return {
            "passed": False,
            "status": "failed",
            "return_code": 1,
            "output": "Static syntax validation failed: " + "; ".join(syntax_errors),
            "changed_files": [
                change["path"]
                for change in changes
                if isinstance(change, dict)
                and isinstance(change.get("path"), str)
                and change.get("path")
            ],
        }

    changed_files = [
        change["path"]
        for change in changes
        if isinstance(change, dict)
        and isinstance(change.get("path"), str)
        and change.get("path").strip()
    ]

    sandbox_enabled = os.getenv(
        "CODEPILOT_ENABLE_E2B", ""
    ).strip().lower() in {"1", "true", "yes"}

    if trusted_project and sandbox_enabled:
        validation_root = None
        try:
            validation_root, _ = _apply_changes_to_copy(root, changes)
            result = run_pytest_in_sandbox(validation_root)
            result["changed_files"] = changed_files
            return result
        except Exception as exc:
            return {
                "passed": None,
                "status": "skipped",
                "return_code": None,
                "test_command_executed": False,
                "output": (
                    "Sandbox validation could not be completed: "
                    f"{type(exc).__name__}. Static validation completed."
                ),
                "changed_files": changed_files,
            }
        finally:
            if validation_root is not None:
                shutil.rmtree(validation_root.parent, ignore_errors=True)

    if not trusted_project:
        message = (
            "This project is not eligible for sandbox test execution. "
            "Static syntax validation completed; tests were skipped."
        )
    else:
        message = (
            "E2B sandbox validation is disabled. "
            "Static syntax validation completed; tests were skipped."
        )

    return {
        "passed": None,
        "status": "skipped",
        "return_code": None,
        "test_command_executed": False,
        "output": message,
        "changed_files": changed_files,
    }
