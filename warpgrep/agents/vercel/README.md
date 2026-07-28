# WarpGrep + Vercel AI SDK

WarpGrep as a tool inside a Vercel AI SDK agent. `generateText` runs the tool loop for you — pass the tool and it keeps calling until the model produces an answer.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
```

Ask a specific question:

```bash
MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does auth work?"
```

## How it works

1. Creates a WarpGrep tool with `createWarpGrepTool({ repoRoot: '.' })` from `@morphllm/morphsdk/tools/warp-grep/vercel`
2. Passes it to `generateText()` as `tools: { warpgrep_codebase_search: grepTool }`
3. `maxSteps: 5` caps how many tool rounds the model gets before it must answer
4. Prints each search from the returned `steps`, then the final text

There is no manual agent loop here — that is the difference from the [anthropic](../anthropic) and [openai](../openai) examples, which drive the loop themselves.
