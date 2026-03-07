"""LLM-powered mutator for evolving code review prompts."""

from __future__ import annotations

import json
import random
import re

import anthropic
from pydantic import ConfigDict

from darwinian_evolver.learning_log import LearningLogEntry
from darwinian_evolver.problem import Mutator

from pr_review_agent.evolver.failure_case import CodeReviewFailureCase
from pr_review_agent.evolver.organism import CodeReviewOrganism


MUTATION_PROMPT_TEMPLATE = """You are improving a code review AI's prompts to increase its F1 score on the withmartian/code-review-benchmark.

## Benchmark Context

The benchmark has 50 PRs across 5 repos (Python, Go, TypeScript, Ruby, Java) with 137 human-verified golden comments. Each golden comment represents a real bug with severity (Critical/High/Medium/Low). An LLM judge determines if our comments SEMANTICALLY match golden comments -- exact file/line accuracy is NOT required, only that the comment describes the same underlying issue. F1 = harmonic mean of precision and recall.

**Current leaders:** Propel 64% F1 (68% precision, 61% recall), Augment 59% (47% precision, 62.8% recall).

## CRITICAL: Our Performance Gap

**Our precision (47.8%) already matches Augment's (47.0%). Our problem is RECALL (46.7% vs their 62.8%).**

We find 64 of 137 golden issues. We need ~25 MORE true positives to reach 64% F1. This means:
- We are currently TOO CONSERVATIVE. We generate only 134 candidates; we should generate 160-180.
- The golden set is 0% style/formatting -- all 137 are real bugs. But our FP-avoidance instructions may be causing us to SKIP real bugs.
- ~25-30 of our 74 FPs are DUPLICATES from multi-pass (same bug reported 2-3x). Fixing dedup cuts FPs WITHOUT losing TPs.
- **The optimal mutation improves BOTH: more aggressive bug detection (recall) AND better dedup (precision).**

## Current Configuration

### System Prompt (defines the reviewer's identity and output format)
```
{system_prompt}
```

### Review Instructions (what to look for in each review pass)
```
{review_instructions}
```

### Judge Prompt (validates and filters issues to remove false positives)
```
{judge_prompt}
```

### Numeric Parameters
- confidence_threshold: {confidence_threshold} (base threshold for filtering, 0.0-1.0)
- num_passes: {num_passes} (number of review passes, 2-6)
- max_issues_per_pr: {max_issues_per_pr} (cap per PR, 3-10)

## Failure Cases to Address

These are concrete examples where the current prompts fail:

{failure_cases_text}

## Learning Log (what worked/didn't work in past mutations)

{learning_log_text}

## Mutation Strategies (choose 1-3, prioritize RECALL strategies)

### RECALL Strategies (our primary bottleneck -- we miss 73 golden issues)

**Strategy R1: More Aggressive Bug Detection**
We are too conservative. The review instructions contain many "Do NOT report" rules that suppress real bugs alongside false positives. Consider:
- Relaxing overly cautious FP-avoidance rules that also suppress real bugs
- Lowering confidence_threshold (currently {confidence_threshold}) to catch marginal issues
- Increasing max_issues_per_pr (currently {max_issues_per_pr}; golden avg is 2.7 but some PRs have 6+)
- Adding: "When uncertain whether something is a bug, REPORT IT with appropriate confidence. The judge will filter FPs."

**Strategy R2: Expand Bug Pattern Coverage**
We miss entire categories of bugs. Add detection rules for patterns we currently skip:
- Contract violations: method signature changes that break callers/implementors
- Recursive self-calls: calling self instead of delegate/wrapped object
- Error path bugs: error handlers that corrupt state, cache nil results, or leak resources
- Behavioral regressions: refactoring that silently changes behavior (async->sync, permission removed)
- Framework-specific: class fields evaluated at definition time, System.exit() in libraries, thread-unsafe lazy init

**Strategy R3: Don't Skip Clean-Looking PRs**
We submitted 0 candidates for 3 PRs that have golden comments. Consider:
- Adding: "Every PR in this benchmark has at least one real bug. If you find nothing, look harder."
- Increasing num_passes to give more chances to find subtle bugs

**Strategy R4: Cross-File Reasoning**
We miss bugs requiring multi-file understanding. Consider:
- When a method signature or return type changes, trace ALL callers and implementors
- When a permission check or validation is removed, flag as security regression
- When error handling changes, verify the error path preserves valid state

### PRECISION Strategies (secondary -- fix FPs through dedup, not by rejecting more)

**Strategy P1: Deduplication (cuts ~25-30 FPs without losing TPs)**
Our multi-pass system flags the same bug 2-3x. The judge MUST merge duplicates:
- Same file + same issue = keep only the best-written one
- Same conceptual bug in multiple files = report once for the most important file
- Example: forEach-with-async reported 9 times across 3 files x 3 passes. Should be 1 comment.

**Strategy P2: Suppress Style/Formatting Only**
The golden set has ZERO style/formatting issues. Suppress:
- "CSS selector removed" (that's the intended change)
- "Duplicated if condition" (dead code, not a bug)
- "Consider using X instead of Y" (style preference)
But do NOT suppress: wrong values, wrong variables, copy-paste errors, wrong locale text -- these ARE real bugs.

**Strategy P3: Suppress Speculative-Only Issues**
Remove comments that say "could be null" / "might fail" WITHOUT evidence. But be careful:
- "X could be null" with no evidence -> REMOVE
- "X IS null when Y happens" with traced path -> KEEP (this is a real bug)
- The line between speculation and real bug detection is thin. Err toward KEEPING.

## Concrete Failure Examples

### FPs to eliminate (duplicates and style):
- **Duplicate: cal.com PR#8087** -- forEach-with-async reported 9 TIMES. Should be 1.
- **Duplicate: discourse PR#7** -- Wrong CSS lightness reported 10 TIMES. Should be ~4.
- **Style: cal.com PR#10967** -- "Duplicated if condition" -- dead code, not a bug
- **Style: discourse PR#5** -- "CSS selector removed without replacement" -- intended change

### FNs to start catching (real bugs we miss):
- **Contract violation: cal.com PR#10967** -- Calendar interface requires createEvent(event, credentialId) but implementations only have createEvent(event)
- **Recursive self-call: keycloak PR#32918** -- Recursive caching using `session` instead of `delegate` (CRITICAL severity, missed)
- **Wrong return: keycloak PR#33832** -- Returns default keystore provider instead of BouncyCastle
- **Error path: grafana PR#90939** -- Error handler caches nil, overwriting valid cache
- **System.exit: keycloak PR#36882** -- picocli.exit() calls System.exit() directly (ONLY golden comment, we found 0)
- **Thread safety: discourse PR#9** -- Lazy @loaded_locales without synchronization (we found 0)
- **Compile error: grafana PR#79265** -- dbSession.Exec(args...) given []interface{{}} where string expected (missed)
- **Missing null guard: grafana PR#76186** -- ContextualLoggerMiddleware panics on nil request (we found 0)
- **Race condition: cal.com PR#10600** -- Backup code race condition for concurrent use (missed)
- **Case sensitivity: cal.com PR#10600** -- Case-sensitive backup code validation (missed)
- **Side effect: cal.com PR#14740** -- Notification emails sent to existing attendees on reschedule (missed)

## Your Task

Analyze the failure cases and choose 1-3 strategies to apply. **Prioritize RECALL strategies (R1-R4) -- we need ~25 more TPs.** Use precision strategies (P1-P3) to offset the extra candidates through dedup, not by rejecting more.

**Key principles:**
- RECALL is our bottleneck. Be more aggressive about detecting bugs, not more conservative.
- The judge uses SEMANTIC matching only -- no file/line accuracy needed. A comment describing the right issue in different words still matches.
- Cut FPs through DEDUP (merging duplicates), not through stricter filtering that also removes real bugs.
- Add CONCRETE examples from the FN list above to the review instructions. Real examples are the strongest way to teach new patterns.
- confidence_threshold can go as low as 0.30; max_issues_per_pr can go up to 10. Don't be afraid to use the full range.

## Output Format

Return a JSON object with these fields (all required):
```json
{{
    "system_prompt": "the full updated system prompt",
    "review_instructions": "the full updated review instructions",
    "judge_prompt": "the full updated judge prompt",
    "confidence_threshold": 0.50,
    "num_passes": 4,
    "max_issues_per_pr": 6,
    "change_summary": "2-3 sentence description of what you changed and why, referencing specific strategies used"
}}
```

Return the COMPLETE text for each prompt field (not just the changes). The change_summary should be specific about which failure cases you're addressing and which strategies you applied."""


class CodeReviewMutator(Mutator[CodeReviewOrganism, CodeReviewFailureCase]):
    """Mutates code review prompts using Opus 4.6 based on failure analysis."""

    def __init__(self, model: str = "claude-opus-4-6"):
        super().__init__()
        self._model = model
        self._client = anthropic.Anthropic()

    def mutate(
        self,
        organism: CodeReviewOrganism,
        failure_cases: list[CodeReviewFailureCase],
        learning_log_entries: list[LearningLogEntry],
    ) -> list[CodeReviewOrganism]:
        # Format failure cases
        failure_texts = []
        for i, fc in enumerate(failure_cases, 1):
            failure_texts.append(f"=== Failure {i} ===\n{fc.format_for_mutator()}")
        failure_cases_text = "\n\n".join(failure_texts) if failure_texts else "No failure cases provided."

        # Format learning log
        if learning_log_entries:
            log_parts = []
            for i, entry in enumerate(learning_log_entries, 1):
                log_parts.append(
                    f"--- Attempt {i} ---\n"
                    f"Change: {entry.attempted_change}\n"
                    f"Result: {entry.observed_outcome}"
                )
            learning_log_text = "\n\n".join(log_parts)
        else:
            learning_log_text = "No previous attempts recorded."

        prompt = MUTATION_PROMPT_TEMPLATE.format(
            system_prompt=organism.system_prompt,
            review_instructions=organism.review_instructions,
            judge_prompt=organism.judge_prompt,
            confidence_threshold=organism.confidence_threshold,
            num_passes=organism.num_passes,
            max_issues_per_pr=organism.max_issues_per_pr,
            failure_cases_text=failure_cases_text,
            learning_log_text=learning_log_text,
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=32000,
                thinking={"type": "enabled", "budget_tokens": 16000},
                temperature=1,
                messages=[{"role": "user", "content": prompt}],
            )

            # Extract text response
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
            response_text = "\n".join(text_parts)

            # Parse JSON from response
            parsed = self._parse_response(response_text)
            if not parsed:
                print("  Mutator: failed to parse response")
                return []

            # Validate and clamp numeric params
            confidence = max(0.30, min(0.80, float(parsed.get("confidence_threshold", organism.confidence_threshold))))
            num_passes = max(2, min(10, int(parsed.get("num_passes", organism.num_passes))))
            max_issues = max(3, min(10, int(parsed.get("max_issues_per_pr", organism.max_issues_per_pr))))

            change_summary = parsed.get("change_summary", "Prompt mutation applied.")

            new_organism = CodeReviewOrganism(
                system_prompt=parsed.get("system_prompt", organism.system_prompt),
                review_instructions=parsed.get("review_instructions", organism.review_instructions),
                judge_prompt=parsed.get("judge_prompt", organism.judge_prompt),
                confidence_threshold=confidence,
                num_passes=num_passes,
                max_issues_per_pr=max_issues,
                from_change_summary=change_summary,
            )

            print(f"  Mutator: created organism with {len(change_summary)} char summary")
            return [new_organism]

        except Exception as e:
            print(f"  Mutator error: {e}")
            return []

    @property
    def supports_batch_mutation(self) -> bool:
        return True

    @staticmethod
    def _parse_response(response_text: str) -> dict | None:
        """Extract JSON from LLM response."""
        text = response_text.strip()

        # Try to find JSON in code blocks first
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                cleaned = part.strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
                if cleaned.startswith("{"):
                    try:
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        continue

        # Try to find raw JSON object
        start = text.find("{")
        if start >= 0:
            # Find the matching closing brace
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break

        return None


CROSSOVER_PROMPT_TEMPLATE = """You are combining the best traits from two code review AI configurations to create a superior offspring.

This is Differential Evolution (DE) crossover: you have two parent organisms with different prompt strategies. Your job is to combine the BEST parts of each into one new organism that outperforms both.

## Benchmark Context

The benchmark has 50 PRs across 5 repos (Python, Go, TypeScript, Ruby, Java) with 137 golden comments. The LLM judge uses SEMANTIC matching -- no exact file/line needed, just the same underlying issue. F1 = harmonic mean of precision and recall. Current target: >64% F1.

**Key insight: Our precision (47.8%) already matches competitors. Our RECALL (46.7%) is the bottleneck. Favor the parent with better recall / more aggressive bug detection. Cut FPs through dedup, not by rejecting real bugs.**

## Parent A (score: {parent_a_score})

### System Prompt
```
{parent_a_system_prompt}
```

### Review Instructions
```
{parent_a_review_instructions}
```

### Judge Prompt
```
{parent_a_judge_prompt}
```

### Parameters
- confidence_threshold: {parent_a_confidence_threshold}
- num_passes: {parent_a_num_passes}
- max_issues_per_pr: {parent_a_max_issues_per_pr}

## Parent B (score: {parent_b_score})

### System Prompt
```
{parent_b_system_prompt}
```

### Review Instructions
```
{parent_b_review_instructions}
```

### Judge Prompt
```
{parent_b_judge_prompt}
```

### Parameters
- confidence_threshold: {parent_b_confidence_threshold}
- num_passes: {parent_b_num_passes}
- max_issues_per_pr: {parent_b_max_issues_per_pr}

## Failure Cases (from the primary parent)

{failure_cases_text}

## Your Task

Create a new organism by combining the best elements from both parents:

1. **Identify what's DIFFERENT** between the two parents. Focus on the differing parts.
2. **Keep what works** from the higher-scoring parent as the base.
3. **Incorporate improvements** from the other parent, especially:
   - Bug detection patterns that one has but the other doesn't (RECALL)
   - Deduplication rules that reduce FPs without losing TPs (PRECISION through dedup)
   - Review instructions that catch specific bug types the other misses
4. **Resolve conflicts** by favoring the approach that catches MORE bugs. Err toward recall.
5. **For numeric parameters:** prefer lower confidence_threshold and higher max_issues_per_pr (we need more candidates, not fewer).

## Output Format

Return a JSON object with these fields (all required):
```json
{{
    "system_prompt": "the full combined system prompt",
    "review_instructions": "the full combined review instructions",
    "judge_prompt": "the full combined judge prompt",
    "confidence_threshold": 0.50,
    "num_passes": 4,
    "max_issues_per_pr": 6,
    "change_summary": "2-3 sentences describing what you took from each parent and why"
}}
```

Return COMPLETE text for each field."""


class CrossoverMutator(Mutator[CodeReviewOrganism, CodeReviewFailureCase]):
    """DE-style crossover mutator that combines traits from two parent organisms.

    Uses MutatorContext to access the population and sample a second parent,
    then asks the LLM to combine the best elements of both.
    """

    def __init__(self, model: str = "claude-opus-4-6"):
        super().__init__()
        self._model = model
        self._client = anthropic.Anthropic()

    def mutate(
        self,
        organism: CodeReviewOrganism,
        failure_cases: list[CodeReviewFailureCase],
        learning_log_entries: list[LearningLogEntry],
    ) -> list[CodeReviewOrganism]:
        # Need population context to sample a second parent
        if self._context is None:
            print("  CrossoverMutator: no context available, falling back to single-parent mutation")
            return []

        population = self._context.population

        # Sample a second parent that is different from the primary
        eligible = [
            (org, result)
            for org, result in population.organisms
            if result.is_viable and org.id != organism.id
        ]
        if not eligible:
            print("  CrossoverMutator: no second parent available")
            return []

        # Pick the second parent weighted toward higher scores
        scores = [max(0.01, result.score) for _, result in eligible]
        second_parent, second_result = random.choices(eligible, weights=scores, k=1)[0]

        # Get the primary parent's score from population
        primary_result = population._organisms_by_id.get(organism.id)
        parent_a_score = primary_result[1].score if primary_result else 0.0

        # Format failure cases
        failure_texts = []
        for i, fc in enumerate(failure_cases, 1):
            failure_texts.append(f"=== Failure {i} ===\n{fc.format_for_mutator()}")
        failure_cases_text = "\n\n".join(failure_texts) if failure_texts else "No failure cases provided."

        prompt = CROSSOVER_PROMPT_TEMPLATE.format(
            parent_a_score=f"{parent_a_score:.3f}",
            parent_a_system_prompt=organism.system_prompt,
            parent_a_review_instructions=organism.review_instructions,
            parent_a_judge_prompt=organism.judge_prompt,
            parent_a_confidence_threshold=organism.confidence_threshold,
            parent_a_num_passes=organism.num_passes,
            parent_a_max_issues_per_pr=organism.max_issues_per_pr,
            parent_b_score=f"{second_result.score:.3f}",
            parent_b_system_prompt=second_parent.system_prompt,
            parent_b_review_instructions=second_parent.review_instructions,
            parent_b_judge_prompt=second_parent.judge_prompt,
            parent_b_confidence_threshold=second_parent.confidence_threshold,
            parent_b_num_passes=second_parent.num_passes,
            parent_b_max_issues_per_pr=second_parent.max_issues_per_pr,
            failure_cases_text=failure_cases_text,
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=32000,
                thinking={"type": "enabled", "budget_tokens": 16000},
                temperature=1,
                messages=[{"role": "user", "content": prompt}],
            )

            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
            response_text = "\n".join(text_parts)

            parsed = CodeReviewMutator._parse_response(response_text)
            if not parsed:
                print("  CrossoverMutator: failed to parse response")
                return []

            confidence = max(0.30, min(0.80, float(parsed.get("confidence_threshold", organism.confidence_threshold))))
            num_passes = max(2, min(10, int(parsed.get("num_passes", organism.num_passes))))
            max_issues = max(3, min(10, int(parsed.get("max_issues_per_pr", organism.max_issues_per_pr))))

            change_summary = parsed.get("change_summary", "DE crossover applied.")

            new_organism = CodeReviewOrganism(
                system_prompt=parsed.get("system_prompt", organism.system_prompt),
                review_instructions=parsed.get("review_instructions", organism.review_instructions),
                judge_prompt=parsed.get("judge_prompt", organism.judge_prompt),
                confidence_threshold=confidence,
                num_passes=num_passes,
                max_issues_per_pr=max_issues,
                from_change_summary=f"[CROSSOVER] {change_summary}",
                additional_parents=[second_parent],
            )

            print(f"  CrossoverMutator: created offspring from parents with scores {parent_a_score:.3f} and {second_result.score:.3f}")
            return [new_organism]

        except Exception as e:
            print(f"  CrossoverMutator error: {e}")
            return []

    @property
    def supports_batch_mutation(self) -> bool:
        return True
