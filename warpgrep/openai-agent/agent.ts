// # OpenAI Agent
//
// WarpGrep as a tool inside a GPT-4o agent loop.
// WarpGrep searches in its own context window so GPT's stays clean.
//
// Usage:
//   MORPH_API_KEY=your-key OPENAI_API_KEY=your-key npx tsx agent.ts
//   MORPH_API_KEY=your-key OPENAI_API_KEY=your-key npx tsx agent.ts "How does auth work?"

import OpenAI from "openai";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/openai";

const openai = new OpenAI();
const grepTool = createWarpGrepTool({ repoRoot: "." });

const query =
  process.argv[2] || "How does the tool result get formatted back to the model?";

console.log(`Question: "${query}"\n`);

// Start the agent loop
const messages: OpenAI.ChatCompletionMessageParam[] = [
  {
    role: "system",
    content:
      "You are a code assistant. Use the warpgrep_codebase_search tool to find relevant code before answering questions. Be concise.",
  },
  { role: "user", content: query },
];

for (let turn = 0; turn < 5; turn++) {
  const response = await openai.chat.completions.create({
    model: "gpt-4o",
    tools: [grepTool],
    messages,
  });

  const choice = response.choices[0];

  // If the model is done, print the final text and exit
  if (choice.finish_reason === "stop") {
    console.log(choice.message.content);
    break;
  }

  // Add the assistant message (with tool calls) to history
  messages.push(choice.message);

  // Execute any tool calls
  if (choice.message.tool_calls) {
    for (const toolCall of choice.message.tool_calls) {
      const input = JSON.parse(toolCall.function.arguments);
      console.log(`[WarpGrep] Searching: "${input.query}"`);

      const result = await grepTool.execute(input);

      messages.push({
        role: "tool",
        tool_call_id: toolCall.id,
        content: grepTool.formatResult(result),
      });
    }
  }
}
