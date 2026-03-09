# PR Review Agent Improvement Plan

## Score History

| Run | PRs | F1 | Precision | Recall | TP | FP | FN |
|-----|-----|-----|-----------|--------|----|----|-----|
| Baseline (50 PRs) | 50 | 51.0% | 55.1% | 47.4% | 65 | 55 | 72 |
| Iter 1 (10 random) | 10 | 55.6% | 90.9% | 40.0% | 10 | 1 | 15 |
| Iter 2 (10 random) | 10 | 63.2% | 92.3% | 48.0% | 12 | 1 | 13 |
| Iter 3 (15 mini) | 15 | 51.5% | 73.9% | 39.5% | 17 | 7 | 26 |
| Iter 4 (15 mini) | 15 | 52.2% | 69.2% | 41.9% | 18 | 11 | 25 |
| Iter 5 (15 mini) | 15 | 55.9% | 76.0% | 44.2% | 19 | 7 | 24 |
| Iter 6 (15 mini) | 15 | 56.3% | 85.7% | 41.9% | 18 | 5 | 25 |
| Iter 7 (15 mini) | 15 | 58.5% | 86.4% | 44.2% | 19 | 5 | 24 |
| Iter 8 (15 mini) | 15 | **62.7%** | 87.5% | 48.8% | 21 | 6 | 22 |

### Changes in Iter 1 (system prompt rewrite)
- Condensed 13 categories to principle-based prompt with INVESTIGATION PRINCIPLES section
- Added: trace both branches, verify call-site contracts, concurrency audit, test rigor, stronger dedup

### Changes in Iter 2 (recall boost)
- Removed "Quality over quantity" — was making model too conservative
- Added "report every bug you find, even borderline ones"
- Added "go through EVERY changed file and ask: did I check this?"
- Increased max_tool_rounds from 25 to 40

### Changes in Iter 3-4 (robustness)
- Fixed _execute_read ValueError crash on malformed line ranges
- Added post-processing dedup (_dedup_issues method)
- Improved evaluator judge prompt for semantic matching

### Changes in Iter 5-6 (targeted patterns)
- Added BUDGET YOUR INVESTIGATION instruction
- Added FREQUENTLY MISSED PATTERNS checklist
- Added asymmetric cache trust, empty ORM updates, dict ordering to system prompt
- Reduced max_tool_rounds from 40 to 30
- All PRs run in parallel (removed 10-PR cap)

### Changes in Iter 7 (trace-informed, targeting 42% of FNs)
- Added "Return to open threads" (Step 2.5) — targets abandonment after first finding (25% of FNs)
- Added "Synthesize grep results explicitly" — targets evidence-not-synthesized (17% of FNs)
- Added "Test bug → trace to production" — targets surface-level test findings
- Relaxed missing-definition rule for grep-confirmed absences

## Step 1: False Negative Analysis

Read through every false negative (missed bug) from the benchmark results. For each one, understand why the model failed to catch it. Then group the FNs by root cause, identifying what investigation behavior the model was missing in each case.

The groups should capture patterns like: what category of bug was missed, and what systematic reasoning step would have caught it. Each group gets a root cause explanation describing the gap in the model's review process.

## Step 2: False Positive Analysis

Read through every false positive (spurious finding) from the benchmark results. For each one, understand why the model flagged it incorrectly. Then group the FPs by root cause.

Important: not all FPs are actionable. Some are duplicates of real bugs (model found the same bug multiple times), some are real bugs the golden set missed, and some are evaluator matching failures. Only the genuinely wrong findings are actionable via system prompt changes. The grouping should distinguish these categories so we know what fraction of FPs we can actually fix.

## Step 3: System Prompt Edits

Based on the FN and FP group analysis, identify the highest-ROI edits to the system prompt in `pr_review_agent/prompts/system.py`. Each edit should target a specific root cause group. FN fixes typically add new investigation rules (things the model should systematically check). FP fixes typically tighten the filtering criteria (things the model should stop reporting).

## Step 4: After Edits

After making all edits to `pr_review_agent/prompts/system.py`:

1. Verify syntax: `code-review-benchmark-online/online/etl/.venv/bin/python3 -c "from pr_review_agent.prompts.system import SYSTEM_PROMPT; print('OK')"`
2. Run 16 random PRs (2 per repo, seed=42) from the offline benchmark, all in parallel
3. Run evaluation on just those 16 PRs
4. Compare F1/P/R against baseline (51.0% / 55.1% / 47.4%)
