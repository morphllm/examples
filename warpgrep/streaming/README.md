# WarpGrep: Streaming

Watch WarpGrep's search progress in real-time. See each turn's tool calls (grep, read, list_directory) as they happen, then get the final results.

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=your-key npx tsx stream.ts
```

## Example output

```
Searching for: "Find where errors are handled"

Turn 1:
  grep(pattern="(error|Error|ERROR)", sub_dir="src/")
  list_directory(path="src/")

Turn 2:
  read(path="src/middleware/error-handler.ts", lines="1-50")
  read(path="src/utils/errors.ts", lines="1-80")

Turn 3:
  finish(...)

Found 2 relevant files:
--- src/middleware/error-handler.ts ---
...
```

## When to use streaming

- **CLI tools**: Show users that something is happening during the search
- **Web UIs**: Display a progress indicator or live tool call log
- **Debugging**: See exactly what WarpGrep is searching for
