#!/usr/bin/env python3
"""Online evaluation for the Morph PR review service.

Finds recent PRs from GitHub (via the code-review-benchmark ETL), dispatches
them to the Morph review service on Fly, retrieves the posted review, then
judges the review quality with a 3-step LLM pipeline.

Pipeline:
  1. Discover + enrich PRs (delegates to benchmark ETL via subprocess)
  2. Dispatch each PR to the Morph review service
  3. Poll GitHub for the posted review
  4. Run the 3-step LLM judge (extract suggestions → extract fixes → match)
  5. Print aggregate precision / recall / F1

Usage:
    # Full pipeline (needs GCP_PROJECT for BigQuery)
    uv run python online_eval.py --max-prs 20

    # Skip discovery, use PRs already in the benchmark DB
    uv run python online_eval.py --skip-discover --max-prs 20

    # Skip reviews too — just re-judge existing results
    uv run python online_eval.py --skip-discover --skip-enrich --skip-review

Env vars:
    GITHUB_TOKEN          GitHub PAT with repo access
    REVIEW_SERVICE_URL    Fly review service (default: https://morph-ghapp.fly.dev)
    REVIEW_SERVICE_SECRET Auth secret for the review service
    OPENAI_API_KEY        For the LLM judge
    GCP_PROJECT           For BigQuery discovery (skip with --skip-discover)
    JUDGE_MODEL           Judge model (default: gpt-4.1)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("online_eval")

SCRIPT_DIR = Path(__file__).parent
ETL_DIR = SCRIPT_DIR / "code-review-benchmark" / "online" / "etl"


# ---------------------------------------------------------------------------
# Pydantic schemas (mirrored from benchmark for self-containedness)
# ---------------------------------------------------------------------------

class BotSuggestion(BaseModel):
    issue_id: str
    description: str
    category: str
    file_path: str | None = None
    line_number: int | None = None
    severity: str = "medium"

class BotSuggestionsResponse(BaseModel):
    suggestions: list[BotSuggestion]

class HumanAction(BaseModel):
    action_id: str
    description: str
    category: str
    file_path: str | None = None
    commit_sha: str | None = None
    action_type: str

class HumanActionsResponse(BaseModel):
    actions: list[HumanAction]

class MatchResult(BaseModel):
    bot_issue_id: str
    human_action_id: str | None = None
    matched: bool
    confidence: float
    reasoning: str

class MatchingResponse(BaseModel):
    matches: list[MatchResult]


# ---------------------------------------------------------------------------
# LLM prompts (from benchmark)
# ---------------------------------------------------------------------------

EXTRACT_BOT_SUGGESTIONS = """You are analyzing a pull request to extract all actionable suggestions made by a code review bot.

The bot's username is: {bot_username}

Below you will see:
1. The commits that were under review (the code state the bot saw), including full diffs
2. The bot's review comments on those commits

For each actionable suggestion the bot made, extract:
- A unique ID (S1, S2, ...)
- A description of what was suggested
- The category (bug, style, performance, security, refactor, documentation, other)
- The file path and line number if available
- Severity (low, medium, high, critical)

Only include ACTIONABLE suggestions — skip generic praise, summaries, or "looks good" comments.

PR Title: {pr_title}
PR Author: {pr_author}
Repository: {repo_name}

=== Commits Under Review ===
{commits_under_review}

=== Bot Review Comments ===
{bot_comments}
"""

EXTRACT_HUMAN_ACTIONS = """You are analyzing post-review commit diffs to extract every concrete code issue that was fixed AFTER the bot reviewed the PR.

The bot's username is: {bot_username}

For each distinct code issue fixed in the post-review commits, extract:
- A unique ID (A1, A2, ...)
- A description of the issue that was fixed (what was wrong, not what was done)
- The category (bug, style, performance, security, refactor, documentation, other)
- The file path
- The commit SHA
- Action type (fix, improvement, cleanup, new_feature, other)

Focus on the DIFFS. One action per distinct issue.

PR Title: {pr_title}
PR Author: {pr_author}
Repository: {repo_name}

=== Post-Review Commits ===
{post_review_commits}

=== Post-Review Activity ===
{post_review_activity}
"""

JUDGE_MATCHING = """You are judging whether a bot's code review suggestions correspond to actual code issues that were later fixed.

The bot's username is: {bot_username}

For EACH bot suggestion, determine:
1. Does it match any code fix? (matched: true/false)
2. Which fix? (human_action_id)
3. Confidence (0.0-1.0)
4. Brief reasoning

A suggestion is "matched" if it identified the same issue that was later fixed, even partially.

=== Bot Suggestions ===
{bot_suggestions}

=== Code Fixes (ground truth) ===
{human_actions}
"""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class EvalConfig:
    github_token: str = ""
    review_service_url: str = "https://morph-ghapp.fly.dev"
    review_service_secret: str = ""
    judge_base_url: str = "https://api.openai.com/v1"
    judge_api_key: str = ""
    judge_model: str = "gpt-4.1"
    gcp_project: str = ""
    max_prs: int = 50
    days_back: int = 7
    reference_bot: str = "coderabbitai[bot]"
    concurrency: int = 5
    db_path: str = "online_eval.db"
    output: str = "online_eval_results.json"

    @classmethod
    def from_env(cls, args: argparse.Namespace) -> EvalConfig:
        return cls(
            github_token=os.environ.get("GITHUB_TOKEN", ""),
            review_service_url=os.environ.get("REVIEW_SERVICE_URL", "https://morph-ghapp.fly.dev"),
            review_service_secret=os.environ.get("REVIEW_SERVICE_SECRET", ""),
            judge_base_url=os.environ.get("JUDGE_BASE_URL", "https://api.openai.com/v1"),
            judge_api_key=os.environ.get("OPENAI_API_KEY", ""),
            judge_model=os.environ.get("JUDGE_MODEL", "gpt-4.1"),
            gcp_project=os.environ.get("GCP_PROJECT", ""),
            max_prs=args.max_prs,
            days_back=args.days_back,
            reference_bot=args.reference_bot,
            concurrency=args.concurrency,
            db_path=args.db,
            output=args.output,
        )


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

class LLMJudge:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.model = model
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def structured(self, prompt: str, schema: type[BaseModel]):
        resp = await self._client.beta.chat.completions.parse(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format=schema,
            temperature=1.0,
        )
        return resp.choices[0].message.parsed

    async def close(self):
        await self._client.close()


# ---------------------------------------------------------------------------
# Step 1: Discover + Enrich via benchmark ETL subprocess
# ---------------------------------------------------------------------------

def run_etl_step(step: str, cfg: EvalConfig, extra_args: list[str] | None = None):
    """Run a benchmark ETL step via uv in the ETL directory."""
    cmd = ["uv", "run", "python", "main.py", step]
    cmd += ["--chatbot", cfg.reference_bot]
    if extra_args:
        cmd += extra_args

    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{Path(cfg.db_path).resolve()}",
        "GITHUB_TOKEN": cfg.github_token,
        "GCP_PROJECT": cfg.gcp_project,
    }

    logger.info(f"Running ETL: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ETL_DIR), env=env, capture_output=False)
    if result.returncode != 0:
        logger.error(f"ETL step '{step}' failed with code {result.returncode}")
        sys.exit(1)


def discover_and_enrich(cfg: EvalConfig, skip_discover: bool, skip_enrich: bool):
    if not skip_discover:
        # Limit discovery to avoid enriching thousands of PRs we'll never use
        max_per_day = max(cfg.max_prs * 2, 10)
        run_etl_step("discover", cfg, [
            "--days-back", str(cfg.days_back),
            "--max-prs-per-day", str(max_per_day),
        ])
    if not skip_enrich:
        run_etl_step("enrich", cfg, ["--one-shot", "--max-prs", str(cfg.max_prs)])


# ---------------------------------------------------------------------------
# Step 2: Fetch assembled PRs from the SQLite DB directly
# ---------------------------------------------------------------------------

async def load_assembled_prs(db_path: str, reference_bot: str, limit: int) -> list[dict]:
    """Load assembled PRs from the benchmark SQLite DB."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get chatbot_id
    cur.execute("SELECT id FROM chatbots WHERE github_username = ?", (reference_bot,))
    row = cur.fetchone()
    if not row:
        logger.error(f"Chatbot {reference_bot} not found in DB")
        conn.close()
        return []
    chatbot_id = row["id"]

    # Get assembled PRs (status = 'assembled') that haven't been analyzed yet
    cur.execute(
        """
        SELECT p.* FROM prs p
        WHERE p.chatbot_id = ? AND p.status = 'assembled'
        ORDER BY p.discovered_at DESC
        LIMIT ?
        """,
        (chatbot_id, limit),
    )
    prs = [dict(r) for r in cur.fetchall()]
    conn.close()

    logger.info(f"Loaded {len(prs)} assembled PRs from {db_path}")
    return prs


# ---------------------------------------------------------------------------
# Step 3: Dispatch Morph reviews
# ---------------------------------------------------------------------------

async def dispatch_and_collect(cfg: EvalConfig, prs: list[dict]) -> list[dict]:
    """Dispatch PRs to the Morph review service and collect results."""
    if not prs:
        return []

    logger.info(f"Dispatching {len(prs)} PRs to Morph (concurrency={cfg.concurrency})...")
    sem = asyncio.Semaphore(cfg.concurrency)
    results = []

    async with httpx.AsyncClient(timeout=60) as http:
        async def _do_one(pr: dict) -> dict | None:
            async with sem:
                repo_name = pr["repo_name"]
                pr_number = pr["pr_number"]
                owner, repo_short = repo_name.split("/", 1)

                commits = json.loads(pr.get("commits") or "[]")
                if not commits:
                    logger.warning(f"No commits for {repo_name}#{pr_number}")
                    return None
                head_sha = commits[-1].get("sha", "")

                # Dispatch
                headers = {"Content-Type": "application/json"}
                if cfg.review_service_secret:
                    headers["Authorization"] = f"Bearer {cfg.review_service_secret}"

                payload = {
                    "owner": owner,
                    "repo": repo_short,
                    "pr_number": pr_number,
                    "head_sha": head_sha,
                    "github_token": cfg.github_token,
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "skip_post": True,  # Don't post to GitHub
                }

                try:
                    resp = await http.post(
                        f"{cfg.review_service_url}/review",
                        json=payload,
                        headers=headers,
                        timeout=300,  # Reviews can take a few minutes
                    )
                    if resp.status_code != 200:
                        logger.warning(f"  {repo_name}#{pr_number}: service returned {resp.status_code}")
                        return None
                except Exception as e:
                    logger.error(f"  {repo_name}#{pr_number}: dispatch failed: {e}")
                    return None

                data = resp.json()
                comments = data.get("comments", [])
                logger.info(f"  Got {len(comments)} comments for {repo_name}#{pr_number}")

                # Convert service comments to the format the judge expects
                morph_reviews = [{
                    "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "state": "COMMENTED",
                    "body": f"Found {len(comments)} issue{'s' if len(comments) != 1 else ''}",
                    "inline_comments": [
                        {
                            "path": c.get("file_path", ""),
                            "line": c.get("line_number"),
                            "body": c.get("body", ""),
                            "diff_hunk": "",
                        }
                        for c in comments
                    ],
                }]
                return {"pr": pr, "morph_reviews": morph_reviews}

        tasks = [asyncio.create_task(_do_one(pr)) for pr in prs]
        for coro in asyncio.as_completed(tasks):
            r = await coro
            if r:
                results.append(r)

    logger.info(f"Collected {len(results)}/{len(prs)} reviews")
    return results


# ---------------------------------------------------------------------------
# Step 4: Judge
# ---------------------------------------------------------------------------

def _fmt_morph(reviews: list[dict]) -> str:
    lines = []
    for rv in reviews:
        ts = rv.get("submitted_at", "")
        body = rv.get("body", "")
        lines.append(f"[{ts}] REVIEW ({rv.get('state', '')}):")
        if body:
            lines.append(f"  {body}")
        for c in rv.get("inline_comments", []):
            path = c.get("path", "")
            line = c.get("original_line") or c.get("line", "")
            lines.append(f"[{ts}] REVIEW_COMMENT ({path}:{line}):")
            if c.get("diff_hunk"):
                lines.append(f"  ```\n{c['diff_hunk']}\n  ```")
            if c.get("body"):
                lines.append(f"  {c['body']}")
        lines.append("")
    return "\n".join(lines) or "(no comments)"


def _fmt_commits(commits: list[dict], details: dict) -> str:
    if not commits:
        return "(no commits)"
    lines = []
    for c in commits:
        sha = c.get("sha", "")[:12]
        lines.append(f"COMMIT {sha} by {c.get('author', '?')} [{c.get('date', '')}]")
        lines.append(f"  {c.get('message', '')}")
        d = details.get(c.get("sha", ""), {})
        for f in d.get("files", []):
            lines.append(f"  {f.get('status', '').upper()} {f.get('filename', '')} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})")
            if f.get("patch"):
                lines.append(f"  ```diff\n{f['patch']}\n  ```")
        lines.append("")
    return "\n".join(lines)


def _fmt_suggestions(suggestions: list[dict]) -> str:
    lines = []
    for s in suggestions:
        loc = ""
        if s.get("file_path"):
            loc = f" ({s['file_path']}:{s.get('line_number', '')})"
        lines.append(f"- [{s['issue_id']}] ({s['category']}/{s['severity']}){loc}: {s['description']}")
    return "\n".join(lines) or "(none)"


def _fmt_actions(actions: list[dict]) -> str:
    lines = []
    for a in actions:
        loc = f" ({a['file_path']})" if a.get("file_path") else ""
        lines.append(f"- [{a['action_id']}] ({a['category']}/{a['action_type']}){loc}: {a['description']}")
    return "\n".join(lines) or "(none)"


async def judge_all(cfg: EvalConfig, results: list[dict]) -> list[dict]:
    if not results:
        return []

    llm = LLMJudge(cfg.judge_base_url, cfg.judge_api_key, cfg.judge_model)
    sem = asyncio.Semaphore(cfg.concurrency)
    judgments = []

    async def _judge(item: dict) -> dict | None:
        async with sem:
            pr = item["pr"]
            repo_name = pr["repo_name"]
            pr_number = pr["pr_number"]

            assembled = json.loads(pr.get("assembled") or "{}")
            commits = json.loads(pr.get("commits") or "[]")
            commit_details = json.loads(pr.get("commit_details") or "[]")
            reviews_raw = json.loads(pr.get("reviews") or "[]")
            events = assembled.get("events", [])

            # Find split point
            bot_lower = cfg.reference_bot.lower()
            hash_x = None
            for r in reviews_raw:
                author = (r.get("author") or r.get("user", {}).get("login", "")).lower()
                if author == bot_lower and r.get("commit_id"):
                    hash_x = r["commit_id"]
                    break
            if not hash_x and commits:
                hash_x = commits[-1].get("sha")

            pre, post = [], []
            if hash_x:
                for i, c in enumerate(commits):
                    sha = c.get("sha", "")
                    if sha == hash_x or sha.startswith(hash_x) or hash_x.startswith(sha):
                        pre, post = commits[:i + 1], commits[i + 1:]
                        break
                else:
                    pre, post = commits, []
            else:
                pre, post = commits, []

            details = {d["sha"]: d for d in commit_details if d.get("sha")}

            morph_text = _fmt_morph(item["morph_reviews"])
            pre_text = _fmt_commits(pre, details)
            post_text = _fmt_commits(post, details)

            pr_title = assembled.get("pr_title", "")
            pr_author = assembled.get("pr_author", "unknown")

            try:
                # Step 1: extract morph suggestions
                s_resp = await llm.structured(
                    EXTRACT_BOT_SUGGESTIONS.format(
                        bot_username="morph-subagents[bot]",
                        pr_title=pr_title, pr_author=pr_author, repo_name=repo_name,
                        commits_under_review=pre_text, bot_comments=morph_text,
                    ),
                    BotSuggestionsResponse,
                )
                suggestions = [s.model_dump() for s in s_resp.suggestions]

                # Step 2: extract ground truth
                a_resp = await llm.structured(
                    EXTRACT_HUMAN_ACTIONS.format(
                        bot_username="morph-subagents[bot]",
                        pr_title=pr_title, pr_author=pr_author, repo_name=repo_name,
                        post_review_commits=post_text, post_review_activity="(see commits above)",
                    ),
                    HumanActionsResponse,
                )
                actions = [a.model_dump() for a in a_resp.actions]

                # Step 3: match
                m_resp = await llm.structured(
                    JUDGE_MATCHING.format(
                        bot_username="morph-subagents[bot]",
                        bot_suggestions=_fmt_suggestions(suggestions),
                        human_actions=_fmt_actions(actions),
                    ),
                    MatchingResponse,
                )
                matches = [m.model_dump() for m in m_resp.matches]

                # Metrics
                s_ids = {s["issue_id"] for s in suggestions}
                a_ids = {a["action_id"] for a in actions}
                matched_s = {m["bot_issue_id"] for m in matches if m["matched"] and m.get("bot_issue_id") in s_ids}
                matched_a = {m["human_action_id"] for m in matches if m["matched"] and m.get("human_action_id") in a_ids}

                n_s, n_ms = len(suggestions), len(matched_s)
                n_a, n_ma = len(actions), len(matched_a)
                p = n_ms / n_s if n_s else None
                r = n_ma / n_a if n_a else None
                f1 = 2 * p * r / (p + r) if p and r and (p + r) > 0 else None

                ps = f"{p:.2f}" if p is not None else "N/A"
                rs = f"{r:.2f}" if r is not None else "N/A"
                fs = f"{f1:.2f}" if f1 is not None else "N/A"
                logger.info(f"  {repo_name}#{pr_number}: {n_s} sug, P={ps} R={rs} F1={fs}")

                return {
                    "repo_name": repo_name, "pr_number": pr_number,
                    "total_suggestions": n_s, "matched_suggestions": n_ms,
                    "total_actions": n_a, "matched_actions": n_ma,
                    "precision": p, "recall": r, "f1": f1,
                    "suggestions": suggestions, "actions": actions, "matches": matches,
                }
            except Exception as e:
                logger.error(f"  Judge failed {repo_name}#{pr_number}: {e}")
                return None

    tasks = [asyncio.create_task(_judge(r)) for r in results]
    for coro in asyncio.as_completed(tasks):
        j = await coro
        if j:
            judgments.append(j)

    await llm.close()
    return judgments


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(judgments: list[dict], output_path: str):
    if not judgments:
        print("\nNo judgments to summarize.")
        return

    n_s = sum(j["total_suggestions"] for j in judgments)
    n_ms = sum(j["matched_suggestions"] for j in judgments)
    n_a = sum(j["total_actions"] for j in judgments)
    n_ma = sum(j["matched_actions"] for j in judgments)
    ps = [j["precision"] for j in judgments if j["precision"] is not None]
    rs = [j["recall"] for j in judgments if j["recall"] is not None]
    fs = [j["f1"] for j in judgments if j["f1"] is not None]

    print("\n" + "=" * 60)
    print("MORPH ONLINE EVAL RESULTS")
    print("=" * 60)
    print(f"PRs evaluated:         {len(judgments)}")
    print(f"Total suggestions:     {n_s}")
    print(f"Matched suggestions:   {n_ms}")
    print(f"Total ground-truth:    {n_a}")
    print(f"Matched ground-truth:  {n_ma}")
    print()
    if n_s:
        print(f"Aggregate precision:   {n_ms / n_s:.3f}")
    if n_a:
        print(f"Aggregate recall:      {n_ma / n_a:.3f}")
    if ps:
        print(f"Mean PR precision:     {sum(ps) / len(ps):.3f}")
    if rs:
        print(f"Mean PR recall:        {sum(rs) / len(rs):.3f}")
    if fs:
        print(f"Mean PR F1:            {sum(fs) / len(fs):.3f}")
    print("=" * 60)

    Path(output_path).write_text(json.dumps(judgments, indent=2, default=str))
    print(f"\nDetailed results saved to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def async_main(args: argparse.Namespace):
    cfg = EvalConfig.from_env(args)

    if not cfg.github_token:
        print("ERROR: GITHUB_TOKEN required", file=sys.stderr); sys.exit(1)
    if not cfg.judge_api_key:
        print("ERROR: OPENAI_API_KEY required", file=sys.stderr); sys.exit(1)

    # Step 1: Discover + enrich (via benchmark ETL subprocess)
    if not args.skip_discover:
        if not cfg.gcp_project:
            print("ERROR: GCP_PROJECT required for discovery (or use --skip-discover)", file=sys.stderr)
            sys.exit(1)

    discover_and_enrich(cfg, args.skip_discover, args.skip_enrich)

    # Step 2: Load PRs from DB
    prs = await load_assembled_prs(cfg.db_path, cfg.reference_bot, cfg.max_prs)
    if not prs:
        print("No assembled PRs found. Run without --skip-discover/--skip-enrich.")
        return

    # Step 3: Dispatch reviews
    if args.skip_review:
        logger.info("Skipping review dispatch (--skip-review)")
        results = []
    else:
        results = await dispatch_and_collect(cfg, prs)

    # Step 4: Judge
    judgments = await judge_all(cfg, results)

    # Step 5: Summary
    print_summary(judgments, cfg.output)


def main():
    parser = argparse.ArgumentParser(description="Morph online eval")
    parser.add_argument("--max-prs", type=int, default=50)
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--reference-bot", default="coderabbitai[bot]")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--skip-discover", action="store_true")
    parser.add_argument("--skip-enrich", action="store_true")
    parser.add_argument("--skip-review", action="store_true")
    parser.add_argument("--db", default="online_eval.db")
    parser.add_argument("--output", default="online_eval_results.json")
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
