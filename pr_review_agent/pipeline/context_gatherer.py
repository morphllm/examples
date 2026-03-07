"""Gather codebase context using WarpGrep for enhanced reviews."""

from __future__ import annotations

import sys

from pr_review_agent.config import Config
from pr_review_agent.pipeline.diff_parser import FileDiff
from pr_review_agent.warpgrep.client import WarpGrepClient
from pr_review_agent.warpgrep.query_planner import plan_queries


class ContextGatherer:
    """Gathers codebase context for each changed file using WarpGrep."""

    def __init__(self, config: Config):
        self.config = config
        self.client = WarpGrepClient(
            api_key=config.morph_api_key,
            base_url=config.warpgrep_base_url,
            model=config.warpgrep_model,
        )

    def gather_for_file(self, file_diff: FileDiff, repo_path: str) -> str:
        """Gather context for a single changed file.

        Args:
            file_diff: Parsed diff for one file.
            repo_path: Path to the cloned repository.

        Returns:
            Formatted context string for the reviewer.
        """
        queries = plan_queries(
            file_diff.file_path,
            file_diff.raw_diff,
            max_queries=self.config.warpgrep_queries_per_file,
        )

        context_parts = []
        for query in queries:
            try:
                result = self.client.search(
                    query, repo_path,
                    max_turns=self.config.warpgrep_max_turns,
                )
                if result and len(result.strip()) > 20:
                    context_parts.append(f"#### {query}\n{result[:3000]}")
            except Exception as e:
                print(f"  WarpGrep query failed: {e}", file=sys.stderr)

        if not context_parts:
            return ""

        return "\n\n".join(context_parts)

    def gather_codebase_patterns(self, file_diffs: list[FileDiff], repo_path: str) -> str:
        """Gather general codebase patterns for calibration.

        Searches for error handling patterns, testing conventions, and
        common idioms used in the codebase.

        Args:
            file_diffs: All changed files in the PR.
            repo_path: Path to the cloned repository.

        Returns:
            Formatted codebase patterns string.
        """
        pattern_queries = [
            "error handling patterns and conventions in this codebase",
            "testing patterns and test helper utilities",
        ]

        languages = {f.language for f in file_diffs}
        for lang in languages:
            if lang == "python":
                pattern_queries.append("exception handling and logging patterns")
            elif lang == "go":
                pattern_queries.append("error wrapping and sentinel error patterns")
            elif lang == "java":
                pattern_queries.append("null checking patterns and Optional usage")
            elif lang == "ruby":
                pattern_queries.append("nil handling and ActiveRecord patterns")
            elif lang == "typescript":
                pattern_queries.append("null/undefined handling and type guard patterns")

        results = []
        for query in pattern_queries[:3]:
            try:
                result = self.client.search(
                    query, repo_path,
                    max_turns=self.config.warpgrep_max_turns,
                )
                if result and len(result.strip()) > 20:
                    results.append(f"### Pattern: {query}\n{result[:2000]}")
            except Exception as e:
                print(f"  WarpGrep pattern query failed: {e}", file=sys.stderr)

        return "\n\n".join(results) if results else "No codebase patterns gathered."
