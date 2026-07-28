# Reflex: Streaming Webhook

An HTTP server that classifies every request body as it arrives. Drop it in front of a support inbox, a Slack event stream, or an agent turn log to tag traffic in real time.

Runs on [Bun](https://bun.sh) — it uses `Bun.serve`.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key bun run webhook.ts
```

Then POST some JSON:

```bash
curl -X POST http://localhost:3000 \
  -H "Content-Type: application/json" \
  -d '{"text": "this is the third time I have asked"}'
```

```json
{
  "original": "this is the third time I have asked",
  "label": "Frustrated",
  "confidence": 0.9999,
  "classes": [...],
  "inference_time_ms": 13.6
}
```

Defaults to the built-in `user-frustrated` reflex. Override with `REFLEX_MODEL_ID`.

## What it does

1. Serves on port 3000, POST only
2. Pulls the text out of `text`, `message`, or `content` — whichever the caller sent
3. Classifies it against `/v1/reflex/predict`
4. Returns the top label plus the full score distribution

Classification adds ~15ms, so this stays fast enough to sit inline on a webhook path.
