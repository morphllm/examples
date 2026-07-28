// # Classify
//
// Send contrasting prompts to /v1/router/classify and print the three
// classifier heads side by side. One call per prompt, ~50ms each.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx classify.ts
//   MORPH_API_KEY=your-key npx tsx classify.ts "your own prompt"

const apiKey = process.env.MORPH_API_KEY;
if (!apiKey) {
  console.error("MORPH_API_KEY is not set. Get one at https://morphllm.com/dashboard/api-keys");
  process.exit(1);
}

interface Head {
  class_id: number;
  label: string;
  confidence: number;
  meets_threshold: boolean;
}

interface ClassifyResponse {
  classifications: {
    difficulty?: Head;
    ambiguity?: Head;
    domain?: Head;
  };
}

const PROMPTS = [
  "Fix the typo in the README title",
  "Design a multi-region event sourcing system with exactly-once delivery and prove the invariants hold under partition",
  "make it faster",
];

async function classify(input: string): Promise<ClassifyResponse> {
  const res = await fetch("https://api.morphllm.com/v1/router/classify", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ input, classes: ["difficulty", "ambiguity", "domain"] }),
  });

  if (!res.ok) {
    throw new Error(`classify returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }

  return res.json() as Promise<ClassifyResponse>;
}

// A head that missed its confidence threshold carries no signal. For difficulty
// that means needs_info: the prompt is too vague to size.
function show(head: Head | undefined, whenBelow: string): string {
  if (!head) return "-";
  const label = head.meets_threshold ? head.label : whenBelow;
  return `${label} (${head.confidence.toFixed(2)})`;
}

const prompts = process.argv[2] ? [process.argv[2]] : PROMPTS;

const rows = await Promise.all(
  prompts.map(async (input) => {
    const started = Date.now();
    const { classifications } = await classify(input);
    return {
      prompt: input.length > 52 ? `${input.slice(0, 49)}...` : input,
      difficulty: show(classifications.difficulty, "needs_info"),
      ambiguity: show(classifications.ambiguity, "no signal"),
      domain: show(classifications.domain, "no signal"),
      ms: Date.now() - started,
    };
  })
);

console.table(rows);
