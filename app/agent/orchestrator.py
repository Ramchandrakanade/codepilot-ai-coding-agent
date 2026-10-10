from __future__ import annotations

from .codebase import scan_codebase, select_relevant_files
from .llm import generate_plan_and_patch
from .validator import run_validation


def run_agent(
    task: str,
    project_root: str = "sample_project",
    trusted_project: bool = False,
) -> dict:

    if not task or len(task.strip()) < 5:
        raise ValueError(
            "Please enter a specific developer task."
        )

    # 1. Inspect the selected repository.
    files = scan_codebase(project_root)

    # 2. Identify files relevant to the developer task.
    relevant = select_relevant_files(
        task,
        files,
    )

    # 3. Ask the LLM to understand the task,
    #    create a plan, and propose a patch.
    result = generate_plan_and_patch(
        task,
        relevant,
    )

    changes = result.get(
        "changes",
        [],
    )

    # 4. Perform static validation only.
    #    Test execution is disabled until an isolated sandbox exists.
    validation = run_validation(
        root=project_root,
        changes=changes,
        trusted_project=trusted_project,
    )

    return {
        "task": task,
        "files_scanned": len(files),

        "project_root": project_root,

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

        # Informational only: the server does not execute this command.
        "test_command": result.get(
            "test_command",
            "pytest -q",
        ),
        "test_command_executed": bool(validation.get("test_command_executed", False)),

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
