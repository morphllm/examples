# WarpGrep + Docker / SSH

A coding agent that uses WarpGrep to search code on a remote machine via SSH. The repo is cloned on the remote host, and WarpGrep executes ripgrep, sed, and find remotely via `remoteCommands` over an SSH connection.

## Prerequisites

The remote host must have the following installed:

- `git`
- `ripgrep` (`rg`)
- Standard Unix tools (`sed`, `find`)

## Setup

```bash
npm install
```

## Run

```bash
SSH_HOST=your-host SSH_USER=your-user SSH_KEY_PATH=~/.ssh/id_rsa MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts
```

Ask a specific question:

```bash
SSH_HOST=your-host SSH_USER=your-user SSH_KEY_PATH=~/.ssh/id_rsa MORPH_API_KEY=your-key ANTHROPIC_API_KEY=your-key npx tsx agent.ts "How does streaming work?"
```

## How it works

1. Connects to the remote host via SSH using `node-ssh`
2. Clones `anthropics/anthropic-cookbook` on the remote host
3. Creates a WarpGrep tool with `remoteCommands` that execute over SSH via `ssh.execCommand()`
4. Runs a Claude agent loop — when Claude calls the tool, WarpGrep searches the remote repo
5. Cleans up the cloned repo and disconnects when done

The agent loop runs up to 5 turns, giving Claude multiple chances to search and refine its understanding.
