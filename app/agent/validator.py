from __future__ import annotations

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

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="codepilot_validation_"
        )
    )

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

    for change in changes:

        path_value = change.get("path")
        new_content = change.get("content")

        if not path_value:
            continue

        if new_content is None:
            raise ValueError(
                f"No complete file content supplied for {path_value}"
            )

        target = (
            destination / path_value
        ).resolve()

        # Security check.
        if (
            destination not in target.parents
            and target != destination
        ):
            raise ValueError(
                f"Unsafe patch path rejected: {path_value}"
            )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        target.write_text(
            new_content,
            encoding="utf-8",
        )

        changed_files.append(
            path_value
        )

    return destination, changed_files


def run_validation(
    root: str = "sample_project",
    changes: list[dict] | None = None,
) -> dict:

    changes = changes or []

    if not changes:
        return {
            "passed": False,
            "return_code": 1,
            "output": (
                "No AI patch was generated, so the "
                "proposed change could not be validated."
            ),
            "changed_files": [],
        }

    temp_root = None

    try:

        temp_root, changed_files = (
            _apply_changes_to_copy(
                root,
                changes,
            )
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--import-mode=importlib",
            ],
            cwd=temp_root,
            capture_output=True,
            text=True,
            timeout=60,
        )

        return {
            "passed": result.returncode == 0,
            "return_code": result.returncode,
            "output": (
                result.stdout
                + "\n"
                + result.stderr
            ).strip()[-4000:],
            "changed_files": changed_files,
        }

    except subprocess.TimeoutExpired:

        return {
            "passed": False,
            "return_code": 124,
            "output": (
                "Validation timed out after "
                "60 seconds."
            ),
            "changed_files": [],
        }

    except Exception as exc:

        return {
            "passed": False,
            "return_code": 1,
            "output": (
                f"Validation error: {exc}"
            ),
            "changed_files": [],
        }

    finally:

        if temp_root:
            shutil.rmtree(
                temp_root.parent,
                ignore_errors=True,
            )