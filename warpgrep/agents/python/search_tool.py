"""
WarpGrep as a tool for OpenAI, Anthropic, and Gemini agents.

Shows how to wire WarpGrep's `search()` into the tool-calling loop
of each major provider. Requires: openai, anthropic, google-genai.

Usage:
    MORPH_API_KEY=... OPENAI_API_KEY=... ANTHROPIC_API_KEY=... GOOGLE_API_KEY=... python search_tool.py
"""

import json
import os

from search import search

# ── Shared ───────────────────────────────────────────────────────────────────

PROMPT = (
    "Use the codebase_search tool to find how the WarpGrep agent loop "
    "works in this repo, then summarize what you found."
)

TOOL_DESCRIPTION = (
    "Search a codebase using WarpGrep. Returns relevant file snippets "
    "matching the natural-language query."
)


def format_results(results: list[dict]) -> str:
    """Turn search results into a readable string for the model."""
    if not results:
        return "No results found."
    parts = []
    for r in results:
        parts.append(f"--- {r['path']} ---\n{r['content'][:1000]}")
    return "\n\n".join(parts)


# ── OpenAI ───────────────────────────────────────────────────────────────────


def run_gpt():
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    tools = [
        {
            "type": "function",
            "function": {
                "name": "codebase_search",
                "description": TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Natural-language search query"}},
                    "required": ["query"],
                },
            },
        }
    ]

    messages = [{"role": "user", "content": PROMPT}]
    response = client.chat.completions.create(model="gpt-4o-mini", messages=messages, tools=tools)

    # Handle tool call
    tool_call = response.choices[0].message.tool_calls[0]
    query = json.loads(tool_call.function.arguments)["query"]
    print(f"  Tool call: codebase_search({query!r})")

    results = search(query, ".")
    result_text = format_results(results)

    messages.append(response.choices[0].message)
    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_text})

    final = client.chat.completions.create(model="gpt-4o-mini", messages=messages)
    return final.choices[0].message.content


# ── Anthropic ────────────────────────────────────────────────────────────────


def run_claude():
    import anthropic

    client = anthropic.Anthropic()

    tools = [
        {
            "name": "codebase_search",
            "description": TOOL_DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Natural-language search query"}},
                "required": ["query"],
            },
        }
    ]

    messages = [{"role": "user", "content": PROMPT}]

    # Loop until we get a text response (Claude may chain multiple tool calls)
    for _ in range(3):
        response = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=1024, messages=messages, tools=tools
        )

        # Extract text and tool_use blocks
        text_blocks = [b for b in response.content if hasattr(b, "text") and b.type == "text"]
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        # If no tool use, return text
        if not tool_uses:
            return text_blocks[0].text if text_blocks else "(no response)"

        # Execute each tool call
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tool_use in tool_uses:
            query = tool_use.input["query"]
            print(f"  Tool call: codebase_search({query!r})")
            results = search(query, ".")
            tool_results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": format_results(results)})
        messages.append({"role": "user", "content": tool_results})

    # Exhausted tool rounds — ask for a summary without tools
    messages.append({"role": "user", "content": "Please summarize what you found so far."})
    final = client.messages.create(
        model="claude-sonnet-4-20250514", max_tokens=1024, messages=messages
    )
    return final.content[0].text


# ── Gemini ───────────────────────────────────────────────────────────────────


def run_gemini():
    from google import genai
    from google.genai import types

    client = genai.Client()

    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="codebase_search",
                description=TOOL_DESCRIPTION,
                parameters=types.Schema(
                    type="OBJECT",
                    properties={"query": types.Schema(type="STRING", description="Natural-language search query")},
                    required=["query"],
                ),
            )
        ]
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=PROMPT,
        config=types.GenerateContentConfig(tools=[tool]),
    )

    # Handle function call
    fc = response.candidates[0].content.parts[0].function_call
    query = fc.args["query"]
    print(f"  Tool call: codebase_search({query!r})")

    results = search(query, ".")
    result_text = format_results(results)

    final = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text=PROMPT)]),
            response.candidates[0].content,
            types.Content(
                role="user",
                parts=[types.Part.from_function_response(name="codebase_search", response={"result": result_text})],
            ),
        ],
        config=types.GenerateContentConfig(tools=[tool]),
    )
    return final.text


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not os.environ.get("MORPH_API_KEY"):
        print("Error: set MORPH_API_KEY environment variable")
        raise SystemExit(1)

    for name, fn in [("GPT-4o-mini", run_gpt), ("Claude Sonnet", run_claude), ("Gemini Flash", run_gemini)]:
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")
        try:
            result = fn()
            print(f"\n{result}")
        except Exception as e:
            print(f"\n  Skipped: {e}")
