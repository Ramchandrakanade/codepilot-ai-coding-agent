# Regression tests for structured-edit target validation.
import pytest
from app.agent.llm import TaskContractError, validate_edit_targets

def test_replace_function_requires_a_target():
    result = {"edits": [{"operation": "replace_function", "path": "app.py", "code": "def calculate(value):\n    return value + 1", "target": ""}]}
    files = [{"path": "app.py", "content": "def calculate(value):\n    return value\n"}]
    with pytest.raises(TaskContractError, match="without a target"):
        validate_edit_targets(result, files)

def test_replace_function_target_must_exist_in_source():
    result = {"edits": [{"operation": "replace_function", "path": "app.py", "code": "def missing(value):\n    return value + 1", "target": "missing"}]}
    files = [{"path": "app.py", "content": "def calculate(value):\n    return value\n"}]
    with pytest.raises(TaskContractError, match="does not exist"):
        validate_edit_targets(result, files)

def test_replacement_code_must_define_the_named_target():
    result = {"edits": [{"operation": "replace_function", "path": "app.py", "code": "def other(value):\n    return value + 1", "target": "calculate"}]}
    files = [{"path": "app.py", "content": "def calculate(value):\n    return value\n"}]
    with pytest.raises(TaskContractError, match="exactly one replacement"):
        validate_edit_targets(result, files)

def test_valid_replace_function_target_is_accepted():
    result = {"edits": [{"operation": "replace_function", "path": "app.py", "code": "def calculate(value):\n    return value + 1", "target": "calculate"}]}
    files = [{"path": "app.py", "content": "def calculate(value):\n    return value\n"}]
    assert validate_edit_targets(result, files) is None

def test_non_replace_edits_do_not_need_a_target():
    result = {"edits": [{"operation": "add_function", "path": "app.py", "code": "def extra():\n    return True", "target": ""}]}
    assert validate_edit_targets(result, [{"path": "app.py", "content": ""}]) is None
import app.agent.llm as llm
import app.agent.prompts as prompts


def _prepare_recovery_test(monkeypatch):
    monkeypatch.setattr(
        prompts,
        "build_analysis_prompt",
        lambda task, files: "Test coding task",
    )
    monkeypatch.setattr(
        llm,
        "extract_requested_new_functions",
        lambda task: [],
    )
    monkeypatch.setattr(
        llm,
        "normalize_generated_result",
        lambda task, result: result,
    )
    monkeypatch.setattr(
        llm,
        "parse_model_json",
        lambda content: {"edits": [{"generated": content}]},
    )
    monkeypatch.setattr(
        llm,
        "validate_task_contract",
        lambda task, result: True,
    )


def test_malformed_target_is_corrected(monkeypatch):
    _prepare_recovery_test(monkeypatch)
    calls = []

    def generate(prompt, system_prompt):
        calls.append(prompt)
        return (
            "initial" if len(calls) == 1 else "corrected",
            "mock-model",
            "mock-provider",
        )

    monkeypatch.setattr(
        llm,
        "generate_with_selected_provider",
        generate,
    )

    applied = []

    def apply(result, task, files, model, provider):
        applied.append(result["edits"][0]["generated"])
        if len(applied) == 1:
            raise llm.TaskContractError(
                "Edit 1 uses replace_function without a target."
            )
        return {"success": True}

    monkeypatch.setattr(llm, "apply_result", apply)

    result = llm.generate_plan_and_patch(
        "Fix the existing calculation",
        [{"path": "app.py", "content": "def calculate():\n    return 1\n"}],
    )

    assert result == {"success": True}
    assert len(calls) == 2
    assert "IMPORTANT CORRECTION" in calls[1]
    assert applied == ["initial", "corrected"]


def test_failed_target_correction_uses_provider_fallback(monkeypatch):
    _prepare_recovery_test(monkeypatch)
    monkeypatch.setattr(llm, "AI_PROVIDER", "openrouter")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    selected_calls = []
    fallback_calls = []
    applied = []

    def generate_selected(prompt, system_prompt):
        selected_calls.append(prompt)
        return (
            f"selected-{len(selected_calls)}",
            "mock-model",
            "openrouter",
        )

    def generate_fallback(provider, prompt, system_prompt):
        fallback_calls.append(provider)
        return "fallback", "mock-model", provider

    def apply(result, task, files, model, provider):
        applied.append(provider)
        if len(applied) <= 2:
            raise llm.TaskContractError(
                "Edit 1 uses replace_function without a target."
            )
        return {"success": True, "provider": provider}

    monkeypatch.setattr(
        llm,
        "generate_with_selected_provider",
        generate_selected,
    )
    monkeypatch.setattr(
        llm,
        "generate_with_provider",
        generate_fallback,
    )
    monkeypatch.setattr(llm, "apply_result", apply)

    result = llm.generate_plan_and_patch(
        "Fix the existing calculation",
        [{"path": "app.py", "content": "def calculate():\n    return 1\n"}],
    )

    assert result == {"success": True, "provider": "gemini"}
    assert len(selected_calls) == 2
    assert fallback_calls == ["gemini"]
    assert len(applied) == 3

def test_apply_result_rejects_invalid_replacement_before_applying(monkeypatch):
    files = [
        {
            "path": "app.py",
            "content": "def calculate():\n    return 1\n",
        }
    ]
    result = {
        "plan": [],
        "edits": [
            {
                "operation": "replace_function",
                "path": "app.py",
                "target": "missing_function",
                "code": "def missing_function():\n    return 2\n",
            }
        ],
        "explanation": "Update calculation",
        "test_command": "pytest -q",
    }

    applied = []

    def should_not_apply(*args, **kwargs):
        applied.append(True)
        raise AssertionError(
            "Edits must not be applied for an invalid target"
        )

    monkeypatch.setattr(
        llm,
        "apply_structured_edits",
        should_not_apply,
    )

    with pytest.raises(llm.TaskContractError, match="does not exist"):
        llm.apply_result(
            result,
            "Fix the existing calculation",
            files,
            "mock-model",
            "mock-provider",
        )

    assert applied == []
