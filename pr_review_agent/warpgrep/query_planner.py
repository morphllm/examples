"""Generate targeted WarpGrep queries from PR diffs."""

from __future__ import annotations

import re


def plan_queries(file_path: str, diff_content: str, max_queries: int = 3) -> list[str]:
    """Generate WarpGrep queries to gather context for reviewing a diff.

    Extracts function names, class names, imports, and changed identifiers
    from the diff to build targeted search queries.

    Args:
        file_path: Path of the changed file.
        diff_content: The unified diff content.
        max_queries: Maximum number of queries to generate.

    Returns:
        List of search query strings.
    """
    queries = []

    # Extract function/method definitions from changed lines
    func_names = _extract_functions(diff_content)
    for name in func_names[:2]:
        queries.append(f"callers of {name} and its usage patterns")

    # Extract class names
    class_names = _extract_classes(diff_content)
    for name in class_names[:1]:
        queries.append(f"class {name} definition and subclasses")

    # Extract imported modules/types from changed lines
    imports = _extract_imports(diff_content)
    for imp in imports[:1]:
        queries.append(f"how is {imp} used in this codebase")

    # Extract type names from annotations
    types = _extract_type_annotations(diff_content)
    for t in types[:1]:
        queries.append(f"definition and usage of type {t}")

    # If we have few queries, add a general one about the file
    if len(queries) < 2:
        module_name = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        queries.append(f"tests for {module_name} and related test patterns")

    return queries[:max_queries]


def _extract_functions(diff: str) -> list[str]:
    """Extract function/method names from changed lines."""
    patterns = [
        r'^\+.*def\s+(\w+)',           # Python
        r'^\+.*func\s+(\w+)',          # Go
        r'^\+.*function\s+(\w+)',      # JS/TS
        r'^\+.*(?:public|private|protected)\s+\w+\s+(\w+)\s*\(', # Java
        r'^\+.*def\s+(\w+)',           # Ruby
    ]
    names = []
    for line in diff.split("\n"):
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                name = match.group(1)
                if name not in ("__init__", "main", "test", "setup"):
                    names.append(name)
    return list(dict.fromkeys(names))  # dedupe preserving order


def _extract_classes(diff: str) -> list[str]:
    """Extract class names from changed lines."""
    patterns = [
        r'^\+.*class\s+(\w+)',
        r'^\+.*interface\s+(\w+)',
        r'^\+.*struct\s+(\w+)',
    ]
    names = []
    for line in diff.split("\n"):
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                names.append(match.group(1))
    return list(dict.fromkeys(names))


def _extract_imports(diff: str) -> list[str]:
    """Extract imported module/type names from changed lines."""
    patterns = [
        r'^\+.*from\s+(\S+)\s+import',       # Python
        r'^\+.*import\s+"([^"]+)"',            # Go
        r'^\+.*import\s+{([^}]+)}',            # JS/TS
        r'^\+.*import\s+(\S+)',                 # Java
        r'^\+.*require\s+["\']([^"\']+)',       # Ruby
    ]
    names = []
    for line in diff.split("\n"):
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                val = match.group(1).strip().split(",")[0].strip()
                if val and len(val) < 60:
                    names.append(val)
    return list(dict.fromkeys(names))


def _extract_type_annotations(diff: str) -> list[str]:
    """Extract type names from annotations in changed lines."""
    patterns = [
        r'^\+.*:\s*(\w+(?:\[\w+\])?)\s*[=,)]',  # Python type hints
        r'^\+.*\)\s*(\w+)\s*{',                     # Go return types
        r'^\+.*:\s*(\w+(?:<\w+>)?)\s*[=;]',         # TS/Java types
    ]
    names = []
    for line in diff.split("\n"):
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                t = match.group(1)
                # Skip primitives
                if t.lower() not in ("str", "int", "float", "bool", "none", "void",
                                     "string", "number", "boolean", "any", "object"):
                    names.append(t)
    return list(dict.fromkeys(names))
