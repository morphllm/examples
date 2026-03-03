# WarpGrep + Vercel Sandbox

Code search subagent running inside an isolated sandbox.

```
sub·a·gent  /ˈsʌbˌeɪdʒənt/  noun

  An individual agent with its own separate context window that
  returns context to the parent agent without exceeding the sum
  of the tokens it processed.

  "The subagent searched 47 files across 3 directories and returned
   two relevant code blocks — 200 tokens back from 12,000 processed."
```

## How it works

```
                         ┌─────────────────────────────────┐
                         │         Parent Agent             │
                         │         (Opus 4.6)               │
                         │                                  │
                         │  "Find where middleware is        │
                         │   registered in this repo"        │
                         │                                  │
                         └──────────┬───────────────────────┘
                                    │
                                    │  tool_use: warp_grep
                                    │  { query: "..." }
                                    │
                         ┌──────────▼───────────────────────┐
                         │         Subagent                  │
                         │         (WarpGrep)                │
                         │                                   │
                         │  Own context window. Runs a       │
                         │  multi-turn search loop:          │
                         │                                   │
                         │  Turn 1: list_directory src/      │
                         │  Turn 2: grep "middleware" *.ts   │
                         │  Turn 3: read src/hono-base.ts    │
                         │  Turn 4: finish → 2 files         │
                         │                                   │
                         │  Tokens processed: ~12,000        │
                         │  Tokens returned:  ~200           │
                         │                                   │
                         └──────────┬───────────────────────┘
                                    │
                                    │  tool_result: { contexts: [...] }
                                    │
                         ┌──────────▼───────────────────────┐
                         │         Parent Agent              │
                         │         (Opus 4.6)                │
                         │                                   │
                         │  Receives only the relevant code  │
                         │  blocks. Context window stays     │
                         │  clean. Continues its task.       │
                         └───────────────────────────────────┘
```

The parent agent never sees the 47 files WarpGrep scanned, the dead-end
grep results, or the directory listings. It gets back just the code that
matters. That's the point of a subagent: it burns tokens so the parent
doesn't have to.

## In this example

The subagent runs inside a **Vercel Sandbox** — an isolated cloud VM.
The parent agent's `tool_use` triggers:

1. **Sandbox creation** — a fresh Amazon Linux 2023 VM spins up
2. **Repo clone + ripgrep install** — sets up the search environment
3. **WarpGrep agentic loop** — multiple turns of grep/read/listDir
   executed inside the sandbox via `sandbox.runCommand()`
4. **Result extraction** — only the relevant file snippets come back

```
  Opus 4.6 (parent)
    │
    ├──→ WarpGrep (subagent, morph-warp-grep-v2)
    │      │
    │      ├── rg "middleware" src/ ──→  Vercel Sandbox (VM)
    │      ├── sed -n '156,168p' src/hono-base.ts ──→  Vercel Sandbox
    │      └── finish: 2 files
    │
    ◄── tool_result: src/hono-base.ts:156-168, src/compose.ts
```

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=sk-... ANTHROPIC_API_KEY=sk-... npx tsx index.ts \
  "https://github.com/honojs/hono.git" \
  "Find where middleware is registered"
```

## What you need

- `MORPH_API_KEY` — get one at [morphllm.com/dashboard](https://morphllm.com/dashboard)
- `ANTHROPIC_API_KEY` — for the parent agent (Claude)
- A Vercel account with Sandbox access (`vercel link` + `vercel env pull`)

## Docs

- [WarpGrep tool reference](https://docs.morphllm.com/sdk/components/warp-grep/tool) (see Vercel Sandbox tab)
- [WarpGrep overview](https://docs.morphllm.com/sdk/components/warp-grep/index)
- [Vercel Sandbox SDK](https://vercel.com/docs/sandbox)
