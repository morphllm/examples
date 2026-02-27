# Code Review Agent Research Findings

**Goal**: Beat 64% F1 on withmartian/code-review-benchmark (current best: Propel at 64%).
**Our current score**: F1=40% (P=39.1%, R=40.9%) on grafana subset. Full benchmark F1=38.5% (P=31.5%, R=49.6%).

---

## 1. Leaderboard State (Feb 2026)

| Tool               | Precision | Recall | F1   |
| ------------------- | --------- | ------ | ---- |
| **Propel**          | 68%       | 61%    | 64%  |
| Augment Code Review | 65%       | 55%    | 59%  |
| Cursor BugBot       | 60%       | 41%    | 49%  |
| Greptile            | 45%       | 45%    | 45%  |
| Codex Code Review   | 68%       | 29%    | 41%  |
| CodeRabbit          | 36%       | 43%    | 39%  |
| Claude Code         | 23%       | 51%    | 31%  |
| GitHub Copilot      | 20%       | 34%    | 25%  |
| Qodo 2.0            | ~55%      | ~57%   | 60%  |

**Key insight**: Raw Claude Code has 51% recall but only 23% precision. Our agent needs to maintain or improve recall while dramatically boosting precision. We currently have the opposite problem from most tools: reasonable recall (49.6%) but terrible precision (31.5%). We need to kill false positives.

---

## 2. Cursor BugBot Architecture

Source: [Building a better Bugbot](https://cursor.com/blog/building-bugbot)

### Core technique: Shuffled diff ordering + majority voting

**How it works:**
1. Run **8 parallel passes**, each receiving the diff files in a **different random order**. Shuffling nudges the model toward different lines of reasoning (position bias means models over-attend to early content).
2. **Cluster similar findings into buckets** by file region and root cause.
3. **Majority vote**: only keep issues flagged by 2+ passes. Issues from a single pass are dropped.
4. Merge each bucket into a single clear description.
5. **Category filter**: remove unwanted categories (compiler warnings, documentation errors).
6. **Validator model**: run remaining issues through a separate model call specifically designed to catch false positives.
7. Deduplicate against bugs from previous runs.

**Results**: Resolution rate improved from 52% to 70%. Bugs per run: 0.4 to 0.7. Resolved bugs per PR doubled from 0.2 to 0.5.

**Evolution**: BugBot later moved to a fully **agentic design** where the agent can call tools to pull in additional context at runtime (dynamic context retrieval) instead of requiring everything upfront.

### Actionable for us:
- **[HIGH IMPACT] Implement N-pass with shuffled ordering.** Run 4-8 passes with randomized file order. Keep only issues found in 2+ passes. This is the single most effective false positive reduction technique in production.
- **[HIGH IMPACT] Add a dedicated validator/judge pass.** After collecting issues, run a separate LLM call asking "is this actually a bug?" with the issue + relevant code context.
- **[MEDIUM] Category filtering.** We already do this somewhat, but we should be more aggressive about suppressing documentation, style, and defensive programming findings.

---

## 3. Augment Code Review Architecture

Source: [Augment Code Review blog](https://www.augmentcode.com/blog/introducing-augment-code-review)

### Context retrieval strategy
Augment's Context Engine (200k tokens) retrieves **cross-file context** for every PR:
- Dependency chains
- Call sites
- Type definitions
- Tests and fixtures
- Historical changes

They use a proprietary "Codegraph" technology: a three-layer context system with cross-file dependency analysis. The engine indexes 1M+ files and uses smart chunking, priority indexing, and semantic scoring to select relevant context.

### Key insight: "If a comment won't likely change a merge decision, we don't post it."

This philosophy drove their 65% precision. They suppress lint-level noise and only surface correctness issues.

### Model choice
They switched to GPT-5.2 (over Anthropic Sonnet) for code review specifically because it has deeper reasoning at the cost of latency. Quote: "We'd rather wait an extra 30 seconds and catch a subtle concurrency bug than get a fast-but-shallow review."

### Actionable for us:
- **[HIGH IMPACT] Improve context retrieval quality.** Our WarpGrep queries are generic ("error handling patterns"). We should query for specific things referenced in the diff: function definitions, callers of changed functions, type signatures, test files for changed code.
- **[HIGH IMPACT] Severity-based suppression.** Only post comments that would change a merge decision. Our confidence filter needs to be more aggressive: raise thresholds for low-severity findings.
- **[MEDIUM] Consider using a reasoning-heavy model.** Extended thinking budget may be worth increasing for the primary review pass.

---

## 4. Qodo 2.0 Multi-Agent Architecture

Source: [Qodo 2.0 blog](https://www.qodo.ai/blog/introducing-qodo-2-0-agentic-code-review/)

### Architecture: Specialized agents + judge agent

**Agent types:**
1. **Bug Detection Agent** - focuses on correctness issues
2. **Coding Standards Agent** - focuses on rule violations
3. **Risk Assessment Agent** - evaluates risk level of changes
4. **Architecture Agent** - checks structural patterns
5. **Recommendation Agent** - uses PR history and past reviews
6. **Judge Agent** - quality gate that resolves conflicts, removes duplicates, filters low-signal results

**Each agent has dedicated context**: agents don't share one giant prompt. Each gets context relevant to its specialty.

### Context engineering
- Full codebase context (not just the diff)
- **PR history as first-class signal**: past PRs, past review decisions. If a pattern was accepted before, don't flag it.
- Recurring patterns to understand change intent

### Performance: F1=60.1%, highest recall at 56.7%

Qodo prioritized recall: "If a system fails to detect an issue, no amount of post-processing can recover it."

### Actionable for us:
- **[HIGH IMPACT] Add a judge/validator pass.** After primary review, run a separate call that evaluates each finding: "Given the codebase context, is this actually a bug? Is it significant enough to report?" Filter anything the judge doesn't confirm.
- **[MEDIUM] Specialized review passes.** Instead of two passes with slightly different prompts, make each pass focus on a distinct category: (1) logic/correctness bugs, (2) cross-file consistency + API contract violations, (3) security + concurrency.
- **[LOW] PR history signal.** We don't have access to PR history on the benchmark, but for production use, learning from accepted/rejected past comments is valuable.

---

## 5. Propel Code Review (Benchmark Leader)

Source: [Propel Benchmarks](https://www.propelcode.ai/benchmarks)

### Performance: P=68%, R=61%, F1=64%

**Critical finding**: Propel is strongest on high-severity bugs:
- Critical recall: 77.8%
- High recall: 70.7%
- Medium recall: 59.6%
- Low recall: 50.0%

This suggests they have severity-aware filtering that's more aggressive on low-severity findings.

### Architecture
Propel describes itself as "an AI Tech Lead" with a **risk-tiered review system**:
- **Policy layer**: security, compliance, architectural rules
- **Test proof layer**: unit/integration test verification
- **Diff heuristics**: file count, ownership boundaries
- **AI review gates**: tuned for risk and policy detection
- **Human escalation**: high-risk or low-confidence changes

### Actionable for us:
- **[HIGH IMPACT] Risk-tier the review.** Focus review energy on high-impact areas (security, logic, API contracts) and be much more conservative about reporting lower-impact findings. Our current approach treats all categories equally.
- **[HIGH IMPACT] Diff heuristics pre-filtering.** Before LLM review, use heuristics to classify the diff: is it a refactor (low risk), new feature (medium), security change (high), deletion of safety checks (critical)?
- **[MEDIUM] Ownership-aware review.** If a file is a test file, apply different thresholds. If it's configuration, focus on correctness of values.

---

## 6. K-LLM Ensemble Approach (Jose Casanova)

Source: [AI Code Review with K-LLM Orchestration](https://www.josecasanova.com/blog/ai-code-review-opencode)

### Architecture: 6 models, 3 providers, consensus voting

**Models**: Claude Opus 4.6, Sonnet 4.6, GPT-5.2, GPT-5.3, Gemini 3 Pro, Gemini 3 Flash.
**Temperature**: 0.3-0.55 (varied per model for diversity).

**Pipeline:**
1. Generate 6 shuffled variants of the diff using deterministic seeds
2. Run parallel reviews across all models (min 3 successful passes)
3. Cluster findings by file region and root cause
4. Rank by agreement level:
   - **Strong consensus** (4+ models): high confidence
   - **Moderate** (2-3 models): medium confidence
   - **Weak** (1 model): filtered out
5. Trace execution paths against canonical diff to filter false positives

### Actionable for us:
- **[HIGH IMPACT] Multi-model ensemble is likely overkill cost-wise, but multi-pass with the same model + shuffled ordering achieves 80% of the benefit.** The key insight is that varying the input (shuffled diff order + varied temperature) creates diverse reasoning paths, and consensus across paths strongly predicts true positives.
- **[MEDIUM] Use deterministic seeds for shuffling** to ensure reproducibility.

---

## 7. LLM-as-Judge for False Positive Filtering

Source: [Datadog: Using LLMs to filter false positives](https://www.datadoghq.com/blog/using-llms-to-filter-out-false-positives/)

### Key findings from Datadog's production system:

- LLMs can reason about **data flow through functions**, **input validation**, and **whether conditions for exploitation are actually met** to filter FPs from static analysis.
- There is a fundamental **tradeoff in prompt tuning**: prompts optimized for catching true positives tend to misclassify more false positives, and vice versa. This means using **separate prompts** for detection vs. validation is optimal.
- Confidence badges with reasoning explanations let engineers validate results.
- Sequential parameter tuning (system prompt > model type > temperature > top-p) maximizes efficiency.

### Academic findings on LLM judges:
- LLMs are good at identifying valid outputs (TPR >96%) but poor at identifying invalid ones (TNR <25%). This means a judge that says "yes, this is a real bug" is much more reliable than one that says "no, this is not a bug."
- **Simulated Annotators**: generate diverse annotator preferences through in-context learning and estimate confidence as agreement ratio. Significantly improves calibration.

### Actionable for us:
- **[HIGH IMPACT] Two-phase prompting: detect then validate.** Use an aggressive recall-focused prompt for detection, then a precision-focused prompt to validate each finding. Never try to do both in one prompt.
- **[HIGH IMPACT] When the judge says "keep", trust it. When it says "drop", trust it less.** Weight judge "keep" decisions more heavily.
- **[MEDIUM] Add reasoning explanations to judge output** and use agreement between judge reasoning and original finding reasoning as a confidence signal.

---

## 8. Priority Implementation Plan

Based on effort vs. impact analysis for reaching 64% F1 from our current 38.5%:

### Phase 1: Precision fixes (target: P from 31.5% to 55%+)

These require no architecture changes, just prompt/threshold tuning:

1. **Aggressive severity filtering**: Drop all low-severity findings. Only report medium+ severity with confidence >= 0.6. This alone could cut FPs by 30-40%.

2. **Suppress common FP categories entirely**: Remove `missing_validation`, `resource_leak`, `performance`, `documentation`, `style`, `naming`, `refactor` from output. These are almost always FPs.

3. **Dedicated validator pass**: After collecting all issues from passes 1+2, run a third call asking the model to evaluate each issue as a strict judge. Prompt: "You are a senior engineer. For each finding, determine if it describes a bug that will cause incorrect behavior at runtime. Remove anything that is a style preference, defensive programming suggestion, or unverifiable claim about undefined variables."

### Phase 2: Architecture improvements (target: F1 from ~50% to 60%+)

4. **Shuffled multi-pass with voting**: Run 4 passes with shuffled diff file ordering. Keep only issues found in 2+ passes. This is the single highest-impact precision technique across all tools studied.

5. **Better context retrieval**: Instead of generic WarpGrep queries, extract specific identifiers from the diff (function names, class names, imported modules) and query for their definitions, callers, and test coverage. This improves both precision (verifying claims) and recall (understanding context).

6. **Separate detection from validation prompts**: Use a high-recall prompt for detection ("find anything that might be a bug") and a high-precision prompt for validation ("is this definitely a bug?").

### Phase 3: Advanced techniques (target: F1 65%+)

7. **Specialized review agents**: Split into bug-detection pass, cross-file consistency pass, and security/concurrency pass. Each with specialized context and prompts.

8. **Risk-tier the diff**: Before review, classify changed files by risk level (test files = low, security files = high, config = medium). Apply different review strategies per tier.

9. **Temperature/seed diversity**: Use different temperatures (0.3, 0.5, 0.7) across passes to generate diverse reasoning paths, improving the signal from majority voting.

---

## 9. Key Numbers to Remember

- **BugBot**: 8 passes, majority voting, resolution rate 52% -> 70%
- **Augment**: 65% precision via deep context retrieval + merge-decision filtering
- **Propel**: 68% precision, 61% recall, 77.8% recall on critical bugs
- **Qodo**: 60% F1, judge agent for quality gating, PR history as signal
- **K-LLM**: 6 models, 4+ agreement = strong consensus, 1 model = filtered out
- **Our gap**: Precision (31.5% vs 68% target) is the main problem. Recall (49.6%) is actually competitive with Augment (55%) and Qodo (57%).

**Bottom line**: Our recall is decent. The entire improvement opportunity is in precision. The top 3 techniques for precision are: (1) multi-pass majority voting, (2) dedicated judge/validator pass, (3) aggressive severity/category filtering.
