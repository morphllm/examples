"""Axiom telemetry for PR review pipeline."""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_client = None
_DATASET = "pr-review"


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
    client = _get_client()
    if client is None:
        logger.debug("Axiom not configured, skipping telemetry event")
        return
    try:
        if "_time" not in event:
            event["_time"] = datetime.now(timezone.utc).isoformat()
        client.ingest_events(dataset=_DATASET, events=[event])
        logger.info("Sent telemetry event to Axiom (%s)", event.get("status", "?"))
    except Exception:
        logger.warning("Failed to send event to Axiom", exc_info=True)
