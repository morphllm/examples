// # Agent Loop
//
// The production pattern: Claude decides *what* to change and emits only the
// changed lines; morph-v3-fast merges that snippet into the real file.
//
// Usage:
//   MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
//   MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "your request"

import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";

if (!process.env.MORPH_API_KEY) {
  console.error("MORPH_API_KEY is not set");
  process.exit(1);
}
if (!process.env.ANTHROPIC_API_KEY) {
  console.error("ANTHROPIC_API_KEY is not set");
  process.exit(1);
}

const anthropic = new Anthropic();
const morph = new OpenAI({
  apiKey: process.env.MORPH_API_KEY,
  baseURL: "https://api.morphllm.com/v1",
});

const targetPath = fileURLToPath(new URL("./server.ts", import.meta.url));
const targetName = "server.ts";

const request =
  process.argv[2] ||
  "Return a 400 with a JSON error instead of throwing when the request body is not valid JSON.";

const editFileTool: Anthropic.Tool = {
  name: "edit_file",
  description:
    "Edit a file by describing only the lines that change. Never reproduce the whole file.",
  input_schema: {
    type: "object",
    properties: {
      instructions: {
        type: "string",
        description:
          "A single first-person sentence describing the edit, e.g. 'I will add input validation to the handler'.",
      },
      code_edit: {
        type: "string",
        description:
          "Only the changed lines. Represent every unchanged region with the literal marker '// ... existing code ...'. Keep enough surrounding context that each changed region is unambiguous.",
      },
    },
    required: ["instructions", "code_edit"],
    additionalProperties: false,
  },
};

const originalCode = readFileSync(targetPath, "utf8");

console.log(`Request: "${request}"\n`);

const response = await anthropic.messages.create({
  model: "claude-opus-5",
  max_tokens: 16000,
  tools: [editFileTool],
  tool_choice: { type: "tool", name: "edit_file" },
  system:
    "You are a coding agent. Use edit_file to make changes. Emit lazy edits: only the lines that change, with '// ... existing code ...' standing in for everything you are leaving alone. A separate merge model applies your snippet to the real file, so rewriting the file in full is wasted work.",
  messages: [
    {
      role: "user",
      content: `${request}\n\n${targetName}:\n\`\`\`typescript\n${originalCode}\n\`\`\``,
    },
  ],
});

const toolUse = response.content.find(
  (block): block is Anthropic.ToolUseBlock => block.type === "tool_use",
);
if (!toolUse) {
  console.error("Claude did not call edit_file");
  process.exit(1);
}

const { instructions, code_edit: codeEdit } = toolUse.input as {
  instructions: string;
  code_edit: string;
};

console.log(`Claude: ${instructions}`);
console.log(`Snippet: ${codeEdit.split("\n").length} lines vs ${originalCode.split("\n").length} in the file\n`);

const merge = await morph.chat.completions.create({
  model: "morph-v3-fast",
  messages: [
    {
      role: "user",
      content: `<instruction>${instructions}</instruction>\n<code>${originalCode}</code>\n<update>${codeEdit}</update>`,
    },
  ],
});

const merged = merge.choices[0].message.content;
if (!merged) {
  console.error("Fast Apply returned no content");
  process.exit(1);
}

writeFileSync(targetPath, merged.endsWith("\n") ? merged : `${merged}\n`);
console.log(`Wrote ${merged.split("\n").length} lines to ${targetName}`);
