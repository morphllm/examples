# WarpGrep + OpenAI Agent

A coding agent that uses GPT-4o as the reasoning model and WarpGrep as a code search tool.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key OPENAI_API_KEY=your-key npx tsx agent.ts
```

Ask a specific question:

```bash
MORPH_API_KEY=your-key OPENAI_API_KEY=your-key npx tsx agent.ts "Find all API endpoints"
```

## How it works

Same pattern as the Anthropic example, but using the OpenAI SDK:

1. `createWarpGrepTool({ repoRoot: '.' })` — creates an OpenAI-formatted tool
2. Pass it via `tools: [grepTool]` in the chat completion
3. Execute with `grepTool.execute(input)` when GPT calls the tool
4. Format results with `grepTool.formatResult(result)`
