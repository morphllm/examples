# Integrating Morph Compaction via API

This guide is a standalone prompt you can hand to a coding agent (or use yourself) to integrate Morph Compaction into any agent framework. It covers the raw HTTP API — no SDK required.

---

## What Morph Compaction Does

Morph Compaction removes irrelevant lines from chat history at 33,000 tok/s. Every surviving line is **byte-for-byte identical** to the original — this is not summarization, it's surgical line-level filtering. A typical pass achieves 50-70% reduction.

The model is called `morph-compactor` and has a 1M token context window.

---

## The API

**Endpoint:** `POST https://api.morphllm.com/v1/compact`

**Auth:** `Authorization: Bearer <MORPH_API_KEY>`

### Request Body

```json
{
  "model": "morph-compactor",
  "messages": [
    { "role": "user", "content": "Build a Node.js API with JWT auth" },
    { "role": "assistant", "content": "Sure, here's the implementation..." },
    { "role": "user", "content": "Now add rate limiting" }
  ],
  "query": "rate limiting",
  "compression_ratio": 0.3,
  "preserve_recent": 2,
  "compress_system_messages": false,
  "include_line_ranges": true,
  "include_markers": true
}
```

| Parameter | Type | Default | What it does |
|---|---|---|---|
| `messages` | `{role, content}[]` | required | Chat history to compact. Alternative: pass `input` as a single string. |
| `query` | `string` | auto-detected from last user message | Focuses compression — lines relevant to this query survive. Same input with different queries produces different output. |
| `compression_ratio` | `float` | `0.5` | Range 0.05-1.0. Lower = more aggressive. Use `0.3` for long sessions (100+ turns), `0.7` for short ones. |
| `preserve_recent` | `int` | `2` | Number of final messages to skip compacting entirely. These pass through untouched. |
| `compress_system_messages` | `bool` | `false` | Whether to compress system messages. Usually leave false. |
| `include_line_ranges` | `bool` | `true` | Include `compacted_line_ranges` in response (useful for debugging). |
| `include_markers` | `bool` | `true` | Insert `(filtered N lines)` markers where content was removed. |

### Response

```json
{
  "id": "cmpr-7373faf8af65",
  "object": "compact",
  "model": "morph-compactor",
  "output": "compressed text as single string...",
  "messages": [
    {
      "role": "user",
      "content": "compressed content for message 1",
      "compacted_line_ranges": [{ "start": 5, "end": 10 }],
      "kept_line_ranges": []
    },
    {
      "role": "assistant",
      "content": "compressed content for message 2"
    }
  ],
  "usage": {
    "input_tokens": 101,
    "output_tokens": 65,
    "compression_ratio": 0.644,
    "processing_time_ms": 109
  }
}
```

Key: `messages` is a **1:1 mapping** to your input messages. Each compacted message preserves the original role. Use this array to replace your chat state directly.

### curl Example

```bash
curl -X POST "https://api.morphllm.com/v1/compact" \
  -H "Authorization: Bearer $MORPH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "morph-compactor",
    "messages": [
      {"role": "user", "content": "Build Node.js API with JWT"},
      {"role": "assistant", "content": "Sure, here is the full implementation with express, jsonwebtoken, bcrypt, middleware, error handling, database setup, connection pooling, migrations..."},
      {"role": "user", "content": "Add rate limiting"}
    ],
    "query": "rate limiting",
    "compression_ratio": 0.3,
    "preserve_recent": 1
  }'
```

---

## How to Integrate: The Three Agent Harness Patterns

From studying real-world integrations (opencode, kimi-cli, crewAI, goose, SWE-agent, Roo-Code, suna), compaction integration follows one of three patterns depending on your agent's architecture.

### Pattern 1: Hook / Middleware (recommended)

**Used by:** opencode-morph-plugin, kimi-morph-plugin

This is the cleanest pattern. The agent framework exposes lifecycle hooks that fire before each LLM call. You intercept the message list, compact it, and write it back.

**How it works:**

```
Agent loop iteration N:
  1. User sends message
  2. Framework prepares messages for LLM
  3. >>> YOUR HOOK FIRES HERE (messages.transform / pre-request) <<<
     - Estimate total tokens (chars / 3 is a rough proxy)
     - If over threshold → call Morph compact API
     - Replace messages array with compacted version
     - Freeze the result for cache stability
  4. Messages sent to LLM
  5. LLM responds
  6. Repeat
```

**The opencode pattern in detail:**

```
┌─────────────────────────────────────────────────────┐
│ Hook: "experimental.chat.messages.transform"        │
│                                                     │
│ 1. Read output.messages (the full chat history)     │
│ 2. Estimate chars: sum of all text + tool outputs   │
│ 3. Compute threshold:                               │
│    - Fixed: MORPH_COMPACT_TOKEN_LIMIT * 3           │
│    - Or: modelContextTokens * 0.7 * 3              │
│ 4. If under threshold → return (no-op)              │
│ 5. Split: toCompact = messages[:-preserveRecent]    │
│           recent    = messages[-preserveRecent:]     │
│ 6. Serialize parts → {role, content} for API        │
│ 7. POST to /v1/compact                              │
│ 8. Build frozen block from result.messages           │
│ 9. output.messages = [...frozen, ...recent]          │
│ 10. Cache frozen block for next iteration            │
└─────────────────────────────────────────────────────┘
```

**Critical detail — freezing for prompt cache stability:**

Once you compact, the compacted messages become a "frozen block." On every subsequent hook call, you reuse the frozen block byte-for-byte (don't re-compact) until the threshold is crossed again. This preserves the LLM provider's prompt prefix cache — the first N bytes of the prompt never change, so the provider can cache-hit on them.

When re-compaction fires, you only compact the *uncompacted* messages (everything after the frozen block). You never double-compact. The old frozen block is discarded entirely.

```
State after first compaction:
  [frozen_msg_1, frozen_msg_2, frozen_msg_3] [recent_msg_1]
                                              ↑ preserved

Next 5 turns (under threshold):
  [frozen_msg_1, frozen_msg_2, frozen_msg_3] [msg_4, msg_5, msg_6, msg_7, msg_8]
  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  byte-stable prefix (cache hit)              new uncompacted messages

Threshold crossed again:
  Compact only [msg_4, msg_5, msg_6, msg_7] → new frozen block
  [new_frozen_1, new_frozen_2] [msg_8]
```

**The kimi-cli pattern (plugin-based):**

kimi-cli uses a plugin system where compaction is a replaceable provider. The plugin declares a `compact()` method conforming to a protocol:

```python
class Compaction(Protocol):
    async def compact(self, messages, llm, *, custom_instruction="") -> CompactionResult:
        ...
```

The framework calls `self._compaction.compact(self._context.history, llm)` when the threshold triggers. The result's `.messages` replace the context history directly.

The Morph plugin:
1. Splits messages: last N preserved, rest sent for compaction
2. Converts to `{role, content}` format
3. POSTs to `/v1/compact` with `compression_ratio: 0.3`
4. Maps response messages 1:1 back to original message objects
5. Falls back to a single "compaction summary" message if tool calls / non-text parts exist

**When to use this pattern:**
- Your agent framework has pre-request hooks or middleware
- You want compaction to be transparent to the agent (it never knows it happened)
- You want prompt cache stability

### Pattern 2: Explicit Command / Slash Command

**Used by:** opencode's `/compact`, any agent with manual triggers

The user or agent explicitly triggers compaction. This is simpler but requires awareness.

**How it works:**

```
User types /compact (or agent decides to compact)
  1. Grab current chat history
  2. POST to /v1/compact
  3. Replace chat state with compacted messages
  4. Continue conversation
```

This is often combined with Pattern 1 — auto-compact at threshold, manual compact on demand.

**When to use this pattern:**
- Your framework doesn't have hooks
- You want user control over when compaction happens
- You're building a simple agent without a plugin system

### Pattern 3: Pre-flight / Standalone

**Used by:** standalone scripts, batch processing, CI pipelines

Compact messages before they're sent to any LLM. This is "compaction as a preprocessing step."

```python
import requests

def compact_before_send(messages, query):
    response = requests.post(
        "https://api.morphllm.com/v1/compact",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "morph-compactor",
            "messages": messages,
            "query": query,
            "compression_ratio": 0.3,
            "preserve_recent": 2,
        },
    )
    return response.json()["messages"]

# In your agent loop:
messages = get_chat_history()
if estimate_tokens(messages) > threshold:
    messages = compact_before_send(messages, current_query)
response = call_llm(messages)
```

**When to use this pattern:**
- You control the agent loop directly
- No plugin/hook system available
- Simple scripts or one-off integrations

---

## Threshold Recommendations

From real-world usage and the Morph team's recommendations:

| Scenario | Trigger threshold | compression_ratio | preserve_recent |
|---|---|---|---|
| Default / general | 70% of context window | 0.3 | 1-2 |
| Tasks requiring high clarity | 50-60% of context window | 0.5 | 3 |
| Long agentic loops (100+ turns) | 70% of context window | 0.3 | 1 |
| Emergency (near context limit) | 90% of context window | 0.2 | 1 |
| Fixed token budget | Set `MORPH_COMPACT_TOKEN_LIMIT` | 0.3 | 1 |

The rough token estimation used in practice: **~3 characters per token**. This is intentionally conservative.

---

## Serializing Messages for the API

Most agent frameworks store messages as rich objects (with tool calls, reasoning blocks, metadata). The Morph API expects `{role, content}` pairs with plain text content. You need a serialization layer.

**What to include:**
- Text content → as-is
- Tool call outputs → `[Tool: tool_name] {input}\nOutput: {output}` (truncate to ~2000 chars)
- Tool errors → `[Tool: tool_name] Error: {error}`
- Reasoning blocks → `[Reasoning] {text}`
- Everything else → `[{type}]` (brief marker to preserve structure)

**What to exclude:**
- Binary content, images
- Internal metadata
- Messages with empty content after serialization

---

## Preserving Context with `<keepContext>` Tags

Wrap critical sections in `<keepContext></keepContext>` to force preservation:

```
<keepContext>
The database password is stored in vault at path secret/db/prod.
Never log or print this value.
</keepContext>
```

Rules:
- Tags must be on their own line
- Tags must open and close within the same message
- Preserved content counts against the compression budget
- Unclosed tags preserve everything to message end
- Tags are stripped from output

---

## Error Handling

| Status | What happened | What to do |
|---|---|---|
| 400 | Malformed request or input too large | Check message format |
| 401 | Invalid or missing API key | Check `MORPH_API_KEY` |
| 503 | Model not loaded | Retry after brief delay |
| 504 | Timeout | Increase timeout, reduce input size |

**Fallback strategy:** If compaction fails, continue with uncompacted messages. The agent should never break because compaction is unavailable. In the opencode plugin, a failed re-compaction falls back to the stale frozen block. A failed first compaction falls back to native/no compaction.

---

## OpenAI-Compatible Endpoints

If your framework already speaks OpenAI format, you can use Morph as a drop-in:

**Chat Completions format:**
```bash
curl -X POST "https://api.morphllm.com/v1/chat/completions" \
  -H "Authorization: Bearer $MORPH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "morph-compactor",
    "messages": [...]
  }'
```

**Responses API format:**
```bash
curl -X POST "https://api.morphllm.com/v1/responses" \
  -H "Authorization: Bearer $MORPH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "morph-compactor",
    "input": "your text or messages here"
  }'
```

This means you can point any OpenAI SDK client at `https://api.morphllm.com/v1` and use `model: "morph-compactor"` directly.

---

## Anti-Patterns

- **Don't double-compact.** Never feed already-compacted messages back into the compactor. Track what's been compacted and only compact new messages.
- **Don't compact too aggressively too early.** Compacting at 30% context usage throws away context the model needs. Start at 70%.
- **Don't skip `preserve_recent`.** The most recent messages contain the user's active intent. Always preserve at least 1-2.
- **Don't ignore the 1:1 message mapping.** The response `messages` array maps directly to your input. Use it to replace messages in-place, preserving roles and structure.
- **Don't treat this as summarization.** The output is verbatim lines from the input, not a rewrite. Design your integration accordingly — message structure is preserved, not collapsed.
