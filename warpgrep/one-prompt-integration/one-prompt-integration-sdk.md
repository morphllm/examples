# codebase_search SDK Integration Prompt

> **What is this?** A prompt you can give to any AI coding assistant (Claude, GPT, Cursor, etc.) to integrate `codebase_search` into an existing TypeScript/Node.js agent codebase using the `@morphllm/morphsdk` package. Copy everything below the line and paste it into your assistant.
>
> **Python?** If your agent is written in Python, use the [Python Integration Prompt](./python-agent/INTEGRATION_PROMPT.md) instead — it uses the raw HTTP protocol (there is no Python SDK).

---

## Prompt

You are integrating `codebase_search` — a code search tool — into an existing TypeScript/Node.js agent codebase.

**What `codebase_search` is:** A separate LLM sub-agent (powered by the `@morphllm/morphsdk` package) that takes a **natural-language search string**, searches a codebase using ripgrep, read, ls, find, glob tools internally, and returns relevant code snippets. It runs in its own context window so it doesn't pollute the parent agent's context. Under the hood, the SDK calls the `morph-warp-grep-v2-1` model and executes ripgrep/file-read/directory-list operations in a multi-turn loop (typically 2-6 turns, under 6 seconds) before returning aggregated results. This is an agentic search tool. Not semantic search, not keyworkd search, not regex search. This tool is intelligent and is capable of reasoning.

**What `codebase_search` is NOT:**
- It is **not** regex search. Do not pass grep patterns to it.
- It is **not** semantic/vector search. It does not use embeddings.
- It is **not** called "WarpGrep", "grep", or "search" from the model's perspective. The tool name visible to the parent model must be `codebase_search`. Models see the word "grep" and assume they need to pass regex patterns. They don't — the input is plain English like "Find the authentication middleware" or "How does the payment flow work?".

**How it works at a high level:**
```
Parent agent receives user question
  → Parent calls codebase_search("How does auth work?")
    → SDK spins up a sub-agent (morph-warp-grep-v2)
    → Sub-agent internally runs grep/read/listDir commands (2-4 turns)
    → Sub-agent returns aggregated code snippets
  → Parent receives { file: "src/auth.ts", content: "..." }
  → Parent uses the code context to answer the user
```

Follow these steps exactly. **At the end of each step, write down what you found and verify it before moving on.** Do not skip ahead.

---

### Step 1: Map the agent architecture

Search the codebase to understand how the agent is structured. You need to answer these questions — write down the answers explicitly before proceeding:

1. **Where is the core agent harness?** Find the main agent loop — the file that sends messages to an LLM and processes responses. Usually `agent.ts`, `index.ts`, `main.ts`, or similar.
2. **Where are tools created and registered?** Find where tool definitions live and how they're wired into the agent. Look for tool schemas, function declarations, tool execution handlers.
3. **What LLM provider SDK does the agent use?** This determines which adapter to import:

| Provider SDK | Import path |
|---|---|
| `@anthropic-ai/sdk` | `@morphllm/morphsdk/tools/warp-grep/anthropic` |
| `openai` | `@morphllm/morphsdk/tools/warp-grep/openai` |
| `@google/generative-ai` | `@morphllm/morphsdk/tools/warp-grep/gemini` |
| Vercel AI SDK (`ai`) | `@morphllm/morphsdk/tools/warp-grep/vercel` |

4. **What tools does the agent already have?** Look at their schemas and execution patterns — you'll follow the same pattern for `codebase_search`.
5. **How does the agent pass environment variables / secrets?** You'll need `MORPH_API_KEY`.

**Checkpoint:** Before continuing, write down:
- File path of the agent loop: `___`
- File path(s) where tools are defined: `___`
- Provider SDK: `___`
- Existing tool names: `___`
- How env vars are configured: `___`

Read only the provider section that matches the detected SDK in Step 5 below. Skip the others.

---

### Step 2: Install the SDK

```bash
npm install @morphllm/morphsdk
```

The SDK bundles `@vscode/ripgrep` for local searches — no separate ripgrep install needed.

**Requirements:**
- Node.js 18+
- `MORPH_API_KEY` environment variable (get from https://morphllm.com/dashboard/api-keys)
- If using the Vercel AI SDK adapter: `ai` package version 5.0.0 or later

**Checkpoint — verify the install:**
```bash
node -e "const { MorphClient } = require('@morphllm/morphsdk'); console.log('SDK:', typeof MorphClient === 'function' ? 'OK' : 'FAIL')"
```
If this fails, check that the install succeeded and your bundler supports Node.js package `exports`.

---

### Step 3: Verify that local commands work

Before wiring anything into the agent, verify that the SDK's underlying commands — `grep_search`, `read`, `glob`, and `list_directory` — work on your filesystem. These are what `codebase_search` uses internally to search the codebase.

**How providers work:** By default, the SDK executes these commands **locally** — it shells out to ripgrep, reads files with `fs`, and lists directories with `find`. This works when the SDK and the cloned repo are on the same machine. If the code lives in a **remote sandbox** (e.g., E2B, Modal, Vercel Sandbox, Docker), you override these with `remoteCommands` — see Step 7.

Run this test to verify local execution works:

```typescript
// Save as test-local-commands.ts, run with: npx tsx test-local-commands.ts
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";
// ↑ Change the import path to match your provider from Step 1

const tool = createWarpGrepTool({ repoRoot: "." });

// Verify the tool was created and has the right shape
console.log("Tool name:", tool.name);                                    // should be "codebase_search"
console.log("Has execute:", typeof tool.execute === "function");          // true
console.log("Has formatResult:", typeof tool.formatResult === "function"); // true

// Run a basic search to verify ripgrep + file reads work locally
const result = await tool.execute({ search_term: "Find the main entry point of this project" });

console.log("\nSearch result:");
console.log("  success:", result.success);
console.log("  files found:", result.contexts?.length ?? 0);
if (!result.success) {
  console.error("  error:", result.error);
  console.error("\nDiagnosis:");
  console.error("  - Is MORPH_API_KEY set?", !!process.env.MORPH_API_KEY);
  console.error("  - Is repoRoot '.' a directory with source files?");
  process.exit(1);
}

// Verify the formatted output looks right — it should contain file paths and code
const formatted = tool.formatResult(result);
console.log("\nFormatted output preview:");
console.log(formatted.slice(0, 500));
if (formatted.length > 500) console.log("  ...(truncated)");

// Check: does the output contain file paths? Code content?
const hasFilePaths = result.contexts?.some(c => c.file.includes("/") || c.file.includes("."));
const hasContent = result.contexts?.some(c => c.content.length > 0);
console.log("\nContent checks:");
console.log("  Contains file paths:", hasFilePaths ? "YES" : "NO — something is wrong");
console.log("  Contains code content:", hasContent ? "YES" : "NO — something is wrong");
```

**Run it:** `MORPH_API_KEY=your-key npx tsx test-local-commands.ts`

**Checkpoint — verify all of these pass:**
- [ ] Tool name is `codebase_search`
- [ ] `execute()` and `formatResult()` are functions
- [ ] Search returns `success: true`
- [ ] At least 1 file found
- [ ] Formatted output contains file paths and actual code content
- [ ] The returned code looks like real file content (not summaries or hallucinations)

If any check fails, stop and debug before continuing. Common issues:
- `MORPH_API_KEY` not set or invalid → 401 error
- `repoRoot` points to empty directory → no results
- Network blocked → connection error (the SDK calls `api.morphllm.com`)

---

### Step 4: Create the `codebase_search` tool and wire it into the agent

The SDK provides a `createWarpGrepTool()` function for each provider. It returns a tool object already formatted for that provider's API.

The tool object has three parts:
1. **The tool definition itself** — pass it to your LLM provider's `tools` parameter
2. **`.execute(input)`** — runs the `codebase_search` sub-agent; call this when the model invokes the tool
3. **`.formatResult(result)`** — formats search results into a string to send back to the model

**Tool schema:** The tool accepts a single `search_term` string parameter — the natural-language query. The model fills this in automatically based on its conversation context.

**Result shape:** `.execute()` returns `{ success: boolean, contexts?: Array<{ file: string, content: string }>, error?: string }`. Always check `result.success` before using results.

Below are complete examples for each provider. **Use only the one that matches your agent's stack from Step 1.**

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
      console.log(`[codebase_search] Searching: "${(block.input as { search_term: string }).search_term}"`);
      const result = await grepTool.execute(block.input);  // ← run the search
      if (!result.success) {
        console.error(`[codebase_search] Search failed: ${result.error}`);
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
      console.log(`[codebase_search] Searching: "${input.search_term}"`);
      const result = await grepTool.execute(input);  // ← run the search
      if (!result.success) {
        console.error(`[codebase_search] Search failed: ${result.error}`);
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
    console.log(`[codebase_search] Searched: "${call.args.search_term}"`);
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
    console.log(`[codebase_search] Searching: "${(call.args as { search_term: string }).search_term}"`);
    const result = await grepTool.execute(call.args);  // ← run the search
    if (!result.success) {
      console.error(`[codebase_search] Search failed: ${result.error}`);
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

### Step 5: Add system prompt guidance

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

IMPORTANT: codebase_search takes plain English, not regex. Write queries like you'd describe what you're looking for to another engineer.

Best practice: Use codebase_search at the START of a task to orient yourself, then use direct file reads/grep for targeted follow-ups.
```

---

### Step 6: Test incrementally

Do not skip layers. Each test verifies a different part of the stack. If a layer fails, fix it before moving on — problems compound.

#### Layer 1 — SDK loads and tool has the right shape

Verify that the package installed correctly and the tool object is well-formed.

```typescript
// Save as test-1-install.ts, run with: npx tsx test-1-install.ts
import { MorphClient } from "@morphllm/morphsdk";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";
// ↑ Change the import path to match your provider from Step 1

console.log("MorphClient:", typeof MorphClient === "function" ? "OK" : "FAIL");
console.log("createWarpGrepTool:", typeof createWarpGrepTool === "function" ? "OK" : "FAIL");

const tool = createWarpGrepTool({ repoRoot: "." });
console.log("Tool created:", tool ? "OK" : "FAIL");
console.log("Tool name:", tool.name);  // Must be "codebase_search"
console.log("Has execute:", typeof tool.execute === "function");
console.log("Has formatResult:", typeof tool.formatResult === "function");
```

**Expected:** All OK. Tool name is `codebase_search`. If imports fail, check that `npm install` succeeded and your bundler supports package `exports`.

#### Layer 2 — API key, network, and basic search work

Verify the SDK can reach the API and execute a search against your repo.

```typescript
// Save as test-2-search.ts, run with: MORPH_API_KEY=your-key npx tsx test-2-search.ts
import { MorphClient } from "@morphllm/morphsdk";

if (!process.env.MORPH_API_KEY) {
  console.error("Set MORPH_API_KEY — get one from https://morphllm.com/dashboard/api-keys");
  process.exit(1);
}

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const result = await morph.warpGrep.execute({
  query: "Find the main entry point of this project",
  repoRoot: ".",
});

console.log("success:", result.success);
console.log("files found:", result.contexts?.length ?? 0);
if (!result.success) {
  console.error("error:", result.error);
  process.exit(1);
}

for (const ctx of result.contexts ?? []) {
  console.log(`  ${ctx.file} (${ctx.content.length} chars)`);
}
```

**Expected:** `success: true`, at least 1 file. If 401 → bad API key. If network error → `api.morphllm.com` is blocked.

#### Layer 3 — Tool execute + formatResult work correctly

Simulate what happens when the parent model calls `codebase_search`. Verify the returned context looks like real file content.

```typescript
// Save as test-3-tool.ts, run with: MORPH_API_KEY=your-key npx tsx test-3-tool.ts
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";
// ↑ Change the import path to match your provider

const tool = createWarpGrepTool({ repoRoot: "." });

// Simulate a tool call from the parent model
const result = await tool.execute({ search_term: "Find the main entry point of this project" });

console.log("success:", result.success);
console.log("files found:", result.contexts?.length ?? 0);

// Verify the formatted output contains real file content
const formatted = tool.formatResult(result);
console.log("\nFormatted output preview:");
console.log(formatted.slice(0, 500));
if (formatted.length > 500) console.log("  ...(truncated)");

// Sanity checks on the returned context
const hasFilePaths = result.contexts?.some(c => c.file.includes("/") || c.file.includes("."));
const hasContent = result.contexts?.some(c => c.content.length > 20);
console.log("\nSanity checks:");
console.log("  Has file paths:", hasFilePaths ? "PASS" : "FAIL");
console.log("  Has real content (>20 chars):", hasContent ? "PASS" : "FAIL");
```

**Expected:** File paths point to real files in the repo. Content is actual code, not summaries.

#### Layer 4 — Mock agent end-to-end (no LLM call)

Verify the full tool round-trip works: tool definition → execute → format → message. This catches wiring issues before you spend LLM tokens.

```typescript
// Save as test-4-mock-agent.ts, run with: MORPH_API_KEY=your-key npx tsx test-4-mock-agent.ts
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";
// ↑ Change the import path to match your provider

const tool = createWarpGrepTool({ repoRoot: "." });

// 1. Verify the tool definition is valid JSON schema
const toolDef = JSON.stringify(tool);
console.log("Tool definition is valid JSON:", !!JSON.parse(toolDef) ? "PASS" : "FAIL");

// 2. Simulate a tool call from the model
const fakeToolCall = { search_term: "Where are database migrations defined?" };
console.log("\nSimulated tool call:", JSON.stringify(fakeToolCall));

// 3. Execute the tool (this is the real codebase_search call)
const result = await tool.execute(fakeToolCall);
console.log("Execute returned success:", result.success);

// 4. Format the result (this is what goes back to the model as tool_result)
const formatted = tool.formatResult(result);
console.log("Formatted result length:", formatted.length, "chars");
console.log("Formatted result is non-empty string:", formatted.length > 0 ? "PASS" : "FAIL");

// 5. Verify the formatted result could be included in a message
const asMessage = {
  role: "user" as const,
  content: [{ type: "tool_result" as const, tool_use_id: "fake-id", content: formatted }],
};
console.log("Can construct tool_result message:", !!asMessage ? "PASS" : "FAIL");

console.log("\nFull round-trip: tool_call → execute → formatResult → tool_result message: PASS");
```

**Expected:** All PASS. If anything fails here, the agent integration will also fail — fix it first.

#### Layer 5 — Real agent end-to-end

Now test with the actual agent. Send a message that requires code search:

> "Use codebase_search to find how errors are handled in this project, then summarize what you found."

**Verify each of these explicitly:**

1. **Does the agent call `codebase_search`?** (not grep, cat, find, or any other tool)
2. **Is the query natural language?** The `search_term` should read like English ("Find error handling in the API layer"), not regex (`catch\s*\(.*Error`).
3. **Does the search succeed?** Check the logs for `success: true`. If `success: false`, check the error.
4. **Are any tool calls failing?** Look for error logs or empty results.
5. **Does the returned context contain real code?** The formatted result should have file paths and actual code snippets, not summaries or "no results found".
6. **Does the agent use the context in its response?** The final answer should reference specific files, functions, or code patterns from the search results.

**If the model passes regex instead of English:** The system prompt from Step 5 is missing or the tool name contains "grep". Double-check both.

---

### Step 7: Sandbox / remote execution (optional — only if code is in a remote sandbox)

**Skip this step if the SDK and the repo are on the same machine.** Local execution (the default) already works — Step 3 verified it.

If your agent runs code in a sandbox (E2B, Modal, Vercel Sandbox, Cloudflare Workers, Daytona, Docker/SSH), the SDK needs to execute its internal commands (`grep`, `read`, `listDir`) inside the sandbox instead of locally. You do this by passing `remoteCommands`.

**Prerequisites:** `ripgrep` must be installed inside the sandbox (`apt-get install -y ripgrep` or download the static binary).

```typescript
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

const grepTool = createWarpGrepTool({
  repoRoot: "/home/user/repo",  // path inside the sandbox
  remoteCommands: {
    grep: async (pattern, path, glob) => {
      // Execute ripgrep inside the sandbox
      const globArg = glob ? ` --glob '${glob}'` : "";
      const result = await sandbox.commands.run(
        `rg --no-heading --line-number -C 1 '${pattern}' '${path}'${globArg}`
      );
      return result.stdout || "";
    },
    read: async (path, start, end) => {
      // Read file lines inside the sandbox
      const result = await sandbox.commands.run(
        `sed -n '${start},${end}p' '${path}'`
      );
      return result.stdout || "";
    },
    listDir: async (path, maxDepth) => {
      // List directory inside the sandbox
      const result = await sandbox.commands.run(
        `find '${path}' -maxdepth ${maxDepth} -not -path '*/node_modules/*' -not -path '*/.git/*'`
      );
      return result.stdout || "";
    },
  },
});

// Use grepTool exactly the same way as local — the SDK routes calls through remoteCommands
```

Adapt `sandbox.commands.run(...)` to your sandbox's execution API (`sandbox.exec()`, `sandbox.process.executeCommand()`, `ssh.execCommand()`, etc.). The three functions just need to return stdout as a string.

**Always wrap sandbox usage in try/finally:**
```typescript
const sandbox = await Sandbox.create();
try {
  // ... clone repo, create tool, run agent ...
} finally {
  await sandbox.kill();
}
```

**Test remote commands independently before wiring them in.** Run each one manually inside the sandbox and verify the output looks right:

```typescript
// Test grep
const grepOut = await sandbox.commands.run("rg --no-heading --line-number -C 1 'import' 'src/'");
console.log("grep output:", grepOut.stdout?.slice(0, 200));  // Should show matching lines

// Test read
const readOut = await sandbox.commands.run("sed -n '1,10p' 'package.json'");
console.log("read output:", readOut.stdout?.slice(0, 200));  // Should show first 10 lines

// Test listDir
const listOut = await sandbox.commands.run("find 'src/' -maxdepth 2 -not -path '*/node_modules/*'");
console.log("listDir output:", listOut.stdout?.slice(0, 200));  // Should show file tree
```

The `remoteCommands` interface is the same for all providers — only the import path changes. See the [sandbox examples](https://github.com/morphllm/examples/tree/main/warpgrep) for provider-specific code.

---

### Step 8: Advanced features (optional — only implement if explicitly requested)

#### Streaming search results

Show search progress in real-time instead of waiting for the full result:

```typescript
import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const stream = morph.warpGrep.execute({
  query: "Find where errors are handled",
  repoRoot: ".",
  streamSteps: true,
});

let result;
for (;;) {
  const { value, done } = await stream.next();
  if (done) {
    result = value;
    break;
  }
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

Search any public GitHub repository without cloning it:

```typescript
import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const result = await morph.warpGrep.searchGitHub({
  query: "How does the App Router handle parallel routes?",
  github: "vercel/next.js",
});

if (result.success) {
  for (const ctx of result.contexts ?? []) {
    console.log(`--- ${ctx.file} ---\n${ctx.content}`);
  }
}
```

#### Searching inside node_modules

The SDK excludes `node_modules`, `.git`, `dist`, `__pycache__`, and ~30 other patterns by default. To search inside dependencies:

```typescript
const result = await morph.warpGrep.execute({
  query: "How does the MorphClient initialize?",
  repoRoot: ".",
  excludes: [],  // ← clears all default excludes
});
```

---

### Reference Documentation

- **SDK Package:** `npm install @morphllm/morphsdk` ([npm](https://www.npmjs.com/package/@morphllm/morphsdk))
- **Docs:** https://docs.morphllm.com/sdk/components/warp-grep/index
- **Direct API Protocol (Python/any language):** https://docs.morphllm.com/sdk/components/warp-grep/direct
- **Examples (all providers + sandboxes):** https://github.com/morphllm/examples/tree/main/warpgrep
- **Python Integration Prompt:** https://github.com/morphllm/examples/blob/main/warpgrep/python-agent/INTEGRATION_PROMPT.md
- **Pricing:** $0.80 / 1M tokens input, $0.80 / 1M tokens output
- **API Keys:** https://morphllm.com/dashboard/api-keys
