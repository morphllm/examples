# WarpGrep: GitHub Search

Search any public GitHub repository without cloning it. WarpGrep indexes the repo on Morph's servers, so you don't need the code locally or ripgrep installed.

## Setup

```bash
npm install
```

## Run

Defaults to searching `vercel/next.js` for App Router internals:

```bash
MORPH_API_KEY=your-key npx tsx search.ts
```

Pass a query and a repo:

```bash
MORPH_API_KEY=your-key npx tsx search.ts "How is the config file parsed?" tailwindlabs/tailwindcss
```

## What it does

1. Creates a `MorphClient` with your API key
2. Calls `morph.warpGrep.searchGitHub()` with a query and an `owner/repo` string
3. Prints the relevant files and snippets

No local checkout, no `repoRoot`. The search runs entirely remotely.
