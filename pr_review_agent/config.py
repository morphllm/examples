"""Configuration for the PR Review Agent."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Central configuration for the review pipeline."""

    # API keys
    anthropic_api_key: str = ""
    morph_api_key: str = ""

    # Paths
    benchmark_dir: Path = Path("")
    clone_dir: Path = Path("")
    output_dir: Path = Path("")

    # Model settings
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 16000

    # Confidence thresholds (lower = more recall, higher = more precision)
    base_confidence_threshold: float = 0.6
    category_thresholds: dict = field(default_factory=lambda: {
        "logic_error": 0.55,
        "incorrect_value": 0.55,
        "incorrect_values": 0.55,
        "wrong_parameter": 0.55,
        "race_condition": 0.65,
        "type_mismatch": 0.65,
        "type_error": 0.65,
        "null_reference": 0.75,  # FP-prone
        "api_misuse": 0.6,
        "missing_validation": 0.85,  # very FP-prone
        "resource_leak": 0.85,  # very FP-prone
        "resource_leaks": 0.85,
        "security": 0.65,
        "localization": 0.55,
        "test_correctness": 0.65,
        "portability": 0.65,
        "style": 0.99,
        "naming": 0.99,
        "refactor": 0.99,
        "documentation": 0.99,
        "performance": 0.99,
    })

    # WarpGrep settings
    warpgrep_model: str = "morph-warpgrep"
    warpgrep_base_url: str = "https://api.morphllm.com/v1"

    # Pipeline settings
    max_concurrent_prs: int = 3
    warpgrep_queries_per_file: int = 3
    review_passes: int = 3  # file-level, cross-file, calibration

    def __post_init__(self):
        # Set defaults from environment variables
        self.anthropic_api_key = self.anthropic_api_key or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        self.morph_api_key = self.morph_api_key or os.environ.get(
            "MORPH_API_KEY", ""
        )

        base = Path(__file__).parent.parent
        if not self.benchmark_dir or str(self.benchmark_dir) == ".":
            self.benchmark_dir = base / "code-review-benchmark" / "offline"
        if not self.clone_dir or str(self.clone_dir) == ".":
            self.clone_dir = base / "pr_clones"
        if not self.output_dir or str(self.output_dir) == ".":
            self.output_dir = base / "pr_review_agent" / "output"

        self.clone_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
