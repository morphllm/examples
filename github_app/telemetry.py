"""Axiom telemetry for PR review pipeline."""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_client = None
_DATASET = os.environ.get("AXIOM_DATASET", "morph-errors")


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

    # Always log structured JSON for Fly.io log drain
    import json
    logger.info("REVIEW_EVENT %s", json.dumps(event, default=str))

    client = _get_client()
    if client is None:
        return
    try:
        client.ingest_events(dataset=_DATASET, events=[event])
    except Exception:
        logger.warning("Failed to send event to Axiom (dataset=%s)", _DATASET, exc_info=True)
