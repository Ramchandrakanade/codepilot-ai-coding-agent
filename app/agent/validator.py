from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


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
    """Validate patch structure and Python syntax without execution."""
    errors = []

    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            errors.append(
                f"Patch entry {index + 1} must be an object."
            )
            continue

        path = change.get("path")

        if not isinstance(path, str) or not path.strip():
            errors.append(
                f"Patch entry {index + 1} must have a non-empty string path."
            )
            continue

        source = change.get("content")

        if not isinstance(source, str):
            errors.append(
                f"{path}: file content must be supplied as text."
            )
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

    return {
        "passed": None,
        "status": "skipped",
        "return_code": None,
        "output": (
            "Test execution is disabled until an isolated sandbox "
            "is available. Static syntax validation completed."
        ),
        "changed_files": [
            change["path"]
            for change in changes
            if isinstance(change, dict)
            and isinstance(change.get("path"), str)
            and change.get("path").strip()
        ],
    }
