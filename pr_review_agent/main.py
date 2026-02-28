#!/usr/bin/env python3
"""Entry point: run the full PR review benchmark pipeline.

Usage:
    python -m pr_review_agent.main --no-warpgrep --limit 5
    python -m pr_review_agent.main --calibrate
    python -m pr_review_agent.main --repo sentry
    python -m pr_review_agent.main  # full run on all 50 PRs
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from pr_review_agent.config import Config
from pr_review_agent.pipeline.confidence_filter import ConfidenceFilter
from pr_review_agent.pipeline.diff_parser import filter_reviewable_files, parse_diff
from pr_review_agent.pipeline.output_formatter import (
    format_candidates,
    write_candidates_json,
    write_review_details,
)
from pr_review_agent.pipeline.reviewer import Reviewer

TOOL_NAME = "opus_warpgrep"

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


def load_benchmark_data(config: Config) -> dict:
    data_file = config.benchmark_dir / "results" / "benchmark_data.json"
    if not data_file.exists():
        print(f"Error: {data_file} not found")
        sys.exit(1)
    with open(data_file) as f:
        return json.load(f)


def fetch_diff_via_gh(pr_url: str, entry: dict) -> str | None:
    """Fetch PR diff using gh CLI from any benchmark fork."""
    for review in entry.get("reviews", []):
        fork_url = review.get("pr_url", "")
        if not fork_url:
            continue
        # Parse: https://github.com/code-review-benchmark/REPO/pull/N
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


def select_calibration_prs(benchmark_data: dict) -> list[str]:
    """Pick 1 PR per base repo for calibration (5 total)."""
    repo_prs: dict[str, list[str]] = {}
    for url, entry in benchmark_data.items():
        repo = entry.get("source_repo", "unknown")
        base = repo.split("-")[0] if "-" in repo else repo
        repo_prs.setdefault(base, []).append(url)
    return [urls[0] for urls in repo_prs.values() if urls]


def run_benchmark(config: Config, args: argparse.Namespace) -> None:
    print("=" * 60)
    print("PR Review Agent - Benchmark Pipeline")
    print("=" * 60)

    benchmark_data = load_benchmark_data(config)
    print(f"Loaded {len(benchmark_data)} PRs")

    # Components
    reviewer = Reviewer(config)
    confidence_filter = ConfidenceFilter(config)

    # Apply organism overrides if specified
    if args.organism:
        from pr_review_agent.evolver.run import load_organism_json
        organism = load_organism_json(Path(args.organism))
        reviewer.configure_from_organism(organism)
        confidence_filter = ConfidenceFilter(config, base_threshold_override=organism.confidence_threshold)
        print(f"Organism: {args.organism}")
        print(f"  confidence_threshold={organism.confidence_threshold}, "
              f"num_passes={organism.num_passes}, "
              f"max_issues={organism.max_issues_per_pr}")

    print(f"WarpGrep: {'ENABLED' if config.warpgrep_tool_enabled else 'DISABLED'}")

    # Select PRs
    if args.pr_url:
        pr_urls = [u for u in benchmark_data if args.pr_url in u]
    elif args.calibrate:
        pr_urls = select_calibration_prs(benchmark_data)
        print(f"Calibration: {len(pr_urls)} PRs (1 per repo)")
    elif args.repo:
        pr_urls = [
            u for u, e in benchmark_data.items()
            if args.repo.lower() in e.get("source_repo", "").lower()
        ]
    else:
        pr_urls = list(benchmark_data.keys())

    if args.limit:
        pr_urls = pr_urls[:args.limit]
    print(f"Reviewing {len(pr_urls)} PRs\n")

    all_candidates = {}
    total_before = total_after = errors = 0
    t0 = time.time()

    for i, pr_url in enumerate(pr_urls, 1):
        entry = benchmark_data[pr_url]
        repo = entry.get("source_repo", "?")
        pr_num = pr_url.split("/pull/")[-1] if "/pull/" in pr_url else "?"
        golden_n = len(entry.get("golden_comments", []))
        print(f"[{i}/{len(pr_urls)}] {repo} PR#{pr_num} ({golden_n} golden)")

        # Fetch diff
        diff = fetch_diff_via_gh(pr_url, entry)
        if not diff:
            print("  SKIP: no diff")
            errors += 1
            continue

        # Parse and filter
        file_diffs = filter_reviewable_files(parse_diff(diff))
        if not file_diffs:
            print("  SKIP: no reviewable files")
            continue

        added = sum(f.total_added for f in file_diffs)
        print(f"  {len(file_diffs)} files, {added} added lines")

        # Resolve repo_path from source_repo
        source_repo = entry.get("source_repo", "")
        base_name = REPO_PATH_MAP.get(source_repo, source_repo.split("-")[0])
        repo_path = str(config.clone_dir / base_name)
        if not Path(repo_path).is_dir():
            repo_path = None  # fallback if clone not available

        # Review (shuffled multi-pass + majority voting)
        try:
            issues = reviewer.review_pr(file_diffs, repo_path=repo_path)
        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1
            continue

        total_before += len(issues)

        # Judge pass: validate issues against the diff to remove FPs
        try:
            issues = reviewer.judge_issues(issues, file_diffs)
        except Exception as e:
            print(f"  Judge error (skipping): {e}")

        # Confidence filter
        filtered = confidence_filter.filter(issues)

        # Cap at 6 comments per PR (most PRs have 2-5 golden, too many = FPs)
        if len(filtered) > 6:
            filtered.sort(key=lambda x: x.confidence, reverse=True)
            filtered = filtered[:6]

        total_after += len(filtered)
        print(f"  {len(issues)} raw -> {len(filtered)} filtered")

        # Save details
        write_review_details(pr_url, issues, config.output_dir / "details")

        # Format candidates
        all_candidates[pr_url] = format_candidates(pr_url, filtered, tool_name=TOOL_NAME)

        for issue in filtered:
            print(f"    [{issue.confidence:.2f}] {issue.category}: {issue.comment[:90]}")

    elapsed = time.time() - t0

    # Write candidates.json to our output dir
    candidates_path = config.output_dir / "candidates.json"
    write_candidates_json(all_candidates, candidates_path)

    # Also write to benchmark results dir for step3 evaluation
    model_dir = config.benchmark_dir / "results" / TOOL_NAME
    model_dir.mkdir(parents=True, exist_ok=True)
    step3_candidates = {}
    for pr_url, tool_cands in all_candidates.items():
        step3_candidates[pr_url] = {TOOL_NAME: tool_cands.get(TOOL_NAME, [])}
    with open(model_dir / "candidates.json", "w") as f:
        json.dump(step3_candidates, f, indent=2)

    # Inject into benchmark_data.json so step2/step3 can find us
    for pr_url, tool_cands in all_candidates.items():
        if pr_url not in benchmark_data:
            continue
        cands = tool_cands.get(TOOL_NAME, [])
        comments = [
            {"path": c.get("path"), "line": c.get("line"), "body": c["text"],
             "created_at": "2026-02-27T00:00:00Z"}
            for c in cands
        ]
        benchmark_data[pr_url]["reviews"] = [
            r for r in benchmark_data[pr_url].get("reviews", [])
            if r["tool"] != TOOL_NAME
        ]
        benchmark_data[pr_url]["reviews"].append({
            "tool": TOOL_NAME,
            "repo_name": f"{TOOL_NAME}__{pr_url.split('/pull/')[-1]}",
            "pr_url": "",
            "review_comments": comments,
        })

    bdata_path = config.benchmark_dir / "results" / "benchmark_data.json"
    with open(bdata_path, "w") as f:
        json.dump(benchmark_data, f, indent=2)

    print(f"\n{'='*60}")
    print(f"DONE: {len(pr_urls)-errors} reviewed, {total_before} raw -> {total_after} filtered")
    print(f"Avg/PR: {total_after/max(1,len(pr_urls)-errors):.1f}, Time: {elapsed:.0f}s")
    print(f"Candidates: {candidates_path}")
    print(f"Benchmark data updated: {bdata_path}")

    with open(config.output_dir / "run_summary.json", "w") as f:
        json.dump({
            "prs_reviewed": len(pr_urls) - errors,
            "raw_issues": total_before,
            "filtered_issues": total_after,
            "elapsed_seconds": round(elapsed, 1),
            "errors": errors,
            "avg_per_pr": round(total_after / max(1, len(pr_urls) - errors), 1),
        }, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="PR Review Agent")
    parser.add_argument("--pr-url", help="Review specific PR URL")
    parser.add_argument("--repo", help="Filter by repo (sentry, grafana, etc.)")
    parser.add_argument("--limit", type=int, help="Max PRs to review")
    parser.add_argument("--calibrate", action="store_true", help="5 PRs, 1 per repo")
    parser.add_argument("--threshold", type=float, help="Override confidence threshold")
    parser.add_argument("--no-warpgrep", action="store_true", help="Skip WarpGrep")
    parser.add_argument("--organism", help="Path to organism JSON (evolved prompts)")
    parser.add_argument("--evolve", action="store_true", help="Run darwinian evolution")
    args = parser.parse_args()

    if args.evolve:
        from pr_review_agent.evolver.run import main as evolve_main
        sys.argv = [sys.argv[0]]  # Reset argv for evolver's argparse
        evolve_main()
        return

    config = Config()
    if args.threshold:
        config.base_confidence_threshold = args.threshold
    if args.no_warpgrep:
        config.warpgrep_tool_enabled = False
        config.warpgrep_validate_issues = False

    run_benchmark(config, args)


if __name__ == "__main__":
    main()
