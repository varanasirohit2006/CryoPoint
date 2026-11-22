"""Temporary server failures reach the model without fake authentication handoffs."""
import json
import os

import httpx
import pytest

from jit_ui.browser import BrowserSession
from jit_ui.config import Settings
from jit_ui.domain.models import ConditionSpec, TaskRequest
from jit_ui.providers.ollama import OllamaProvider
from jit_ui.runtime.browser_helpers import temporary_page_error
from jit_ui.runtime.service import RuntimeService


pytestmark = pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed browser checks")
BUSY = "<html lang='hi'><head><title>Oops!</title></head><body><h1>Oops!</h1><p>It's rush hour and traffic is piling up on that page.</p></body></html>"
READY = "<html lang='hi'><head><title>Shopping</title></head><body><p>Shopping in Hindi</p></body></html>"


@pytest.mark.parametrize("persistent", [False, True])
async def test_temporary_error_uses_model_choices_and_has_a_recovery_limit(tmp_path, persistent):
    navigations, model_choices, sessions, requests = [], [], [], []

    class Routed(BrowserSession):
        async def open(self):
            self.defer_navigation = True
            await super().open()
            async def respond(route):
                navigations.append(route.request.url)
                busy = persistent or len(navigations) == 1
                await route.fulfill(status=503 if busy else 200, content_type="text/html", body=BUSY if busy else READY)
            await self._context.route("https://www.amazon.in/**", respond)
            return self

    def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        context = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])
        if "url" in payload["format"]["properties"]:
            return httpx.Response(200, json={"message": {"content": json.dumps({"url": context["url"]})}, "prompt_eval_count": 100, "eval_count": 20})
        assert context["page_issue"]["code"] == "TEMPORARY_SITE_ERROR"
        assert not any(item["op"] == "handoff" for item in context["menu"])
        op = "wait" if not model_choices and not persistent else "goto"
        candidate = next(item for item in context["menu"] if item["op"] == op and (op != "wait" or item["amount"] == "500"))
        if not model_choices:
            assert len(navigations) == 1, "No automatic browser retry before the model chooses"
            assert context["previous_operations"][-1]["temporary_server_error"]
        response = {"candidate_id": candidate["id"], "intent": "Model chooses recovery " + op}
        model_choices.append(op)
        return httpx.Response(200, json={"message": {"content": json.dumps(response)}, "prompt_eval_count": 100, "eval_count": 20})

    def session_factory(settings, directory, site):
        session = Routed(settings, directory, site)
        sessions.append(session)
        return session

    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(transport)) as client:
        settings = Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact")
        runtime = RuntimeService(settings, session_factory=session_factory, provider_factory=lambda *args: OllamaProvider(client=client))
        try:
            task = TaskRequest(site_id="web", target_url="https://www.amazon.in/", task="Change Amazon language to Hindi",
                provider="ollama", mode="cold", completion_conditions=[ConditionSpec(kind="language_equals", value_ref="language")])
            run = await runtime.execute(await runtime.submit(task))
            if persistent:
                assert run.status == "FAILED" and run.error_code == "TEMPORARY_SITE_ERROR", run.error
                assert len(model_choices) == 3 and not run.workflow_id
                assert "Show browser" not in run.error
            else:
                assert run.status == "SUCCEEDED", run.error
                assert model_choices == ["wait", "goto"] and len(navigations) == 2
            assert run.metrics.model_calls == len(requests)
            assert run.metrics.input_tokens + run.metrics.output_tokens == len(requests) * 120
            events = runtime.repository.list_events(run.run_id)
            assert any(event.kind == "site_recovery" for event in events)
            assert not any(event.kind == "confirmation_required" for event in events)
        finally:
            runtime.repository.close()


async def test_service_unavailable_product_text_is_not_a_page_failure(tmp_path):
    settings = Settings(_env_file=None)
    site = {"site_id": "web", "start_url": "https://www.amazon.in/", "allowed_origin": "https://www.amazon.in"}
    session = BrowserSession(settings, tmp_path, site)
    session.defer_navigation = True
    try:
        await session.open()
        await session._context.route("https://www.amazon.in/**", lambda route: route.fulfill(content_type="text/html",
            body='<html><head><title>Books</title></head><body><h1>Books</h1><label>Search<input></label><p>A book about service unavailable errors</p></body></html>'))
        await session.page.goto(site["start_url"])
        assert await temporary_page_error(session.page, site) is None
        assert (await session.observe()).page_issue is None
    finally:
        await session.close()
