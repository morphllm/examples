"""Basic Compaction

Compact a long agent transcript and print the compression ratio.
Every line that survives is byte-for-byte identical to the input —
compaction deletes lines, it never rewrites them.

Usage:
    MORPH_API_KEY=your-key python compact.py
"""

import os
import sys

import requests

api_key = os.environ.get("MORPH_API_KEY")
if not api_key:
    sys.exit("MORPH_API_KEY is not set")

TRANSCRIPT = """user: hey, quick question about the checkout service
assistant: Sure, what's up?
user: we're seeing duplicate charges on retries
assistant: Let me look at the payment path.
assistant: Found it. In services/checkout/charge.ts the retry wrapper re-invokes
  createCharge() without an idempotency key, so Stripe treats each attempt as a
  new charge.
assistant: The fix is to generate the idempotency key once per checkout attempt
  and pass it through the retry loop:
    key = f"checkout_{order_id}_{attempt_id}"
    stripe.Charge.create(**params, idempotency_key=key)
user: nice. unrelated, what's our python version in CI?
assistant: .github/workflows/ci.yml pins python 3.12.
user: and the test command?
assistant: `pytest --cov`, with an 80% line threshold in pyproject.toml.
user: cool. is the staging DB seeded from prod?
assistant: No. scripts/seed_staging.py generates synthetic rows; there's no prod
  dump anywhere in the pipeline.
user: ok. how many rows?
assistant: 10,000 orders, 2,500 users, 40 merchants.
user: thanks
assistant: No problem.
user: oh also the frontend has a flash of unstyled content on the pricing page
assistant: That's a separate issue — the Tailwind stylesheet is loaded async in
  app/pricing/layout.tsx. Worth a follow-up ticket.
user: yeah later. back to the charges thing — do we need to backfill refunds?
assistant: Yes. Query for charges sharing (order_id, amount) within a 60s window:
    SELECT order_id, COUNT(*) FROM charges
    GROUP BY order_id, amount, date_trunc('minute', created_at)
    HAVING COUNT(*) > 1;
assistant: That returned 47 duplicate pairs over the last 30 days. Each needs a
  refund on the later charge.
user: who owns that runbook?
assistant: docs/runbooks/payment-refunds.md, owned by the payments team.
user: got it. what about alerting so we catch this next time?
assistant: Add a Grafana alert on the rate of charges-per-order exceeding 1.0
  over a 5 minute window. The metric already exists: checkout_charges_total.
user: perfect. anything else in charge.py that looks off?
assistant: One thing: the except block swallows CardError without logging, so
  declined cards are invisible in Loki. Add a logger.warning there."""

response = requests.post(
    "https://api.morphllm.com/v1/compact",
    headers={"Authorization": f"Bearer {api_key}"},
    json={
        "input": TRANSCRIPT,
        "query": "duplicate charges on retry in the checkout service",
        "compression_ratio": 0.4,
        "preserve_recent": 0,
    },
    timeout=120,
)
response.raise_for_status()
data = response.json()

print(data["output"])
print("\n---")
usage = data["usage"]
print(f"input tokens:       {usage['input_tokens']}")
print(f"output tokens:      {usage['output_tokens']}")
print(f"compression ratio:  {usage['compression_ratio']}")
print(f"processing time:    {usage['processing_time_ms']}ms")
