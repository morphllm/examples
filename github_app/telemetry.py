"""Axiom telemetry for PR review pipeline.

Events are emitted at multiple granularity levels:
- review.started / review.completed / review.failed — lifecycle
- review.clone / review.post — outer operations
- review.agentic_round / review.tool_call / review.api_call — inner pipeline
- review.extraction — structured extraction step
- review.warpgrep_failed — WarpGrep-specific warnings
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)

_client = None
_DATASET = os.environ.get("AXIOM_DATASET", "morph-errors")

# Log levels for different event types
_EVENT_LEVELS = {
    "review.failed": "error",
    "review.warpgrep_failed": "warning",
}


def _get_client():
    global _client
    if _client is not None:
        return _client
    token = os.environ.get("AXIOM_TOKEN", "")
    if not token:
        return None
    try:
        from axiom_py import Client

        _client = Client(token=token)
        return _client
    except Exception:
        logger.warning("Failed to init Axiom client", exc_info=True)
        return None


def send_review_event(event: dict):
    """Send a structured review event to Axiom. Never raises."""
    if "_time" not in event:
        event["_time"] = datetime.now(timezone.utc).isoformat()
    event.setdefault("event_type", "pr_review")

    # Determine log level from event type
    level = _EVENT_LEVELS.get(event.get("event_type", ""), "info")
    event["level"] = level

    # Always log structured JSON for Fly.io log drain
    log_fn = getattr(logger, level, logger.info)
    log_fn("REVIEW_EVENT %s", json.dumps(event, default=str))

    client = _get_client()
    if client is None:
        return
    try:
        client.ingest_events(dataset=_DATASET, events=[event])
    except Exception:
        logger.warning("Failed to send event to Axiom (dataset=%s)", _DATASET, exc_info=True)


def make_event_emitter(base_context: dict) -> Callable[[str, dict], None]:
    """Create a telemetry callback that auto-attaches base context to every event.

    Usage:
        emit = make_event_emitter({"repo": "owner/repo", "pr_number": 42, ...})
        emit("review.started", {"diff_size_chars": 5000})
        # -> sends event with all base_context fields + event_type + data fields
    """
    def emit(event_name: str, data: dict) -> None:
        event = {**base_context, "event_type": event_name, **data}
        send_review_event(event)
    return emit
