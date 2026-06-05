# Fast Apply: Eval Harness

Run a tiny exact-match benchmark for Fast Apply edits. Each task includes an input file, a partial `codeEdit`, and the expected merged output.

## Setup

```bash
npm install
```

Use `.env.example` as a template, then export your key or pass it inline:

```bash
MORPH_API_KEY=your-key npx tsx run-eval.ts
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx run-eval.ts
```

## What it does

1. Loads every task in `tasks/`
2. Calls `applyEdit()` with `input.ts`, the instruction, and the partial `codeEdit`
3. Compares `mergedCode` to `expected.ts`
4. Prints pass/fail results and a diff for failures

This is a small harness, not a full benchmark suite. It shows how to evaluate Fast Apply behavior in a repeatable way before putting it inside a larger coding-agent workflow.
