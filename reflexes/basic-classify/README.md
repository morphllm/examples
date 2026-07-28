# Reflex: Basic Classify

Classify text with a trained reflex. One HTTP call, ~15ms of inference. Same example in TypeScript and Python.

## Setup

TypeScript:

```bash
npm install
```

Python:

```bash
pip install -r requirements.txt
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx classify.ts
MORPH_API_KEY=your-key python classify.py
```

Both default to the built-in `user-frustrated` reflex. Point them at your own model with `REFLEX_MODEL_ID`:

```bash
MORPH_API_KEY=your-key REFLEX_MODEL_ID=your-model-id npx tsx classify.ts
```

## Example output

```
"I need a refund for my order, this is unacceptable"
  → Frustrated (100.0%)

"How do I reset my password?"
  → Not Frustrated (100.0%)
```

## What it does

1. POSTs `{ model, text }` to `https://api.morphllm.com/v1/reflex/predict`
2. Reads the `classes` array off the response — each entry has a `label`, a `score`, and a `selected` flag
3. Prints the highest-scoring class for each of four sample texts

No SDK, no dependencies beyond `fetch` (TS) and `requests` (Python).
