// # Streaming
//
// Stream tokens to stdout as they generate, then report measured throughput.
// Time to first token and tok/s are the two numbers that matter for agent loops.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx stream.ts
//   MORPH_API_KEY=your-key npx tsx stream.ts "your prompt"

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
const prompt = process.argv[2] || "Explain how a token bucket rate limiter works, then implement one in TypeScript.";

const start = Date.now();
let firstTokenAt = 0;
let tokens = 0;

const stream = await client.chat.completions.create({
  model,
  messages: [{ role: "user", content: prompt }],
  max_tokens: 2048,
  stream: true,
  stream_options: { include_usage: true },
});

let served = model;
let reported: number | undefined;

for await (const chunk of stream) {
  if (chunk.model) served = chunk.model;
  if (chunk.usage) reported = chunk.usage.completion_tokens;

  const delta = chunk.choices[0]?.delta as { content?: string; reasoning_content?: string } | undefined;
  const text = delta?.content ?? delta?.reasoning_content;
  if (!text) continue;

  if (!firstTokenAt) firstTokenAt = Date.now();
  tokens++;
  process.stdout.write(text);
}

const total = (Date.now() - start) / 1000;
const generation = (Date.now() - firstTokenAt) / 1000;
const counted = reported ?? tokens;

console.log(`\n\n--- served by: ${served} ---`);
console.log(`time to first token: ${((firstTokenAt - start) / 1000).toFixed(2)}s`);
console.log(`completion tokens: ${counted}`);
console.log(`generation: ${(counted / generation).toFixed(1)} tok/s`);
console.log(`end to end: ${(counted / total).toFixed(1)} tok/s over ${total.toFixed(2)}s`);
