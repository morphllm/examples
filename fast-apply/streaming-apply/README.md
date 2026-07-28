# Fast Apply: Streaming Apply

Stream a merged file to stdout as it is generated, then print measured throughput.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx apply.ts
```

## What it does

1. Sends the same `<instruction>` / `<code>` / `<update>` message as [basic-apply](../basic-apply), with `stream: true`
2. Writes each content delta straight to stdout, so the merged file appears as it is produced
3. Passes `stream_options: { include_usage: true }` — the final chunk has no `choices`, only `usage`, which is where the real output-token count comes from
4. Prints time to first token and output tokens per second

Throughput is measured from the first content token to the last, so it reports generation speed rather than generation speed plus queueing. `morph-v3-fast` sustains 10,500+ tok/s on production hardware; a single small request over the public internet will read lower because prefill and network latency dominate at this size.

Streaming matters for editor integrations: the user sees the merged file filling in immediately instead of waiting for the whole response.
