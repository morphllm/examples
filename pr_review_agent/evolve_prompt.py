#!/usr/bin/env python3
"""Darwinian evolution of PR review agent prompts using darwinian_evolver.

Evolves the SYSTEM_PROMPT to maximize F1 score on the code review benchmark.

Usage:
    # Quick test (3 iterations, 5 calibration PRs)
    python -m pr_review_agent.evolve_prompt --num_iterations 3

    # Full run (5 iterations, all 50 PRs)
    python -m pr_review_agent.evolve_prompt --num_iterations 5 --full

    # Resume from snapshot
    python -m pr_review_agent.evolve_prompt --resume_from_snapshot /tmp/pr_evolve/snapshots/iteration_2.pkl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import anthropic
import jinja2
from pydantic import computed_field

# Add darwinian_evolver to path
sys.path.insert(0, "/tmp/darwinian_evolver")

from darwinian_evolver.cli_common import parse_learning_log_view_type
from darwinian_evolver.evolve_problem_loop import EvolveProblemLoop
from darwinian_evolver.learning_log import LearningLogEntry
from darwinian_evolver.problem import (
    EvaluationFailureCase,
    EvaluationResult,
    Evaluator,
    Mutator,
    Organism,
    Problem,
)

from pr_review_agent.config import Config
from pr_review_agent.evaluate import judge_match
from pr_review_agent.main import (
    REPO_PATH_MAP,
    TOOL_NAME,
    fetch_diff_via_gh,
    load_benchmark_data,
    select_calibration_prs,
)
from pr_review_agent.pipeline.confidence_filter import ConfidenceFilter
from pr_review_agent.pipeline.diff_parser import filter_reviewable_files, parse_diff
from pr_review_agent.pipeline.reviewer import Reviewer


# ---------------------------------------------------------------------------
# 1. Organism: wraps the SYSTEM_PROMPT text
# ---------------------------------------------------------------------------

class PRReviewOrganism(Organism):
    """An organism whose evolvable payload is the SYSTEM_PROMPT string."""

    system_prompt: str

    @computed_field
    @property
    def visualizer_props(self) -> dict[str, str | float]:
        return {"prompt_length": len(self.system_prompt)}


# ---------------------------------------------------------------------------
# 2. Failure cases
# ---------------------------------------------------------------------------

class PRReviewFailureCase(EvaluationFailureCase):
    """A single PR where our review missed a golden comment (FN) or
    produced a false positive (FP)."""

    pr_url: str
    failure_detail: str  # human-readable description of what went wrong
    golden_comment: str | None = None  # the golden we missed (for FNs)
    false_positive_comment: str | None = None  # our FP comment text


class PRReviewEvaluationResult(EvaluationResult):
    """Extended result with PR review metrics."""

    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @computed_field
    @property
    def visualizer_props(self) -> dict[str, str | float]:
        return {
            "f1": round(self.f1, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
        }

    def format_observed_outcome(self, parent_result: EvaluationResult | None, ndigits: int = 4) -> str:
        outcome = f"F1={self.f1:.1%}, Precision={self.precision:.1%}, Recall={self.recall:.1%} (TP={self.tp}, FP={self.fp}, FN={self.fn})"
        if parent_result is not None and isinstance(parent_result, PRReviewEvaluationResult):
            delta = self.f1 - parent_result.f1
            direction = "improvement" if delta > 0 else "regression" if delta < 0 else "no change"
            outcome += f". {direction.capitalize()} of {abs(delta):.1%} F1 vs parent."
        return outcome

    @property
    def failure_type_weights(self) -> dict[str, float]:
        # Weight FNs higher than FPs since recall is harder to improve
        return {
            "false_negative": 2.0,
            "false_positive": 1.0,
        }


# ---------------------------------------------------------------------------
# 3. Evaluator: runs the benchmark pipeline and computes F1
# ---------------------------------------------------------------------------

class PRReviewEvaluator(Evaluator[PRReviewOrganism, PRReviewEvaluationResult, PRReviewFailureCase]):
    """Evaluates a SYSTEM_PROMPT by running the review pipeline on benchmark PRs
    and computing F1 against golden comments."""

    def __init__(self, pr_urls: list[str], benchmark_data: dict, config: Config):
        self._pr_urls = pr_urls
        self._benchmark_data = benchmark_data
        self._config = config
        self._judge_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def evaluate(self, organism: PRReviewOrganism) -> PRReviewEvaluationResult:
        """Run the full pipeline with the organism's system_prompt and score it."""
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"EVALUATING organism {str(organism.id)[:8]}...", file=sys.stderr)
        print(f"Prompt length: {len(organism.system_prompt)} chars", file=sys.stderr)

        # Monkey-patch the SYSTEM_PROMPT for this evaluation
        import pr_review_agent.prompts.system as system_module
        original_prompt = system_module.SYSTEM_PROMPT
        system_module.SYSTEM_PROMPT = organism.system_prompt

        try:
            result = self._run_and_score(organism)
        finally:
            # Restore original prompt
            system_module.SYSTEM_PROMPT = original_prompt

        print(f"Result: F1={result.f1:.1%}, P={result.precision:.1%}, R={result.recall:.1%}", file=sys.stderr)
        return result

    def _run_and_score(self, organism: PRReviewOrganism) -> PRReviewEvaluationResult:
        """Run reviews on all benchmark PRs and compute metrics."""
        config = self._config
        reviewer = Reviewer(config, context_gatherer=None)
        confidence_filter = ConfidenceFilter(config)

        total_tp = 0
        total_fp = 0
        total_fn = 0
        trainable_failures: list[PRReviewFailureCase] = []

        for pr_url in self._pr_urls:
            entry = self._benchmark_data.get(pr_url, {})
            golden_comments = entry.get("golden_comments", [])
            if not golden_comments:
                continue

            # Fetch diff
            diff = fetch_diff_via_gh(pr_url, entry)
            if not diff:
                continue

            file_diffs = filter_reviewable_files(parse_diff(diff))
            if not file_diffs:
                continue

            # Resolve repo_path
            source_repo = entry.get("source_repo", "")
            base_name = REPO_PATH_MAP.get(source_repo, source_repo.split("-")[0])
            repo_path = str(config.clone_dir / base_name)
            if not Path(repo_path).is_dir():
                repo_path = None

            # Review with the organism's prompt (already patched)
            try:
                issues = reviewer.review_pr(file_diffs, repo_path=repo_path)
            except Exception as e:
                print(f"  ERROR reviewing {pr_url}: {e}", file=sys.stderr)
                continue

            # Filter
            filtered = confidence_filter.filter(issues)
            if len(filtered) > 8:
                filtered.sort(key=lambda x: x.confidence, reverse=True)
                filtered = filtered[:8]

            candidates = [issue.comment for issue in filtered]

            # Match against golden comments
            pr_num = pr_url.split("/pull/")[-1] if "/pull/" in pr_url else "?"
            golden_matched = {}
            candidate_matched = set()

            for gi, gc in enumerate(golden_comments):
                golden_text = gc["comment"]
                best_match = None
                best_confidence = 0

                for ci, cand in enumerate(candidates):
                    result = judge_match(self._judge_client, golden_text, cand)
                    if result.get("match") and result.get("confidence", 0) > best_confidence:
                        best_match = ci
                        best_confidence = result["confidence"]

                if best_match is not None:
                    golden_matched[gi] = best_match
                    candidate_matched.add(best_match)

            tp = len(golden_matched)
            fp = len(candidates) - len(candidate_matched)
            fn = len(golden_comments) - tp

            total_tp += tp
            total_fp += fp
            total_fn += fn

            # Collect failure cases for the mutator
            # FNs: golden comments we missed
            for gi, gc in enumerate(golden_comments):
                if gi not in golden_matched:
                    trainable_failures.append(PRReviewFailureCase(
                        data_point_id=f"fn_{pr_url}_{gi}",
                        failure_type="false_negative",
                        pr_url=pr_url,
                        failure_detail=f"Missed golden comment in {source_repo} PR#{pr_num}: {gc['comment'][:200]}",
                        golden_comment=gc["comment"],
                    ))

            # FPs: our comments that didn't match any golden
            for ci, cand in enumerate(candidates):
                if ci not in candidate_matched:
                    trainable_failures.append(PRReviewFailureCase(
                        data_point_id=f"fp_{pr_url}_{ci}",
                        failure_type="false_positive",
                        pr_url=pr_url,
                        failure_detail=f"False positive in {source_repo} PR#{pr_num}: {cand[:200]}",
                        false_positive_comment=cand,
                    ))

        # Compute metrics
        total_candidates = total_tp + total_fp
        precision = total_tp / total_candidates if total_candidates > 0 else 0
        recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        return PRReviewEvaluationResult(
            score=f1,  # F1 is our fitness score
            trainable_failure_cases=trainable_failures,
            precision=precision,
            recall=recall,
            f1=f1,
            tp=total_tp,
            fp=total_fp,
            fn=total_fn,
            is_viable=True,
        )


# ---------------------------------------------------------------------------
# 4. Mutator: uses Claude to improve the SYSTEM_PROMPT
# ---------------------------------------------------------------------------

class ImproveSystemPromptMutator(Mutator[PRReviewOrganism, PRReviewFailureCase]):
    """Uses an LLM to diagnose failure cases and improve the SYSTEM_PROMPT."""

    MUTATION_PROMPT_TEMPLATE = """You are an expert at writing prompts for AI code reviewers. Your task is to improve a SYSTEM_PROMPT that instructs an AI to find bugs in pull requests.

## Current SYSTEM_PROMPT
```
{{ organism.system_prompt }}
```

## Evaluation Metrics
The current prompt achieves the following on a code review benchmark:
- Bugs are real defects identified by human reviewers ("golden comments")
- We measure Precision (what fraction of our comments are real bugs) and Recall (what fraction of golden bugs we find)
- F1 score combines both: higher is better

## Failure Cases to Address
{% for fc in failure_cases %}
### Failure {{ loop.index }}: {{ fc.failure_type | upper }}
{{ fc.failure_detail }}
{% if fc.golden_comment %}
**Golden comment we missed:** {{ fc.golden_comment[:500] }}
{% endif %}
{% if fc.false_positive_comment %}
**Our false positive comment:** {{ fc.false_positive_comment[:500] }}
{% endif %}
---
{% endfor %}

{% if learning_log_entries %}
## Previous Attempted Changes (learn from these)
{% for entry in learning_log_entries %}
### Change {{ loop.index }}
**What was tried:** {{ entry.attempted_change }}
**Result:** {{ entry.observed_outcome }}
{% endfor %}
{% endif %}

## Instructions
1. Diagnose WHY each failure case happened given the current prompt
2. Propose a SINGLE targeted improvement to the SYSTEM_PROMPT that would help with these failure cases without hurting other areas
3. The improvement should be surgical: add/modify/remove specific instructions, examples, or heuristics
4. Do NOT rewrite the entire prompt from scratch. Make a focused change.
5. Keep the same overall structure (WHAT TO REPORT, WHAT NOT TO REPORT, CONFIDENCE SCALE, OUTPUT FORMAT sections)
6. Do NOT make the prompt excessively long. Every word must earn its place.

## Output Format
First, write your diagnosis and proposed change (2-3 paragraphs).
Then output the complete improved SYSTEM_PROMPT wrapped in triple backticks.
After the closing backticks, write a 1-2 sentence summary of the change you made."""

    def mutate(
        self,
        organism: PRReviewOrganism,
        failure_cases: list[PRReviewFailureCase],
        learning_log_entries: list[LearningLogEntry],
    ) -> list[PRReviewOrganism]:
        prompt = (
            jinja2.Template(self.MUTATION_PROMPT_TEMPLATE.strip())
            .render(
                organism=organism,
                failure_cases=failure_cases,
                learning_log_entries=learning_log_entries,
            )
            .strip()
        )

        client = anthropic.Anthropic()
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = response.content[0].text
        except Exception as e:
            print(f"Mutation LLM call failed: {e}", file=sys.stderr)
            return []

        try:
            diagnosis, improved_prompt, change_summary = self._parse_response(response_text)
        except ValueError as e:
            print(f"Error parsing mutation response: {e}", file=sys.stderr)
            return []

        return [
            PRReviewOrganism(
                system_prompt=improved_prompt,
                from_change_summary=change_summary,
            ),
        ]

    @property
    def supports_batch_mutation(self) -> bool:
        return True

    def _parse_response(self, response: str) -> tuple[str, str, str]:
        """Extract diagnosis, improved prompt, and change summary from LLM response."""
        parts = response.split("```")
        if len(parts) < 3:
            raise ValueError("Response does not contain a prompt in triple backticks")

        # Find the largest code block (the improved prompt)
        best_block = ""
        best_idx = -1
        for i in range(1, len(parts), 2):  # odd indices are code blocks
            block = parts[i].strip()
            # Strip optional language tag
            if block.startswith("text\n") or block.startswith("text "):
                block = block[5:].strip()
            if len(block) > len(best_block):
                best_block = block
                best_idx = i

        if not best_block or len(best_block) < 100:
            raise ValueError("No valid prompt found in code blocks")

        diagnosis = parts[0].strip() if best_idx > 0 else ""
        change_summary = parts[-1].strip() if best_idx < len(parts) - 1 else ""

        return diagnosis, best_block, change_summary


# ---------------------------------------------------------------------------
# 5. Problem definition: ties everything together
# ---------------------------------------------------------------------------

def make_pr_review_problem(
    pr_urls: list[str],
    benchmark_data: dict,
    config: Config,
    initial_prompt: str,
) -> Problem:
    """Create the Darwinian evolution Problem for PR review prompt optimization."""
    initial_organism = PRReviewOrganism(system_prompt=initial_prompt)
    evaluator = PRReviewEvaluator(pr_urls, benchmark_data, config)
    mutator = ImproveSystemPromptMutator()

    return Problem[PRReviewOrganism, PRReviewEvaluationResult, PRReviewFailureCase](
        initial_organism=initial_organism,
        evaluator=evaluator,
        mutators=[mutator],
    )


# ---------------------------------------------------------------------------
# 6. CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Evolve PR review SYSTEM_PROMPT via Darwinian evolution")

    parser.add_argument("--num_iterations", type=int, default=3, help="Number of evolution iterations (default: 3)")
    parser.add_argument("--num_parents_per_iteration", type=int, default=2, help="Parents sampled per iteration (default: 2)")
    parser.add_argument("--batch_size", type=int, default=3, help="Failure cases per mutation (default: 3)")
    parser.add_argument("--full", action="store_true", help="Use all 50 benchmark PRs instead of 5 calibration PRs")
    parser.add_argument("--limit", type=int, help="Limit number of PRs to evaluate on")
    parser.add_argument("--output_dir", type=Path, default=Path("/tmp/pr_evolve"), help="Output directory")
    parser.add_argument("--resume_from_snapshot", type=Path, help="Resume from a snapshot file")
    parser.add_argument("--learning_log", type=str, default="ancestors", help="Learning log strategy (default: ancestors)")
    parser.add_argument("--no-warpgrep", action="store_true", help="Disable WarpGrep during evaluation")

    args = parser.parse_args()

    # Setup
    config = Config()
    if args.no_warpgrep:
        config.warpgrep_tool_enabled = False
        config.warpgrep_validate_issues = False

    benchmark_data = load_benchmark_data(config)
    print(f"Loaded {len(benchmark_data)} benchmark PRs")

    # Select PRs for evaluation
    if args.full:
        pr_urls = list(benchmark_data.keys())
    else:
        pr_urls = select_calibration_prs(benchmark_data)
        print(f"Using {len(pr_urls)} calibration PRs (1 per repo)")

    if args.limit:
        pr_urls = pr_urls[:args.limit]
    print(f"Evaluating on {len(pr_urls)} PRs per organism")

    # Load current SYSTEM_PROMPT as the initial organism
    from pr_review_agent.prompts.system import SYSTEM_PROMPT
    initial_prompt = SYSTEM_PROMPT

    # Create the problem
    problem = make_pr_review_problem(pr_urls, benchmark_data, config, initial_prompt)

    # Setup output
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = output_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    json_log_file = output_dir / "results.jsonl"
    problem.evaluator.set_output_dir(str(output_dir))

    # Create evolution loop
    evolve_loop = EvolveProblemLoop(
        problem,
        learning_log_view_type=parse_learning_log_view_type(args.learning_log),
        num_parents_per_iteration=args.num_parents_per_iteration,
        mutator_concurrency=1,  # Sequential: each eval is expensive
        evaluator_concurrency=1,
        batch_size=args.batch_size,
        snapshot_to_resume_from=args.resume_from_snapshot.read_bytes() if args.resume_from_snapshot else None,
        fixed_midpoint_score=0.4,  # F1 scores are in 0.3-0.6 range
    )

    if args.resume_from_snapshot:
        print(f"Resuming from snapshot: {args.resume_from_snapshot}")
    else:
        print("Evaluating initial organism (current SYSTEM_PROMPT)...")

    t0 = time.time()

    for snapshot in evolve_loop.run(num_iterations=args.num_iterations):
        elapsed = time.time() - t0
        best_org, best_result = snapshot.best_organism_result

        print(f"\n{'='*60}")
        print(f"Iteration {snapshot.iteration} (elapsed: {elapsed:.0f}s)")
        print(f"  Population size: {snapshot.population_size}")
        print(f"  Best F1: {best_result.score:.1%}")

        if isinstance(best_result, PRReviewEvaluationResult):
            print(f"  Best P/R: {best_result.precision:.1%} / {best_result.recall:.1%}")
            print(f"  Best TP/FP/FN: {best_result.tp}/{best_result.fp}/{best_result.fn}")

        if isinstance(best_org, PRReviewOrganism):
            print(f"  Best prompt length: {len(best_org.system_prompt)} chars")

        # Save snapshot
        snapshot_file = snapshot_dir / f"iteration_{snapshot.iteration}.pkl"
        with snapshot_file.open("wb") as f:
            f.write(snapshot.snapshot)

        # Save results log
        with json_log_file.open("a") as f:
            log_dict = {
                "iteration": snapshot.iteration,
                "population": snapshot.population_json_log,
            }
            f.write(json.dumps(log_dict) + "\n")

        # Save best prompt to a readable file
        if isinstance(best_org, PRReviewOrganism):
            best_prompt_file = output_dir / f"best_prompt_iter{snapshot.iteration}.txt"
            with best_prompt_file.open("w") as f:
                f.write(best_org.system_prompt)
            print(f"  Best prompt saved to: {best_prompt_file}")

        # Print evolver stats
        stats = snapshot.evolver_stats
        print(f"  Mutations: {stats.num_generated_mutations}, Evaluations: {stats.num_evaluate_calls}")

    total_elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"EVOLUTION COMPLETE in {total_elapsed:.0f}s")
    print(f"Output: {output_dir}")

    # Print the best prompt
    best_org, best_result = evolve_loop.population.get_best()
    if isinstance(best_org, PRReviewOrganism):
        final_prompt_file = output_dir / "best_prompt_final.txt"
        with final_prompt_file.open("w") as f:
            f.write(best_org.system_prompt)
        print(f"Best prompt: {final_prompt_file}")
        print(f"Best F1: {best_result.score:.1%}")


if __name__ == "__main__":
    main()
