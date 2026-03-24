# codebase_search SDK Integration Prompt

> **What is this?** A prompt you can give to any AI coding assistant (Claude, GPT, Cursor, etc.) to integrate `codebase_search` into an existing TypeScript/Node.js agent codebase using the `@morphllm/morphsdk` package. Copy everything below the line and paste it into your assistant.
>
> **Python / Go / Rust / other language?** If your agent is NOT written in TypeScript/Node.js, use the [Direct API Integration Prompt](./one-prompt-integration-api.md) instead — it uses the raw HTTP protocol and works with any language.

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
6. **Does the agent operate on local code, or a remote sandbox?** By default, the sdk will execute local ripgrep, read, find, ls, commands. But if the agent you're dealing with executes tool calls on a filesystem where these will not work, you might need to overwrite the default set of tools.

**Checkpoint:** Before continuing, write down:
- File path of the agent loop: `___`
- File path(s) where tools are defined: `___`
- Provider SDK: `___`
- Existing tool names: `___`
- How env vars are configured: `___`
- True or False, the default set of tools will work: `___`

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

Before you move on to the integration, think about codebase best practices. Where are tools commonly defined? If there is no existing pattern for tools, design an abstraction layer that improves the codebase (e.g., create a concept of a provider).

#### Step 4a: Identify the agent harness type

The agent you're integrating into uses one of three harness patterns. **This determines everything about how you wire in the SDK.** Look at what you found in Step 1 — specifically how tools are defined, registered, and invoked — and classify it:

**Type 1: Tool-Use Loop (direct LLM SDK call)**

The agent calls an LLM provider SDK directly (OpenAI, Anthropic, Google, Vercel AI) and has its own tool-calling loop. You'll see code like:
- `anthropic.messages.create({ tools: [...] })` or `openai.chat.completions.create({ tools: [...] })`
- A loop that checks `stop_reason` / `finish_reason` and processes `tool_use` / `tool_calls` blocks
- Tool definitions as JSON schemas or Zod objects passed to the LLM call

**How to detect:** Look for direct imports of `@anthropic-ai/sdk`, `openai`, `@google/generative-ai`, or `ai` (Vercel). The agent manages its own message history and tool execution loop.

→ **If this is your agent:** Proceed to **Step 4b** below. Use the provider-specific examples to wire `createWarpGrepTool()` directly into the existing tool array and execution handler.

---

**Type 2: Agent Framework with its own Tool abstraction**

The agent uses a framework that has its own tool interface — a class, decorator, or factory function that wraps tool definitions in a framework-specific way. You'll see code like:
- `class MyTool(BaseTool)` with a `_run()` method (crewAI, LangChain)
- `Tool.define("name", { parameters: z.object(...), execute: ... })` (custom frameworks)
- `@tool` decorators or `StructuredTool` subclasses
- `defineTool({ name, schema, handler })` patterns
- MCP `list_tools()` / `call_tool()` trait implementations

**How to detect:** Look for framework-specific base classes, decorators, or factory functions. The framework — not your code — manages the LLM loop and tool dispatch. Your tool definition conforms to the framework's interface, not a raw LLM provider schema.

→ **If this is your agent:** You cannot use the provider examples below as-is. The `createWarpGrepTool()` return value is shaped for raw LLM SDKs. Instead, proceed to **Step 4c** — you'll instantiate the SDK client directly and wrap it in the framework's tool interface.

---

**Type 3: CLI / Bash agent**

The agent operates by emitting shell commands, and tools are configured as executables with command signatures. You'll see code like:
- `tools:` config blocks with `signature: "toolname <arg>"` and `docstring: "..."` (SWE-agent)
- A bash execution harness that runs commands and captures stdout as the "observation"
- No programmatic tool interface — the model just generates shell commands

**How to detect:** Look for YAML/JSON tool configs with `signature` fields, or a harness that pipes model output through `bash -c`. The agent doesn't call tool functions — it generates commands as text.

→ **If this is your agent:** You cannot use the provider examples or framework wrappers. Proceed to **Step 4d** — you'll write a CLI script that wraps the SDK and register it as a shell command.

---

**Checkpoint:** Before continuing, write down:
- Harness type: `Tool-Use Loop` / `Agent Framework` / `CLI/Bash`
- Evidence (what you saw that told you): `___`

---

#### Step 4b: Tool-Use Loop integration (direct LLM SDK)

Use this section **only** if the agent calls an LLM provider SDK directly and manages its own tool loop. Use the example that matches your agent's provider from Step 1.

---

##### Anthropic (`@anthropic-ai/sdk`)

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

##### OpenAI (`openai`)

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

##### Vercel AI SDK (`ai`)

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

##### Google Gemini (`@google/generative-ai`)

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

#### Step 4c: Agent Framework integration (framework-specific tool wrapper)

Use this section **only** if the agent uses a framework with its own tool abstraction (crewAI, LangChain, custom `Tool.define()`, MCP `call_tool()`, etc.). The `createWarpGrepTool()` return value won't fit the framework's tool interface — you need to use the SDK client directly and wrap it yourself.

**The pattern is always the same:**

1. Instantiate `MorphClient` from `@morphllm/morphsdk`
2. Define a tool that conforms to the framework's interface (subclass, factory, decorator — whatever the framework uses)
3. In the tool's execute/run method, call `morph.warpGrep.execute()` with the search query and repo root
4. Return the formatted result as a string

**Generic template — adapt to your framework's tool interface:**

```typescript
import { MorphClient } from "@morphllm/morphsdk";

// 1. Instantiate the client (do this once, outside the tool)
const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

// 2. Define the tool using your framework's interface
//    Replace FrameworkTool / BaseTool / Tool.define with whatever your framework uses
//    The important parts are: name, description, parameter schema, and execute body

// name: "codebase_search"  ← MUST be this name, never "grep" or "search"
// description: "Search the codebase using natural language. Input is plain English describing what you're looking for, NOT regex."
// parameters: { search_term: string }  ← single required string parameter

// 3. In the execute/run body:
async function executeCodebaseSearch(searchTerm: string): Promise<string> {
  const result = await morph.warpGrep.execute({
    query: searchTerm,
    repoRoot: ".",  // ← adjust to match your agent's working directory
  });

  if (!result.success) {
    return `codebase_search failed: ${result.error}`;
  }

  if (!result.contexts?.length) {
    return "No results found.";
  }

  // Format results as file:content pairs for the parent model
  return result.contexts
    .map((ctx) => `--- ${ctx.file} ---\n${ctx.content}`)
    .join("\n\n");
}
```

**Concrete examples for common frameworks:**

<details>
<summary>crewAI (Python-style — adapt concept to TypeScript)</summary>

crewAI uses `BaseTool` with a `_run()` method:

```typescript
// The crewAI pattern in TypeScript would look like:
class CodebaseSearchTool extends BaseTool {
  name = "codebase_search";
  description = "Search the codebase using natural language. Input is plain English, NOT regex.";

  async _run(searchTerm: string): Promise<string> {
    return executeCodebaseSearch(searchTerm);
  }
}
```

</details>

<details>
<summary>Custom Tool.define() / defineTool() pattern</summary>

If the framework uses a factory function:

```typescript
const codebaseSearchTool = Tool.define("codebase_search", {
  description: "Search the codebase using natural language. Input is plain English, NOT regex.",
  parameters: z.object({
    search_term: z.string().describe("Plain English description of what to search for"),
  }),
  async execute(params, ctx) {
    return executeCodebaseSearch(params.search_term);
  },
});
```

</details>

<details>
<summary>MCP server (list_tools / call_tool)</summary>

If the agent discovers tools via MCP protocol, you implement the MCP trait:

```typescript
// In your MCP server's list_tools handler:
{
  name: "codebase_search",
  description: "Search the codebase using natural language. Input is plain English, NOT regex.",
  inputSchema: {
    type: "object",
    properties: {
      search_term: {
        type: "string",
        description: "Plain English description of what to search for",
      },
    },
    required: ["search_term"],
  },
  annotations: {
    readOnlyHint: true,
    idempotentHint: true,
    openWorldHint: false,
  },
}

// In your MCP server's call_tool handler:
if (toolName === "codebase_search") {
  const result = await executeCodebaseSearch(args.search_term);
  return { content: [{ type: "text", text: result }] };
}
```

</details>

**Key rules:**
- The tool name **must** be `codebase_search`. If the model sees "grep" in the name, it will pass regex patterns instead of English.
- The description **must** say "natural language" or "plain English". This steers the model away from regex.
- There is only one parameter: `search_term` (string). No other parameters.
- The `MorphClient` should be instantiated once and reused across calls. Do not create a new client per search.
- Set `repoRoot` to the directory the agent operates on. This is typically the repo root or workspace directory.

**Checkpoint:** After wiring it in:
- [ ] The tool appears in the framework's tool registry with name `codebase_search`
- [ ] A test invocation returns real file content (not errors, not empty)
- [ ] The result is a plain string that the parent model can consume

Then skip to **Step 5**.

---

#### Step 4d: CLI / Bash integration (executable wrapper)

Use this section **only** if the agent operates by emitting shell commands and tools are registered as executables with command signatures.

You need two things:
1. A CLI script that wraps the SDK
2. A tool config entry that tells the agent how to invoke it

**1. Create the CLI wrapper:**

Save this as `bin/codebase_search` (or wherever the agent's tool scripts live) and make it executable (`chmod +x`):

```typescript
#!/usr/bin/env npx tsx
// bin/codebase_search — CLI wrapper for codebase_search SDK
import { MorphClient } from "@morphllm/morphsdk";

const query = process.argv.slice(2).join(" ");
if (!query) {
  console.error("Usage: codebase_search <natural language query>");
  console.error('Example: codebase_search "Find the authentication middleware"');
  process.exit(1);
}

if (!process.env.MORPH_API_KEY) {
  console.error("MORPH_API_KEY environment variable is required");
  process.exit(1);
}

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const result = await morph.warpGrep.execute({
  query,
  repoRoot: process.env.REPO_ROOT || process.cwd(),
});

if (!result.success) {
  console.error(`Search failed: ${result.error}`);
  process.exit(1);
}

if (!result.contexts?.length) {
  console.log("No results found.");
  process.exit(0);
}

for (const ctx of result.contexts) {
  console.log(`--- ${ctx.file} ---`);
  console.log(ctx.content);
  console.log();
}
```

**2. Register it in the agent's tool config:**

Adapt this to your agent's config format (YAML, JSON, Python dict — match the existing tools):

```yaml
tools:
  codebase_search:
    signature: "codebase_search <query>"
    docstring: |
      Search the codebase using natural language. Takes a plain English description
      of what you're looking for (NOT regex). Returns matching file contents.
      Example: codebase_search "Find the authentication middleware"
      Example: codebase_search "How does the payment flow work?"
      Example: codebase_search "Where are database connections configured?"
```

**3. Test it independently:**

```bash
MORPH_API_KEY=your-key ./bin/codebase_search "Find the main entry point"
```

You should see file paths and code content on stdout. If not, debug the script before registering it with the agent.

**Trade-offs vs the other harness types:**
- Simplest integration — no framework coupling, no SDK imports in the agent itself
- But: no structured output (just stdout text), no rich error handling (just exit codes), and the agent must shell out for every search
- If the agent's harness supports structured tool definitions (Type 1 or Type 2), prefer those — you get better error handling and the model sees typed results

**Checkpoint:** After wiring it in:
- [ ] `./bin/codebase_search "test query"` returns real file content on stdout
- [ ] The tool appears in the agent's tool config with name `codebase_search`
- [ ] The agent can invoke it and receives the output as an observation

Then proceed to **Step 5**.

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
- **Direct API Integration Prompt (Python/any language):** [one-prompt-integration-api.md](./one-prompt-integration-api.md)
- **Direct API Protocol Docs:** https://docs.morphllm.com/sdk/components/warp-grep/direct
- **Examples (all providers + sandboxes):** https://github.com/morphllm/examples/tree/main/warpgrep
- **Pricing:** $0.80 / 1M tokens input, $0.80 / 1M tokens output
- **API Keys:** https://morphllm.com/dashboard/api-keys
