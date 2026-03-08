"""Multi-provider LLM adapter for the PR review agent.

Supports Anthropic (Claude), OpenAI (GPT), and Google (Gemini) with a
unified interface. Each provider handles its own message format, tool
calling conventions, and JSON extraction.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Unified data classes
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """Provider-agnostic representation of a single tool call."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMUsage:
    """Token usage from a single LLM response."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass
class LLMResponse:
    """Provider-agnostic LLM response."""

    text_parts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    stop_reason: str = ""
    raw: Any = None  # Original provider response for edge cases


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """Interface every provider must implement."""

    rate_limit_exception: type[Exception]

    def chat(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 64000,
        model: str | None = None,
    ) -> LLMResponse:
        """Send a chat completion request with optional tool definitions."""
        ...

    def extract_json(
        self,
        *,
        messages: list[dict],
        json_schema: dict,
        max_tokens: int = 8000,
        model: str | None = None,
    ) -> LLMResponse:
        """Request structured JSON output conforming to *json_schema*."""
        ...

    def format_tool_result(self, tool_call: ToolCall, content: str, *, is_error: bool = False) -> dict:
        """Format a tool execution result for the next message turn."""
        ...

    def format_assistant_message(self, response: LLMResponse) -> dict:
        """Format the assistant's response for appending to the messages list."""
        ...

    def convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert Anthropic-style tool definitions to this provider's format."""
        ...


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Adapter for the Anthropic Messages API (Claude)."""

    rate_limit_exception: type[Exception]

    def __init__(self, api_key: str):
        import anthropic
        import httpx

        self._client = anthropic.Anthropic(
            api_key=api_key,
            timeout=httpx.Timeout(600.0, connect=30.0),
        )
        self.rate_limit_exception = anthropic.RateLimitError

    # -- public API --

    def chat(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 64000,
        model: str | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = dict(
            model=model or "claude-opus-4-6",
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            temperature=1,
            system=system,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools  # Already in Anthropic format
        raw = self._client.messages.create(**kwargs)
        return self._parse(raw)

    def extract_json(
        self,
        *,
        messages: list[dict],
        json_schema: dict,
        max_tokens: int = 8000,
        model: str | None = None,
    ) -> LLMResponse:
        raw = self._client.messages.create(
            model=model or "claude-opus-4-6",
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            temperature=1,
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": json_schema}},
        )
        return self._parse(raw)

    def format_tool_result(self, tool_call: ToolCall, content: str, *, is_error: bool = False) -> dict:
        d: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_call.id,
            "content": content,
        }
        if is_error:
            d["is_error"] = True
        return d

    def format_assistant_message(self, response: LLMResponse) -> dict:
        return {"role": "assistant", "content": response.raw.content}

    def convert_tools(self, tools: list[dict]) -> list[dict]:
        # Tools are already in Anthropic format (name, description, input_schema)
        return tools

    # -- internal --

    @staticmethod
    def _parse(raw) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in raw.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=block.input)
                )

        return LLMResponse(
            text_parts=text_parts,
            tool_calls=tool_calls,
            usage=LLMUsage(
                input_tokens=raw.usage.input_tokens,
                output_tokens=raw.usage.output_tokens,
                cache_read_tokens=getattr(raw.usage, "cache_read_input_tokens", 0) or 0,
            ),
            stop_reason=raw.stop_reason,
            raw=raw,
        )


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class OpenAIProvider:
    """Adapter for the OpenAI Chat Completions API."""

    rate_limit_exception: type[Exception]

    def __init__(self, api_key: str):
        import openai

        self._client = openai.OpenAI(api_key=api_key, timeout=600.0)
        self.rate_limit_exception = openai.RateLimitError

    # -- public API --

    def chat(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 64000,
        model: str | None = None,
    ) -> LLMResponse:
        oai_messages = self._inject_system(messages, system)
        kwargs: dict[str, Any] = dict(
            model=model or "gpt-5.4",
            max_completion_tokens=max_tokens,
            messages=oai_messages,
        )
        if tools:
            kwargs["tools"] = self.convert_tools(tools)
        raw = self._client.chat.completions.create(**kwargs)
        return self._parse(raw)

    def extract_json(
        self,
        *,
        messages: list[dict],
        json_schema: dict,
        max_tokens: int = 8000,
        model: str | None = None,
    ) -> LLMResponse:
        raw = self._client.chat.completions.create(
            model=model or "gpt-5.4",
            max_completion_tokens=max_tokens,
            messages=self._to_oai_messages(messages),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "review_result",
                    "strict": True,
                    "schema": json_schema,
                },
            },
        )
        return self._parse(raw)

    def format_tool_result(self, tool_call: ToolCall, content: str, *, is_error: bool = False) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": content,
        }

    def format_assistant_message(self, response: LLMResponse) -> dict:
        msg: dict[str, Any] = {"role": "assistant"}
        raw_msg = response.raw.choices[0].message

        # Preserve content (may be None when only tool calls)
        msg["content"] = raw_msg.content

        # Preserve tool_calls in the exact format OpenAI expects
        if raw_msg.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in raw_msg.tool_calls
            ]
        return msg

    def convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert Anthropic tool defs (name, description, input_schema) to OpenAI function tools."""
        oai_tools = []
        for t in tools:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
            })
        return oai_tools

    # -- internal --

    @staticmethod
    def _inject_system(messages: list[dict], system: str) -> list[dict]:
        """Prepend system message and convert tool results to OpenAI format."""
        oai = [{"role": "system", "content": system}]
        for m in messages:
            role = m.get("role", "")
            content = m.get("content")

            if role == "user" and isinstance(content, list):
                # Tool result list -> individual tool messages
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        # Anthropic format
                        oai.append({
                            "role": "tool",
                            "tool_call_id": item["tool_use_id"],
                            "content": item.get("content", ""),
                        })
                    elif isinstance(item, dict) and item.get("role") == "tool":
                        # Already in OpenAI format
                        oai.append(item)
                    else:
                        oai.append({"role": "user", "content": str(item)})
            elif role == "assistant" and not isinstance(content, str):
                # Already formatted via format_assistant_message; pass through
                oai.append(m)
            else:
                oai.append(m)
        return oai

    @staticmethod
    def _to_oai_messages(messages: list[dict]) -> list[dict]:
        """Minimal conversion for extract_json (no tools, no system needed)."""
        return messages

    @staticmethod
    def _parse(raw) -> LLMResponse:
        choice = raw.choices[0]
        msg = choice.message

        text_parts = [msg.content] if msg.content else []
        tool_calls: list[ToolCall] = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(
                    ToolCall(id=tc.id, name=tc.function.name, input=args)
                )

        usage = LLMUsage()
        if raw.usage:
            usage = LLMUsage(
                input_tokens=raw.usage.prompt_tokens or 0,
                output_tokens=raw.usage.completion_tokens or 0,
            )

        return LLMResponse(
            text_parts=text_parts,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=choice.finish_reason or "",
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Google Gemini  (google-genai SDK, *not* the old google-generativeai)
# ---------------------------------------------------------------------------


class GeminiProvider:
    """Adapter for Google Gemini via the google.genai SDK."""

    rate_limit_exception: type[Exception]

    def __init__(self, api_key: str):
        from google import genai
        from google.genai import types
        from google.api_core import exceptions as gapi_exceptions

        self._genai = genai
        self._types = types
        self._client = genai.Client(api_key=api_key)
        self.rate_limit_exception = gapi_exceptions.ResourceExhausted

    # -- public API --

    def chat(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 64000,
        model: str | None = None,
    ) -> LLMResponse:
        types = self._types
        gemini_contents = self._to_gemini_contents(messages)

        config_kwargs: dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "system_instruction": system,
        }
        if tools:
            config_kwargs["tools"] = self._to_gemini_tools(tools)

        config = types.GenerateContentConfig(**config_kwargs)
        raw = self._client.models.generate_content(
            model=model or "gemini-3.1-pro-preview",
            contents=gemini_contents,
            config=config,
        )
        return self._parse(raw)

    def extract_json(
        self,
        *,
        messages: list[dict],
        json_schema: dict,
        max_tokens: int = 8000,
        model: str | None = None,
    ) -> LLMResponse:
        types = self._types
        gemini_contents = self._to_gemini_contents(messages)

        # Convert JSON Schema to Gemini format (uppercase types, etc.)
        gemini_schema = self._convert_schema_for_gemini(json_schema)

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            response_schema=gemini_schema,
        )

        raw = self._client.models.generate_content(
            model=model or "gemini-3.1-pro-preview",
            contents=gemini_contents,
            config=config,
        )
        return self._parse(raw)

    def format_tool_result(self, tool_call: ToolCall, content: str, *, is_error: bool = False) -> dict:
        """Return a serializable dict that _to_gemini_contents can convert."""
        return {
            "_gemini_function_response": True,
            "name": tool_call.name,
            "response": {"result": content, "is_error": is_error},
        }

    def format_assistant_message(self, response: LLMResponse) -> dict:
        """Store raw Gemini parts so we can reconstruct Content objects later."""
        return {
            "role": "assistant",
            "_gemini_raw_parts": response.raw.candidates[0].content.parts if response.raw.candidates else [],
        }

    def convert_tools(self, tools: list[dict]) -> list[dict]:
        # Stored in Anthropic format; actual conversion happens in _to_gemini_tools
        return tools

    # -- internal: message conversion --

    def _to_gemini_contents(self, messages: list[dict]) -> list:
        """Convert our message list into Gemini Content objects."""
        types = self._types
        contents = []

        for m in messages:
            role = m.get("role", "")

            # Assistant message with raw Gemini parts (from format_assistant_message)
            if role == "assistant" and "_gemini_raw_parts" in m:
                contents.append(
                    types.Content(role="model", parts=m["_gemini_raw_parts"])
                )
                continue

            # User message containing tool results (list of dicts)
            content_val = m.get("content")
            if role == "user" and isinstance(content_val, list):
                parts = []
                for item in content_val:
                    if isinstance(item, dict) and item.get("_gemini_function_response"):
                        parts.append(
                            types.Part.from_function_response(
                                name=item["name"],
                                response=item["response"],
                            )
                        )
                    elif isinstance(item, dict) and item.get("type") == "tool_result":
                        # Anthropic-format fallback (shouldn't happen after refactor, but be safe)
                        parts.append(
                            types.Part.from_function_response(
                                name=item.get("_tool_name", "unknown"),
                                response={"result": item.get("content", "")},
                            )
                        )
                    else:
                        parts.append(types.Part.from_text(text=str(item)))
                contents.append(types.Content(role="user", parts=parts))
                continue

            # Plain text messages
            gemini_role = "model" if role == "assistant" else "user"
            text = content_val if isinstance(content_val, str) else str(content_val)
            contents.append(
                types.Content(
                    role=gemini_role,
                    parts=[types.Part.from_text(text=text)],
                )
            )

        return contents

    def _to_gemini_tools(self, tools: list[dict]) -> list:
        """Convert Anthropic-style tool defs to Gemini function declarations."""
        types = self._types
        declarations = []

        for t in tools:
            schema = t.get("input_schema", {})
            # Gemini uses uppercase type names and slightly different schema format
            converted_schema = self._convert_schema_for_gemini(schema)

            declarations.append(
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t.get("description", ""),
                    parameters=converted_schema,
                )
            )

        return [types.Tool(function_declarations=declarations)]

    @staticmethod
    def _convert_schema_for_gemini(schema: dict) -> dict:
        """Convert JSON Schema to Gemini-compatible schema.

        Gemini expects uppercase type names (STRING, OBJECT, ARRAY, etc.)
        and uses a slightly different format.
        """
        if not schema:
            return schema

        result = {}
        type_map = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }

        if "type" in schema:
            result["type"] = type_map.get(schema["type"], schema["type"])

        if "description" in schema:
            result["description"] = schema["description"]

        if "properties" in schema:
            result["properties"] = {
                k: GeminiProvider._convert_schema_for_gemini(v)
                for k, v in schema["properties"].items()
            }

        if "required" in schema:
            result["required"] = schema["required"]

        if "items" in schema:
            result["items"] = GeminiProvider._convert_schema_for_gemini(schema["items"])

        if "enum" in schema:
            result["enum"] = schema["enum"]

        return result

    # -- internal: parsing --

    def _parse(self, raw) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        if not raw.candidates:
            return LLMResponse(usage=self._extract_usage(raw), raw=raw)

        for part in raw.candidates[0].content.parts:
            if part.text is not None:
                text_parts.append(part.text)
            elif part.function_call is not None:
                fc = part.function_call
                # Gemini has no tool_call IDs; generate synthetic ones
                tool_calls.append(
                    ToolCall(
                        id=f"gemini_{uuid.uuid4().hex[:12]}",
                        name=fc.name,
                        input=dict(fc.args) if fc.args else {},
                    )
                )

        finish = raw.candidates[0].finish_reason if raw.candidates else ""

        return LLMResponse(
            text_parts=text_parts,
            tool_calls=tool_calls,
            usage=self._extract_usage(raw),
            stop_reason=str(finish),
            raw=raw,
        )

    @staticmethod
    def _extract_usage(raw) -> LLMUsage:
        um = getattr(raw, "usage_metadata", None)
        if um is None:
            return LLMUsage()
        return LLMUsage(
            input_tokens=getattr(um, "prompt_token_count", 0) or 0,
            output_tokens=getattr(um, "candidates_token_count", 0) or 0,
            cache_read_tokens=getattr(um, "cached_content_token_count", 0) or 0,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_provider(config) -> LLMProvider:
    """Instantiate the right provider from a Config object.

    Args:
        config: A pr_review_agent.config.Config instance with at least
                ``provider``, ``anthropic_api_key``, ``openai_api_key``,
                and ``google_api_key`` fields.

    Returns:
        An LLMProvider implementation.
    """
    name = getattr(config, "provider", "anthropic").lower()

    if name == "anthropic":
        if not config.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the Anthropic provider")
        return AnthropicProvider(api_key=config.anthropic_api_key)

    elif name == "openai":
        if not config.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI provider")
        return OpenAIProvider(api_key=config.openai_api_key)

    elif name == "google":
        if not config.google_api_key:
            raise ValueError("GOOGLE_API_KEY is required for the Google provider")
        return GeminiProvider(api_key=config.google_api_key)

    else:
        raise ValueError(
            f"Unknown provider {name!r}. Must be one of: anthropic, openai, google"
        )
