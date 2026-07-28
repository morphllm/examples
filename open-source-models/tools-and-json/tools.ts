// # Tools and Structured Output
//
// Two things every agent needs: a tool call loop and a response you can parse
// without a regex. Both on the standard OpenAI shapes.
//
// Usage:
//   MORPH_API_KEY=your-key npx tsx tools.ts

import OpenAI from "openai";

if (!process.env.MORPH_API_KEY) {
  console.error("MORPH_API_KEY is not set. Get a key at https://morphllm.com/dashboard/api-keys");
  process.exit(1);
}

const client = new OpenAI({
  apiKey: process.env.MORPH_API_KEY,
  baseURL: "https://api.morphllm.com/v1",
});

const model = process.env.MORPH_MODEL || "morph-glm52-744b";

// --- Part 1: tool calling ---

const tools: OpenAI.Chat.Completions.ChatCompletionTool[] = [
  {
    type: "function",
    function: {
      name: "get_weather",
      description: "Get the current weather for a city",
      parameters: {
        type: "object",
        properties: {
          city: { type: "string", description: "City name" },
          unit: { type: "string", enum: ["celsius", "fahrenheit"] },
        },
        required: ["city"],
      },
    },
  },
];

function getWeather(city: string, unit = "celsius") {
  return { city, unit, temperature: unit === "celsius" ? 17 : 63, conditions: "fog" };
}

const messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[] = [
  { role: "user", content: "What's the weather in San Francisco? Use the tool." },
];

const first = await client.chat.completions.create({ model, messages, tools, max_tokens: 2048 });
const call = first.choices[0].message.tool_calls?.[0];

if (!call) {
  console.error("Model did not call the tool:", first.choices[0].message.content);
  process.exit(1);
}

const args = JSON.parse(call.function.arguments);
console.log(`tool call: ${call.function.name}(${call.function.arguments})`);

const result = getWeather(args.city, args.unit);
messages.push(first.choices[0].message, {
  role: "tool",
  tool_call_id: call.id,
  content: JSON.stringify(result),
});

const second = await client.chat.completions.create({ model, messages, tools, max_tokens: 2048 });
console.log(`answer: ${second.choices[0].message.content}\n`);

// --- Part 2: structured output ---

const report =
  "The login button on /settings does nothing in Safari 17. Users cannot save their profile. " +
  "12 people have reported it since Tuesday and there is no workaround.";

const structured = await client.chat.completions.create({
  model,
  messages: [
    { role: "user", content: `Extract this bug report as JSON:\n\n${report}` },
  ],
  max_tokens: 2048,
  response_format: {
    type: "json_schema",
    json_schema: {
      name: "bug_report",
      strict: true,
      schema: {
        type: "object",
        properties: {
          title: { type: "string" },
          severity: { type: "string", enum: ["low", "medium", "high", "critical"] },
          component: { type: "string" },
          affected_users: { type: "integer" },
        },
        required: ["title", "severity", "component", "affected_users"],
        additionalProperties: false,
      },
    },
  },
});

const parsed = JSON.parse(structured.choices[0].message.content ?? "{}");
console.log("structured output:");
console.log(JSON.stringify(parsed, null, 2));
console.log(`\n--- served by: ${structured.model} ---`);
