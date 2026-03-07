# WarpGrep + Modal Sandbox

A coding agent that searches code inside a [Modal](https://modal.com) cloud sandbox. The repo is cloned into a Modal container, and WarpGrep runs ripgrep remotely via `remoteCommands`.

## Setup

```bash
npm install
```

You need three sets of credentials:

- **Modal** — run `modal token new` or set `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`
- **Morph** — get a key at [morphllm.com/dashboard/api-keys](https://morphllm.com/dashboard/api-keys)
- **Anthropic** — get a key at [console.anthropic.com](https://console.anthropic.com)

## Run

```bash
MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
```

Ask a specific question:

```bash
MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does streaming work?"
```

## How it works

1. Creates a Modal sandbox with an image that has `ripgrep` and `git` installed
2. Clones `anthropics/anthropic-cookbook` into the sandbox
3. Creates a WarpGrep tool with `remoteCommands` that execute `rg`, `sed`, and `find` inside the sandbox
4. Runs a Claude agent loop — Claude calls WarpGrep, which searches the remote sandbox
5. Terminates the sandbox on exit
