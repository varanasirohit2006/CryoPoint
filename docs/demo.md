# Four-act demonstration

Start the four services with `scripts/dev.ps1`, open http://127.0.0.1:5173, and select a provider. Use **Offline simulation** for an inference-free integration demonstration. Use Gemini or Ollama for a real-model demonstration and retain that distinction in recordings and reports.

1. **Learn.** Select Chaos Shop. Search for `shoes`, set a maximum price of `5000`, and sort by `rating_desc`. Force a cold run. Show the observed browser state, decisions, verified result, typed workflow, and exported Python.
2. **Reuse.** Search for `backpack` under `3500`, sorted by `price_asc`. Run automatically or select the learned workflow for hot replay. Show new results and zero model calls.
3. **Repair.** Apply the blocking overlay or copy mutation from Chaos Lab. Repeat the task. Show the failed logical step, retained completed prefix, bounded repair, and new workflow version. Reintroduce the mutation and replay with another parameter set to show deterministic reuse of the patch.
4. **Evaluate.** Run the local benchmark command in [evaluation.md](evaluation.md), then open the generated HTML/CSV report. Explain which changes were handled by locator alternatives and which required model calls. Show measured completion, durations, tokens, and failures.

For the order scenario, learn with order `1042`, then reset fixture data and hot-replay order `2089`. Apply a mutation and use order `3015` for repair. Reset fixtures while preserving the patch, then replay order `4027`. Each successful run should record exactly one shipment for the requested order.

Keep fixture reset and workflow rollback separate: fixture reset changes the mock applications; activating a version changes the runtime's cached workflow. An Amazon demonstration is optional and separate from local evaluation. A challenge or CAPTCHA is a valid blocked outcome, not a successful learned search.
