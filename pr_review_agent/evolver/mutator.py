"""LLM-powered mutator for evolving code review prompts."""

from __future__ import annotations

import json
import re

import anthropic
from pydantic import ConfigDict

from darwinian_evolver.learning_log import LearningLogEntry
from darwinian_evolver.problem import Mutator

from pr_review_agent.evolver.failure_case import CodeReviewFailureCase
from pr_review_agent.evolver.organism import CodeReviewOrganism


MUTATION_PROMPT_TEMPLATE = """You are improving a code review AI's prompts to increase its F1 score (balance of precision and recall).

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

## Your Task

Analyze the failure cases and modify the prompts to fix them. Consider:

For FALSE POSITIVES (precision problem):
- Add more specific exclusion rules to the judge prompt
- Make the review instructions more precise about what qualifies as a real bug
- Raise confidence_threshold to be more selective
- Add examples of common FP patterns to the system prompt

For FALSE NEGATIVES (recall problem):
- Add the missed bug pattern to the review instructions
- Add concrete examples to the system prompt's "WHAT TO REPORT" section
- Lower confidence_threshold to catch more marginal issues
- Increase num_passes for more coverage

General guidelines:
- Make targeted changes, not wholesale rewrites
- Each change should address specific failure cases
- The system prompt examples are crucial - add real examples from the failures
- The judge prompt's KEEP/REMOVE criteria strongly affect precision

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
    "change_summary": "2-3 sentence description of what you changed and why"
}}
```

Return the COMPLETE text for each prompt field (not just the changes). The change_summary should be specific about which failure cases you're addressing."""


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
            num_passes = max(2, min(6, int(parsed.get("num_passes", organism.num_passes))))
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

    def _parse_response(self, response_text: str) -> dict | None:
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
