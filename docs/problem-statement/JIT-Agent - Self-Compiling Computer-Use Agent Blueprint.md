# Project Blueprint: Just-In-Time (JIT) Self-Compiling Computer-Use Agent

**Working Title:** *JIT-UI / Vision-to-Code Autonomous Web Compiler*  
**Core CS Domains:** Compiler Design Concepts (JIT/De-optimization), Vision-Language Models (VLMs), Browser Internals (CDP/DOM), Distributed Automation  
**Target Engineering Roles:** Software Development Engineer (SDE), AI Systems Engineer, Applied AI Engineer, Developer Tools Engineer  

---

## 1. Executive Summary & The Recruiter Pitch

### The 30-Second Pitch (For HR & Engineering Managers)
> *"Today, companies automating browser and desktop workflows face a painful trade-off: traditional automation scripts break the moment a website changes a single button ID, while modern AI computer-use agents that 'look' at the screen are 50x slower and burn expensive API tokens on every single click. I built a **Just-In-Time (JIT) Compiler for AI Agents**. The first time it sees a workflow, a Vision AI explores the interface slowly while my engine captures the underlying browser DOM and network events, automatically compiling that visual session into a fast, zero-AI deterministic Python script. Every future run executes in milliseconds at zero API cost—and if the website layout ever changes, the runtime catches the exact failing step, wakes up the Vision AI for two seconds to self-heal, patches its own code, and resumes at full speed."*

### Why Engineering Leaders Care
* **Massive Unit-Economics Impact:** Reduces recurring AI agent inference costs by over $95\%$ on repetitive enterprise workflows (ERP data entry, invoice reconciliation, QA regression, onboarding).
* **Deep Computer Science Foundation:** Demonstrates mastery of systems engineering (Chrome DevTools Protocol, AST synthesis, state machines, caching) rather than surface-level prompt engineering.

---

## 2. Detailed Problem Statement

### 2.1 Background & Industry Context
Organizations rely heavily on web applications and legacy portals that lack clean public APIs. To automate multi-step tasks across these interfaces, the software industry currently relies on two flawed paradigms:

1. **Deterministic Automation (Playwright, Selenium, Traditional RPA):**
   * **Strengths:** Millisecond execution speed, $0$ inference cost, $100\%$ repeatable behavior.
   * **Fatal Flaw (Brittleness):** Tightly coupled to static CSS selectors, XPaths, or element IDs. A minor frontend deployment (renaming a class, moving a modal, adding a promotional banner) immediately crashes the entire pipeline, requiring manual developer maintenance.

2. **Autonomous Vision-Language Computer-Use Agents (VLM Agents):**
   * **Strengths:** Human-like adaptability. Can visually interpret unfamiliar interfaces, navigate dynamic popups, and generalize across layout changes.
   * **Fatal Flaw (Latency, Cost, & Drift):** Every single step requires taking a high-resolution screenshot, transmitting it to a multimodal LLM, waiting $3\text{--}8\text{ seconds}$ for visual reasoning, and executing one click. A 10-step workflow takes $45\text{--}90\text{ seconds}$, costs $\$0.05\text{--}\$0.30$ per execution, and suffers from non-deterministic failures even on tasks it has successfully performed 1,000 times before.

### 2.2 The Core Unsolved Problem
There is currently **no automated compilation bridge** between exploratory AI intelligence and deterministic software execution. In modern programming language runtimes (such as the V8 JavaScript Engine or JVM), code starts in a flexible **interpreted mode**, profiles hot execution paths, compiles them into **optimized native machine code**, and triggers **de-optimization** only when runtime assumptions break.

**Problem Statement:**  
*Design and implement a Just-In-Time (JIT) Self-Compiling Computer-Use Runtime that executes novel web workflows through multimodal AI exploration (Cold Path), synthesizes clean, parameterized deterministic automation code from observed DOM and network traces (Compilation Phase), executes repeat workflows at native speed with zero LLM calls (Hot Path), and performs surgical, single-step visual self-healing and code patching whenever target UI structures mutate (De-optimization Loop).*

---

## 3. Core Architecture: The Four-Phase Execution Lifecycle

```text
                    [ User Task + Parameters ]
                                |
                                v
                  +---------------------------+
                  | 1. Semantic Task Router   |
                  | (Vector Workflow Cache)   |
                  +---------------------------+
                       /                 \
          Cache Miss /                     \ Cache Hit
                    v                       v
    +----------------------------+   +----------------------------+
    | 2. COLD PATH (Interpreter) |   | 4. HOT PATH (Compiled Run) |
    | - VLM Visual Reasoning     |   | - Pure Playwright / HTTP   |
    | - CDP DOM & Network Tracer |   | - $0 Token Cost, Fast Run  |
    +----------------------------+   +----------------------------+
                    |                       |            |
            Trace Captured             All Steps      Step k Fails
                    v                    Pass       (UI Mutated)
    +----------------------------+          |            v
    | 3. JIT TRACE COMPILER      |          |   +-------------------------+
    | - Dead-End Pruning         |          |   | 5. SURGICAL SELF-HEALER |
    | - Variable Parameterization|          |   | - Capture Failure State |
    | - Multi-Tier Locator Gen   |          |   | - VLM Heals Step k Only |
    | - Python/IR Code Emission  |          |   | - Hot-Patch IR & Script |
    +----------------------------+          |   +-------------------------+
                    |                       |               |
                    +-----> [ Workflow Repository ] <-------+
                                            |
                                            v
                                  [ Task Completed ]
```

---

## 4. Deep Dive: How to Engineer Each Subsystem

### 4.1 Subsystem 1: Semantic Workflow Router (Intent Matching)
A user rarely phrases the same request identically twice (e.g., *"Search for Sony headphones under 5000 and sort by rating"* vs. *"Find Nike shoes under 3000 sorted by rating"*).
* **Workflow Template Matching:** Store compiled workflows using an abstracted natural-language intent signature (e.g., `"search_product_with_price_filter_and_sort"`) embedded in a local vector database.
* **Parameter Extraction:** When a new prompt arrives and exceeds a cosine similarity threshold (e.g., $\tau \ge 0.88$), a fast lightweight extractor (local SLM or structured regex/JSON extractor) maps the new prompt's dynamic entities (`query="Nike shoes"`, `max_price=3000`) into the compiled script's input schema.

### 4.2 Subsystem 2: Cold Path Explorer & CDP Execution Tracer
During the first run of an unseen workflow, the system runs a standard Vision + Accessibility Tree agent loop using **Playwright**.
* **Set-of-Mark (SoM) Visual Prompting:** Before capturing each screenshot, inject a lightweight JavaScript overlay via Playwright that draws numbered bounding boxes around all interactive elements (`button`, `a`, `input`, `select`, `[role="button"]`). This allows the VLM to output exact element IDs (`click(14)`) rather than guessing raw $(x, y)$ pixel coordinates.
* **Silent Telemetry Recording (Chrome DevTools Protocol):** Every time the VLM performs an action on element `id=14`, your tracer intercepts and records a rich **Action Frame**:
  * **Semantic Intent:** The VLM's reasoning for this step (e.g., *"Click the Apply Price Filter button"*).
  * **DOM Fingerprint:** Tag name, ARIA role, accessible name, `data-testid`, CSS path, relative XPath, text content, and parent container landmarks.
  * **Pre/Post State Assertions:** URL pattern changes, expected DOM elements appearing, or HTTP `200 OK` responses triggered by the click.
  * **Network Trace:** Underlying `XHR`/`fetch` requests fired immediately after the action.

### 4.3 Subsystem 3: The JIT Trace-to-Code Compiler
This is the core computer science innovation of your project. Raw agent traces are messy—an AI might click a wrong link, hit Back, dismiss a cookie banner, and finally succeed. Compiling raw traces directly produces fragile bloat. Your compiler runs a **4-stage optimization pipeline**:

1. **Stage A — Trace Pruning (Dead-End Elimination):**
   * Represent browser states (URL + DOM structural hash) as nodes in a directed graph.
   * Detect cycles and backtrack loops (e.g., $S_2 \rightarrow S_3 \rightarrow S_2$) and prune dead branches that do not contribute to reaching the final goal state.
2. **Stage B — Dynamic Input Parameterization:**
   * Compare every `type(text)` or `select(value)` action against the user's original prompt tokens.
   * Replace hardcoded literals (e.g., `"Sony headphones"`) with typed template variables (`{{search_query}}`).
3. **Stage C — Multi-Tier Resilient Locator Ranking:**
   * Never emit a single brittle CSS selector like `div.sc-8f2a > button:nth-child(3)`.
   * Score and rank candidate locators by stability weight $W$:
     * **Tier 1 (Highest Stability):** `data-testid` or semantic ARIA role + accessible name (`get_by_role("button", name="Add to Cart")`).
     * **Tier 2 (Semantic Text/Label):** Associated `<label>` or stable visible text (`get_by_label("Max Price")`).
     * **Tier 3 (Anchor-Relative XPath):** Relative path anchored to a nearby immutable heading or landmark.
     * **Tier 4 (Structural CSS):** Fallback CSS selector.
4. **Stage D — Intermediate Representation (IR) & Code Emission:**
   * Save the compiled workflow as a versioned **Workflow IR JSON** (for machine editing/patching) AND transpile it into a human-readable, standalone **Python Playwright script** using Jinja2 templates.

#### Example Workflow IR Schema (Compiled Artifact)
```json
{
  "workflow_id": "wf_ecommerce_filter_v1",
  "intent_signature": "Search product on store, apply max price filter, sort by rating",
  "parameters": {
    "product_name": "string",
    "max_price": "integer"
  },
  "steps": [
    {
      "step_id": 1,
      "action": "goto",
      "target_url": "http://localhost:3000/shop",
      "intent": "Open storefront homepage"
    },
    {
      "step_id": 2,
      "action": "fill",
      "value_ref": "{{product_name}}",
      "intent": "Enter product keyword into main search input",
      "locators": [
        { "tier": 1, "strategy": "role", "value": "searchbox[name='Search products']" },
        { "tier": 2, "strategy": "placeholder", "value": "Search catalog..." },
        { "tier": 3, "strategy": "css", "value": "header input[type='text']" }
      ],
      "post_condition": { "type": "value_equals", "expected": "{{product_name}}" }
    },
    {
      "step_id": 3,
      "action": "click",
      "intent": "Submit search query",
      "locators": [
        { "tier": 1, "strategy": "testid", "value": "search-submit-btn" },
        { "tier": 2, "strategy": "role", "value": "button[name='Search']" }
      ],
      "post_condition": { "type": "url_contains", "expected": "/search" }
    }
  ]
}
```

### 4.4 Subsystem 4: Hot Path Execution Engine
When a cached workflow runs:
* It loads the Workflow IR and injects runtime parameters.
* For each step $k$, it iterates through the ranked `locators` list with short timeouts (e.g., $800\text{ ms}$ per tier).
* If Tier 1 succeeds, it executes immediately and validates the `post_condition`.
* **Zero LLM API calls are made.** Execution completes at raw browser speed.

### 4.5 Subsystem 5: Surgical De-Optimization & Self-Healing Engine
When a website undergoes a frontend redesign and **all locator tiers fail at Step $k$** (or the `post_condition` assertion fails):

1. **Freeze Execution:** Do NOT kill the browser session or restart from Step 1. Keep the browser alive at the exact DOM state of Step $k$.
2. **Context Packaging:** Capture:
   * Current annotated Set-of-Mark screenshot.
   * Pruned Accessibility Tree of visible elements.
   * The exact semantic `intent` stored in Step $k$ of the IR (e.g., *"Submit search query"*) and the failure error.
3. **Scoped VLM Recovery:** Ask the VLM a laser-focused single-step question: *"The compiled script expected to perform `[intent]` here, but the old element disappeared. Which numbered element on the current screen fulfills this intent, or is there a blocking popup to dismiss first?"*
4. **Live Verification & Hot-Patching:**
   * Execute the VLM's proposed fix (e.g., dismiss unexpected popup or click newly styled button ID `22`).
   * Verify Step $k$'s `post_condition` passes.
   * Extract the fresh multi-tier DOM locators from element `22`, **atomically patch Step $k$ in the Workflow IR (bumping version $v_1 \rightarrow v_2$)**, regenerate the Python script, and immediately switch back to **Hot Path** for Steps $k+1 \dots n$.

### 4.6 Advanced Extension: Network-Level "Super-Compilation" (Optional Distinction Feature)
For high-scoring academic papers or standout resumes, add an optimization pass:
* During Cold Path tracing, if the tracer detects that a multi-step search or form submission triggers a clean JSON REST/GraphQL endpoint (e.g., `GET /api/products?q=Sony&maxPrice=5000`), the compiler flags the workflow as **API-Liftable**.
* The Hot Path can then replay the parameterized HTTP request directly via `httpx` (reusing browser session cookies), reducing latency from $1.5\text{ seconds}$ (headless browser) to under $150\text{ ms}$.

---

## 5. Recommended Technology Stack

| Layer | Technology Choice | Justification |
| :--- | :--- | :--- |
| **Browser Engine** | **Playwright (Python) + CDP** | Native accessibility tree inspection, fast locator engines, built-in network/DOM interception. |
| **Core Backend & Compiler** | **Python 3.11+, FastAPI, Pydantic, Jinja2** | Clean typed IR validation, fast AST/template code generation, async WebSockets. |
| **Vision-Language Model** | **Pluggable Adapter** (Gemini 2.5 Flash / Qwen2.5-VL / GPT-4o) | Keeping an abstract `BaseVLMProvider` lets you benchmark cloud models vs. local open-source VLMs. |
| **Workflow Storage** | **SQLite / PostgreSQL + ChromaDB (or Qdrant)** | Relational storage for versioned IR/metrics + vector index for semantic task routing. |
| **Observability UI** | **React + Vite + Tailwind CSS** | Live execution timeline, side-by-side Cold vs. Hot telemetry charts, and visual diff of self-healed code patches. |
| **Testbed ("Chaos Lab")** | **Custom Local Mock Web Apps (React/Flask)** | Allows reproducible UI mutations (changing IDs, layouts, popups) without getting rate-limited by live websites. |

---

## 6. Clean Project Repository Structure

```text
jit-ui-agent/
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── router/
│   │   ├── semantic_cache.py
│   │   └── param_extractor.py
│   ├── cold_path/
│   │   ├── som_injector.js
│   │   ├── vlm_provider.py
│   │   ├── explorer_agent.py
│   │   └── cdp_tracer.py
│   ├── compiler/
│   │   ├── ir_schema.py
│   │   ├── trace_pruner.py
│   │   ├── parameterizer.py
│   │   ├── locator_ranker.py
│   │   └── code_generator.py
│   ├── hot_path/
│   │   ├── executor.py
│   │   └── assertion_engine.py
│   ├── healer/
│   │   ├── surgical_healer.py
│   │   └── ir_patcher.py
│   └── storage/
│       ├── workflow_repo.py
│       └── metrics_logger.py
├── chaos_testbed/
│   ├── mock_ecommerce_app/
│   ├── mock_erp_portal/
│   └── mutation_controller.py
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── LiveBrowserView.jsx
│   │   │   ├── ExecutionTraceGraph.jsx
│   │   │   ├── CodeDiffViewer.jsx
│   │   │   └── CostLatencyCharts.jsx
│   │   └── App.jsx
└── benchmarks/
    ├── run_suite.py
    └── report_generator.py
```

---

## 7. Scientific Evaluation & Benchmarking Plan

To make your final-year thesis and technical interviews undeniable, build a **Chaos Mutation Controller** inside your local mock web apps that programmatically injects 5 classes of real-world UI breakage:
1. **Selector Drift:** Randomly hashes all CSS class names and `id` attributes.
2. **Spatial Shift:** Moves buttons from the top navbar to a sidebar or footer.
3. **Copy Mutation:** Changes button text (e.g., `"Submit"` $\rightarrow$ `"Confirm Order"`).
4. **DOM Nesting Change:** Wraps form inputs inside extra `<div>` containers or shadow DOM layers.
5. **Obstacle Injection:** Randomly spawns a modal popup or newsletter overlay mid-workflow.

### Key Performance Metrics (Formulas for Your Report)

1. **Latency Speedup Factor ($S$):**
$$S = \frac{T_{\text{cold}}}{T_{\text{hot}}}$$
*Target:* $S \ge 15\times$ to $40\times$ faster execution on compiled runs.

2. **Inference Cost Reduction ($C_{\text{save}}$):**
$$C_{\text{save}} = \left(1 - \frac{\text{Tokens}_{\text{hot}}}{\text{Tokens}_{\text{cold}}}\right) \times 100\%$$
*Target:* $100\%$ token reduction on clean hot runs; $> 85\%$ net savings across 10 runs even with 1 UI mutation event.

3. **Surgical Healing Efficiency ($H_{\text{eff}}$):**
$$H_{\text{eff}} = \frac{N_{\text{healed\_steps}}}{N_{\text{total\_steps}}}$$
*Goal:* Prove that when Step $k$ breaks in an $N$-step workflow, only $1$ step triggers VLM inference ($H_{\text{eff}} = \frac{1}{N}$), rather than falling back to $N$ steps of full vision execution.

4. **End-to-End Mutation Recovery Rate ($R_{\text{heal}}$):**
$$R_{\text{heal}} = \frac{\text{Workflows Successfully Healed \& Patched}}{\text{Total Mutated Workflow Runs}} \times 100\%$$
*Target:* $R_{\text{heal}} \ge 90\%$ across 50 controlled chaos test runs.

---

## 8. 10-Week Implementation Roadmap

| Timeline | Phase | Concrete Deliverable |
| :--- | :--- | :--- |
| **Weeks 1–2** | **Foundation & Chaos Testbed** | Build a realistic local multi-page E-Commerce / Admin Portal web app with a toggleable "UI Version 1 vs. UI Version 2 (Broken Layout)" switch. |
| **Weeks 3–4** | **Cold Path Explorer + SoM Tracer** | Implement Playwright + `som_injector.js` bounding-box overlay, connect VLM API, and record raw DOM/Action JSON logs for a 5-step task. |
| **Weeks 5–6** | **The JIT Compiler Core** | Build dead-end cycle pruning, prompt-to-action parameterization, multi-tier locator ranking, and Workflow IR $\rightarrow$ Python script generation. |
| **Week 7** | **Hot Path & Semantic Router** | Implement vector cache matching, parameter injection, and deterministic IR execution with strict post-condition checks. |
| **Week 8** | **Surgical Self-Healer** | Implement failure interception at Step $k$, scoped single-step VLM recovery, IR hot-patching ($v_1 \rightarrow v_2$), and automatic resumption. |
| **Week 9** | **Dashboard & Live Streaming** | Build the React dashboard showing live execution state (`COLD` / `HOT` / `HEALING`), code diff viewer for patched scripts, and real-time cost/latency graphs. |
| **Week 10** | **Benchmarking & Polish** | Run automated 50-trial chaos mutation benchmarks, record demo video, and compile performance tables for final report & resume. |

---

## 9. The 4-Act Live Demo Script (Guaranteed to Impress Evaluators)

Never just show slides. Run this exact 3-minute live demo during your project defense or SDE interview:

1. **Act 1 — The Cold Run (Learning via Vision):**  
   Type: *"Log into the Admin Portal, search for order #1042, and mark it as Shipped."*  
   Show the dashboard badge glowing **COLD PATH (VLM)**. The agent takes ~22 seconds, spends ~14,000 tokens, and succeeds. Instantly reveal the **newly auto-compiled Python script** generated on the right pane.
2. **Act 2 — The Hot Run (Parameterized Compiled Speed):**  
   Type a *different* input: *"Find order #2089 and mark it as Shipped."*  
   Show the badge turning green **HOT PATH (COMPILED)**. The exact workflow finishes in **0.7 seconds with $0$ tokens**.
3. **Act 3 — Live Chaos Mutation (Breaking Traditional Automation):**  
   Click your **"Deploy UI v2 (Break Frontend)"** button on the portal—live-changing the Search bar ID, renaming `"Mark Shipped"` to `"Dispatch Package"`, moving it to a dropdown menu, and adding a blocking modal.
4. **Act 4 — Surgical Self-Healing & Recompilation:**  
   Run *"Find order #3015 and mark it as Shipped."*  
   Watch Steps 1–2 fly through deterministically, Step 3 hit a locator error, the badge flash **SELF-HEALING STEP 3**, the VLM dismiss the popup and locate `"Dispatch Package"` in 2 seconds, **display a live red/green Git-style diff of the auto-patched script**, and then run a 4th order back at **0.7 seconds with $0$ tokens**.

---

## 10. Resume Bullet Points (Ready to Use)

* *Engineered a **Just-In-Time (JIT) Self-Compiling Computer-Use Agent** using **Python, Playwright (CDP), and Vision-Language Models**, synthesizing deterministic automation scripts from visual exploration traces.*
* *Implemented a 4-stage trace compiler featuring **cycle-pruning, dynamic input parameterization, and multi-tier ARIA/DOM locator ranking**, achieving a **30x latency speedup** and **98% LLM token cost reduction** on repeat runs.*
* *Designed a **surgical runtime self-healing loop** that intercepts UI-mutation failures mid-execution, performs single-step visual recovery, and atomically patches compiled Workflow IR scripts—recovering **92% of simulated frontend breaking changes** with zero manual maintenance.*