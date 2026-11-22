"""New actions follow the model's live selections, including optional popup handling."""
import json
import os

import httpx
import pytest

from jit_ui.browser import BrowserSession
from jit_ui.config import Settings
from jit_ui.domain.models import ElementFingerprint, Observation, TaskRequest
from jit_ui.providers.action_catalog import build_catalog, parse_selection
from jit_ui.providers.base import ProviderError
from jit_ui.providers.ollama import OllamaProvider
from jit_ui.runtime.service import RuntimeService


HTML = '''<!doctype html><html><head><title>Catalog</title></head><body>
<form onsubmit="event.preventDefault();const r=document.querySelector('#results');r.textContent=document.querySelector('#query').value;
r.dataset.min=document.querySelector('#min').value;r.dataset.max=document.querySelector('#max').value">
<label>Keywords<input id="query"></label><label>Minimum price<input id="min" type="number"></label>
<label>Maximum price<input id="max" type="number"></label><button>Search</button></form><div id="results"></div>
<div id="popup" style="position:fixed;inset:0;z-index:99999;background:#5558"><div style="position:relative;margin:120px auto;width:450px;background:white;padding:30px">
<span role="button" onclick="document.querySelector('#popup').remove()">✕</span><h2>Login</h2>
<label>Email or mobile<input id="phone" type="text"></label></div></div></body></html>'''


class Routed(BrowserSession):
    async def execute(self, action, parameters):
        self.executed = getattr(self, "executed", []) + [action.model_copy(deep=True)]
        return await super().execute(action, parameters)

    async def open(self):
        self.defer_navigation = True
        await super().open()
        await self._context.route("**/*", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=HTML))
        return self


@pytest.mark.parametrize("order", [("min_price", "query", "max_price"), ("query", "max_price", "min_price")])
@pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed browser tests")
async def test_runtime_follows_model_choices_and_does_not_auto_dismiss_or_order_fields(tmp_path, order):
    selections = [("wait", None, None), ("click", "✕", None)]
    labels = {"query": "Keywords", "min_price": "Minimum price", "max_price": "Maximum price"}
    selections += [("fill", labels[name], name) for name in order]
    selections += [("click", "Search", None), ("done", None, None)]
    calls, sessions, requests = [], [], []

    def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        context = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])
        if "completion_conditions" in payload["format"]["properties"]:
            reply = {"completion_conditions": [
                {"kind": "text_contains", "locator": {"strategy": "css", "value": "#results"}, "value_ref": "query"},
                {"kind": "number_at_least", "locator": {"strategy": "css", "value": "#min"}, "value_ref": "min_price"},
                {"kind": "number_at_most", "locator": {"strategy": "css", "value": "#max"}, "value_ref": "max_price"}]}
            return httpx.Response(200, json={"message": {"content": json.dumps(reply)}, "prompt_eval_count": 100, "eval_count": 20})
        op, name, reference = selections[len(calls)]
        controls = {item["target"]: item for item in context["controls"]}
        candidate = next(item for item in context["menu"] if item["op"] == op and (name is None or controls.get(item.get("target"), {}).get("name") == name) and (reference is None or item.get("parameter") == reference))
        if len(calls) < 2:
            assert any(item.get("purpose") == "dismiss" and item.get("context") == "Login" for item in context["controls"])
        reply = {"candidate_id": candidate["id"], "intent": "Model chooses " + op}
        calls.append(reply)
        return httpx.Response(200, json={"message": {"content": json.dumps(reply)}, "prompt_eval_count": 100, "eval_count": 20})

    def session_factory(settings, directory, site):
        session = Routed(settings, directory, site)
        sessions.append(session)
        return session

    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(transport)) as client:
        settings = Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact")
        runtime = RuntimeService(settings, session_factory=session_factory, provider_factory=lambda *args: OllamaProvider(client=client))
        try:
            task = TaskRequest(site_id="web", target_url="https://catalog.example/", task="Search coat minimum 500 maximum 1200",
                               parameters={"query": "coat", "min_price": 500, "max_price": 1200}, parameter_source="explicit", provider="ollama", mode="cold")
            run = await runtime.execute(await runtime.submit(task))
            assert run.status == "SUCCEEDED", run.error
            assert run.metrics.model_calls == len(requests) == len(selections) + 1 and run.metrics.execution_strategy == "model"
            assert run.metrics.input_tokens + run.metrics.output_tokens == len(requests) * 120
            workflow = runtime.repository.get_workflow(run.workflow_id)
            actions = [action for step in workflow.steps for action in step.actions]
            assert [action.action for action in sessions[0].executed[:3]] == ["goto", "wait", "click"]
            assert next(action for action in actions if action.action == "click").optional
            assert [action.value_ref for action in actions if action.action == "fill"] == list(order)
            legacy = workflow.model_copy(deep=True)
            legacy.metadata["execution_strategy"] = "native"
            assert not runtime._compatible(legacy, run.request)
            replay_task = task.model_copy(update={"mode": "auto", "task": "Search jacket minimum 600 maximum 1100",
                "parameters": {"query": "jacket", "min_price": 600, "max_price": 1100}})
            replay = await runtime.execute(await runtime.submit(replay_task))
            assert replay.status == "SUCCEEDED", replay.error
            assert replay.mode == "HOT" and replay.metrics.model_calls == 0
            assert len(calls) == len(selections)
            assert replay.metrics.estimated_tokens_saved == len(requests) * 120
        finally:
            runtime.repository.close()


def test_catalog_keeps_operation_target_pairs_and_does_not_rewrite_model_click():
    task = TaskRequest(site_id="web", task="Search coat", parameters={"query": "coat"})
    observation = Observation(url="https://example.com", elements=[ElementFingerprint(index=1, tag="input", role="textbox", name="Search")])
    menu = build_catalog(observation, task)
    click = next(item for item in menu if item["op"] == "click")
    decision, _ = parse_selection(json.dumps({"candidate_id": click["id"], "intent": "Focus the input"}), menu, task)
    assert decision.action.action == "click" and decision.action.target_index == 1
    with pytest.raises(ProviderError):
        parse_selection(json.dumps({"candidate_id": click["id"], "intent": "Type", "value_ref": "query"}), menu, task)


def test_catalog_exposes_literal_navigation_and_exact_native_options_without_an_order():
    task = TaskRequest(site_id="web", task="Search coats under 1200", goal_id="web_search", parameters={"query": "coats", "max_price": 1200.0})
    observation = Observation(url="https://example.com", elements=[
        ElementFingerprint(index=1, tag="input", role="textbox", name="Search"),
        ElementFingerprint(index=2, tag="input", role="textbox", name="Date picker", readonly=True),
        ElementFingerprint(index=3, tag="select", name="Maximum price", options=[{"value": "1000", "label": "₹1,000"}, {"value": "2000", "label": "₹2,000"}])])
    menu = build_catalog(observation, task, compact=True)
    assert any(item["op"] == "goto" for item in menu)
    assert any(item["op"] == "fill" and item.get("input") == "literal value" for item in menu)
    assert not any(item["op"] == "fill" and item.get("target") == 2 for item in menu)
    chosen = next(item for item in menu if item["op"] == "select" and item["option"] == "1000")
    decision, _ = parse_selection(json.dumps({"candidate_id": chosen["id"], "intent": "Apply a budget within the request"}), menu, task)
    assert decision.action.value == "1000" and decision.action.action == "select"
    above = next(item for item in menu if item["op"] == "select" and item["option"] == "2000")
    assert above["violates_requested_bound"]
    with pytest.raises(ProviderError):
        parse_selection(json.dumps({"candidate_id": above["id"], "intent": "Apply the requested maximum"}), menu, task)
    press = next(item for item in menu if item["op"] == "press" and item["key"] == "Enter")
    with pytest.raises(ProviderError):
        parse_selection(json.dumps({"candidate_id": press["id"], "intent": "Change key", "value": "Tab"}), menu, task)
