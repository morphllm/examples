# WarpGrep Examples

Code search subagent examples. Each example is self-contained — pick the one that matches your stack.

| Example | Language | What it shows |
|---------|----------|---------------|
| [basic-search](./basic-search) | TypeScript | Simplest possible search — query in, results out |
| [streaming](./streaming) | TypeScript | Stream local search progress in real-time |
| [search-node-modules](./search-node-modules) | TypeScript | Search inside dependencies |
| [github-search](./github-search) | TypeScript | Search a public GitHub repo without cloning |
| [github-streaming](./github-streaming) | TypeScript | Stream GitHub search progress in real-time |
| [agents/anthropic](./agents/anthropic) | TypeScript | Claude + WarpGrep in an agent loop |
| [agents/openai](./agents/openai) | TypeScript | GPT-4o + WarpGrep in an agent loop |
| [agents/vercel](./agents/vercel) | TypeScript | Vercel AI SDK — automatic tool loop |
| [agents/gemini](./agents/gemini) | TypeScript | Gemini + WarpGrep in an agent loop |
| [agents/python](./agents/python) | Python | Full protocol reference implementation (no SDK) |
| [sandboxes/e2b](./sandboxes/e2b) | TypeScript | WarpGrep in an E2B cloud sandbox |
| [sandboxes/modal](./sandboxes/modal) | TypeScript | WarpGrep in a Modal cloud sandbox |
| [sandboxes/daytona](./sandboxes/daytona) | TypeScript | WarpGrep in a Daytona sandbox |
| [sandboxes/vercel](./sandboxes/vercel) | TypeScript | WarpGrep in a Vercel Sandbox |
| [sandboxes/cloudflare](./sandboxes/cloudflare) | TypeScript | WarpGrep in a Cloudflare Worker sandbox |
| [sandboxes/chroma](./sandboxes/chroma) | TypeScript | Custom backend — WarpGrep over Chroma Package Search |
| [sandboxes/docker-ssh](./sandboxes/docker-ssh) | TypeScript | WarpGrep over SSH to a Docker container |
| [integration-prompt](./integration-prompt) | — | Copy-paste prompts to add WarpGrep to an existing agent |

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
