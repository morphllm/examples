import { serve } from 'bun';

const MORPH_API_KEY = process.env.MORPH_API_KEY!;
const MODEL_ID = process.env.REFLEX_MODEL_ID || 'your-model-id';

async function classify(text: string) {
  const res = await fetch('https://api.morphllm.com/v1/reflex/predict', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${MORPH_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ model: MODEL_ID, text }),
  });
  return res.json();
}

serve({
  port: 3000,
  async fetch(req) {
    if (req.method !== 'POST') {
      return new Response('POST only', { status: 405 });
    }

    const body = await req.json();
    const text = body.text || body.message || body.content;

    if (!text) {
      return Response.json({ error: 'No text field found' }, { status: 400 });
    }

    const result = await classify(text);

    return Response.json({
      original: text,
      classification: result,
    });
  },
});

console.log('Webhook server running on http://localhost:3000');
console.log('POST any JSON with a "text" field to classify it');
