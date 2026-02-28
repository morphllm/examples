"""FastAPI server for the PR Review Agent GitHub App."""

import hashlib
import hmac
import json
import logging
from collections import OrderedDict

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from github_app.config import AppConfig
from github_app.worker import process_review

logger = logging.getLogger(__name__)

app = FastAPI(title="PR Review Agent")
config = AppConfig()

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.log_level),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Delivery dedup (bounded LRU)
_seen_deliveries: OrderedDict[str, None] = OrderedDict()
_MAX_SEEN = 1000


def _verify_signature(body: bytes, secret: str, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256 from GitHub webhook."""
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.HMAC(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()

    # Verify signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body, config.github_webhook_secret, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    # Dedupe
    if delivery_id in _seen_deliveries:
        logger.debug("Duplicate delivery %s, skipping", delivery_id)
        return {"status": "duplicate"}
    _seen_deliveries[delivery_id] = None
    if len(_seen_deliveries) > _MAX_SEEN:
        _seen_deliveries.popitem(last=False)

    payload = json.loads(body)

    if event == "pull_request" and payload.get("action") in (
        "opened",
        "synchronize",
        "reopened",
    ):
        pr = payload["pull_request"]
        logger.info(
            "Enqueuing review for %s PR #%d (action=%s)",
            pr["base"]["repo"]["full_name"],
            pr["number"],
            payload["action"],
        )
        background_tasks.add_task(process_review, payload, config)

    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
