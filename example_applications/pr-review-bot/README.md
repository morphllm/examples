# PR Review Bot

A GitHub App that reviews pull requests. It clones the PR, uses [WarpGrep](../../warpgrep) to
pull in the surrounding code the diff depends on, runs a multi-pass review with a judge pass to
drop false positives, and posts inline comments plus a Check Run.

The interesting part for Morph users is the context step. Reviewing a diff in isolation produces
noise — you cannot tell whether a removed guard clause mattered without reading the callers.
WarpGrep runs as an agentic search tool inside the review loop, so the reviewer can go look.

## Layout

| Path | What it is |
|---|---|
| `app.py` | FastAPI server. `POST /webhook` (GitHub, HMAC-verified) and `POST /review` (direct API call with an optional reviewer persona and completion callback). |
| `worker.py` | The webhook path: clone → review → post comments → Check Run → cleanup. |
| `cli.py` | Review a PR from your terminal, no server or GitHub App needed. |
| `github_client.py`, `auth.py` | GitHub REST calls and App-installation JWT minting. |
| `telemetry.py` | Optional Axiom events. No-ops without `AXIOM_TOKEN`. |
| `review_engine/` | The reviewer itself, independent of GitHub. `review_diff()` in `review_engine/review.py` is the whole public surface. |
| `review_engine/warpgrep/` | WarpGrep client (`morph-warp-grep-v2.1`) and query planner. |

## Quick start: review a PR from the CLI

```bash
pip install -r requirements.txt
export MORPH_API_KEY=...       # WarpGrep
export OPENAI_API_KEY=...      # the reviewer LLM
export GITHUB_TOKEN=...        # optional for public repos, required for private

python cli.py https://github.com/owner/repo/pull/123
python cli.py owner/repo#123 --max-issues 5 --no-clone
```

`--no-clone` reviews the diff alone. It is faster and needs no disk, but it disables WarpGrep
context search, which is most of what makes the review good.

## Running the GitHub App

### 1. Register the app

At [github.com/settings/apps/new](https://github.com/settings/apps/new):

- **Webhook URL**: your server's `/webhook` endpoint ([smee.io](https://smee.io) works for local dev)
- **Webhook secret**: `python -c "import secrets; print(secrets.token_hex(32))"`
- **Permissions**: Contents `read`, Pull requests `write`, Checks `write`, Metadata `read`
- **Events**: Pull request

Generate a private key and download the PEM.

### 2. Configure

```bash
cp .env.example .env
```

`GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY_PATH`, and `GITHUB_WEBHOOK_SECRET` come from the app's
settings page. See `.env.example` for the rest.

### 3. Run

```bash
uvicorn app:app --reload --port 8080
npx smee -u https://smee.io/YOUR_CHANNEL -t http://localhost:8080/webhook
```

Or with Docker:

```bash
docker compose up --build
curl http://localhost:8080/health
```

Then open a PR on a repo the app is installed on. You should see the Check Run go
`in_progress` → `completed`, and inline comments tagged with severity, category, and confidence.

## Choosing the reviewer model

`review_engine/config.py` selects the provider. `provider` is `openai` (default, `gpt-5.4`),
`anthropic` (`claude-opus-4-6`), or `google` (`gemini-3.1-pro-preview`).

Because the OpenAI provider accepts a `base_url`, you can point the reviewer at Morph's own
chat models instead:

```bash
export OPENAI_BASE_URL=https://api.morphllm.com/v1
export OPENAI_API_KEY=$MORPH_API_KEY
```

then set `model="morph-glm52-744b"` (or any model from
[open-source-models](../../open-source-models)).

WarpGrep always runs against Morph regardless of which reviewer model you pick.

## How a review runs

1. Webhook arrives; the signature is verified and the work is handed to a background task.
2. The diff is fetched and the repo is shallow-cloned at the PR head.
3. `parse_diff` → `filter_reviewable_files` drops lockfiles, generated code, and vendored paths.
4. `Reviewer.review_pr` runs file-level and cross-file passes. WarpGrep is available as a tool
   throughout, so the model can search the clone for callers, definitions, and similar patterns.
5. A calibration pass rescores each issue; per-category confidence thresholds in
   `review_engine/config.py` decide what survives.
6. Survivors are sorted by confidence, capped at `MAX_ISSUES_PER_PR`, and posted.

## Configuration

| Env Var | Description | Default |
|---|---|---|
| `GITHUB_APP_ID` | GitHub App ID | required |
| `GITHUB_PRIVATE_KEY_PATH` | Path to the PEM file (or `GITHUB_PRIVATE_KEY` inline / base64) | required |
| `GITHUB_WEBHOOK_SECRET` | Webhook HMAC secret | required |
| `ANTHROPIC_API_KEY` | Anthropic key | required |
| `MORPH_API_KEY` | Powers WarpGrep context search | required in practice |
| `OPENAI_API_KEY` | Needed when `provider="openai"` (the default) | — |
| `GOOGLE_API_KEY` | Needed when `provider="google"` | — |
| `OPENAI_BASE_URL` | Point the OpenAI provider at another compatible endpoint | — |
| `MAX_CONCURRENT_REVIEWS` | Parallel review limit | 3 |
| `MAX_ISSUES_PER_PR` | Max comments per PR | 8 |
| `AXIOM_TOKEN`, `AXIOM_DATASET` | Optional telemetry | — |
| `LOG_LEVEL` | Logging level | INFO |
