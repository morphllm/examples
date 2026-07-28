// # Basic Compaction
//
// Compact a long agent transcript and print the compression ratio.
// Every line that survives is byte-for-byte identical to the input —
// compaction deletes lines, it never rewrites them.
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
user: hey, quick question about the checkout service
assistant: Sure, what's up?
user: we're seeing duplicate charges on retries
assistant: Let me look at the payment path.
assistant: Found it. In services/checkout/charge.ts the retry wrapper re-invokes
  createCharge() without an idempotency key, so Stripe treats each attempt as a
  new charge.
assistant: The fix is to generate the idempotency key once per checkout attempt
  and pass it through the retry loop:
    const key = \`checkout_\${orderId}_\${attemptId}\`;
    await stripe.charges.create({ ...params }, { idempotencyKey: key });
user: nice. unrelated, what's our node version in CI?
assistant: .github/workflows/ci.yml pins node 20.11.0.
user: and the test command?
assistant: \`bun test --coverage\`, with a 80% line threshold in bunfig.toml.
user: cool. is the staging DB seeded from prod?
assistant: No. scripts/seed-staging.ts generates synthetic rows; there's no prod
  dump anywhere in the pipeline.
user: ok. how many rows?
assistant: 10,000 orders, 2,500 users, 40 merchants.
user: thanks
assistant: No problem.
user: oh also the frontend has a flash of unstyled content on the pricing page
assistant: That's a separate issue — the Tailwind stylesheet is loaded async in
  app/pricing/layout.tsx. Worth a follow-up ticket.
user: yeah later. back to the charges thing — do we need to backfill refunds?
assistant: Yes. Query for charges sharing (order_id, amount) within a 60s window:
    SELECT order_id, COUNT(*) FROM charges
    GROUP BY order_id, amount, date_trunc('minute', created_at)
    HAVING COUNT(*) > 1;
assistant: That returned 47 duplicate pairs over the last 30 days. Each needs a
  refund on the later charge.
user: who owns that runbook?
assistant: docs/runbooks/payment-refunds.md, owned by the payments team.
user: got it. what about alerting so we catch this next time?
assistant: Add a Grafana alert on the rate of charges-per-order exceeding 1.0
  over a 5 minute window. The metric already exists: checkout_charges_total.
user: perfect. anything else in charge.ts that looks off?
assistant: One thing: the catch block swallows StripeCardError without logging,
  so declined cards are invisible in Loki. Add a logger.warn there.
`.trim();

const result = await morph.compact({
  input: transcript,
  query: "duplicate charges on retry in the checkout service",
  compressionRatio: 0.4,
  preserveRecent: 0,
});

console.log(result.output);
console.log("\n---");
console.log(`input tokens:       ${result.usage.input_tokens}`);
console.log(`output tokens:      ${result.usage.output_tokens}`);
console.log(`compression ratio:  ${result.usage.compression_ratio}`);
console.log(`processing time:    ${result.usage.processing_time_ms}ms`);
