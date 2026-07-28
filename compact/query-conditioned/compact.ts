// # Query-Conditioned Compaction
//
// The same input, compacted twice under two different queries.
// Compaction scores every line against the query and drops what the query
// doesn't need — so the two outputs keep different halves of the transcript.
// This is what separates it from truncation, which would keep the same lines
// no matter what you asked.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx compact.ts

import { MorphClient } from "@morphllm/morphsdk";

if (!process.env.MORPH_API_KEY) {
  console.error("MORPH_API_KEY is not set");
  process.exit(1);
}

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const transcript = `
user: starting on the new service, where does auth live?
assistant: lib/auth/jwt.ts. verifyToken() checks the RS256 signature against the
  JWKS endpoint cached in memory for 10 minutes.
assistant: Tokens carry a 15 minute expiry. Refresh tokens are opaque and stored
  hashed in the sessions table.
user: what happens on an expired token?
assistant: verifyToken() throws TokenExpiredError, the middleware catches it and
  returns 401 with { code: "token_expired" } so the client knows to refresh.
user: is there rate limiting on the refresh endpoint?
assistant: Yes, 10 refreshes per minute per user, enforced in middleware/limit.ts
  with a sliding window in Redis.
user: ok now the database side. how are connections managed?
assistant: A single pg.Pool in lib/db/pool.ts, max 20 connections, idle timeout
  30 seconds, and statement_timeout set to 10s server-side.
assistant: Under load the pool saturates around 18 connections. The p99 acquire
  wait is 40ms, which is where most of the tail latency comes from.
user: can we raise max?
assistant: Not much. Postgres max_connections is 100 and we run 4 app replicas,
  so 20 each already leaves little headroom for migrations and psql sessions.
assistant: PgBouncer in transaction mode would be the real fix — it would let the
  app open 200 logical connections against 20 real ones.
user: got it. and why is the dashboard slow to paint?
assistant: The Tailwind stylesheet is loaded async in app/dashboard/layout.tsx,
  so there's a flash of unstyled content on first paint.
assistant: Also the font is not preloaded, which costs another 200ms on cold
  cache. Add <link rel="preload"> for the woff2.
user: thanks, that's enough for today
assistant: Sounds good.
`.trim();

const queries = [
  "how does JWT token verification and refresh work?",
  "database connection pooling and tail latency",
];

const results = await Promise.all(
  queries.map((query) =>
    morph.compact({
      input: transcript,
      query,
      compressionRatio: 0.3,
      preserveRecent: 0,
    }),
  ),
);

results.forEach((result, i) => {
  const { input_tokens, output_tokens, compression_ratio } = result.usage;
  console.log(`=== query: ${queries[i]}`);
  console.log(`=== kept ${output_tokens}/${input_tokens} tokens (ratio ${compression_ratio})\n`);
  console.log(result.output);
  console.log();
});

// The two outputs are drawn from the same source lines, so a set difference
// shows exactly what each query pulled in that the other one dropped.
const isContent = (line: string) => line.trim() && !line.startsWith("(filtered");
const [a, b] = results.map((r) => new Set(r.output.split("\n").filter(isContent)));

console.log("=== kept only for the auth query");
for (const line of a) if (!b.has(line)) console.log(line);

console.log("\n=== kept only for the pooling query");
for (const line of b) if (!a.has(line)) console.log(line);
