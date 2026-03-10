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
| Iter 8 (15 mini) | 15 | 62.7% | 87.5% | 48.8% | 21 | 6 | 22 |
| v29 (15 mini) | 15 | 60.3% | 73.3% | 51.2% | 22 | 11 | 21 |
| v30 (15 mini) | 15 | 60.3% | 73.3% | 51.2% | 22 | 11 | 21 |
| v31 (15 mini) | 15 | 56.8% | 67.7% | 48.8% | 21 | 12 | 22 |
| v32 (15 mini) | 15 | 64.9% | 77.4% | 55.8% | 24 | 11 | 19 |
| v33 (15 mini) | 15 | **79.5%** | **96.7%** | **67.4%** | 29 | 7 | 14 |
| v34 (de-overfit) | 15 | 64.0% | 75.0% | 55.8% | 24 | 11 | 19 |
| v35 (15 mini) | 15 | **73.7%** | **84.8%** | **65.1%** | 28 | 12 | 15 |
| v36 (15 mini) | 15 | **70.9%** | **77.8%** | **65.1%** | 28 | 14 | 15 |
^ OFFLINE RESULTS ^

NEW RESULTS for ONLINE:

| Run | PRs | F1 | Precision | Recall | TP | FP | FN |
|-----|-----|-----|-----------|--------|----|----|-----|
| Online v1 (13 random) | 13 | 55.9% | 57.6% | 54.3% | 19 | 16 | 16 |


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

### Changes in v29-v33 (recall boost + targeted patterns)
- Added VERIFY NEW DEFINITIONS to Step 2: grep to confirm new imports and callback registrations exist
- Added CHECK SPELLING to Step 2: scan new identifiers for typos
- Added concurrency tracing into initialized components: "trace into each component's internal code"
- Added MISSING DEFINITIONS (#9) and QUERY NORMALIZATION (#10) to frequently missed patterns
- Added "silently ignored error returns" to system prompt ALSO REPORT section
- Added LOOP COMPLETENESS and MISSING DEFINITIONS to sweep checklist
- Added "don't report code organization issues" rule for frameworks that load by name
- Added DATA MIGRATION focus on normalization (not just SQL injection)
- Kept sweep at 4 tool rounds (6 rounds caused FP regression)
- Kept max_tool_rounds=35 (45 was catastrophic)

### Changes in v34 (de-overfitting)
- Replaced all benchmark-specific examples with core programming concepts
- Removed: specific typo examples, Rails callback names, library-specific code patterns
- Replaced with: general paradigm descriptions (lifecycle hooks, identifier typos, type hierarchies)
- F1 dropped from 79.5% (overfitted) to 64.0% (honest baseline)

### Changes in v35 (generalizable recall boost)
- Added PORTABILITY (#14) to bug categories: OS-specific shell syntax, platform differences
- Added PLATFORM PORTABILITY (#11) and CRUD COMPLETENESS (#12) to frequently missed patterns
- Added ORM EMPTY DATA (#8), DATA NORMALIZATION (#9), PORTABILITY (#10) to sweep checklist
- Strengthened data format mismatch guidance: focus on normalization in migrations and raw SQL inserts
- Result: +4 TP, +1 FP, -4 FN vs v34. All patterns are generalizable.

### Changes in v36 (imperative sweep + concrete descriptions)
- Added Step 1.5 surface scan: typos, missing imports, wrong variables, inconsistent naming
- Strengthened missing-import instruction: "Do NOT assume it exists, grep NOW"
- Restructured bug description guidance: require (1) exact wrong code, (2) what it should be, (3) runtime consequence
- Made sweep imperative: action verbs ("READ", "LIST", "FIND", "CHECK", "VERIFY") instead of questions
- Reduced sweep from 10 patterns to 5 focused checks
- Restored full self-critique with 6 anti-speculation filters
- Result: F1=70.9%, P=77.8%, R=65.1%. 28 TP, 14 FP, 15 FN. All changes generalizable.

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
2. Run 16 random PRs (2 per repo, seed=42) from the ONLINE benchmark, all in parallel - MUST BE THE ONLINE EVAL
3. Run evaluation on just those 16 PRs - not more 
4. Compare F1/P/R against baseline (51.0% / 55.1% / 47.4%)
