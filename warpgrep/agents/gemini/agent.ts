// # Gemini Agent
//
// WarpGrep as a tool inside a Google Gemini agent.
// Gemini uses function declarations instead of JSON schemas —
// the SDK adapter handles the format conversion.
//
// Usage:
//   MORPH_API_KEY=your-key GOOGLE_API_KEY=your-key npx tsx agent.ts
//   MORPH_API_KEY=your-key GOOGLE_API_KEY=your-key npx tsx agent.ts "How does auth work?"

import { GoogleGenerativeAI } from "@google/generative-ai";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/gemini";

const genAI = new GoogleGenerativeAI(process.env.GOOGLE_API_KEY!);
const grepTool = createWarpGrepTool({ repoRoot: "." });

const query =
  process.argv[2] || "Where does the 404 handler get registered?";

console.log(`Question: "${query}"\n`);

// Gemini takes function declarations inside a tools array.
const model = genAI.getGenerativeModel({
  model: "gemini-2.0-flash",
  tools: [{ functionDeclarations: [grepTool] }],
  systemInstruction:
    "You are a code assistant. Use the warpgrep_codebase_search tool to search the codebase before answering. Be concise.",
});

const chat = model.startChat();
let response = await chat.sendMessage(query);

// Agent loop — run until Gemini stops calling tools.
for (let turn = 0; turn < 5; turn++) {
  const calls = response.response.functionCalls();
  if (!calls?.length) break;

  // Execute each function call and send results back.
  const results = [];
  for (const call of calls) {
    console.log(
      `[WarpGrep] Searching: "${(call.args as { query: string }).query}"`
    );
    const result = await grepTool.execute(call.args);
    results.push({
      functionResponse: {
        name: call.name,
        response: { result: grepTool.formatResult(result) },
      },
    });
  }

  response = await chat.sendMessage(results);
}

console.log(`\n${response.response.text()}`);
