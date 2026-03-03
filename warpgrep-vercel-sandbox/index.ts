import { Sandbox } from '@vercel/sandbox';
import Anthropic from '@anthropic-ai/sdk';
import { MorphClient } from '@morphllm/morphsdk';

const repoUrl = process.argv[2] || 'https://github.com/morphllm/morphsdk-examples.git';
const query = process.argv[3] || 'Find where warpgrep is used';

const morph = new MorphClient({ apiKey: process.env.MORPH_API_KEY! });
const anthropic = new Anthropic();

console.log(`Creating sandbox and cloning ${repoUrl}...`);

const sandbox = await Sandbox.create({ runtime: 'node24' });

try {
  // Clone the repo manually (source: { type: 'git' } is unreliable)
  await sandbox.runCommand('git', ['clone', '--depth', '1', repoUrl, '/vercel/sandbox/repo']);

  // Install ripgrep (not in Amazon Linux 2023 repos, download binary)
  await sandbox.runCommand({
    cmd: 'sh',
    args: ['-c', 'curl -sL https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz | tar xz -C /tmp && cp /tmp/ripgrep-14.1.1-x86_64-unknown-linux-musl/rg /usr/local/bin/'],
    sudo: true,
  });

  const repoDir = '/vercel/sandbox/repo';

  const grepTool = morph.anthropic.createWarpGrepTool({
    repoRoot: repoDir,
    remoteCommands: {
      grep: async (pattern, path, glob) => {
        const args = ['--no-heading', '--line-number', '-C', '1', pattern, path];
        if (glob) args.push('--glob', glob);
        const r = await sandbox.runCommand('rg', args);
        return await r.stdout();
      },
      read: async (path, start, end) => {
        const r = await sandbox.runCommand('sed', ['-n', `${start},${end}p`, path]);
        return await r.stdout();
      },
      listDir: async (path, maxDepth) => {
        const r = await sandbox.runCommand('find', [
          path, '-maxdepth', String(maxDepth),
          '-not', '-path', '*/node_modules/*',
          '-not', '-path', '*/.git/*',
        ]);
        return await r.stdout();
      },
    },
  });

  console.log(`Searching: "${query}"\n`);

  // Agent loop
  let messages: Anthropic.MessageParam[] = [
    { role: 'user', content: query },
  ];

  while (true) {
    const response = await anthropic.messages.create({
      model: 'claude-sonnet-4-5-20250929',
      max_tokens: 12000,
      tools: [grepTool],
      messages,
    });

    // Collect text output
    for (const block of response.content) {
      if (block.type === 'text') {
        console.log(block.text);
      }
    }

    // If no tool use, we're done
    const toolUse = response.content.find(
      (c): c is Anthropic.ContentBlock & { type: 'tool_use' } => c.type === 'tool_use'
    );

    if (!toolUse || response.stop_reason === 'end_turn') {
      break;
    }

    // Execute the tool
    console.log(`\n[Executing ${toolUse.name}...]\n`);
    const result = await grepTool.execute(toolUse.input);
    const formatted = grepTool.formatResult(result);

    // Feed result back
    messages.push({ role: 'assistant', content: response.content });
    messages.push({
      role: 'user',
      content: [{ type: 'tool_result', tool_use_id: toolUse.id, content: formatted }],
    });
  }
} finally {
  await sandbox.stop();
  console.log('\nSandbox stopped.');
}
