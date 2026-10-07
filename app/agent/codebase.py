from __future__ import annotations

from pathlib import Path
import re


IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "node_modules",
}

ALLOWED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".html",
    ".css",
}


def scan_codebase(root: str = "sample_project") -> list[dict]:
    """Read the small sample repository used by CodePilot."""

    base = Path(root)

    if not base.exists():
        raise FileNotFoundError(f"Codebase not found: {root}")

    files = []

    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue

        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue

        if any(part in IGNORED_DIRS for part in path.parts):
            continue

        text = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        rel = path.relative_to(base).as_posix()

        files.append(
            {
                "path": rel,
                "content": text,
                "lines": len(text.splitlines()),
            }
        )

    return files


def select_relevant_files(
    task: str,
    files: list[dict],
    max_files: int = 4,
) -> list[dict]:
    """
    Rank files using task keywords and common software-engineering
    relationships such as implementation file + test file.
    """

    task_lower = task.lower()

    terms = {
        term.lower()
        for term in re.findall(
            r"[a-zA-Z_][a-zA-Z0-9_]+",
            task,
        )
        if len(term) > 2
    }

    scored = []

    for file in files:
        path = file["path"]
        content = file["content"]

        path_lower = path.lower()
        content_lower = content.lower()

        score = 0

        # Direct task keyword matches.
        for term in terms:
            if term in path_lower:
                score += 5

            if term in content_lower:
                score += 1

        # Validation-related requests.
        if any(
            word in task_lower
            for word in (
                "validation",
                "validator",
                "validate",
                "invalid",
            )
        ):
            if "validator" in path_lower:
                score += 8

            if "validation" in content_lower:
                score += 4

        # Test-related requests.
        if any(
            word in task_lower
            for word in (
                "test",
                "testing",
                "pytest",
            )
        ):
            if (
                "test" in path_lower
                or path_lower.startswith("tests/")
            ):
                score += 8

        # Prefer Python implementation files for Python tasks.
        if ".py" in path_lower:
            score += 1

        # Penalize documentation for implementation tasks.
        if path_lower.endswith((".md", ".txt")):
            score -= 2

        scored.append((score, file))

    scored.sort(
        key=lambda item: (-item[0], item[1]["path"])
    )

    chosen = [
        file
        for score, file in scored
        if score > 0
    ][:max_files]

    # If nothing matched, still provide a small sample.
    if not chosen:
        chosen = [
            file
            for _, file in scored[:max_files]
        ]

    return chosen