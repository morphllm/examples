// # Basic Search
//
// The simplest way to search a codebase with WarpGrep.
// Query in, results out — no streaming, no agent loop.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx search.ts
//   MORPH_API_KEY=your-key npx tsx search.ts "your custom query"

import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const query = process.argv[2] || "Find the main entry point of this project";

console.log(`Searching for: "${query}"\n`);

const result = await morph.warpGrep.execute({
  query,
  repoRoot: ".",
});

if (!result.success) {
  console.error("Search failed:", result.error);
  process.exit(1);
}

console.log(`Found ${result.contexts?.length ?? 0} relevant files:\n`);

for (const ctx of result.contexts ?? []) {
  console.log(`--- ${ctx.file} ---`);
  console.log(ctx.content);
  console.log();
}
