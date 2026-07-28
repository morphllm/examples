# Open Source Models: Tools and Structured Output

A full tool-call round trip, then a `json_schema` response you can `JSON.parse` without guarding. Both use the standard OpenAI request shapes.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx tools.ts
```

## What it does

**Tool calling** — sends a `tools` array, reads `message.tool_calls`, runs the local function, appends a `role: "tool"` message with the result, and sends it back for a final answer.

**Structured output** — sends `response_format: { type: "json_schema", json_schema: { strict: true, schema } }`. The content comes back as JSON matching the schema, including the `enum` constraint on `severity`.

## Notes

Runs on `morph-glm52-744b`. Every model whose `supported_features` lists `tools` and `structured_outputs` works here — check [`/api/models/json`](https://www.morphllm.com/api/models/json) and override with `MORPH_MODEL=...`.

For looser JSON, `response_format: { type: "json_object" }` also works, but say "respond in JSON" in the prompt as well or you can get prose back. `json_schema` needs no such hint.

Tool calls use OpenAI shape on `/v1/chat/completions`. If you're on the Anthropic-compatible `/v1/messages` endpoint, use Anthropic `tool_use` blocks instead.
