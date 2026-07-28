// # Route, then call
//
// The real pattern: ask /v1/router/multimodel which model to use, then call
// that model. Runs the same prompt under every policy first so you can see the
// knob move the pick, then executes the balanced decision.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx route.ts
//   MORPH_API_KEY=your-key npx tsx route.ts "your own prompt"

const morphKey = process.env.MORPH_API_KEY;
if (!morphKey) {
  console.error("MORPH_API_KEY is not set. Get one at https://morphllm.com/dashboard/api-keys");
  process.exit(1);
}

const POLICIES = ["balanced", "cost_efficient", "capability_heavy", "domain_skills"] as const;
type Policy = (typeof POLICIES)[number];

// Two tiers, cheap and strong, is the shape that holds up in production. The
// whole catalog maximizes how often the model changes, which is what costs you.
const CANDIDATES = ["deepseek-v4-flash", "glm-5.2", "claude-opus-4-8"];

interface RouteResult {
  model: string;
  provider: string;
  difficulty?: string;
  confidence?: number;
  ambiguity?: string;
  ambiguity_confidence?: number;
  domain?: string;
  domain_confidence?: number;
}

// Each provider's own API. The router names the model; you still call it
// yourself, with that provider's key.
const PROVIDERS: Record<string, { envKey: string; call: (model: string, prompt: string, key: string) => Promise<Response> }> = {
  anthropic: {
    envKey: "ANTHROPIC_API_KEY",
    call: (model, prompt, key) =>
      fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json" },
        body: JSON.stringify({ model, max_tokens: 256, messages: [{ role: "user", content: prompt }] }),
      }),
  },
  openai: { envKey: "OPENAI_API_KEY", call: openaiCompatible("https://api.openai.com/v1") },
  deepseek: { envKey: "DEEPSEEK_API_KEY", call: openaiCompatible("https://api.deepseek.com") },
  zai: { envKey: "ZAI_API_KEY", call: openaiCompatible("https://api.z.ai/api/paas/v4") },
  moonshot: { envKey: "MOONSHOT_API_KEY", call: openaiCompatible("https://api.moonshot.cn/v1") },
  gemini: {
    envKey: "GEMINI_API_KEY",
    call: (model, prompt, key) =>
      fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
        method: "POST",
        headers: { "x-goog-api-key": key, "Content-Type": "application/json" },
        body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }),
      }),
  },
};

// No token cap here: the field that sets one differs across the
// OpenAI-compatible providers (max_tokens vs max_completion_tokens).
function openaiCompatible(baseUrl: string) {
  return (model: string, prompt: string, key: string) =>
    fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model, messages: [{ role: "user", content: prompt }] }),
    });
}

async function route(input: string, policy: Policy): Promise<RouteResult> {
  const res = await fetch("https://api.morphllm.com/v1/router/multimodel", {
    method: "POST",
    headers: { Authorization: `Bearer ${morphKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      input,
      allowed_models: CANDIDATES,
      policy,
      default_model: "glm-5.2",
    }),
  });

  if (!res.ok) {
    throw new Error(`multimodel returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }

  return res.json() as Promise<RouteResult>;
}

const prompt = process.argv[2] ?? "Write a distributed rate limiter with sliding windows across 12 regions and prove the invariants";

console.log(`Prompt: ${prompt}`);
console.log(`Candidates: ${CANDIDATES.join(", ")}\n`);

const picks = await Promise.all(
  POLICIES.map(async (policy) => {
    const started = Date.now();
    const result = await route(prompt, policy);
    return { policy, ...result, ms: Date.now() - started };
  })
);

console.table(
  picks.map(({ policy, model, provider, difficulty, ambiguity, domain, ms }) => ({
    policy,
    model,
    provider,
    difficulty: difficulty ?? "-",
    ambiguity: ambiguity ?? "-",
    domain: domain ?? "-",
    ms,
  }))
);

// Second hop: actually call whatever balanced picked.
const decision = picks[0];
const provider = PROVIDERS[decision.provider];
const providerKey = provider && process.env[provider.envKey];

if (!provider) {
  console.log(`\nNo call path wired up for provider "${decision.provider}". Routing decision: ${decision.model}`);
  process.exit(0);
}

if (!providerKey) {
  console.log(`\nRouted to ${decision.model} (${decision.provider}), but ${provider.envKey} is not set — skipping the call.`);
  console.log(`Set ${provider.envKey} to run the second hop.`);
  process.exit(0);
}

console.log(`\nCalling ${decision.model} via ${decision.provider}...`);

const res = await provider.call(decision.model, prompt, providerKey);
const body = await res.text();

if (!res.ok) {
  console.error(`${decision.provider} returned ${res.status}: ${body.slice(0, 300)}`);
  process.exit(1);
}

console.log(`${decision.model} served the request. Response:\n`);
console.log(body.slice(0, 1000));
