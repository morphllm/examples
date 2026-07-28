# Fast Apply

Fast Apply takes an original file and a lazy edit snippet — only the changed lines, with `// ... existing code ...` standing in for everything else — and returns the full merged file. It exists so a frontier model never has to reproduce code it isn't changing: the frontier model writes the small snippet, Fast Apply expands it at 10,500+ tokens/second. The API is OpenAI-compatible at `https://api.morphllm.com/v1`, and a request is a single user message containing `<instruction>`, `<code>`, and `<update>`.

## Models

| Model | Speed | Accuracy | Context | Best for |
|-------|-------|----------|---------|----------|
| `morph-v3-fast` | 10,500+ tok/s | 96% | 262,144 | Real-time edits, editor integrations |
| `morph-v3-large` | 2,500+ tok/s | 98% | 262,144 | Complex multi-region changes |
| `auto` | variable | ~98% | 262,144 | Recommended default — picks between the two |

## Examples

| Example | Language | Description |
|---------|----------|-------------|
| [basic-apply](./basic-apply) | TypeScript, Python | The minimal call: file in, merged file out |
| [streaming-apply](./streaming-apply) | TypeScript | `stream: true`, merged file to stdout, measured tok/s |
| [agent-loop](./agent-loop) | TypeScript | Claude emits a lazy edit, Fast Apply merges it to disk |

## Message format

```
<instruction>I will add error handling to prevent division by zero</instruction>
<code>...the complete original file...</code>
<update>...only the changed regions, with // ... existing code ... markers...</update>
```

`<instruction>` is optional but recommended — a first-person sentence disambiguates edits that could otherwise land in more than one place. `<code>` must be the whole original file. `<update>` should contain only what changes.
