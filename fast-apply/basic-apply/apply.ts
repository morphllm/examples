// # Basic Apply
//
// The minimal Fast Apply call. Read a file, send it plus a lazy edit
// snippet, get the merged file back.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx apply.ts

import { readFileSync } from "node:fs";
import OpenAI from "openai";

if (!process.env.MORPH_API_KEY) {
  console.error("MORPH_API_KEY is not set");
  process.exit(1);
}

const morph = new OpenAI({
  apiKey: process.env.MORPH_API_KEY,
  baseURL: "https://api.morphllm.com/v1",
});

const originalCode = readFileSync(new URL("./cart.ts", import.meta.url), "utf8");

const instruction = "I will reject negative discounts and clamp them to 100%";

// Only the changed region. Everything else is elided with a marker.
const codeEdit = `// ... existing code ...
export function applyDiscount(total: number, percentOff: number): number {
  if (percentOff < 0) {
    throw new Error("percentOff must be non-negative");
  }
  const clamped = Math.min(percentOff, 100);
  return total * (1 - clamped / 100);
}
// ... existing code ...`;

const response = await morph.chat.completions.create({
  model: "morph-v3-fast",
  messages: [
    {
      role: "user",
      content: `<instruction>${instruction}</instruction>\n<code>${originalCode}</code>\n<update>${codeEdit}</update>`,
    },
  ],
});

console.log(response.choices[0].message.content);
