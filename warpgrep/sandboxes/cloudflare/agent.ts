// # Cloudflare Sandbox Agent (Pattern Reference)
//
// Cloudflare Sandbox requires a Worker environment. This example shows
// the remoteCommands pattern for integrating WarpGrep with Cloudflare Sandbox.
//
// In production, getSandbox() runs in your Cloudflare Worker, and
// createWarpGrepTool() runs in your Node.js backend that calls the Worker.
//
// This file cannot run standalone — it demonstrates the integration pattern.
// See the Cloudflare Sandbox docs for setting up the Worker environment.
//
// Usage (conceptual):
//   MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts

import Anthropic from "@anthropic-ai/sdk";
import { getSandbox } from "@cloudflare/sandbox";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

const REPO_URL = "https://github.com/anthropics/anthropic-cookbook";
const REPO_DIR = "/home/user/anthropic-cookbook";

// 1. Get a Cloudflare sandbox instance
// In production, `env` comes from the Worker's fetch handler:
//   export default { async fetch(req, env) { const sandbox = getSandbox(env.Sandbox, 'code-search'); ... } }
declare const env: { Sandbox: unknown };

console.log("Getting Cloudflare sandbox...");
const sandbox = getSandbox(env.Sandbox, "code-search");

console.log("Installing ripgrep and cloning repo...");
await sandbox.exec(
  `apt-get update && apt-get install -y ripgrep`
);
await sandbox.exec(
  `git clone --depth 1 ${REPO_URL} ${REPO_DIR}`
);
console.log("Sandbox ready.\n");

// 2. Create WarpGrep tool with remoteCommands that execute inside the sandbox
const grepTool = createWarpGrepTool({
  repoRoot: REPO_DIR,
  remoteCommands: {
    grep: async (pattern, path, glob) => {
      const globArg = glob ? ` --glob '${glob}'` : "";
      const r = await sandbox.exec(
        `rg --no-heading --line-number -C 1 '${pattern}' '${path}'${globArg}`
      );
      return r.stdout;
    },
    read: async (path, start, end) => {
      const r = await sandbox.exec(
        `sed -n '${start},${end}p' '${path}'`
      );
      return r.stdout;
    },
    listDir: async (path, maxDepth) => {
      const r = await sandbox.exec(
        `find '${path}' -maxdepth ${maxDepth} -not -path '*/node_modules/*' -not -path '*/.git/*'`
      );
      return r.stdout;
    },
  },
});

// 3. Run the agent loop
const anthropic = new Anthropic();
const query =
  process.argv[2] || "How does the anthropic-cookbook handle tool use?";

console.log(`Question: "${query}"\n`);

const messages: Anthropic.MessageParam[] = [
  { role: "user", content: query },
];

try {
  for (let turn = 0; turn < 5; turn++) {
    const response = await anthropic.messages.create({
      model: "claude-sonnet-4-5-20250929",
      max_tokens: 4096,
      system:
        "You are a code assistant. Use the warpgrep_codebase_search tool to find relevant code before answering questions. Be concise.",
      tools: [grepTool],
      messages,
    });

    messages.push({ role: "assistant", content: response.content });

    // If the model is done, print the final text and exit
    if (response.stop_reason === "end_turn") {
      for (const block of response.content) {
        if (block.type === "text") {
          console.log(block.text);
        }
      }
      break;
    }

    // Execute any tool calls
    const toolResults: Anthropic.ToolResultBlockParam[] = [];

    for (const block of response.content) {
      if (block.type === "tool_use") {
        console.log(
          `[WarpGrep] Searching: "${(block.input as { query: string }).query}"`
        );
        const result = await grepTool.execute(block.input);
        toolResults.push({
          type: "tool_result",
          tool_use_id: block.id,
          content: grepTool.formatResult(result),
        });
      }
    }

    if (toolResults.length > 0) {
      messages.push({ role: "user", content: toolResults });
    }
  }
} finally {
  console.log("\nDone.");
}
