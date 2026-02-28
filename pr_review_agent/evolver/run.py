#!/usr/bin/env python3
"""Entry point for running the darwinian evolution loop on code review prompts.

Usage:
    # Run evolution (10-PR calibration subset for first iterations)
    python -m pr_review_agent.evolver.run --iterations 5 --num-parents 2 --batch-size 5

    # Use smaller calibration subset for faster iteration
    python -m pr_review_agent.evolver.run --iterations 10 --calibration-size 10

    # Resume from checkpoint
    python -m pr_review_agent.evolver.run --iterations 5 --resume snapshot.pkl

    # Show best organism
    python -m pr_review_agent.evolver.run --show-best

    # Evaluate best organism on full benchmark
    python -m pr_review_agent.evolver.run --evaluate-best
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

from darwinian_evolver.evolve_problem_loop import EvolveProblemLoop
from darwinian_evolver.learning_log_view import AncestorLearningLogView
from darwinian_evolver.problem import Problem

from pr_review_agent.config import Config
from pr_review_agent.evolver.evaluator import (
    CodeReviewEvaluationResult,
    CodeReviewEvaluator,
    build_train_holdout_split,
    prefetch_diffs,
)
from pr_review_agent.evolver.failure_case import CodeReviewFailureCase
from pr_review_agent.evolver.mutator import CodeReviewMutator
from pr_review_agent.evolver.organism import CodeReviewOrganism, make_initial_organism


def load_benchmark_data(config: Config) -> dict:
    data_file = config.benchmark_dir / "results" / "benchmark_data.json"
    if not data_file.exists():
        print(f"Error: {data_file} not found", file=sys.stderr)
        sys.exit(1)
    with open(data_file) as f:
        return json.load(f)


def save_organism_json(organism: CodeReviewOrganism, path: Path) -> None:
    """Save organism prompts as readable JSON."""
    data = {
        "system_prompt": organism.system_prompt,
        "review_instructions": organism.review_instructions,
        "judge_prompt": organism.judge_prompt,
        "confidence_threshold": organism.confidence_threshold,
        "num_passes": organism.num_passes,
        "max_issues_per_pr": organism.max_issues_per_pr,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved organism to {path}", file=sys.stderr)


def load_organism_json(path: Path) -> CodeReviewOrganism:
    """Load organism from JSON file."""
    with open(path) as f:
        data = json.load(f)
    return CodeReviewOrganism(**data)


def run_evolution(args: argparse.Namespace) -> None:
    config = Config()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load benchmark data
    benchmark_data = load_benchmark_data(config)
    print(f"Loaded {len(benchmark_data)} PRs from benchmark", file=sys.stderr)

    # Build train/holdout split
    all_train_urls, holdout_urls = build_train_holdout_split(benchmark_data)
    print(f"Split: {len(all_train_urls)} train, {len(holdout_urls)} holdout", file=sys.stderr)

    # Use calibration subset if specified
    if args.calibration_size and args.calibration_size < len(all_train_urls):
        # Take calibration_size PRs, stratified by repo
        import random
        random.seed(42)

        repo_prs: dict[str, list[str]] = {}
        for url in all_train_urls:
            entry = benchmark_data.get(url, {})
            repo = entry.get("source_repo", "unknown")
            base = repo.split("-")[0] if "-" in repo else repo
            repo_prs.setdefault(base, []).append(url)

        train_urls = []
        per_repo = max(1, args.calibration_size // len(repo_prs))
        for repo, urls in sorted(repo_prs.items()):
            train_urls.extend(urls[:per_repo])

        # Fill remaining from any repo
        remaining = args.calibration_size - len(train_urls)
        if remaining > 0:
            unused = [u for u in all_train_urls if u not in train_urls]
            train_urls.extend(unused[:remaining])

        train_urls = train_urls[:args.calibration_size]
        print(f"Using calibration subset: {len(train_urls)} train PRs", file=sys.stderr)
    else:
        train_urls = all_train_urls

    # Pre-fetch all diffs
    all_urls = train_urls + holdout_urls
    diff_cache = prefetch_diffs(benchmark_data, all_urls)

    # Create problem components
    initial_organism = make_initial_organism()
    evaluator = CodeReviewEvaluator(
        train_pr_urls=train_urls,
        holdout_pr_urls=holdout_urls,
        benchmark_data=benchmark_data,
        config=config,
        diff_cache=diff_cache,
    )
    mutator = CodeReviewMutator()

    problem = Problem[CodeReviewOrganism, CodeReviewEvaluationResult, CodeReviewFailureCase](
        initial_organism=initial_organism,
        evaluator=evaluator,
        mutators=[mutator],
    )

    # Load snapshot for resumption if specified
    snapshot_bytes = None
    if args.resume:
        resume_path = Path(args.resume)
        if resume_path.exists():
            with open(resume_path, "rb") as f:
                snapshot_bytes = f.read()
            print(f"Resuming from {resume_path}", file=sys.stderr)

    # Create evolution loop
    loop = EvolveProblemLoop(
        problem=problem,
        learning_log_view_type=(AncestorLearningLogView, {"max_depth": 5}),
        num_parents_per_iteration=args.num_parents,
        batch_size=args.batch_size,
        evaluator_concurrency=args.evaluator_concurrency,
        mutator_concurrency=args.num_parents,
        snapshot_to_resume_from=snapshot_bytes,
    )

    print(f"\nStarting evolution: {args.iterations} iterations, "
          f"{args.num_parents} parents/iter, batch_size={args.batch_size}",
          file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    best_score = 0.0
    best_organism = None

    for snapshot in loop.run(num_iterations=args.iterations):
        best_org, best_result = snapshot.best_organism_result

        # Print iteration summary
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Iteration {snapshot.iteration}: "
              f"population={snapshot.population_size}, "
              f"best F1={best_result.score:.1%}",
              file=sys.stderr)

        if hasattr(best_result, 'precision'):
            print(f"  Best: P={best_result.precision:.1%} R={best_result.recall:.1%} "
                  f"TP={best_result.tp} FP={best_result.fp} FN={best_result.fn}",
                  file=sys.stderr)

        # Print score distribution
        percentiles = snapshot.score_percentiles
        if percentiles:
            p_strs = [f"{int(p)}%={v:.1%}" for p, v in sorted(percentiles.items())
                      if p in (0, 25, 50, 75, 100)]
            print(f"  Score distribution: {', '.join(p_strs)}", file=sys.stderr)

        # Save snapshot
        snapshot_path = output_dir / f"snapshot_iter{snapshot.iteration}.pkl"
        with open(snapshot_path, "wb") as f:
            f.write(snapshot.snapshot)

        # Save best organism if improved
        if best_result.score > best_score:
            best_score = best_result.score
            best_organism = best_org
            save_organism_json(best_org, output_dir / "best_organism.json")
            print(f"  NEW BEST: F1={best_score:.1%}", file=sys.stderr)

        # Save latest snapshot for easy resumption
        with open(output_dir / "latest_snapshot.pkl", "wb") as f:
            f.write(snapshot.snapshot)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Evolution complete. Best F1: {best_score:.1%}", file=sys.stderr)
    if best_organism:
        save_organism_json(best_organism, output_dir / "best_organism.json")


def show_best(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    best_path = output_dir / "best_organism.json"
    if not best_path.exists():
        print(f"No best organism found at {best_path}", file=sys.stderr)
        sys.exit(1)

    with open(best_path) as f:
        data = json.load(f)

    print("Best Organism Configuration:")
    print(f"  confidence_threshold: {data['confidence_threshold']}")
    print(f"  num_passes: {data['num_passes']}")
    print(f"  max_issues_per_pr: {data['max_issues_per_pr']}")
    print(f"  system_prompt length: {len(data['system_prompt'])} chars")
    print(f"  review_instructions length: {len(data['review_instructions'])} chars")
    print(f"  judge_prompt length: {len(data['judge_prompt'])} chars")
    print(f"\nFull config saved at: {best_path}")


def evaluate_best(args: argparse.Namespace) -> None:
    """Evaluate the best organism on the full benchmark."""
    output_dir = Path(args.output_dir)
    best_path = output_dir / "best_organism.json"
    if not best_path.exists():
        print(f"No best organism found at {best_path}", file=sys.stderr)
        sys.exit(1)

    organism = load_organism_json(best_path)
    config = Config()

    benchmark_data = load_benchmark_data(config)
    all_train, holdout = build_train_holdout_split(benchmark_data)
    all_urls = all_train + holdout
    diff_cache = prefetch_diffs(benchmark_data, all_urls)

    evaluator = CodeReviewEvaluator(
        train_pr_urls=all_urls,  # Evaluate on ALL PRs
        holdout_pr_urls=[],
        benchmark_data=benchmark_data,
        config=config,
        diff_cache=diff_cache,
    )

    result = evaluator.evaluate(organism)
    print(f"\nFull Benchmark Results:")
    print(f"  F1:        {result.score:.1%}")
    print(f"  Precision: {result.precision:.1%}")
    print(f"  Recall:    {result.recall:.1%}")
    print(f"  TP={result.tp} FP={result.fp} FN={result.fn}")


def main():
    parser = argparse.ArgumentParser(description="Darwinian evolution for code review prompts")

    # Evolution parameters
    parser.add_argument("--iterations", type=int, default=10, help="Number of evolution iterations")
    parser.add_argument("--num-parents", type=int, default=2, help="Parents sampled per iteration")
    parser.add_argument("--batch-size", type=int, default=5, help="Failure cases per mutation")
    parser.add_argument("--evaluator-concurrency", type=int, default=1,
                        help="Concurrent evaluations (rate limit dependent)")
    parser.add_argument("--calibration-size", type=int, default=10,
                        help="Number of train PRs for calibration (0 = use all)")
    parser.add_argument("--output-dir", default="pr_review_agent/evolver/output",
                        help="Output directory for snapshots and best organism")
    parser.add_argument("--resume", help="Path to snapshot pickle to resume from")

    # Inspection commands
    parser.add_argument("--show-best", action="store_true", help="Show best organism config")
    parser.add_argument("--evaluate-best", action="store_true",
                        help="Evaluate best organism on full benchmark")

    args = parser.parse_args()

    if args.show_best:
        show_best(args)
    elif args.evaluate_best:
        evaluate_best(args)
    else:
        run_evolution(args)


if __name__ == "__main__":
    main()
