// # Chroma Package Search + WarpGrep
//
// WarpGrep searching code inside Chroma's Package Search index.
// No sandbox or VM needed — Chroma indexes public packages and
// exposes grep, read, and file listing over its MCP API.
// WarpGrep uses remoteCommands to route all tool calls through Chroma.
//
// Usage:
//   CHROMA_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
//   CHROMA_API_KEY=your-key MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does this package handle API key authentication?"

import Anthropic from "@anthropic-ai/sdk";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

// --- Configuration ---

const CHROMA_MCP_URL = "https://mcp.trychroma.com/package-search/v1";
const CHROMA_API_KEY = process.env.CHROMA_API_KEY!;

// The package to search — swap these to search any indexed package
const REGISTRY = "py_pi"; // "npm", "crates_io", "golang_proxy", "py_pi"
const PACKAGE = "fastapi";

// Virtual root for WarpGrep — all paths are relative to this
const REPO_ROOT = "/repo";

// --- 1. Chroma Package Search client ---

// Cache file_path -> sha256 (Chroma identifies files by sha256, not path)
const fileIndex = new Map<string, string>();

async function callChroma(tool: string, args: Record<string, unknown>) {
  const res = await fetch(CHROMA_MCP_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "x-chroma-token": CHROMA_API_KEY,
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      method: "tools/call",
      id: 1,
      params: { name: tool, arguments: args },
    }),
  });

  const text = await res.text();
  const dataLine = text.split("\n").find((l) => l.startsWith("data: "));
  if (!dataLine) throw new Error("No data in Chroma response");

  const json = JSON.parse(dataLine.slice(6));
  if (json.error) throw new Error(json.error.message);
  return JSON.parse(json.result.content[0].text);
}

// Chroma paths look like "fastapi/security/api_key.py".
// Strip the package name prefix so WarpGrep sees "security/api_key.py".
const pkgPrefix = PACKAGE + "/";
function stripPkg(chromaPath: string): string {
  return chromaPath.startsWith(pkgPrefix)
    ? chromaPath.slice(pkgPrefix.length)
    : chromaPath;
}

// WarpGrep passes absolute paths like "/repo/security/api_key.py".
// Convert back to the Chroma path "fastapi/security/api_key.py".
function toChromaPath(warpgrepPath: string): string {
  const rel = warpgrepPath.startsWith(REPO_ROOT + "/")
    ? warpgrepPath.slice(REPO_ROOT.length + 1)
    : warpgrepPath.startsWith(REPO_ROOT)
      ? warpgrepPath.slice(REPO_ROOT.length)
      : warpgrepPath;
  return pkgPrefix + rel;
}

function cacheFiles(results: any[]) {
  for (const r of results) {
    const { file_path, filename_sha256 } = r.result ?? r;
    if (file_path && filename_sha256) {
      fileIndex.set(file_path, filename_sha256);
    }
  }
}

// --- 2. Verify the package is indexed ---

console.log(`Connecting to Chroma Package Search...`);
const probe = await callChroma("package_search_grep", {
  registry_name: REGISTRY,
  package_name: PACKAGE,
  pattern: ".",
  head_limit: 1,
  output_mode: "files_with_matches",
});
console.log(
  `Package "${PACKAGE}" found on ${REGISTRY} (v${probe.version_used})\n`
);

// --- 3. Create WarpGrep tool with Chroma as the backend ---

const grepTool = createWarpGrepTool({
  repoRoot: REPO_ROOT,
  remoteCommands: {
    // grep → package_search_grep (content mode → ripgrep-formatted output)
    grep: async (pattern, _path, _glob) => {
      const result = await callChroma("package_search_grep", {
        registry_name: REGISTRY,
        package_name: PACKAGE,
        pattern,
        output_mode: "content",
        c: 1,
        head_limit: 30,
      });

      cacheFiles(result.results);

      // Convert to ripgrep format: path:line:content
      let output = "";
      for (const r of result.results) {
        const { file_path, content, start_line } = r.result;
        if (!file_path || !content) continue;
        const relPath = stripPkg(file_path);
        const lines = content.split("\n");
        lines.forEach((line: string, i: number) => {
          output += `${relPath}:${start_line + i}:${line}\n`;
        });
      }
      return output;
    },

    // read → package_search_read_file (sha256 lookup from cache)
    read: async (path, start, end) => {
      const chromaPath = toChromaPath(path);
      let sha256 = fileIndex.get(chromaPath);

      // If not cached yet, build the index with a broad file listing
      if (!sha256) {
        const lookup = await callChroma("package_search_grep", {
          registry_name: REGISTRY,
          package_name: PACKAGE,
          pattern: ".",
          output_mode: "files_with_matches",
          head_limit: 100,
        });
        cacheFiles(lookup.results);
        sha256 = fileIndex.get(chromaPath);
      }

      if (!sha256) return `Error: file not found in Chroma index — ${chromaPath}`;

      // Chroma limits reads to 200 lines per request
      const clampedEnd = Math.min(end, start + 199);

      const result = await callChroma("package_search_read_file", {
        registry_name: REGISTRY,
        package_name: PACKAGE,
        filename_sha256: sha256,
        start_line: start,
        end_line: clampedEnd,
      });

      return result.content ?? "";
    },

    // listDir → package_search_grep (files_with_matches → one path per line)
    listDir: async (_path, _maxDepth) => {
      const result = await callChroma("package_search_grep", {
        registry_name: REGISTRY,
        package_name: PACKAGE,
        pattern: ".",
        output_mode: "files_with_matches",
        head_limit: 100,
      });

      cacheFiles(result.results);

      return result.results
        .map((r: any) => r.result?.file_path)
        .filter(Boolean)
        .map(stripPkg)
        .join("\n");
    },
  },
});

// --- 4. Run the agent loop ---

const anthropic = new Anthropic();
const query =
  process.argv[2] ||
  "What security classes does this package provide?";

console.log(`Question: "${query}"\n`);

const messages: Anthropic.MessageParam[] = [
  { role: "user", content: query },
];

for (let turn = 0; turn < 8; turn++) {
  const response = await anthropic.messages.create({
    model: "claude-sonnet-4-5-20250929",
    max_tokens: 4096,
    system: `You are a code assistant. Use the codebase_search tool to find relevant code before answering questions. You are searching the "${PACKAGE}" package (${REGISTRY}, v${probe.version_used}). Be concise.`,
    tools: [grepTool],
    messages,
  });

  messages.push({ role: "assistant", content: response.content });

  if (response.stop_reason === "end_turn") {
    for (const block of response.content) {
      if (block.type === "text") {
        console.log(block.text);
      }
    }
    break;
  }

  const toolResults: Anthropic.ToolResultBlockParam[] = [];

  for (const block of response.content) {
    if (block.type === "tool_use") {
      const searchTerm = (block.input as { search_term: string }).search_term;
      console.log(`[WarpGrep → Chroma] Searching: "${searchTerm}"`);
      const result = await grepTool.execute(block.input);
      toolResults.push({
        type: "tool_result",
        tool_use_id: block.id,
        content: grepTool.formatResult(result),
      });
    }
  }

  if (toolResults.length > 0) {
    messages.push({ role: "user", content: toolResults });
  }
}
