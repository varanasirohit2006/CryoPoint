# Local setup

Use Windows PowerShell with Python 3.12 or newer and Node.js 22.12 or newer. The project keeps dependencies in `.venv/` and `node_modules/`, and browsers, databases, screenshots, logs, and evaluation reports in ignored `.data/`.

```powershell
.\scripts\setup.ps1
.\scripts\dev.ps1
```

Setup installs the locked Python and npm dependencies and Playwright Chromium. It creates `.env` only when that file does not exist. `-SkipBrowsers` reuses an existing browser installation. The default lightweight router uses `JIT_EMBEDDING_BACKEND=lexical`. Run `setup.ps1 -Semantic` to install Chroma/ONNX and provision MiniLM inside `.data/cache/embeddings`, then set `JIT_EMBEDDING_BACKEND=chroma`.

Development runs four hidden child processes. The terminal prints links after all services are ready; Ctrl+C stops them. For background use:

```powershell
.\scripts\dev.ps1 -Detach
.\scripts\stop.ps1
```

Startup allows 120 seconds by default and reports which service is still starting. The runtime readiness check does not wait on Ollama. For a slower machine, use `.\scripts\dev.ps1 -Detach -StartupTimeoutSeconds 180`. A timeout identifies the pending services and their logs under `.data/logs/`.

When the installed local Ollama server is stopped, the launcher starts it automatically for the default local URL. It reuses your installed models and stays running when project services stop. To start only Ollama, run `.\scripts\start_ollama.ps1`. Remote Ollama configurations are left to their configured server. Provider status refreshes independently from run history; **Refresh connection** forces a new provider check.

| Service | Address |
| --- | --- |
| Dashboard | http://127.0.0.1:5173 |
| Shopping testbed | http://127.0.0.1:3000/shop |
| Order testbed | http://127.0.0.1:3000/admin |
| Runtime API/OpenAPI | http://127.0.0.1:8000/docs |
| Testbed API/OpenAPI | http://127.0.0.1:8001/docs |

## Model providers

Set `JIT_GEMINI_API_KEY` in `.env` for Gemini. `JIT_GEMINI_MODEL` defaults to the previously selected `gemini-3.5-flash-lite`; change it if your Google account requires another available vision model. Calls are paced by `JIT_GEMINI_RPM=12`, with bounded retries. Account access and quota must be verified with a real run; configuration alone does not establish model readiness.

For local inference, start Ollama and download the selected model:

```powershell
ollama pull qwen3-vl:2b-instruct
ollama list
```

Keep `JIT_OLLAMA_URL=http://127.0.0.1:11434` and select Ollama in the task form. Use the exact tag shown by `ollama list`: the installed model in this workspace is `qwen3-vl:2b-instruct`. Set `JIT_OLLAMA_MODEL` if your installed tag differs. The local adapter uses a compact action format, 192 output tokens, and screenshots scaled to 960 pixels wide, then converts decisions into the same validated contracts as Gemini. `JIT_OLLAMA_MAX_OUTPUT_TOKENS` and `JIT_OLLAMA_IMAGE_MAX_WIDTH` configure those limits. CPU inference can take tens of seconds per decision; cached runs use no inference.

There is no automatic provider switch. The Offline simulation option uses scripted decisions against real local browser pages; it does not measure AI quality.

Chroma needs its local MiniLM embedding model to be provisioned before semantic runs. If downloads are unavailable, use the explicitly selected lexical router or run a known workflow from the library. Generative-model calls are counted separately from embedding work.

## Verification and troubleshooting

```powershell
.\scripts\check.ps1
# Requires the four services to be running:
.\scripts\check.ps1 -BrowserSmoke
```

Service output is in `.data/logs/*.out.log` and `.data/logs/*.err.log`. A used port stops startup before any service is launched. Run `stop.ps1` to stop project processes; it cancels active runs, waits for browser cleanup, and checks recorded process start times before stopping launchers and Python workers. A failed browser launch usually means Chromium was not installed or `PLAYWRIGHT_BROWSERS_PATH` does not match `.data/browsers`.

If the development dependency optimizer cannot spawn under a restricted shell, build with `npm.cmd run build` and launch `scripts/dev.ps1 -Preview` to serve the built frontends. Preview does not rebuild source changes automatically.

The testbed portal defaults to the local fixture credentials `demo` / `demo-password`. Credentials are environment configuration; exported workflows resolve credential references instead of storing their values.

The default **Any website** mode accepts a URL and a free task description. You can leave the URL blank when the task names a recognized website or includes an address. Examples: `Go to Amazon and change the language to Hindi`, `Go to Google and search for The Hobbit`, or a detailed form/booking task on your chosen URL. Other HTTP(S) websites do not need to be added to a fixed site registry. Compound requests remain general browser tasks rather than being reduced to a single search or language change.

Use **Automatic** to check for a compatible model-learned workflow. Every cache miss uses the selected LLM to choose each next operation from the current live page. Search, language changes, filters and optional popup dismissal have no built-in sequence. The runtime checks concrete completion evidence and compiles the successful trace. Repeating the process replays that trace with parameterized values and completion checks. Different websites, tasks, fixed values, or explicit outcome requirements cannot silently reuse an incompatible entry. Legacy native workflows are excluded.

For websites needing manual verification or login, enable **Show browser for manual steps**. Complete the step in the visible browser, then select **Resume automation** in the dashboard. The agent does not solve verification challenges. Booking tasks need complete itinerary/passenger information; final booking/payment submissions pause with a review and require **Approve final action**. Cached booking workflows request approval again for each final submission. Standalone exports stop at actions requiring that approval.

After completion, the live run view shows actual input/output tokens, model calls, cached workflow/version, and estimated tokens saved against its recorded learning run. Savings percentage is shown only when a complete measured baseline exists. Historical native runs remain labelled and do not invent learning costs. The per-set ledger and CSV/JSON downloads retain the same measurements.

Amazon observations include search, price/rating filters, sorting, department/category choices, product links, and overlay dismissal. Delivery/location, account and cart controls are excluded in the Amazon search preset. Actions can occur in any useful order, including public filtered search URLs. Temporary server/traffic errors stay in the running automation: the model can choose a wait, revisit a relevant URL or use an available retry control. Recovery is limited to three model decisions and still requires actual completion evidence. Persistent outages fail with `TEMPORARY_SITE_ERROR`; manual verification is requested only for an actual verification/login step.

For Amazon, describe your request in the task box. For example: `Go to Amazon and search for 5 years girl toys which are below 500 rupees.` The preview shows keywords `5 years girl toys` and maximum price `500`. Changing the sentence changes the request; stale keyword fields are not submitted. Use **Set keywords and filters manually** for explicit overrides. Complete commands pasted into the keyword field are also interpreted. Leave optional fields blank to omit them.

Keyword, price-range, and sort requests now use the selected model on first execution. It can choose visible fields, dropdown options, links, keys or navigation as the page requires. General shopping completion requires price evidence for each requested bound; numeric assertions verify all visible locator matches and reject missing/non-numeric values. Cached replay follows the successful model-learned trace. Replays skip the initial homepage when their first recorded action navigates directly to search.

Choose an automation set or create a named one to track a learning run and its replays. Open **Evaluation → Tokens by automation set** for measured input/output tokens, model calls, estimated savings, and CSV/JSON downloads. The automatic set groups searches by active filter types, so an unfiltered search cannot silently replace a budget workflow. Compatible workflows accept new keywords and budget values. Static age/category/brand facets or product inspections bind a workflow to its learning keywords.
