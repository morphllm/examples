# WarpGrep + Anthropic Agent

A coding agent that uses Claude as the reasoning model and WarpGrep as a code search tool. When Claude needs to understand code, it calls WarpGrep, which searches in a separate context window and returns only the relevant snippets.

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
MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How are errors handled?"
```

## How it works

1. Creates a WarpGrep tool with `createWarpGrepTool({ repoRoot: '.' })`
2. Passes the tool to Claude via `tools: [grepTool]`
3. When Claude calls the tool, executes it with `grepTool.execute(input)`
4. Sends results back with `grepTool.formatResult(result)`
5. Claude reads the code and answers your question

The agent loop runs up to 5 turns, giving Claude multiple chances to search and refine its understanding.
