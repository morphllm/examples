# # Basic Chat
#
# The OpenAI SDK pointed at Morph. One completion, no streaming.
# Prints which model actually served the request and what it cost in tokens.
#
# Usage:
#   MORPH_API_KEY=your-key python chat.py
#   MORPH_API_KEY=your-key python chat.py "your prompt"

import os
import sys

from openai import OpenAI

if not os.environ.get("MORPH_API_KEY"):
    sys.exit("MORPH_API_KEY is not set. Get a key at https://morphllm.com/dashboard/api-keys")

client = OpenAI(
    api_key=os.environ["MORPH_API_KEY"],
    base_url="https://api.morphllm.com/v1",
)

model = os.environ.get("MORPH_MODEL", "morph-glm52-744b")
prompt = sys.argv[1] if len(sys.argv) > 1 else "Write a Python function that debounces an async function."

response = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": "You are a senior backend engineer. Be concise."},
        {"role": "user", "content": prompt},
    ],
    max_tokens=2048,
    temperature=0.2,
)

print(response.choices[0].message.content)

usage = response.usage
print(f"\n--- requested: {model} | served by: {response.model} ---")
print(f"prompt: {usage.prompt_tokens}  completion: {usage.completion_tokens}  total: {usage.total_tokens}")

details = getattr(usage, "prompt_tokens_details", None)
cached = getattr(details, "cached_tokens", None) if details else None
if cached:
    print(f"cached prompt tokens: {cached}")
