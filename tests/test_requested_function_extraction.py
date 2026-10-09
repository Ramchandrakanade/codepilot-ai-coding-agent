from app.agent.llm import (
    extract_requested_functions,
    extract_requested_new_functions,
)


def test_extracts_name_from_python_function_request():
    task = "Write a Python function named is_even(number)."
    assert extract_requested_functions(task) == ["is_even"]


def test_extracts_explicit_function_name():
    task = "Add a calculate_average(numbers) function."
    assert extract_requested_functions(task) == ["calculate_average"]


def test_extracts_new_function_name_after_language_name():
    task = "Write a Python function named is_even(number)."
    assert extract_requested_new_functions(task) == ["is_even"]


def test_extracts_explicit_new_function_name():
    task = "Add a new function named validate_score(df)."
    assert extract_requested_new_functions(task) == ["validate_score"]
