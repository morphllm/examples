# Compact: Basic

Compact a long agent transcript and print the before/after token counts.

## Setup

TypeScript:

```bash
npm install
```

Python:

```bash
pip install -r requirements.txt
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx compact.ts
MORPH_API_KEY=your-key python compact.py
```

## What it does

1. Sends a ~560 token transcript to `POST /v1/compact` with `query`, `compression_ratio: 0.4`, and `preserve_recent: 0`
2. Prints the compacted output, where dropped runs appear as `(filtered N lines)` markers
3. Prints `usage` — input tokens, output tokens, and the achieved compression ratio

Real output from a run:

```
input tokens:       559
output tokens:      259
compression ratio:  0.463
processing time:    67ms
```

The transcript wanders across five topics; the query names one of them. Everything about CI config, staging seed data, and the unrelated CSS bug gets dropped. The idempotency-key fix, the duplicate-charge SQL, and the alerting plan survive — verbatim, character for character.

`preserve_recent: 0` is used here so the whole transcript is eligible for compaction. In production set it to 3 or higher so the latest turns pass through untouched.
