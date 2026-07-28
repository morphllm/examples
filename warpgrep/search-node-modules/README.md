# WarpGrep: Search node_modules

WarpGrep skips `node_modules` by default. Clear the exclude list to search inside your dependencies — useful for debugging a library or reading how a package works under the hood.

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
MORPH_API_KEY=your-key npx tsx search.ts "Where does this package retry failed requests?"
```

## What it does

1. Creates a `MorphClient` with your API key
2. Calls `morph.warpGrep.execute()` with `excludes: []`
3. Prints the first 400 characters of each matching file

`excludes: []` clears the default exclude list — `node_modules`, `dist`, `.git`, `__pycache__`, and ~30 other patterns. WarpGrep runs faster with less context, so only clear it when you actually need to search dependencies.
