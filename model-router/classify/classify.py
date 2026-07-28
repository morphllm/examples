# # Classify
#
# Send contrasting prompts to /v1/router/classify and print the three
# classifier heads side by side. One call per prompt, ~50ms each.
#
# Usage:
#   MORPH_API_KEY=your-key python classify.py
#   MORPH_API_KEY=your-key python classify.py "your own prompt"

import os
import sys
import time

import requests

API_KEY = os.environ.get("MORPH_API_KEY")
if not API_KEY:
    sys.exit("MORPH_API_KEY is not set. Get one at https://morphllm.com/dashboard/api-keys")

PROMPTS = [
    "Fix the typo in the README title",
    "Design a multi-region event sourcing system with exactly-once delivery "
    "and prove the invariants hold under partition",
    "make it faster",
]


def classify(text: str) -> dict:
    resp = requests.post(
        "https://api.morphllm.com/v1/router/classify",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={"input": text, "classes": ["difficulty", "ambiguity", "domain"]},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["classifications"]


def show(head: dict | None, when_below: str) -> str:
    """A head that missed its confidence threshold carries no signal. For
    difficulty that means needs_info: the prompt is too vague to size."""
    if not head:
        return "-"
    label = head["label"] if head["meets_threshold"] else when_below
    return f"{label} ({head['confidence']:.2f})"


prompts = [sys.argv[1]] if len(sys.argv) > 1 else PROMPTS

header = f"{'PROMPT':<52} {'DIFFICULTY':<20} {'AMBIGUITY':<18} {'DOMAIN':<18} {'MS':>5}"
print(header)
print("-" * len(header))

for prompt in prompts:
    started = time.monotonic()
    heads = classify(prompt)
    elapsed = int((time.monotonic() - started) * 1000)
    label = prompt if len(prompt) <= 52 else prompt[:49] + "..."
    print(
        f"{label:<52} "
        f"{show(heads.get('difficulty'), 'needs_info'):<20} "
        f"{show(heads.get('ambiguity'), 'no signal'):<18} "
        f"{show(heads.get('domain'), 'no signal'):<18} "
        f"{elapsed:>5}"
    )
