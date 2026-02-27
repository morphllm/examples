"""WarpGrep client for codebase search via Morph API."""

import json
import subprocess

import requests


class WarpGrepClient:
    """Search codebases using WarpGrep via the Morph API.

    Falls back to local ripgrep if the API is unavailable.
    """

    def __init__(self, api_key: str, base_url: str = "https://api.morphllm.com/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def search(self, query: str, path: str, timeout: int = 30) -> str:
        """Search codebase using WarpGrep API.

        Args:
            query: Natural language or keyword search query.
            path: Local directory path to search in.
            timeout: Request timeout in seconds.

        Returns:
            Search results as a string.
        """
        try:
            return self._api_search(query, path, timeout)
        except Exception:
            return self._fallback_search(query, path)

    def _api_search(self, query: str, path: str, timeout: int) -> str:
        """Search via WarpGrep API using tool calling."""
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self.headers,
            json={
                "model": "morph-warpgrep",
                "messages": [{"role": "user", "content": query}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "warpgrep_codebase_search",
                            "description": "Search codebase for relevant code",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "Search query",
                                    },
                                    "path": {
                                        "type": "string",
                                        "description": "Directory to search in",
                                    },
                                },
                                "required": ["query", "path"],
                            },
                        },
                    }
                ],
                "tool_choice": "auto",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        # Extract content from response
        message = data["choices"][0]["message"]
        if message.get("content"):
            return message["content"]

        # If tool calls were made, extract the results
        if message.get("tool_calls"):
            results = []
            for tc in message["tool_calls"]:
                if tc.get("function", {}).get("name") == "warpgrep_codebase_search":
                    results.append(json.dumps(tc["function"].get("arguments", {})))
            return "\n".join(results) if results else "No results"

        return "No results"

    def _fallback_search(self, query: str, path: str) -> str:
        """Fallback: use local ripgrep for keyword search."""
        # Extract key terms from the query for grep
        keywords = [w for w in query.split() if len(w) > 3 and w.isalpha()]
        if not keywords:
            keywords = query.split()[:3]

        results = []
        for keyword in keywords[:2]:  # limit to 2 keywords
            try:
                proc = subprocess.run(
                    ["rg", "--no-heading", "-n", "-i", "--max-count", "5", keyword, path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.stdout:
                    results.append(f"# Results for '{keyword}':\n{proc.stdout[:2000]}")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

        return "\n\n".join(results) if results else "No results found"
