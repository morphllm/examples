# PR Review Agent — Autoresearch Loop

## 1. Objective (immutable)

**Primary metric:** Mean F1 on online eval (15 random recent PRs per run).
**Target:** F1 > 0.55 (current baseline: 0.406 on 66 PRs, v10).
**Files in scope:** `pr_review_agent/prompts/system.py` and `pr_review_agent/pipeline/reviewer.py` ONLY.
**Constraint:** Never overfit. Every change must teach a transferable reasoning pattern, not hard-code a specific bug or answer. No domain-specific knowledge (framework names, library APIs).

## 2. The Loop

```
LOOP FOREVER:
1. READ THIS FILE — especially Ideas Backlog, What's Been Tried, Dead Ends, and Anti-Thrashing Rules.
2. PICK the highest-priority idea from the Ideas Backlog. If the backlog is empty or stale, analyze recent traces/misses to generate new ideas (see Analysis Protocol below).
3. DRAFT the prompt change. Max 2-3 sentences. Must be CONDITIONAL (triggers only in specific situations). Must address a THOUGHT PROCESS gap, not a specific bug type.
4. CHECKPOINT: git add -A && git commit -m "checkpoint before experiment N"
5. APPLY the change to system.py or reviewer.py.
6. SYNTAX CHECK: python3 -c "from pr_review_agent.prompts.system import SYSTEM_PROMPT; print('OK')"
7. DEPLOY: fly deploy --remote-only
8. EVAL (15 random recent PRs):
   export GITHUB_TOKEN=$(gh auth token)
   REVIEW_SERVICE_SECRET=morph-review-2024 \
   python3 online_eval.py --skip-discover --skip-enrich \
     --db code-review-benchmark-online/online/etl/pr_review.db \
     --max-prs 15 --concurrency 10 \
     --output pr_review_agent/output/online_exp<N>.log
9. COMPARE F1 to the last kept baseline:
   - Improved (+3 or more matches on shared PRs) → KEEP
   - Noise (-2 to +2 matches) → KEEP if change is small (1-2 sentences) and theoretically sound; DISCARD if verbose
   - Regressed (-3 or more matches) → DISCARD
10. If KEEP:
    - git add -A && git commit -m "experiment N: <description> — F1 X.XX → Y.YY"
    - Update Score History (append new row)
    - Update What's Been Tried (add entry with outcome + why)
    - Move idea from Backlog to What's Been Tried
11. If DISCARD:
    - git reset --hard HEAD~1 (back to checkpoint)
    - Update What's Been Tried (add entry with outcome + why it failed)
    - Move idea from Backlog to Dead Ends
    - Pick next idea from Backlog → go to step 3
12. Loop back to step 1.
```

### Analysis Protocol (for generating new ideas)

When the backlog is empty or after 3 consecutive DISCARDs:
1. Read 3-5 traces from `pr_review_agent/output/traces/` for PRs where F1 < 0.3
2. For each missed GT item, ask: **"What THOUGHT PROCESS would have led the model to this bug?"** Not "what bug category is this" but "what investigation step was skipped."
3. Look for patterns across misses. Good patterns have 3+ instances.
4. Frame each pattern as a conditional investigation instruction: "When you see X, also check Y"
5. Add to Ideas Backlog with priority and rationale.

## 3. Score History (append-only)

| # | Version | F1 | P | R | PRs | Change | Keep/Discard |
|---|---------|-----|-----|-----|-----|--------|--------------|
| 0 | baseline (pre-v7) | 0.370 | 0.288 | 0.120 | 52 GT | 0.70 thresholds, no borderline, extensive search | baseline |
| 1 | attempt1 | 0.369 | 0.244 | 0.111 | 51 GT | + code quality paragraph, + borderline bug line | DISCARD |
| 2 | attempt2 | 0.418 | 0.301 | 0.128 | 51 GT | - code quality, + completeness principle | KEEP |
| 3 | attempt3 | 0.363 | 0.263 | 0.104 | 52 GT | + config/infra category, + config emphasis | DISCARD |
| 4 | v10 (current) | 0.406 | 0.108 | 0.135 | 26 GT | 13 principles, 17 categories, plan mode, sweep, self-critique | KEEP (best on large eval) |
| 5 | v8 | 0.258 | — | — | 15 | softened CSS, removed frontend cap, per-file 3→5 | DISCARD |
| 6 | v11 | 0.337 | — | — | 62 | confidence floor 0.60→0.50, relaxed dedup | DISCARD |
| 7 | v6 | 0.444 | — | — | 13 (1 match) | broadened to style/docs | DISCARD (tiny sample, 1 match) |

**Current baseline: v10, F1=0.406.** All future experiments compare against this.

## 4. Ideas Backlog

Priority order. Pick from top. Agent adds new ideas at bottom, re-ranks periodically.

### High Priority

- **"Follow the surprise" investigation heuristic.** When the model encounters something unexpected during investigation (a function that does more than its name suggests, a type that's different than expected, a config value that seems wrong), it should STOP and investigate that surprise rather than noting it and moving on. Missed bugs often correlate with the model noticing something odd but not following up. Add 1-2 sentences to reviewer.py's investigation step.

- **Hypothesis-driven search.** Currently the model searches for symbols from the diff. Instead, it should form hypotheses about what could go wrong ("if this lock scope changed, there might be unprotected readers") and search to CONFIRM or DENY each hypothesis. The investigation should be driven by theories about bugs, not just symbol lookup. Modify the WarpGrep instruction block in reviewer.py.

- **"What would break?" pre-mortem.** Before investigating, the model should spend 30 seconds listing 3-5 ways the PR could introduce a bug (based on the type of change: refactor → missed caller, new feature → missing error path, concurrency change → race condition). Then use searches to CHECK each one. This front-loads bug hypotheses rather than hoping to stumble on them.

- **Trace backward from side effects.** When the diff modifies state (writes to DB, updates cache, emits events, sends notifications), trace BACKWARD: "what conditions must be true for this state change to be correct?" Then verify those conditions are actually checked. Currently the model traces forward (input → transform → output) but misses bugs where preconditions are violated.

### Medium Priority

- **Cross-file invariant checking.** When a PR changes a data structure or type definition, the model should ask "what invariants did the old code maintain?" and verify the new code still maintains them. This is different from just grepping callers — it's about understanding the implicit contract.

- **"Second bug in same scope" rule.** When the model finds one bug, it should explicitly look for a second bug in the same function/class/file. Bugs cluster. The model currently reports one finding per area and moves on. Add to the "DON'T STOP AT THE FIRST FINDING" section.

- **Strengthen error path tracing.** The current prompt mentions error paths but the model still under-investigates them. Make it more concrete: "After tracing the success path, explicitly re-read the code assuming every external call FAILS. What state gets corrupted?"

### Low Priority

- **Test assertion accuracy.** The model finds test bugs but misses wrong expected values. Add a specific check: "For each assertion in changed test code, independently compute what the expected value should be. Don't trust the test author's expected value."

- **Migration/schema drift.** When the diff includes DB migrations, the model should verify the migration matches the ORM model definition. Schema drift between migration and model is a common source of runtime errors.

## 5. What's Been Tried (with outcomes)

### Worked (kept)

**Completeness principle (attempt 2, F1: 0.370→0.418).** Added investigation principle about verifying completeness of new additions (resource cleanup, DB constraints, authorization, feature flag both-paths). Worked because it's a conditional trigger that deepens investigation on specific code patterns without broadening scope. +13 gained matches vs -9 lost.

**13 investigation principles + 17 bug categories (v10, F1=0.406 on 66 PRs).** The current prompt architecture. System prompt with specific bug categories and concrete examples is critical for recall. Freeform review + extraction beats report_issue tool. Plan mode with front-loaded WarpGrep searches provides codebase context. max_tool_rounds=25-35 works (50 = model talks itself out of bugs).

**Borderline bug encouragement.** "It is better to report a borderline real bug than to miss one" helps recall without meaningfully hurting precision. The confidence score communicates uncertainty.

### Failed (discarded)

**Code quality paragraph (attempt 1, F1: 0.370→0.369).** Added "ALSO LOOK FOR CODE QUALITY ISSUES." Generated 5 more non-bug suggestions but only 1 matched. Crowded out bug-finding: 18 fewer bug suggestions, 6 lost matches. *Why it failed:* broadened scope, diluted the model's attention from bugs to style issues.

**Config/infrastructure category (attempt 3, F1: 0.418→0.363).** Added bug category #15 for config files + "pay equal attention to config files." Generated 14 more suggestions but 6 fewer matches. *Why it failed:* same pattern as code quality — broadening scope always hurts. Model spent cognitive budget on config analysis at expense of logic bugs.

**Softened CSS suppression (v8, F1→0.258).** Removed frontend caps, raised per-file cap 3→5. Model still gravitates to backend regardless. *Why it failed:* CSS bugs require fundamentally different review approach; loosening caps just added noise.

**Lowered confidence floor (v11, F1→0.337).** Confidence 0.60→0.50, relaxed dedup thresholds. *Why it failed:* lower-confidence findings are lower quality. Produced noise, not signal.

**Broadened to style/docs (v6, F1=0.444 on 13 PRs, 1 match).** Tried capturing style/refactor/docs GT items. *Why it failed:* tiny sample (unreliable), and attempting to catch non-bug GT always dilutes bug precision.

## 6. Dead Ends (anti-thrashing reference)

### Theme: Broadening Scope
Every attempt to make the model look at MORE categories has failed. Code quality, config/infra, CSS emphasis, style/docs — all regressed F1. The model's attention is zero-sum. **Do not try variations of "also look for X" unless X is a specific investigation PROCESS, not a bug category.**

### Theme: Loosening Filters
Lowering confidence floor, relaxing dedup, raising per-file caps — all produced noise. The current thresholds (0.60 confidence floor, per-file cap 3, dedup at 0.25/0.30/0.35/0.50) are well-calibrated. **Do not soften filters without strong evidence.**

### Theme: Catching Non-Bug GT
31% of online GT is style/refactor/docs. These are structurally unreachable without diluting bug precision. **Accept the ~21% bug recall ceiling and optimize WITHIN bug-finding rather than trying to expand categories.**

## 7. Key Lessons (hard constraints — violating these has always regressed F1)

1. **Broadening scope always hurts.** Every attempt to add new categories diluted bug-finding. The model's attention is zero-sum.
2. **Deepening > broadening.** "When you find X, also check Y in the same scope" works. "Also look for Z everywhere" doesn't. Deepening triggers conditionally; broadening fires on every PR.
3. **Small samples lie.** 15-PR evals are dominated by noise. A single PR with 9 CSS GT items = 42% of all GT. Use 15 PRs for quick signal but don't make irreversible decisions on them.
4. **GT ≠ ground truth.** GT is what humans fixed, not all bugs. The model finds real bugs humans didn't fix. Style/refactor/docs are 31% of GT but unreachable.
5. **Non-determinism dominates.** Same prompt gives different bugs each run. ±2 matches on 15 PRs is noise. Only trust ±3 or more.
6. **The model's attention budget is finite.** Every sentence in the prompt competes. Adding a pattern that helps on 2 PRs but distracts on 60 is net negative.
7. **Thought processes > bug lists.** The goal is improving HOW the model investigates, not WHAT bugs it looks for. Bug categories are already comprehensive. The gap is in investigation depth and follow-through.

## 8. Anti-Thrashing Rules

1. **3 consecutive DISCARDs in the same theme** → stop. Pivot to a structurally different approach. Read traces from scratch.
2. **F1 hasn't improved in 5 iterations** → stop iterating on prompt text. Re-read actual traces from `pr_review_agent/output/traces/` and identify what THOUGHT PROCESSES the model is missing. Generate fundamentally new ideas.
3. **An idea adds >3 sentences to the prompt** → split into smaller, independently testable changes. One variable at a time.
4. **Reverting the same idea twice** → it's dead. Move to Dead Ends permanently, even if you think "maybe with a slight tweak." The tweak is a new idea — write it up separately.
5. **Tempted to broaden scope** → re-read Dead Ends section first. Broadening has failed every time. Instead, ask: "How can I make the model go DEEPER on what it already looks at?"

## 9. Eval Commands Reference

```bash
# Syntax check
python3 -c "from pr_review_agent.prompts.system import SYSTEM_PROMPT; print('OK')"

# Deploy
fly deploy --remote-only

# Run eval (15 random recent PRs)
export GITHUB_TOKEN=$(gh auth token)
REVIEW_SERVICE_SECRET=morph-review-2024 \
python3 online_eval.py --skip-discover --skip-enrich \
  --db code-review-benchmark-online/online/etl/pr_review.db \
  --max-prs 15 --concurrency 10 \
  --output pr_review_agent/output/online_exp<N>.log

# Run larger eval (for final validation)
REVIEW_SERVICE_SECRET=morph-review-2024 \
python3 online_eval.py --skip-discover --skip-enrich \
  --db code-review-benchmark-online/online/etl/pr_review.db \
  --max-prs 70 --concurrency 10 \
  --output pr_review_agent/output/online_exp<N>.log
```

## 10. Files Reference

| File | Purpose |
|------|---------|
| `pr_review_agent/prompts/system.py` | System prompt: investigation principles, bug categories, confidence scale. Foundational reasoning. |
| `pr_review_agent/pipeline/reviewer.py` | Review prompt: how-to-review instructions, surface scan, frequently-missed patterns, self-critique. Tactical checklists. |
| `pr_review_agent/output/traces/` | Trace files from eval runs. Read these to understand WHY the model missed bugs. |
| `online_eval.py` | Online eval pipeline. Dispatches to Fly, LLM judge compares. |
| `eval_results.md` | Historical eval results and analysis. |
