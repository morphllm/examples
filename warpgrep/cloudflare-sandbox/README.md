# WarpGrep + Cloudflare Sandbox

A pattern reference for using WarpGrep to search code inside a [Cloudflare Sandbox](https://developers.cloudflare.com/sandbox/). The repo is cloned into the sandbox, and WarpGrep executes ripgrep, sed, and find remotely via `remoteCommands`.

> **Note:** This is a pattern reference, not a standalone runnable example. Cloudflare Sandbox requires a Cloudflare Workers environment. The `agent.ts` file shows how to wire up `remoteCommands` using `sandbox.exec()` — in production, `getSandbox()` runs in your Worker and `createWarpGrepTool()` runs in your Node.js backend.

## Setup

```bash
npm install
```

## How it works

1. In your Worker, get a sandbox via `getSandbox(env.Sandbox, 'code-search')` and clone `anthropics/anthropic-cookbook` into it
2. Install ripgrep in the sandbox
3. Create a WarpGrep tool with `remoteCommands` that execute inside the sandbox via `sandbox.exec()`
4. Run a Claude agent loop — when Claude calls the tool, WarpGrep searches the sandboxed repo

## Environment

This example requires a Cloudflare Workers project with the Sandbox binding configured. See the [Cloudflare Sandbox docs](https://developers.cloudflare.com/sandbox/) for setup instructions.

Required environment variables for the agent portion:

```
MORPH_API_KEY=your-key
ANTHROPIC_API_KEY=your-key
```

The agent loop runs up to 5 turns, giving Claude multiple chances to search and refine its understanding.
