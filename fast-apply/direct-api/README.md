# Fast Apply: Direct API

Call Morph Fast Apply directly through the OpenAI-compatible chat completions API. This example does not use the Morph SDK.

## Setup

```bash
npm install
```

Use `.env.example` as a template, then export your key or pass it inline:

```bash
MORPH_API_KEY=your-key npx tsx apply.ts
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx apply.ts
```

## What it does

1. Reads `src/auth.ts`
2. Sends the original code, instruction, and partial `code_edit` to `https://api.morphllm.com/v1/chat/completions`
3. Uses `morph-v3-fast` to merge the partial edit into the full file
4. Writes the merged code back to `src/auth.ts` and prints a diff

This is the lowest-level Fast Apply integration path. It is useful when you want direct API control or when you are integrating Morph into an existing model gateway.
