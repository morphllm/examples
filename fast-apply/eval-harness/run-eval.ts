// # Fast Apply Eval Harness
//
// Run a tiny exact-match benchmark over several Fast Apply edit tasks.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx run-eval.ts

import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { applyEdit } from "@morphllm/morphsdk";
import { createTwoFilesPatch } from "diff";

const tasksDir = "tasks";
const separator = "\n---CODE_EDIT---\n";

function normalize(code: string): string {
  return code.trimEnd() + "\n";
}

async function loadTask(name: string) {
  const taskDir = join(tasksDir, name);
  const [input, expected, editFile] = await Promise.all([
    readFile(join(taskDir, "input.ts"), "utf-8"),
    readFile(join(taskDir, "expected.ts"), "utf-8"),
    readFile(join(taskDir, "edit.txt"), "utf-8"),
  ]);

  const [instruction, codeEdit] = editFile.split(separator);

  if (!instruction || !codeEdit) {
    throw new Error(`Task ${name} is missing ${separator.trim()} in edit.txt`);
  }

  return {
    name,
    input,
    expected,
    instruction: instruction.trim(),
    codeEdit: codeEdit.trim(),
  };
}

const taskNames = (await readdir(tasksDir, { withFileTypes: true }))
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort();

let passed = 0;

for (const taskName of taskNames) {
  const task = await loadTask(taskName);

  const result = await applyEdit({
    originalCode: task.input,
    instruction: task.instruction,
    codeEdit: task.codeEdit,
  });

  if (!result.success || !result.mergedCode) {
    console.log(`FAIL ${task.name}: ${result.error ?? "missing mergedCode"}`);
    continue;
  }

  const actual = normalize(result.mergedCode);
  const expected = normalize(task.expected);

  if (actual === expected) {
    passed += 1;
    console.log(`PASS ${task.name}`);
    continue;
  }

  console.log(`FAIL ${task.name}`);
  console.log(
    createTwoFilesPatch(
      `${task.name}/expected.ts`,
      `${task.name}/actual.ts`,
      expected,
      actual,
      "Expected",
      "Actual"
    )
  );
}

console.log(`\n${passed}/${taskNames.length} tasks passed`);

if (passed !== taskNames.length) {
  process.exit(1);
}
