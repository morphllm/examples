// # Direct API
//
// Call Morph Fast Apply through the OpenAI-compatible chat completions API.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx apply.ts

import { readFile, writeFile } from "node:fs/promises";
import { createTwoFilesPatch } from "diff";

const apiKey = process.env.MORPH_API_KEY;
const filepath = "src/auth.ts";

if (!apiKey) {
  console.error("Missing MORPH_API_KEY");
  process.exit(1);
}

const originalCode = await readFile(filepath, "utf-8");
const instruction =
  "I am adding a null check after findUserByEmail so login throws Invalid credentials when no user is found.";
const codeEdit = `// ... existing code ...
export async function login(email: string, password: string): Promise<User> {
  const user = await findUserByEmail(email);

  if (!user) {
    throw new Error("Invalid credentials");
  }

  const passwordMatches = await verifyPassword(password, user.passwordHash);
// ... existing code ...`;

const response = await fetch("https://api.morphllm.com/v1/chat/completions", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${apiKey}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "morph-v3-fast",
    messages: [
      {
        role: "user",
        content: `<instruction>${instruction}</instruction>\n<code>${originalCode}</code>\n<update>${codeEdit}</update>`,
      },
    ],
  }),
});

if (!response.ok) {
  const errorText = await response.text();
  console.error(`Fast Apply request failed: ${response.status} ${response.statusText}`);
  console.error(errorText);
  process.exit(1);
}

const completion = (await response.json()) as {
  choices?: Array<{ message?: { content?: string } }>;
};

const mergedCode = completion.choices?.[0]?.message?.content;

if (!mergedCode) {
  console.error("Fast Apply response did not include merged code");
  process.exit(1);
}

await writeFile(filepath, mergedCode);

const diff = createTwoFilesPatch(filepath, filepath, originalCode, mergedCode, "Original", "Modified");

console.log(`Edited ${filepath}`);
console.log("\nDiff:\n");
console.log(diff);
