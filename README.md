# JIT-UI

Learn a browser workflow through visual exploration, compile the successful trace into reusable Python, run it with new parameters, and repair a failed logical step when the interface changes.

This local portfolio project includes a React dashboard, seeded shopping and order testbeds, persistent workflow versions, deterministic replay/export, and reproducible evaluation. Gemini and Ollama perform real visual inference; the separately selected Offline simulation provider uses scripted decisions and is labelled throughout the UI and reports.

## Quick start

From the project directory in PowerShell, with Python 3.12+ and Node 22.12+:

```powershell
.\scripts\setup.ps1
# Configure the existing .env for your selected provider.
.\scripts\dev.ps1
```

Setup preserves an existing `.env`. Set `JIT_GEMINI_API_KEY` to use the selected Gemini model, or start Ollama with `qwen3-vl:2b-instruct`. Providers are selected explicitly; the app does not switch to another model or simulation automatically. Optional `setup.ps1 -Semantic` provisions Chroma/MiniLM locally; the default router uses lexical similarity.

Open the dashboard at **http://127.0.0.1:5173**. The local shopping and admin apps are at **http://127.0.0.1:3000/shop** and **http://127.0.0.1:3000/admin**. Runtime API documentation: **http://127.0.0.1:8000/docs**.

Ctrl+C stops the four services. `dev.ps1 -Detach` runs them in the background; `stop.ps1` stops recorded project processes. `dev.ps1 -Preview` serves the frontends after `npm.cmd run build` if the development optimizer cannot run in your shell.

For a first offline check choose **Offline simulation**, run the shopping example, change its product parameters, and run again. The second run should be `HOT` with zero model calls. Use the Chaos Lab controls to mutate the local UI, then repeat and inspect the repair version.

Choose **Any website** and enter a URL plus your task. With Gemini or Ollama selected, every cache miss uses the LLM to choose each next action from the current page. There are no automatic site-specific search, language, or popup sequences. Successful model-chosen traces compile into workflows that Automatic mode can replay; the completed-run view and automation ledger show measured tokens and estimated savings. Legacy native workflows are excluded from routing.

Optional login popups are visible to the model, which can dismiss them or request a manual step. Enable the visible browser for required login/verification; final booking/payment actions require approval. The separate Amazon search preset limits actions to public shopping. Live website results are variable and kept separate from reproducible local benchmarks.

Temporary server/traffic errors are shown to the LLM for bounded recovery, including waits and revisiting relevant URLs. They are not treated as manual-verification walls. Persistent outages stop with a temporary-site error and never publish a successful workflow.

## Project organization

- `backend/`: typed contracts, provider adapters, browser tracing, compiler, router, runtime, and SQLite storage.
- `frontend/`: React dashboard.
- `chaos_testbed/`: seeded shopping/admin application and mutation API.
- `benchmarks/`: repeatable evaluation and report generation.
- `scripts/`: setup, development launch, and verification helpers.
- `docs/`: original problem statements, architecture, setup, evaluation, and demo guide.
- `.data/`: ignored databases, downloaded browser/embedding files, traces, screenshots, and reports.

Read [plan.md](plan.md) for scope, milestones, and acceptance criteria. Source documents are preserved under [docs/problem-statement](docs/problem-statement/).

Guides: [setup](docs/setup.md), [architecture](docs/architecture.md), [four-act demo](docs/demo.md), [evaluation methodology](docs/evaluation.md), and [current verification](docs/verification.md).

## Checks

```powershell
.\scripts\check.ps1
# With the four services running:
.\scripts\check.ps1 -BrowserSmoke
.venv\Scripts\python.exe benchmarks\run_suite.py --provider fixture --trials 5
```

The benchmark writes measured JSON/CSV/HTML/SVG reports under `.data/benchmarks/`, labels simulations, restores workflow baselines between cases, and verifies patched workflows with new parameters. Real inference claims require Gemini/Ollama trials; no target performance number is presented as achieved without measurements.
