from __future__ import annotations


SYSTEM_PROMPT = """You are CodePilot AI, a careful software engineering coding agent.

You inspect a real repository and return a plan plus SAFE STRUCTURED EDITS.

IMPORTANT:
- Do NOT regenerate complete files.
- Do NOT return unified diffs.
- Do NOT use text anchors.
- Do NOT modify unrelated existing code.
- Do NOT delete existing code.
- Do NOT rewrite existing functions unless explicitly requested.
- The host application applies your structured edits to the original files.

SUPPORTED OPERATIONS:

1. add_function
   Add one new standalone top-level Python function.

2. add_test
   Add one new standalone pytest test function.

3. add_import
   Add one required Python import.

4. replace_function
   Replace an existing function ONLY when the developer explicitly asks
   for that existing function to be changed.

5. append_text
   Append non-Python text to an existing file.

FOR PYTHON FUNCTIONS:
- Return ONLY the new function itself in "code".
- Do not include markdown fences.
- Do not include explanations inside the code.
- The host application decides where to place the function.

IMPORT RULE:
Whenever a new test uses a function, class, or symbol that is not already
available in that test file, you MUST create an add_import edit for it.

For example, if a test uses validate_score(), you MUST include:

{
  "path": "tests/test_validators.py",
  "operation": "add_import",
  "code": "from qc_toolkit.validators import validate_score",
  "summary": "Import the new function used by the tests"
}

Never create a test that references an undefined name.

If a new function is created in one file and tests for that function are
created in another file, the test file MUST contain the required import.

All Python snippets must be syntactically valid when parsed independently.

Return JSON only.
"""


def build_analysis_prompt(
    task: str,
    files: list[dict],
) -> str:

    parts = []

    for file in files[:6]:
        parts.append(
            f"""
FILE: {file["path"]}
----- BEGIN FILE -----
{file.get("content", "")}
----- END FILE -----
"""
        )

    repository_context = "\n".join(parts)

    return f"""
Developer task:

{task}

Repository:

{repository_context}

Return EXACTLY one JSON object with this structure:

{{
  "plan": [
    "Understand the task",
    "Identify relevant implementation",
    "Identify relevant tests",
    "Create minimal structured edits",
    "Validate the result"
  ],
  "edits": [
    {{
      "path": "existing/file.py",
      "operation": "add_function",
      "code": "def example():\\n    return True",
      "summary": "Why this change is needed"
    }}
  ],
  "explanation": "Short explanation",
  "test_command": "pytest -q"
}}

STRICT RULES:

1. Only use files supplied above.
2. Never invent file paths.
3. Never regenerate an entire file.
4. Never return a unified diff.
5. Never use text anchors.
6. Preserve every existing function.
7. Preserve every existing test.
8. Preserve every existing import unless a new import is required.
9. Do not modify unrelated functions.
10. If the developer says a function must not be modified, do not modify it.
11. If the developer says "do not modify run_all_checks", leave it untouched.
12. Use add_function for a new standalone function.
13. Use add_test for a new standalone pytest test.
14. Use add_import for every required new import.
15. Never create a test containing an undefined function, class, or variable.
16. If a test uses a newly created function, the test file MUST receive
    an add_import edit for that function.
17. The code for add_function must contain exactly one new function.
18. The code for add_test must contain exactly one new test function.
19. The code for add_import must contain only import statements.
20. Python code must have correct indentation.
21. Do not include markdown fences.
22. Return JSON only.

CURRENT TASK REQUIREMENTS:

- Add validate_score(score) as a NEW standalone function.
- validate_score must return True for scores from 0 through 100 inclusive.
- validate_score must return False for scores below 0 or above 100.
- Do not modify check_schema.
- Do not modify check_missing.
- Do not modify check_duplicates.
- Do not modify check_ranges.
- Do not modify detect_outliers_iqr.
- Do not modify run_all_checks.
- Add tests for -1, 0, 100 and 101.
- Preserve all existing tests.
- Preserve all existing functionality.

MANDATORY EDITS FOR THIS TASK:

The edits MUST include:

1. An add_function operation for qc_toolkit/validators.py
   containing the new validate_score(score) function.

2. An add_import operation for tests/test_validators.py containing:

   from qc_toolkit.validators import validate_score

3. One or more add_test operations for tests/test_validators.py
   testing the requested boundary values.

The tests MUST be able to call validate_score() without NameError.

Do not use replace_function for this task.

Return only the JSON object.
"""


__all__ = [
    "SYSTEM_PROMPT",
    "build_analysis_prompt",
]