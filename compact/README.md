# Compact: Context Compression

Compaction removes irrelevant lines from chat history and code context at 33,000 tok/s. Every line that survives is byte-for-byte identical to the input. Typical reduction is 50-70%.

## Why not just truncate

Truncation drops the oldest tokens. The problem is that "oldest" and "least relevant" are different things — the file path the agent found on turn 3 is exactly what it needs on turn 90, and a sliding window throws it away first.

Compaction scores every line against a `query` and drops the ones the query doesn't need, wherever they sit in the history. Feed the same transcript in twice with two different queries and you get two different outputs — see [query-conditioned](./query-conditioned) for a runnable demo of that.

It is also not summarization. Summarization rewrites, which means drift and the occasional invented detail. Compaction only ever deletes whole lines, so an identifier that survives is still spelled the way your code spells it.

The one input this is wrong for: minified code, single-line JSON blobs, anything where most of the payload lives on one line. There is nothing for the model to cut. Split it into lines first.

## Quickstart

```bash
curl -X POST https://api.morphllm.com/v1/compact \
  -H "Authorization: Bearer $MORPH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "def hello():\n    return 1\n\ndef unused():\n    pass\n\ndef world():\n    return 2",
    "query": "hello function",
    "compression_ratio": 0.5,
    "preserve_recent": 0
  }'
```

## Two surfaces

| Surface | Shape | Use when |
|---|---|---|
| `POST /v1/compact` | Native Morph. Returns `output`, per-message `compacted_line_ranges`, and `usage.compression_ratio` | You want the line ranges and the achieved ratio back |
| `POST /v1/chat/completions` with `model: "morph-compactor"` | OpenAI Chat Completions | You already have an OpenAI client and want a one-line change |

`POST /v1/responses` also accepts `model: "morph-compactor"` for the OpenAI Responses API shape.

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `input` | string or array | — | Text or `{role, content}` array. One of `input`/`messages` required |
| `messages` | array | — | `{role, content}` messages. Takes priority over `input` |
| `query` | string | auto-detected | What matters for the next LLM call. Always set it |
| `compression_ratio` | float | `0.5` | Target fraction to keep. `0.3` aggressive, `0.7` light |
| `preserve_recent` | int | `2` | Keep the last N messages uncompressed |
| `compress_system_messages` | bool | `false` | Compress system messages too |
| `include_line_ranges` | bool | `true` | Return `compacted_line_ranges` |
| `include_markers` | bool | `true` | Emit `(filtered N lines)` markers where lines were cut |

Wrap anything that must never be dropped in `<keepContext>` / `</keepContext>` tags, each on its own line. Preserved regions come back in `kept_line_ranges`.

## Examples

| Example | Language | Description |
|---------|----------|-------------|
| [basic](./basic) | TypeScript, Python | Compact a transcript, print before/after token counts |
| [query-conditioned](./query-conditioned) | TypeScript | One transcript, two queries, two different outputs |
| [chat-completions](./chat-completions) | TypeScript | The `morph-compactor` model through the OpenAI SDK |
| [websearch](./websearch) | Python (notebook) | Compact fetched web pages, then judge answer quality with and without |
| [integration-prompt](./integration-prompt) | — | Copy-paste prompts that make a coding agent wire compaction into your codebase |

## Practical notes

- Compact **before** the LLM call. Compacting a response after generation saves storage, not inference cost.
- Set `preserve_recent` to at least 3 in production. The examples here use `0` so the whole input is eligible.
- `compression_ratio` is a target, not a cap. When more of the input is relevant to the query, less gets cut.
