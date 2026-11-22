"""Completion survives result navigation and verifies actual playback, including replay/export."""
import importlib.util
import json
import os

import pytest
from playwright.async_api import async_playwright

from jit_ui.browser import BrowserSession
from jit_ui.compiler import generate_python
from jit_ui.config import Settings
from jit_ui.domain.models import ActionSpec, ConditionSpec, Observation, ProviderResponse, TaskRequest
from jit_ui.exploration.completion import accept_completion_conditions
from jit_ui.providers.action_catalog import build_catalog, selection_schema
from jit_ui.providers.staged_selection import argument_schema
from jit_ui.providers.task_interpretation import TaskInterpretation
from jit_ui.runtime.browser_helpers import check_conditions, read_media_state, record_search_evidence
from jit_ui.runtime.service import RuntimeService


def playback_task():
    return TaskRequest(site_id="web", target_url="https://video.example/", task="Search for games trailer and play the first video",
        parameters={"query":"games trailer"}, objectives=["Search for games trailer", "Play the first video"], provider="gemini", mode="cold")


def test_both_provider_schemas_require_explicit_completion_values():
    task = playback_task()
    catalog = build_catalog(Observation(url=task.target_url), task)
    gemini = selection_schema(task, catalog, strict_completion=True)
    done = next(candidate for candidate in catalog if candidate["op"] == "done")
    ollama = argument_schema(done, task, catalog)
    for fields in (gemini["properties"], ollama["properties"]):
        condition = fields["completion_conditions"]["items"]
        assert "expected" in condition["required"]
        assert "objective_index" in condition["required"]
        assert "anyOf" not in condition


def test_missing_comparison_reports_the_exact_condition_and_a_correction():
    conditions = [ConditionSpec(kind="url_query_equals", objective_index=0, attribute="search_query"),
        ConditionSpec(kind="media_playing", objective_index=1)]
    with pytest.raises(ValueError, match=r"condition 0 \(url_query_equals\).*expected or value_ref"):
        accept_completion_conditions(playback_task(), {}, conditions)


def test_playback_cannot_be_proved_by_a_video_title():
    conditions = [ConditionSpec(kind="search_submitted", value_ref="query", objective_index=0),
        ConditionSpec(kind="title_contains", expected="Games trailer", objective_index=1)]
    with pytest.raises(ValueError, match="Use media_playing"):
        accept_completion_conditions(playback_task(), {}, conditions)


async def test_recorded_search_survives_navigation_without_accepting_wrong_keywords():
    class Page:
        url = "https://video.example/results?search_query=games+trailer&token=do-not-record"
    page, site = Page(), {}
    parameters = {"query":"games trailer"}
    record_search_evidence(page, parameters, site)
    assert "token" not in json.dumps(site)
    page.url = "https://video.example/watch?v=first"
    condition = {"kind":"search_submitted", "value_ref":"query"}
    assert await check_conditions(page, [condition], parameters, site)
    assert not await check_conditions(page, [condition], {"query":"wrong trailer"}, site)
    assert not await check_conditions(page, [condition], parameters, {})
    assert not await check_conditions(page, [{"kind":"url_query_equals", "attribute":"search_query", "value_ref":"query"}], parameters, site)


HTML = '''<!doctype html><html><head><title>First trailer</title></head><body>
<input id="query" type="search" aria-label="Search"><button id="search" onclick="history.pushState({},'', '/results?search_query='+encodeURIComponent(document.querySelector('#query').value));document.querySelector('#results').hidden=false">Search</button>
<div id="results" hidden><a id="first" href="/watch?v=first" onclick="event.preventDefault();history.pushState({},'',this.href);startVideo()">First trailer</a></div>
<canvas id="stream" width="64" height="64" hidden></canvas><video id="video" width="160" height="120" muted style="display:none"></video>
<script>
const canvas=document.querySelector('#stream'),video=document.querySelector('#video');
video.srcObject=canvas.captureStream(20);video.muted=true;
setInterval(()=>{const ctx=canvas.getContext('2d');ctx.fillStyle=(Date.now()%200<100?'red':'blue');ctx.fillRect(0,0,64,64)},50);
function startVideo(){video.style.display='block';return video.play()}
</script></body></html>'''


@pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed browser tests")
async def test_native_media_evidence_rejects_paused_and_hidden_video():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(HTML)
            site = {"condition_timeout_ms":100}
            assert not await check_conditions(page, [{"kind":"media_playing"}], {}, site)
            await page.evaluate("startVideo()")
            await page.wait_for_function("document.querySelector('#video').currentTime>0 && document.querySelector('#video').readyState>=3")
            assert await check_conditions(page, [{"kind":"media_playing"}], {}, site)
            assert (await read_media_state(page))[0]["paused"] is False
            await page.locator("#video").evaluate("video=>video.pause()")
            assert not await check_conditions(page, [{"kind":"media_playing"}], {}, site)
            await page.evaluate("startVideo()")
            await page.locator("#video").evaluate("video=>video.style.display='none'")
            assert not await check_conditions(page, [{"kind":"media_playing"}], {}, site)
        finally:
            await browser.close()


@pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed browser tests")
async def test_playback_verification_waits_for_async_start_without_repeating_a_click():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(HTML)
            await page.evaluate("() => {window.playStarts=0;setTimeout(()=>{window.playStarts++;startVideo()},150)}")
            assert await check_conditions(page, [{"kind":"media_playing"}], {}, {"condition_timeout_ms":1500})
            assert await page.evaluate("window.playStarts") == 1
        finally:
            await browser.close()


class Routed(BrowserSession):
    async def open(self):
        self.defer_navigation = True
        await super().open()
        await self._context.route("**/*", lambda route:route.fulfill(content_type="text/html", body=HTML))
        return self


@pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed browser tests")
async def test_corrected_evidence_completes_without_repeating_actions_and_exports_replay(tmp_path):
    class Provider:
        model = "mock-vision"
        attempts_total = 0
        remaining_attempts = None
        choices = 0

        async def interpret_task(self, task):
            self.attempts_total += 1
            return TaskInterpretation(search_query="games trailer", objectives=playback_task().objectives), ProviderResponse(model=self.model, input_tokens=100, output_tokens=20)

        async def decide(self, observation, task, history, instruction=""):
            self.attempts_total += 1
            self.choices += 1
            operations = [("fill","#query","query"),("click","#search",None),("click","#first",None)]
            if self.choices <= 3:
                operation, css, reference = operations[self.choices-1]
                target = next(element for element in observation.elements if element.css == css)
                return ProviderResponse(model=self.model, input_tokens=100, output_tokens=20,
                    action=ActionSpec(action=operation, target_index=target.index, value_ref=reference))
            if self.choices == 4:
                return ProviderResponse(done=True, model=self.model, input_tokens=100, output_tokens=20, completion_conditions=[
                    ConditionSpec(kind="url_query_equals", attribute="search_query", objective_index=0),
                    ConditionSpec(kind="media_playing", objective_index=1)])
            assert "condition 0 (url_query_equals)" in instruction
            assert observation.media and observation.submitted_searches
            return ProviderResponse(done=True, model=self.model, input_tokens=100, output_tokens=20, completion_conditions=[
                ConditionSpec(kind="search_submitted", value_ref="query", expected="games trailer", objective_index=0),
                ConditionSpec(kind="media_playing", expected="playing", objective_index=1)])

    provider, constructions = Provider(), []
    def factory(*args):
        constructions.append(True)
        return provider
    runtime = RuntimeService(Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact"), session_factory=Routed, provider_factory=factory)
    original = playback_task().model_copy(update={"objectives":[]})
    try:
        learned = await runtime.execute(await runtime.submit(original))
        assert learned.status == "SUCCEEDED", learned.error
        assert learned.metrics.actions == 4 and learned.metrics.model_calls == 6
        assert all(condition["passed"] for condition in learned.result["conditions"])
        replay = await runtime.execute(await runtime.submit(original.model_copy(update={"mode":"auto"})))
        assert replay.status == "SUCCEEDED" and replay.mode == "HOT", replay.error
        assert replay.metrics.model_calls == 0 and replay.metrics.estimated_tokens_saved == 720
        assert len(constructions) == 1
        workflow = runtime.repository.get_workflow(learned.workflow_id)
        legacy = workflow.model_copy(deep=True)
        legacy.goal_conditions[1] = ConditionSpec(kind="title_contains", expected="First trailer", objective_index=1)
        assert not runtime._compatible(legacy, learned.request)
        exported = tmp_path / "playback_export.py"
        exported.write_text(generate_python(workflow, {"site_id":"web", "start_url":original.target_url}), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("playback_export", exported)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.route("**/*", lambda route:route.fulfill(content_type="text/html", body=HTML))
                result = await module.run_workflow(page, {"query":"different trailer"})
                assert all(condition["passed"] for condition in result["conditions"])
            finally:
                await browser.close()
    finally:
        runtime.repository.close()
