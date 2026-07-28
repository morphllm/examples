# Compact: Chat Completions

Compaction through the OpenAI SDK. If you already have an OpenAI client in your codebase, this is the smallest possible change: swap the `baseURL` and set `model: "morph-compactor"`.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx compact.ts
```

## What it does

1. Constructs an `OpenAI` client with `baseURL: "https://api.morphllm.com/v1"` and your Morph key
2. Calls `chat.completions.create()` with `model: "morph-compactor"` and the transcript as a user message
3. Reads the compacted text from `choices[0].message.content`

```typescript
const response = await client.chat.completions.create({
  model: "morph-compactor",
  messages: [{ role: "user", content: transcript }],
  query: "how the rate limiter behaves when Redis is down",
  compression_ratio: 0.4,
});
```

`query` and `compression_ratio` are not part of the OpenAI schema, so TypeScript needs a `@ts-expect-error` on them. The API accepts them fine.

Real output from a run:

```
{
  "prompt_tokens": 226,
  "completion_tokens": 132,
  "total_tokens": 358
}
```

Note that this surface returns OpenAI-shaped usage — token counts only, no `compression_ratio` field and no `compacted_line_ranges`. If you need either, use the native `POST /v1/compact` endpoint shown in [basic](../basic).
