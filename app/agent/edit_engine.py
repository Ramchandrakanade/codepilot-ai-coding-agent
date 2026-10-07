from __future__ import annotations

import ast
import difflib
import re


def _function_names(source: str) -> set[str]:
    tree = ast.parse(source)

    return {
        node.name
        for node in tree.body
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
    }


def _protected_names(task: str) -> set[str]:
    patterns = [
        r"\bdo not modify\s+`?([A-Za-z_]\w*)",
        r"\bdo not change\s+`?([A-Za-z_]\w*)",
        r"\bleave\s+`?([A-Za-z_]\w*)\s+unchanged",
    ]

    names = set()

    for pattern in patterns:
        names.update(
            re.findall(
                pattern,
                task,
                flags=re.IGNORECASE,
            )
        )

    return names


def _append_top_level_function(
    source: str,
    code: str,
) -> str:

    snippet = code.strip()

    tree = ast.parse(snippet)

    functions = [
        node
        for node in tree.body
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
    ]

    if len(functions) != 1:
        raise ValueError(
            "add_function/add_test must contain exactly one "
            "Python function."
        )

    existing = _function_names(source)

    function_name = functions[0].name

    if function_name in existing:
        raise ValueError(
            f"Function '{function_name}' already exists."
        )

    return (
        source.rstrip()
        + "\n\n"
        + snippet
        + "\n"
    )


def _normalize_import(code: str) -> str:
    """
    Extract valid Python import statements from model output.

    Handles common model mistakes such as:
    - markdown fences
    - JSON-style quoted strings
    - explanatory text around the import
    """

    text = str(code).strip()

    # Remove markdown fences.
    if text.startswith("```"):
        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    # Try the complete snippet first.
    try:
        tree = ast.parse(text)

        if tree.body and all(
            isinstance(
                node,
                (ast.Import, ast.ImportFrom),
            )
            for node in tree.body
        ):
            return text

    except SyntaxError:
        pass

    # Look for individual import lines.
    import_lines = []

    for line in text.splitlines():

        stripped = line.strip()

        if (
            stripped.startswith("import ")
            or stripped.startswith("from ")
        ):
            candidate = stripped.rstrip(";")

            try:
                ast.parse(candidate)

                import_lines.append(candidate)

            except SyntaxError:
                continue

    if import_lines:
        return "\n".join(import_lines)

    # Handle a quoted import returned by the model.
    match = re.search(
        r"(?:from\s+[A-Za-z_][\w.]*\s+import\s+[\w*, ]+)"
        r"|(?:import\s+[A-Za-z_][\w., ]*)",
        text,
    )

    if match:
        candidate = match.group(0).strip()

        try:
            ast.parse(candidate)
            return candidate
        except SyntaxError:
            pass

    raise ValueError(
        f"Could not extract a valid Python import from: {code!r}"
    )


def _add_import(
    source: str,
    code: str,
) -> str:

    snippet = _normalize_import(code)

    tree = ast.parse(snippet)

    if not tree.body or not all(
        isinstance(
            node,
            (ast.Import, ast.ImportFrom),
        )
        for node in tree.body
    ):
        raise ValueError(
            "add_import must contain only Python imports."
        )

    # Don't add an import that already exists.
    existing_imports = set()

    source_tree = ast.parse(source)

    for node in source_tree.body:

        if isinstance(
            node,
            (ast.Import, ast.ImportFrom),
        ):
            existing_imports.add(
                ast.unparse(node)
            )

    new_imports = []

    for node in tree.body:

        import_text = ast.unparse(node)

        if import_text not in existing_imports:
            new_imports.append(import_text)

    if not new_imports:
        return source

    lines = source.splitlines()

    insert_at = 0

    # Preserve module docstring.
    if (
        source_tree.body
        and isinstance(
            source_tree.body[0],
            ast.Expr,
        )
        and isinstance(
            getattr(
                source_tree.body[0],
                "value",
                None,
            ),
            ast.Constant,
        )
        and isinstance(
            source_tree.body[0].value.value,
            str,
        )
    ):
        insert_at = (
            source_tree.body[0].end_lineno
        )

    # Preserve future imports.
    for node in source_tree.body:

        if isinstance(
            node,
            ast.ImportFrom,
        ) and node.module == "__future__":
            insert_at = max(
                insert_at,
                node.end_lineno,
            )

    # Put normal imports after existing imports.
    for node in source_tree.body:

        if isinstance(
            node,
            (ast.Import, ast.ImportFrom),
        ):
            insert_at = max(
                insert_at,
                node.end_lineno,
            )

    import_text = "\n".join(
        new_imports
    )

    lines.insert(
        insert_at,
        import_text,
    )

    return "\n".join(lines) + (
        "\n"
        if source.endswith("\n")
        else ""
    )


def _python_module_from_path(path: str) -> str:
    """
    Convert a repository Python path into an importable module path.

    Example:
        qc_toolkit/report.py
        -> qc_toolkit.report
    """

    normalized = path.replace("\\", "/")

    if not normalized.endswith(".py"):
        raise ValueError(
            f"Cannot create Python module name from '{path}'."
        )

    module = normalized[:-3].replace("/", ".")

    if module.endswith(".__init__"):
        module = module[:-9]

    return module


def _ensure_test_imports(
    source: str,
    test_code: str,
    test_path: str,
    added_functions: dict[str, str],
) -> str:
    """
    Automatically import newly-added production functions used by tests.

    Example:

        add_function:
            qc_toolkit/report.py
            calculate_average

        add_test:
            tests/test_report_and_cli.py

    If the test references calculate_average(), this automatically
    adds:

        from qc_toolkit.report import calculate_average
    """

    if not added_functions:
        return source

    try:
        test_tree = ast.parse(test_code)
    except SyntaxError:
        return source

    # Names referenced anywhere in the generated test.
    referenced_names = {
        node.id
        for node in ast.walk(test_tree)
        if isinstance(node, ast.Name)
    }

    updated = source

    for function_name, source_path in added_functions.items():

        if function_name not in referenced_names:
            continue

        # Do not import a function from itself.
        if source_path == test_path:
            continue

        module = _python_module_from_path(source_path)

        import_statement = (
            f"from {module} import {function_name}"
        )

        updated = _add_import(
            updated,
            import_statement,
        )

    return updated


def _replace_function(
    source: str,
    target: str,
    code: str,
) -> str:

    if not target:
        raise ValueError(
            "replace_function requires a target."
        )

    replacement = code.strip()

    replacement_tree = ast.parse(
        replacement
    )

    functions = [
        node
        for node in replacement_tree.body
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
    ]

    if (
        len(functions) != 1
        or functions[0].name != target
    ):
        raise ValueError(
            f"Replacement must contain exactly one "
            f"function named '{target}'."
        )

    tree = ast.parse(source)

    target_node = None

    for node in tree.body:

        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ) and node.name == target:

            target_node = node
            break

    if target_node is None:
        raise ValueError(
            f"Function '{target}' was not found."
        )

    lines = source.splitlines(
        keepends=True
    )

    start = target_node.lineno - 1
    end = target_node.end_lineno

    lines[start:end] = [
        replacement.rstrip() + "\n"
    ]

    return "".join(lines)


def _validate_python(
    source: str,
    path: str,
) -> None:

    try:
        ast.parse(source)

    except SyntaxError as exc:

        raise ValueError(
            f"Generated code for '{path}' is invalid Python: "
            f"{exc}"
        ) from exc

    bad_tokens = [
        "TODO: implement",
        "pass  # implement",
        "your code here",
        "<IMPLEMENT>",
        "<YOUR_CODE>",
    ]

    for token in bad_tokens:

        if token.lower() in source.lower():

            raise ValueError(
                f"Generated file '{path}' contains "
                f"placeholder code: {token}"
            )


def apply_structured_edits(
    edits: list[dict],
    files: list[dict],
    task: str = "",
) -> list[dict]:

    file_map = {
        file["path"]: file.get(
            "content",
            "",
        )
        for file in files
    }

    original_map = dict(file_map)

    protected = _protected_names(task)

    # ---------------------------------------------------------
    # Track newly-added production functions.
    #
    # This allows a generated test to automatically import a
    # function that was created earlier in the same agent run.
    # ---------------------------------------------------------

    added_functions: dict[str, str] = {}

    for edit in edits:

        if edit.get("operation") != "add_function":
            continue

        edit_path = edit.get("path", "")
        edit_code = edit.get("code", "")

        try:
            added_tree = ast.parse(edit_code)

            for node in added_tree.body:

                if isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    added_functions[node.name] = edit_path

        except SyntaxError:
            # Normal validation will report the actual
            # syntax problem later.
            continue

    # ---------------------------------------------------------
    # Apply edits in order.
    # ---------------------------------------------------------

    for edit in edits:

        path = edit.get(
            "path",
            "",
        )

        operation = edit.get(
            "operation",
            "",
        )

        code = edit.get(
            "code",
            "",
        )

        target = edit.get(
            "target",
            "",
        )

        if path not in file_map:

            raise ValueError(
                f"Unknown file selected by model: {path}"
            )

        if not str(code).strip():

            raise ValueError(
                f"Empty code returned for {path}."
            )

        current = file_map[path]

        # -----------------------------------------------------
        # Add production function.
        # -----------------------------------------------------

        if operation == "add_function":

            updated = _append_top_level_function(
                current,
                code,
            )

        # -----------------------------------------------------
        # Add test function.
        #
        # Before adding the test, automatically add imports for
        # newly-created functions referenced by that test.
        # -----------------------------------------------------

        elif operation == "add_test":

            current = _ensure_test_imports(
                current,
                code,
                path,
                added_functions,
            )

            updated = _append_top_level_function(
                current,
                code,
            )

        # -----------------------------------------------------
        # Add import.
        # -----------------------------------------------------

        elif operation == "add_import":

            updated = _add_import(
                current,
                code,
            )

        # -----------------------------------------------------
        # Replace existing function.
        # -----------------------------------------------------

        elif operation == "replace_function":

            if target in protected:

                raise ValueError(
                    f"Safety check blocked modification "
                    f"of protected function '{target}'."
                )

            updated = _replace_function(
                current,
                target,
                code,
            )

        # -----------------------------------------------------
        # Append raw text.
        # -----------------------------------------------------

        elif operation == "append_text":

            updated = (
                current.rstrip()
                + "\n\n"
                + str(code).strip()
                + "\n"
            )

        else:

            raise ValueError(
                f"Unsupported edit operation "
                f"'{operation}' for '{path}'."
            )

        # -----------------------------------------------------
        # Validate Python syntax.
        # -----------------------------------------------------

        _validate_python(
            updated,
            path,
        )

        # -----------------------------------------------------
        # Safety check:
        # Existing functions must not disappear unless the
        # operation is explicitly replace_function.
        # -----------------------------------------------------

        old_functions = _function_names(
            current
        )

        new_functions = _function_names(
            updated
        )

        if operation != "replace_function":

            missing = (
                old_functions
                - new_functions
            )

            if missing:

                raise ValueError(
                    f"Safety check blocked edit to "
                    f"'{path}'. Existing functions "
                    f"disappeared: {sorted(missing)}"
                )

        file_map[path] = updated

    # ---------------------------------------------------------
    # Build unified diffs.
    # ---------------------------------------------------------

    changes = []

    for path, new_content in file_map.items():

        old_content = original_map[path]

        if new_content == old_content:
            continue

        diff = "".join(
            difflib.unified_diff(
                old_content.splitlines(
                    keepends=True
                ),
                new_content.splitlines(
                    keepends=True
                ),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )

        changes.append(
            {
                "path": path,
                "content": new_content,
                "diff": diff,
            }
        )

    return changes