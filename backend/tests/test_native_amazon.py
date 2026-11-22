"""Native Amazon search executes and replays real Chromium DOM fixtures without inference."""
import html
import importlib.util
import os
from urllib.parse import parse_qs, urlparse

import pytest

from jit_ui.browser import BrowserSession
from jit_ui.config import Settings
from jit_ui.domain.models import ProviderResponse, RunMode, RunRecord, RunStatus, TaskRequest
from jit_ui.domain.sites import site_definitions
from jit_ui.exploration.native_amazon import native_search_action, supports_native_search
from jit_ui.runtime.service import RuntimeService


pytestmark = pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1",
                               reason="Enable JIT_RUN_BROWSER_TESTS=1 for routed native-search Chromium checks")


def result_html(query):
    query = html.escape(query, quote=True)
    cards = "" if not query else f'''
      <div data-component-type="s-search-result" data-asin="BMATCH"><h2><a href="/dp/BMATCH">Matching {query}</a></h2>
        <span class="a-price"><span class="a-offscreen">₹650</span></span></div>
      <div data-component-type="s-search-result" data-asin="BHIGH"><h2>Expensive sponsored item</h2>
        <span class="a-price"><span class="a-offscreen">₹1,500</span></span></div>
      <div data-component-type="s-search-result" data-asin="BUNKNOWN"><h2>Price unavailable</h2></div>'''
    return f'''<!doctype html><html><body>
      <form id="nav-search-bar-form" action="/s"><input id="twotabsearchtextbox" name="k" value="{query}" aria-label="Search Amazon.in">
        <input id="nav-search-submit-button" type="submit" value="Go" aria-label="Go"></form>
      {cards}</body></html>'''


class RoutedAmazon(BrowserSession):
    def __init__(self, settings, artifact_dir, site):
        super().__init__(settings, artifact_dir, site)
        self.addresses = []

    async def open(self):
        initial_defer = self.defer_navigation
        self.defer_navigation = True
        await super().open()

        async def respond(route):
            self.addresses.append(route.request.url)
            query = parse_qs(urlparse(route.request.url).query).get("k", [""])[0]
            await route.fulfill(content_type="text/html; charset=utf-8", body=result_html(query))

        await self._context.route("https://www.amazon.in/**", respond)
        if not initial_defer:
            await self.page.goto(self.site["start_url"])
        return self


@pytest.mark.parametrize("provider", ["gemini", "ollama"])
async def test_model_decision_cold_and_cached_export_bind_queries_and_budget(tmp_path, provider):
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact")
    sessions = []

    def browser_factory(configuration, artifact_dir, site):
        session = RoutedAmazon(configuration, artifact_dir, site)
        sessions.append(session)
        return session

    constructions = []

    class Model:
        async def decide(self, observation, task, history, instruction=""):
            return ProviderResponse(action=native_search_action(task.parameters), model="mock-vision",
                                    input_tokens=100, output_tokens=20)

    def model_factory(name, settings):
        constructions.append(name)
        return Model()

    runtime = RuntimeService(settings, session_factory=browser_factory, provider_factory=model_factory)
    task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Find men formals below 1000",
        parameters={"query": "men formals for 20 years old", "min_price": 500, "max_price": 1000,
                    "sort_by": "price-asc-rank"}, provider=provider, mode="cold", parameter_source="explicit")
    try:
        learned = await runtime.execute(RunRecord(request=task))
        assert learned.status == RunStatus.SUCCEEDED, learned.error
        assert learned.metrics.execution_strategy == "model"
        assert learned.metrics.model_calls == 1 and constructions == [provider]
        assert learned.metrics.estimated_tokens_saved == 0
        assert learned.workflow_id
        assert learned.result["query"] == task.parameters["query"]
        assert learned.result["applied_filters"] == {"min_price": 500, "max_price": 1000, "sort_by": "price-asc-rank"}
        assert len(learned.result["products"]) == 1
        assert learned.result["products"][0]["price"] == "₹650"
        assert learned.result["excluded_outside_budget"] == 2
        first_url = parse_qs(urlparse(sessions[0].addresses[-1]).query)
        assert first_url == {"k": [task.parameters["query"]], "low-price": ["500"], "high-price": ["1000"], "s": ["price-asc-rank"]}

        changed = {"query": "girls toys for 5 years", "min_price": 200, "max_price": 800, "sort_by": "price-asc-rank"}
        replay_task = task.model_copy(update={"parameters": changed, "mode": "hot", "workflow_id": learned.workflow_id})
        replay = await runtime.execute(RunRecord(request=replay_task))
        assert replay.status == RunStatus.SUCCEEDED, replay.error
        assert replay.mode == RunMode.HOT and replay.metrics.execution_strategy == "replay"
        assert replay.metrics.model_calls == replay.metrics.input_tokens == replay.metrics.output_tokens == 0
        assert constructions == [provider] and replay.metrics.estimated_tokens_saved == 120
        assert replay.result["query"] == changed["query"]
        assert replay.result["applied_filters"]["max_price"] == 800
        assert replay.result["applied_filters"]["min_price"] == 200
        final_url = parse_qs(urlparse(sessions[1].addresses[-1]).query)
        assert urlparse(sessions[1].addresses[0]).path == "/s"
        assert final_url["k"] == [changed["query"]]
        assert final_url["high-price"] == ["800"] and final_url["low-price"] == ["200"]

        artifact = runtime.repository.artifact(learned.workflow_id, 1, "python")
        specification = importlib.util.spec_from_file_location(f"native_amazon_export_{provider}", artifact)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        exported_session = RoutedAmazon(settings, tmp_path / "exported-session", site_definitions(settings)["amazon_in"])
        exported_session.defer_navigation = True
        try:
            await exported_session.open()
            result = await module.run_workflow(exported_session.page, changed)
            assert result["query"] == changed["query"]
            assert result["applied_filters"]["max_price"] == 800
            assert len(result["products"]) == 1
        finally:
            await exported_session.close()
    finally:
        runtime.repository.close()


def test_native_capability_supports_price_sort_and_defers_rating_requests():
    task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Find toys",
                       parameters={"query": "toys", "max_price": 1000})
    assert supports_native_search(task)
    assert native_search_action(task.parameters).url_refs == {"k": "query", "high-price": "max_price"}
    assert not supports_native_search(task.model_copy(update={"parameters": {"query": "toys", "min_rating": 4}}))
