from __future__ import annotations

from .codebase import scan_codebase, select_relevant_files
from .llm import generate_plan_and_patch
from .validator import run_validation


def run_agent(task: str) -> dict:
    if not task or len(task.strip()) < 5:
        raise ValueError(
            "Please enter a specific developer task."
        )

    # 1. Inspect the sample repository.
    files = scan_codebase()

    # 2. Identify files relevant to the developer task.
    relevant = select_relevant_files(
        task,
        files,
    )

    # 3. Ask the local LLM to understand the task,
    #    create a plan, and propose a patch.
    result = generate_plan_and_patch(
        task,
        relevant,
    )

    changes = result.get(
        "changes",
        [],
    )

    # 4. Validate the proposed AI changes in
    #    an isolated temporary copy.
    validation = run_validation(
        changes=changes,
    )

    return {
        "task": task,
        "files_scanned": len(files),

        "relevant_files": [
            {
                "path": f["path"],
                "lines": f["lines"],
            }
            for f in relevant
        ],

        "plan": result.get(
            "plan",
            [],
        ),

        "changes": changes,

        "explanation": result.get(
            "explanation",
            "",
        ),

        "test_command": result.get(
            "test_command",
            "pytest -q",
        ),

        "validation": validation,

        "demo_mode": bool(
            result.get(
                "demo_mode",
                False,
            )
        ),

        "model": result.get(
            "model",
            "qwen2.5-coder:7b",
        ),
    }