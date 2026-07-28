// # Streaming Apply
//
// The same Fast Apply call with `stream: true`. The merged file is written to
// stdout as it arrives, and measured throughput is printed at the end.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx apply.ts

import OpenAI from "openai";

if (!process.env.MORPH_API_KEY) {
  console.error("MORPH_API_KEY is not set");
  process.exit(1);
}

const morph = new OpenAI({
  apiKey: process.env.MORPH_API_KEY,
  baseURL: "https://api.morphllm.com/v1",
});

const originalCode = `type Bucket = {
  tokens: number;
  updatedAt: number;
};

export class RateLimiter {
  private buckets = new Map<string, Bucket>();

  constructor(
    private readonly capacity: number,
    private readonly refillPerSecond: number,
  ) {}

  private refill(bucket: Bucket, now: number): void {
    const elapsed = (now - bucket.updatedAt) / 1000;
    bucket.tokens = Math.min(
      this.capacity,
      bucket.tokens + elapsed * this.refillPerSecond,
    );
    bucket.updatedAt = now;
  }

  take(key: string, cost = 1): boolean {
    const now = Date.now();
    let bucket = this.buckets.get(key);
    if (!bucket) {
      bucket = { tokens: this.capacity, updatedAt: now };
      this.buckets.set(key, bucket);
    }
    this.refill(bucket, now);
    if (bucket.tokens < cost) {
      return false;
    }
    bucket.tokens -= cost;
    return true;
  }

  remaining(key: string): number {
    const bucket = this.buckets.get(key);
    if (!bucket) {
      return this.capacity;
    }
    this.refill(bucket, Date.now());
    return bucket.tokens;
  }

  reset(key: string): void {
    this.buckets.delete(key);
  }
}
`;

const instruction =
  "I will add a retryAfter method that reports how long until the next token is available";

const codeEdit = `// ... existing code ...
  remaining(key: string): number {
    const bucket = this.buckets.get(key);
    if (!bucket) {
      return this.capacity;
    }
    this.refill(bucket, Date.now());
    return bucket.tokens;
  }

  retryAfter(key: string, cost = 1): number {
    const available = this.remaining(key);
    if (available >= cost) {
      return 0;
    }
    return ((cost - available) / this.refillPerSecond) * 1000;
  }
// ... existing code ...`;

const startedAt = performance.now();
let firstTokenAt: number | undefined;
let completionTokens = 0;

const stream = await morph.chat.completions.create({
  model: "morph-v3-fast",
  stream: true,
  stream_options: { include_usage: true },
  messages: [
    {
      role: "user",
      content: `<instruction>${instruction}</instruction>\n<code>${originalCode}</code>\n<update>${codeEdit}</update>`,
    },
  ],
});

for await (const chunk of stream) {
  const delta = chunk.choices[0]?.delta?.content;
  if (delta) {
    firstTokenAt ??= performance.now();
    process.stdout.write(delta);
  }
  // The final chunk carries no choices, only usage.
  if (chunk.usage) {
    completionTokens = chunk.usage.completion_tokens;
  }
}

const finishedAt = performance.now();
const generationSeconds = (finishedAt - (firstTokenAt ?? startedAt)) / 1000;

console.log(`
--- ${completionTokens} output tokens`);
console.log(`--- time to first token: ${(((firstTokenAt ?? finishedAt) - startedAt)).toFixed(0)} ms`);
console.log(`--- ${(completionTokens / generationSeconds).toFixed(0)} tok/s`);
