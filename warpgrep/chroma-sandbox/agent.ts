// # WarpGrep + Chroma Package Search
//
// Shows how to connect WarpGrep to any remote backend by implementing
// four operations: grep, read, listDir, and glob.
//
// This example uses Chroma Package Search as the backend (indexes 3,000+
// public packages, no sandbox needed). The same pattern works for any
// system that can search, read, and list files.
//
// Usage:
//   CHROMA_API_KEY=… MORPH_API_KEY=… ANTHROPIC_API_KEY=… npx tsx agent.ts
//   CHROMA_API_KEY=… MORPH_API_KEY=… ANTHROPIC_API_KEY=… npx tsx agent.ts "How does routing work?"

import Anthropic from "@anthropic-ai/sdk";
import { createWarpGrepTool } from "@morphllm/morphsdk/tools/warp-grep/anthropic";

// ─── Config ──────────────────────────────────────────────────────────

const REGISTRY = "py_pi"; // "npm", "crates_io", "golang_proxy", "py_pi"
const PACKAGE = "fastapi";
const REPO_ROOT = "/repo"; // virtual root — all WarpGrep paths are relative to this

// ─── Chroma Package Search provider ─────────────────────────────────
//
// This class wraps Chroma's API into the four operations WarpGrep needs:
//   grep     — regex search, returns ripgrep-formatted "path:line:content"
//   read     — read file content by path, returns raw lines
//   listDir  — list all files, returns one path per line
//   glob     — find files matching a pattern, returns matching paths
//
// Replace this with your own backend (E2B sandbox, SSH, HTTP API, etc.).

class ChromaProvider {
  private apiUrl = "https://mcp.trychroma.com/package-search/v1";
  private apiKey = process.env.CHROMA_API_KEY!;
  private pkgPrefix = PACKAGE + "/";
  version = "";

  // Chroma identifies files by sha256, not by path. When we grep or list
  // files, Chroma returns both the path and the sha256. We cache this
  // mapping so that read() can look up the sha256 for a given path.
  private sha256Cache = new Map<string, string>();

  // --- Core API call ---

  private async call(tool: string, args: Record<string, unknown>) {
    const res = await fetch(this.apiUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
        "x-chroma-token": this.apiKey,
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

  // --- Path translation ---
  //
  // Chroma paths include the package name:  "fastapi/security/api_key.py"
  // WarpGrep paths use the virtual root:    "/repo/security/api_key.py"
  //
  // These helpers convert between the two formats.

  private toRelPath(chromaPath: string): string {
    return chromaPath.startsWith(this.pkgPrefix)
      ? chromaPath.slice(this.pkgPrefix.length)
      : chromaPath;
  }

  private toChromaPath(warpGrepPath: string): string {
    const rel = warpGrepPath.startsWith(REPO_ROOT + "/")
      ? warpGrepPath.slice(REPO_ROOT.length + 1)
      : warpGrepPath;
    return this.pkgPrefix + rel;
  }

  private cacheSha256(results: any[]) {
    for (const r of results) {
      const { file_path, filename_sha256 } = r.result ?? r;
      if (file_path && filename_sha256) {
        this.sha256Cache.set(file_path, filename_sha256);
      }
    }
  }

  // --- The four operations WarpGrep needs ---

  async connect(): Promise<void> {
    const probe = await this.call("package_search_grep", {
      registry_name: REGISTRY,
      package_name: PACKAGE,
      pattern: ".",
      head_limit: 1,
      output_mode: "files_with_matches",
    });
    this.version = probe.version_used;
  }

  /** Search for a pattern. Returns ripgrep-formatted output: "path:line:content" */
  async grep(pattern: string): Promise<string> {
    const result = await this.call("package_search_grep", {
      registry_name: REGISTRY,
      package_name: PACKAGE,
      pattern,
      output_mode: "content",
      c: 1,
      head_limit: 30,
    });

    this.cacheSha256(result.results);

    let output = "";
    for (const r of result.results) {
      const { file_path, content, start_line } = r.result;
      if (!file_path || !content) continue;
      const relPath = this.toRelPath(file_path);
      for (const [i, line] of content.split("\n").entries()) {
        output += `${relPath}:${start_line + i}:${line}\n`;
      }
    }
    return output;
  }

  /** Read file content by path. Returns raw lines. */
  async read(path: string, start: number, end: number): Promise<string> {
    const chromaPath = this.toChromaPath(path);
    let sha256 = this.sha256Cache.get(chromaPath);

    if (!sha256) {
      // File not in cache — populate by listing all files
      const listing = await this.listFiles();
      // listFiles already called cacheSha256, try again
      sha256 = this.sha256Cache.get(chromaPath);
    }

    if (!sha256) return `Error: file not found — ${chromaPath}`;

    const result = await this.call("package_search_read_file", {
      registry_name: REGISTRY,
      package_name: PACKAGE,
      filename_sha256: sha256,
      start_line: start,
      end_line: Math.min(end, start + 199), // Chroma caps reads at 200 lines
    });
    return result.content ?? "";
  }

  /** List all files. Returns one path per line. */
  async listFiles(): Promise<string> {
    const result = await this.call("package_search_grep", {
      registry_name: REGISTRY,
      package_name: PACKAGE,
      pattern: ".",
      output_mode: "files_with_matches",
      head_limit: 100,
    });

    this.cacheSha256(result.results);

    return result.results
      .map((r: any) => r.result?.file_path)
      .filter(Boolean)
      .map((p: string) => this.toRelPath(p))
      .join("\n");
  }

  /** Find files matching a glob pattern (e.g. "*.py", "routes/*.ts"). */
  async glob(pattern: string): Promise<string> {
    const allFiles = await this.listFiles();
    const globToRegex = (g: string) =>
      new RegExp(
        g.replace(/[.+^${}()|[\]\\]/g, "\\$&")
          .replace(/\*\*/g, "<<<GLOBSTAR>>>")
          .replace(/\*/g, "[^/]*")
          .replace(/<<<GLOBSTAR>>>/g, ".*")
          .replace(/\?/g, ".")
      );
    const regex = globToRegex(pattern);
    return allFiles
      .split("\n")
      .filter((p) => regex.test(p))
      .join("\n");
  }
}

// ─── Step 1: Set up the backend ──────────────────────────────────────

const chroma = new ChromaProvider();

console.log("Connecting to Chroma Package Search...");
await chroma.connect();
console.log(`Package "${PACKAGE}" found on ${REGISTRY} (v${chroma.version})\n`);

// ─── Step 2: Create WarpGrep tool with remoteCommands ────────────────
//
// Override these functions to tell WarpGrep how to search your backend.
// Each returns a plain string — the SDK handles parsing internally.
//
//   grep(pattern, path, glob?)  → ripgrep-formatted lines: "path:line:content"
//   read(path, start, end)      → raw file content (newline-separated)
//   listDir(path, maxDepth)     → one file path per line
//
// Note: glob is also part of the WarpGrep provider interface. When using
// remoteCommands, the SDK auto-derives glob from listDir. If you need
// custom glob behavior, implement the full WarpGrepProvider instead.

const searchTool = createWarpGrepTool({
  repoRoot: REPO_ROOT,
  remoteCommands: {
    grep: async (pattern, _path, _glob) => chroma.grep(pattern),
    read: async (path, start, end) => chroma.read(path, start, end),
    listDir: async (_path, _maxDepth) => chroma.listFiles(),
  },
});

// ─── Step 3: Use the tool with the Anthropic SDK ─────────────────────
//
// searchTool is a valid Anthropic tool definition. Pass it to tools[],
// call searchTool.execute() for tool calls, and searchTool.formatResult()
// to format the response back to Claude.

const anthropic = new Anthropic();
const query = process.argv[2] || "What security classes does this package provide?";
console.log(`Question: "${query}"\n`);

const messages: Anthropic.MessageParam[] = [
  { role: "user", content: query },
];

for (let turn = 0; turn < 8; turn++) {
  const response = await anthropic.messages.create({
    model: "claude-sonnet-4-5-20250929",
    max_tokens: 4096,
    system: `You are a code assistant. Use the codebase_search tool to find relevant code before answering. You are searching the "${PACKAGE}" package (${REGISTRY}, v${chroma.version}). Be concise.`,
    tools: [searchTool],
    messages,
  });

  messages.push({ role: "assistant", content: response.content });

  if (response.stop_reason === "end_turn") {
    for (const block of response.content) {
      if (block.type === "text") console.log(block.text);
    }
    break;
  }

  // Execute tool calls and feed results back
  const toolResults: Anthropic.ToolResultBlockParam[] = [];
  for (const block of response.content) {
    if (block.type === "tool_use") {
      const searchTerm = (block.input as { search_term: string }).search_term;
      console.log(`[WarpGrep → Chroma] Searching: "${searchTerm}"`);
      const result = await searchTool.execute(block.input);
      toolResults.push({
        type: "tool_result",
        tool_use_id: block.id,
        content: searchTool.formatResult(result),
      });
    }
  }

  if (toolResults.length > 0) {
    messages.push({ role: "user", content: toolResults });
  }
}
