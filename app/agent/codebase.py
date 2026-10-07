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
    max_files: int = 6,
) -> list[dict]:
    """
    Rank repository files using the developer task.

    Implementation files are preferred for production changes.
    Test files remain important when the task explicitly requests tests,
    but they should not replace the relevant production source file.
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

    requests_tests = any(
        word in task_lower
        for word in (
            "test",
            "tests",
            "testing",
            "pytest",
        )
    )

    requests_validation = any(
        word in task_lower
        for word in (
            "validation",
            "validator",
            "validate",
            "invalid",
        )
    )

    scored = []

    for file in files:
        path = file["path"]
        content = file["content"]

        path_lower = path.lower()
        content_lower = content.lower()

        is_test_file = (
            path_lower.startswith("tests/")
            or "/tests/" in path_lower
            or path_lower.startswith("test_")
            or path_lower.endswith("_test.py")
        )

        is_source_file = (
            path_lower.endswith((".py", ".js", ".ts", ".tsx", ".jsx"))
            and not is_test_file
        )

        score = 0

        # ---------------------------------------------------------
        # 1. Direct task keyword matches
        # ---------------------------------------------------------

        for term in terms:
            if term in path_lower:
                score += 8

            if term in content_lower:
                score += 1

        # ---------------------------------------------------------
        # 2. Function-name detection
        # ---------------------------------------------------------

        function_names = re.findall(
            r"\b(?:function|method|def)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
            task,
            flags=re.IGNORECASE,
        )

        for function_name in function_names:
            function_name = function_name.lower()

            if function_name in path_lower:
                score += 12

            if re.search(
                rf"\bdef\s+{re.escape(function_name)}\s*\(",
                content_lower,
            ):
                score += 12

        # ---------------------------------------------------------
        # 3. Production implementation preference
        # ---------------------------------------------------------

        if is_source_file:
            score += 5

        if is_test_file:
            # Test files are useful, but should not dominate production
            # source files merely because the task also requests tests.
            score -= 2

        # ---------------------------------------------------------
        # 4. Test-related requests
        # ---------------------------------------------------------

        if requests_tests and is_test_file:
            score += 6

        # A test request should still expose source implementation files.
        if requests_tests and is_source_file:
            score += 3

        # ---------------------------------------------------------
        # 5. Validation-related requests
        # ---------------------------------------------------------

        if requests_validation:
            if "validator" in path_lower:
                score += 8

            if "validation" in content_lower:
                score += 4

        # ---------------------------------------------------------
        # 6. Python preference
        # ---------------------------------------------------------

        if path_lower.endswith(".py"):
            score += 2

        # ---------------------------------------------------------
        # 7. Documentation penalty
        # ---------------------------------------------------------

        if path_lower.endswith((".md", ".txt")):
            score -= 4

        # ---------------------------------------------------------
        # 8. Package/init files are usually less useful
        # ---------------------------------------------------------

        if path_lower.endswith("__init__.py"):
            score -= 3

        scored.append((score, file))

    scored.sort(
        key=lambda item: (-item[0], item[1]["path"])
    )

    # -------------------------------------------------------------
    # First select strong implementation files.
    # This prevents test files from completely replacing source files.
    # -------------------------------------------------------------

    implementation_files = [
        file
        for score, file in scored
        if score > 0
        and file["path"].lower().endswith(
            (".py", ".js", ".ts", ".tsx", ".jsx")
        )
        and not (
            file["path"].lower().startswith("tests/")
            or "/tests/" in file["path"].lower()
            or file["path"].lower().startswith("test_")
            or file["path"].lower().endswith("_test.py")
        )
    ]

    test_files = [
        file
        for score, file in scored
        if score > 0
        and (
            file["path"].lower().startswith("tests/")
            or "/tests/" in file["path"].lower()
            or file["path"].lower().startswith("test_")
            or file["path"].lower().endswith("_test.py")
        )
    ]

    other_files = [
        file
        for score, file in scored
        if score > 0
        and file not in implementation_files
        and file not in test_files
    ]

    chosen = []

    # Always expose the strongest implementation candidates first.
    for file in implementation_files:
        if len(chosen) >= max_files:
            break
        chosen.append(file)

    # If tests were explicitly requested, include the strongest test files.
    if requests_tests:
        for file in test_files:
            if len(chosen) >= max_files:
                break
            if file not in chosen:
                chosen.append(file)

    # Fill remaining context with other relevant files.
    for file in other_files:
        if len(chosen) >= max_files:
            break
        if file not in chosen:
            chosen.append(file)

    # Final fallback.
    if not chosen:
        chosen = [
            file
            for _, file in scored[:max_files]
        ]

    return chosen