// # Vercel AI SDK Agent
//
// WarpGrep as a tool inside a Vercel AI SDK agent.
// The Vercel SDK handles the tool loop automatically —
// you just pass the tool and it runs until done.
//
// Usage:
//   MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
//   MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does auth work?"

import { generateText } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/vercel";

const grepTool = createWarpGrepTool({ repoRoot: "." });

const query =
  process.argv[2] || "Find the rate limiting implementation";

console.log(`Question: "${query}"\n`);

// generateText handles the tool loop for you. maxSteps controls
// how many times the model can call tools before it must respond.
const { text, steps } = await generateText({
  model: anthropic("claude-sonnet-4-5-20250929"),
  tools: { warpgrep_codebase_search: grepTool },
  maxSteps: 5,
  system:
    "You are a code assistant. Use the warpgrep_codebase_search tool to search the codebase before answering. Be concise.",
  prompt: query,
});

// Show what happened behind the scenes.
for (const step of steps) {
  for (const call of step.toolCalls) {
    console.log(`[WarpGrep] Searched: "${call.input.query}"`);
  }
}

console.log(`\n${text}`);
