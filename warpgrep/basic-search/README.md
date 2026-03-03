# WarpGrep: Basic Search

Search any local codebase with a natural language query. Results come back in under 6 seconds.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx search.ts
```

Pass a custom query:

```bash
MORPH_API_KEY=your-key npx tsx search.ts "Find where errors are handled"
```

## What it does

1. Creates a `MorphClient` with your API key
2. Calls `morph.warpGrep.execute()` with a natural language query
3. Prints the relevant code files and snippets

That's it. WarpGrep handles all the searching, reasoning, and filtering internally.
