// # Compaction via Chat Completions
//
// Compaction through the OpenAI SDK — point baseURL at api.morphllm.com/v1 and
// use model "morph-compactor". Useful when you already have an OpenAI client
// wired up and want compaction without adding a dependency.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx compact.ts

import OpenAI from "openai";

if (!process.env.MORPH_API_KEY) {
  console.error("MORPH_API_KEY is not set");
  process.exit(1);
}

const client = new OpenAI({
  apiKey: process.env.MORPH_API_KEY,
  baseURL: "https://api.morphllm.com/v1",
});

const transcript = `
user: where is the rate limiter?
assistant: middleware/limit.ts — a sliding window in Redis, 100 requests per
  minute per API key.
assistant: The window key is \`rl:\${apiKey}:\${minuteBucket}\` with a 120 second TTL.
user: what happens when it trips?
assistant: 429 with a Retry-After header set to the seconds remaining in the
  current bucket.
user: side question, what's the log retention?
assistant: 30 days in Loki, then object storage for a year.
user: and the dashboard URL?
assistant: grafana.internal/d/api-overview
user: ok. does the limiter fail open or closed?
assistant: Open. If Redis is unreachable, incr() throws, the catch block logs a
  warning and the request proceeds. That's deliberate — a Redis outage should not
  take down the API.
user: makes sense
assistant: One caveat: there's no circuit breaker, so every request pays the full
  Redis timeout (200ms) while Redis is down.
`.trim();

const response = await client.chat.completions.create({
  model: "morph-compactor",
  messages: [{ role: "user", content: transcript }],
  // Morph-specific knobs, passed through the OpenAI SDK's extra body
  // @ts-expect-error not part of the OpenAI schema
  query: "how the rate limiter behaves when Redis is down",
  compression_ratio: 0.4,
});

console.log(response.choices[0].message.content);
console.log("\n---");
console.log(JSON.stringify(response.usage, null, 2));
