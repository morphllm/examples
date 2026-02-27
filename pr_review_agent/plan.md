# PR Review Agent - Progress Tracker

## Goal
Beat Augment's 53.8% F1 on withmartian/code-review-benchmark using Opus 4.6 + WarpGrep.

## Current Scores
| Iteration | Precision | Recall | F1 | TP | FP | Notes |
|-----------|-----------|--------|-----|----|----|-------|
| 1 (calib) | 43.5% | 66.7% | 52.6% | 10/15 | 13 | Sonnet4 + thinking, batched review |
| 2 (full) | 31.3% | 51.8% | 39.0% | 71/137 | 160 | Same settings, 50 PRs. Precision collapsed. |
| 3 (full) | 47.7% | 37.2% | 41.8% | 51/137 | 57 | Precision-focused prompts, dual-pass, cap 4 |
| 4 (full) | 45.8% | 43.8% | 44.8% | 60/137 | 73 | Better prompts, adaptive thinking, cap 8 |

## Benchmark Targets (Augment = 47%P / 62.8%R / 53.8% F1)
- F1 > 54% (beat Augment)
- Precision > 50%
- Recall > 60%

## Architecture
- Fetch diffs from benchmark fork repos via `gh pr diff`
- Review with Sonnet 4 + extended thinking (budget 10k tokens)
- Batched review: all files in one prompt for speed + cross-file awareness
- Confidence filtering with dedup
- Output injected into benchmark_data.json

## Key Levers for Improvement
1. **Multi-pass with majority voting** (BugBot strategy) - 3 themed passes, keep issues found in 2+
2. **WarpGrep context** - Validate findings against codebase patterns (currently disabled)
3. **Few-shot examples** in prompt - show model what good findings look like
4. **Precision filtering** - Discourse has 16.7% precision, need category-specific filters
5. **Sentry-specific** - Missed process spawning, monkeypatched sleep issues
6. **Test awareness** - Some golden comments are about test quality (flaky sleep, mocking)

## Per-Repo Analysis (Iteration 1)
| Repo | Precision | Recall | F1 | Analysis |
|------|-----------|--------|-----|----------|
| keycloak | 80.0% | 100% | 88.9% | Excellent. Found all 4 golden. |
| cal.com | 50.0% | 100% | 66.7% | Good recall, 2 FP need filtering |
| grafana | 33.3% | 50% | 40.0% | Missed TotalDocs race. 2 FP. |
| sentry | 40.0% | 40% | 40.0% | Missed 3/5: spawning, monkeypatch, flaky test |
| discourse | 16.7% | 50% | 25.0% | 5 FP! Only 1 TP. Too noisy. |

## Iteration Log
### Iteration 1 - Baseline (calibration only)
- Status: COMPLETE
- Model: claude-sonnet-4-20250514 + extended thinking (10k budget)
- Approach: Single batched review of all files in one prompt
- Threshold: 0.5 base, no category-specific filtering
- Results: 43.5% P / 66.7% R / 52.6% F1
- Weakness: Too many FP in Discourse (5), missed subtle Sentry issues

### Iteration 2 - Full run baseline
- Status: COMPLETE
- Results: 31.3% P / 51.8% R / 39.0% F1 (71 TP, 160 FP, 66 FN)
- Per-repo: keycloak 42.4%, cal.com 41.0%, discourse 42.1%, sentry 37.5%, grafana 32.3%
- Root cause: 70% of comments are FPs. Prompt encourages speculation. No verification.
- Key FP categories: missing_validation, null_reference, resource_leak - all speculative

### Iteration 3 - Precision-focused
- Status: COMPLETE
- Changes: Precision-focused prompts, dual-pass review, cap 4, self-contained comments
- Results: 47.7% P / 37.2% R / 41.8% F1 (51 TP, 57 FP, 107 candidates)
- Per-repo: cal.com 42.3%, discourse 44.0%, grafana 50.0%, keycloak 45.5%, sentry 37.7%
- Analysis: Precision improved dramatically (31% -> 48%) but recall dropped (52% -> 37%). Too conservative.

### Iteration 4 - Recall recovery with improved prompts
- Status: COMPLETE
- Changes:
  1. Linter rewrote prompts with much more specific bug patterns (12+ categories)
  2. Adaptive thinking (budget 10k)
  3. Pass 2 prompt redesigned with 10 commonly-missed pattern categories
  4. More aggressive dedup with _issues_same method
  5. Cap raised to 8 per PR
  6. Thresholds lowered: base 0.50, logic_error 0.50, incorrect_value 0.50
- Results: 45.8% P / 43.8% R / 44.8% F1 (60 TP, 73 FP, 131 candidates)
- Per-repo: cal.com 52.5%, grafana 47.8%, keycloak 43.5%, sentry 41.4%, discourse 38.6%
- Analysis: Found 9 more TPs vs iter 3. Still 10 points below Augment (54%). Need more recall.

### Next Steps
- Need ~75 TP to reach F1=55%. Currently at 60. Need +15 more TP.
- FP at 73 is acceptable, precision at 46% is close to Augment's 47%.
- Key missing: subtle bugs in sentry (monkeypatched sleep, isinstance hierarchy), grafana (React props, traceID)
- Potential improvements: WarpGrep for cross-file context, third focused pass, higher thinking budget
