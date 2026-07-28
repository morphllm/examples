# Model Router

A "fix this typo" request and a "design an event sourcing system" request look identical to your API call, but one costs 10x more than it should. The Morph Router classifies prompt difficulty, ambiguity, and domain in one call, then tells you which model to send it to.

Two endpoints:

- **`/v1/router/multimodel`** — hand it your candidate models plus a policy, get back the one to call. No mapping table to maintain.
- **`/v1/router/classify`** — get the raw classifier labels and do your own mapping.

Both are `POST https://api.morphllm.com/v1/...` with `Authorization: Bearer $MORPH_API_KEY`.

## Examples

| Example | Language | Description |
|---------|----------|-------------|
| [classify](./classify) | TypeScript, Python | Three contrasting prompts, labels printed side by side |
| [multimodel](./multimodel) | TypeScript | Router picks a model, you call it — with the policy knob moving the pick |

## Quickstart

```bash
curl -s -X POST https://api.morphllm.com/v1/router/multimodel \
  -H "Authorization: Bearer $MORPH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": "Add error handling to this function", "allowed_providers": ["anthropic"]}'
```

```json
{
  "model": "claude-haiku-4-5-20251001",
  "provider": "anthropic",
  "difficulty": "easy",
  "confidence": 0.93,
  "ambiguity": "low",
  "ambiguity_confidence": 0.88,
  "domain": "coding",
  "domain_confidence": 0.91
}
```

`model` is what you call next. `ambiguity` and `domain` are present only when those heads cleared their threshold — treat a missing field as no signal.

## Labels

**Difficulty**

| Label | What it means | Typical target |
|---|---|---|
| `easy` | Trivial change, any model handles it | Haiku, DeepSeek Flash, Gemini Flash |
| `medium` | Moderate complexity, benefits from a capable model | Sonnet, GLM-5.2, GPT-5.5 |
| `hard` | Complex task, needs a strong model | Opus, Kimi K3, Gemini Pro |
| `needs_info` | Difficulty didn't clear the confidence threshold | Your `default_model` |

**Ambiguity**

| Label | What it means |
|---|---|
| `low` | Well-specified request |
| `med` | Some detail missing |
| `high` | Underspecified; may need clarification |

**Domain**

| Label | What it means |
|---|---|
| `general` | General-purpose prompt |
| `summary` | Summarization / extraction |
| `coding` | Code generation or editing |
| `design` | Design / architecture |
| `data` | Data / analytics |

## Policies

The router scores every candidate on distance from the request's difficulty and ambiguity tier, domain coverage, and cost. The `policy` field sets the weights.

| Policy | Behavior |
|---|---|
| `balanced` (default) | Capability-first and cost-blind. Among models that cover the request equally, the more capable one wins. |
| `cost_efficient` | Same coverage test, cheaper tie-break. Drops a tier rather than pay for headroom. |
| `capability_heavy` | Domain-aware and cost-blind. The strongest in-domain model for the request. |
| `domain_skills` | Domain-aware and cost-aware. The cheapest in-domain specialist that still covers the tier. |

`balanced` and `cost_efficient` ignore domain, so they return the same model for every domain at a given difficulty and ambiguity.

## Provider catalog

| Provider | Models |
|---|---|
| `openai` | `gpt-5.5` |
| `anthropic` | `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-8`, `claude-fable-5` |
| `gemini` | `gemini-3.5-flash`, `gemini-3.1-pro-preview` |
| `deepseek` | `deepseek-v4-flash`, `deepseek-v4-pro` |
| `zai` | `glm-5.2` |
| `moonshot` | `kimi-k3` |

Restrict the candidate set with `allowed_models`, `allowed_providers`, or both — they union. Two or three candidates is the shape that holds up in production.

## Latency and cost

~180ms average per routing decision, one call. $0.005 per request. Max input 65,536 tokens; larger returns `413`.

## Prerequisites

- [Morph API key](https://morphllm.com/dashboard/api-keys) (`MORPH_API_KEY`)
- Node.js 18+ for the TypeScript examples, Python 3.10+ for the Python one
- A provider API key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...) only if you want [multimodel](./multimodel) to actually call the model it picked

## Docs

- [Model Router](https://docs.morphllm.com/sdk/components/router)
- [Prompt Caching](https://docs.morphllm.com/sdk/components/caching) — what a model switch costs you
- [Enterprise Model Routing](https://docs.morphllm.com/guides/enterprise-model-routing) — org-wide policy for Claude Code
