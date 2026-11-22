# Reproducible evaluation

## Persistent automation token ledger

Every submitted run has an `automation_id`. Automatic sets group the site, goal, active filter types, and simulation status; a named set can be selected explicitly. All runs, including failures, retain measured input/output tokens and provider attempts in SQLite. The dashboard exposes summaries and per-run rows; `/api/automations/{id}/export?format=csv` or `json` exports the ledger.

General website sets additionally distinguish the target website and operation/abstract task. Completion checks are part of the compiled workflow, and changed explicit conditions or fixed values force a new learning run. The completed-run display includes actual usage, calls, cache/version, estimated saving and percentage when available. Manual verification and final-action approval consume elapsed run time but do not invent model calls or token usage.

For a successful replay, estimated tokens saved equal its workflow's measured learning tokens minus the replay's measured tokens. The original learning run receives zero initial saving. A comparable failed run receives no completed-task credit, so its token cost reduces cumulative savings. Savings percentage uses completed-run reference tokens as its denominator. This estimates repeated learning costs; it is not a separate controlled always-vision experiment or a claim about billing/compute savings.

The original learning run remains the reference after repairs. Runs invoking different inference models need separate baselines; a zero-inference replay references the learning model regardless of the selected provider. Missing usage metadata, retries with unknown token usage, and simulations receive no savings estimate. New tasks use LLM decisions on every cache miss. Historical `execution_strategy=native` runs remain in the ledger, have no AI learning baseline, and their workflows are excluded from new routing. Tracked counts remain visible with incomplete-usage coverage. Histories are migrated and persisted on runtime startup.

Amazon verifies that requested price/sort/rating filters were actually applied, using search URL and selected-facet evidence. Displayed cards above the budget, below the minimum, or lacking required price/rating data are excluded from returned products. Empty eligible results are reported honestly. Price correctness is independent of the model's completion claim.

Run against the four local development services. The suite resets shared fixture data, sessions, and shipments, so let interactive runs finish before starting it.

```powershell
# Small integration smoke check:
.venv\Scripts\python.exe benchmarks\run_suite.py --provider fixture --trials 1 --sites shop --mutations obstacle --skip-baselines

# 50 independent mutation cases: 5 classes x 2 apps x 5 seeds.
.venv\Scripts\python.exe benchmarks\run_suite.py --provider fixture --trials 5

# Use the same runner for measured inference, subject to account/model readiness:
.venv\Scripts\python.exe benchmarks\run_suite.py --provider ollama --trials 1
```

The default mutation classes are selector drift, spatial shift, copy changes, DOM nesting, and blocking overlays. `--mutations compound` adds the more disruptive redesign case. `--sites`, `--mutations`, and `--trials` select a smaller experiment. Cloud runs can take substantially longer because each forced exploration observes free-tier pacing. The runner never switches providers.

Each app first learns a clean workflow. Before every independent mutation case, the runner restores its original active version. Before every method, it resets the fixture and applies the selected mutation with the same seed. It compares fixed original-selector Playwright, always-vision exploration with compilation disabled, clean JIT replay, JIT replay with repair, and hot replay of the resulting patch. Post-repair replay uses new parameters and reintroduces the same mutation, including the obstacle.

Reports are written to `.data/benchmarks/` as raw JSON, row-level CSV, standalone HTML, and an SVG duration chart. They retain all failures and skipped cases. Every row identifies provider, simulation flag, seed, method, durations, actions, model calls/tokens, repairs, workflow version, and errors. Configuration and dependency/hardware information are captured without credentials.

Successful hot and post-repair runs require `HOT` mode and zero model calls. Admin runs additionally require exactly one shipment for the requested order. A failed mutation run skips post-repair replay and makes the required JIT checks fail. Baseline failures remain in the report without making expected static-selector breakage a suite failure.

Latency summaries use successful-run medians and p95; completion rates account for all rows. The static baseline includes browser startup; API client duration additionally includes submission/polling and is reported separately from runtime duration. Locator fallback counts as resilience, not AI healing. Actual healing is identified through the recorded healed-step count.

Fixture decisions are scripted. Their timings validate integration and cannot establish AI speedup, recovery accuracy, or cloud efficiency. Real-provider suites report observed outcomes and may calculate a cold/hot ratio from successful samples. There is no promised 15–40x speedup or 90% recovery result. Token prices are not invented; local inference still consumes compute even when it has no API bill.

Regenerate presentation files from recorded JSON without rerunning a model:

```powershell
.venv\Scripts\python.exe -m benchmarks.report_generator .data\benchmarks\suite-EXAMPLE.json
```
