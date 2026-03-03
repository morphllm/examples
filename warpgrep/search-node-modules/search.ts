// # Search node_modules
//
// WarpGrep excludes node_modules by default. Pass excludes: []
// to search inside your dependencies — useful for debugging a
// library or understanding how a package works under the hood.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx search.ts
//   MORPH_API_KEY=your-key npx tsx search.ts "your query"

import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const query =
  process.argv[2] || "How does the MorphClient initialize and authenticate?";

console.log(`Searching node_modules for: "${query}"\n`);

// excludes: [] clears the default exclude list, which includes
// node_modules, dist, .git, __pycache__, and ~30 other patterns.
// WarpGrep runs faster with less context, so only do this when
// you actually need to search dependencies.
const result = await morph.warpGrep.execute({
  query,
  repoRoot: ".",
  excludes: [],
});

if (!result.success) {
  console.error("Search failed:", result.error);
  process.exit(1);
}

for (const ctx of result.contexts ?? []) {
  console.log(`--- ${ctx.file} ---`);
  console.log(ctx.content.slice(0, 400));
  if (ctx.content.length > 400) console.log("  ...");
  console.log();
}
