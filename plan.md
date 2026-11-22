# JIT-UI: portfolio project implementation plan

Status: **Integrated local runtime implemented; live provider validation in progress**. This document records the accepted scope and tracks implementation. Completed checkboxes require verification, not only source files.

## Problem statement

Build a browser agent that learns a novel workflow through multimodal exploration, compiles the successful trace into typed IR and deterministic Python, reuses it with different parameters, and repairs a failed logical step without replaying a completed prefix.

Original references, preserved without content changes:

- [Project blueprint](docs/problem-statement/JIT-Agent%20-%20Self-Compiling%20Computer-Use%20Agent%20Blueprint.md)
- [Complete explanation](docs/problem-statement/JIT_Self_Compiling_Agent_Project_Explanation.md)

The intended deliverable is a complete local, single-user portfolio system: Python runtime/compiler, React dashboard, two seeded mock applications, real Amazon search integration, version history, exported automation, reproducible benchmarks, and a four-act demo. Native desktop automation, production multi-tenancy, arbitrary websites, payments, and direct API lifting are later extensions.

## Architecture and organization

Use Python 3.13, FastAPI, Pydantic, SQLAlchemy/Alembic/SQLite, async Playwright/Chromium/CDP, Jinja2, and local Chroma/MiniLM embeddings. Use React/TypeScript/Vite/Tailwind for dashboard and testbed frontends. Python dependencies are managed with uv and npm dependencies with workspace lockfiles.

Gemini **gemini-3.5-flash-lite** is the primary provider, paced at 12 requests/minute for the user's 15 RPM/500 RPD free-tier quota. Optional Ollama uses **qwen3-vl:2b-instruct** with a small context. The model is downloaded by the user. No automatic switch to a paid model or simulated provider is allowed. A separately selected fixture provider is only for offline development and labelled as simulation in UI and reports.

Root contains standard manifests, README, plan, and environment examples only. Backend modules own domain/API/config/routing/browser/exploration/tracing/compiler/runtime/healing/providers/storage concerns. Testbed, dashboard, benchmarks, docs, and scripts each have dedicated folders. Generated files, databases, caches, and artifacts stay inside ignored `.data/`.

SQLite is authoritative for active workflow versions and run history. Chroma is a derived rebuildable index; MiniLM is provisioned into `.data/cache/embeddings` before use. Explicit lexical similarity is the lightweight setup default. Exact normalized intent lookup can reuse authoritative workflows without an index. Stage immutable IR and code artifacts before committing the SQLite active version. Exported Python bundles the same deterministic action/condition/goal helpers as live replay.

## Goals, contracts, and complete workflow

Supported requests carry site_id, goal_id, natural-language task, typed parameters, provider, and auto/cold/hot mode.

- **shop / search_products:** product_name, max_price, sort_by (rating_desc or price_asc).
- **admin / ship_order:** order_id; fixture credentials remain environment configuration and are excluded from trace values/code.
- **amazon_in / search_products:** query; public search only, at most five visible results in page order, optional displayed prices and sponsored labels. Empty results are valid; CAPTCHA/access/login restrictions produce BLOCKED. Browser actions enforce the search-only boundary.

Validate before launching a browser. Queue a persistent run, create an isolated context, normalize parameter values out of intent, and check compatible cached workflows by site/goal/schema/runtime plus local semantic similarity.

On cache miss: observe screenshot/accessibility/numbered elements, request a structured action, execute it, and record semantic intent, parameter provenance, stable fingerprints, before/after states, network events, and timings. Verify the registered final goal independently of the model.

Compile verified traces: prune proven reversible loops, preserve side effects, parameterize from provenance, rank multiple stable locators, validate versioned IR, emit syntax-checked Python, and publish artifacts. Never execute arbitrary model-generated Python.

On cache hit: bind parameters, resolve unique actionable targets using locator fallbacks, execute each logical step once, validate its postconditions, and verify the final result. Successful hot runs use zero generative-model calls; local embedding work is reported separately.

On failure: preserve the browser and successful prefix, detect site challenges before healing, check whether a mutation already took effect, and request a bounded visual repair. A logical step may require popup dismissal/menu opening/replacement actions. Persist transient obstacles as optional guards, publish the verified patch, and continue. Keep earlier versions for rollback. Do not retry completed shipment mutations.

Persist reconnectable ordered events with status QUEUED/RUNNING/CANCELLING/SUCCEEDED/FAILED/CANCELLED/BLOCKED/INTERRUPTED and mode ROUTING/COLD/COMPILING/HOT/HEALING. One worker serializes runs. Cancellation closes browser resources. Interrupted active runs are marked on restart.

API includes health/sites, submit/list/inspect/cancel runs, websocket events, workflow/version inspection/export/activation, artifact access, and benchmark reports. The dashboard displays task forms, provider readiness, browser observations, timelines, results/errors, compiled code, patch diffs, workflow versions, metrics, chaos controls, and evaluation.

## Implementation milestones

- [x] General website/task mode: arbitrary URLs, compound requests, observed model actions and concrete completion evidence, parameterized cache replay, completion usage/savings, and user handoff for verification/login/final booking submissions.

- [x] Flexible Amazon requests: task-text preview, price ranges, sorting and minimum stars, optional manual overrides, public shopping navigation and variable filtered replay.
- [x] Reliable Amazon keyword/price/sort capability: native public search with independent result verification, explicit zero-inference labels, compiled replay, and semantic input-focus loop prevention. Live men-formals and toys-budget requests passed.
- [x] Automation sets: persistent per-run usage, source learning references, explicit estimated token savings, failure/unknown-usage accounting, dashboard and JSON/CSV exports.

- [x] Foundation: organized docs, dependency locks, config, CI definition, verified setup/start/stop helpers. CI execution and a separate fresh-machine install remain delivery checks.
- [x] Testbed: seeded shopping/admin data, goal oracles, login/search/shipping, reset API.
- [x] Browser: observations/SoM/accessibility, shared actions, CDP/network tracing, isolated contexts.
- [x] Providers: Gemini pacing/bounded retry, Ollama vision with compact constrained actions, explicit fixtures. Gemini full flow is verified; reliable 2B CPU cold runs remain a model evaluation issue recorded in docs/verification.md.
- [x] Cold path: validated bounded decisions, independent goals, real Gemini trace collection; rejected completion receives bounded verifier feedback.
- [x] Compiler: safe pruning, typed parameters, ranked locators, IR/Python export parity.
- [x] Storage/router: checksum-verified immutable versions, persistent history/events, local semantic indexing.
- [x] Hot runtime/healing: deterministic replay, optional guards, scoped verified Gemini repair and zero-inference patch resumption.
- [x] Dashboard: live observations/events, workflow/code library, repair diff, metrics and chaos UI.
- [x] Amazon: live Gemini shoes search followed by backpacks hot replay with Ollama selected and zero model calls; blocked cases remain explicit. Ollama cold exploration accuracy remains a separate model check.
- [ ] Evaluation: baselines, seeded chaos trials, raw JSON/CSV and measured charts.
- [ ] Delivery: architecture/setup/demo/evaluation docs and fresh-install verification.

Each phase is built and tested incrementally. The original ten-week roadmap is indicative rather than a deadline.

## Tests and acceptance

Meaningful tests cover parameter validation, incompatible routing, malformed model output, stale/ambiguous elements, safe pruning, quoting/parameter data handling, helper/export parity, locator fallback, goal verification, cancellation/reconnection, interrupted artifacts, and healing without duplicate side effects.

Five seeded mutation classes: selector drift, spatial shift, copy changes, nesting changes, and blocking overlays. Separate locator-resilience cases from genuine healing cases that invalidate all old target locators. Keep goal oracles stable. The full suite runs at least 50 independent trials (5 classes × 2 apps × 5 seeds), restores original fixtures/workflow version between trials, and checks a new-parameter hot run after each repair.

Compare static-selector automation, always-vision execution, and JIT cold/hot/healing. Record provider/model, simulation flag, dependency/hardware configuration, seed, latency distributions, calls/tokens/cost, wrong actions, failures, and recovery. Live Amazon is opt-in and separate from local chaos benchmarks. Targets of 15–40× speedup and ≥90% recovery must be measured rather than promised. Local-model cost reports indicate no billed API tokens; compute still has resource cost.

Defaults: one active run, 600-second run timeout to allow CPU-local/free-tier paced exploration, 20 provider decisions, 30 cold actions, 3 repair decisions/6 repair actions per incident, and at most 2 repaired logical steps. Provider retries count toward limits. All settings are configurable. No provider credential is included in source control.

Completion requires a fresh installation to learn with a real provider, compile inspectable artifacts, hot-replay new inputs without inference, repair a controlled mutation, reuse the patch, display the lifecycle, and export reproducible evidence. Offline integration checks alone do not establish AI functionality.
