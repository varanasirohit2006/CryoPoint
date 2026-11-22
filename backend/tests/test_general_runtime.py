"""General model workflows must verify, replay, and pause before final submissions."""
import asyncio
import os
from urllib.parse import parse_qs, urlparse

import pytest

from jit_ui.browser import BrowserSession
from jit_ui.config import Settings
from jit_ui.domain.models import ActionSpec, ConditionSpec, LocatorSpec, ProviderResponse, RunMode, RunStatus, TaskRequest
from jit_ui.runtime.service import RuntimeService


pytestmark = pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1",
                               reason="Enable JIT_RUN_BROWSER_TESTS=1 for routed general-runtime Chromium checks")


CATALOG = '''<!doctype html><html lang="en"><head><title>Example catalog</title></head><body>
  <form onsubmit="event.preventDefault();document.querySelector('#results').textContent='Matching '+document.querySelector('#keywords').value">
    <label>Keywords<input id="keywords" data-testid="keywords"></label><button data-testid="filter">Apply filter</button></form>
  <div id="results" data-testid="results"></div></body></html>'''
BOOKING = '''<!doctype html><html lang="en"><head><title>Reservation review</title></head><body>
  <p>Example trip review: London, refundable fare, total ₹2,000.</p>
  <button data-testid="confirm-booking" onclick="window.bookingClicks=(window.bookingClicks||0)+1;document.querySelector('#booking-status').textContent='Booking confirmed'">Confirm booking</button>
  <p id="booking-status" data-testid="booking-status">Awaiting final submission</p></body></html>'''
LANGUAGE_PAGE = '''<!doctype html><html lang="en"><body><button data-testid="choose-hindi"
    onclick="document.documentElement.lang='hi'">Hindi</button></body></html>'''


class RoutedGeneral(BrowserSession):
    def __init__(self, settings, artifact_dir, site):
        super().__init__(settings, artifact_dir, site)
        self.booking_clicks = 0

    async def open(self):
        # Interactive runtime behavior is verified without opening a visible test window.
        self.settings = self.settings.model_copy(update={"browser_headless": True})
        self.defer_navigation = True
        await super().open()

        async def respond(route):
            parsed = urlparse(route.request.url)
            if parsed.hostname in {"amazon.in", "www.amazon.in"}:
                language = parse_qs(parsed.query).get("language", ["en_IN"])[0].replace("_", "-")
                body = f'<!doctype html><html lang="{language}"><head><title>Amazon display language</title></head><body><p>Shopping home</p></body></html>'
            elif parsed.hostname == "www.google.com":
                body = '<!doctype html><html><head><title>Manual review</title></head><body><p>Complete the manual verification step in this window.</p></body></html>'
            else:
                body = BOOKING if "/booking" in parsed.path else LANGUAGE_PAGE if "/locale" in parsed.path else CATALOG
            await route.fulfill(content_type="text/html; charset=utf-8", body=body)

        await self._context.route("**/*", respond)
        return self

    async def close(self):
        if self.page is not None:
            self.booking_clicks = await self.page.evaluate("window.bookingClicks || 0")
        await super().close()


class PageProvider:
    def __init__(self, actions, completion):
        self.actions = list(actions)
        self.completion = completion
        self.calls = 0
        self.attempts_total = 0
        self.remaining_attempts = None

    async def decide(self, observation, task, history, instruction=""):
        self.calls += 1
        self.attempts_total += 1
        if self.actions:
            operation, testid, reference = self.actions.pop(0)
            element = next(item for item in observation.elements if item.testid == testid)
            return ProviderResponse(action=ActionSpec(action=operation, target_index=element.index,
                value_ref=reference, intent=operation), model="mock-vision", input_tokens=100, output_tokens=10)
        return ProviderResponse(done=True, completion_conditions=self.completion, model="mock-vision", input_tokens=100, output_tokens=10)


class NavigationModel:
    def __init__(self, build_action, conditions):
        self.build_action, self.conditions = build_action, conditions
        self.calls, self.attempts_total = 0, 0

    async def decide(self, observation, task, history, instruction=""):
        self.calls += 1
        self.attempts_total += 1
        return ProviderResponse(action=self.build_action(task) if self.calls == 1 else None,
                                done=self.calls > 1, completion_conditions=self.conditions,
                                model="mock-vision", input_tokens=100, output_tokens=10)


def configuration(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact", run_timeout_seconds=30)


async def execute_submitted(runtime, task):
    return await runtime.execute(await runtime.submit(task))


async def test_custom_catalog_learns_concrete_goal_and_replays_changed_parameters(tmp_path):
    condition = ConditionSpec(kind="text_contains", expected="Matching shoes",
                              locator=LocatorSpec(strategy="testid", value="results"))
    provider = PageProvider([("fill", "keywords", "query"), ("click", "filter", None)], [condition])
    constructions = []

    def provider_factory(name, settings):
        constructions.append(name)
        return provider

    runtime = RuntimeService(configuration(tmp_path), session_factory=RoutedGeneral, provider_factory=provider_factory)
    task = TaskRequest(site_id="web", goal_id="browser_task", target_url="https://catalog.example.test/",
        task="Filter catalog for shoes", parameters={"query": "shoes"}, parameter_source="explicit", provider="ollama", mode="cold")
    try:
        learned = await execute_submitted(runtime, task)
        assert learned.status == RunStatus.SUCCEEDED, learned.error
        assert learned.workflow_id and learned.metrics.execution_strategy == "model"
        assert learned.metrics.model_calls == 3 and learned.metrics.input_tokens == 300 and learned.metrics.output_tokens == 30
        assert learned.metrics.estimated_tokens_saved == 0
        workflow = runtime.repository.get_workflow(learned.workflow_id)
        assert workflow.goal_conditions[0].kind == "text_contains"
        assert workflow.metadata["intent_key"] == learned.request.intent_key
        assert workflow.metadata["fixed_parameters"] == {}
        changed = task.model_copy(update={"task": "Filter catalog for backpacks", "parameters": {"query": "backpacks"}, "mode": "auto"})
        replay = await execute_submitted(runtime, changed)
        assert replay.request.intent_key == learned.request.intent_key
        assert replay.status == RunStatus.SUCCEEDED and replay.mode == RunMode.HOT, replay.error
        assert replay.metrics.model_calls == 0 and replay.metrics.execution_strategy == "replay"
        assert constructions == ["ollama"] and provider.calls == 3
        assert replay.metrics.baseline_run_id == learned.run_id and replay.metrics.estimated_tokens_saved == 330
        for updates in ({"task": "Update catalog prices for backpacks"}, {"target_url": "https://another.example.test/"}):
            from jit_ui.domain.sites import validate_task
            unrelated = validate_task(changed.model_copy(update=updates), runtime.settings)
            assert not runtime._compatible(workflow, unrelated)
    finally:
        runtime.repository.close()


async def test_literal_language_choice_on_a_custom_site_compiles_and_caches_safely(tmp_path):
    provider = PageProvider([("click", "choose-hindi", None)], [ConditionSpec(kind="language_equals", value_ref="language")])
    runtime = RuntimeService(configuration(tmp_path), session_factory=RoutedGeneral, provider_factory=lambda *args: provider)
    task = TaskRequest(site_id="web", target_url="https://catalog.example.test/locale",
                       task="Change language to Hindi", provider="gemini", mode="cold")
    try:
        learned = await execute_submitted(runtime, task)
        assert learned.status == RunStatus.SUCCEEDED, learned.error
        workflow = runtime.repository.get_workflow(learned.workflow_id)
        assert workflow.metadata["fixed_parameters"] == {"language": "hi"}
        replay = await execute_submitted(runtime, task.model_copy(update={"mode": "auto"}))
        assert replay.status == RunStatus.SUCCEEDED and replay.mode == RunMode.HOT
        assert replay.metrics.model_calls == 0
        assert not runtime._compatible(workflow, learned.request.model_copy(update={"parameters": {"language": "en"}}))
    finally:
        runtime.repository.close()


async def test_booking_is_not_reported_complete_without_approved_submission(tmp_path):
    provider = PageProvider([], [ConditionSpec(kind="visible", locator=LocatorSpec(strategy="testid", value="confirm-booking"))])
    runtime = RuntimeService(configuration(tmp_path), session_factory=RoutedGeneral, provider_factory=lambda *args: provider)
    try:
        run = await execute_submitted(runtime, TaskRequest(site_id="web", target_url="https://catalog.example.test/booking",
            task="Book a flight to London", provider="gemini", mode="cold"))
        assert run.status == RunStatus.FAILED and run.workflow_id is None
        assert run.error_code == "COMPLETION_EVIDENCE_MISSING"
    finally:
        runtime.repository.close()


async def test_model_cannot_remove_user_requested_completion_evidence(tmp_path):
    provider = PageProvider([("fill", "keywords", "query"), ("click", "filter", None)],
        [ConditionSpec(kind="text_contains", locator=LocatorSpec(strategy="testid", value="results"), value_ref="query")])
    runtime = RuntimeService(configuration(tmp_path), session_factory=RoutedGeneral, provider_factory=lambda *args: provider)
    try:
        run = await execute_submitted(runtime, TaskRequest(site_id="web", target_url="https://catalog.example.test/",
            task="Filter catalog for shoes", parameters={"query": "shoes"}, parameter_source="explicit", provider="gemini", mode="cold",
            completion_conditions=[ConditionSpec(kind="title_contains", expected="Required extra marker")]))
        assert run.status == RunStatus.FAILED and run.workflow_id is None
        assert run.error_code == "GOAL_NOT_VERIFIED"
    finally:
        runtime.repository.close()


async def test_model_language_decision_then_cached_english_without_new_inference(tmp_path):
    constructions = []

    def model_factory(name, settings):
        constructions.append(name)
        return NavigationModel(lambda task: ActionSpec(action="goto", url=task.target_url, url_refs={"language": "language"}),
                               [ConditionSpec(kind="language_equals", value_ref="language")])

    runtime = RuntimeService(configuration(tmp_path), session_factory=RoutedGeneral, provider_factory=model_factory)
    task = TaskRequest(site_id="web", goal_id="change_language", target_url="https://www.amazon.in/",
                       task="Change Amazon language to Hindi", parameters={"language": "hi"}, parameter_source="explicit", provider="gemini", mode="cold")
    try:
        hindi = await execute_submitted(runtime, task)
        assert hindi.status == RunStatus.SUCCEEDED and hindi.metrics.execution_strategy == "model", hindi.error
        assert hindi.metrics.model_calls == 2 and hindi.workflow_id and constructions == ["gemini"]
        english_task = task.model_copy(update={"task": "Change Amazon language to English", "parameters": {"language": "en"}, "provider": "ollama", "mode": "auto"})
        english = await execute_submitted(runtime, english_task)
        assert english.status == RunStatus.SUCCEEDED and english.mode == RunMode.HOT, english.error
        assert english.metrics.model_calls == 0 and english.metrics.execution_strategy == "replay"
        assert constructions == ["gemini"]
        assert "language=en_IN" in english.result["url"]
    finally:
        runtime.repository.close()


@pytest.mark.parametrize("approve", [True, False])
async def test_final_booking_button_waits_for_persisted_review_and_explicit_decision(tmp_path, approve):
    completion = [ConditionSpec(kind="text_equals", expected="Booking confirmed",
                                locator=LocatorSpec(strategy="testid", value="booking-status"))]
    provider = PageProvider([("click", "confirm-booking", None)], completion)
    sessions = []

    def browser_factory(settings, artifact_dir, site):
        session = RoutedGeneral(settings, artifact_dir, site)
        sessions.append(session)
        return session

    runtime = RuntimeService(configuration(tmp_path), session_factory=browser_factory, provider_factory=lambda *args: provider)
    task = TaskRequest(site_id="web", goal_id="browser_task", target_url="https://travel.example.test/booking",
                       task="Complete the reservation", provider="gemini", mode="cold")
    execution = None
    try:
        run = await runtime.submit(task)
        execution = asyncio.create_task(runtime.execute(run))
        async with asyncio.timeout(12):
            while runtime.repository.get_run(run.run_id).status != RunStatus.AWAITING_CONFIRMATION:
                if execution.done():
                    result = await execution
                    pytest.fail(f"Run ended before review: {result.error}")
                await asyncio.sleep(.02)
        review = runtime.repository.get_run(run.run_id)
        assert review.pending_confirmation["label"] == "Confirm booking"
        assert "₹2,000" in review.pending_confirmation["details"]
        assert await sessions[0].page.evaluate("window.bookingClicks || 0") == 0
        assert not execution.done()
        assert any(event.kind == "confirmation_required" for event in runtime.repository.list_events(run.run_id))
        await runtime.confirm(run.run_id, approve)
        # Browser/CDP/driver cleanup each have bounded waits; allow the whole cleanup.
        result = await asyncio.wait_for(execution, 30)
        assert result.status == (RunStatus.SUCCEEDED if approve else RunStatus.CANCELLED), result.error
        assert sessions[0].booking_clicks == (1 if approve else 0)
        assert result.pending_confirmation is None
        assert bool(result.workflow_id) == approve
    finally:
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        runtime.repository.close()


async def test_body_visible_completion_is_rejected_and_never_compiled(tmp_path):
    provider = PageProvider([], [ConditionSpec(kind="visible", locator=LocatorSpec(strategy="css", value="body"))])
    runtime = RuntimeService(configuration(tmp_path), session_factory=RoutedGeneral, provider_factory=lambda *args: provider)
    task = TaskRequest(site_id="web", goal_id="browser_task", target_url="https://catalog.example.test/",
                       task="Find a specific catalog record", provider="gemini", mode="cold")
    try:
        result = await execute_submitted(runtime, task)
        assert result.status == RunStatus.FAILED
        assert result.error_code == "COMPLETION_EVIDENCE_MISSING"
        assert result.workflow_id is None and runtime.repository.list_workflows() == []
        assert provider.calls == 3
    finally:
        runtime.repository.close()


@pytest.mark.parametrize("approve", [True, False])
async def test_interactive_manual_verification_waits_for_user_before_resuming(tmp_path, monkeypatch, approve):
    from jit_ui.runtime import browser_helpers
    blocked = True
    original_detection = browser_helpers.detect_blocking

    async def simulated_verification(page, site=None):
        if blocked and urlparse(page.url).hostname == "www.google.com" and urlparse(page.url).path == "/search":
            raise browser_helpers.SiteBlockedError("Fixture requires a manual verification step.")
        await original_detection(page, site)

    monkeypatch.setattr(browser_helpers, "detect_blocking", simulated_verification)
    sessions = []

    def browser_factory(settings, artifact_dir, site):
        session = RoutedGeneral(settings, artifact_dir, site)
        sessions.append(session)
        return session

    def model_factory(*args):
        return NavigationModel(lambda task: ActionSpec(action="goto", url="https://www.google.com/search", url_refs={"q": "query"}),
            [ConditionSpec(kind="url_query_equals", attribute="q", value_ref="query"),
             ConditionSpec(kind="visible", locator=LocatorSpec(strategy="css", value="#search a"))])

    runtime = RuntimeService(configuration(tmp_path), session_factory=browser_factory, provider_factory=model_factory)
    original_timeout = asyncio.timeout
    deadlines = []

    def tracked_timeout(delay):
        context = original_timeout(delay)
        if delay == runtime.settings.run_timeout_seconds:
            deadlines.append((context, context.when()))
        return context

    monkeypatch.setattr(asyncio, "timeout", tracked_timeout)
    task = TaskRequest(site_id="web", goal_id="web_search", target_url="https://www.google.com/",
                       task="Search Google for toys", parameters={"query": "toys"},
                       parameter_source="explicit", provider="gemini", mode="cold", interactive=True)
    execution = None
    try:
        run = await runtime.submit(task)
        execution = asyncio.create_task(runtime.execute(run))
        async with asyncio.timeout(12):
            while runtime.repository.get_run(run.run_id).status != RunStatus.AWAITING_CONFIRMATION:
                if execution.done():
                    result = await execution
                    pytest.fail(f"Run ended before manual verification: {result.error}")
                await asyncio.sleep(.02)
        pending = runtime.repository.get_run(run.run_id)
        assert pending.pending_confirmation["manual"] is True
        assert pending.metrics.model_calls == 1 and not execution.done()
        assert "manual verification" in pending.pending_confirmation["details"].lower()
        assert await sessions[0].page.locator("#search a").count() == 0
        deadline, original_deadline = deadlines[0]
        assert deadline.when() is None
        original_page = sessions[0].page
        await runtime.confirm(run.run_id, True)
        async with asyncio.timeout(12):
            while len([event for event in runtime.repository.list_events(run.run_id) if event.kind == "confirmation_required"]) < 2:
                if execution.done():
                    pytest.fail("Premature Resume closed the manual browser session.")
                await asyncio.sleep(.02)
        pending = runtime.repository.get_run(run.run_id)
        assert pending.status == RunStatus.AWAITING_CONFIRMATION
        assert pending.pending_confirmation["label"] == "Verification still pending"
        assert pending.metrics.model_calls == 1
        assert sessions[0].page is original_page
        assert deadline.when() is None
        # This state change represents the explicit human step; the agent has no solving action.
        if approve:
            await sessions[0].page.set_content('''<!doctype html><html lang="en"><head><title>Toys - Google Search</title></head>
                <body><div id="search"><a href="https://example.com/toys"><h3>Toy results</h3></a></div></body></html>''')
            blocked = False
        await runtime.confirm(run.run_id, approve)
        result = await asyncio.wait_for(execution, 30)
        assert result.status == (RunStatus.SUCCEEDED if approve else RunStatus.CANCELLED), result.error
        assert result.metrics.model_calls == (2 if approve else 1) and result.metrics.execution_strategy == "model"
        assert result.pending_confirmation is None
        assert deadline.when() > original_deadline
        assert run.run_id not in runtime._confirmations
    finally:
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        runtime.repository.close()
