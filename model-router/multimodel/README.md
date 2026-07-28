# Model Router: Route, then call

`/v1/router/multimodel` takes your candidate models and a policy, and returns the single model to call. This example runs one prompt under all four policies so you can see the knob move the pick, then calls whatever `balanced` chose.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx route.ts
MORPH_API_KEY=your-key npx tsx route.ts "Rename the variable foo to bar in utils.py"
```

The second hop needs the chosen provider's own key. Set whichever you have:

| Provider | Env var | API |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `https://api.anthropic.com/v1/messages` |
| `openai` | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| `gemini` | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta` |
| `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| `zai` | `ZAI_API_KEY` | `https://api.z.ai/api/paas/v4` |
| `moonshot` | `MOONSHOT_API_KEY` | `https://api.moonshot.cn/v1` |

Without the matching key the script prints the routing decision and stops there.

## Output

```
Prompt: Write a distributed rate limiter with sliding windows across 12 regions and prove the invariants
Candidates: deepseek-v4-flash, glm-5.2, claude-opus-4-8

policy              model              provider    difficulty  ambiguity  domain
balanced            claude-opus-4-8    anthropic   hard        med        coding
cost_efficient      glm-5.2            zai         hard        med        coding
capability_heavy    claude-opus-4-8    anthropic   hard        med        coding
domain_skills       glm-5.2            zai         hard        med        coding

Routed to claude-opus-4-8 (anthropic), but ANTHROPIC_API_KEY is not set — skipping the call.
```

Same prompt, same classification, two different models. `capability_heavy` is cost-blind and buys Opus. `cost_efficient` sees GLM-5.2 covers `hard`/`med` too and takes the cheaper one. The policy is the only thing that changed.

## What it does

1. `POST /v1/router/multimodel` with `{ input, allowed_models, policy, default_model }`, once per policy
2. Prints each pick with the classifier signals the router echoed back
3. Calls the `balanced` pick on its own provider's API, or reports why it can't

## Notes

`allowed_models` pins the candidate set to three. Leaving it off considers the whole catalog, which is the right way to explore the API and the wrong way to run it in production — every extra candidate is another prefix cache to lose. `default_model` is returned as-is when the prompt classifies as `needs_info`.

A model switch is a full re-prefill upstream, so route at session and task boundaries rather than per turn. Details in the [router docs](https://docs.morphllm.com/sdk/components/router).
