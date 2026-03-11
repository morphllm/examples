# Online Eval Results

Evaluating prompt changes on the same set of ~91 PRs (skip-discover, skip-enrich).
Using 10 workers, gpt-5.4, skip_post=True.

## Baseline (Run 2 — 0.70 thresholds, no borderline line, extensive search line)

| Metric | All PRs | PRs w/ GT (52) |
|--------|---------|----------------|
| Suggestions | 208 | 125 |
| Matched suggestions | 36 | 36 |
| Ground truth | 300 | 300 |
| Precision | 0.173 | 0.288 |
| Recall | 0.120 | 0.120 |
| Mean F1 | — | 0.370 (26 PRs) |

## Attempt 1 — Add code quality paragraph + restore borderline bug line

**Changes:**
- Restored "it is better to report a borderline real bug than to miss one" in system.py and reviewer.py
- Added paragraph: "ALSO LOOK FOR CODE QUALITY ISSUES THE AUTHOR WOULD FIX" — inconsistent naming/style, unnecessarily complex logic, duplicated code, dead code, unused imports
- Kept 0.70 thresholds and extensive search line

**Results:**

| Metric | All PRs (90) | PRs w/ GT (51) |
|--------|--------------|----------------|
| Suggestions | 195 | 123 |
| Matched suggestions | 30 | 30 |
| Ground truth | 271 | 271 |
| Precision | 0.154 | 0.244 |
| Recall | 0.111 | 0.111 |
| Mean F1 | — | 0.369 (21 PRs) |

**Analysis:** Code quality paragraph was net negative. Generated 5 more non-bug suggestions (21 vs 16) but only 1 matched. Crowded out bug-finding: 18 fewer bug suggestions, 6 lost matches. Lost matches on 12 PRs vs gained on 8.

## Attempt 2 — Remove code quality, add completeness principle

**Changes:**
- Removed "ALSO LOOK FOR CODE QUALITY ISSUES" paragraph (proven harmful)
- Added investigation principle #5: "VERIFY COMPLETENESS OF NEW ADDITIONS" — resource cleanup/teardown, DB constraints matching business logic, API endpoint authorization, read/write pair consistency, feature flag both-paths
- Kept borderline bug line, 0.70 thresholds, extensive search line

**Results:**

| Metric | All PRs (85) | PRs w/ GT (51) |
|--------|--------------|----------------|
| Suggestions | 195 | 123 |
| Matched suggestions | 37 | 37 |
| Ground truth | 290 | 290 |
| Precision | 0.190 | 0.301 |
| Recall | 0.128 | 0.128 |
| Mean F1 | — | 0.418 (25 PRs) |

**Analysis:** Best run so far. Removing code quality paragraph refocused the model on bugs. Completeness principle helped find resource cleanup, constraint, and lifecycle bugs. +13 gained matches vs -9 lost on shared PR set. Bug match rate improved from 16% to 19%, security from 10% to 18%.

## Attempt 3 — Add config/infrastructure category + investigation budget emphasis

**Changes:**
- Added bug category #15: "Configuration / infrastructure" — missing env var mappings, Dockerfile steps, CI workflow permissions, version pins
- Strengthened "DON'T STOP AT THE FIRST FINDING" with "Pay equal attention to config files as to source code"

**Results:**

| Metric | All PRs (91) | PRs w/ GT (52) |
|--------|--------------|----------------|
| Suggestions | 209 | — |
| Matched suggestions | 31 | 31 |
| Ground truth | 299 | 299 |
| Precision | 0.148 | 0.263 |
| Recall | 0.104 | 0.104 |
| Mean F1 | — | 0.363 (24 PRs) |

**Analysis:** Regressed vs Attempt 2. Same pattern as Attempt 1 — broadening scope dilutes bug-finding focus. Generated 14 more suggestions but 6 fewer matches. Lost on 15 shared PRs vs gained on 8. Config emphasis caused the model to spend cognitive budget on config analysis at the expense of core logic bugs.

## Summary

| Run | P (GT) | Recall | Mean F1 | Key Change |
|-----|--------|--------|---------|------------|
| Baseline | 0.288 | 0.120 | 0.370 | 0.70 thresholds, no borderline, extensive search |
| Attempt 1 | 0.244 | 0.111 | 0.369 | + code quality paragraph, + borderline bug line |
| **Attempt 2** | **0.301** | **0.128** | **0.418** | - code quality, + completeness principle |
| Attempt 3 | 0.263 | 0.104 | 0.363 | + config/infra category, + config emphasis |

**Best: Attempt 2.** Key insight: the model performs best when its scope is tightly focused on runtime defects + structural completeness. Any instruction that broadens scope to non-bug categories (code quality, config/infra) dilutes focus and hurts precision without meaningfully improving recall.

---

## New Eval Runs (v7+ prompt with 13 investigation principles)

Using the improved prompt from main branch (13 principles, 14 bug categories, 15 frequently-missed patterns, plan mode, sweep, self-critique). Evaluated on coderabbitai[bot] PRs from the benchmark DB.

### v10 — Full 70-PR eval with v7 prompt (best)

**Prompt state:** system.py with 13 principles + 17 categories, reviewer.py with expectation-driven review, three-question filter, self-critique, surface scan, confidence floor 0.60, per-file cap 3, dedup thresholds 0.25/0.30/0.35/0.50.

| Metric | All PRs (66) | PRs w/ GT (26) |
|--------|-------------|----------------|
| Suggestions | 166 | — |
| Matched suggestions | 18 | 18 |
| Ground truth | 133 | 133 |
| Precision | 0.108 | — |
| Recall | 0.135 | 0.135 |
| Mean PR F1 | — | 0.406 (13 PRs) |

**GT breakdown:** 80 bugs (21% match rate), 21 style (0%), 13 refactor (0%), 7 docs (0%), 4 improvement (0%), 3 security (0%), 2 performance (50%).

### Failed experiments

| Version | Change from v10 | Result | Why it failed |
|---------|-----------------|--------|---------------|
| v8 | Softened CSS suppression, removed frontend cap, per-file cap 3→5 | F1=0.258 (15 PRs) | Model still gravitates to backend bugs regardless of CSS rules |
| v11 | Confidence floor 0.60→0.50, relaxed dedup thresholds | F1=0.337 (62 PRs) | Fewer high-quality findings, more noise |
| v6 | Broadened scope to style/docs, softened suppressions | F1=0.444 (13 PRs, 1 match) | Same pattern as Attempt 1 — dilutes bug-finding |

### Key findings from v10 analysis

1. **Bug recall ceiling is ~21%**: Model finds real bugs but different ones than humans fix. On 80 bug GT items, we match 17 (21%). The model averages 2.4 suggestions/PR.
2. **Style/refactor/docs are unreachable**: 41/133 GT items (31%) are in categories our prompt excludes. Attempting to catch them (v6, v8) hurts bug precision without meaningful style recall.
3. **Non-determinism dominates small samples**: Same prompt gives different bugs each run. A/B tests on 15 PRs are unreliable — need 50+ PRs for stable signal.
4. **CHAT-TONER outlier**: One PR has 9 CSS GT items (7% of all GT). Our model correctly finds backend bugs but 0 CSS issues. CSS review requires fundamentally different approach.
5. **Strong performers exist**: 6 PRs scored F1≥0.50 (highest 0.667). Model excels on PRs where GT is actual bugs rather than style/refactor.
