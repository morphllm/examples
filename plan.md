╭─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ Plan to implement                                                                                                               │
│                                                                                                                                 │
│ PR Code Review Agent: Beat withmartian/code-review-benchmark with Opus 4.6 + WarpGrep                                           │
│                                                                                                                                 │
│ Context                                                                                                                         │
│                                                                                                                                 │
│ The withmartian/code-review-benchmark is an open-source evaluation framework for AI code review tools. It has 50 real PRs       │
│ across 5 repos (Sentry/Python, Grafana/Go, Cal.com/TypeScript, Discourse/Ruby, Keycloak/Java) with 137 human-curated "golden    │
│ comments" marking real issues. Current top score is ~52-54% F1 (Augment at 53.8%). We'll build an agent that uses Anthropic     │
│ Opus 4.6 + Morph WarpGrep to beat that.                                                                                         │
│                                                                                                                                 │
│ Why WarpGrep is the edge: Most false positives come from lack of codebase context. Claude Code currently scores 33% F1 (49 TP,  │
│ 99 FP). WarpGrep lets Opus search the full repo to validate findings before reporting them, dramatically improving precision.   │
│                                                                                                                                 │
│ API Keys                                                                                                                        │
│                                                                                                                                 │
│ - MORPH_API_KEY: (set via environment variable)                                                                                 │
│ - ANTHROPIC_API_KEY: (set via environment variable)                                                                             │
│ - GH_TOKEN: Use existing gh CLI auth                                                                                            │
│                                                                                                                                 │
│ Agent Team (3 agents in parallel)                                                                                               │
│                                                                                                                                 │
│ Agent 1: Researcher (Explore + WebSearch)                                                                                       │
│                                                                                                                                 │
│ Research top strategies for AI code review agents, WarpGrep optimization, and prompt engineering for bug detection.             │
│ Deliverables:                                                                                                                   │
│ - Best practices for agent-based code review from papers/blogs                                                                  │
│ - WarpGrep query patterns that maximize context relevance                                                                       │
│ - Analysis of what the 137 golden comments look for (bug taxonomy)                                                              │
│ - How Augment likely achieves high precision + recall                                                                           │
│                                                                                                                                 │
│ Agent 2: Builder (general-purpose, worktree)                                                                                    │
│                                                                                                                                 │
│ Build the full PR review pipeline. Core implementation work.                                                                    │
│                                                                                                                                 │
│ Agent 3: Runner (general-purpose)                                                                                               │
│                                                                                                                                 │
│ Clone benchmarks, run calibration, score results, iterate.                                                                      │
│                                                                                                                                 │
│ Architecture                                                                                                                    │
│                                                                                                                                 │
│ pr_review_agent/                                                                                                                │
│   main.py                    # Entry point: run full benchmark                                                                  │
│   config.py                  # API keys, paths, thresholds                                                                      │
│   pipeline/                                                                                                                     │
│     clone.py                 # Clone benchmark fork repos + checkout PRs                                                        │
│     diff_parser.py           # Parse PR diffs into structured format                                                            │
│     context_gatherer.py      # WarpGrep integration for context                                                                 │
│     reviewer.py              # Opus 4.6 multi-pass review engine                                                                │
│     confidence_filter.py     # Precision filtering with tunable thresholds                                                      │
│     output_formatter.py      # Generate candidates.json for benchmark                                                           │
│   warpgrep/                                                                                                                     │
│     client.py                # MCP tool calls to mcp__morph-mcp__warpgrep_codebase_search                                       │
│     query_planner.py         # Generate targeted queries from diffs                                                             │
│   benchmark/                                                                                                                    │
│     data_loader.py           # Load benchmark_data.json + golden comments                                                       │
│     evaluator.py             # Run step3 judge for scoring                                                                      │
│   prompts/                                                                                                                      │
│     system.py                # System prompts for Opus                                                                          │
│     review.py                # Review prompts per language                                                                      │
│                                                                                                                                 │
│ Pipeline (per PR)                                                                                                               │
│                                                                                                                                 │
│ Step 1: Setup                                                                                                                   │
│                                                                                                                                 │
│ - Clone the benchmark fork repo locally                                                                                         │
│ - Parse the PR diff via GitHub API (gh pr diff)                                                                                 │
│ - Classify changed files by language and substantiveness                                                                        │
│                                                                                                                                 │
│ Step 2: WarpGrep Context Gathering (3-5 queries per PR)                                                                         │
│                                                                                                                                 │
│ For each substantive changed file, use WarpGrep (mcp__morph-mcp__warpgrep_codebase_search) to search the cloned repo:           │
│ 1. "Find all callers of [modified function] in the codebase"                                                                    │
│ 2. "Find tests that cover [modified component]"                                                                                 │
│ 3. "Find similar patterns to [suspicious code] elsewhere in this codebase"                                                      │
│ 4. "Find type/interface definitions used by [changed file]"                                                                     │
│                                                                                                                                 │
│ WarpGrep runs against the local clone, returns (file, line_range) spans. This gives Opus the full picture.                      │
│                                                                                                                                 │
│ Step 3: Multi-Pass Review with Opus 4.6                                                                                         │
│                                                                                                                                 │
│ - Pass 1 (File-level): Review each changed file with diff + WarpGrep context. Identify potential issues with severity +         │
│ confidence scores.                                                                                                              │
│ - Pass 2 (Cross-file): Holistic review of all changes together. Catch interaction bugs, flag Pass 1 false positives.            │
│ - Pass 3 (Calibration): For each candidate issue, explicitly check: "Is this pattern used intentionally elsewhere? Does a test  │
│ validate this? Could this be deliberate?" Assign final confidence 0.0-1.0.                                                      │
│                                                                                                                                 │
│ Step 4: Confidence Filtering                                                                                                    │
│                                                                                                                                 │
│ - Base threshold: 0.7 confidence                                                                                                │
│ - Per-category thresholds (wrong_parameter: 0.5, missing_error_handling: 0.8, etc.)                                             │
│ - Deduplicate issues describing the same problem                                                                                │
│ - Target: ~3-4 comments per PR (matching golden comment density of 2.7/PR)                                                      │
│                                                                                                                                 │
│ Step 5: Output                                                                                                                  │
│                                                                                                                                 │
│ - Generate candidates.json in benchmark format                                                                                  │
│ - Run step3_judge_comments for scoring                                                                                          │
│                                                                                                                                 │
│ Key Prompt Strategy                                                                                                             │
│                                                                                                                                 │
│ The system prompt must:                                                                                                         │
│ 1. Focus on BUGS, not style. Golden comments are: logic errors, wrong parameters, race conditions, type mismatches,             │
│ localization errors, API misuse                                                                                                 │
│ 2. Use WarpGrep evidence to validate: "I confirmed this pattern is NOT used elsewhere, so this is likely a bug" vs "This        │
│ pattern appears in 15 other places, so it's intentional"                                                                        │
│ 3. Be language-specific (Java null safety, Go goroutine leaks, Python Django ORM pitfalls, etc.)                                │
│ 4. Explicitly suppress low-signal categories (style, naming, minor refactors)                                                   │
│                                                                                                                                 │
│ Scoring Targets                                                                                                                 │
│                                                                                                                                 │
│ ┌─────────────────┬────────────────────────┬────────────┐                                                                       │
│ │     Metric      │ Current Best (Augment) │ Our Target │                                                                       │
│ ├─────────────────┼────────────────────────┼────────────┤                                                                       │
│ │ Precision       │ 47.0%                  │ 55%+       │                                                                       │
│ ├─────────────────┼────────────────────────┼────────────┤                                                                       │
│ │ Recall          │ 62.8%                  │ 60%+       │                                                                       │
│ ├─────────────────┼────────────────────────┼────────────┤                                                                       │
│ │ F1              │ 53.8%                  │ 58%+       │                                                                       │
│ ├─────────────────┼────────────────────────┼────────────┤                                                                       │
│ │ True Positives  │ 86/137                 │ 90+/137    │                                                                       │
│ ├─────────────────┼────────────────────────┼────────────┤                                                                       │
│ │ False Positives │ 97                     │ <75        │                                                                       │
│ └─────────────────┴────────────────────────┴────────────┘                                                                       │
│                                                                                                                                 │
│ The edge: WarpGrep context should cut FPs from ~100 to <75 while maintaining recall.                                            │
│                                                                                                                                 │
│ Setup Steps                                                                                                                     │
│                                                                                                                                 │
│ 1. Clone https://github.com/withmartian/code-review-benchmark into working directory                                            │
│ 2. cd offline && uv sync to set up the benchmark pipeline                                                                       │
│ 3. Create pr_review_agent/ project structure                                                                                    │
│ 4. Install deps: pip install anthropic requests tqdm                                                                            │
│ 5. Set env vars (ANTHROPIC_API_KEY, MORPH_API_KEY)                                                                              │
│ 6. Calibrate on 5 PRs (1 per repo), then full run                                                                               │
│                                                                                                                                 │
│ Verification                                                                                                                    │
│                                                                                                                                 │
│ 1. Run on 5 calibration PRs, manually compare output against golden comments                                                    │
│ 2. Run full benchmark: python -m code_review_benchmark.step3_judge_comments                                                     │
│ 3. Check F1 > 52% (minimum), target > 55%                                                                                       │
│ 4. Analyze per-repo breakdown to identify weak spots                                                                            │
│ 5. Iterate on prompts/thresholds for repos with low scores                                                                      │
│                                                                                                                                 │
│ Critical Files to Modify/Create                                                                                                 │
│                                                                                                                                 │
│ All new files in /Users/tejas/personal/applymodel/zmisc/examples/pr_review_agent/                                               │
│                                                                                                                                 │
│ Risks                                                                                                                           │
│                                                                                                                                 │
│ - WarpGrep timeout (30s default) on large repos like Sentry/Grafana. Mitigation: target queries narrowly, increase timeout via  │
│ MORPH_WARP_GREP_TIMEOUT                                                                                                         │
│ - Opus cost (~$50-150 for full run). Mitigation: calibrate on 5 PRs first                                                       │
│ - Judge model variance. Mitigation: use same judge model as the benchmark leaderboard