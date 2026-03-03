// # Search a GitHub Repo
//
// Search any public GitHub repository without cloning it.
// WarpGrep indexes the repo on Morph's servers — no local ripgrep needed.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx search.ts
//   MORPH_API_KEY=your-key npx tsx search.ts "your query" owner/repo

import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

// Parse CLI args — defaults to searching Next.js for routing logic.
const query = process.argv[2] || "How does the App Router handle parallel routes?";
const repo = process.argv[3] || "vercel/next.js";

console.log(`Searching ${repo} for: "${query}"\n`);

const result = await morph.warpGrep.searchGitHub({
  query,
  github: repo,
});

if (!result.success) {
  console.error("Search failed:", result.error);
  process.exit(1);
}

for (const ctx of result.contexts ?? []) {
  console.log(`--- ${ctx.file} ---`);
  console.log(ctx.content);
  console.log();
}
