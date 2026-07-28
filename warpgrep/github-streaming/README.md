# WarpGrep: GitHub Streaming

Same as [github-search](../github-search), but streams each agent turn as it happens. Show the user what WarpGrep is doing instead of a spinner.

## Setup

```bash
npm install
```

## Run

Defaults to searching `expressjs/express` for middleware chaining:

```bash
MORPH_API_KEY=your-key npx tsx stream.ts
```

Pass a query and a repo:

```bash
MORPH_API_KEY=your-key npx tsx stream.ts "Find the router matching logic" expressjs/express
```

## Example output

```
Streaming search on expressjs/express: "Find how middleware chains work"

Turn 1:
  grep(pattern="function next", sub_dir="lib/")
  list_directory(path="lib/")

Turn 2:
  read(path="lib/router/index.js", lines="1-120")

Found 2 files:
--- lib/router/index.js ---
...
```

## What it does

1. Calls `morph.warpGrep.searchGitHub()` with `streamSteps: true`, which returns an async generator
2. Iterates the generator manually — each yield is one agent turn, with `turn` and `toolCalls`
3. The `done` value is the final result, with `contexts` holding the matched files
