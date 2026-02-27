# AI Code Review Agent: Strategy Research

Research compiled from Augment, Cursor BugBot, Qodo, CodeRabbit, Greptile, and the WithMartian benchmark (Feb 2026).

---

## 1. WithMartian Benchmark: How It Works

**Dataset**: 50 PRs from 5 open-source projects (Sentry, Grafana, Cal.com, Discourse, Keycloak) across Python, Go, TypeScript, Ruby, Java. Each PR has human-curated golden comments with severity labels (Low/Medium/High/Critical).

**Scoring**: LLM judge matches your review comments against golden comments. The judge asks "do these describe the same underlying issue?" Different wording is fine; only substance matters. Computes precision, recall, and F1.

**Key insight for scoring well**: The judge is semantic, not lexical. You don't need to match exact wording. You need to identify the same underlying issue. A comment that says "potential null dereference on line 42 when config is missing" matches a golden comment saying "missing null check for config object" even though the wording differs completely.

### Current Leaderboard (Offline Benchmark)

| Tool | Precision | Recall | F-score |
|------|-----------|--------|---------|
| Augment | 65% | 55% | 59% |
| Qodo | ~60% | ~60% | 60.1% |
| Cursor BugBot | 60% | 41% | 49% |
| Greptile | 45% | 45% | 45% |
| Codex | 68% | 29% | 41% |
| CodeRabbit | 36% | 43% | 39% |
| Claude Code | 23% | 51% | 31% |
| GitHub Copilot | 20% | 34% | 25% |

**Observations**:
- Codex has highest precision (68%) but terrible recall (29%) = too conservative
- Claude Code has decent recall (51%) but awful precision (23%) = too noisy
- Winners (Augment, Qodo) balance both metrics
- The gap between 59% and 25% is entirely about context retrieval and noise filtering

---

## 2. What Makes Top Tools Score High

### The #1 Differentiator: Context Retrieval

Every source agrees: **context retrieval is the bottleneck, not generation**. The LLM is smart enough to find bugs IF it sees the right code. Most tools fail because they only see the diff.

What top tools retrieve:
- **Dependency chains**: What functions/classes are imported and used by changed code
- **Call sites**: Who calls the changed function, and with what arguments
- **Type definitions**: Full type info that affects nullability, error handling
- **Test files**: Related tests that should be updated or that reveal expected behavior
- **Historical context**: Previous changes, past review comments, why decisions were made
- **Cross-file interactions**: How the changed code affects other parts of the system

**Augment's advantage**: "Consistently retrieved the correct dependency chains, call sites, type definitions, tests, and related modules." This is what gave them 65% precision + 55% recall.

### Implementation Tactics for Context

1. **AST-based parsing** (CodeRabbit uses Tree-sitter): Instead of "line 10 changed," understand "method calculate_total in Class Cart changed." Extract symbols, then resolve their dependencies.

2. **Graph-based dependency analysis** (Greptile/CodeRabbit): Build a dependency graph of the codebase. When code changes, traverse the graph to find affected code.

3. **Semantic search on code** (Greptile insight): Translating code to natural language before embedding works better than embedding raw code. Chunk at function level, not file level.

4. **Dynamic context at runtime** (BugBot): Don't preload everything. Give the agent tools to search the codebase and let it pull what it needs. "Even small changes in tool design or availability had an outsized impact on outcomes."

5. **1:1 code-to-context ratio** (CodeRabbit): For every line of code under review, provide equivalent weight of contextual information.

---

## 3. Reducing False Positives

### Strategy A: Multi-Pass Majority Voting (BugBot v1)

BugBot's original approach:
1. Run 8 parallel bug-finding passes with **randomized diff ordering** (shuffled file order forces different reasoning paths)
2. Cluster similar findings across passes
3. **Majority voting**: If only 1 pass finds an issue, it's likely noise. If 4+ passes independently find it, it's real.
4. Category filtering (remove compiler warnings, documentation nits)
5. Validator model as final false-positive filter
6. Dedup against previous review runs

**Result**: Resolution rate went from 52% to 76% over 40 experiments.

### Strategy B: K-LLM Ensemble (OpenCode k-review)

Send the same diff to 6 different LLMs with shuffled orderings:
- Claude Opus 4.6 (temp 0.3), Sonnet 4.6 (0.4)
- GPT 5.2 (0.35), GPT 5.3 Codex (0.45)
- Gemini 3 Pro (0.5), Flash (0.55)

Consensus thresholds:
- 4+/6 models agree = **strong** (high confidence)
- 2-3/6 agree = **moderate**
- 1/6 only = **weak** (likely false positive)

Validation step traces execution paths against canonical diff to eliminate remaining false positives.

**Key insight**: Different models have different biases. Cross-model agreement is a stronger signal than same-model agreement.

### Strategy C: Specialized Micro-Agents (Qodo)

12+ specialized agents, each focused on one concern:
- Backend bugs agent
- UI issues agent
- Runtime failures agent
- Security risks agent
- Performance regressions agent
- Accessibility agent
- Rule violations agent

"Each agent knows exactly what to look for and what to ignore. No single model can hold that much specialized knowledge without tradeoffs."

**Result**: 51% reduction in false positives compared to single-model approach.

### Strategy D: Confidence Scoring with Evidence Requirements

From the adversarial review approach:
- Every finding MUST include exact file:line reference + code snippet
- Findings without evidence auto-downgrade to lowest confidence
- Severity x Confidence matrix determines action:
  - S3 + C3 = CRITICAL (blocks merge)
  - S2 + C2 = MEDIUM (recommended fix)
  - S1 + C1 = INFO (logged only)

"No hallucinated bugs." If the model can't point to exact code, the finding is noise.

### Strategy E: Domain-Specific Suppression (Augment)

- Suppress lint-level clutter (formatting, naming conventions)
- Only post comments that "would likely change a merge decision"
- Use developer feedback to continuously tune: track which comments developers actually address vs ignore

---

## 4. Multi-Pass Review Architecture

### BugBot's Agentic Shift (Most Important Finding)

BugBot's biggest improvement came from switching to a **fully agentic design**:

**Before (pipeline)**: Fixed sequence of passes with static context
**After (agentic)**: Agent reasons over the diff, calls tools, decides where to dig deeper

Counterintuitive finding: **The agentic approach was too cautious, not too noisy.** They had to use aggressive prompts that "encouraged the agent to investigate every suspicious pattern and err on the side of flagging potential issues."

This is the opposite of the non-agentic approach where they restrained the model.

### Adversarial Multi-Pass Loop (Ralph Wiggum Loop)

Per iteration:
1. **Mechanical pre-pass**: Run linters to eliminate trivial issues before burning LLM tokens
2. **State review**: Load findings history and coverage tracking
3. **Angle selection**: Pick 3-5 untried attack vectors from 28 angles across 7 categories
4. **Parallel agents**: Launch reviewers with shuffled file orderings
5. **Evidence-based scoring**: Every finding needs file:line evidence
6. **Auto-fix**: CRITICAL/HIGH bugs get immediate remediation + regression tests

Attack angle taxonomy (28 angles, 7 categories):
| Category | Examples |
|----------|----------|
| Cross-feature | Adjacent mutations, shared endpoints, cascading events |
| Data integrity | Round-trip consistency, NULL/empty boundaries, type coercion |
| Client-server | Response shape mismatches, validation gaps, optimistic UI races |
| Security | Input sanitization, authorization gaps, audit completeness |
| Template/display | Render caller coverage, i18n gaps, CSS conflicts |
| Edge cases | Empty states, capacity limits, rapid/concurrent interactions |
| Ecosystem | Indexers, exports, API consumers, portals |

Loop terminates only when agent declares ALL_CLEAN after testing all 7 ODC trigger types.

---

## 5. Prompt Engineering for Bug Detection

### Core Prompt Structure

Five components that matter:

1. **Specific persona**: Not "you are a code reviewer" but "you are a senior backend engineer specializing in distributed systems with expertise in race conditions and data consistency"

2. **Rich context**: System architecture, business objectives, constraints, historical decisions, scale requirements

3. **Few-shot examples** (most powerful technique): 3-5 real examples of input diffs and the bugs found in them. Show the model what a good finding looks like.

4. **Explicit, narrow instructions**: Replace "review this code" with "identify potential race conditions in concurrent data access, focusing on shared state mutations between lines 40-80"

5. **Structured output format**: JSON with fields for file, line, severity, confidence, description, evidence, suggested fix

### Prompting Anti-Patterns

- Asking for "all issues" produces noise. Ask for specific categories one at a time.
- Generic "review this code" produces generic responses. Be surgical.
- Not providing the enclosing function/class context causes hallucinated line references.
- Asking the model to both find and fix issues in one pass reduces finding quality.

### Confidence Calibration

Ask the model to include confidence levels (high/medium/low) with every finding. Then filter:
- High confidence only = high precision, lower recall
- All confidence levels = high recall, lower precision
- Tunable threshold based on your target F1

### Chain-of-Thought for Complex Analysis

Break into explicit sequential steps:
1. "First, identify all state mutations in the changed code"
2. "Next, trace each mutation to its callers and determine if thread safety is maintained"
3. "Finally, assess whether error handling covers all failure modes"

This produces more thorough analysis than a single "find bugs" prompt.

---

## 6. Practical Implementation Recommendations

### For the WithMartian Benchmark Specifically

1. **Maximize recall first, then filter for precision.** Claude Code already gets 51% recall with only 23% precision. The model CAN find the bugs; the problem is noise. Apply post-processing to filter.

2. **Fetch codebase context aggressively.** The benchmark PRs are from real open-source repos. Clone them. For each changed file, resolve imports, find type definitions, find callers. Feed this to the model.

3. **Use multiple passes with different framings:**
   - Pass 1: Bug-focused ("find correctness bugs, race conditions, null dereferences")
   - Pass 2: Security-focused ("find injection, auth bypass, data exposure")
   - Pass 3: Architecture-focused ("find breaking changes, API contract violations, missing error handling")
   - Synthesis: Merge and deduplicate, keep findings that appear in 2+ passes

4. **Require evidence.** Every finding must include: exact file path, line number, code snippet, and explanation of WHY it's a bug. Filter anything without concrete evidence.

5. **Suppress categories that generate noise:**
   - Style/formatting suggestions
   - Documentation improvements
   - "Could be improved" suggestions without concrete bugs
   - Naming convention comments
   - Performance suggestions without measurable impact

6. **Match the judge's semantic matching.** The LLM judge checks if your comment describes the same underlying issue as the golden comment. Focus on clearly describing the root cause and impact rather than exact location matching.

### Cost-Effective Architecture

For a benchmark with 50 PRs, you can afford to be thorough:

```
Per PR Pipeline:
1. Clone repo, checkout PR base
2. Parse diff to extract changed files + symbols
3. For each changed symbol, retrieve:
   - Full function/class definition
   - Callers (grep for function name)
   - Type definitions (resolve imports)
   - Related test files
4. Run 3 themed review passes (bug/security/architecture)
5. Merge findings, deduplicate by file+line proximity
6. Filter: require evidence, remove style nits
7. Format as review comments
```

Estimated cost at ~$0.10/pass with Claude Sonnet: ~$15 for 50 PRs with 3 passes each. Affordable to iterate.

---

## Sources

- [Augment Benchmark Results](https://www.augmentcode.com/blog/we-benchmarked-7-ai-code-review-tools-on-real-world-prs-here-are-the-results)
- [Augment Code Review Architecture](https://www.augmentcode.com/blog/introducing-augment-code-review)
- [Cursor BugBot: Building a Better BugBot](https://cursor.com/blog/building-bugbot)
- [Qodo Benchmark Methodology](https://www.qodo.ai/blog/how-we-built-a-real-world-benchmark-for-ai-code-review/)
- [Qodo Precision-Recall Architecture](https://www.qodo.ai/blog/why-code-review-needs-its-own-ai-with-state-of-the-art-precision-recall/)
- [WithMartian Code Review Benchmark](https://github.com/withmartian/code-review-benchmark)
- [Adversarial Code Review Loop](https://jonroosevelt.com/blog/agentic-engineering-part-2-adversarial-code-review)
- [K-LLM Orchestration for Code Review](https://www.josecasanova.com/blog/ai-code-review-opencode)
- [CodeRabbit Context Engineering](https://www.coderabbit.ai/blog/context-engineering-ai-code-reviews)
- [Prompting LLMs for Security Reviews](https://crashoverride.com/blog/prompting-llm-security-reviews)
- [Greptile v3 Agentic Code Review](https://www.greptile.com/blog/greptile-v3-agentic-code-review)
