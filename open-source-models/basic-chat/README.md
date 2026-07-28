# Open Source Models: Basic Chat

The smallest possible Morph completion: the OpenAI SDK with `baseURL` swapped to Morph. Prints the model that served the request and the token usage.

## Setup

```bash
npm install
```

Python:

```bash
pip install -r requirements.txt
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx chat.ts
MORPH_API_KEY=your-key python chat.py
```

Pass a custom prompt:

```bash
MORPH_API_KEY=your-key npx tsx chat.ts "Write a SQL query for top 5 customers by revenue"
```

Pick a different model:

```bash
MORPH_MODEL=morph-dsv4flash MORPH_API_KEY=your-key npx tsx chat.ts
```

## What it does

1. Creates an OpenAI client with `baseURL: "https://api.morphllm.com/v1"`
2. Calls `chat.completions.create()` with `model: "morph-glm52-744b"`
3. Prints the completion, then the served model and token counts

The `served by` line comes from `response.model` — the model Morph actually routed to, which is not always the ID you asked for. `cached prompt tokens` shows automatic prefix caching working; run the same prompt twice and the second call reports a cache hit.
