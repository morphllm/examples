# WarpGrep + Daytona Sandbox

A coding agent that uses WarpGrep to search code inside a [Daytona](https://daytona.io) cloud sandbox. The repo is cloned into the sandbox, and WarpGrep executes ripgrep, sed, and find remotely via `remoteCommands`.

## Setup

```bash
npm install
```

## Run

```bash
DAYTONA_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
```

Ask a specific question:

```bash
DAYTONA_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does streaming work?"
```

## How it works

1. Creates a Daytona sandbox and clones `anthropics/anthropic-cookbook` into it
2. Installs ripgrep in the sandbox
3. Creates a WarpGrep tool with `remoteCommands` that execute inside the sandbox via `sandbox.process.executeCommand()`
4. Runs a Claude agent loop — when Claude calls the tool, WarpGrep searches the sandboxed repo
5. Cleans up the sandbox when done

The agent loop runs up to 5 turns, giving Claude multiple chances to search and refine its understanding.
