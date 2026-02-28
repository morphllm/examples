"""Configuration for the GitHub App."""

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


@dataclass
class AppConfig:
    """GitHub App configuration loaded from environment variables."""

    github_app_id: str = ""
    github_private_key: str = ""
    github_webhook_secret: str = ""
    anthropic_api_key: str = ""
    morph_api_key: str = ""
    clone_base_dir: str = "/tmp/pr-review-clones"
    max_concurrent_reviews: int = 3
    max_issues_per_pr: int = 8
    log_level: str = "INFO"

    def __post_init__(self):
        self.github_app_id = self.github_app_id or os.environ.get("GITHUB_APP_ID", "")
        self.github_webhook_secret = self.github_webhook_secret or os.environ.get(
            "GITHUB_WEBHOOK_SECRET", ""
        ) or os.environ.get("GHAPP_INTERNAL_SECRET", "")
        self.anthropic_api_key = self.anthropic_api_key or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        self.morph_api_key = self.morph_api_key or os.environ.get("MORPH_API_KEY", "")

        # Load private key: direct env var, base64-encoded env var, or file path
        if not self.github_private_key:
            raw = os.environ.get("GITHUB_PRIVATE_KEY", "")
            if raw:
                # Could be base64-encoded
                if "BEGIN" not in raw:
                    try:
                        self.github_private_key = base64.b64decode(raw).decode()
                    except Exception:
                        self.github_private_key = raw
                else:
                    self.github_private_key = raw
            else:
                key_path = os.environ.get("GITHUB_PRIVATE_KEY_PATH", "")
                if key_path:
                    self.github_private_key = Path(key_path).read_text()

        self.max_concurrent_reviews = int(
            os.environ.get("MAX_CONCURRENT_REVIEWS", self.max_concurrent_reviews)
        )
        self.max_issues_per_pr = int(
            os.environ.get("MAX_ISSUES_PER_PR", self.max_issues_per_pr)
        )
        self.log_level = os.environ.get("LOG_LEVEL", self.log_level)

        # Validate required fields
        missing = []
        if not self.github_app_id:
            missing.append("GITHUB_APP_ID")
        if not self.github_private_key:
            missing.append("GITHUB_PRIVATE_KEY or GITHUB_PRIVATE_KEY_PATH")
        if not self.github_webhook_secret:
            missing.append("GITHUB_WEBHOOK_SECRET")
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if missing:
            raise ValueError(f"Missing required config: {', '.join(missing)}")

        Path(self.clone_base_dir).mkdir(parents=True, exist_ok=True)
