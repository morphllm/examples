# Compact: Query-Conditioned

The same transcript, compacted twice under two different queries. This is the example that shows compaction is not truncation.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx compact.ts
```

## What it does

1. Compacts one transcript twice — once with `query: "how does JWT token verification and refresh work?"`, once with `query: "database connection pooling and tail latency"` — both at `compression_ratio: 0.3`
2. Prints each compacted output with its token counts
3. Takes the set difference of the surviving lines, so you can see exactly what each query pulled in that the other dropped

Because compaction only ever deletes lines, both outputs are subsets of the same source. A line-level diff between them is meaningful in a way it never would be between two summaries.

Real output from a run:

```
=== query: how does JWT token verification and refresh work?
=== kept 157/429 tokens (ratio 0.366)

=== query: database connection pooling and tail latency
=== kept 221/429 tokens (ratio 0.515)

=== kept only for the auth query
assistant: lib/auth/jwt.ts. verifyToken() checks the RS256 signature against the
  hashed in the sessions table.
user: what happens on an expired token?
assistant: verifyToken() throws TokenExpiredError, the middleware catches it and

=== kept only for the pooling query
user: ok now the database side. how are connections managed?
assistant: A single pg.Pool in lib/db/pool.ts, max 20 connections, idle timeout
  30 seconds, and statement_timeout set to 10s server-side.
assistant: Under load the pool saturates around 18 connections. The p99 acquire
  wait is 40ms, which is where most of the tail latency comes from.
  app open 200 logical connections against 20 real ones.
```

Under the auth query the entire database section — 17 consecutive lines — is dropped. Under the pooling query it survives intact and the JWT specifics thin out. Truncation would have returned the same lines both times.

The achieved ratio differs per query (0.366 vs 0.515) because `compression_ratio` is a target, not a hard cap. When more of the input is genuinely relevant, less of it gets cut.
