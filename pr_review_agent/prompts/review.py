"""Language-specific review hints for the reviewer."""

# Map of language to specific things to watch for
LANGUAGE_HINTS = {
    "python": """Python-specific checks:
- Division by zero (use of / without checking denominator)
- Mutable default arguments (def f(x=[]))
- Late binding closures in loops
- Missing await on async functions
- Dictionary key errors (missing .get() or key checks)
- Type coercion issues (str vs bytes, int vs float)
- Context manager misuse (missing __exit__, unclosed resources)
- Django/Flask specific: SQL injection in raw queries, missing CSRF""",

    "go": """Go-specific checks:
- Unchecked error returns (err ignored after function call)
- Goroutine leaks (goroutine started but never joined/cancelled)
- Race conditions on shared state (missing mutex/channel)
- Nil pointer dereference (interface nil vs typed nil)
- Slice append gotchas (shared underlying array)
- Context cancellation not propagated
- Deferred function call order (LIFO)""",

    "typescript": """TypeScript-specific checks:
- Null/undefined not handled (missing ?. or nullish checks)
- Type assertions hiding bugs (as SomeType without validation)
- Promise not awaited (missing await)
- Array index out of bounds (no length check)
- Event handler memory leaks (missing removeEventListener)
- React-specific: stale closures in useEffect, missing dependencies
- Incorrect type narrowing""",

    "ruby": """Ruby-specific checks:
- NoMethodError on nil (missing &. or nil checks)
- Incorrect use of == vs === vs eql?
- Thread safety issues with shared mutable state
- ActiveRecord N+1 queries
- Missing strong parameters in controllers
- SQL injection in string interpolation
- Symbol/string key confusion in hashes""",

    "java": """Java-specific checks:
- NullPointerException (missing null checks)
- Unchecked casts (ClassCastException)
- Resource leaks (missing try-with-resources)
- ConcurrentModificationException (modifying collection during iteration)
- Incorrect equals/hashCode implementations
- Thread safety issues (missing synchronized/volatile)
- Incorrect generics usage (type erasure issues)""",
}


def get_language_from_path(file_path: str) -> str:
    """Detect language from file extension."""
    ext_map = {
        ".py": "python",
        ".go": "go",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "typescript",
        ".jsx": "typescript",
        ".rb": "ruby",
        ".java": "java",
        ".kt": "java",
        ".scala": "java",
    }
    for ext, lang in ext_map.items():
        if file_path.endswith(ext):
            return lang
    return "unknown"


def get_language_hint(language: str) -> str:
    """Get language-specific review hints."""
    return LANGUAGE_HINTS.get(language, "")
