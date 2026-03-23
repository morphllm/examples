# WarpGrep Integration Prompt — Docs Platform Agents

> **What is this?** A prompt you can give to any AI coding assistant (Claude, GPT, Cursor, etc.) to integrate WarpGrep into a documentation platform that runs an LLM agent in a sandboxed environment (e.g., Mintlify Workflows, GitBook, ReadMe, or similar docs-as-code platforms). Copy everything below the line and paste it into your assistant.
>
> **Generic TypeScript agent?** If your agent is a standard Node.js/TypeScript app (not a sandboxed docs platform), use the [SDK Integration Prompt](../SDK_INTEGRATION_PROMPT.md) instead — it covers Anthropic, OpenAI, Gemini, and Vercel AI SDK directly.
>

---

## Prompt

You are integrating WarpGrep — a code search sub-agent — into a documentation platform's agent system. WarpGrep is NOT a regex tool. It is a separate LLM that takes a **natural-language query**, searches a codebase using ripgrep and file reads internally, and returns relevant code snippets. It runs in its own context window so it doesn't pollute the parent agent's context.

**Target architecture:** The platform pre-bundles the `@morphllm/morphsdk` Node.js package in its agent sandbox, then exposes a `codebase_search` tool that the agent can call. The tool calls the SDK, which handles everything — multi-turn search loop, ripgrep execution, result aggregation.

**Important naming convention:** Name the tool `codebase_search`, NOT `warpgrep` or `grep`. Models see the word "grep" and assume they need to pass regex patterns. They don't — the input is plain English like "Find the authentication middleware" or "How does the payment flow work?".

Follow these steps exactly. Complete each step before moving to the next.

---

### Step 1: Understand the agent's execution environment

These platforms typically run their agent in an **isolated sandbox** with some combination of:
- Node.js / Bun runtime
- Shell utilities (`grep`, `sed`, `awk`, `curl`)
- `git` and `gh` CLI
- A platform-specific CLI (e.g., `mint` for Mintlify)
- Cloned repositories (specified in workflow config)

Search the codebase for how the sandbox is built and configured. Look for:
- **Sandbox image definition** — Dockerfile, image config, build scripts, dependency manifests
- **Available runtimes** — What can the agent execute? (Node.js, Bun, Python, shell)
- **Tool registry** — How are tools registered and dispatched to the agent? Is it function calling via an LLM SDK, a command-line interface, or something custom?
- **Existing tools** — What tools does the agent already have? Look at their schemas and execution patterns — you'll follow the same pattern.
- **Environment variable injection** — How does the platform pass secrets/config into the sandbox? (Dashboard settings, secrets manager, .env injection, workflow config)

### Step 2: Verify network connectivity

WarpGrep calls `api.morphllm.com` to run searches. The sandbox must be able to reach this endpoint.

Check connectivity from inside the sandbox:
```bash
curl -sf https://api.morphllm.com/v1/models && echo "OK" || echo "BLOCKED"
```

If blocked:
- Check if the platform has a network allowlist or egress firewall rules. Add `api.morphllm.com` (port 443).
- Check if the platform routes traffic through a proxy. If so, the SDK respects `HTTPS_PROXY`.
- If network access truly cannot be granted, WarpGrep cannot function in this sandbox — escalate to the platform team.

### Step 3: Bundle the SDK in the sandbox image

Add `@morphllm/morphsdk` to the sandbox so it's available at runtime without needing `npm install` (most doc platform sandboxes can't install packages at runtime).

Add it to whatever dependency manifest the sandbox image uses:
```
@morphllm/morphsdk
```

The SDK bundles its own ripgrep binary — no separate ripgrep installation needed.

Use a caret range (`^1`) so non-breaking updates are picked up when the image rebuilds. The SDK will stay current as the platform rebuilds its sandbox image on its normal release cadence.

**Requirements:**
- Node.js 18+ or Bun
- `MORPH_API_KEY` environment variable (get from https://morphllm.com/dashboard/api-keys)

### Step 4: Provision the API key

Find how the platform injects environment variables into the sandbox and add:

| Variable | Value | Source |
|---|---|---|
| `MORPH_API_KEY` | `sk-morph-...` | https://morphllm.com/dashboard/api-keys |

This is typically configured in the platform's dashboard under integrations, secrets, or environment settings. If the platform supports per-organization keys, provision one per customer workspace.

### Step 5: Create the `codebase_search` tool

Create a tool that wraps the SDK. The tool should:
1. Accept a natural-language `query` string
2. Point at the cloned repo directory in the sandbox
3. Call the SDK
4. Return formatted results to the agent

**Using the SDK directly (simplest):**
```typescript
import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

async function codebaseSearch(query: string, repoRoot: string): Promise<string> {
  const result = await morph.warpGrep.execute({ query, repoRoot });

  if (!result.success || !result.contexts?.length) {
    return "No relevant code found.";
  }

  return result.contexts
    .map((ctx) => `--- ${ctx.file} ---\n${ctx.content}`)
    .join("\n\n");
}
```

**Using a provider adapter (if the agent uses a specific LLM SDK):**

The SDK ships pre-formatted tool objects for each major provider. Use the one that matches the agent's LLM SDK:

| Agent's LLM SDK | Import |
|---|---|
| Vercel AI SDK (`ai`) | `@morphllm/morphsdk/tools/warp-grep/vercel` |
| `@anthropic-ai/sdk` | `@morphllm/morphsdk/tools/warp-grep/anthropic` |
| `openai` | `@morphllm/morphsdk/tools/warp-grep/openai` |
| `@google/generative-ai` | `@morphllm/morphsdk/tools/warp-grep/gemini` |

```typescript
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/vercel";
// or: /anthropic, /openai, /gemini

const grepTool = createWarpGrepTool({ repoRoot: "/path/to/cloned/repo" });
// Pass grepTool directly into the agent's tools array
```

The adapter handles tool schema, execution, and result formatting — you just wire it in.

### Step 6: Handle sandbox execution (if code is in a remote sandbox)

By default, the SDK executes ripgrep, file reads, and directory listings **locally** — on the same filesystem where the Node.js process runs. This works when the SDK and the cloned repos are on the same machine.

If the code lives in a **separate sandbox** (the SDK runs on the platform backend, but the repo is cloned inside an isolated container), the default tools will fail because they can't see the files. You need to override them with `remoteCommands` that route execution into the sandbox.

**Check:** Does the SDK run in the same filesystem as the cloned repos?
- **Yes** (SDK is bundled inside the sandbox image, repos are cloned there too) → Skip this step. Local execution works.
- **No** (SDK runs on the platform backend, sandbox is a separate container) → You must provide `remoteCommands`.

`remoteCommands` takes three functions — `grep`, `read`, and `listDir` — that execute shell commands inside the sandbox using whatever execution API the platform provides:

```typescript
const grepTool = createWarpGrepTool({
  repoRoot: "/path/to/repo/in/sandbox",
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
```

Adapt `sandbox.commands.run(...)` to whatever the platform's sandbox execution API is (e.g., `sandbox.exec()`, `sandbox.process.executeCommand()`, `ssh.execCommand()`, etc.). The three functions just need to return stdout as a string.

**Prerequisite:** `ripgrep` must be installed inside the sandbox. Add `apt-get install -y ripgrep` to the sandbox image build, or download the static binary.

For detailed examples across E2B, Modal, Daytona, Vercel Sandbox, Cloudflare Workers, and Docker/SSH, see:
https://docs.morphllm.com/sdk/components/warp-grep/sandbox-execution

### Step 7: Register the tool with the agent

Wire `codebase_search` into the agent's tool registry alongside its existing tools. Follow whatever pattern the existing tools use (Step 1 findings).

The tool schema is minimal — one required string parameter:

```
name: codebase_search
description: Search the codebase for relevant code using natural language.
  Takes a query like "Find the authentication middleware" or
  "How does error handling work in the API layer".
  Returns matching code snippets with file paths.
  Do NOT pass regex — use plain English.
parameters:
  query (string, required): What code to search for
```

### Step 8: Add system prompt guidance

Append the following to the agent's system prompt so it knows when and how to use the tool:

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

### Step 9: Test the integration

Run these in order inside the sandbox. Each verifies a different layer.

**Test A — SDK is loadable (no API key needed):**
```bash
node -e "
  const { MorphClient } = require('@morphllm/morphsdk');
  console.log('SDK:', typeof MorphClient === 'function' ? 'OK' : 'FAIL');
"
```

**Test B — Network + API key:**
```bash
node -e "
  const { MorphClient } = require('@morphllm/morphsdk');
  const m = new MorphClient({ apiKey: process.env.MORPH_API_KEY });
  m.warpGrep.execute({ query: 'Find the main entry point', repoRoot: '.' })
    .then(r => console.log('Search:', r.success ? 'OK' : 'FAIL', '— files:', r.contexts?.length ?? 0))
    .catch(e => console.error('FAIL:', e.message));
"
```
If this fails with a network error, revisit Step 2. If 401, revisit Step 4.

**Test C — Full agent integration:**

Trigger the agent with a prompt that requires code search:
> "Use codebase_search to find how errors are handled in this project, then summarize what you found."

Verify:
1. The agent calls `codebase_search` (not grep or cat)
2. It passes a natural-language query (not a regex pattern)
3. It receives code snippets and uses them in its response

---

### Reference

- **SDK Package:** `@morphllm/morphsdk` ([npm](https://www.npmjs.com/package/@morphllm/morphsdk))
- **SDK Docs:** https://docs.morphllm.com/sdk/components/warp-grep/index
- **Raw HTTP Protocol (Python / any language):** https://docs.morphllm.com/sdk/components/warp-grep/direct
- **Examples (all providers + sandboxes):** https://github.com/morphllm/examples/tree/main/warpgrep
- **API Keys:** https://morphllm.com/dashboard/api-keys
- **Pricing:** $0.80 / 1M tokens input, $0.80 / 1M tokens output
