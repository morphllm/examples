"""Failure cases for the code review evolver."""

from __future__ import annotations

from darwinian_evolver.problem import EvaluationFailureCase


class CodeReviewFailureCase(EvaluationFailureCase):
    """A single FP or FN from code review evaluation.

    Gives the mutator concrete examples of what the current prompts get wrong.
    """

    # "false_positive" or "false_negative"
    failure_type: str = "default"

    # For FPs: the candidate comment that was wrong
    candidate_comment: str | None = None
    candidate_category: str | None = None

    # For FNs: the golden comment that was missed
    golden_comment: str | None = None
    golden_severity: str | None = None

    # Context
    pr_repo: str = ""
    pr_num: str = ""
    file_path: str | None = None

    def format_for_mutator(self) -> str:
        """Format this failure case for the mutation prompt."""
        parts = [f"PR: {self.pr_repo} #{self.pr_num}"]
        if self.file_path:
            parts.append(f"File: {self.file_path}")

        if self.failure_type == "false_positive":
            parts.append(f"Type: FALSE POSITIVE (our tool reported a bug that isn't real)")
            parts.append(f"Category: {self.candidate_category}")
            parts.append(f"Our comment: {self.candidate_comment}")
        elif self.failure_type == "false_negative":
            parts.append(f"Type: FALSE NEGATIVE (we missed a real bug)")
            parts.append(f"Severity: {self.golden_severity}")
            parts.append(f"Expected comment: {self.golden_comment}")

        return "\n".join(parts)
