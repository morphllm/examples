import { readFileSync, writeFileSync } from 'fs';

const MORPH_API_KEY = process.env.MORPH_API_KEY!;
const MODEL_ID = process.env.REFLEX_MODEL_ID || 'user-frustrated';
const CONCURRENCY = 10;

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

async function batchClassify(texts: string[]) {
  const results: Array<{ text: string; label: string; confidence: number }> = [];

  for (let i = 0; i < texts.length; i += CONCURRENCY) {
    const batch = texts.slice(i, i + CONCURRENCY);
    const batchResults = await Promise.all(
      batch.map(async (text) => {
        const result = await classify(text);
        const top = result.classes.reduce((a, b) => (b.score > a.score ? b : a));
        return { text, label: top.label, confidence: top.score };
      })
    );
    results.push(...batchResults);
    console.log(`Classified ${results.length}/${texts.length}`);
  }

  return results;
}

async function main() {
  const inputFile = process.argv[2];
  if (!inputFile) {
    console.log('Usage: MORPH_API_KEY=... npx tsx batch.ts input.csv');
    console.log('CSV should have a "text" column');
    process.exit(1);
  }

  const csv = readFileSync(inputFile, 'utf-8');
  const lines = csv.trim().split('\n');
  const header = lines[0].split(',');
  const textCol = header.findIndex(h => h.trim().toLowerCase() === 'text');

  if (textCol === -1) {
    console.error('No "text" column found in CSV');
    process.exit(1);
  }

  const texts = lines.slice(1).map(line => {
    const cols = line.split(',');
    return cols[textCol].replace(/^"|"$/g, '').trim();
  }).filter(Boolean);

  console.log(`Classifying ${texts.length} texts with concurrency ${CONCURRENCY}...`);
  const results = await batchClassify(texts);

  const outputFile = inputFile.replace('.csv', '-classified.csv');
  const output = 'text,label,confidence\n' +
    results.map(r => `"${r.text.replace(/"/g, '""')}",${r.label},${r.confidence.toFixed(4)}`).join('\n');

  writeFileSync(outputFile, output);
  console.log(`Results written to ${outputFile}`);
}

main().catch(console.error);
