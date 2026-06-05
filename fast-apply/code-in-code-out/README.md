# Fast Apply: Code In, Code Out

Apply a focused edit with Fast Apply while managing file reads and writes yourself. This is useful in sandboxes, harnesses, or hosted environments where your app controls the filesystem.

## Setup

```bash
npm install
```

Use `.env.example` as a template, then export your key or pass it inline:

```bash
MORPH_API_KEY=your-key npx tsx apply.ts
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx apply.ts
```

## What it does

1. Reads `src/auth.ts`
2. Calls `applyEdit()` with the original file contents and a partial `codeEdit`
3. Receives `mergedCode` instead of letting the SDK write to disk
4. Writes the merged code itself and prints the diff

The example fixes a missing null check in `login()`. Use this pattern when Fast Apply should return code for a caller, sandbox, or harness to store explicitly.
