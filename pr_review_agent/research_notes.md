# PR Review Agent - Research Notes

## 1. Benchmark Structure

### Overview
The Code Review Benchmark (by withmartian) evaluates AI code review tools on **50 PRs** across 5 open-source repos. Each PR has human-curated **golden comments** (ground truth bugs). Tools are scored on precision (what % of tool comments matched real issues) and recall (what % of real issues the tool found).

### Repos and Languages
| Repository | Language | PRs | Golden Comments |
|---|---|---|---|
| Sentry | Python | 10 | 32 |
| Grafana | Go | 10 | 22 |
| Cal.com | TypeScript | 10 | 31 |
| Discourse | Ruby | 10 | 28 |
| Keycloak | Java | 10 | 24 |
| **Total** | | **50** | **137** |

### Golden Comment Schema
```json
{
  "comment": "Description of the bug/issue",
  "severity": "Low | Medium | High | Critical"
}
```

### Severity Distribution
- Critical: 9 (6.6%)
- High: 41 (29.9%)
- Medium: 47 (34.3%)
- Low: 40 (29.2%)

### benchmark_data.json Schema
```json
{
  "https://github.com/.../pull/123": {
    "pr_title": "...",
    "original_url": "...",
    "source_repo": "sentry",
    "golden_comments": [{"comment": "...", "severity": "High"}],
    "reviews": [
      {
        "tool": "claude",
        "repo_name": "sentry__sentry__claude__PR123__20260127",
        "pr_url": "https://github.com/code-review-benchmark/.../pull/1",
        "review_comments": [
          {"path": "file.py", "line": 42, "body": "...", "created_at": "..."}
        ]
      }
    ]
  }
}
```

## 2. Pipeline Mechanics (How Scoring Works)

### Step 2: Extract Candidates
- All review comments from a tool are concatenated and sent to an LLM
- The LLM extracts individual distinct issues as standalone text items
- Each extracted issue becomes a "candidate" stored as `{"text": "...", "path": null, "line": null, "source": "extracted"}`
- Short comments (<20 chars) are skipped

### Step 3: Judge Comments (THE CRITICAL STEP)
- **Every candidate is compared against every golden comment** (N x M matrix)
- Judge prompt asks: "Do these describe the SAME underlying issue?"
- Judge returns `{"reasoning": "...", "match": true/false, "confidence": 0.0-1.0}`
- A golden comment is "found" if ANY candidate matches it with confidence > previous best
- **Precision** = (golden comments matched by at least one candidate) / (total candidates)
- **Recall** = (golden comments matched by at least one candidate) / (total golden comments)
- Judge model: Uses temperature=0.0, accepts semantic matches (different wording OK)
- Batch size: 40 concurrent LLM calls

### Judge Prompt (exact)
```
You are evaluating AI code review tools.
Determine if the candidate issue matches the golden (expected) comment.

Golden Comment (the issue we're looking for):
{golden_comment}

Candidate Issue (from the tool's review):
{candidate}

Instructions:
- Determine if the candidate identifies the SAME underlying issue as the golden comment
- Accept semantic matches - different wording is fine if it's the same problem
- Focus on whether they point to the same bug, concern, or code issue

Respond with ONLY a JSON object:
{"reasoning": "brief explanation", "match": true/false, "confidence": 0.0-1.0}
```

### Key Insight: The Judge is Semantic
The judge explicitly accepts different wording. What matters is identifying the same underlying issue. This means:
- You don't need to quote exact code
- You don't need the same terminology as the golden comment
- You DO need to identify the same root cause/concern

## 3. Current Tool Performance (Judge: Claude Opus 4.5)

| Tool | Precision | Recall | F1 | TP | FP | FN | Median Comments |
|---|---|---|---|---|---|---|---|
| augment | 47.0% | 62.8% | 53.8% | 86 | 97 | 51 | 6 |
| copilot | 26.6% | 53.3% | 35.5% | 73 | 201 | 64 | 6 |
| bugbot | 46.2% | 43.8% | 44.9% | 60 | 70 | 4 | 4 |
| propel | 46.0% | 38.0% | 41.6% | 52 | 61 | 85 | 3 |
| greptile | 38.4% | 38.7% | 38.5% | 53 | 85 | 84 | 5 |
| qodo | 30.6% | 43.8% | 36.0% | 60 | 136 | 77 | 2 |
| gemini | 29.8% | 37.2% | 33.1% | 51 | 120 | 86 | 5 |
| claude | 33.1% | 35.8% | 34.4% | 49 | 99 | 88 | 2 |
| baz | 44.0% | 29.2% | 35.1% | 40 | 51 | 97 | 2 |
| kg | 46.9% | 16.8% | 24.7% | 23 | 26 | 114 | 1 |
| graphite | 75.0% | 8.8% | 15.7% | 12 | 4 | 125 | 0 |

### Key Observations
1. **Augment leads with 62.8% recall** - finds the most bugs, generating ~6 comments per PR
2. **Precision-recall tradeoff is real**: more comments = higher recall but lower precision
3. **The sweet spot is 3-6 comments per PR** for balanced F1
4. **Even the best tool misses 37% of issues** - there's significant room for improvement
5. **No tool exceeds 50% precision** - false positives are endemic

## 4. Bug Type Taxonomy

### From LLM Labels
| Bug Type | Count | % |
|---|---|---|
| logic_error | 25 | 18.2% |
| incorrect_value | 22 | 16.1% |
| api_misuse | 20 | 14.6% |
| race_condition | 12 | 8.8% |
| null_reference | 11 | 8.0% |
| missing_validation | 9 | 6.6% |
| type_error | 9 | 6.6% |
| security | 5 | 3.6% |
| dead_code | 4 | 2.9% |
| initialization | 3 | 2.2% |
| boundary_check | 2 | 1.5% |
| other | 15 | 10.9% |

### Catch Rate by Severity (all tools combined)
| Severity | TP | FN | Catch Rate |
|---|---|---|---|
| Critical | 65 | 43 | 60.2% |
| High | 209 | 283 | 42.5% |
| Medium | 221 | 343 | 39.2% |
| Low | 118 | 362 | 24.6% |

### Review Difficulty Distribution
- 36/50 PRs rated "subtle" (72%)
- 10/50 rated "moderate" (20%)
- 3/50 rated "very_subtle" (6%)

### Context Requirements
- 34/50 PRs require "cross_file" context (68%)
- 15/50 require "file" level context (30%)
- Only 1/50 requires just "local" context

## 5. What Makes Issues Hard to Catch

### Common False Negative Patterns (issues most tools miss)
1. **Cross-file inconsistencies**: Feature flags used differently in different files, interface contracts broken across modules
2. **Subtle logic inversions**: Conditions that are almost right but inverted in edge cases
3. **Race conditions requiring system-level reasoning**: Need to understand threading model, process model, or async execution
4. **Security issues requiring attack model thinking**: SSRF, permission bypass, cache trust asymmetry
5. **Framework-specific gotchas**: Django queryset slicing, Rails serializer conventions, React key props
6. **Behavioral regressions**: Code works but changes existing behavior in a breaking way

### What Top Tools Do Differently
From analyzing augment's true positives:
1. **Specific and concrete**: Candidates name the exact function, variable, and line involved
2. **Root cause focused**: Don't just describe symptoms, identify WHY the code is wrong
3. **Cross-file awareness**: Can connect behavior in one file to contracts in another
4. **Framework knowledge**: Know Python's hash randomization, Go's goroutine model, etc.

## 6. Strategies for Our PR Review Agent

### Strategy 1: Maximize Recall First, Then Prune
- The benchmark rewards finding issues more than avoiding false positives (Augment wins with 47% precision but 63% recall)
- Generate a comprehensive list of potential issues, then filter the weakest ones
- Target: 4-6 high-quality comments per PR

### Strategy 2: Structured Bug Hunting Checklist
Based on the taxonomy, systematically check for:
1. **Null/nil references**: Unchecked optional access, missing nil guards
2. **Logic errors**: Inverted conditions, unreachable branches, wrong comparisons
3. **Race conditions**: Concurrent access without synchronization, async/await issues
4. **API misuse**: Wrong method signatures, deprecated APIs, interface contract violations
5. **Type errors**: Wrong parameter types, unsafe casts, serialization mismatches
6. **Security**: Input validation, injection, permission checks
7. **Incorrect values**: Wrong variables, copy-paste errors, hardcoded values
8. **Missing validation**: Unchecked edge cases, boundary conditions

### Strategy 3: Multi-Pass Review
1. **Pass 1 - Local analysis**: Look at each changed file for obvious bugs
2. **Pass 2 - Cross-file analysis**: Check for interface contract violations, inconsistent feature flags, broken abstractions
3. **Pass 3 - System-level analysis**: Race conditions, security implications, behavioral regressions

### Strategy 4: Comment Format for Judge Matching
Based on successful true positive matches, the ideal candidate comment should:
- **Name the specific code element**: function, variable, class, line
- **State the problem concisely**: "X does Y, but should do Z"
- **Explain the consequence**: "This causes ..."
- **Be self-contained**: Don't require context from other comments

Example format that matches well:
> `getBouncyCastleProvider()` returns the provider of the default KeyStore type which is typically not a BouncyCastle provider, causing BouncyIntegration to fail.

### Strategy 5: Severity-Aware Prioritization
- Focus energy on High/Critical issues (50% of golden comments, hardest to catch)
- Don't over-invest in style/naming issues (Low severity, 29% of total, but lowest catch rate)
- The benchmark counts all matches equally, but catching a Critical counts the same as catching a Low

### Strategy 6: Language-Specific Bug Patterns
- **Python**: hash() non-determinism, mutable default args, Django queryset gotchas, None checks
- **Go**: Race conditions, nil panics, goroutine leaks, interface compliance, double-checked locking
- **TypeScript**: async/await in forEach, === for object comparison, Zod schema issues, Prisma gotchas
- **Ruby**: nil method calls, Rails conventions (include_?), method overriding, thread safety
- **Java**: null checks, abstract method implementation, feature flag consistency, recursive calls

## 7. Recommended Architecture

### Input
- PR diff (unified diff format)
- File contents for changed files
- Optionally: full file tree for cross-file context

### Processing Pipeline
1. Parse diff into structured changes (file, hunks, added/removed lines)
2. For each changed file, read full file content for context
3. Run multi-pass review (local -> cross-file -> system-level)
4. Generate candidate issues
5. Score and filter candidates (remove duplicates, low-confidence items)
6. Format output as review comments

### Output Format
Each comment should include:
- `path`: file path (optional but helpful)
- `line`: line number (optional but helpful)
- `body`: the review comment text
- `severity`: Low/Medium/High/Critical (for our tracking, not scored by benchmark)

### Key Design Decisions
1. **Use the full diff**: The diff is the primary input, not just changed lines
2. **Include surrounding context**: The changed lines alone miss many cross-file issues
3. **Generate 4-6 comments per PR**: Balances precision and recall based on top performers
4. **Focus on bugs, not style**: 93% of golden comments are about correctness, not style
5. **Be specific and concrete**: Name exact functions, variables, and consequences
