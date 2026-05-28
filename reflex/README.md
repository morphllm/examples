# Reflex: Train and Deploy Text Classifiers

Reflex lets you train small, fast text classifiers (LoRA fine-tuned on ModernBERT) and serve them via API. You describe what you want to classify, Reflex generates training data, trains a model, and gives you an endpoint.

## Quickstart

```bash
# Classify text with a trained reflex
curl -X POST https://api.morphllm.com/v1/reflex/predict \
  -H "Authorization: Bearer $MORPH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "your-model-id", "text": "I need a refund for my order"}'
```

Response:

```json
{
  "label": "complaint",
  "confidence": 0.94,
  "scores": {
    "complaint": 0.94,
    "question": 0.04,
    "praise": 0.02
  }
}
```

## Examples

| Example | Language | Description |
|---------|----------|-------------|
| [basic-classify](./basic-classify.ts) | TypeScript | Classify text with a trained reflex |
| [basic-classify.py](./basic-classify.py) | Python | Same thing in Python |
| [streaming-webhook](./streaming-webhook.ts) | TypeScript | Classify incoming webhooks in real-time |
| [batch-classify](./batch-classify.ts) | TypeScript | Classify a CSV of texts in parallel |

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

**Response:**

```json
{
  "label": "predicted_label",
  "confidence": 0.95,
  "scores": {
    "label_a": 0.95,
    "label_b": 0.03,
    "label_c": 0.02
  }
}
```

**Headers:**

- `Authorization: Bearer <your-api-key>` (required)
- `Content-Type: application/json`

**Model ID:** Find your model ID on the reflex detail page, or set a custom alias in the chat.

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
    return res.json();
  }
}];
```
