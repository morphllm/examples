# WarpGrep + Chroma Package Search

A coding agent that uses WarpGrep to search code inside [Chroma](https://www.trychroma.com/package-search)'s Package Search index. No sandbox VM needed — Chroma indexes 3,000+ public packages and exposes grep, file reading, and file listing over its API. WarpGrep routes all tool calls through Chroma via `remoteCommands`.

## Setup

```bash
npm install
```

Get a Chroma API key at [trychroma.com/package-search](https://www.trychroma.com/package-search).

## Run

```bash
CHROMA_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
```

Ask a specific question:

```bash
CHROMA_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does this package handle request validation?"
```

## How it works

1. Connects to Chroma Package Search and verifies the target package is indexed
2. Creates a WarpGrep tool with `remoteCommands` that route through Chroma's API:
   - **grep** → `package_search_grep` (regex search, ripgrep-style output)
   - **read** → `package_search_read_file` (file content by sha256 lookup)
   - **listDir** → `package_search_grep` with `files_with_matches` mode
3. Runs a Claude agent loop — when Claude calls the tool, WarpGrep searches the package via Chroma
4. No cleanup needed (no sandbox to tear down)

## Changing the package

Edit the `REGISTRY` and `PACKAGE` constants in `agent.ts`:

```typescript
const REGISTRY = "npm";       // "npm", "crates_io", "golang_proxy", "py_pi"
const PACKAGE = "zod";        // any package indexed by Chroma
```

Chroma indexes packages from npm, PyPI, crates.io, Go proxy, GitHub Releases, RubyGems, and Terraform Registry.
