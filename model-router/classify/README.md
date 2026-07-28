# Model Router: Classify

Send three contrasting prompts to `/v1/router/classify` and print the raw classifier labels side by side. Use this endpoint when you already have a model mapping you trust and only want the signals.

## Setup

```bash
npm install              # TypeScript
pip install -r requirements.txt   # Python
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx classify.ts
MORPH_API_KEY=your-key python classify.py
```

Classify your own prompt:

```bash
MORPH_API_KEY=your-key npx tsx classify.ts "Migrate the auth service off sessions onto JWTs"
```

## Output

```
PROMPT                                               DIFFICULTY    AMBIGUITY     DOMAIN
Fix the typo in the README title                     easy (1.00)   low (0.98)    coding (0.81)
Design a multi-region event sourcing system with ... hard (0.97)   med (0.89)    coding (0.99)
make it faster                                       easy (0.86)   high (0.95)   coding (0.80)
```

Three prompts, three distinct routing decisions. The typo is `easy`/`low` and any cheap model handles it. The event sourcing design is `hard` and needs a strong one. "make it faster" is a small task whose *intent* is unclear — `easy` difficulty but `high` ambiguity, which is the case for escalating rather than saving.

## What it does

1. `POST /v1/router/classify` with `{ input, classes: ["difficulty", "ambiguity", "domain"] }`
2. Reads `label`, `confidence`, and `meets_threshold` off each head
3. Renders a head that missed its threshold as carrying no signal — for `difficulty` that means `needs_info`

One call per prompt, all three heads in that call. See the [root README](../README.md) for the full label taxonomy.
