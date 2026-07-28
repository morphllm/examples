"""Configuration for the PR Review Agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the app directory
load_dotenv(Path(__file__).parent / ".env")


@dataclass
class Config:
    """Central configuration for the review pipeline."""

    # Provider selection: "anthropic" | "openai" | "google"
    provider: str = "openai"

    # API keys
    anthropic_api_key: str = ""
    morph_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # Override to run the reviewer against any OpenAI-compatible endpoint,
    # e.g. https://api.morphllm.com/v1 with model="morph-glm52-744b".
    openai_base_url: str = ""

    # Model settings
    model: str = "gpt-5.4"
    max_tokens: int = 64000

    # Confidence thresholds (lower = more recall, higher = more precision)
    base_confidence_threshold: float = 0.50
    category_thresholds: dict = field(default_factory=lambda: {
        # High-value categories: keep thresholds at 0.50
        "logic_error": 0.50,
        "incorrect_value": 0.50,
        "incorrect_values": 0.50,
        "wrong_parameter": 0.50,
        "api_misuse": 0.50,
        "localization": 0.50,
        "test_correctness": 0.50,
        "portability": 0.50,
        # Medium-value: raise thresholds
        "race_condition": 0.60,
        "type_mismatch": 0.60,
        "type_error": 0.60,
        "security": 0.60,
        "null_reference": 0.70,  # FP-prone
        # Low-value / FP-prone: higher bar but not suppressed
        "missing_validation": 0.70,
        "resource_leak": 0.70,
        "resource_leaks": 0.70,
        "performance": 0.70,
        "documentation": 0.70,
        "style": 0.70,
        "naming": 0.70,
        "refactor": 0.70,
    })

    # WarpGrep settings
    warpgrep_model: str = "morph-warp-grep-v2.1"
    warpgrep_base_url: str = "https://api.morphllm.com/v1"
    warpgrep_max_turns: int = 6
    warpgrep_validate_issues: bool = True
    warpgrep_tool_enabled: bool = True  # Enable WarpGrep as a Claude tool during review

    # Pipeline settings
    max_concurrent_prs: int = 3
    warpgrep_queries_per_file: int = 3
    review_passes: int = 3  # file-level, cross-file, calibration

    # Optional reviewer personality for persona injection
    personality: str | None = None

    def __post_init__(self):
        # Set defaults from environment variables
        self.anthropic_api_key = self.anthropic_api_key or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        self.morph_api_key = self.morph_api_key or os.environ.get(
            "MORPH_API_KEY", ""
        )
        self.openai_api_key = self.openai_api_key or os.environ.get(
            "OPENAI_API_KEY", ""
        )
        self.google_api_key = self.google_api_key or os.environ.get(
            "GOOGLE_API_KEY", ""
        )
        self.openai_base_url = self.openai_base_url or os.environ.get(
            "OPENAI_BASE_URL", ""
        )
