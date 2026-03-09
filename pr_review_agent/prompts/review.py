"""Language-specific review hints for the reviewer."""

# Map of language to specific things to watch for
LANGUAGE_HINTS = {
    "python": """Python-specific checks:
- Missing import causing NameError (e.g., math.floor without import math)
- Mutable default arguments (def f(x=[]))
- Class field datetime.now() evaluated at definition time, not per-instance
- Missing await on async functions
- Django QuerySet does NOT support negative slicing
- queue.shutdown() doesn't exist in standard library
- Late binding closures in loops
- isinstance checks that are always true/false""",

    "go": """Go-specific checks:
- Race conditions: lock scope reduced, missing mutex. When a lock scope is reduced, check ALL readers/writers of the previously-protected resource for unsynchronized access
- Goroutine leaks (started but never cancelled)
- Nil pointer dereference (interface nil vs typed nil)
- Exec/Query args format: Exec(query, args...) not Exec(args...)
- Incomplete double-checked locking (must re-check after acquiring lock)
- Missing error return checks
- Context cancellation not propagated
- Concurrent map read/write without sync.Map or mutex
- File descriptor / connection leaks (defer close missing after open)""",

    "typescript": """TypeScript-specific checks:
- forEach with async callbacks does NOT await (use for...of)
- === compares object references, not values (dayjs objects need .isSame())
- Null/undefined from array access without length check
- Promise not awaited (missing await)
- Invalid Zod schema syntax (computed property keys)
- SafeParseResult vs unwrapped data confusion
- React: missing key prop in list rendering""",

    "ruby": """Ruby-specific checks:
- Method called on nil (find_by returns nil, then .method called)
- method redefinition silently overwrites previous def
- lifecycle callback registered on class/model that doesn't support it
- Missing ? suffix on predicate methods (Rails expects include_X? not include_X)
- Fabricator/factory defined for wrong model
- Regex anchoring: @(#{domains}) matches suffixes, not full domains
- String interpolation in SQL queries
- invalid ERB syntax (end if instead of end)
- Thread-safety: lazy @instance_variable without synchronization races under concurrent requests (use Mutex or eager init)
- Symbol vs String: :en != "en" in Ruby. Hash lookups and include? checks can silently fail when mixing Symbol keys with String values
- Return value changes: adding a new last expression in a method changes its return value. In around_action/around_filter, this can break the filter chain
- Locale loading: I18n backends may not be thread-safe for lazy loading""",

    "java": """Java-specific checks:
- NullPointerException: Optional.get() without isPresent()
- Method doesn't exist on the type (wrong class/interface)
- Wrong parameter in null check (checking param A instead of param B)
- Inverted substring/equality logic
- Missing abstract method implementation
- ConcurrentModificationException
- Contract violation (returning null when contract says non-null)""",
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
