import { getSandbox, type Sandbox } from '@cloudflare/sandbox';
import OpenAI from 'openai';

export { Sandbox } from '@cloudflare/sandbox';

interface Env {
  Sandbox: DurableObjectNamespace<Sandbox>;
  MORPH_API_KEY: string;
}

// WarpGrep agent config (mirrors SDK defaults)
const MAX_TURNS = 4;
const MAX_OUTPUT_LINES = 200;
const MAX_CONTEXT_CHARS = 540_000;
const MODEL = 'morph-warp-grep-v2';
const MORPH_API_URL = 'https://api.morphllm.com/v1';

// Skipped dirs/files in listings
const SKIP_NAMES = new Set([
  '.git', 'node_modules', '.pnpm', 'bower_components', '__pycache__',
  '.next', '.nuxt', 'dist', 'build', '.cache', '.turbo',
]);

// ── Tool call parser (matches SDK's Qwen3 format) ──

interface ToolCall {
  name: string;
  arguments: Record<string, any>;
}

function parseToolCalls(text: string): ToolCall[] {
  const clean = text.replace(/<think>[\s\S]*?<\/think>/gi, '');
  const tools: ToolCall[] = [];
  const re = /<tool_call>\s*<function=([a-z_][a-z0-9_]*)>([\s\S]*?)<\/function>\s*<\/tool_call>/gi;
  let m;
  while ((m = re.exec(clean)) !== null) {
    const name = m[1].toLowerCase();
    const body = m[2];
    const params: Record<string, string> = {};
    const pRe = /<parameter=([a-z_][a-z0-9_]*)>([\s\S]*?)<\/parameter>/gi;
    let pm;
    while ((pm = pRe.exec(body)) !== null) {
      params[pm[1].toLowerCase()] = pm[2].trim();
    }

    if (name === 'ripgrep') {
      if (!params.pattern) continue;
      tools.push({
        name: 'grep',
        arguments: {
          pattern: params.pattern,
          path: params.path || '.',
          ...(params.glob && { glob: params.glob }),
        },
      });
    } else if (name === 'list_directory') {
      const dirPath = params.path || '.';
      tools.push({ name: 'list_directory', arguments: { path: dirPath, pattern: params.pattern || null } });
    } else if (name === 'read') {
      if (!params.path) continue;
      const args: Record<string, any> = { path: params.path };
      if (params.lines) {
        const ranges: number[][] = [];
        for (const r of params.lines.split(',')) {
          const [s, e] = r.trim().split('-').map(Number);
          if (Number.isFinite(s) && Number.isFinite(e)) ranges.push([s, e]);
          else if (Number.isFinite(s)) ranges.push([s, s]);
        }
        if (ranges.length === 1) { args.start = ranges[0][0]; args.end = ranges[0][1]; }
        else if (ranges.length > 1) { args.lines = ranges; }
      }
      tools.push({ name: 'read', arguments: args });
    } else if (name === 'finish') {
      if (params.result && !params.files) {
        tools.push({ name: 'finish', arguments: { files: [], textResult: params.result } });
        continue;
      }
      if (!params.files) {
        tools.push({ name: 'finish', arguments: { files: [], textResult: 'No relevant code found.' } });
        continue;
      }
      const files: { path: string; lines: string | number[][] }[] = [];
      for (const line of params.files.split('\n')) {
        const t = line.trim();
        if (!t) continue;
        const ci = t.indexOf(':');
        if (ci === -1) { files.push({ path: t, lines: '*' }); continue; }
        const fp = t.slice(0, ci);
        const rp = t.slice(ci + 1);
        const ranges: number[][] = [];
        for (const rs of rp.split(',')) {
          const rt = rs.trim();
          if (!rt || rt === '*') { files.push({ path: fp, lines: '*' }); break; }
          const [s, e] = rt.split('-').map(Number);
          if (Number.isFinite(s) && Number.isFinite(e)) ranges.push([s, e]);
          else if (Number.isFinite(s)) ranges.push([s, s]);
        }
        if (ranges.length > 0) files.push({ path: fp, lines: ranges });
        else if (!files.some(f => f.path === fp)) files.push({ path: fp, lines: '*' });
      }
      tools.push({ name: 'finish', arguments: { files: files.length > 0 ? files : [], textResult: files.length === 0 ? params.files : undefined } });
    }
  }
  return tools;
}

// ── Sandbox-backed command execution ──

function createSandboxProvider(sandbox: ReturnType<typeof getSandbox>, repoDir: string) {
  return {
    grep: async (pattern: string, path: string, glob?: string) => {
      const absPath = path.startsWith('/') ? path : `${repoDir}/${path}`;
      let cmd = `rg --no-heading --line-number -C 1 '${pattern}' '${absPath}'`;
      if (glob) cmd += ` --glob '${glob}'`;
      const r = await sandbox.exec(cmd);
      return r.stdout;
    },
    read: async (path: string, start = 1, end = 1000000) => {
      const absPath = path.startsWith('/') ? path : `${repoDir}/${path}`;
      const r = await sandbox.exec(`sed -n '${start},${end}p' '${absPath}'`);
      return r.stdout;
    },
    listDir: async (path: string, maxDepth = 3) => {
      const absPath = path.startsWith('/') ? path : `${repoDir}/${path}`;
      const r = await sandbox.exec(
        `find '${absPath}' -maxdepth ${maxDepth} -not -path '*/node_modules/*' -not -path '*/.git/*'`,
      );
      return r.stdout;
    },
  };
}

// Parse find output into structured entries
function parseFindOutput(stdout: string, repoDir: string): { name: string; path: string; type: string; depth: number }[] {
  const paths = (stdout || '').trim().split(/\r?\n/).filter(p => p.length > 0);
  const entries: { name: string; path: string; type: string; depth: number }[] = [];
  for (const fullPath of paths) {
    if (fullPath === repoDir) continue;
    const name = fullPath.split('/').pop() || '';
    if (SKIP_NAMES.has(name)) continue;
    let relPath = fullPath;
    if (fullPath.startsWith(repoDir)) {
      relPath = fullPath.slice(repoDir.length).replace(/^\//, '');
    }
    const depth = relPath.split('/').filter(Boolean).length - 1;
    const hasExt = name.includes('.') && !name.startsWith('.');
    entries.push({ name, path: relPath, type: hasExt ? 'file' : 'dir', depth: Math.max(0, depth) });
    if (entries.length >= MAX_OUTPUT_LINES) break;
  }
  return entries;
}

// ── WarpGrep agentic loop ──

async function runWarpGrep(
  query: string,
  repoDir: string,
  commands: ReturnType<typeof createSandboxProvider>,
  apiKey: string,
) {
  const client = new OpenAI({ apiKey, baseURL: MORPH_API_URL });

  // Build initial state
  const treeStdout = await commands.listDir(repoDir, 2);
  const entries = parseFindOutput(treeStdout, repoDir);
  const treeLines = entries.map(e => `${'  '.repeat(e.depth)}${e.type === 'dir' ? `${e.name}/` : e.name}`);
  const repoName = repoDir.split('/').pop() || 'repo';
  const treeOutput = treeLines.length > 0 ? `${repoName}/\n${treeLines.join('\n')}` : `${repoName}/`;
  const budgetStr = `<context_budget>0% (0K/${Math.round(MAX_CONTEXT_CHARS / 1000)}K chars)</context_budget>`;

  const messages: { role: 'user' | 'assistant'; content: string }[] = [
    {
      role: 'user',
      content: `<repo_structure>\n${treeOutput}\n</repo_structure>\n\n<search_string>\n${query}\n</search_string>\n${budgetStr}\nTurn 0/${MAX_TURNS}`,
    },
  ];

  let finishMeta: { files: { path: string; lines: string | number[][] }[]; textResult?: string } | null = null;

  for (let turn = 1; turn <= MAX_TURNS; turn++) {
    // Call Morph API
    const data = await client.chat.completions.create({
      model: MODEL,
      temperature: 0,
      max_tokens: 1024,
      messages,
    });

    const content = data.choices?.[0]?.message?.content;
    if (!content) break;

    messages.push({ role: 'assistant', content });
    const toolCalls = parseToolCalls(content);
    if (toolCalls.length === 0) break;

    const finishCalls = toolCalls.filter(c => c.name === 'finish');
    const grepCalls = toolCalls.filter(c => c.name === 'grep');
    const listDirCalls = toolCalls.filter(c => c.name === 'list_directory');
    const readCalls = toolCalls.filter(c => c.name === 'read');

    // Execute all tool calls in parallel
    const results: string[] = [];

    const promises: Promise<string>[] = [];
    for (const c of grepCalls) {
      promises.push(
        commands.grep(c.arguments.pattern, c.arguments.path, c.arguments.glob)
          .then(out => {
            const lines = (out || '').trim().split(/\r?\n/).filter(l => l.length > 0);
            const truncated = lines.length > MAX_OUTPUT_LINES
              ? [...lines.slice(0, MAX_OUTPUT_LINES), `... (truncated at ${MAX_OUTPUT_LINES} of ${lines.length} lines)`]
              : lines;
            return `<tool_response>\n${truncated.join('\n') || 'no matches'}\n</tool_response>`;
          })
          .catch(e => `<tool_response>\n[GREP ERROR] ${e}\n</tool_response>`),
      );
    }
    for (const c of listDirCalls) {
      promises.push(
        commands.listDir(c.arguments.path, 3)
          .then(out => {
            const parsed = parseFindOutput(out, repoDir);
            const tree = parsed.map(e => `${'  '.repeat(e.depth)}${e.type === 'dir' ? `${e.name}/` : e.name}`).join('\n');
            return `<tool_response>\n${tree || 'empty'}\n</tool_response>`;
          })
          .catch(e => `<tool_response>\n[LIST ERROR] ${e}\n</tool_response>`),
      );
    }
    for (const c of readCalls) {
      const start = c.arguments.start ?? 1;
      const end = c.arguments.end ?? 1000000;
      promises.push(
        commands.read(c.arguments.path, start, end)
          .then(out => {
            const contentLines = (out || '').split('\n');
            if (contentLines.length > 0 && contentLines[contentLines.length - 1] === '') contentLines.pop();
            const numbered = contentLines.map((line, idx) => `${start + idx}|${line}`);
            return `<tool_response>\n${numbered.join('\n') || '(empty file)'}\n</tool_response>`;
          })
          .catch(e => `<tool_response>\n[READ ERROR] ${e}\n</tool_response>`),
      );
    }

    const toolResults = await Promise.all(promises);
    results.push(...toolResults);

    if (results.length > 0) {
      const totalChars = messages.reduce((sum, m) => sum + m.content.length, 0);
      const percent = Math.round((totalChars / MAX_CONTEXT_CHARS) * 100);
      const usedK = Math.round(totalChars / 1000);
      const maxK = Math.round(MAX_CONTEXT_CHARS / 1000);
      const turnsRemaining = MAX_TURNS - turn;
      const turnMsg = turnsRemaining === 1
        ? `\nYou have used ${turn} turns, you only have 1 turn remaining. You have run out of turns to explore the code base and MUST call the finish tool now`
        : `\nYou have used ${turn} turn${turn === 1 ? '' : 's'} and have ${turnsRemaining} remaining`;
      messages.push({
        role: 'user',
        content: results.join('\n') + turnMsg + `\n<context_budget>${percent}% (${usedK}K/${maxK}K chars)</context_budget>`,
      });
    }

    if (finishCalls.length > 0) {
      const fc = finishCalls[0];
      finishMeta = { files: fc.arguments.files ?? [], textResult: fc.arguments.textResult };
      break;
    }
  }

  if (!finishMeta) {
    return { success: false, error: 'Search did not complete within turn limit' };
  }

  if (finishMeta.files.length === 0) {
    return { success: true, contexts: [], summary: finishMeta.textResult || 'No relevant code found.' };
  }

  // Resolve finish files by reading their content
  const contexts: { file: string; content: string; lines: string | number[][] }[] = [];
  for (const f of finishMeta.files) {
    if (f.lines === '*') {
      const content = await commands.read(f.path);
      contexts.push({ file: f.path, content, lines: '*' });
    } else if (Array.isArray(f.lines)) {
      const chunks: string[] = [];
      for (const [s, e] of f.lines) {
        const content = await commands.read(f.path, s, e);
        chunks.push(content);
      }
      contexts.push({ file: f.path, content: chunks.join('\n...\n'), lines: f.lines });
    }
  }

  const summary = ['Relevant context found:', ...finishMeta.files.map(f => {
    const ranges = f.lines === '*' ? '*' : Array.isArray(f.lines) ? f.lines.map(([s, e]) => `${s}-${e}`).join(', ') : '*';
    return `- ${f.path}: ${ranges}`;
  })].join('\n');

  return { success: true, contexts, summary };
}

// ── Worker ──

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Test endpoint: verify sandbox primitives work
    if (url.pathname === '/test') {
      const sandbox = getSandbox(env.Sandbox, 'test');

      await sandbox.exec('rm -rf /workspace/repo');
      const clone = await sandbox.exec('git clone --depth 1 https://github.com/honojs/hono.git /workspace/repo');
      if (!clone.success) {
        return Response.json({ error: 'git clone failed', stderr: clone.stderr }, { status: 500 });
      }

      const grep = await sandbox.exec("rg --no-heading --line-number -C 1 'middleware' /workspace/repo/src --glob '*.ts' -l");
      const read = await sandbox.exec("sed -n '1,5p' /workspace/repo/package.json");
      const listDir = await sandbox.exec("find /workspace/repo/src -maxdepth 2 -not -path '*/node_modules/*' -not -path '*/.git/*' -type d");

      return Response.json({
        clone: { success: clone.success },
        grep: { success: grep.success, stdout: grep.stdout.substring(0, 500) },
        read: { success: read.success, stdout: read.stdout },
        listDir: { success: listDir.success, stdout: listDir.stdout.substring(0, 500) },
      });
    }

    // Main search endpoint
    if (request.method !== 'POST' || url.pathname !== '/search') {
      return new Response('POST /search with { "repo": "...", "query": "..." } or GET /test', { status: 405 });
    }

    const { repo, query } = await request.json<{ repo: string; query: string }>();
    if (!repo || !query) {
      return Response.json({ error: 'Missing repo or query' }, { status: 400 });
    }

    const sandbox = getSandbox(env.Sandbox, 'code-search');
    const repoDir = '/workspace/repo';

    await sandbox.exec(`rm -rf ${repoDir}`);
    const cloneResult = await sandbox.exec(`git clone --depth 1 ${repo} ${repoDir}`);
    if (!cloneResult.success) {
      return Response.json({ error: 'git clone failed', stderr: cloneResult.stderr }, { status: 500 });
    }

    const commands = createSandboxProvider(sandbox, repoDir);
    const result = await runWarpGrep(query, repoDir, commands, env.MORPH_API_KEY);

    return Response.json(result);
  },
};
