# Integrating Morph Compaction via SDK

This guide is a standalone prompt you can hand to a coding agent to integrate Morph Compaction using the `@morphllm/morphsdk` TypeScript SDK or the OpenAI Python SDK. For the raw HTTP API approach, see `one-prompt-integration-api.md`.

---

## Quick Start

### TypeScript (Morph SDK)

```bash
npm install @morphllm/morphsdk
```

```typescript
import { CompactClient } from "@morphllm/morphsdk";

const compact = new CompactClient({
  morphApiKey: process.env.MORPH_API_KEY!,
  morphApiUrl: "https://api.morphllm.com",
  timeout: 60000,
});

const result = await compact.compact({
  messages: [
    { role: "user", content: "Build a REST API with auth..." },
    { role: "assistant", content: "Here's the full implementation..." },
    { role: "user", content: "Add rate limiting" },
  ],
  compressionRatio: 0.3,
  preserveRecent: 1,
});

// result.messages → 1:1 compacted version of input messages
// result.output   → single string with all compacted content
// result.usage    → { input_tokens, output_tokens, compression_ratio, processing_time_ms }
```

### TypeScript (MorphClient — unified)

```typescript
import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY! });

const result = await morph.compact({
  input: chatHistory,
  query: "How do I validate JWT tokens?",
});
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["MORPH_API_KEY"],
    base_url="https://api.morphllm.com/v1",
)

response = client.responses.create(
    model="morph-compactor",
    input=chat_history,
)

compressed = response.output[0].content[0].text
```

### Python (requests)

```python
import requests

response = requests.post(
    "https://api.morphllm.com/v1/compact",
    headers={"Authorization": f"Bearer {os.environ['MORPH_API_KEY']}"},
    json={
        "model": "morph-compactor",
        "messages": messages,
        "query": current_query,
        "compression_ratio": 0.3,
        "preserve_recent": 2,
    },
)
result = response.json()
compacted_messages = result["messages"]  # 1:1 mapping to input
```

### Edge Runtime

```typescript
import { CompactClient } from "@morphllm/morphsdk/edge";

export default {
  async fetch(request: Request, env: Env) {
    const compact = new CompactClient({ morphApiKey: env.MORPH_API_KEY });
    const { input, query } = await request.json();
    const result = await compact.compact({ input, query });
    return Response.json({ output: result.output, usage: result.usage });
  },
};
```

---

## Full Integration Example: Plugin with Hook-Based Auto-Compaction

This is the pattern used by the opencode-morph-plugin. It's the gold standard for transparent, cache-friendly compaction in a plugin system.

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Agent Framework                       │
│                                                         │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ User msg │───>│ messages.    │───>│ Send to LLM  │  │
│  │          │    │ transform    │    │              │  │
│  └──────────┘    │ hook         │    └──────────────┘  │
│                  │              │                       │
│                  │ Your plugin  │                       │
│                  │ lives here   │                       │
│                  └──────┬───────┘                       │
│                         │                               │
│              ┌──────────▼──────────┐                    │
│              │  Morph Compact API  │                    │
│              │  /v1/compact        │                    │
│              └─────────────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

### Step-by-Step Implementation

**1. Configuration**

```typescript
// Tunable via environment variables
const COMPACT_CONTEXT_THRESHOLD = parseFloat(
  process.env.MORPH_COMPACT_CONTEXT_THRESHOLD || "0.7"
);
const COMPACT_PRESERVE_RECENT = parseInt(
  process.env.MORPH_COMPACT_PRESERVE_RECENT || "1", 10
);
const COMPACT_RATIO = parseFloat(
  process.env.MORPH_COMPACT_RATIO || "0.3"
);
const COMPACT_TOKEN_LIMIT = process.env.MORPH_COMPACT_TOKEN_LIMIT
  ? parseInt(process.env.MORPH_COMPACT_TOKEN_LIMIT, 10)
  : null;

const CHARS_PER_TOKEN = 3; // rough estimate
```

**2. Client Setup**

```typescript
import { CompactClient } from "@morphllm/morphsdk";

const compactClient = new CompactClient({
  morphApiKey: process.env.MORPH_API_KEY!,
  morphApiUrl: "https://api.morphllm.com",
  timeout: 60000,
});
```

**3. State Management — The Frozen Block**

This is the most important concept. Once messages are compacted, the result is "frozen" and reused byte-for-byte on every subsequent hook call. This preserves prompt cache stability — the prefix bytes never change.

```typescript
let compactionState: {
  frozenMessages: CompactedMessage[];   // the compacted messages
  compactedUpToIndex: number;           // where the frozen block ends in the original array
  frozenChars: number;                  // char count of frozen block (for threshold math)
} | null = null;
```

**4. Message Serialization**

Agent frameworks store messages as rich objects. The Morph API needs `{role, content}` with plain text.

```typescript
function serializePart(part: Part): string {
  switch (part.type) {
    case "text":
      return part.text;
    case "tool": {
      if (part.state.status === "completed") {
        const input = JSON.stringify(part.state.input).slice(0, 500);
        const output = (part.state.output || "").slice(0, 2000);
        return `[Tool: ${part.tool}] ${input}\nOutput: ${output}`;
      }
      if (part.state.status === "error") {
        return `[Tool: ${part.tool}] Error: ${part.state.error}`;
      }
      return `[Tool: ${part.tool}] ${part.state.status}`;
    }
    case "reasoning":
      return `[Reasoning] ${part.text}`;
    default:
      return `[${part.type}]`;
  }
}

function messagesToCompactInput(messages) {
  return messages
    .map(m => ({
      role: m.info.role,
      content: m.parts.map(serializePart).join("\n"),
    }))
    .filter(m => m.content.length > 0);
}
```

**5. The Hook**

```typescript
hooks["messages.transform"] = async (_input, output) => {
  const messages = output.messages;

  // Calculate threshold
  const charThreshold = COMPACT_TOKEN_LIMIT
    ? COMPACT_TOKEN_LIMIT * CHARS_PER_TOKEN
    : modelContextTokens * COMPACT_CONTEXT_THRESHOLD * CHARS_PER_TOKEN;

  const totalChars = estimateTotalChars(messages);

  // Need something to compact beyond what we preserve
  if (messages.length <= COMPACT_PRESERVE_RECENT) return;

  // ── CASE 1: We have a frozen block from a previous compaction ──
  if (compactionState) {
    const uncompacted = messages.slice(compactionState.compactedUpToIndex);
    const effectiveChars = compactionState.frozenChars + estimateTotalChars(uncompacted);

    if (effectiveChars < charThreshold) {
      // Under threshold → reuse frozen block as-is (cache hit)
      output.messages = [...compactionState.frozenMessages, ...uncompacted];
      return;
    }

    // Over threshold again → re-compact only the uncompacted messages
    // NEVER double-compact the frozen block
    if (uncompacted.length <= COMPACT_PRESERVE_RECENT) return;

    const toCompact = uncompacted.slice(0, -COMPACT_PRESERVE_RECENT);
    const recent = uncompacted.slice(-COMPACT_PRESERVE_RECENT);

    try {
      const result = await compactClient.compact({
        messages: messagesToCompactInput(toCompact),
        compressionRatio: COMPACT_RATIO,
        preserveRecent: 0,  // we handle preservation ourselves
      });

      const frozen = buildCompactedMessages(toCompact, result);
      compactionState = {
        frozenMessages: frozen,
        compactedUpToIndex: messages.length - recent.length,
        frozenChars: estimateTotalChars(frozen),
      };
      output.messages = [...frozen, ...recent];
    } catch (err) {
      // On failure, use stale frozen block as best-effort
      output.messages = [...compactionState.frozenMessages, ...uncompacted];
    }
    return;
  }

  // ── CASE 2: No frozen block — check if first compaction needed ──
  if (totalChars < charThreshold) return;

  const toCompact = messages.slice(0, -COMPACT_PRESERVE_RECENT);
  const recent = messages.slice(-COMPACT_PRESERVE_RECENT);

  if (toCompact.length === 0) return;

  try {
    const result = await compactClient.compact({
      messages: messagesToCompactInput(toCompact),
      compressionRatio: COMPACT_RATIO,
      preserveRecent: 0,
    });

    const frozen = buildCompactedMessages(toCompact, result);
    compactionState = {
      frozenMessages: frozen,
      compactedUpToIndex: messages.length - recent.length,
      frozenChars: estimateTotalChars(frozen),
    };
    output.messages = [...frozen, ...recent];
  } catch (err) {
    // Fall back to no compaction — agent continues normally
  }
};
```

**6. Building Compacted Messages**

The API returns a `messages` array that maps 1:1 to your input. Preserve the original message structure (IDs, roles) but replace content.

```typescript
function buildCompactedMessages(originalMessages, result) {
  // 1:1 mapping — each original message gets its compacted version
  if (result.messages.length !== originalMessages.length) {
    // Fallback: wrap entire output as a single user message
    return [{
      info: { ...originalMessages[0].info, role: "user" },
      parts: [{ type: "text", text: result.output }],
    }];
  }

  return result.messages.map((compacted, i) => {
    const original = originalMessages[i];
    return {
      info: { ...original.info, role: compacted.role },
      parts: [{
        id: `morph-compact-${original.info.id}`,   // deterministic ID
        type: "text",
        text: compacted.content,
      }],
    };
  });
}
```

The deterministic ID (`morph-compact-${originalId}`) is important — it ensures the frozen block is byte-identical across repeated calls, which is what enables prompt cache hits.

---

## The kimi-cli Pattern: Compaction as a Replaceable Provider

kimi-cli takes a different approach — compaction is a **pluggable provider** that the framework discovers at startup.

### Plugin Declaration

```json
{
  "name": "morph-plugin",
  "compaction": {
    "entrypoint": "morph_compaction.MorphCompaction"
  }
}
```

### The Protocol

```python
class Compaction(Protocol):
    async def compact(
        self,
        messages: Sequence[Message],
        llm: LLM,
        *,
        custom_instruction: str = "",
    ) -> CompactionResult:
        ...

class CompactionResult(NamedTuple):
    messages: Sequence[Message]
    usage: TokenUsage | None
```

### Implementation

```python
class MorphCompaction:
    async def compact(self, messages, llm, *, custom_instruction=""):
        # 1. Split: preserve last N messages, compact the rest
        to_compact, preserved = self._prepare_messages(messages)

        # 2. Serialize to {role, content} pairs
        api_messages = [
            {"role": m.role, "content": extract_text(m)}
            for m in to_compact
        ]

        # 3. Call Morph API
        payload = {
            "model": "morph-compactor",
            "messages": api_messages,
            "compression_ratio": 0.3,
            "preserve_recent": 0,
            "include_markers": True,
        }

        # Uses the provider's base_url and api_key from config
        response = await self._post_compact(payload)

        # 4. Map response messages back to framework Message objects
        compacted = self._build_messages(to_compact, response)

        return CompactionResult(
            messages=[*compacted, *preserved],
            usage=MorphTokenUsage(response["usage"]),
        )
```

### Config Activation

```toml
[providers.morph]
type = "openai_legacy"
base_url = "https://api.morphllm.com/v1"
api_key = "your-key"

[models.morph-compaction]
provider = "morph"
model = "morph-compactor"
max_context_size = 128000

[loop_control]
compaction_model = "morph-compaction"
compaction_plugin = "morph-plugin"
```

The framework validates that `compaction_model.max_context_size >= active_model.max_context_size` at startup, ensuring the compactor can handle the full context.

---

## Integration Checklist

When integrating compaction into a new agent framework, verify:

- [ ] **API key resolution**: `MORPH_API_KEY` from env, config, or secrets store
- [ ] **Message serialization**: Rich objects → `{role, content}` with plain text
- [ ] **Tool output truncation**: Cap tool outputs at ~2000 chars to avoid bloating the compact request
- [ ] **Threshold calculation**: `modelContextTokens * 0.7 * 3` chars as default trigger
- [ ] **Preserve recent**: Always keep 1-2 most recent messages uncompacted
- [ ] **Frozen block**: Cache compacted results for prompt cache stability
- [ ] **No double-compaction**: On re-compact, only compact uncompacted messages
- [ ] **Deterministic IDs**: Compacted message IDs derived from originals
- [ ] **Graceful failure**: If compact API fails, fall back to uncompacted (or stale frozen block)
- [ ] **Feature flag**: Let users disable compaction (`MORPH_COMPACT=false`)
- [ ] **Logging/toast**: Notify user when compaction fires and the compression ratio achieved

---

## Threshold Tuning via Environment Variables

These are the env vars used by the opencode-morph-plugin (a good convention to follow):

```bash
# Compact at 70% of context window (default)
export MORPH_COMPACT_CONTEXT_THRESHOLD=0.7

# Or use a fixed token limit instead
export MORPH_COMPACT_TOKEN_LIMIT=20000

# Compression aggressiveness (0.05-1.0, lower = more aggressive)
export MORPH_COMPACT_RATIO=0.3

# Number of recent messages to keep uncompacted
export MORPH_COMPACT_PRESERVE_RECENT=1

# Disable compaction entirely
export MORPH_COMPACT=false
```

For tasks requiring higher clarity, compress earlier (50-60% threshold) with a less aggressive ratio (0.5).

---

## Best Practices from Production Deployments

1. **Always provide `query`** — explicit queries yield tighter, more relevant compression. Auto-detection from the last user message works but is less precise.

2. **Use `<keepContext>` tags** for critical context that must never be removed (credentials paths, invariants, architectural decisions).

3. **Track compaction count per message** — if a message has been compacted 2+ times, consider deleting it entirely on the next pass rather than compacting again. (From the Morph team's recommendation.)

4. **Compact before LLM calls, not after** — this reduces inference cost on the downstream model.

5. **The frozen block pattern is non-negotiable for production** — without it, you'll thrash the prompt cache on every turn, negating the cost savings from compaction.

6. **Don't set `preserve_recent: 0` in the top-level call** — handle preservation yourself by splitting messages before calling the API. This gives you control over exactly which messages are protected.
