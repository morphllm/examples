import { serve } from 'bun';

const MORPH_API_KEY = process.env.MORPH_API_KEY!;
const MODEL_ID = process.env.REFLEX_MODEL_ID || 'user-frustrated';

interface ReflexResponse {
  model: string;
  mode: 'single_label' | 'multi_label';
  classes: Array<{ class_id: number; label: string; score: number; selected: boolean }>;
  inference_time_ms: number;
}

async function classify(text: string): Promise<ReflexResponse> {
  const res = await fetch('https://api.morphllm.com/v1/reflex/predict', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${MORPH_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ model: MODEL_ID, text }),
  });

  if (!res.ok) {
    throw new Error(`Reflex API error: ${res.status} ${await res.text()}`);
  }

  return res.json() as Promise<ReflexResponse>;
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
    const top = result.classes.reduce((a, b) => (b.score > a.score ? b : a));

    return Response.json({
      original: text,
      label: top.label,
      confidence: top.score,
      classes: result.classes,
      inference_time_ms: result.inference_time_ms,
    });
  },
});

console.log('Webhook server running on http://localhost:3000');
console.log('POST any JSON with a "text" field to classify it');
