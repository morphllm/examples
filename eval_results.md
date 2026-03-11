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
