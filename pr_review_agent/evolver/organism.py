"""Prompt organism for evolutionary code review improvement."""

from __future__ import annotations

from pydantic import computed_field

from darwinian_evolver.problem import EvaluationFailureCase, Organism
from darwinian_evolver.learning_log import LearningLogEntry

from pr_review_agent.prompts.system import SYSTEM_PROMPT


# Default review instructions (pass1 body) - extracted from reviewer.py
DEFAULT_REVIEW_INSTRUCTIONS = """Focus ONLY on changed lines (+ and - lines). Find issues that WILL cause incorrect behavior:

1. **Wrong variable/value**: Copy-paste errors, wrong parameter checked, wrong string constant, wrong method called (e.g. recordLegacyDuration vs recordStorageDuration), returning original instead of modified copy
2. **Wrong locale/translation**: Text in wrong language for locale file
3. **Inverted/wrong logic**: Condition backwards (AND vs OR), function always returns same value, unreachable branches, inverted comparison
4. **API misuse**: Method doesn't exist, wrong parameters, abstract class not implemented, forEach with async (needs for...of + await)
5. **Race condition**: Lock scope reduced, thread not joined, stale reads under concurrency, double-checked locking missing second check
6. **Null/nil dereference**: Accessing member on value that IS null/undefined/nil in a concrete path
7. **Type mismatch**: math ops on datetime, Django negative QuerySet slice, object reference comparison instead of value comparison (=== on dayjs), case-sensitive comparison when case-insensitive needed
8. **Security**: SSRF (open with user URL), X-Frame-Options weakened, permission check removed, auth bypass, origin validation bypassable
9. **Broken test**: Name typo, comment contradicts assertion, HTTP method mismatch, mock makes test vacuous, monkeypatch invalidates test logic
10. **Framework bugs**: Class field evaluated at definition time (datetime.now as default), missing import, fabricator in wrong file, Rails missing ? suffix on predicate
11. **Contract violations**: Returns null when contract says non-null, deletion logic affects wrong types, side effects in read-only method, empty data prevents @updatedAt
12. **CSS bugs**: Wrong lightness/color value in conversion, mixing float with flexbox, invalid vendor prefix

## Also Report
- Method/function name typos that affect behavior (test won't be discovered, method won't match interface)
- Property name typos (misspelled identifiers)
- Dead code where results are computed but discarded
- Docstring/comment that contradicts what code actually does
- Wrong log level (Error for non-error information)
- Hardcoded values that ignore configurable settings
- Interface contract changes that break existing implementations

## CRITICAL: Avoid False Positives
- Do NOT claim a variable/function/import is "undefined" or "missing" unless you can PROVE it from the diff. The diff only shows changed lines - the rest of the file exists but isn't shown. If a function is called and you don't see its definition in the diff, it's almost certainly defined elsewhere.
- Do NOT claim a variable is "used before defined" unless you can prove the ordering from the diff.
- Do NOT claim a module is "not imported" unless you see the file's complete import section in the diff and it's definitely missing.
- Do NOT suggest defensive null checks ("X could be null") unless you can trace a CONCRETE path where null arrives.
- Do NOT suggest "consider using X instead of Y" when Y works correctly.
- Do NOT report speculative edge cases ("if input is empty/malformed...") without evidence they occur.
- Do NOT report the same bug multiple times across different files. If forEach+async appears in 3 files, report it ONCE for the most important file.
- When in doubt about whether something exists outside the diff, DO NOT report it.

## Rules
- Point to the EXACT line and explain WHY it's wrong
- Each comment must be SELF-CONTAINED
- Return [] if no real bugs exist
- Quality over quantity: 3 real bugs > 8 questionable ones
- Do NOT report: pure formatting preferences, defensive programming for impossible paths, general performance suggestions, "consider" suggestions"""


# Default judge prompt - extracted from reviewer.py
DEFAULT_JUDGE_PROMPT = """## KEEP an issue ONLY if ALL of these are true:
1. The specific code element mentioned actually exists in the diff
2. The claimed behavior is provably wrong from the code shown
3. It causes incorrect runtime behavior, data corruption, security vulnerability, or broken tests
4. The issue is in CHANGED code (+ or - lines), not unchanged context

## REMOVE an issue if ANY of these patterns match:

**Pattern A - Hallucinated entities:**
"Variable X is undefined" / "function Y doesn't exist" / "Z is not imported" - the diff only shows CHANGED lines. Variables, functions, and imports almost certainly exist outside the diff context. REMOVE unless you see the COMPLETE import section and can confirm the import is truly missing.

**Pattern B - Defensive programming:**
"Should add null check" / "could be null" / "might throw" / "potential NPE" - unless you can trace a CONCRETE code path where null/undefined actually arrives. "Could theoretically be null" is not a bug. REMOVE.

**Pattern C - Style preferences:**
"Should use X instead of Y" / "consider using" / "would be better to" - when the current code works correctly. REMOVE.

**Pattern D - Speculative edge cases:**
"If the input is empty/null/malformed..." / "cache collision possible" / "parameter limit could be exceeded" - theoretical problems without evidence they occur. REMOVE.

**Pattern E - Performance / best practices:**
"Inefficient algorithm" / "unnecessary allocation" / "should cache" - not bugs. REMOVE.

**Pattern F - Duplicate of another issue:**
If two issues describe the SAME bug at the SAME location (or same conceptual bug across files), keep only the better-written one. REMOVE the duplicate.

## TRUE BUG examples (keep these):
- "forEach with async: callbacks aren't awaited" -> KEEP (provable API misuse)
- "sed -i '' fails on Linux" -> KEEP (concrete portability bug)
- "Italian text in Lithuanian locale file" -> KEEP (wrong language, provable)
- "recordLegacyDuration called instead of recordStorageDuration" -> KEEP (wrong method)
- "enableSqlExpressions always returns false" -> KEEP (inverted logic, provable)
- "Test comment says 'allow' but map stores false" -> KEEP (contradiction, provable)
- "Method name typo: santizeAnchors should be sanitizeAnchors" -> KEEP (real typo)

## FALSE POSITIVE examples (remove these):
- "config variable is used but never defined" -> REMOVE (defined outside diff)
- "Should validate input before database call" -> REMOVE (defensive)
- "Potential null pointer if X returns null" -> REMOVE (speculative)
- "Consider using const instead of let" -> REMOVE (style)
- "ctx is undefined in this scope" -> REMOVE (likely defined in surrounding code)
- "Missing error handling for edge case" -> REMOVE (defensive)"""


class CodeReviewOrganism(Organism):
    """An evolving code review prompt configuration.

    Fields that get mutated by the evolver:
    - system_prompt: The main system prompt (biggest lever)
    - review_instructions: Pass-specific review instructions (what to look for)
    - judge_prompt: The validator prompt that removes FPs
    - confidence_threshold: Base confidence threshold for filtering
    - num_passes: Number of review passes
    - max_issues_per_pr: Cap on issues per PR
    """

    system_prompt: str
    review_instructions: str
    judge_prompt: str
    confidence_threshold: float = 0.50
    num_passes: int = 4
    max_issues_per_pr: int = 6

    # Mutation diagnostics (not evolved, just for tracking)
    from_failure_diagnosis: str | None = None

    @computed_field
    @property
    def visualizer_props(self) -> dict[str, str | float]:
        return {
            "confidence_threshold": self.confidence_threshold,
            "num_passes": float(self.num_passes),
            "max_issues_per_pr": float(self.max_issues_per_pr),
            "system_prompt_len": float(len(self.system_prompt)),
            "review_instructions_len": float(len(self.review_instructions)),
            "judge_prompt_len": float(len(self.judge_prompt)),
        }


def make_initial_organism() -> CodeReviewOrganism:
    """Create the initial organism from current production prompts."""
    return CodeReviewOrganism(
        system_prompt=SYSTEM_PROMPT,
        review_instructions=DEFAULT_REVIEW_INSTRUCTIONS,
        judge_prompt=DEFAULT_JUDGE_PROMPT,
        confidence_threshold=0.50,
        num_passes=4,
        max_issues_per_pr=6,
    )
