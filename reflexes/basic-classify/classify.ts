const MORPH_API_KEY = process.env.MORPH_API_KEY;
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

function topClass(res: ReflexResponse) {
  return res.classes.reduce((a, b) => (b.score > a.score ? b : a));
}

async function main() {
  const texts = [
    'I need a refund for my order, this is unacceptable',
    'How do I reset my password?',
    'Your product is amazing, saved me hours of work',
    'Can you add dark mode support?',
  ];

  for (const text of texts) {
    const result = await classify(text);
    const top = topClass(result);
    console.log(`"${text}"`);
    console.log(`  → ${top.label} (${(top.score * 100).toFixed(1)}%)\n`);
  }
}

main().catch(console.error);
