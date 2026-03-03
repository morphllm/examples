// # Streaming Search
//
// Watch WarpGrep search in real-time. Each turn shows what tools
// it's calling (grep, read, list_directory) before the final results.
// Useful for building UIs that show search progress.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx stream.ts
//   MORPH_API_KEY=your-key npx tsx stream.ts "your query"

import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const query = process.argv[2] || "Find where errors are handled";

console.log(`Searching for: "${query}"\n`);

const stream = morph.warpGrep.execute({
  query,
  repoRoot: ".",
  streamSteps: true,
});

// Stream each turn as it happens
let result;
for (;;) {
  const { value, done } = await stream.next();
  if (done) {
    result = value;
    break;
  }

  // Show what WarpGrep is doing
  console.log(`Turn ${value.turn}:`);
  for (const call of value.toolCalls) {
    const args = Object.entries(call.arguments)
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
      .join(", ");
    console.log(`  ${call.name}(${args})`);
  }
  console.log();
}

// Print final results
if (!result.success) {
  console.error("Search failed:", result.error);
  process.exit(1);
}

console.log(`\nFound ${result.contexts?.length ?? 0} relevant files:\n`);

for (const ctx of result.contexts ?? []) {
  console.log(`--- ${ctx.file} ---`);
  console.log(ctx.content.slice(0, 500));
  if (ctx.content.length > 500) console.log("  ...(truncated)");
  console.log();
}
