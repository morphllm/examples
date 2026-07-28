# WarpGrep + Gemini Agent

A coding agent that uses Gemini as the reasoning model and WarpGrep as a code search tool. Gemini takes function declarations rather than JSON schemas — the SDK adapter emits the right shape.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key GOOGLE_API_KEY=your-key npx tsx agent.ts
```

Ask a specific question:

```bash
MORPH_API_KEY=your-key GOOGLE_API_KEY=your-key npx tsx agent.ts "How does auth work?"
```

## How it works

1. Creates a WarpGrep tool with `createWarpGrepTool({ repoRoot: '.' })` from `@morphllm/morphsdk/tools/warp-grep/gemini`
2. Registers it via `tools: [{ functionDeclarations: [grepTool] }]` on `getGenerativeModel()`
3. Starts a chat, then loops: read `response.functionCalls()`, run `grepTool.execute(call.args)`
4. Sends each result back as a `functionResponse` part, formatted with `grepTool.formatResult()`
5. Stops when Gemini returns no more function calls, or after 5 turns
