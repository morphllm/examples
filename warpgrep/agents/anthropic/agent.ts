// # Anthropic Agent
//
// WarpGrep as a tool inside a Claude agent loop.
// WarpGrep searches in its own context window so Claude's stays clean.
//
// Usage:
//   MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
//   MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does auth work?"

import Anthropic from "@anthropic-ai/sdk";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

const anthropic = new Anthropic();
const grepTool = createWarpGrepTool({ repoRoot: "." });

const query =
  process.argv[2] || "How is the middleware pipeline structured?";

console.log(`Question: "${query}"\n`);

// Start the agent loop
const messages: Anthropic.MessageParam[] = [
  { role: "user", content: query },
];

for (let turn = 0; turn < 5; turn++) {
  const response = await anthropic.messages.create({
    model: "claude-sonnet-4-5-20250929",
    max_tokens: 4096,
    system:
      "You are a code assistant. Use the warpgrep_codebase_search tool to find relevant code before answering questions. Be concise.",
    tools: [grepTool],
    messages,
  });

  // Collect all content blocks
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
