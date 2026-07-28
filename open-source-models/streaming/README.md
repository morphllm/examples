# Open Source Models: Streaming

Stream tokens to stdout and measure what you actually got: time to first token, generation tok/s, and end-to-end tok/s.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx stream.ts
```

Pass a custom prompt or model:

```bash
MORPH_API_KEY=your-key npx tsx stream.ts "Write a binary search in Rust"
MORPH_MODEL=morph-qwen36-27b MORPH_API_KEY=your-key npx tsx stream.ts
```

## What it does

1. Sets `stream: true` and `stream_options: { include_usage: true }`
2. Writes each delta to stdout as it arrives
3. Prints throughput from the server's own token count on the final usage chunk

## Notes

Generation tok/s excludes the wait for the first token; end-to-end includes it. On a long prompt the gap between the two is prefill, and that gap is what prefix caching shrinks on repeat calls.

Reasoning models stream their chain of thought as `delta.reasoning_content` before the answer arrives in `delta.content`. This example prints both so you see tokens immediately instead of a long silence.
