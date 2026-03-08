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


def select_mini_prs(benchmark_data: dict) -> list[str]:
    """Pick 3 PRs per base repo for mini evaluation (15 total)."""
    repo_prs: dict[str, list[str]] = {}
    for url, entry in benchmark_data.items():
        repo = entry.get("source_repo", "unknown")
        base = repo.split("-")[0] if "-" in repo else repo
        repo_prs.setdefault(base, []).append(url)
    selected = []
    for urls in repo_prs.values():
        selected.extend(urls[:3])
    return selected


def run_benchmark(config: Config, args: argparse.Namespace) -> None:
    print("=" * 60)
    print("PR Review Agent - Benchmark Pipeline")
    print("=" * 60)

    benchmark_data = load_benchmark_data(config)
    print(f"Loaded {len(benchmark_data)} PRs")

    # Components
    reviewer = Reviewer(config)

    # Apply organism overrides if specified
    max_issues_per_pr = 6  # default cap
    if args.organism:
        from pr_review_agent.evolver.run import load_organism_json
        organism = load_organism_json(Path(args.organism))
        reviewer.configure_from_organism(organism)
        max_issues_per_pr = organism.max_issues_per_pr
        print(f"Organism: {args.organism}")
        print(f"  confidence_threshold={organism.confidence_threshold}, "
              f"num_passes={organism.num_passes}, "
              f"max_issues={organism.max_issues_per_pr}")

    print(f"WarpGrep: {'ENABLED' if config.warpgrep_tool_enabled else 'DISABLED'}")

    # Select PRs
    if args.pr_url:
        pr_urls = [u for u in benchmark_data if args.pr_url in u]
    elif args.mini:
        pr_urls = select_mini_prs(benchmark_data)
        print(f"Mini eval: {len(pr_urls)} PRs (3 per repo)")
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

    from concurrent.futures import ThreadPoolExecutor, as_completed

    parallel = len(pr_urls)  # all PRs in parallel

    def _review_one(idx_and_url):
        i, pr_url = idx_and_url
        entry = benchmark_data[pr_url]
        repo = entry.get("source_repo", "?")
        pr_num = pr_url.split("/pull/")[-1] if "/pull/" in pr_url else "?"
        golden_n = len(entry.get("golden_comments", []))
        tag = f"[{i}/{len(pr_urls)}] {repo} PR#{pr_num}"
        print(f"{tag} ({golden_n} golden)", flush=True)

        # Each thread gets its own Reviewer (separate Anthropic client)
        thread_reviewer = Reviewer(config)
        if args.organism:
            from pr_review_agent.evolver.run import load_organism_json
            organism = load_organism_json(Path(args.organism))
            thread_reviewer.configure_from_organism(organism)

        # Fetch diff
        diff = fetch_diff_via_gh(pr_url, entry)
        if not diff:
            print(f"  {tag} SKIP: no diff", flush=True)
            return ("error", pr_url, None, 0, 0)

        # Parse and filter
        file_diffs = filter_reviewable_files(parse_diff(diff))
        if not file_diffs:
            print(f"  {tag} SKIP: no reviewable files", flush=True)
            return ("skip", pr_url, None, 0, 0)

        added = sum(f.total_added for f in file_diffs)
        print(f"  {tag} {len(file_diffs)} files, {added} added lines", flush=True)

        # Resolve repo_path
        source_repo = entry.get("source_repo", "")
        base_name = REPO_PATH_MAP.get(source_repo, source_repo.split("-")[0])
        repo_path = str(config.clone_dir / base_name)
        if not Path(repo_path).is_dir():
            repo_path = None

        # Review
        try:
            issues = thread_reviewer.review_pr(file_diffs, repo_path=repo_path)
        except Exception as e:
            print(f"  {tag} ERROR: {e}", flush=True)
            return ("error", pr_url, None, 0, 0)

        raw_count = len(issues)

        # Store trace
        trace = getattr(thread_reviewer, '_last_trace', [])
        if trace:
            trace_dir = config.output_dir / "traces"
            trace_dir.mkdir(parents=True, exist_ok=True)
            repo_name = repo.replace("/", "_").replace("-", "_")
            trace_file = trace_dir / f"{repo_name}_pr{pr_num}_trace.json"
            with open(trace_file, "w") as tf:
                json.dump(trace, tf, indent=2)

        # Cap by confidence
        if len(issues) > max_issues_per_pr:
            issues.sort(key=lambda x: x.confidence, reverse=True)
            issues = issues[:max_issues_per_pr]

        print(f"  {tag} {raw_count} raw -> {len(issues)} kept", flush=True)
        write_review_details(pr_url, issues, config.output_dir / "details")
        candidates = format_candidates(pr_url, issues, tool_name=TOOL_NAME)

        for issue in issues:
            print(f"    {tag} [{issue.confidence:.2f}] {issue.category}: {issue.comment[:90]}", flush=True)

        return ("ok", pr_url, candidates, raw_count, len(issues))

    print(f"Running {len(pr_urls)} PRs with parallelism={parallel}\n", flush=True)

    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {
            executor.submit(_review_one, (i, url)): url
            for i, url in enumerate(pr_urls, 1)
        }
        for future in as_completed(futures):
            status, pr_url, candidates, raw, filt = future.result()
            if status == "error":
                errors += 1
            elif status == "ok":
                total_before += raw
                total_after += filt
                all_candidates[pr_url] = candidates

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
    parser.add_argument("--mini", action="store_true", help="15 PRs, 3 per repo (fast eval)")
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
