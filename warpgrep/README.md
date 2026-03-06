# WarpGrep Examples

Code search subagent examples. Each example is self-contained — pick the one that matches your stack.

| Example | Language | What it shows |
|---------|----------|---------------|
| [basic-search](./basic-search) | TypeScript | Simplest possible search — query in, results out |
| [streaming](./streaming) | TypeScript | Stream local search progress in real-time |
| [search-node-modules](./search-node-modules) | TypeScript | Search inside dependencies |
| [github-search](./github-search) | TypeScript | Search a public GitHub repo without cloning |
| [github-streaming](./github-streaming) | TypeScript | Stream GitHub search progress in real-time |
| [anthropic-agent](./anthropic-agent) | TypeScript | Claude + WarpGrep in an agent loop |
| [openai-agent](./openai-agent) | TypeScript | GPT-4o + WarpGrep in an agent loop |
| [vercel-agent](./vercel-agent) | TypeScript | Vercel AI SDK — automatic tool loop |
| [gemini-agent](./gemini-agent) | TypeScript | Gemini + WarpGrep in an agent loop |
| [python-agent](./python-agent) | Python | Full protocol reference implementation (no SDK) |
| [e2b-sandbox](./e2b-sandbox) | TypeScript | WarpGrep in an E2B cloud sandbox |
| [modal-sandbox](./modal-sandbox) | TypeScript | WarpGrep in a Modal cloud sandbox |
| [daytona-sandbox](./daytona-sandbox) | TypeScript | WarpGrep in a Daytona sandbox |
| [vercel-sandbox](./vercel-sandbox) | TypeScript | WarpGrep in a Vercel Sandbox |
| [cloudflare-sandbox](./cloudflare-sandbox) | TypeScript | WarpGrep in a Cloudflare Worker sandbox |
| [docker-ssh](./docker-ssh) | TypeScript | WarpGrep over SSH to a Docker container |

## Prerequisites

All examples require a [Morph API key](https://morphllm.com/dashboard/api-keys) (`MORPH_API_KEY`).

Local search examples also need [ripgrep](https://github.com/BurntSushi/ripgrep) installed. GitHub search examples do not — WarpGrep indexes the repo remotely.

TypeScript examples need Node.js 18+. The Python example needs Python 3.10+.

## Quick test

Run any example against its own directory:

```bash
cd basic-search
npm install
MORPH_API_KEY=your-key npx tsx search.ts "What files are in this project?"
```
