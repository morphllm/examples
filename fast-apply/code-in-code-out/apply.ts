// # Code In, Code Out
//
// Apply a Fast Apply edit while managing file reads and writes yourself.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx apply.ts

import { readFile, writeFile } from "node:fs/promises";
import { applyEdit } from "@morphllm/morphsdk";

const filepath = "src/auth.ts";
const originalCode = await readFile(filepath, "utf-8");

const result = await applyEdit({
  originalCode,
  instruction:
    "I am adding a null check after findUserByEmail so login throws Invalid credentials when no user is found.",
  codeEdit: `// ... existing code ...
export async function login(email: string, password: string): Promise<User> {
  const user = await findUserByEmail(email);

  if (!user) {
    throw new Error("Invalid credentials");
  }

  const passwordMatches = await verifyPassword(password, user.passwordHash);
// ... existing code ...`,
});

if (!result.success || !result.mergedCode) {
  console.error("Edit failed:", result.error);
  process.exit(1);
}

await writeFile(filepath, result.mergedCode);

console.log(`Edited ${filepath}`);
console.log(
  `Changes: +${result.changes.linesAdded} -${result.changes.linesRemoved} ~${result.changes.linesModified}`
);

if (result.udiff) {
  console.log("\nDiff:\n");
  console.log(result.udiff);
}
