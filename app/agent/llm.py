from __future__ import annotations

import json
import os

import ollama

from .edit_engine import apply_structured_edits


OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash",
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
    from .prompts import (
        SYSTEM_PROMPT,
        build_analysis_prompt,
    )

    prompt = build_analysis_prompt(
        task,
        files,
    )

    try:
        # ----------------------------------------
        # Generate response from selected AI
        # ----------------------------------------

        if AI_PROVIDER == "gemini":
            content = generate_with_gemini(
                prompt,
                SYSTEM_PROMPT,
            )
            model_name = GEMINI_MODEL

        elif AI_PROVIDER == "huggingface":
            content = generate_with_huggingface(
                prompt,
                SYSTEM_PROMPT,
            )
            model_name = HF_MODEL

        else:
            content = generate_with_ollama(
                prompt,
                SYSTEM_PROMPT,
            )
            model_name = OLLAMA_MODEL

        # ----------------------------------------
        # Parse AI JSON response
        # ----------------------------------------

        result = parse_model_json(content)

        result.setdefault(
            "plan",
            [],
        )

        result.setdefault(
            "edits",
            [],
        )

        result.setdefault(
            "explanation",
            "",
        )

        result.setdefault(
            "test_command",
            "pytest -q",
        )

        # ----------------------------------------
        # Apply structured edits
        # ----------------------------------------

        changes = apply_structured_edits(
            result.get("edits", []),
            files,
            task=task,
        )

        # ----------------------------------------
        # Final result
        # ----------------------------------------

        result["changes"] = changes

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
                "CodePilot rejected the AI-generated edit "
                "because it was unsafe or invalid.\n\n"
                f"Reason: {exc}"
            ),
            "test_command": "pytest -q",
            "demo_mode": True,
            "model": (
                GEMINI_MODEL
                if AI_PROVIDER == "gemini"
                else (
                    HF_MODEL
                    if AI_PROVIDER == "huggingface"
                    else OLLAMA_MODEL
                )
            ),
            "provider": AI_PROVIDER,
            "task": task,
            "error": str(exc),
        }


# ============================================================
# Ollama
# ============================================================

def generate_with_ollama(
    prompt: str,
    system_prompt: str,
) -> str:

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


# ============================================================
# Gemini
# ============================================================

def generate_with_gemini(
    prompt: str,
    system_prompt: str,
) -> str:

    from google import genai
    from google.genai import types

    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:
        raise ValueError(
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

    if not response.text:
        raise ValueError(
            "Gemini returned an empty response."
        )

    return response.text


# ============================================================
# Hugging Face
# ============================================================

def generate_with_huggingface(
    prompt: str,
    system_prompt: str,
) -> str:

    from huggingface_hub import InferenceClient

    token = os.getenv(
        "HF_TOKEN"
    )

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


# ============================================================
# JSON Parser
# ============================================================

def parse_model_json(
    content: str,
) -> dict:

    text = str(content).strip()

    # Remove markdown JSON fences if the model adds them.
    if text.startswith("```"):

        lines = text.splitlines()[1:]

        if (
            lines
            and lines[-1].strip() == "```"
        ):
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    # First try normal JSON parsing.
    try:
        result = json.loads(text)

        if not isinstance(result, dict):
            raise ValueError(
                "AI response must be a JSON object."
            )

        return result

    except json.JSONDecodeError:
        pass

    # Try extracting the JSON object from extra text.
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:

        candidate = text[
            start:end + 1
        ]

        try:
            result = json.loads(
                candidate
            )

            if not isinstance(result, dict):
                raise ValueError(
                    "AI response must be a JSON object."
                )

            return result

        except json.JSONDecodeError:
            pass

    raise ValueError(
        "The AI model did not return valid JSON."
    )