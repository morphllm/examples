# Morph Examples

Runnable examples for every Morph product. The top-level folders match the
[docs](https://docs.morphllm.com) navigation, so if you found something in the docs, it lives
under the same name here.

| Folder | What it covers | Docs |
|---|---|---|
| [open-source-models](./open-source-models) | Open-weight models (Kimi K3, GLM-5.2, MiniMax M3, Qwen, DeepSeek, Gemma) served on Morph's own stack | [docs](https://docs.morphllm.com/sdk/components/fast-models) |
| [fast-apply](./fast-apply) | Merge an LLM's lazy edit snippet into a file at 10,500+ tok/s | [docs](https://docs.morphllm.com/api-reference/endpoint/apply) |
| [compact](./compact) | Query-conditioned context compaction | [docs](https://docs.morphllm.com/sdk/components/compact) |
| [warpgrep](./warpgrep) | Agentic code search — local repos, GitHub without cloning, and inside sandboxes | [docs](https://docs.morphllm.com/sdk/components/warp-grep) |
| [model-router](./model-router) | Classify a prompt's difficulty/ambiguity/domain and route it to the right model | [docs](https://docs.morphllm.com/sdk/components/router) |
| [reflexes](./reflexes) | Fast text classification | [docs](https://docs.morphllm.com/reflexes) |
| [example_applications](./example_applications) | Whole apps built on the above, not single-endpoint snippets | — |

## Setup

Every example needs a Morph API key from the
[dashboard](https://morphllm.com/dashboard/api-keys):

```bash
export MORPH_API_KEY=your-key
```

Each example directory is self-contained. TypeScript examples want Node 18+ and run with
`npm install` then `npm start`; Python examples want Python 3.10+ and
`pip install -r requirements.txt`. The local WarpGrep examples also need
[ripgrep](https://github.com/BurntSushi/ripgrep) on your PATH.

Some examples call a non-Morph model as well and say so in their README — those need
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_API_KEY`.

## Quick start

```bash
cd warpgrep/basic-search
npm install
MORPH_API_KEY=your-key npx tsx search.ts "Where is the request retry logic?" /path/to/repo
```

## Applications

| App | What it is |
|---|---|
| [pr-review-bot](./example_applications/pr-review-bot) | GitHub App that reviews pull requests, using WarpGrep to pull in the code the diff depends on. Also runs from the CLI against any PR URL. |
