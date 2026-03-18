# WarpGrep + Chroma Package Search

Use WarpGrep with any remote backend by implementing three functions: `grep`, `read`, and `listDir`. This example uses [Chroma Package Search](https://www.trychroma.com/package-search) as the backend (no sandbox needed).

## The pattern

```typescript
import Anthropic from "@anthropic-ai/sdk";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

// 1. Create the tool — override grep, read, listDir with your backend
const searchTool = createWarpGrepTool({
  repoRoot: "/repo",
  remoteCommands: {
    grep: async (pattern, path, glob?) => {
      // Return ripgrep-formatted output: "path:line:content\n"
      return myBackend.grep(pattern);
    },
    read: async (path, start, end) => {
      // Return raw file content (newline-separated lines)
      return myBackend.readFile(path, start, end);
    },
    listDir: async (path, maxDepth) => {
      // Return one file path per line
      return myBackend.listFiles(path);
    },
  },
});

// 2. Pass the tool to the Anthropic SDK
const anthropic = new Anthropic();
const response = await anthropic.messages.create({
  model: "claude-sonnet-4-5-20250929",
  tools: [searchTool],  // ← valid Anthropic tool definition
  messages,
});

// 3. Execute tool calls and feed results back to Claude
for (const block of response.content) {
  if (block.type === "tool_use") {
    const result = await searchTool.execute(block.input);
    const formatted = searchTool.formatResult(result);
    // ... return as tool_result message
  }
}
```

See [`agent.ts`](./agent.ts) for the full working example with Chroma as the backend.

## Setup

```bash
npm install
```

Get a Chroma API key at [trychroma.com/package-search](https://www.trychroma.com/package-search).

## Run

```bash
CHROMA_API_KEY=… MORPH_API_KEY=… ANTHROPIC_API_KEY=… npx tsx agent.ts
```

```bash
CHROMA_API_KEY=… MORPH_API_KEY=… ANTHROPIC_API_KEY=… npx tsx agent.ts "How does routing work?"
```

## Changing the package

Edit `REGISTRY` and `PACKAGE` in `agent.ts`:

```typescript
const REGISTRY = "npm";       // "npm", "crates_io", "golang_proxy", "py_pi"
const PACKAGE = "zod";        // any package indexed by Chroma
```
