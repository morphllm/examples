// # E2B Sandbox Agent
//
// WarpGrep searching code inside an E2B cloud sandbox.
// The repo is cloned into the sandbox, and WarpGrep uses
// remoteCommands to run ripgrep/sed/find inside it.
//
// Usage:
//   E2B_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
//   E2B_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does streaming work?"

import Anthropic from "@anthropic-ai/sdk";
import { Sandbox } from "@e2b/code-interpreter";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

const REPO_URL = "https://github.com/anthropics/anthropic-cookbook";
const REPO_DIR = "/home/user/anthropic-cookbook";

// 1. Create an E2B sandbox and clone the repo
console.log("Creating E2B sandbox...");
const sandbox = await Sandbox.create();

console.log("Installing ripgrep and cloning repo...");
await sandbox.commands.run("apt-get update && apt-get install -y ripgrep");
await sandbox.commands.run(`git clone --depth 1 ${REPO_URL} ${REPO_DIR}`);
console.log("Sandbox ready.\n");

// 2. Create WarpGrep tool with remoteCommands that execute inside the sandbox
const grepTool = createWarpGrepTool({
  repoRoot: REPO_DIR,
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
  // 4. Clean up the sandbox
  await sandbox.kill();
  console.log("\nSandbox cleaned up.");
}
