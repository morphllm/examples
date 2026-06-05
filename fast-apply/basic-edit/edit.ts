// # Basic Edit
//
// Apply a targeted partial edit to a TypeScript file with Fast Apply.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx edit.ts

import { MorphClient } from "@morphllm/morphsdk";

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY });

const result = await morph.fastApply.execute(
  {
    target_filepath: "src/auth.ts",
    instruction:
      "I am adding a null check after findUserByEmail so login throws Invalid credentials when no user is found.",
    code_edit: `// ... existing code ...
export async function login(email: string, password: string): Promise<User> {
  const user = await findUserByEmail(email);

  if (!user) {
    throw new Error("Invalid credentials");
  }

  const passwordMatches = await verifyPassword(password, user.passwordHash);
// ... existing code ...`,
  },
  { baseDir: "." }
);

if (!result.success) {
  console.error("Edit failed:", result.error);
  process.exit(1);
}

console.log(`Edited ${result.filepath}`);
console.log(
  `Changes: +${result.changes.linesAdded} -${result.changes.linesRemoved} ~${result.changes.linesModified}`
);

if (result.udiff) {
  console.log("\nDiff:\n");
  console.log(result.udiff);
}
