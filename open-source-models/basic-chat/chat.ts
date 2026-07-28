// # Basic Chat
//
// The OpenAI SDK pointed at Morph. One completion, no streaming.
// Prints which model actually served the request and what it cost in tokens.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx chat.ts
//   MORPH_API_KEY=your-key npx tsx chat.ts "your prompt"

import OpenAI from "openai";

if (!process.env.MORPH_API_KEY) {
  console.error("MORPH_API_KEY is not set. Get a key at https://morphllm.com/dashboard/api-keys");
  process.exit(1);
}

const client = new OpenAI({
  apiKey: process.env.MORPH_API_KEY,
  baseURL: "https://api.morphllm.com/v1",
});

const model = process.env.MORPH_MODEL || "morph-glm52-744b";
const prompt = process.argv[2] || "Write a TypeScript function that debounces an async function.";

const response = await client.chat.completions.create({
  model,
  messages: [
    { role: "system", content: "You are a senior backend engineer. Be concise." },
    { role: "user", content: prompt },
  ],
  max_tokens: 2048,
  temperature: 0.2,
});

console.log(response.choices[0].message.content);

const usage = response.usage;
console.log(`\n--- requested: ${model} | served by: ${response.model} ---`);
console.log(`prompt: ${usage?.prompt_tokens}  completion: ${usage?.completion_tokens}  total: ${usage?.total_tokens}`);

const cached = (usage?.prompt_tokens_details as { cached_tokens?: number } | undefined)?.cached_tokens;
if (cached) console.log(`cached prompt tokens: ${cached}`);
