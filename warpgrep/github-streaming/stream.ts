// # Stream a GitHub Search
//
// Same as github-search, but streams each turn as it happens.
// Useful for showing progress in a UI — the user sees what
// WarpGrep is doing instead of staring at a spinner.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx stream.ts
//   MORPH_API_KEY=your-key npx tsx stream.ts "your query" owner/repo

import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const query = process.argv[2] || "Find how middleware chains work";
const repo = process.argv[3] || "expressjs/express";

console.log(`Streaming search on ${repo}: "${query}"\n`);

// searchGitHub with streamSteps returns an async generator.
const stream = morph.warpGrep.searchGitHub({
  query,
  github: repo,
  streamSteps: true,
});

// Each yield is one agent turn — typically 2-4 turns total.
let result;
for (;;) {
  const { value, done } = await stream.next();
  if (done) {
    result = value;
    break;
  }

  console.log(`Turn ${value.turn}:`);
  for (const call of value.toolCalls) {
    const args = Object.entries(call.arguments)
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
      .join(", ");
    console.log(`  ${call.name}(${args})`);
  }
  console.log();
}

if (!result.success) {
  console.error("Search failed:", result.error);
  process.exit(1);
}

console.log(`Found ${result.contexts?.length ?? 0} files:\n`);
for (const ctx of result.contexts ?? []) {
  console.log(`--- ${ctx.file} ---`);
  console.log(ctx.content.slice(0, 300));
  if (ctx.content.length > 300) console.log("  ...");
  console.log();
}
