───────────────────────────────────────────────╮
│ Plan to implement                                              │
│                                                                │
│ PR Code Review Agent: WarpGrep Agent Tool + Research-Driven    │
│ Improvements                                                   │
│                                                                │
│ Context                                                        │
│                                                                │
│ Beat withmartian/code-review-benchmark leaderboard (target F1  │
│ > 64%). Current best: 41.8% F1.                                │
│ Key bottleneck: precision at 31.5% (too many false positives). │
│                                                                │
│ What's Already Done (Steps 1-6)                                │
│                                                                │
│ All core WarpGrep integration is implemented and tested:       │
│                                                                │
│ - warpgrep/client.py - COMPLETE: Rewritten with official Morph │
│  SDK Python implementation. Uses morph-warp-grep-v1 model,     │
│ multi-turn XML protocol with local tool execution (grep, read, │
│  list_directory, finish). Tested: returns 38K chars of real    │
│ code from Keycloak repo. Also exposes create_warpgrep_tool()   │
│ for Anthropic SDK tool_use integration.                        │
│ - config.py - COMPLETE: Model fixed to morph-warp-grep-v1,     │
│ added warpgrep_max_turns=4, warpgrep_validate_issues=True.     │
│ - main.py - COMPLETE: Added REPO_PATH_MAP, resolves repo_path  │
│ from source_repo.                                              │
│ - pipeline/reviewer.py - COMPLETE: WarpGrep registered as      │
│ Anthropic tool via create_warpgrep_tool(). Claude can call it  │
│ on-demand during review. Agentic loop (_agentic_loop) handles  │
│ tool_use round trips. Both passes inject WarpGrep              │
│ instructions. Post-review validation kills FPs.                │
│ - pipeline/context_gatherer.py - COMPLETE: Updated with new    │
│ SDK client.                                                    │
│                                                                │
│ What Remains                                                   │
│                                                                │
│ Step 7: Run calibration with API keys set                      │
│                                                                │
│ ANTHROPIC_API_KEY="sk-ant-api03-h9m14Uqm_wVoKChS1q4f2Qv4ROliMi │
│ owHKbaP2IUp3nHgDc1M9IKvaJi0ymRkovUsI7J_sHx4Yw1szHnpIvbaw-POVND │
│ QAA" \                                                         │
│ MORPH_API_KEY="sk-DFSz0qsGMa7n4Dc5wBi424VoaXHYQ9IDVLoC8MS-mNsY │
│ 72wz" \                                                        │
│ python3 -u -m pr_review_agent.main --calibrate 2>&1            │
│                                                                │
│ Step 8: Fix thinking.type deprecation warning                  │
│                                                                │
│ Change thinking={"type": "enabled", ...} to thinking={"type":  │
│ "adaptive", ...} in _agentic_loop and _call_opus.              │
│                                                                │
│ Step 9: Apply research findings for precision boost            │
│                                                                │
│ From research agents (findings in research_findings.md):       │
│                                                                │
│ 1. Confidence penalty for single-pass issues: In               │
│ _merge_passes(), penalize unconfirmed issues by -0.1           │
│ (currently only boost confirmed +0.1).                         │
│ 2. Raise low-severity thresholds: Set medium at 0.60, low at   │
│ 0.80, keep critical/high at 0.50.                              │
│ 3. Suppress FP-prone categories entirely: Drop                 │
│ missing_validation, resource_leak, performance, documentation, │
│  style thresholds to 0.99.                                     │
│                                                                │
│ Step 10: Evaluate and iterate                                  │
│                                                                │
│ python3 -m pr_review_agent.evaluate                            │
│ # Target: F1 > 50% on calibration (improvement from 41.8%)     │
│ # Then full 50-PR run                                          │
│                                                                │
│ Files Modified                                                 │
│                                                                │
│ ┌──────────────────────────────┬─────────────────────────────┐ │
│ │             File             │           Status            │ │
│ ├──────────────────────────────┼─────────────────────────────┤ │
│ │                              │ DONE - Official SDK         │ │
│ │ warpgrep/client.py           │ implementation + Anthropic  │ │
│ │                              │ tool integration            │ │
│ ├──────────────────────────────┼─────────────────────────────┤ │
│ │ config.py                    │ DONE - Fixed model, added   │ │
│ │                              │ settings                    │ │
│ ├──────────────────────────────┼─────────────────────────────┤ │
│ │ main.py                      │ DONE - Repo path resolution │ │
│ ├──────────────────────────────┼─────────────────────────────┤ │
│ │                              │ DONE - WarpGrep as          │ │
│ │ pipeline/reviewer.py         │ Anthropic tool, agentic     │ │
│ │                              │ loop                        │ │
│ ├──────────────────────────────┼─────────────────────────────┤ │
│ │ pipeline/context_gatherer.py │ DONE - Updated SDK client   │ │
│ │                              │ usage                       │ │
│ └──────────────────────────────┴─────────────────────────────┘ │
│                                                                │
│ Verification                                                   │
│                                                                │
│ 1. Test WarpGrep standalone: search_codebase_text() returns    │
│ 38K chars ✅                                                   │
│ 2. Run calibration with both API keys set                      │
│ 3. Evaluate with python3 -m pr_review_agent.evaluate           │
│ 4. Full 50-PR run + evaluation                                 │
│ 5. Target: F1 > 64%                                            │
╰────────────────────────────────────────────────────────────────╯
