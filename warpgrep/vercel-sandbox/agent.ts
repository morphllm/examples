// # Vercel Sandbox Agent
//
// WarpGrep searching code inside a Vercel cloud sandbox.
// The repo is cloned into the sandbox, and WarpGrep uses
// remoteCommands to run ripgrep/sed/find inside it.
//
// Note: Vercel Sandbox runs Amazon Linux 2023, which does not have apt-get.
// Ripgrep is installed by downloading the static musl binary.
//
// Usage:
//   VERCEL_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
//   VERCEL_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does streaming work?"

import Anthropic from "@anthropic-ai/sdk";
import { Sandbox } from "@vercel/sandbox";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

const REPO_URL = "https://github.com/anthropics/anthropic-cookbook";
const REPO_DIR = "/home/user/anthropic-cookbook";

// 1. Create a Vercel sandbox and clone the repo
console.log("Creating Vercel sandbox...");
const sandbox = await Sandbox.create({ runtime: "node24" });

console.log("Installing ripgrep and cloning repo...");
await sandbox.runCommand({
  cmd: "sh",
  args: [
    "-c",
    "curl -sL https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz | tar xz -C /tmp && cp /tmp/ripgrep-14.1.1-x86_64-unknown-linux-musl/rg /usr/local/bin/",
  ],
  sudo: true,
});
await sandbox.runCommand({
  cmd: "git",
  args: ["clone", "--depth", "1", REPO_URL, REPO_DIR],
});
console.log("Sandbox ready.\n");

// 2. Create WarpGrep tool with remoteCommands that execute inside the sandbox
const grepTool = createWarpGrepTool({
  repoRoot: REPO_DIR,
  remoteCommands: {
    grep: async (pattern, path, glob) => {
      const args = ["--no-heading", "--line-number", "-C", "1", pattern, path];
      if (glob) {
        args.push("--glob", glob);
      }
      const r = await sandbox.runCommand({ cmd: "rg", args });
      return await r.stdout();
    },
    read: async (path, start, end) => {
      const r = await sandbox.runCommand({
        cmd: "sed",
        args: ["-n", `${start},${end}p`, path],
      });
      return await r.stdout();
    },
    listDir: async (path, maxDepth) => {
      const r = await sandbox.runCommand({
        cmd: "find",
        args: [
          path,
          "-maxdepth",
          String(maxDepth),
          "-not",
          "-path",
          "*/node_modules/*",
          "-not",
          "-path",
          "*/.git/*",
        ],
      });
      return await r.stdout();
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
  await sandbox.close();
  console.log("\nSandbox cleaned up.");
}
