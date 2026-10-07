from __future__ import annotations

import ast
import difflib
import json
import os

import ollama


OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "qwen2.5-coder:3b",
)

HF_MODEL = os.getenv(
    "HF_MODEL",
    "Qwen/Qwen2.5-Coder-3B-Instruct",
)

AI_PROVIDER = os.getenv(
    "AI_PROVIDER",
    "ollama",
).lower()


def generate_plan_and_patch(
    task: str,
    files: list[dict],
) -> dict:
    """Generate a coding-agent response using the configured AI provider."""

    from .prompts import (
        SYSTEM_PROMPT,
        build_analysis_prompt,
    )

    prompt = build_analysis_prompt(
        task,
        files,
    )

    try:
        if AI_PROVIDER == "huggingface":
            content = generate_with_huggingface(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
            )
            model_name = HF_MODEL

        else:
            content = generate_with_ollama(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
            )
            model_name = OLLAMA_MODEL

        result = parse_model_json(content)

        result.setdefault("plan", [])
        result.setdefault("changes", [])
        result.setdefault("explanation", "")
        result.setdefault("test_command", "pytest -q")

        safe_changes = []

        for change in result["changes"]:
            path = change.get("path")
            new_content = change.get("content")

            if not path or new_content is None:
                continue

            new_content = clean_file_content(new_content)

            original_file = next(
                (
                    file
                    for file in files
                    if file["path"] == path
                ),
                None,
            )

            if original_file is None:
                continue

            old_content = original_file["content"]

            # Safety check 1:
            # Reject placeholder implementations.
            if contains_placeholder_code(new_content):
                raise ValueError(
                    f"Unsafe AI response for {path}: "
                    "existing code was replaced with placeholder code."
                )

            # Safety check 2:
            # Make sure Python files remain valid Python.
            if path.endswith(".py"):
                try:
                    ast.parse(new_content)
                except SyntaxError as exc:
                    raise ValueError(
                        f"AI generated invalid Python in {path}: {exc}"
                    ) from exc

            # Safety check 3:
            # Make sure existing functions were not accidentally removed.
            if path.endswith(".py"):
                old_functions = get_function_names(old_content)
                new_functions = get_function_names(new_content)

                missing_functions = [
                    name
                    for name in old_functions
                    if name not in new_functions
                ]

                if missing_functions:
                    raise ValueError(
                        f"Unsafe AI response for {path}: "
                        f"existing functions were removed: "
                        f"{', '.join(missing_functions)}"
                    )

            change["content"] = new_content

            # Generate a real unified diff for the UI.
            diff = difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )

            change["patch"] = "".join(diff)

            safe_changes.append(change)

        result["changes"] = safe_changes
        result["demo_mode"] = False
        result["model"] = model_name
        result["provider"] = AI_PROVIDER
        result["task"] = task

        return result

    except Exception as exc:
        return {
            "plan": [],
            "changes": [],
            "explanation": (
                "CodePilot rejected the AI-generated change "
                "because it was unsafe or invalid.\n\n"
                f"Reason: {exc}"
            ),
            "test_command": "pytest -q",
            "demo_mode": True,
            "model": (
                HF_MODEL
                if AI_PROVIDER == "huggingface"
                else OLLAMA_MODEL
            ),
            "provider": AI_PROVIDER,
            "task": task,
            "error": str(exc),
        }


def generate_with_ollama(
    prompt: str,
    system_prompt: str,
) -> str:
    """Generate JSON using the local Ollama server."""

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

    return response["message"]["content"]


def generate_with_huggingface(
    prompt: str,
    system_prompt: str,
) -> str:
    """Generate JSON using Hugging Face Inference Providers."""

    from huggingface_hub import InferenceClient

    token = os.getenv("HF_TOKEN")

    if not token:
        raise ValueError(
            "HF_TOKEN is not configured."
        )

    client = InferenceClient(
        api_key=token,
        provider="auto",
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

    return response.choices[0].message.content


def contains_placeholder_code(content: str) -> bool:
    """
    Detect incomplete code returned by the model.

    Examples:
        ...
        function_body = ...
        # Existing implementation
    """

    lines = [
        line.strip()
        for line in content.splitlines()
    ]

    for line in lines:
        if line == "...":
            return True

        if "# Existing implementation" in line:
            return True

        if "# existing implementation" in line:
            return True

    return False


def get_function_names(content: str) -> set[str]:
    """Return function names from a Python file."""

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()

    names = set()

    for node in ast.walk(tree):
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ):
            names.add(node.name)

    return names


def clean_file_content(content: str) -> str:
    """Remove accidental Markdown code fences."""

    content = str(content).strip()

    if content.startswith("```"):
        lines = content.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        content = "\n".join(lines).strip()

    return content + "\n"


def parse_model_json(content: str) -> dict:
    """Parse JSON returned by the AI model."""

    text = content.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    try:
        return json.loads(text)

    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start != -1 and end > start:
            candidate = text[start:end + 1]

            try:
                return json.loads(candidate)

            except json.JSONDecodeError as exc:
                raise ValueError(
                    "The AI model returned malformed JSON."
                ) from exc

        raise ValueError(
            "The AI model did not return valid JSON."
        )