# # Basic Apply
#
# The minimal Fast Apply call. Read a file, send it plus a lazy edit
# snippet, get the merged file back.
#
# Usage:
#   MORPH_API_KEY=your-key python3 apply.py

import os
import sys
from pathlib import Path

from openai import OpenAI

api_key = os.environ.get("MORPH_API_KEY")
if not api_key:
    sys.exit("MORPH_API_KEY is not set")

morph = OpenAI(api_key=api_key, base_url="https://api.morphllm.com/v1")

original_code = (Path(__file__).parent / "cart.ts").read_text()

instruction = "I will reject negative discounts and clamp them to 100%"

# Only the changed region. Everything else is elided with a marker.
code_edit = """// ... existing code ...
export function applyDiscount(total: number, percentOff: number): number {
  if (percentOff < 0) {
    throw new Error("percentOff must be non-negative");
  }
  const clamped = Math.min(percentOff, 100);
  return total * (1 - clamped / 100);
}
// ... existing code ..."""

response = morph.chat.completions.create(
    model="morph-v3-fast",
    messages=[
        {
            "role": "user",
            "content": f"<instruction>{instruction}</instruction>\n<code>{original_code}</code>\n<update>{code_edit}</update>",
        }
    ],
)

print(response.choices[0].message.content)
