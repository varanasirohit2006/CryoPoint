# JIT Self-Compiling Computer-Use Agent

## Complete Project Explanation

> **Core idea:** Build an AI agent that learns a browser workflow once,
> compiles the observed actions into reusable automation code, executes
> future similar tasks quickly, and repairs individual steps when the
> website changes.

------------------------------------------------------------------------

## 1. Project overview

The project is called **JIT-UI / Vision-to-Code Autonomous Web
Compiler**.

It combines: - Traditional browser automation (Playwright) -
Vision-Language Models (VLMs) - Browser inspection through Chrome
DevTools Protocol (CDP) - Compiler concepts such as optimization,
intermediate representation (IR), and code generation - Workflow caching
and semantic matching - Runtime error recovery and self-healing

### The problem

There are two common approaches to browser automation.

#### Traditional automation

Tools such as Selenium and Playwright execute predefined instructions
using CSS selectors, XPath, IDs, labels, or accessible roles.

Example:

``` python
page.goto("http://localhost:3000/shop")
page.fill("#search", "Sony headphones")
page.click("#search-button")
page.fill("#max-price", "5000")
page.click("#apply-filter")
```

**Limitation:** If the website changes an element ID or moves a button,
the script may fail. A developer must update the automation manually.

#### AI-based computer-use agents

A Vision-Language Model can inspect screenshots, understand the page,
identify controls, and decide what to click or type.

**Limitation:** Repeated screenshot capture and AI reasoning can make
every action slower and more expensive. The agent may also behave
differently between runs.

### The proposed solution

The project creates a bridge between AI exploration and deterministic
automation:

1.  AI explores a new workflow.
2.  The system records browser actions and page information.
3.  A compiler converts the successful workflow into structured IR and
    Python code.
4.  Future similar tasks reuse the compiled workflow with new
    parameters.
5.  If a step breaks after a website change, AI repairs that step and
    the system updates the stored workflow.

The goal is: **learn once, execute repeatedly, and adapt when the UI
changes.**

------------------------------------------------------------------------

## 2. What does JIT mean?

**JIT** means **Just-In-Time**.

In this project, the first execution of an unfamiliar workflow is the
learning or **Cold Path**. The system then compiles the observed
workflow. Later executions use the compiled version, called the **Hot
Path**.

There are three main modes:

  -----------------------------------------------------------------------
  Mode                    What happens            AI usage
  ----------------------- ----------------------- -----------------------
  Cold Path               AI explores a new       Uses VLM
                          workflow and records    
                          actions                 

  Hot Path                Previously compiled     No VLM inference on a
                          automation runs         normal successful run

  Self-Healing            AI repairs a failed     Uses VLM for recovery
                          step after a UI change  
  -----------------------------------------------------------------------

This resembles JIT ideas in language runtimes: flexible initial
execution, optimization of reusable paths, and recovery when assumptions
stop being valid. The project applies that idea to browser workflows.

------------------------------------------------------------------------

## 3. Overall architecture

The system contains five major subsystems:

1.  Semantic Task Router
2.  Cold Path Explorer and CDP Tracer
3.  JIT Trace-to-Code Compiler
4.  Hot Path Execution Engine
5.  Surgical Self-Healing Engine

### High-level flow

``` text
                 User task + parameters
                           |
                           v
                Semantic Task Router
                           |
                Is a similar workflow stored?
                     /             \
               Cache miss         Cache hit
                  |                  |
                  v                  v
             Cold Path           Hot Path
          AI explores page     Run compiled workflow
          and records actions         |
                  |                   |
                  v                   v
             JIT Compiler        Validate each step
          Trace -> IR -> Python       |
                  |             Does a step fail?
                  v                /       \
           Workflow Store        No         Yes
                  |               |          |
                  |               v          v
                  |           Completed   Self-Healer
                  |                          |
                  |                    Repair failed step
                  |                    Patch IR and script
                  |                          |
                  +--------------------------+
                             |
                             v
                       Task completed
```

------------------------------------------------------------------------

## 4. Subsystem 1 -- Semantic Task Router

### Purpose

Identify whether the user has requested a workflow that is similar to
one already compiled and stored.

For example:

-   Request 1: "Search Sony headphones under 5000 and sort by rating."
-   Request 2: "Find Nike shoes below 3000 and sort by rating."

The product and price differ, but both requests use the same sequence:

1.  Open the shopping website.
2.  Search for a product.
3.  Apply a maximum-price filter.
4.  Sort by rating.

The router tries to match the new request to the existing workflow.

### How it works

1.  Convert the task description into a numerical representation called
    an **embedding**.
2.  Search stored workflow embeddings in a vector database.
3.  Compare similarity, for example using cosine similarity.
4.  If a suitable workflow is found, extract the new parameter values.
5.  Reuse the existing workflow with those values.

The blueprint suggests a similarity threshold such as `0.88` as an
example. This is a configurable design choice, not a universal rule.

### Example parameter extraction

User request:

``` text
Find Nike shoes below 3000 and sort by rating.
```

Extracted values:

``` json
{
  "product_name": "Nike shoes",
  "max_price": 3000
}
```

The stored workflow is reused with the new values.

### Technologies

-   Python
-   Text embeddings
-   ChromaDB or Qdrant
-   Parameter extraction using a lightweight model or structured parsing

**Important:** Similarity alone is not sufficient. The router should
also check that the requested actions and required parameters fit the
stored workflow.

------------------------------------------------------------------------

## 5. Subsystem 2 -- Cold Path Explorer and CDP Tracer

### Purpose

Explore a workflow that has not been compiled before, using AI, and
record how the task is completed.

### Step 1: Open the browser using Playwright

Playwright is a browser automation framework. It lets Python open pages,
inspect elements, click controls, type text, and observe browser
behavior.

Example:

``` python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto("http://localhost:3000/shop")
```

### Step 2: Capture a screenshot

The system captures the current webpage and sends the screenshot to a
Vision-Language Model (VLM).

A VLM can interpret both images and text. It can identify visible
controls such as:

-   Search boxes
-   Buttons
-   Price filters
-   Dropdowns
-   Product cards

### Step 3: Set-of-Mark (SoM) visual prompting

Set-of-Mark prompting means placing numbered visual markers or boxes
over interactive elements.

For example:

``` text
Element 1 – Search input
Element 2 – Search button
Element 3 – Maximum price input
Element 4 – Apply filter button
```

The VLM can refer to a numbered element instead of estimating raw screen
coordinates.

The project can inject JavaScript through Playwright to draw these
markers around buttons, links, inputs, selects, and elements with
interactive ARIA roles.

### Step 4: CDP tracer records browser activity

**CDP** means **Chrome DevTools Protocol**. It provides a way to inspect
and monitor a Chrome-based browser.

The tracer records information about each AI action. An action record
may include:

  Field              Example
  ------------------ ----------------------------
  Semantic intent    Apply price filter
  Action             Click
  DOM information    HTML tag and attributes
  ARIA role          Button
  Accessible name    Apply Filter
  Test ID            `apply-filter-btn`
  CSS path / XPath   Candidate element locators
  Before state       Filter panel open
  After state        Filter applied
  Network activity   Products API request

This recorded information is an **Action Frame**.

The important idea is that the system does not merely let AI perform the
task. It observes and records how the task was performed. Those
observations become compiler input.

------------------------------------------------------------------------

## 6. Subsystem 3 -- JIT Trace-to-Code Compiler

**This is one of the core innovations of the project.**

### Purpose

Convert the raw actions recorded during AI exploration into a clean,
reusable automation workflow.

An AI exploration can include unnecessary actions, mistakes, or
backtracking. Directly converting every recorded action into code would
create fragile and bloated scripts.

The compiler therefore uses four stages.

### Stage A -- Trace Pruning

**Purpose:** Remove unnecessary actions and dead-end paths.

The compiler represents browser states as nodes in a directed graph. A
state can be described using information such as the URL and a
structural hash of the DOM.

Example:

``` text
S1: Homepage
 |
 v
S2: Wrong menu
 |
 v
S1: Homepage (backtracked)
 |
 v
S3: Search page
 |
 v
S4: Filtered results
```

The compiler detects that the path from S1 to S2 and back to S1 did not
help reach the goal. It can prune that dead-end path.

This is conceptually similar to dead-code elimination, applied to
recorded browser actions.

### Stage B -- Dynamic Input Parameterization

**Purpose:** Replace hardcoded values with variables.

Original recorded actions:

``` python
page.fill("#search", "Sony headphones")
page.fill("#max-price", "5000")
```

Parameterized actions:

``` python
page.fill("#search", product_name)
page.fill("#max-price", str(max_price))
```

Now the same workflow can be reused for different products and prices.

### Stage C -- Multi-Tier Locator Ranking

A **locator** is a way to identify an element on a webpage.

A single CSS selector can be fragile. The compiler therefore stores
multiple candidate locators, ranked by expected stability.

  ----------------------------------------------------------------------------------------
  Tier                    Locator type            Example
  ----------------------- ----------------------- ----------------------------------------
  1                       Test ID or semantic     `get_by_role("button", name="Search")`
                          ARIA role/name          

  2                       Label or visible text   `get_by_label("Max Price")`

  3                       Anchor-relative XPath   Find a control relative to a stable
                                                  heading

  4                       Structural CSS          `header input[type="text"]`
  ----------------------------------------------------------------------------------------

The Hot Path tries the higher-priority locators first and falls back to
the next candidate if necessary.

These tiers improve resilience, but no locator strategy can guarantee
success after every possible UI change.

### Stage D -- Intermediate Representation and Code Generation

**IR** means **Intermediate Representation**.

IR is a structured, machine-readable description of the workflow. It
sits between the recorded actions and the generated Python script.

Example IR:

``` json
{
  "workflow_id": "product_search",
  "parameters": {
    "product_name": "string",
    "max_price": "integer"
  },
  "steps": [
    {
      "step_id": 1,
      "action": "goto",
      "target_url": "/shop"
    },
    {
      "step_id": 2,
      "action": "fill",
      "value_ref": "{{product_name}}"
    },
    {
      "step_id": 3,
      "action": "click",
      "intent": "Submit search"
    }
  ]
}
```

Why use IR instead of generating Python directly?

IR is easier to:

-   Validate
-   Store
-   Modify
-   Version
-   Debug
-   Convert into code again

The blueprint proposes:

-   **Pydantic** to validate IR data
-   **Jinja2** templates to generate Python Playwright scripts

The pipeline is:

``` text
Raw action traces
       |
       v
Trace optimization
       |
       v
Workflow IR (JSON)
       |
       v
Python code generation
       |
       v
Reusable Python Playwright script
```

------------------------------------------------------------------------

## 7. Subsystem 4 -- Hot Path Execution Engine

### Purpose

Execute a previously compiled workflow quickly without repeatedly
calling the VLM during normal successful runs.

Suppose the user asks:

``` text
Find Nike shoes below 3000 and sort by rating.
```

The Semantic Task Router finds the existing product-search workflow.

The system supplies:

``` python
product_name = "Nike shoes"
max_price = 3000
```

The Hot Path then:

1.  Loads the stored Workflow IR.
2.  Injects the runtime parameters.
3.  Processes the steps in order.
4.  Tries the ranked locators for each step.
5.  Performs the browser action.
6.  Checks the expected post-condition.

Example:

``` python
search_box = page.get_by_role(
    "searchbox",
    name="Search products"
)

search_box.fill(product_name)

page.get_by_role(
    "button",
    name="Search"
).click()

assert "/search" in page.url
```

### What is a post-condition?

A post-condition checks whether an action produced the expected result.

Examples:

-   After clicking Search, the URL contains `/search`.
-   After applying a filter, filtered results appear.
-   After submitting a form, a success message appears.

The Hot Path uses deterministic Playwright execution for normal
successful runs. It does not need VLM inference for every click.

------------------------------------------------------------------------

## 8. Subsystem 5 -- Surgical Self-Healing Engine

**This is another major innovation of the project.**

### Purpose

Repair only the failed step when the website UI changes, rather than
restarting and relearning the entire workflow.

### Example: Website Version 1

Original HTML:

``` html
<button id="ship-btn">
    Mark Shipped
</button>
```

The compiled script may contain:

``` python
page.get_by_role(
    "button",
    name="Mark Shipped"
).click()
```

### Website Version 2

The developer redesigns the button:

``` html
<button id="dispatch-btn">
    Dispatch Package
</button>
```

The old locator no longer works.

The Hot Path tries all stored locator tiers. If they all fail, or the
post-condition fails, the self-healing process starts.

### Step 1 -- Freeze execution

Suppose the workflow has 10 steps and Step 6 fails.

The system pauses at Step 6 and keeps the browser session alive. It does
not restart from Step 1.

### Step 2 -- Capture failure information

The system captures:

-   Current annotated screenshot
-   Current accessibility tree
-   Failed step number
-   Original semantic intent
-   Failed locator information
-   Error message

Example:

``` text
Failed step: 6
Expected action: Click Mark Shipped
Failure: Element not found
```

### Step 3 -- Ask the VLM to identify the replacement

The VLM receives the current screenshot and the original intent.

It is asked to identify the element that now performs the same
operation.

It may identify the new **Dispatch Package** button. It may also
identify a blocking popup that needs to be dismissed first.

### Step 4 -- Execute and verify the repair

The system performs the proposed action and checks the expected result.

Example:

``` text
Expected state: Order status = Shipped
Actual state:   Order status = Shipped
Result: PASS
```

A successful click alone is not enough. The system should verify the
actual resulting application state.

### Step 5 -- Patch the IR and regenerate the script

Old instruction:

``` json
{
  "step_id": 6,
  "action": "click",
  "locator": "Mark Shipped"
}
```

Updated instruction:

``` json
{
  "step_id": 6,
  "action": "click",
  "locator": "Dispatch Package"
}
```

The workflow version changes, for example, from `v1` to `v2`.

The system regenerates the Python script and resumes from Step 7.

This is called **surgical self-healing** because it repairs the failed
step instead of relearning the complete workflow.

**Implementation detail:** Save patches atomically so that an incomplete
update cannot corrupt the previously working workflow.

------------------------------------------------------------------------

## 9. Advanced Extension -- Network-Level Super-Compilation

This is an optional advanced feature.

During Cold Path tracing, the system may observe that a browser action
triggers a JSON REST or GraphQL API request.

For example:

``` http
GET /api/products?q=Sony&maxPrice=5000
```

If the endpoint is suitable, the Hot Path may replace several browser
interactions with a direct HTTP request.

Example:

``` python
import httpx

response = httpx.get(
    "http://localhost:3000/api/products",
    params={
        "q": product_name,
        "maxPrice": max_price
    }
)
```

This may reduce latency compared with controlling the browser for every
step.

However, it depends on authentication, cookies, CSRF protections,
application state, and whether the API reproduces the required
application behavior.

The blueprint calls this **API lifting**.

------------------------------------------------------------------------

## 10. Technology stack

  -----------------------------------------------------------------------
  Layer                   Technology              Purpose
  ----------------------- ----------------------- -----------------------
  Browser engine          Playwright (Python) +   Browser automation, DOM
                          CDP                     and network inspection

  Backend and compiler    Python 3.11+, FastAPI,  Backend coordination,
                          Pydantic, Jinja2        IR validation and code
                                                  generation

  Vision model            Pluggable VLM provider  Screenshot
                                                  understanding and
                                                  recovery

  Workflow storage        SQLite or PostgreSQL    Store workflows,
                                                  versions and metrics

  Vector database         ChromaDB or Qdrant      Semantic workflow
                                                  matching

  Dashboard               React + Vite + Tailwind Execution visualization
                          CSS                     and monitoring

  Testbed                 Local React/Flask mock  Reproducible UI changes
                          apps                    and testing
  -----------------------------------------------------------------------

The VLM is designed as a pluggable provider, so different supported
models can be tested without rewriting the complete system.

------------------------------------------------------------------------

## 11. Chaos Testbed -- Proving that self-healing works

The project proposes a local e-commerce or admin portal with a **Chaos
Mutation Controller**.

It deliberately introduces UI changes to test whether compiled workflows
survive.

  -----------------------------------------------------------------------
  Mutation                What changes            What the system should
                                                  attempt
  ----------------------- ----------------------- -----------------------
  Selector drift          IDs and CSS classes     Try alternative
                          change                  locators or AI repair

  Spatial shift           Button moves to another Visually locate the new
                          area                    position

  Copy mutation           "Submit" becomes        Identify the equivalent
                          "Confirm Order"         action

  DOM nesting change      Extra containers are    Use resilient locators
                          added                   or repair

  Obstacle injection      A modal popup appears   Detect and handle the
                                                  popup
  -----------------------------------------------------------------------

The objective is to run the same task on the original and mutated
website and measure whether the system completes it.

------------------------------------------------------------------------

## 12. Performance evaluation

The blueprint proposes four metrics.

### 1. Latency Speedup

Measures how much faster Hot Path is than Cold Path.

\[ S = `\frac{T_{\text{cold}}}{T_{\text{hot}}}`{=tex} \]

Example:

-   Cold Path = 20 seconds
-   Hot Path = 1 second

\[ S = `\frac{20}{1}`{=tex} = 20`\times`{=tex} \]

Proposed target: 15× to 40× speedup.

### 2. AI Inference Cost Reduction

Measures the reduction in VLM token usage.

\[ C\_{`\text{save}`{=tex}} =
`\left`{=tex}(1-`\frac{\text{Tokens}_{\text{hot}}}`{=tex}
{`\text{Tokens}`{=tex}\_{`\text{cold}`{=tex}}}`\right`{=tex})`\times100`{=tex}%
\]

If Hot Path uses zero VLM tokens, VLM token reduction is 100% for that
clean run.

This does not mean the entire system has zero operating cost.

### 3. Surgical Healing Efficiency

Measures how many workflow steps need AI recovery.

\[ H\_{`\text{eff}`{=tex}} = `\frac{N_{\text{healed steps}}}`{=tex}
{N\_{`\text{total steps}`{=tex}}} \]

Example: One step requires recovery in a 10-step workflow.

\[ H\_{`\text{eff}`{=tex}} = `\frac{1}{10}`{=tex}=0.1 \]

This demonstrates that only one step needed AI recovery.

### 4. End-to-End Mutation Recovery Rate

Measures the percentage of mutated workflows successfully repaired.

\[ R\_{`\text{heal}`{=tex}} =
`\frac{\text{Successfully healed workflows}}`{=tex}
{`\text{Total mutated workflow runs}`{=tex}}`\times100`{=tex}% \]

Example: 45 successful recoveries out of 50 tests.

\[ R\_{`\text{heal}`{=tex}} = `\frac{45}{50}`{=tex}`\times100`{=tex}=90%
\]

Proposed target: at least 90% across controlled mutation tests.

**Note:** These are proposed targets from the project blueprint. They
must be measured experimentally before being reported as achieved
results.

------------------------------------------------------------------------

## 13. Ten-week implementation roadmap

  -----------------------------------------------------------------------
  Timeline                Phase                   Deliverable
  ----------------------- ----------------------- -----------------------
  Weeks 1--2              Foundation and Chaos    Local e-commerce/admin
                          Testbed                 portal with UI Version
                                                  1 and Version 2

  Weeks 3--4              Cold Path Explorer and  Playwright, VLM
                          SoM Tracer              integration, visual
                                                  markers and action logs

  Weeks 5--6              JIT Compiler            Trace pruning,
                                                  parameterization,
                                                  locator ranking, IR and
                                                  Python generation

  Week 7                  Hot Path and Semantic   Workflow matching,
                          Router                  parameter injection and
                                                  deterministic execution

  Week 8                  Self-Healing            Failed-step recovery,
                                                  IR patching and
                                                  resumption

  Week 9                  Dashboard               Live execution state,
                                                  code diff and
                                                  performance charts

  Week 10                 Benchmarking            Controlled mutation
                                                  tests and performance
                                                  report
  -----------------------------------------------------------------------

### Recommended implementation order

Do not build every subsystem at once.

Start with a local shopping website and a simple Playwright script that
can:

1.  Search for a product.
2.  Apply a maximum-price filter.
3.  Sort by rating.

Then add tracing, AI exploration, compilation, workflow reuse, and
self-healing in stages.

------------------------------------------------------------------------

## 14. Four-act final project demonstration

The blueprint proposes a three-minute demonstration.

### Act 1 -- Cold Run

User request:

> Log into the Admin Portal, search for order #1042, and mark it as
> Shipped.

Show:

-   Cold Path status
-   AI exploring the website
-   Recorded actions
-   Generated Python script

### Act 2 -- Hot Run

User request:

> Find order #2089 and mark it as Shipped.

Show:

-   Hot Path status
-   Reuse of the same workflow
-   New order number passed as a parameter
-   Faster execution without VLM inference on a clean run

### Act 3 -- Break the website

Activate a UI mutation:

-   Change the search bar ID
-   Rename "Mark Shipped" to "Dispatch Package"
-   Move the action into a dropdown
-   Add a blocking modal

Show that the old compiled workflow encounters a failure.

### Act 4 -- Self-Healing

Show:

1.  Execution pauses at the failed step.
2.  The system captures the failure state.
3.  The VLM identifies the replacement element.
4.  The action is executed and verified.
5.  The IR and Python script are patched.
6.  Execution resumes.
7.  A later run uses the updated workflow.

The demo should prove learning, reuse, and recovery---not just display
slides.

------------------------------------------------------------------------

## 15. Interview-ready project explanation

### Project: JIT Self-Compiling Computer-Use Agent

The main objective of this project is to develop an intelligent browser
automation system that combines the flexibility of AI-based computer-use
agents with the speed of traditional automation.

Traditional automation tools like Selenium and Playwright depend on
predefined selectors. When the website UI changes, these scripts can
fail.

On the other hand, AI-based computer-use agents can understand websites
visually, but they require repeated screenshots and AI inference, which
increases execution time and cost.

To solve these problems, I designed a Just-In-Time self-compiling agent.

The system works in three major modes:

**1. Cold Path:** When a new workflow is received, a Vision-Language
Model explores the website using Playwright. A Chrome DevTools Protocol
tracer records browser actions, DOM information and network events.

**2. Compilation and Hot Path:** The recorded actions are processed by a
compiler. It removes unnecessary actions, replaces hardcoded inputs with
parameters, ranks element locators based on reliability, and generates
an Intermediate Representation in JSON. This IR is then converted into a
reusable Python Playwright script.

During subsequent requests, the system identifies similar workflows
using semantic matching and executes the compiled script without
requiring repeated AI inference.

**3. Self-Healing:** If the website changes and an automation step
fails, the system pauses at that particular step. It captures the
current screenshot and accessibility information and asks the VLM to
identify the new element corresponding to the original action. After
verifying the action, it updates the IR, regenerates the script and
resumes execution.

We evaluate the system using execution latency, AI inference cost
reduction, workflow recovery rate and self-healing efficiency.

The ultimate goal is to build browser automation that learns once,
executes repeatedly and automatically adapts to UI changes.

------------------------------------------------------------------------

## 16. Important concepts to prepare

  -----------------------------------------------------------------------
  Concept                             Meaning in this project
  ----------------------------------- -----------------------------------
  JIT Compilation                     Compile reusable workflows after
                                      initial exploration

  DOM                                 Structure of elements on a webpage

  HTML                                Defines webpage elements

  CSS Selector                        Identifies webpage elements

  XPath                               Locates elements using document
                                      structure

  ARIA                                Accessibility roles and names

  Playwright                          Browser automation framework

  CDP                                 Chrome browser inspection and
                                      communication

  VLM                                 Model that understands images and
                                      text

  Embeddings                          Numerical representation of text
                                      meaning

  Vector Database                     Stores and searches embeddings

  IR                                  Intermediate structured
                                      representation

  AST                                 Abstract Syntax Tree used in code
                                      processing

  Parameterization                    Replacing fixed values with
                                      variables

  Hot Patching                        Updating stored workflow
                                      instructions

  Assertions                          Checking expected outcomes

  API                                 Interface for communication between
                                      applications
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 17. Important engineering limitations

1.  **Not every UI change can be healed.** A changed button label may be
    easy to handle, but a changed business process may require a new
    workflow or human approval.
2.  **Zero VLM inference does not mean zero operating cost.** Browser
    execution, backend services, databases and hosting still consume
    resources.
3.  **Direct API execution is not always possible.** Authentication,
    CSRF tokens, session state and application validation may prevent
    safe API lifting.
4.  **A click is not proof of success.** The system should verify the
    actual resulting application state.
5.  **Workflow reuse must be validated.** Similar wording does not
    always mean the same task or business intent.
6.  **Self-healing should be safe.** For sensitive actions such as
    payments, deleting records, or changing shipment status, the system
    should apply appropriate authorization and confirmation checks
    before acting.

------------------------------------------------------------------------

## 18. Five-question knowledge check

Use these questions to test your understanding.

1.  **What is the main purpose of Cold Path?**
    -   Correct idea: Explore a new workflow using AI and record
        actions.
2.  **Why convert action traces into IR?**
    -   Correct idea: Store and modify workflow instructions in a
        structured format.
3.  **What happens during a normal Hot Path execution?**
    -   Correct idea: The compiled automation script executes without
        repeated VLM inference.
4.  **If Step 6 fails in a 10-step workflow, what should the self-healer
    do?**
    -   Correct idea: Repair Step 6 and resume, rather than restarting
        all 10 steps.
5.  **What is the purpose of the Semantic Task Router?**
    -   Correct idea: Find a suitable existing workflow and extract new
        parameters.

------------------------------------------------------------------------

## 19. Final takeaway

The project is built around three central ideas:

-   **Learn:** Use AI to explore a new browser workflow and record what
    happened.
-   **Compile and reuse:** Convert the successful workflow into
    structured IR and reusable Python automation.
-   **Repair:** When the UI changes, use AI to recover the failed step,
    patch the workflow, and continue.

The most important parts to understand deeply are the **Cold Path, IR
Compiler, Hot Path, and Surgical Self-Healing Engine**. These define the
core identity of the project.
