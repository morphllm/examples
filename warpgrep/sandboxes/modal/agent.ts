// # Modal Sandbox Agent
//
// WarpGrep searching code inside a Modal cloud sandbox.
// The repo is cloned into a Modal container, and WarpGrep
// runs ripgrep/sed/find inside it via remoteCommands.
//
// Usage:
//   MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key MODAL_TOKEN_ID=... MODAL_TOKEN_SECRET=... npx tsx agent.ts
//   MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does streaming work?"

import Anthropic from "@anthropic-ai/sdk";
import modal from "@modal/client";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

const REPO_URL = "https://github.com/anthropics/anthropic-cookbook";
const REPO_DIR = "/home/repo";

// --- 1. Create a Modal sandbox with ripgrep and git ---

console.log("Creating Modal sandbox...");

const image = modal.Image.debian_slim().apt_install("ripgrep", "git");
const sandbox = await modal.Sandbox.create({ image });

console.log("Cloning repo into sandbox...");
const cloneProc = await sandbox.exec("git", "clone", "--depth", "1", REPO_URL, REPO_DIR);
await cloneProc.stdout.read(); // wait for clone to finish
console.log("Repo cloned.\n");

// --- 2. Create WarpGrep tool with remoteCommands ---

const grepTool = createWarpGrepTool({
  repoRoot: REPO_DIR,
  remoteCommands: {
    grep: async (pattern, path, glob) => {
      const args = ["rg", "--no-heading", "--line-number", "-C", "1", pattern, path];
      if (glob) {
        args.push("--glob", glob);
      }
      const proc = await sandbox.exec(...args);
      const stdout = await proc.stdout.read();
      return stdout ?? "";
    },
    read: async (path, start, end) => {
      const proc = await sandbox.exec("sed", "-n", `${start},${end}p`, path);
      const stdout = await proc.stdout.read();
      return stdout ?? "";
    },
    listDir: async (path, maxDepth) => {
      const proc = await sandbox.exec(
        "find", path,
        "-maxdepth", String(maxDepth),
        "-not", "-path", "*/node_modules/*",
        "-not", "-path", "*/.git/*"
      );
      const stdout = await proc.stdout.read();
      return stdout ?? "";
    },
  },
});

// --- 3. Run the agent loop ---

const anthropic = new Anthropic();

const query =
  process.argv[2] || "How are the prompt caching examples structured?";

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

    if (response.stop_reason === "end_turn") {
      for (const block of response.content) {
        if (block.type === "text") {
          console.log(block.text);
        }
      }
      break;
    }

    const toolResults: Anthropic.ToolResultBlockParam[] = [];

    for (const block of response.content) {
      if (block.type === "tool_use") {
        console.log(`[WarpGrep] Searching: "${(block.input as { query: string }).query}"`);
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
  // 4. Cleanup
  console.log("\nTerminating sandbox...");
  await sandbox.terminate();
  console.log("Done.");
}
