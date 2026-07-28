# Fast Apply: Agent Loop

Claude decides what to change and writes only the changed lines; `morph-v3-fast` merges that snippet into the real file on disk.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
```

Pass your own request:

```bash
MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "add a request timeout"
```

`server.ts` is a fixture and is rewritten in place. Reset it with `git checkout server.ts`.

## What it does

1. Gives Claude one tool, `edit_file`, taking `instructions` and `code_edit`
2. The system prompt tells Claude to emit **lazy** edits — only changed lines, with `// ... existing code ...` for everything else — and that a separate merge model will apply them
3. Forces the tool call with `tool_choice`, then reads `instructions` and `code_edit` off the `tool_use` block
4. Sends `(original file, snippet)` to `morph-v3-fast` as `<instruction>` / `<code>` / `<update>`
5. Writes the merged file back to disk

## Why not have Claude rewrite the whole file

**Cost.** Frontier output tokens are the expensive ones. A 20-line snippet against a 500-line file is roughly a 25x reduction in frontier output for the same edit, and the merge that expands it back runs on a 7B model at a fraction of the price.

**Latency.** Frontier models generate at tens of tokens per second; `morph-v3-fast` does 10,500+. Regenerating 500 unchanged lines on the frontier model is dead time. Producing 20 lines and expanding them on the fast model is faster end to end, and the merge streams.

**Truncation.** Full-file rewrites scale output length with file size, so large files run into `max_tokens` and come back cut off mid-function — a corrupted write, not a failed one. Lazy edit length scales with the size of the change instead, so it stays small regardless of the file.

The alternative — search-and-replace — needs one exact-match tool call per edit region and breaks on whitespace, reordering, and ambiguous matches. Fast Apply handles every edit to a file in one call.
