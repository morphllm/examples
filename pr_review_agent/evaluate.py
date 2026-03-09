#!/usr/bin/env python3
"""Evaluate our review candidates against golden comments.

Uses Claude Sonnet as judge (same quality as benchmark's LLM judge).
Reads our candidates and golden comments, runs N x M matching, reports scores.

Usage:
    python -m pr_review_agent.evaluate
    python -m pr_review_agent.evaluate --repo sentry
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import anthropic

from pr_review_agent.config import Config

JUDGE_PROMPT = """You are evaluating AI code review tools.
Determine if the candidate issue matches the golden (expected) comment.

Golden Comment (the issue we're looking for):
{golden_comment}

Candidate Issue (from the tool's review):
{candidate}

Instructions:
- Determine if the candidate identifies the SAME underlying issue as the golden comment
- Accept semantic matches — different wording, different level of detail, or different framing of the same problem all count as matches
- Focus on whether they point to the same bug, concern, or code location. If both describe the same root cause or the same code defect, it's a match.
- Match liberally: if the candidate describes the same buggy behavior even using completely different terminology, that's still a match
- The candidate does NOT need to propose the same fix — only identify the same problem

Respond with ONLY a JSON object:
{{"reasoning": "brief explanation", "match": true/false, "confidence": 0.0-1.0}}"""

TOOL_NAME = "opus_warpgrep"


def load_data(config: Config):
    """Load benchmark data and our candidates."""
    bdata_path = config.benchmark_dir / "results" / "benchmark_data.json"
    with open(bdata_path) as f:
        benchmark_data = json.load(f)
    return benchmark_data


def judge_match(client: anthropic.Anthropic, golden: str, candidate: str) -> dict:
    """Judge if a candidate matches a golden comment."""
    prompt = JUDGE_PROMPT.format(golden_comment=golden, candidate=candidate)
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system="You are a precise code review evaluator. Always respond with valid JSON.",
            messages=[
                {"role": "user", "content": prompt},
            ],
        )
        text = response.content[0].text.strip()
        # Parse JSON from response
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
            return json.loads(text[start:end+1])
    except Exception as e:
        return {"error": str(e), "match": False, "confidence": 0}
    return {"match": False, "confidence": 0}


def _evaluate_pr(client: anthropic.Anthropic, pr_url: str, entry: dict) -> dict:
    """Evaluate a single PR: match goldens against candidates. Returns metrics."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    repo = entry.get("source_repo", "?")
    golden_comments = entry.get("golden_comments", [])

    our_review = None
    for review in entry.get("reviews", []):
        if review["tool"] == TOOL_NAME:
            our_review = review
            break

    if not our_review:
        return None  # Skip unreviewed PRs

    candidates = [c["body"] for c in our_review.get("review_comments", []) if c.get("body")]
    if not candidates:
        return {"tp": 0, "fp": 0, "fn": len(golden_comments),
                "golden": len(golden_comments), "candidates": 0,
                "repo": repo, "pr_url": pr_url, "tp_details": [], "fn_details": golden_comments}

    pr_num = pr_url.split("/pull/")[-1] if "/pull/" in pr_url else "?"

    # Fire all golden×candidate judge calls in parallel
    judge_results = {}  # (gi, ci) -> result
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {}
        for gi, gc in enumerate(golden_comments):
            for ci, cand in enumerate(candidates):
                fut = pool.submit(judge_match, client, gc["comment"], cand)
                futures[fut] = (gi, ci)
        for fut in as_completed(futures):
            gi, ci = futures[fut]
            judge_results[(gi, ci)] = fut.result()

    # Greedy match: for each golden, find best matching candidate
    golden_matched = {}
    candidate_matched = set()
    for gi, gc in enumerate(golden_comments):
        best_match = None
        best_confidence = 0
        for ci in range(len(candidates)):
            result = judge_results.get((gi, ci), {})
            if result.get("match") and result.get("confidence", 0) > best_confidence:
                best_match = ci
                best_confidence = result["confidence"]
        if best_match is not None:
            golden_matched[gi] = best_match
            candidate_matched.add(best_match)

    tp = len(golden_matched)
    fp = len(candidates) - len(candidate_matched)
    fn = len(golden_comments) - tp

    tp_details = [gc for i, gc in enumerate(golden_comments) if i in golden_matched]
    fn_details = [gc for i, gc in enumerate(golden_comments) if i not in golden_matched]

    print(f"  {repo} PR#{pr_num}: {tp} TP, {fp} FP, {fn} FN ({len(golden_comments)} golden, {len(candidates)} cand)", flush=True)
    for gc in tp_details:
        print(f"    TP: [{gc['severity']}] {gc['comment'][:80]}...", flush=True)
    for gc in fn_details:
        print(f"    FN: [{gc['severity']}] {gc['comment'][:80]}...", flush=True)

    return {"tp": tp, "fp": fp, "fn": fn,
            "golden": len(golden_comments), "candidates": len(candidates),
            "repo": repo, "pr_url": pr_url, "tp_details": tp_details, "fn_details": fn_details}


def evaluate(config: Config, repo_filter: str | None = None):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    benchmark_data = load_data(config)
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    # Collect PRs to evaluate
    pr_tasks = []
    for pr_url, entry in benchmark_data.items():
        repo = entry.get("source_repo", "?")
        if repo_filter and repo_filter.lower() not in repo.lower():
            continue
        golden_comments = entry.get("golden_comments", [])
        if not golden_comments:
            continue
        pr_tasks.append((pr_url, entry))

    print(f"Evaluating {len(pr_tasks)} PRs in parallel...\n", flush=True)

    # Evaluate all PRs in parallel (each PR internally parallelizes judge calls)
    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_evaluate_pr, client, url, entry): url for url, entry in pr_tasks}
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                results.append(result)

    total_tp = sum(r["tp"] for r in results)
    total_fp = sum(r["fp"] for r in results)
    total_fn = sum(r["fn"] for r in results)
    total_golden = sum(r["golden"] for r in results)
    total_candidates = sum(r["candidates"] for r in results)
    repo_metrics = {}
    for r in results:
        base_repo = r["repo"].split("-")[0]
        if base_repo not in repo_metrics:
            repo_metrics[base_repo] = {"tp": 0, "fp": 0, "fn": 0, "golden": 0, "candidates": 0}
        repo_metrics[base_repo]["tp"] += r["tp"]
        repo_metrics[base_repo]["fp"] += r["fp"]
        repo_metrics[base_repo]["fn"] += r["fn"]
        repo_metrics[base_repo]["golden"] += r["golden"]
        repo_metrics[base_repo]["candidates"] += r["candidates"]

    # Summary
    precision = total_tp / total_candidates if total_candidates > 0 else 0
    recall = total_tp / total_golden if total_golden > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    print(f"\n{'='*60}")
    print(f"OVERALL RESULTS")
    print(f"{'='*60}")
    print(f"  True Positives:  {total_tp}/{total_golden}")
    print(f"  False Positives: {total_fp}")
    print(f"  False Negatives: {total_fn}")
    print(f"  Total Candidates: {total_candidates}")
    print(f"  Precision: {precision:.1%}")
    print(f"  Recall:    {recall:.1%}")
    print(f"  F1:        {f1:.1%}")

    print(f"\nPer-repo breakdown:")
    print(f"{'Repo':<15} {'Prec':>8} {'Recall':>8} {'F1':>8} {'TP':>5} {'FP':>5} {'FN':>5}")
    print("-" * 55)
    for repo in sorted(repo_metrics.keys()):
        m = repo_metrics[repo]
        p = m["tp"] / m["candidates"] if m["candidates"] > 0 else 0
        r = m["tp"] / m["golden"] if m["golden"] > 0 else 0
        f = 2 * p * r / (p + r) if (p + r) > 0 else 0
        print(f"{repo:<15} {p:>8.1%} {r:>8.1%} {f:>8.1%} {m['tp']:>5} {m['fp']:>5} {m['fn']:>5}")

    # Save results
    results = {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "total_golden": total_golden,
        "total_candidates": total_candidates,
        "per_repo": repo_metrics,
    }
    results_path = config.output_dir / "evaluation_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {results_path}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate PR review agent")
    parser.add_argument("--repo", help="Filter by repo")
    args = parser.parse_args()

    config = Config()
    evaluate(config, repo_filter=args.repo)


if __name__ == "__main__":
    main()
