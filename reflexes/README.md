# Reflex: Train and Deploy Text Classifiers

Reflex lets you train small, fast text classifiers (LoRA fine-tuned on ModernBERT) and serve them via API. You describe what you want to classify, Reflex generates training data, trains a model, and gives you an endpoint.

## Quickstart

```bash
# Classify text with a trained reflex
curl -X POST https://api.morphllm.com/v1/reflex/predict \
  -H "Authorization: Bearer $MORPH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "user-frustrated", "text": "I need a refund for my order"}'
```

Response:

```json
{
  "model": "user-frustrated",
  "mode": "single_label",
  "classes": [
    { "class_id": 0, "label": "Frustrated", "score": 0.9999, "selected": true },
    { "class_id": 1, "label": "Not Frustrated", "score": 0.0001, "selected": false }
  ],
  "served_by": "reflex",
  "inference_time_ms": 14.5,
  "prefill_tokens": 11
}
```

## Examples

| Example | Language | Description |
|---------|----------|-------------|
| [basic-classify](./basic-classify) | TypeScript / Python | Classify text with a trained reflex |
| [streaming-webhook](./streaming-webhook) | TypeScript | Classify incoming webhooks in real-time |
| [batch-classify](./batch-classify) | TypeScript | Classify a CSV of texts in parallel |

## How it works

1. Go to [morphllm.com/dashboard/reflex](https://morphllm.com/dashboard/reflex)
2. Click **NEW REFLEX +** and describe what you want to classify (or pick a preset)
3. The agent generates training data and trains a LoRA on ModernBERT
4. Once ready, use the API endpoint to classify text at ~10k inferences/sec

## API Reference

### POST /v1/reflex/predict

Classify a text string.

**Request:**

```json
{
  "model": "your-model-id-or-alias",
  "text": "text to classify"
}
```

One text per request — there is no `texts` array.

**Response:**

```json
{
  "model": "your-model-id-or-alias",
  "mode": "single_label",
  "classes": [
    { "class_id": 0, "label": "label_a", "score": 0.95, "selected": true },
    { "class_id": 1, "label": "label_b", "score": 0.03, "selected": false },
    { "class_id": 2, "label": "label_c", "score": 0.02, "selected": false }
  ],
  "served_by": "reflex",
  "inference_time_ms": 12.9,
  "prefill_tokens": 7
}
```

`classes` is ordered by `class_id`, not by score — take the highest `score`, or read the `selected` flags. In `single_label` mode exactly one class is selected; in `multi_label` mode any number can be.

**Headers:**

- `Authorization: Bearer <your-api-key>` (required)
- `Content-Type: application/json`

**Model ID:** Find your model ID on the reflex detail page, or set a custom alias in the chat. Built-in reflexes (`user-frustrated`, `jailbreak`, `domain`, `difficulty`, ...) are callable by name on every account.

## Integration with AI agents

Reflex classifiers are useful as routing/filtering steps inside agent workflows. Add this to your agent's tool definitions:

```typescript
const tools = [{
  name: 'classify_text',
  description: 'Classify text using a trained Morph Reflex model',
  parameters: {
    text: { type: 'string', description: 'The text to classify' }
  },
  execute: async ({ text }) => {
    const res = await fetch('https://api.morphllm.com/v1/reflex/predict', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${process.env.MORPH_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ model: 'your-model-id', text }),
    });
    const { classes } = await res.json();
    return classes.reduce((a, b) => (b.score > a.score ? b : a));
  }
}];
```
