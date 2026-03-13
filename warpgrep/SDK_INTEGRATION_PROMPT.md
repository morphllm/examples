# WarpGrep SDK Integration Prompt

> **What is this?** A universal prompt you can give to any AI coding assistant (Claude, GPT, Cursor, etc.) to integrate WarpGrep into an existing TypeScript/Node.js agent codebase using the `@morphllm/morphsdk` package. Copy everything below the line and paste it into your assistant.
>
> **Python?** If your agent is written in Python, use the [Python Integration Prompt](./python-agent/INTEGRATION_PROMPT.md) instead — it uses the raw HTTP protocol (there is no Python SDK).

---

## Prompt

You are integrating WarpGrep — a code search sub-agent — into an existing TypeScript/Node.js agent codebase. WarpGrep is NOT a regex tool. It is a separate LLM that takes a **natural-language query**, searches a codebase using ripgrep/file-reads internally, and returns relevant code snippets. It runs in its own context window so it doesn't pollute the parent agent's context.

The integration uses the official `@morphllm/morphsdk` package, which handles the entire WarpGrep agent loop for you. Under the hood, the SDK calls the `morph-warp-grep-v2` model, parses XML tool calls from its responses, and executes ripgrep/file-read/directory-list operations locally in a multi-turn loop (typically 2–4 turns, under 6 seconds on most codebases) before returning aggregated results. You don't need to manage any of this — you just call `createWarpGrepTool()` and wire it into your agent.

**Important naming convention:** The SDK names the tool `codebase_search`. This avoids models seeing "grep" and assuming they need to pass regex patterns. The input is plain English like "Find the authentication middleware" or "How does the payment flow work?".

Follow these steps exactly. Complete each step before moving to the next.

---

### Step 1: Identify the core agent harness

Search the codebase for the main agent loop. Look for:
- A conversation loop that sends messages to an LLM and processes responses
- Tool/function calling handling (parsing tool calls from model responses, executing them, sending results back)
- The main entry point file (usually `agent.ts`, `index.ts`, `main.ts`, or similar)

Identify:
- What file contains the agent loop?
- What LLM provider SDK does it use? This determines which adapter to import:

| Provider SDK | Import path |
|---|---|
| `@anthropic-ai/sdk` | `@morphllm/morphsdk/tools/warp-grep/anthropic` |
| `openai` | `@morphllm/morphsdk/tools/warp-grep/openai` |
| `@google/generative-ai` | `@morphllm/morphsdk/tools/warp-grep/gemini` |
| Vercel AI SDK (`ai`) | `@morphllm/morphsdk/tools/warp-grep/vercel` |

**Read only the provider section that matches the detected SDK.** Skip the other provider examples in Step 5.

### Step 2: Find how the agent registers and executes tools

Search for how tools are defined and added. Look for:
- Tool definitions / function declarations (JSON schemas, Zod schemas, tool objects)
- Tool execution handlers (where tool call results are processed and sent back to the model)
- The pattern used: Is it Anthropic `tools: [...]`? OpenAI `tools: [...]`? Vercel AI SDK `tools: { name: tool }`? Gemini `functionDeclarations`?

### Step 3: Check for existing tools

Search the codebase for any tools already registered with the agent. This tells you the exact pattern to follow. Look for:
- Tool name strings, tool description constants
- Tool schema definitions (JSON Schema objects, Zod schemas, function declarations)
- Tool execution dispatch (if/else chains, switch statements, or automatic tool execution)

### Step 4: Install the SDK

Add the Morph SDK to the project:

```bash
npm install @morphllm/morphsdk
```

The SDK bundles `@vscode/ripgrep` for local searches — no separate ripgrep install needed. For GitHub search, no additional dependencies are needed.

**Requirements:**
- Node.js 18+
- `MORPH_API_KEY` environment variable (get from https://morphllm.com/dashboard/api-keys)
- If using the Vercel AI SDK adapter: `ai` package version 5.0.0 or later

### Step 5: Create the WarpGrep tool and wire it into your agent

The SDK provides a `createWarpGrepTool()` function for each provider. It returns a tool object that is already formatted for that provider's API — you pass it directly into the `tools` array.

The tool object has three parts:
1. **The tool definition itself** — pass it to your LLM provider's `tools` parameter
2. **`.execute(input)`** — runs the WarpGrep search; call this when the model invokes the tool
3. **`.formatResult(result)`** — formats the search results into a string to send back to the model

**Tool schema:** The tool accepts a single `search_term` string parameter. This is the natural-language query that WarpGrep uses to search the codebase. The model fills this in automatically based on its conversation context.

**Result shape:** `.execute()` returns `{ success: boolean, contexts?: Array<{ file: string, content: string }>, error?: string }`. Always check `result.success` before using results.

Below are complete examples for each provider. **Use only the one that matches your agent's stack.**

---

#### Anthropic (`@anthropic-ai/sdk`)

```typescript
import Anthropic from "@anthropic-ai/sdk";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

const anthropic = new Anthropic();
const grepTool = createWarpGrepTool({ repoRoot: "." });

const messages: Anthropic.MessageParam[] = [
  { role: "user", content: "How does the auth middleware work?" },
];

for (let turn = 0; turn < 5; turn++) {
  const response = await anthropic.messages.create({
    model: "claude-sonnet-4-5-20250929",
    max_tokens: 4096,
    system:
      "You are a code assistant. Use the codebase_search tool to find relevant code before answering questions. Be concise.",
    tools: [grepTool],  // ← pass the tool directly
    messages,
  });

  messages.push({ role: "assistant", content: response.content });

  if (response.stop_reason === "end_turn") {
    for (const block of response.content) {
      if (block.type === "text") console.log(block.text);
    }
    break;
  }

  // Execute tool calls
  const toolResults: Anthropic.ToolResultBlockParam[] = [];
  for (const block of response.content) {
    if (block.type === "tool_use") {
      console.log(`[WarpGrep] Searching: "${(block.input as { search_term: string }).search_term}"`);
      const result = await grepTool.execute(block.input);  // ← run the search
      if (!result.success) {
        console.error(`[WarpGrep] Search failed: ${result.error}`);
      }
      toolResults.push({
        type: "tool_result",
        tool_use_id: block.id,
        content: grepTool.formatResult(result),  // ← format for the model (handles errors too)
      });
    }
  }

  if (toolResults.length > 0) {
    messages.push({ role: "user", content: toolResults });
  }
}
```

---

#### OpenAI (`openai`)

```typescript
import OpenAI from "openai";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/openai";

const openai = new OpenAI();
const grepTool = createWarpGrepTool({ repoRoot: "." });

const messages: OpenAI.ChatCompletionMessageParam[] = [
  {
    role: "system",
    content:
      "You are a code assistant. Use the codebase_search tool to find relevant code before answering questions. Be concise.",
  },
  { role: "user", content: "How does the auth middleware work?" },
];

for (let turn = 0; turn < 5; turn++) {
  const response = await openai.chat.completions.create({
    model: "gpt-4o",
    tools: [grepTool],  // ← pass the tool directly
    messages,
  });

  const choice = response.choices[0];

  if (choice.finish_reason === "stop") {
    console.log(choice.message.content);
    break;
  }

  messages.push(choice.message);

  if (choice.message.tool_calls) {
    for (const toolCall of choice.message.tool_calls) {
      const input = JSON.parse(toolCall.function.arguments);
      console.log(`[WarpGrep] Searching: "${input.search_term}"`);
      const result = await grepTool.execute(input);  // ← run the search
      if (!result.success) {
        console.error(`[WarpGrep] Search failed: ${result.error}`);
      }
      messages.push({
        role: "tool",
        tool_call_id: toolCall.id,
        content: grepTool.formatResult(result),  // ← format for the model
      });
    }
  }
}
```

---

#### Vercel AI SDK (`ai`)

The Vercel AI SDK handles the tool loop automatically — you just pass the tool and set `maxSteps`. Requires `ai` version 5.0.0+.

```typescript
import { generateText } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/vercel";

const grepTool = createWarpGrepTool({ repoRoot: "." });

const { text, steps } = await generateText({
  model: anthropic("claude-sonnet-4-5-20250929"),
  tools: { codebase_search: grepTool },  // ← pass as named tool
  maxSteps: 5,  // ← SDK handles execution automatically
  system:
    "You are a code assistant. Use the codebase_search tool to search the codebase before answering. Be concise.",
  prompt: "How does the auth middleware work?",
});

// Show what searches happened
for (const step of steps) {
  for (const call of step.toolCalls) {
    console.log(`[WarpGrep] Searched: "${call.args.search_term}"`);
  }
}

console.log(text);
```

---

#### Google Gemini (`@google/generative-ai`)

```typescript
import { GoogleGenerativeAI } from "@google/generative-ai";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/gemini";

const genAI = new GoogleGenerativeAI(process.env.GOOGLE_API_KEY!);
const grepTool = createWarpGrepTool({ repoRoot: "." });

const model = genAI.getGenerativeModel({
  model: "gemini-2.0-flash",
  tools: [{ functionDeclarations: [grepTool] }],  // ← Gemini uses functionDeclarations
  systemInstruction:
    "You are a code assistant. Use the codebase_search tool to search the codebase before answering. Be concise.",
});

const chat = model.startChat();
let response = await chat.sendMessage("How does the auth middleware work?");

for (let turn = 0; turn < 5; turn++) {
  const calls = response.response.functionCalls();
  if (!calls?.length) break;

  const results = [];
  for (const call of calls) {
    console.log(`[WarpGrep] Searching: "${(call.args as { search_term: string }).search_term}"`);
    const result = await grepTool.execute(call.args);  // ← run the search
    if (!result.success) {
      console.error(`[WarpGrep] Search failed: ${result.error}`);
    }
    results.push({
      functionResponse: {
        name: call.name,
        response: { result: grepTool.formatResult(result) },  // ← format for the model
      },
    });
  }

  response = await chat.sendMessage(results);
}

console.log(response.response.text());
```

---

### Step 6: Add system prompt guidance

Append the following to the agent's existing system prompt so the parent model knows when to use the tool. **Do not remove or rewrite existing system prompt content — only append.**

```
## codebase_search — when to use

USE codebase_search when you need to:
- Explore unfamiliar parts of the codebase
- Find implementations across multiple files (e.g., "Find the auth middleware", "Where is the payment flow?")
- Understand how a feature or system works before making changes
- Locate code by description rather than by exact name
- Find related code, callers, or dependencies of a function/class

DO NOT use codebase_search when:
- You already know the exact file and line (just read the file directly)
- You need a simple string/regex match on a known pattern (use grep directly)
- You're searching for a single known symbol name (use grep directly)

Best practice: Use codebase_search at the START of a task to orient yourself, then use direct file reads/grep for targeted follow-ups.
```

### Step 7: Advanced features (optional — only implement if explicitly requested)

These are SDK-specific features beyond basic integration. Skip unless needed.

#### Streaming search results

Show search progress in real-time instead of waiting for the full result. Useful for UIs that want to display WarpGrep's internal steps.

```typescript
import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const stream = morph.warpGrep.execute({
  query: "Find where errors are handled",
  repoRoot: ".",
  streamSteps: true,  // ← enables streaming
});

let result;
for (;;) {
  const { value, done } = await stream.next();
  if (done) {
    result = value;
    break;
  }

  // Each yield is one WarpGrep turn (typically 2-4 turns total)
  console.log(`Turn ${value.turn}:`);
  for (const call of value.toolCalls) {
    const args = Object.entries(call.arguments)
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
      .join(", ");
    console.log(`  ${call.name}(${args})`);
  }
}

if (!result.success) {
  console.error("Search failed:", result.error);
} else {
  for (const ctx of result.contexts ?? []) {
    console.log(`--- ${ctx.file} ---`);
    console.log(ctx.content);
  }
}
```

#### GitHub search (no local clone needed)

Search any public GitHub repository. WarpGrep indexes it on Morph's servers — no local ripgrep required. Streaming works here too — just add `streamSteps: true`.

```typescript
import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const result = await morph.warpGrep.searchGitHub({
  query: "How does the App Router handle parallel routes?",
  github: "vercel/next.js",
});

if (!result.success) {
  console.error("Search failed:", result.error);
} else {
  for (const ctx of result.contexts ?? []) {
    console.log(`--- ${ctx.file} ---`);
    console.log(ctx.content);
  }
}
```

#### Searching inside node_modules

WarpGrep excludes `node_modules`, `.git`, `dist`, `__pycache__`, and ~30 other patterns by default. To search inside dependencies:

```typescript
const result = await morph.warpGrep.execute({
  query: "How does the MorphClient initialize?",
  repoRoot: ".",
  excludes: [],  // ← clears all default excludes
});
```

#### Sandbox/remote execution (`remoteCommands`)

If your agent runs code in a sandbox (E2B, Vercel Sandbox, Cloudflare Workers, Modal, Daytona, Docker/SSH), WarpGrep can execute its tools remotely instead of on the local filesystem.

**Prerequisites:** ripgrep must be installed inside the sandbox. Example: `apt-get install -y ripgrep` or download the static binary.

```typescript
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

const grepTool = createWarpGrepTool({
  repoRoot: "/home/user/repo",  // path inside the sandbox
  remoteCommands: {
    grep: async (pattern, path, glob) => {
      const globArg = glob ? ` --glob '${glob}'` : "";
      const result = await sandbox.commands.run(
        `rg --no-heading --line-number -C 1 '${pattern}' '${path}'${globArg}`
      );
      return result.stdout || "";
    },
    read: async (path, start, end) => {
      const result = await sandbox.commands.run(
        `sed -n '${start},${end}p' '${path}'`
      );
      return result.stdout || "";
    },
    listDir: async (path, maxDepth) => {
      const result = await sandbox.commands.run(
        `find '${path}' -maxdepth ${maxDepth} -not -path '*/node_modules/*' -not -path '*/.git/*'`
      );
      return result.stdout || "";
    },
  },
});

// Use grepTool exactly the same way as local — the SDK routes calls through remoteCommands
```

**Important:** Always wrap sandbox usage in `try/finally` to ensure cleanup:
```typescript
const sandbox = await Sandbox.create();
try {
  // ... clone repo, create tool, run agent ...
} finally {
  await sandbox.kill();
}
```

The `remoteCommands` interface is the same for all providers — only the import path changes. Each sandbox provider has a different command execution API (`sandbox.commands.run`, `sandbox.process.executeCommand`, `ssh.execCommand`, etc.) — see the [sandbox examples](https://github.com/morphllm/examples/tree/main/warpgrep) for provider-specific code.

### Step 8: Test the integration

Run these tests in order. Each one verifies a different layer.

**Test A — Verify installation (no API key needed):**
```typescript
// Save as test-install.ts, run with: npx tsx test-install.ts
import { MorphClient } from "@morphllm/morphsdk";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";
// ↑ Change the import path to match your provider from Step 1

console.log("MorphClient:", typeof MorphClient === "function" ? "OK" : "FAILED");
console.log("createWarpGrepTool:", typeof createWarpGrepTool === "function" ? "OK" : "FAILED");

const tool = createWarpGrepTool({ repoRoot: "." });
console.log("Tool created:", tool ? "OK" : "FAILED");
console.log("Tool name:", tool.name);
console.log("Has execute:", typeof tool.execute === "function");
console.log("Has formatResult:", typeof tool.formatResult === "function");
// Expected: all OK, name="codebase_search", execute and formatResult are functions
// If imports fail: check that `npm install @morphllm/morphsdk` succeeded and your bundler supports package exports
```

**Test B — API key and basic search:**
```typescript
// Save as test-search.ts, run with: MORPH_API_KEY=your-key npx tsx test-search.ts
import { MorphClient } from "@morphllm/morphsdk";

if (!process.env.MORPH_API_KEY) {
  console.error("Error: set MORPH_API_KEY environment variable");
  console.error("Get your key from: https://morphllm.com/dashboard/api-keys");
  process.exit(1);
}

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const result = await morph.warpGrep.execute({
  query: "Find the main entry point of this project",
  repoRoot: ".",
});

if (!result.success) {
  console.error("Search FAILED:", result.error);
  // Common causes: invalid API key, network error, no files in repoRoot
  process.exit(1);
}

console.log(`Search OK — found ${result.contexts?.length ?? 0} files:`);
for (const ctx of result.contexts ?? []) {
  console.log(`  ${ctx.file} (${ctx.content.length} chars)`);
}
// Expected: success=true, at least 1 file found (if searching a non-empty repo)
```

**Test C — Tool execute + formatResult (simulates what happens in the agent loop):**
```typescript
// Save as test-tool.ts, run with: MORPH_API_KEY=your-key npx tsx test-tool.ts
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";
// ↑ Change the import path to match your provider

const grepTool = createWarpGrepTool({ repoRoot: "." });

// This simulates the model calling the tool with a search_term
const result = await grepTool.execute({ search_term: "Find the main entry point of this project" });

console.log(`Success: ${result.success}`);
if (!result.success) {
  console.error(`Error: ${result.error}`);
  process.exit(1);
}

console.log(`Files found: ${result.contexts?.length ?? 0}`);
for (const ctx of result.contexts ?? []) {
  console.log(`  ${ctx.file} (${ctx.content.length} chars)`);
}

// Test formatResult — this is what gets sent back to the parent model
const formatted = grepTool.formatResult(result);
console.log(`\nFormatted result (${formatted.length} chars):`);
console.log(formatted.slice(0, 300));
if (formatted.length > 300) console.log("  ...(truncated)");
// Expected: success=true, at least 1 file, formatted string contains file paths and code snippets
```

**Test D — Full agent integration:**

Send a message to your agent that requires using the tool, e.g.:
> "Use codebase_search to find how errors are handled in this project, then summarize what you found."

Verify the agent:
1. Calls the `codebase_search` tool (not grep or file read)
2. Passes a natural-language `search_term` (not a regex pattern)
3. Receives results and summarizes them in its response

**Troubleshooting:**
- If imports fail → check `npm ls @morphllm/morphsdk` and ensure your bundler supports Node.js package `exports`
- If API returns 401 → verify `MORPH_API_KEY` is set and valid
- If search returns no results → ensure `repoRoot` points to a directory with source files (resolved relative to the process working directory, not the agent's concept of "current directory")
- If the parent model passes regex instead of English → make sure the system prompt from Step 6 is included

---

### Reference Documentation

- **SDK Package:** `npm install @morphllm/morphsdk` ([npm](https://www.npmjs.com/package/@morphllm/morphsdk))
- **WarpGrep Docs:** https://docs.morphllm.com/sdk/components/warp-grep/index
- **Direct API Protocol (Python/any language):** https://docs.morphllm.com/sdk/components/warp-grep/direct
- **Examples (all providers + sandboxes):** https://github.com/morphllm/examples/tree/main/warpgrep
- **Python Integration Prompt:** https://github.com/morphllm/examples/blob/main/warpgrep/python-agent/INTEGRATION_PROMPT.md
- **Pricing:** $0.80 / 1M tokens input, $0.80 / 1M tokens output
- **API Keys:** https://morphllm.com/dashboard/api-keys
