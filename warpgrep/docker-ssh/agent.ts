// # Docker / SSH Agent
//
// WarpGrep searching code on a remote machine via SSH.
// The repo is cloned on the remote host, and WarpGrep uses
// remoteCommands to run ripgrep/sed/find over SSH.
//
// Prerequisites: ripgrep must be installed on the remote host.
//
// Usage:
//   SSH_HOST=your-host SSH_USER=your-user SSH_KEY_PATH=~/.ssh/id_rsa MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
//   SSH_HOST=your-host SSH_USER=your-user SSH_KEY_PATH=~/.ssh/id_rsa MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does streaming work?"

import Anthropic from "@anthropic-ai/sdk";
import { NodeSSH } from "node-ssh";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

const REPO_URL = "https://github.com/anthropics/anthropic-cookbook";
const REPO_DIR = "/tmp/anthropic-cookbook";

// 1. Connect via SSH and clone the repo
console.log("Connecting via SSH...");
const ssh = new NodeSSH();
await ssh.connect({
  host: process.env.SSH_HOST!,
  username: process.env.SSH_USER!,
  privateKey: process.env.SSH_KEY_PATH!,
});

console.log("Cloning repo on remote host...");
await ssh.execCommand(`git clone --depth 1 ${REPO_URL} ${REPO_DIR}`);
console.log("Remote host ready.\n");

// 2. Create WarpGrep tool with remoteCommands that execute over SSH
const grepTool = createWarpGrepTool({
  repoRoot: REPO_DIR,
  remoteCommands: {
    grep: async (pattern, path, glob) => {
      const globArg = glob ? ` --glob '${glob}'` : "";
      const result = await ssh.execCommand(
        `rg --no-heading --line-number -C 1 '${pattern}' '${path}'${globArg}`,
        { cwd: REPO_DIR }
      );
      return result.stdout || "";
    },
    read: async (path, start, end) => {
      const result = await ssh.execCommand(
        `sed -n '${start},${end}p' '${path}'`,
        { cwd: REPO_DIR }
      );
      return result.stdout || "";
    },
    listDir: async (path, maxDepth) => {
      const result = await ssh.execCommand(
        `find '${path}' -maxdepth ${maxDepth} -not -path '*/node_modules/*' -not -path '*/.git/*'`,
        { cwd: REPO_DIR }
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
  // 4. Clean up: remove cloned repo and disconnect
  await ssh.execCommand(`rm -rf ${REPO_DIR}`);
  ssh.dispose();
  console.log("\nSSH connection closed.");
}
