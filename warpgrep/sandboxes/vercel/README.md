# WarpGrep + Vercel Sandbox

A coding agent that uses WarpGrep to search code inside a [Vercel Sandbox](https://vercel.com/docs/sandbox). The repo is cloned into the sandbox, and WarpGrep executes ripgrep, sed, and find remotely via `remoteCommands`.

## Setup

```bash
npm install
```

## Run

```bash
VERCEL_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
```

Ask a specific question:

```bash
VERCEL_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does streaming work?"
```

## How it works

1. Creates a Vercel sandbox and clones `anthropics/anthropic-cookbook` into it
2. Installs ripgrep by downloading the static musl binary (Vercel Sandbox runs Amazon Linux 2023, which does not have apt-get)
3. Creates a WarpGrep tool with `remoteCommands` that execute inside the sandbox via `sandbox.runCommand()`
4. Runs a Claude agent loop — when Claude calls the tool, WarpGrep searches the sandboxed repo
5. Cleans up the sandbox when done

The agent loop runs up to 5 turns, giving Claude multiple chances to search and refine its understanding.
