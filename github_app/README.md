# PR Review Agent - GitHub App

AI-powered PR code review using Claude Opus 4.6 with multi-pass ensemble review, WarpGrep agentic search, and judge-pass FP filtering.

## Setup

### 1. Register a GitHub App

Go to [github.com/settings/apps/new](https://github.com/settings/apps/new):

- **Webhook URL**: Your server's `/webhook` endpoint (use [smee.io](https://smee.io) for local dev)
- **Webhook secret**: Generate with `python -c "import secrets; print(secrets.token_hex(32))"`
- **Permissions**:
  - Contents: Read
  - Pull Requests: Write
  - Checks: Write
  - Metadata: Read
- **Events**: Pull request
- **Where installable**: Any account

After creating, generate a private key and download the PEM file.

### 2. Configure

```bash
cp .env.example .env
```

Fill in your `.env`:
- `GITHUB_APP_ID` - from your app's settings page
- `GITHUB_PRIVATE_KEY_PATH` - path to downloaded PEM file
- `GITHUB_WEBHOOK_SECRET` - the secret you set above
- `ANTHROPIC_API_KEY` - your Anthropic API key
- `MORPH_API_KEY` - for WarpGrep agentic search (optional)

### 3. Run Locally

```bash
pip install -r requirements.txt
uvicorn github_app.app:app --reload --port 8000
```

Set up webhook forwarding for local development:
```bash
npx smee -u https://smee.io/YOUR_CHANNEL -t http://localhost:8000/webhook
```

### 4. Deploy with Docker

```bash
docker compose up --build
```

### 5. Verify

```bash
# Health check
curl http://localhost:8000/health

# Install the app on a test repo, open a PR, and verify:
# - Check run appears (in_progress -> completed)
# - PR review posted with inline comments
# - Comments show severity, category, confidence
```

## How It Works

1. GitHub sends a webhook when a PR is opened/updated
2. The server verifies the webhook signature and enqueues a background review
3. The worker fetches the PR diff and shallow-clones the repo
4. `review_diff()` runs multi-pass review with Claude Opus, WarpGrep context search, and judge filtering
5. Results are posted as inline PR review comments with a GitHub Check Run status

## Configuration

| Env Var | Description | Default |
|---------|-------------|---------|
| `GITHUB_APP_ID` | GitHub App ID | required |
| `GITHUB_PRIVATE_KEY_PATH` | Path to PEM file | required |
| `GITHUB_WEBHOOK_SECRET` | Webhook HMAC secret | required |
| `ANTHROPIC_API_KEY` | Anthropic API key | required |
| `MORPH_API_KEY` | WarpGrep API key | optional |
| `MAX_CONCURRENT_REVIEWS` | Parallel review limit | 3 |
| `MAX_ISSUES_PER_PR` | Max comments per PR | 8 |
| `LOG_LEVEL` | Logging level | INFO |
