# CryoPoint (JIT-UI)

> **Self-Compiling Computer-Use Browser Agent**
> *Cold Vision In. Indestructible Code Out.*

Learn any browser workflow through visual exploration (VLM), compile the successful action trace into reusable standalone Python code, replay it deterministically with new parameters at **0 token cost**, and surgically repair failed steps when the interface changes.

---

## 🌟 Key Features

* **4-Phase JIT Lifecycle:**
  1. **Cold Exploration:** Visual AI models (Gemini 3.5 Flash or local Ollama Qwen3-VL) navigate novel websites using Set-of-Mark (SoM) visual badges.
  2. **JIT Compilation:** Reversible read-only cycles are pruned, 4-tier resilient locators (`testid` $\to$ `role+name` $\to$ `label/text` $\to$ `css/xpath`) are generated, and dynamic parameters are extracted into standalone Playwright scripts (`workflow.py`).
  3. **Hot Replay:** Cached intent signatures replay instantly with zero LLM inference calls and 0 token cost.
  4. **Surgical Self-Healing:** Freezes execution at step $k$ upon DOM drift or modal obstacles, performs a scoped visual repair, commits a new version ($v_{n+1}$), and resumes.
* **Interactive Desktop Browser Window:** Automatic visible Chromium browser window pops up maximized on your screen, allowing you to watch live automation as it happens.
* **Human-in-the-Loop "I am not a robot" / CAPTCHA Handling:** When an anti-bot challenge or Cloudflare check is encountered, the agent pauses gracefully, notifies you with a screenshot in the dashboard, lets you solve it in the visible browser, and resumes with one click.
* **Smart Domain Typo Correction & DNS Recovery:** Automatically corrects common URL typos (`youtbe.com` $\to$ `youtube.com`, `goole.com` $\to$ `google.com`, `amzon.in` $\to$ `amazon.in`) and catches `net::ERR_NAME_NOT_RESOLVED` with friendly diagnostic recovery.
* **Permitted Public Web Navigation:** Supports unrestricted public web navigation, handling relative URLs, HTML5 detached form buttons (`form="id"`), button `formaction` overrides, and SVG links without false-positive security blocks.
* **Multi-Step & Compound Objectives:** Understands chained tasks (such as uploading files, converting documents, and downloading outputs) and executes them sequentially.

---

## Quick start

From the project directory in PowerShell, with Python 3.12+ and Node 22.12+:

```powershell
.\scripts\setup.ps1
# Configure your JIT_GEMINI_API_KEY in .env
.\scripts\dev.ps1
```

* Open the dashboard at **http://127.0.0.1:5173**.
* Local shopping and admin testbed apps: **http://127.0.0.1:3000/shop** and **http://127.0.0.1:3000/admin**.
* Runtime API documentation: **http://127.0.0.1:8000/docs**.

Press `Ctrl+C` in the terminal to stop all services, or run `.\scripts\stop.ps1`.

---

## Project Organization

- `backend/`: Typed contracts, provider adapters (Gemini, Ollama, Fixture), browser session & CDP tracer, compiler, router, runtime, and SQLite repository.
- `frontend/`: React + TypeScript dashboard (Workbench, Workflow Library with version diffs, and Token Ledger).
- `chaos_testbed/`: Seeded shopping/admin storefront with 6 injectable mutation classes (selector drift, spatial shift, copy mutations, nesting, obstacle modals, and compound).
- `benchmarks/`: Automated 50-trial matrix runner and report generator (latency charts, counterfactual token savings).
- `scripts/`: Dev lifecycle, diagnostics, and test scripts (`dev.ps1`, `stop.ps1`, `restart_runtime.ps1`, `check.ps1`, etc.).
- `docs/`: Architecture blueprints, evaluation methodology, setup guides, and verification reports.

---

## Quality & Checks

```powershell
.\scripts\check.ps1
# Run full browser tests:
$env:JIT_RUN_BROWSER_TESTS="1"; .venv\Scripts\pytest.exe backend\tests
```
