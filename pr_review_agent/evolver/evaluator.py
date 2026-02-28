"""Evaluator: runs the review pipeline + judge, returns F1 score."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import anthropic

from darwinian_evolver.problem import EvaluationResult, Evaluator

from pr_review_agent.config import Config
from pr_review_agent.evolver.failure_case import CodeReviewFailureCase
from pr_review_agent.evolver.organism import CodeReviewOrganism
from pr_review_agent.pipeline.confidence_filter import ConfidenceFilter
from pr_review_agent.pipeline.diff_parser import filter_reviewable_files, parse_diff
from pr_review_agent.pipeline.reviewer import Reviewer

# Map benchmark source_repo values to local clone directory names
REPO_PATH_MAP = {
    "keycloak": "keycloak",
    "keycloak-greptile": "keycloak",
    "sentry": "sentry",
    "sentry-greptile": "sentry",
    "grafana": "grafana",
    "discourse-graphite": "discourse",
    "cal.com": "cal.com",
}

TOOL_NAME = "opus_warpgrep"

JUDGE_PROMPT = """You are evaluating AI code review tools.
Determine if the candidate issue matches the golden (expected) comment.

Golden Comment (the issue we're looking for):
{golden_comment}

Candidate Issue (from the tool's review):
{candidate}

Instructions:
- Determine if the candidate identifies the SAME underlying issue as the golden comment
- Accept semantic matches - different wording is fine if it's the same problem
- Focus on whether they point to the same bug, concern, or code issue

Respond with ONLY a JSON object:
{{"reasoning": "brief explanation", "match": true/false, "confidence": 0.0-1.0}}"""


class CodeReviewEvaluationResult(EvaluationResult):
    """Extended result with precision/recall/F1 breakdown."""
    precision: float = 0.0
    recall: float = 0.0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    total_candidates: int = 0
    total_golden: int = 0

    def format_observed_outcome(self, parent_result: EvaluationResult | None, ndigits: int = 3) -> str:
        outcome = (
            f"F1={self.score:.1%} (P={self.precision:.1%}, R={self.recall:.1%}). "
            f"TP={self.tp}, FP={self.fp}, FN={self.fn}. "
            f"Candidates={self.total_candidates}, Golden={self.total_golden}."
        )
        if parent_result is not None:
            delta = self.score - parent_result.score
            direction = "improvement" if delta > 0 else "regression" if delta < 0 else "no change"
            outcome += f" {direction} ({delta:+.1%} from parent F1={parent_result.score:.1%})"
        return outcome


class CodeReviewEvaluator(
    Evaluator[CodeReviewOrganism, CodeReviewEvaluationResult, CodeReviewFailureCase]
):
    """Evaluates a CodeReviewOrganism by running the review pipeline on benchmark PRs."""

    def __init__(
        self,
        train_pr_urls: list[str],
        holdout_pr_urls: list[str],
        benchmark_data: dict,
        config: Config,
        diff_cache: dict[str, str] | None = None,
    ):
        self._train_pr_urls = train_pr_urls
        self._holdout_pr_urls = holdout_pr_urls
        self._benchmark_data = benchmark_data
        self._config = config
        self._diff_cache = diff_cache or {}
        self._judge_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def evaluate(self, organism: CodeReviewOrganism) -> CodeReviewEvaluationResult:
        """Run full pipeline evaluation on train + holdout splits."""
        t0 = time.time()
        print(f"\n{'='*50}", file=sys.stderr)
        print(f"Evaluating organism {str(organism.id)[:8]}...", file=sys.stderr)
        print(f"  confidence_threshold={organism.confidence_threshold}, "
              f"num_passes={organism.num_passes}, "
              f"max_issues={organism.max_issues_per_pr}", file=sys.stderr)

        # Run on train split
        train_results = self._run_on_prs(organism, self._train_pr_urls, "train")
        train_tp, train_fp, train_fn = train_results["tp"], train_results["fp"], train_results["fn"]
        train_failures = train_results["failure_cases"]

        # Run on holdout split
        holdout_results = self._run_on_prs(organism, self._holdout_pr_urls, "holdout")
        holdout_failures = holdout_results["failure_cases"]

        # Compute overall F1 on train set (holdout tracked separately)
        total_tp = train_tp
        total_fp = train_fp
        total_fn = train_fn

        precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
        recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        elapsed = time.time() - t0
        print(f"  Train: F1={f1:.1%} P={precision:.1%} R={recall:.1%} "
              f"(TP={total_tp} FP={total_fp} FN={total_fn})", file=sys.stderr)
        print(f"  Holdout: TP={holdout_results['tp']} FP={holdout_results['fp']} "
              f"FN={holdout_results['fn']}", file=sys.stderr)
        print(f"  Time: {elapsed:.0f}s", file=sys.stderr)

        return CodeReviewEvaluationResult(
            score=f1,
            trainable_failure_cases=train_failures,
            holdout_failure_cases=holdout_failures,
            precision=precision,
            recall=recall,
            tp=total_tp,
            fp=total_fp,
            fn=total_fn,
            total_candidates=total_tp + total_fp,
            total_golden=total_tp + total_fn,
        )

    def _run_on_prs(
        self,
        organism: CodeReviewOrganism,
        pr_urls: list[str],
        split_name: str,
    ) -> dict:
        """Run review pipeline on a set of PRs and compute metrics."""
        # Create reviewer configured with organism's prompts
        reviewer = Reviewer(self._config)
        reviewer.configure_from_organism(organism)

        # Create confidence filter with organism's threshold
        confidence_filter = ConfidenceFilter(
            self._config,
            base_threshold_override=organism.confidence_threshold,
        )

        total_tp = 0
        total_fp = 0
        total_fn = 0
        failure_cases: list[CodeReviewFailureCase] = []

        for i, pr_url in enumerate(pr_urls, 1):
            entry = self._benchmark_data.get(pr_url, {})
            repo = entry.get("source_repo", "?")
            pr_num = pr_url.split("/pull/")[-1] if "/pull/" in pr_url else "?"
            golden_comments = entry.get("golden_comments", [])

            print(f"  [{split_name} {i}/{len(pr_urls)}] {repo} #{pr_num} "
                  f"({len(golden_comments)} golden)", file=sys.stderr)

            # Get diff (from cache or fetch)
            diff = self._get_diff(pr_url, entry)
            if not diff:
                print(f"    SKIP: no diff", file=sys.stderr)
                total_fn += len(golden_comments)
                continue

            # Parse and filter files
            file_diffs = filter_reviewable_files(parse_diff(diff))
            if not file_diffs:
                total_fn += len(golden_comments)
                continue

            # Resolve repo path
            source_repo = entry.get("source_repo", "")
            base_name = REPO_PATH_MAP.get(source_repo, source_repo.split("-")[0])
            repo_path = str(self._config.clone_dir / base_name)
            if not Path(repo_path).is_dir():
                repo_path = None

            # Review
            try:
                issues = reviewer.review_pr(
                    file_diffs,
                    repo_path=repo_path,
                    num_passes=organism.num_passes,
                )
            except Exception as e:
                print(f"    Review error: {e}", file=sys.stderr)
                total_fn += len(golden_comments)
                continue

            # Judge pass
            try:
                issues = reviewer.judge_issues(issues, file_diffs, repo_path=repo_path)
            except Exception as e:
                print(f"    Judge error: {e}", file=sys.stderr)

            # Confidence filter
            filtered = confidence_filter.filter(issues)

            # Cap issues
            if len(filtered) > organism.max_issues_per_pr:
                filtered.sort(key=lambda x: x.confidence, reverse=True)
                filtered = filtered[:organism.max_issues_per_pr]

            # Evaluate against golden comments
            candidates = [issue.comment for issue in filtered]
            candidate_details = [(issue.comment, issue.category, issue.file_path) for issue in filtered]

            if not golden_comments:
                total_fp += len(candidates)
                continue

            # Match golden vs candidates
            golden_matched: dict[int, int] = {}
            candidate_matched: set[int] = set()

            for gi, gc in enumerate(golden_comments):
                golden_text = gc["comment"]
                best_match = None
                best_confidence = 0.0

                for ci, cand in enumerate(candidates):
                    result = self._judge_match(golden_text, cand)
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

            # Collect failure cases
            # FPs: candidates that didn't match any golden
            for ci in range(len(candidates)):
                if ci not in candidate_matched:
                    comment, category, fpath = candidate_details[ci]
                    failure_cases.append(CodeReviewFailureCase(
                        data_point_id=pr_url,
                        failure_type="false_positive",
                        candidate_comment=comment,
                        candidate_category=category,
                        pr_repo=repo,
                        pr_num=pr_num,
                        file_path=fpath,
                    ))

            # FNs: golden comments that weren't matched
            for gi in range(len(golden_comments)):
                if gi not in golden_matched:
                    gc = golden_comments[gi]
                    failure_cases.append(CodeReviewFailureCase(
                        data_point_id=pr_url,
                        failure_type="false_negative",
                        golden_comment=gc["comment"],
                        golden_severity=gc.get("severity", "unknown"),
                        pr_repo=repo,
                        pr_num=pr_num,
                    ))

            print(f"    TP={tp} FP={fp} FN={fn}", file=sys.stderr)

        return {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "failure_cases": failure_cases,
        }

    def _get_diff(self, pr_url: str, entry: dict) -> str | None:
        """Get diff from cache or fetch via gh CLI."""
        if pr_url in self._diff_cache:
            return self._diff_cache[pr_url]

        diff = self._fetch_diff_via_gh(entry)
        if diff:
            self._diff_cache[pr_url] = diff
        return diff

    @staticmethod
    def _fetch_diff_via_gh(entry: dict) -> str | None:
        """Fetch PR diff using gh CLI from any benchmark fork."""
        for review in entry.get("reviews", []):
            fork_url = review.get("pr_url", "")
            if not fork_url:
                continue
            parts = fork_url.replace("https://github.com/", "").split("/")
            if len(parts) >= 4 and parts[2] == "pull":
                repo = f"{parts[0]}/{parts[1]}"
                pr_num = parts[3]
                try:
                    result = subprocess.run(
                        ["gh", "pr", "diff", pr_num, "--repo", repo],
                        capture_output=True, text=True, timeout=60,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        return result.stdout
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                    continue
        return None

    def _judge_match(self, golden: str, candidate: str) -> dict:
        """Judge if a candidate matches a golden comment using Sonnet."""
        prompt = JUDGE_PROMPT.format(golden_comment=golden, candidate=candidate)
        try:
            response = self._judge_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                system="You are a precise code review evaluator. Always respond with valid JSON.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            if "```" in text:
                parts = text.split("```")
                for part in parts:
                    cleaned = part.strip()
                    if cleaned.startswith("json"):
                        cleaned = cleaned[4:].strip()
                    if cleaned.startswith("{"):
                        text = cleaned
                        break
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end >= 0:
                return json.loads(text[start:end + 1])
        except Exception:
            return {"match": False, "confidence": 0}
        return {"match": False, "confidence": 0}


def build_train_holdout_split(
    benchmark_data: dict,
    holdout_per_repo: int = 2,
) -> tuple[list[str], list[str]]:
    """Split PRs into train and holdout sets, stratified by repo.

    Returns (train_urls, holdout_urls).
    """
    import random

    # Group PRs by base repo
    repo_prs: dict[str, list[str]] = {}
    for url, entry in benchmark_data.items():
        repo = entry.get("source_repo", "unknown")
        base = repo.split("-")[0] if "-" in repo else repo
        repo_prs.setdefault(base, []).append(url)

    train_urls = []
    holdout_urls = []

    for repo, urls in sorted(repo_prs.items()):
        random.seed(42)  # Deterministic split
        shuffled = list(urls)
        random.shuffle(shuffled)
        holdout = shuffled[:holdout_per_repo]
        train = shuffled[holdout_per_repo:]
        holdout_urls.extend(holdout)
        train_urls.extend(train)

    return train_urls, holdout_urls


def prefetch_diffs(
    benchmark_data: dict,
    pr_urls: list[str],
) -> dict[str, str]:
    """Pre-fetch and cache all PR diffs to avoid hitting GitHub during evolution."""
    cache: dict[str, str] = {}
    print(f"Pre-fetching diffs for {len(pr_urls)} PRs...", file=sys.stderr)

    for i, url in enumerate(pr_urls, 1):
        entry = benchmark_data.get(url, {})
        diff = CodeReviewEvaluator._fetch_diff_via_gh(entry)
        if diff:
            cache[url] = diff
            print(f"  [{i}/{len(pr_urls)}] Cached {url.split('/pull/')[-1]}", file=sys.stderr)
        else:
            print(f"  [{i}/{len(pr_urls)}] FAILED {url}", file=sys.stderr)

    print(f"Cached {len(cache)}/{len(pr_urls)} diffs", file=sys.stderr)
    return cache
