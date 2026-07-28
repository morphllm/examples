# Open Source Models

Open-weight models — Kimi K3, GLM-5.2, DeepSeek V4, Qwen, MiniMax, Gemma — served on Morph's own inference stack with custom kernels tuned for codegen and agent loops. The API is OpenAI-compatible at `https://api.morphllm.com/v1`, so any OpenAI SDK works by changing `baseURL`. Automatic prefix caching is on for every model, and every model supports tool calling and structured outputs.

## Catalog

Prices are USD per million tokens, pulled from [`/api/models/json`](https://www.morphllm.com/api/models/json) — that endpoint is the live source, this table is a snapshot.

| Model | Model ID | Context | In | Out | Cached in | Features |
|---|---|---|---|---|---|---|
| Kimi K3 | `morph-kimik3` | 1M | $2.90 | $14.00 | $0.29 | tools, json_mode, structured_outputs, logprobs, reasoning |
| Kimi K3 Fast | `morph-kimik3-fast` | 1M | $4.50 | $22.50 | $0.45 | tools, json_mode, structured_outputs, logprobs, reasoning |
| GLM-5.2 (744B) | `morph-glm52-744b` | 1M | $1.10 | $4.10 | $0.22 | tools, json_mode, structured_outputs, reasoning |
| DeepSeek V4 Flash | `morph-dsv4flash` | 1M | $0.139 | $0.278 | — | tools, json_mode, structured_outputs, reasoning |
| MiniMax M3 (428B) | `morph-minimax3-428b` | 256k | $0.30 | $1.20 | — | tools, json_mode, structured_outputs, logprobs, reasoning |
| Gemma 4 31B | `morph-gemma4-31b` | 175k | $0.14 | $0.40 | $0.08 | tools, json_mode, structured_outputs, logprobs, reasoning |
| Qwen 3.6 27B | `morph-qwen36-27b` | 131k | $0.289 | $2.40 | — | tools, json_mode, structured_outputs, logprobs, reasoning |

Kimi K3, MiniMax M3, and Gemma 4 31B accept image input as well as text.

## Examples

| Example | Language | Description |
|---------|----------|-------------|
| [basic-chat](./basic-chat) | TypeScript, Python | One completion; prints the serving model and token usage |
| [streaming](./streaming) | TypeScript | Stream tokens to stdout, report time to first token and tok/s |
| [tools-and-json](./tools-and-json) | TypeScript | Tool-call round trip plus a `json_schema` structured response |

Each directory has its own `README.md` and `package.json`. Set `MORPH_API_KEY` and run `npm install` in the one you want.

## Notes

- **Reasoning is off by default.** Enable with `reasoning: { effort: "low" | "medium" | "high" }`. Reasoning tokens bill as output. Several of these models return their chain of thought in `reasoning_content` and the answer in `content`, so give them enough `max_tokens` to get past the thinking.
- **Prefix caching** is automatic. Repeat the same prompt prefix and `usage.prompt_tokens_details.cached_tokens` reports the hit, billed at the cached rate above.
- **`response.model` is the model that served you**, which is not always the ID you requested. The basic-chat example prints it.
- Full docs: [Open Source Models](https://docs.morphllm.com/sdk/components/fast-models).
