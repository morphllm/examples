# Fast Apply: Basic Edit

Apply a focused code edit to an existing TypeScript file. Fast Apply merges a partial `code_edit` into the full file, so coding agents can describe small changes without rewriting the whole file.

## Setup

```bash
npm install
```

Use `.env.example` as a template, then export your key or pass it inline:

```bash
MORPH_API_KEY=your-key npx tsx edit.ts
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx edit.ts
```

## What it does

1. Creates a `MorphClient` with your API key
2. Calls `morph.fastApply.execute()` for `src/auth.ts`
3. Sends a targeted `code_edit` with `// ... existing code ...` markers
4. Writes the merged edit to the file and prints the diff

The example fixes a missing null check in `login()`. This is useful for coding agents because the agent only needs to specify the intended change and enough local context to place it correctly.
