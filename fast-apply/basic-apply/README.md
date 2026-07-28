# Fast Apply: Basic Apply

Merge a lazy edit snippet into a source file. One request, merged file out.

## Setup

```bash
npm install
# or, for Python
pip3 install -r requirements.txt
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx apply.ts
MORPH_API_KEY=your-key python3 apply.py
```

Both read `cart.ts` from disk and print the merged result to stdout. Neither writes the file back.

## What it does

1. Points an `openai` client at `https://api.morphllm.com/v1`
2. Sends one user message containing `<instruction>`, `<code>` (the whole original file), and `<update>` (only the changed function, with `// ... existing code ...` for everything else)
3. Prints `choices[0].message.content` — the complete merged file

The `<update>` block is deliberately incomplete. `morph-v3-fast` resolves the markers against the original and returns the full file with the edit applied, preserving the parts you elided.
