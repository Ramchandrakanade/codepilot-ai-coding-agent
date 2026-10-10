from __future__ import annotations

import ast
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import ollama

from .edit_engine import apply_structured_edits


OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "qwen2.5-coder:3b",
)

OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "qwen/qwen3.6-plus:free",
)

OPENROUTER_FALLBACK_MODELS = [
    "qwen/qwen3.6-plus:free",
]

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash",
)

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-20b",
)


HF_MODEL = os.getenv(
    "HF_MODEL",
    "Qwen/Qwen2.5-Coder-3B-Instruct",
)

AI_PROVIDER = os.getenv(
    "AI_PROVIDER",
    "ollama",
).lower()



# ============================================================
# TASK CONTRACT VALIDATION
# ============================================================

class TaskContractError(ValueError):
    """Raised when the model output does not match the developer task."""


def extract_requested_functions(task):
    """Extract explicitly requested function names from the task."""

    task = str(task or "")

    found = []

    patterns = [
        r"\b(?:add|implement|write|define)\s+(?:a\s+|an\s+|new\s+)?([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s+function\b",
        r"\bcreate\s+(?:a\s+|an\s+|new\s+)?function\s+([A-Za-z_]\w*)\s*\(",
        r"\b(?:named|called)\s+([A-Za-z_]\w*)\s*(?:\([^)]*\))?",
        r"\bfunction\s+([A-Za-z_]\w*)\s*\(",
        r"\bdef\s+([A-Za-z_]\w*)\s*\(",
    ]

    # Never treat natural-language keywords as function names.
    reserved_words = {
        "new",
        "function",
        "called",
        "named",
        "a",
        "an",
        "the",
        "python",
        "javascript",
        "typescript",
        "java",
        "c",
        "cpp",
        "golang",
        "rust",
    }
    for pattern in patterns:
        for match in re.finditer(
            pattern,
            task,
            flags=re.IGNORECASE,
        ):
            name = match.group(1)

            if name.lower() not in reserved_words and name not in found:
                found.append(name)

    return found
def extract_requested_signature(task, function_name):
    """Extract explicitly requested parameter names."""

    task = str(task or "")

    pattern = (
        rf"\b{re.escape(function_name)}\s*"
        r"\(([^)]*)\)"
    )

    match = re.search(
        pattern,
        task,
        flags=re.IGNORECASE,
    )

    if not match:
        # If the task names a function but omits parentheses,
        # infer a simple parameter from the function/task wording.
        # Example:
        # "Add a new validate_score function ... score ..."
        # should expect validate_score(score).
        if function_name.lower().startswith("validate_"):
            score_match = re.search(
                r"\bscore\b",
                task,
                flags=re.IGNORECASE,
            )
            if score_match:
                return ["score"]

        return None

    raw = match.group(1).strip()

    if not raw:
        return []

    parameters = []

    for item in raw.split(","):
        item = item.strip()
        item = item.split("=")[0].strip()
        item = item.split(":")[0].strip()

        if item and re.match(
            r"^[A-Za-z_]\w*$",
            item,
        ):
            parameters.append(item)

    return parameters


def extract_requested_target_file(task):
    """Extract an explicitly mentioned Python target file."""

    task = str(task or "")

    match = re.search(
        r"(?:to|in|inside|into)\s+"
        r"([A-Za-z0-9_./\\-]+\.py)\b",
        task,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    return match.group(1).replace("\\", "/")


def extract_function_definitions(code):
    """Return top-level function definitions from generated code."""

    try:
        tree = ast.parse(str(code or ""))
    except SyntaxError as exc:
        raise TaskContractError(
            "Generated code contains invalid Python: "
            f"{exc}"
        ) from exc

    return [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    ]


def _all_generated_function_names(edits):
    names = []

    for edit in edits or []:

        if edit.get("operation") not in {
            "add_function",
            "add_test",
        }:
            continue

        functions = extract_function_definitions(
            edit.get("code", "")
        )

        for function in functions:

            if function.name not in names:
                names.append(function.name)

    return names


def _test_code_references_function(code, function_name):
    """
    Determine whether generated test code meaningfully references the
    requested function.

    Supports:
      - direct calls: calculate_total(...)
      - ordinary names: calculate_total
      - imported aliases
      - module attributes: report.calculate_total(...)
      - nested references inside assertions and test functions
    """

    try:
        tree = ast.parse(str(code or ""))
    except SyntaxError:
        return False

    aliases = set()

    for node in ast.walk(tree):

        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name == function_name:
                    aliases.add(imported.asname or imported.name)

        elif isinstance(node, ast.Import):
            for imported in node.names:
                if imported.asname == function_name:
                    aliases.add(function_name)

    aliases.add(function_name)

    for node in ast.walk(tree):

        if isinstance(node, ast.Name):
            if node.id in aliases:
                return True

        elif isinstance(node, ast.Attribute):
            if node.attr == function_name:
                return True

    return False


def validate_task_contract(task, result):
    """
    Validate model output against the actual developer request
    before applying any code changes.
    """

    edits = result.get("edits", [])

    if not isinstance(edits, list):
        raise TaskContractError(
            "AI output contains an invalid edits list."
        )

    requested_functions = extract_requested_functions(
        task
    )

    target_file = extract_requested_target_file(
        task
    )

    # --------------------------------------------------------
    # Requested function names
    # --------------------------------------------------------

    if requested_functions:

        generated_function_names = (
            _all_generated_function_names(edits)
        )

        for requested_name in requested_functions:

            if requested_name not in generated_function_names:

                raise TaskContractError(
                    "The AI did not generate the requested "
                    f"function '{requested_name}'. "
                    f"Generated functions: "
                    f"{generated_function_names or 'none'}"
                )

        allowed = set(requested_functions)

        for edit in edits:

            if edit.get("operation") != "add_function":
                continue

            functions = extract_function_definitions(
                edit.get("code", "")
            )

            for function in functions:

                if function.name not in allowed:

                    raise TaskContractError(
                        "The AI generated an unrelated function "
                        f"'{function.name}'. "
                        f"Requested: {requested_functions}"
                    )

    # --------------------------------------------------------
    # Explicit target file
    # --------------------------------------------------------

    if target_file and requested_functions:

        matching_function_edit = False

        for edit in edits:

            if edit.get("operation") != "add_function":
                continue

            path = str(
                edit.get("path", "")
            ).replace("\\", "/")

            if path == target_file:

                matching_function_edit = True
                break

        if not matching_function_edit:

            raise TaskContractError(
                "The AI generated the requested function, "
                "but not in the requested file. "
                f"Expected: {target_file}"
            )

    # --------------------------------------------------------
    # Function signature
    # --------------------------------------------------------

    for requested_name in requested_functions:

        expected_parameters = (
            extract_requested_signature(
                task,
                requested_name,
            )
        )

        if expected_parameters is None:
            continue

        for edit in edits:

            if edit.get("operation") != "add_function":
                continue

            functions = extract_function_definitions(
                edit.get("code", "")
            )

            for function in functions:

                if function.name != requested_name:
                    continue

                actual_parameters = [
                    argument.arg
                    for argument in function.args.args
                ]

                if actual_parameters != expected_parameters:

                    raise TaskContractError(
                        f"Function '{requested_name}' has "
                        f"parameters {actual_parameters}, "
                        f"but the task requested "
                        f"{expected_parameters}."
                    )

    # --------------------------------------------------------
    # Tests
    # --------------------------------------------------------

    task_lower = str(task).lower()

    asks_for_tests = any(
        word in task_lower
        for word in [
            "test",
            "tests",
            "unit test",
            "pytest",
        ]
    )

    if asks_for_tests and requested_functions:

        for requested_name in requested_functions:

            found_test_reference = False

            for edit in edits:

                if edit.get("operation") != "add_test":
                    continue

                if _test_code_references_function(
                    edit.get("code", ""),
                    requested_name,
                ):
                    found_test_reference = True
                    break

            if not found_test_reference:

                raise TaskContractError(
                    "The task requested tests for "
                    f"'{requested_name}', but the generated "
                    "test code does not reference that function."
                )

    return True


def generate_plan_and_patch(task, files):
    """
    Generate a structured coding-agent response and safely apply it.

    The LLM is never trusted blindly:
    requested function names are extracted from the developer task and
    checked before the edit engine is allowed to apply changes.
    """

    from .prompts import SYSTEM_PROMPT, build_analysis_prompt

    prompt = build_analysis_prompt(
        task,
        files,
    )

    required_functions = extract_requested_new_functions(task)

    # Give the model an explicit deterministic contract.
    if required_functions:
        prompt += build_function_contract(
            required_functions
        )

    primary_error = None

    # ---------------------------------------------------------
    # First generation attempt.
    # ---------------------------------------------------------

    try:
        content, model_name, provider_name = (
            generate_with_selected_provider(
                prompt,
                SYSTEM_PROMPT,
            )
        )

        result = normalize_generated_result(task, parse_model_json(content))
        result["task"] = task

        validate_requested_function_edits(
            result,
            required_functions,
        )

        result = apply_result(
            result,
            task,
            files,
            model_name,
            provider_name,
        )

        return result

    except Exception as exc:
        primary_error = exc

    # ---------------------------------------------------------
    # Controlled regeneration.
    #
    # This is NOT a generic retry.
    # It only happens when the model violates the deterministic
    # requested-function contract.
    # ---------------------------------------------------------

    if required_functions and isinstance(primary_error, TaskContractError):
        try:
            correction_prompt = build_correction_prompt(
                prompt,
                required_functions,
                primary_error,
            )

            content, model_name, provider_name = (
                generate_with_selected_provider(
                    correction_prompt,
                    SYSTEM_PROMPT,
                )
            )

            result = normalize_generated_result(task, parse_model_json(content))
            result["task"] = task

            validate_requested_function_edits(
                result,
                required_functions,
            )

            validate_task_contract(
                task,
                result,
            )

            result = apply_result(
                result,
                task,
                files,
                model_name,
                provider_name,
            )

            return result

        except Exception as correction_error:
            primary_error = RuntimeError(
                "The AI generated an edit for the wrong "
                "function and the controlled correction "
                "attempt also failed.\n\n"
                f"Original AI validation error: "
                f"{primary_error}\n\n"
                f"Correction attempt error: "
                f"{correction_error}"
            )

    # ---------------------------------------------------------
    # Generic correction for malformed replace_function edits.
    # Do not retry provider/network errors as code-generation errors.
    target_error = str(primary_error).lower()

    target_error_markers = (
        "replace_function without a target",
        "does not exist in",
        "exactly one replacement function",
    )

    if (
        not required_functions
        and isinstance(primary_error, TaskContractError)
        and any(marker in target_error for marker in target_error_markers)
    ):
        try:
            correction_prompt = f"""
{prompt}

IMPORTANT CORRECTION:
The previous structured edit failed validation.

Validation error:
{primary_error}

Return corrected JSON using the original structured-edit schema.

Rules:
- For replace_function, target must be an existing top-level
  function name in the specified source file.
- Replacement code must define exactly one top-level function
  with the same name as target.
- Do not guess function names.
- For other operations, target must be an empty string.
- Preserve the user's requested behavior.
- Return JSON only.
"""

            content, model_name, provider_name = (
                generate_with_selected_provider(
                    correction_prompt,
                    SYSTEM_PROMPT,
                )
            )

            result = normalize_generated_result(
                task,
                parse_model_json(content),
            )
            result["task"] = task

            validate_requested_function_edits(
                result,
                required_functions,
            )
            validate_task_contract(task, result)

            result = apply_result(
                result,
                task,
                files,
                model_name,
                provider_name,
            )
            return result

        except Exception as correction_error:
            primary_error = RuntimeError(
                "The controlled target-correction attempt failed. "
                "Provider fallback will now be attempted.\n\n"
                f"Original validation error: {primary_error}\n\n"
                f"Correction error: {correction_error}"
            )
    # Provider fallback chain.
    #
    # Primary provider is AI_PROVIDER.
    # If the primary provider fails, try the available providers
    # in a deterministic order and continue when one fails.
    # ---------------------------------------------------------

    fallback_providers = []

    if AI_PROVIDER == "openrouter":
        # OpenRouter -> Gemini -> Groq
        if os.getenv("GEMINI_API_KEY"):
            fallback_providers.append("gemini")

        if os.getenv("GROQ_API_KEY"):
            fallback_providers.append("groq")

    elif AI_PROVIDER == "gemini":
        # Free-only fallback: Gemini -> Groq.
        # Never fall back to OpenRouter when Gemini is the primary provider.
        if os.getenv("GROQ_API_KEY"):
            fallback_providers.append("groq")

    elif AI_PROVIDER == "groq":
        # Groq -> OpenRouter -> Gemini
        if os.getenv("OPENROUTER_API_KEY"):
            fallback_providers.append("openrouter")

        if os.getenv("GEMINI_API_KEY"):
            fallback_providers.append("gemini")

    elif AI_PROVIDER == "ollama":
        # Ollama -> Gemini -> Groq -> OpenRouter
        if os.getenv("GEMINI_API_KEY"):
            fallback_providers.append("gemini")

        if os.getenv("GROQ_API_KEY"):
            fallback_providers.append("groq")

        if os.getenv("OPENROUTER_API_KEY"):
            fallback_providers.append("openrouter")

    elif AI_PROVIDER == "huggingface":
        # Hugging Face -> Gemini -> Groq -> OpenRouter
        if os.getenv("GEMINI_API_KEY"):
            fallback_providers.append("gemini")

        if os.getenv("GROQ_API_KEY"):
            fallback_providers.append("groq")

        if os.getenv("OPENROUTER_API_KEY"):
            fallback_providers.append("openrouter")

    fallback_errors = []

    for fallback_provider in fallback_providers:

        try:

            content, model_name, provider_name = (
                generate_with_provider(
                    fallback_provider,
                    prompt,
                    SYSTEM_PROMPT,
                )
            )

            result = normalize_generated_result(
                task,
                parse_model_json(content),
            )
            result["task"] = task

            validate_requested_function_edits(
                result,
                required_functions,
            )

            validate_task_contract(
                task,
                result,
            )

            result = apply_result(
                result,
                task,
                files,
                model_name,
                provider_name,
            )

            return result

        except Exception as fallback_error:

            fallback_errors.append(
                f"{fallback_provider}: {fallback_error}"
            )

            # Continue to the next provider.
            continue

    if fallback_errors:

        raise RuntimeError(
            "Primary AI provider failed: "
            f"{primary_error}\n\n"
            "All fallback AI providers failed:\n"
            + "\n".join(fallback_errors)
        )

    raise primary_error



def validate_edit_targets(result, files):
    # Reject malformed replace_function edits before applying any changes.
    file_map = {
        str(file.get("path", "")).replace("\\", "/"): str(file.get("content", ""))
        for file in (files or [])
    }

    for index, edit in enumerate(result.get("edits", []) or []):
        if not isinstance(edit, dict) or edit.get("operation") != "replace_function":
            continue

        path = str(edit.get("path", "")).replace("\\", "/")
        target = str(edit.get("target", "") or "").strip()
        if not target:
            raise TaskContractError(
                f"Edit {index + 1} uses replace_function without a target. "
                "Retry with the exact existing function name; never guess it."
            )
        if path not in file_map:
            raise TaskContractError(f"Edit {index + 1} targets unknown file '{path}'.")

        try:
            source_tree = ast.parse(file_map[path])
            replacement_tree = ast.parse(str(edit.get("code", "")))
        except SyntaxError as exc:
            raise TaskContractError(
                f"Edit {index + 1} has invalid Python while validating target '{target}': {exc}"
            ) from exc

        existing_names = {
            node.name for node in source_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        replacements = [
            node for node in replacement_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if target not in existing_names:
            raise TaskContractError(
                f"Edit {index + 1} targets '{target}', but that function does not exist in '{path}'. Do not guess a target."
            )
        if len(replacements) != 1 or replacements[0].name != target:
            raise TaskContractError(
                f"Edit {index + 1} must contain exactly one replacement function named '{target}'."
            )

def apply_result(
    result,
    task,
    files,
    model_name,
    provider_name,
):
    """Apply a validated model result through the safety engine."""

    result.setdefault("plan", [])
    result.setdefault("edits", [])
    result.setdefault(
        "explanation",
        "",
    )
    result.setdefault(
        "test_command",
        "pytest -q",
    )

    # Validate the developer task contract first.
    validate_task_contract(
        task,
        result,
    )

    # ---------------------------------------------------------
    # PRE-FLIGHT DUPLICATE FUNCTION CHECK
    # ---------------------------------------------------------

    existing_functions_by_file = {}

    for file in files or []:
        path = str(
            file.get("path", "")
        ).replace("\\", "/")

        content = str(
            file.get("content", "")
        )

        if not path.endswith(".py"):
            continue

        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue

        existing_names = {
            node.name
            for node in tree.body
            if isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            )
        }

        existing_functions_by_file[path] = existing_names

    for edit in result.get("edits", []):
        if not isinstance(edit, dict):
            continue

        if edit.get("operation") != "add_function":
            continue

        path = str(
            edit.get("path", "")
        ).replace("\\", "/")

        code = str(
            edit.get("code", "")
        )

        try:
            generated_functions = extract_function_names(code)
        except SyntaxError as exc:
            raise TaskContractError(
                f"Generated add_function code is invalid Python: {exc}"
            ) from exc

        if len(generated_functions) != 1:
            raise TaskContractError(
                "An add_function edit must contain exactly one function."
            )

        generated_name = generated_functions[0]

        existing_names = existing_functions_by_file.get(
            path,
            set(),
        )

        if generated_name in existing_names:
            raise TaskContractError(
                f"The AI attempted to add existing function "
                f"'{generated_name}' to '{path}'. "
                "Use replace_function only when the developer "
                "explicitly requested that existing function to be changed."
            )

    # Validate replacement targets before applying structured edits.
    validate_edit_targets(result, files)

    # Apply only after all safety checks pass.
    changes = apply_structured_edits(
        result.get("edits", []),
        files,
        task=task,
    )

    result["changes"] = changes
    result["demo_mode"] = False
    result["model"] = model_name
    result["provider"] = provider_name
    result["task"] = task

    return result

def extract_requested_new_functions(task):
    """
    Extract function names explicitly requested as new functions.

    Supports requests such as:

        Add a calculate_average function
        Add a calculate_average(numbers) function
        Add a new validate_score function
        Create function calculate_total
        Create a new function named normalize_data
        Implement normalize_data(data) function

    Returns only names that appear to be requested additions.
    """

    text = str(task or "")

    found = []

    patterns = [
        # "Write a Python function named is_even(number)"
        r"\b(?:add|create|implement|write|define)\s+"
        r"(?:a\s+|an\s+|the\s+)?"
        r"(?:new\s+)?"
        r"(?:[A-Za-z][A-Za-z0-9_+#.-]*\s+)?"
        r"function\s+(?:called\s+|named\s+)"
        r"`?([A-Za-z_][A-Za-z0-9_]*)`?",

        # "Add a calculate_average(numbers) function"
        # "Add a new validate_score function"
        r"\b(?:add|create|implement|write|define)\s+"
        r"(?:a\s+|an\s+|the\s+)?"
        r"(?:new\s+)?"
        r"(?:function\s+)?"
        r"(?:called\s+|named\s+)?"
        r"`?([A-Za-z_][A-Za-z0-9_]*)`?"
        r"(?:\s*\([^)]*\))?"
        r"\s+function\b",

        # "Add function calculate_average"
        r"\b(?:add|create|implement|write|define)\s+"
        r"(?:a\s+|an\s+|the\s+)?"
        r"(?:new\s+)?"
        r"function\s+"
        r"(?:called\s+|named\s+)?"
        r"`?([A-Za-z_][A-Za-z0-9_]*)`?",

        # "add a new function named calculate_average"
        r"\b(?:add|create|implement|write|define)\s+"
        r"(?:a\s+|an\s+|the\s+)?"
        r"(?:new\s+)?"
        r"function\s+"
        r"(?:called\s+|named\s+)"
        r"`?([A-Za-z_][A-Za-z0-9_]*)`?",
    ]

    # Never treat natural-language keywords as function names.
    reserved_words = {
        "new",
        "function",
        "called",
        "named",
        "python",
        "javascript",
        "typescript",
        "java",
        "c",
        "cpp",
        "golang",
        "rust",
        "ruby",
        "php",
        "swift",
        "kotlin",
        "scala",
        "sql",
        "bash",
        "powershell",
        "a",
        "an",
        "the",
    }
    for pattern in patterns:
        for match in re.finditer(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            name = match.group(1)

            if name.lower() not in reserved_words and name not in found:
                found.append(name)

    return found

def build_function_contract(function_names):
    """Create a deterministic function-name contract for the LLM."""

    names = ", ".join(
        f"`{name}`"
        for name in function_names
    )

    return f"""

DETERMINISTIC DEVELOPER FUNCTION CONTRACT:

The developer explicitly requested these NEW function(s):

{names}

This is authoritative.

If you use add_function:

- The function name MUST be one of the requested names above.
- Do NOT substitute an existing function.
- Do NOT generate build_report, validate_score, or another unrelated function.
- Do NOT modify an existing function unless the developer explicitly asked
  for that exact function.
- If tests are requested, put the tests in separate add_test edits.

Before returning JSON, compare every add_function function name with:

{names}

Any other function name is INVALID.
"""


def build_correction_prompt(
    original_prompt,
    required_functions,
    error,
):
    names = ", ".join(required_functions)

    signatures = []

    for function_name in required_functions:
        parameters = extract_requested_signature(
            original_prompt,
            function_name,
        )

        if parameters is None:
            signatures.append(
                f"{function_name}(...)"
            )
        else:
            signatures.append(
                f"{function_name}({', '.join(parameters)})"
            )

    signature_text = ", ".join(signatures)

    wants_tests = any(
        word in str(original_prompt).lower()
        for word in [
            "test",
            "tests",
            "unit test",
            "pytest",
        ]
    )

    test_text = ""

    if wants_tests:
        test_text = f"""
TEST REQUIREMENT:

The task explicitly requests tests.

Each add_test edit MUST contain exactly ONE Python test
function. If the task requests multiple test cases, include
all those cases as assertions inside that single test function.
Do not generate multiple top-level test functions in one add_test
edit. Use separate add_test edits only when separate test
functions are genuinely necessary.

The generated add_test code MUST actually call or reference
the requested function.

For the requested function:

{signature_text}

the test MUST contain a real function call.

For example:

assert {required_functions[0]}(...) == ...

Do NOT merely create a test named test_{required_functions[0]}.
Do NOT mention the function only in a comment.
The function itself must appear in executable test code.
"""

    return f"""
{original_prompt}

IMPORTANT CORRECTION:

Your previous response was rejected by the deterministic
contract validator.

Requested function(s):

{names}

Requested signature(s):

{signature_text}

Previous validation error:

{error}

Generate a corrected JSON object using the existing
structured edit schema.

FUNCTION RULES:

1. Generate ONLY the requested function(s).
2. Preserve the exact requested function name.
3. Preserve the exact requested parameter names.
4. Do not replace requested parameter names with names such
   as df, data, value, items, dataset, or input_data.
5. Do not generate unrelated functions.

{test_text}

Return JSON only.
"""

def validate_requested_function_edits(
    result,
    required_functions,
):
    """
    Deterministically verify that the model generated exactly the
    function requested by the developer.

    This validation checks:
      1. Function name
      2. Target file
      3. Function parameters
      4. No unrelated add_function edits
    """

    if not required_functions:
        return

    edits = result.get(
        "edits",
        [],
    )

    add_function_edits = []

    for edit in edits:
        if not isinstance(edit, dict):
            continue

        if edit.get("operation") != "add_function":
            continue

        code = str(
            edit.get("code", "")
        )

        names = extract_function_names(
            code
        )

        if len(names) != 1:
            raise TaskContractError(
                "An add_function edit must contain exactly one function."
            )

        add_function_edits.append(
            edit
        )

    # ---------------------------------------------------------
    # 1. Requested function must exist.
    # ---------------------------------------------------------

    generated_names = []

    for edit in add_function_edits:
        generated_names.extend(
            extract_function_names(
                edit.get("code", "")
            )
        )

    missing = [
        name
        for name in required_functions
        if name not in generated_names
    ]

    if missing:
        actual = (
            ", ".join(generated_names)
            if generated_names
            else "none"
        )

        raise WrongFunctionError(
            "The model did not generate the requested "
            f"function(s): {', '.join(missing)}. "
            f"Generated add_function name(s): {actual}."
        )

    # ---------------------------------------------------------
    # 2. No unrelated functions.
    # ---------------------------------------------------------

    unexpected = [
        name
        for name in generated_names
        if name not in required_functions
    ]

    if unexpected:
        raise WrongFunctionError(
            "The model generated an unrelated function: "
            f"{', '.join(unexpected)}. "
            "Only the function explicitly requested by "
            "the developer may be added."
        )

    # ---------------------------------------------------------
    # 3. Check target file.
    # ---------------------------------------------------------

    task = str(
        result.get("task", "")
    )

    target_file = extract_requested_target_file(
        task
    )

    if target_file:
        target_file = target_file.replace(
            "\\",
            "/",
        )

        matching_file = False

        for edit in add_function_edits:
            edit_path = str(
                edit.get("path", "")
            ).replace(
                "\\",
                "/",
            )

            if edit_path == target_file:
                matching_file = True
                break

        if not matching_file:
            actual_paths = [
                str(
                    edit.get("path", "")
                ).replace(
                    "\\",
                    "/",
                )
                for edit in add_function_edits
            ]

            raise TaskContractError(
                "The model generated the requested function "
                "but placed it in the wrong file. "
                f"Expected: {target_file}. "
                f"Generated path(s): {actual_paths or 'none'}."
            )

    # ---------------------------------------------------------
    # 4. Check exact requested parameters.
    # ---------------------------------------------------------

    for requested_name in required_functions:

        expected_parameters = (
            extract_requested_signature(
                task,
                requested_name,
            )
        )

        if expected_parameters is None:
            continue

        for edit in add_function_edits:

            functions = extract_function_definitions(
                edit.get("code", "")
            )

            for function in functions:

                if function.name != requested_name:
                    continue

                actual_parameters = [
                    argument.arg
                    for argument in function.args.args
                ]

                if actual_parameters != expected_parameters:
                    raise TaskContractError(
                        f"Function '{requested_name}' has "
                        f"parameters {actual_parameters}, "
                        f"but the task requested "
                        f"{expected_parameters}."
                    )

    # ---------------------------------------------------------
    # 5. Verify requested tests reference the function.
    # ---------------------------------------------------------

    task_lower = task.lower()

    asks_for_tests = any(
        word in task_lower
        for word in [
            "test",
            "tests",
            "unit test",
            "pytest",
        ]
    )

    if asks_for_tests:

        for requested_name in required_functions:

            found_test_reference = False

            for edit in edits:

                if edit.get("operation") != "add_test":
                    continue

                if _test_code_references_function(
                    edit.get("code", ""),
                    requested_name,
                ):
                    found_test_reference = True
                    break

            if not found_test_reference:
                raise TaskContractError(
                    "The task requested tests for "
                    f"'{requested_name}', but the generated "
                    "test code does not reference that function."
                )

def extract_function_names(code):
    """Extract top-level Python function names from a snippet."""

    import ast

    tree = ast.parse(
        str(code)
    )

    return [
        node.name
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    ]


class WrongFunctionError(ValueError):
    """Raised when the LLM ignores the requested function name."""


def is_wrong_function_error(error):
    return isinstance(
        error,
        WrongFunctionError,
    )


def generate_with_selected_provider(
    prompt,
    system_prompt,
):
    """Generate using the configured primary provider."""

    return generate_with_provider(
        AI_PROVIDER,
        prompt,
        system_prompt,
    )


def generate_with_provider(
    provider,
    prompt,
    system_prompt,
):
    """Generate using a specific provider."""

    provider = provider.lower()

    if provider == "openrouter":
        content = generate_with_openrouter(
            prompt,
            system_prompt,
        )

        return (
            content,
            OPENROUTER_MODEL,
            "openrouter",
        )

    if provider == "gemini":
        content = generate_with_gemini(
            prompt,
            system_prompt,
        )

        return (
            content,
            GEMINI_MODEL,
            "gemini",
        )

    if provider == "groq":
        content = generate_with_groq(
            prompt,
            system_prompt,
        )

        return (
            content,
            GROQ_MODEL,
            "groq",
        )

    if provider == "huggingface":
        content = generate_with_huggingface(
            prompt,
            system_prompt,
        )

        return (
            content,
            HF_MODEL,
            "huggingface",
        )

    if provider == "ollama":
        content = generate_with_ollama(
            prompt,
            system_prompt,
        )

        return (
            content,
            OLLAMA_MODEL,
            "ollama",
        )

    raise ValueError(
        f"Unsupported AI_PROVIDER: {provider}"
    )


def generate_with_groq(
    prompt,
    system_prompt,
):
    """Generate JSON through the Groq API."""

    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not configured."
        )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_object",
        },
    }

    body = json.dumps(payload).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    request = Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    with urlopen(request, timeout=90) as response:
        raw = response.read().decode("utf-8")
        data = json.loads(raw)

    choices = data.get("choices", [])

    if not choices:
        raise RuntimeError(
            "Groq returned no choices."
        )

    message = choices[0].get("message", {})
    content = message.get("content", "")

    if isinstance(content, list):
        content = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        )

    if not str(content).strip():
        raise RuntimeError(
            "Groq returned empty model output."
        )

    return str(content)


def generate_with_openrouter(
    prompt,
    system_prompt,
):
    """
    Generate structured JSON through OpenRouter.

    This implementation uses:
      1. Explicit coding models instead of openrouter/free random routing.
      2. OpenRouter model-level failover.
      3. Strict JSON Schema.
      4. Provider parameter enforcement.
      5. Response Healing for malformed JSON.
    """

    api_key = os.getenv(
        "OPENROUTER_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured."
        )

    # If Render still has the old openrouter/free environment value,
    # automatically replace it with the new deterministic coding model.
    configured_model = OPENROUTER_MODEL

    if configured_model in {
        "openrouter/free",
        "qwen/qwen3.8-27b:free",
        "qwen/qwen3.8-27b",
        "qwen/qwen3.6-plus-preview:free",
    }:
        configured_model = "qwen/qwen3.6-plus:free"

    # Build a deterministic model fallback list.
    models = []

    for model in [
        configured_model,
        *OPENROUTER_FALLBACK_MODELS,
    ]:
        if model and model not in models:
            models.append(model)

    # ---------------------------------------------------------
    # Strict schema for the Coding Agent result.
    # ---------------------------------------------------------

    coding_agent_schema = {
        "type": "object",
        "properties": {
            "plan": {
                "type": "array",
                "items": {
                    "type": "string"
                },
            },
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string"
                        },
                        "path": {
                            "type": "string"
                        },
                        "code": {
                            "type": "string"
                        },
                        "target": {
                            "type": "string"
                        },
                    },
                    "required": [
                        "operation",
                        "path",
                        "code",
                        "target",
                    ],
                    "additionalProperties": True,
                },
            },
            "explanation": {
                "type": "string"
            },
            "test_command": {
                "type": "string"
            },
        },
        "required": [
            "plan",
            "edits",
            "explanation",
            "test_command",
        ],
        "additionalProperties": True,
    }

    payload = {
        "models": models,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0,
        "max_tokens": 2000,

        # NEW:
        # Force OpenRouter to use a provider that supports
        # the structured-output parameters.
        "provider": {
            "require_parameters": True,
        },

        # NEW:
        # Strict JSON Schema instead of generic json_object.
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "coding_agent_result",
                "strict": True,
                "schema": coding_agent_schema,
            },
        },

        # NEW:
        # OpenRouter response healing helps repair malformed
        # structured responses before they reach our parser.
        "plugins": [
            {
                "id": "response-healing",
            }
        ],
    }

    body = json.dumps(
        payload
    ).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv(
            "OPENROUTER_SITE_URL",
            "https://codepilot-ai-coding-agent.onrender.com",
        ),
        "X-Title": "CodePilot AI Coding Agent",
    }

    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    last_error = None

    # OpenRouter handles model-level failover using the
    # "models" array. We additionally retry transient failures.
    for attempt in range(2):

        try:

            with urlopen(
                request,
                timeout=120,
            ) as response:

                raw = response.read().decode(
                    "utf-8"
                )

                data = json.loads(raw)

            choices = data.get(
                "choices",
                [],
            )

            if not choices:
                raise RuntimeError(
                    "OpenRouter returned no choices."
                )

            message = choices[0].get(
                "message",
                {},
            )

            content = message.get(
                "content",
                "",
            )

            # Some providers may return structured content
            # as a list of text blocks.
            if isinstance(
                content,
                list,
            ):
                content = "".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict)
                )

            if not str(content).strip():

                refusal = message.get(
                    "refusal"
                )

                if refusal:
                    raise RuntimeError(
                        "OpenRouter model refused the request: "
                        f"{refusal}"
                    )

                raise RuntimeError(
                    "OpenRouter returned empty model output."
                )

            # Verify JSON immediately before returning.
            # This prevents invalid content from reaching
            # the rest of the coding agent.
            try:
                json.loads(
                    str(content)
                )
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "OpenRouter returned invalid JSON "
                    "even after structured-output enforcement: "
                    f"{exc}"
                ) from exc

            # The actual model used can be returned by OpenRouter.
            actual_model = data.get(
                "model"
            ) or configured_model

            return str(content)

        except HTTPError as exc:

            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                error_body = ""

            error_message = error_body[:500]
            try:
                error_payload = json.loads(error_body)
                error_message = str(
                    error_payload.get("error", {}).get("message") or error_message
                )
            except (json.JSONDecodeError, AttributeError):
                pass

            last_error = f"OpenRouter HTTP {exc.code}: {error_message}"
            affordable = re.search(
                r"can only afford\s+(\d+)\s+tokens?",
                error_message,
                flags=re.IGNORECASE,
            )
            if exc.code == 402 and attempt == 0 and affordable:
                budget = int(affordable.group(1))
                reduced = budget - max(64, int(budget * 0.05))
                if 512 <= reduced < int(payload["max_tokens"]):
                    payload["max_tokens"] = reduced
                    body = json.dumps(payload).encode("utf-8")
                    request = Request(
                        "https://openrouter.ai/api/v1/chat/completions",
                        data=body, headers=headers, method="POST",
                    )
                    time.sleep(1)
                    continue
                last_error += (
                    ". Reported output budget is too small for a safe retry; "
                    "check OpenRouter credits or select a provider with working API access."
                )
                raise RuntimeError(last_error) from exc

            if exc.code in {408, 429, 500, 502, 503, 504} and attempt == 0:
                time.sleep(2)
                continue
            if exc.code == 402:
                last_error += (
                    ". Check OpenRouter account credits and model/provider pricing; "
                    "automatic retries cannot fix an exhausted balance."
                )
            raise RuntimeError(last_error) from exc

        except URLError as exc:

            last_error = (
                "OpenRouter network error: "
                f"{exc.reason}"
            )

            if attempt == 0:

                time.sleep(2)

                continue

            raise RuntimeError(
                last_error
            ) from exc

        except TimeoutError:

            last_error = (
                "OpenRouter request timed out."
            )

            if attempt == 0:

                time.sleep(2)

                continue

            raise RuntimeError(
                last_error
            )

        except json.JSONDecodeError as exc:

            last_error = (
                "OpenRouter returned invalid JSON: "
                f"{exc}"
            )

            if attempt == 0:

                time.sleep(2)

                continue

            raise RuntimeError(
                last_error
            ) from exc

        except RuntimeError as exc:

            last_error = str(exc)

            if attempt == 0:

                time.sleep(2)

                continue

            raise

    raise RuntimeError(
        last_error
        or "OpenRouter request failed."
    )

def _format_http_error(
    provider,
    error,
):
    """Return a useful provider error without exposing secrets."""

    try:
        body = error.read().decode(
            "utf-8",
            errors="replace",
        )

        try:
            payload = json.loads(body)

            error_data = payload.get(
                "error",
                {},
            )

            message = error_data.get(
                "message"
            )

            if message:
                return (
                    f"{provider} HTTP {error.code}: "
                    f"{message}"
                )

        except json.JSONDecodeError:
            pass

        return (
            f"{provider} HTTP {error.code}: "
            f"{body[:500]}"
        )

    except Exception:
        return (
            f"{provider} HTTP {error.code}"
        )


def generate_with_gemini(
    prompt,
    system_prompt,
):
    from google import genai
    from google.genai import types

    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    client = genai.Client(
        api_key=api_key
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
            max_output_tokens=4000,
            response_mime_type="application/json",
        ),
    )

    text = getattr(
        response,
        "text",
        None,
    )

    if not text:
        raise RuntimeError(
            "Gemini returned empty model output."
        )

    return text


def generate_with_ollama(
    prompt,
    system_prompt,
):
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        format="json",
        options={
            "temperature": 0,
            "num_predict": 4000,
        },
    )

    content = (
        response.get("message", {})
        .get("content", "")
    )

    if not content:
        raise RuntimeError(
            "Ollama returned empty model output."
        )

    return content


def generate_with_huggingface(
    prompt,
    system_prompt,
):
    from huggingface_hub import InferenceClient

    api_key = os.getenv(
        "HF_TOKEN"
    )

    if not api_key:
        raise RuntimeError(
            "HF_TOKEN is not configured."
        )

    client = InferenceClient(
        api_key=api_key,
    )

    response = client.chat.completions.create(
        model=HF_MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0,
        max_tokens=4000,
    )

    content = (
        response.choices[0]
        .message.content
    )

    if not content:
        raise RuntimeError(
            "Hugging Face returned empty model output."
        )

    return content



def normalize_generated_result(task, result):
    """Normalize safe multi-test edits and function-parameter mismatches."""

    # Split multiple test functions only when every top-level statement
    # is either a function definition or an import. Never discard other code.
    original_edits = result.get("edits", [])
    normalized_edits = []

    for edit in original_edits:
        if edit.get("operation") != "add_test":
            normalized_edits.append(edit)
            continue

        source = edit.get("code", "")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            normalized_edits.append(edit)
            continue

        functions = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        imports = [
            node for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]

        allowed_nodes = (
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.Import,
            ast.ImportFrom,
        )

        can_split = (
            len(functions) > 1
            and all(isinstance(node, allowed_nodes) for node in tree.body)
        )

        if not can_split:
            normalized_edits.append(edit)
            continue

        import_code = [ast.unparse(node) for node in imports]

        for function in functions:
            new_edit = dict(edit)
            pieces = import_code + [ast.unparse(function)]
            new_edit["code"] = "\\n\\n".join(pieces) + "\\n"
            normalized_edits.append(new_edit)

    result["edits"] = normalized_edits

    requested_functions = extract_requested_functions(task)
    if not requested_functions:
        return result

    for edit in result.get("edits", []):
        if edit.get("operation") != "add_function":
            continue

        source = edit.get("code", "")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        functions = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        if len(functions) != 1:
            continue

        function_node = functions[0]
        if function_node.name not in requested_functions:
            continue

        requested_params = extract_requested_signature(
            task, function_node.name
        )
        if requested_params is None:
            continue

        actual_args = (
            list(function_node.args.posonlyargs)
            + list(function_node.args.args)
        )
        if len(actual_args) != len(requested_params):
            continue

        rename_map = {
            actual.arg: requested
            for actual, requested in zip(actual_args, requested_params)
            if actual.arg != requested
        }
        if not rename_map:
            continue

        class RenameParameters(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id in rename_map:
                    node.id = rename_map[node.id]
                return self.generic_visit(node)

            def visit_arg(self, node):
                if node.arg in rename_map:
                    node.arg = rename_map[node.arg]
                return self.generic_visit(node)

        RenameParameters().visit(function_node)
        ast.fix_missing_locations(function_node)
        edit["code"] = ast.unparse(function_node)

    return result


def parse_model_json(content):
    """Parse JSON returned by the coding model."""

    if isinstance(content, dict):
        return content

    text = str(content).strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if (
            lines
            and lines[-1].strip() == "```"
        ):
            lines = lines[:-1]

        text = "\n".join(
            lines
        ).strip()

    try:
        data = json.loads(text)

        if not isinstance(data, dict):
            raise ValueError(
                "Model JSON must be an object."
            )

        return data

    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        candidate = text[
            start : end + 1
        ]

        try:
            data = json.loads(
                candidate
            )

            if isinstance(data, dict):
                return data

        except json.JSONDecodeError:
            pass

    raise ValueError(
        "The AI model did not return valid JSON."
    )


__all__ = [
    "generate_plan_and_patch",
    "parse_model_json",
]

