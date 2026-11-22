"""Lifecycle tests protect cancellation, routing, and immutable publication."""

import asyncio
import json

import pytest

from jit_ui.config import Settings
from jit_ui.domain.models import ActionFrame, ActionSpec, LocatorSpec, Observation, ParameterSpec, RunRecord, RunStatus, TaskRequest, WorkflowIR, WorkflowStep
from jit_ui.compiler import generate_python
from jit_ui.domain.sites import site_definitions
from jit_ui.runtime.service import RuntimeService
from jit_ui.storage.repository import Repository, VersionConflict


def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact")


def task(**updates):
    return TaskRequest(site_id="shop", goal_id="search_products", task="Search shoes under 5000 sorted by rating",
                       parameters={"product_name": "shoes", "max_price": 5000, "sort_by": "rating_desc"},
                       provider="fixture", **updates)


class Browser:
    def __init__(self, settings, artifact_dir, site):
        self.site = site
        self.state = {}
        self.artifact_dir = artifact_dir
        self.closed = False

    async def open(self):
        return self

    async def close(self):
        self.closed = True

    async def execute(self, action, parameters):
        if action.value_ref:
            self.state[action.value_ref] = parameters[action.value_ref]
        return ActionFrame(action=action)

    async def verify_goal(self, goal, parameters):
        return self.state == parameters, dict(self.state)

    async def check_conditions(self, conditions, parameters):
        return True

    async def observe(self):
        return Observation(url=self.site["start_url"])


async def learned(session, provider, request, emit, cancelled, *budgets):
    frames = []
    for reference in request.parameters:
        action = ActionSpec(action="select" if reference == "sort_by" else "fill", value_ref=reference,
                            locators=[LocatorSpec(strategy="testid", value=reference)])
        frame = await session.execute(action, request.parameters)
        frames.append(frame)
        await emit("action", {"frame": frame.model_dump(mode="json")})
    return frames


async def wait_terminal(runtime, identifier):
    async with asyncio.timeout(5):
        while True:
            run = runtime.repository.get_run(identifier)
            if run.status in (RunStatus.SUCCEEDED, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.INTERRUPTED):
                return run
            await asyncio.sleep(.01)


async def test_hot_replay_binds_new_values_without_constructing_provider(tmp_path, monkeypatch):
    monkeypatch.setattr("jit_ui.runtime.service.explore", learned)
    providers = []

    def provider(name, configuration):
        providers.append(name)
        return object()

    runtime = RuntimeService(settings(tmp_path), session_factory=Browser, provider_factory=provider)
    first = await runtime.execute(RunRecord(request=task(mode="cold")))
    assert first.status == RunStatus.SUCCEEDED
    changed = task(mode="hot").model_copy(update={"workflow_id": first.workflow_id,
        "parameters": {"product_name": "backpack", "max_price": 2000, "sort_by": "price_asc"}})
    second = await runtime.execute(RunRecord(request=changed))
    assert second.status == RunStatus.SUCCEEDED
    assert second.result == changed.parameters
    assert second.metrics.model_calls == 0 and providers == ["fixture"]
    assert not runtime._compatible(runtime.repository.get_workflow(first.workflow_id),
                                   changed.model_copy(update={"provider": "gemini"}))
    runtime.repository.close()


async def test_actual_usage_is_accounted_after_success_and_hot_replay(tmp_path, monkeypatch):
    async def measured(session, provider, request, emit, cancelled, *budgets):
        await emit("provider_usage", {"input_tokens": 900, "output_tokens": 100, "model": "measured-test", "usage_known": True})
        return await learned(session, provider, request, emit, cancelled, *budgets)

    monkeypatch.setattr("jit_ui.runtime.service.explore", measured)
    runtime = RuntimeService(settings(tmp_path), session_factory=Browser, provider_factory=lambda *args: object())
    request = task(mode="cold").model_copy(update={"provider": "gemini"})
    cold = await runtime.execute(RunRecord(request=request))
    assert cold.metrics.estimated_tokens_saved == 0
    hot = await runtime.execute(RunRecord(request=request.model_copy(update={"mode": "hot", "workflow_id": cold.workflow_id})))
    assert hot.status == RunStatus.SUCCEEDED
    assert hot.metrics.estimated_tokens_saved == 1000
    assert runtime.repository.get_run(hot.run_id).metrics.baseline_run_id == cold.run_id
    assert runtime.ledger.report(hot.automation_id)["summary"]["estimated_tokens_saved"] == 1000
    runtime.repository.close()


def test_amazon_cache_matches_filter_shape_values_and_automation_set(tmp_path):
    from jit_ui.compiler import compile_trace
    configuration = settings(tmp_path)
    runtime = RuntimeService(configuration)
    request = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Find dolls under 500",
                          parameters={"query": "dolls", "max_price": 500})
    specs = site_definitions(configuration)["amazon_in"]["parameters"]
    workflow = compile_trace(request, [ActionFrame(action=ActionSpec(action="goto",
        url="https://www.amazon.in/s?k=dolls&high-price=500"))], specs, "learn-budget")
    changed = request.model_copy(update={"parameters": {"query": "toys", "max_price": 700}})
    assert runtime._compatible(workflow, changed)
    assert not runtime._compatible(workflow, changed.model_copy(update={"parameters": {"query": "toys"}}))
    workflow.metadata["fixed_filter_values"] = {"max_price": 500}
    assert not runtime._compatible(workflow, changed)
    workflow.metadata["fixed_filter_values"] = {}
    group = runtime.ledger.prepare(request)
    runtime.repository.associate_workflow(workflow.workflow_id, group)
    other = runtime.repository.create_automation("Separate toys experiment", "amazon_in", "search_products")
    assert not runtime._compatible(workflow, changed.model_copy(update={"automation_id": other["automation_id"]}))
    workflow.metadata["inspection_bound_query"] = "dolls"
    assert not runtime._compatible(workflow, changed)
    runtime.repository.close()


async def test_cancel_closes_active_browser_and_worker_continues(tmp_path, monkeypatch):
    monkeypatch.setattr("jit_ui.runtime.service.explore", learned)
    opened = asyncio.Event()
    browsers = []

    class SlowBrowser(Browser):
        async def open(self):
            browsers.append(self)
            if len(browsers) == 1:
                opened.set()
                await asyncio.Event().wait()
            return self

    runtime = RuntimeService(settings(tmp_path), session_factory=SlowBrowser, provider_factory=lambda *args: object())
    await runtime.start()
    try:
        first = await runtime.submit(task(mode="cold"))
        await asyncio.wait_for(opened.wait(), 3)
        second = await runtime.submit(task(mode="cold"))
        await runtime.cancel(first.run_id)
        assert (await wait_terminal(runtime, first.run_id)).status == RunStatus.CANCELLED
        assert browsers[0].closed
        assert (await wait_terminal(runtime, second.run_id)).status == RunStatus.SUCCEEDED
        assert runtime.worker and not runtime.worker.done()
    finally:
        await runtime.stop()


async def test_restart_marks_active_interrupted_and_recovers_queued(tmp_path, monkeypatch):
    monkeypatch.setattr("jit_ui.runtime.service.explore", learned)
    repository = Repository(settings(tmp_path))
    interrupted = RunRecord(request=task(), status=RunStatus.RUNNING)
    queued = RunRecord(request=task(mode="cold"))
    repository.save_run(interrupted)
    repository.save_run(queued)
    repository.close()
    runtime = RuntimeService(settings(tmp_path), session_factory=Browser, provider_factory=lambda *args: object())
    await runtime.start()
    try:
        assert runtime.repository.get_run(interrupted.run_id).status == RunStatus.INTERRUPTED
        assert (await wait_terminal(runtime, queued.run_id)).status == RunStatus.SUCCEEDED
        events = runtime.repository.list_events(queued.run_id)
        assert [item.sequence for item in events] == list(range(1, len(events) + 1))
        assert runtime.repository.list_events(queued.run_id, events[-2].sequence) == events[-1:]
    finally:
        await runtime.stop()


async def test_invalid_parameters_are_rejected_before_persistence(tmp_path):
    runtime = RuntimeService(settings(tmp_path))
    try:
        with pytest.raises(ValueError):
            await runtime.submit(task().model_copy(update={"parameters": {"product_name": "shoes", "unknown": True}}))
        assert runtime.repository.list_runs() == []
    finally:
        runtime.repository.close()


async def test_amazon_query_reuses_verified_function_across_task_phrasing(tmp_path):
    configuration = settings(tmp_path)
    runtime = RuntimeService(configuration)
    workflow = WorkflowIR(site_id="amazon_in", goal_id="search_products",
        intent_signature="search_products: search amazon india using the product input",
        parameters={"query": ParameterSpec()}, steps=[WorkflowStep(step_id="query", intent="Enter query",
            actions=[ActionSpec(action="fill", value_ref="query", locators=[LocatorSpec(strategy="css", value="#twotabsearchtextbox")])])])
    try:
        runtime.repository.publish(workflow, generate_python(workflow, site_definitions(configuration)["amazon_in"]))
        request = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Go to Amazon and search for backpacks.",
                              parameters={"query": "backpacks"}, provider="ollama", mode="auto")
        assert (await runtime.route(request)).workflow_id == workflow.workflow_id
        assert await runtime.route(request.model_copy(update={"mode": "cold"})) is None
        assert await runtime.route(request.model_copy(update={"provider": "fixture"})) is None
    finally:
        runtime.repository.close()


async def test_artifact_tampering_invalidates_routing_and_exports(tmp_path, monkeypatch):
    monkeypatch.setattr("jit_ui.runtime.service.explore", learned)
    runtime = RuntimeService(settings(tmp_path), session_factory=Browser, provider_factory=lambda *args: object())
    run = await runtime.execute(RunRecord(request=task(mode="cold")))
    workflow = runtime.repository.get_workflow(run.workflow_id)
    assert workflow is not None
    with pytest.raises(VersionConflict):
        runtime.repository.publish(workflow, "overwrite", expected_version=workflow.version)
    artifact = runtime.repository.artifact(workflow.workflow_id, 1, "ir")
    artifact.write_text("{}", encoding="utf-8")
    assert runtime.repository.get_workflow(workflow.workflow_id) is None
    assert runtime.repository.artifact(workflow.workflow_id, 1, "python") is None
    assert await runtime.route(task(mode="hot")) is None
    runtime.repository.close()


async def test_failed_learning_keeps_trace_without_publishing_a_workflow(tmp_path, monkeypatch):
    async def fails_after_action(session, provider, request, emit, cancelled, *budgets):
        frame = ActionFrame(action=ActionSpec(action="wait", value="0"))
        await emit("action", {"frame": frame.model_dump(mode="json")})
        raise RuntimeError("The next decision failed.")

    monkeypatch.setattr("jit_ui.runtime.service.explore", fails_after_action)
    runtime = RuntimeService(settings(tmp_path), session_factory=Browser, provider_factory=lambda *args: object())
    run = await runtime.execute(RunRecord(request=task(mode="cold")))
    assert run.status == RunStatus.FAILED
    assert runtime.repository.list_workflows() == []
    trace = json.loads((tmp_path / "runs" / run.run_id / "trace.json").read_text(encoding="utf-8"))
    assert len(trace) == 1 and trace[0]["action"]["action"] == "wait"
    runtime.repository.close()


async def test_web_and_amazon_tasks_always_have_visible_browser_and_manual_handler(tmp_path, monkeypatch):
    captured_sessions = []

    class CapturingBrowser(Browser):
        def __init__(self, settings, artifact_dir, site):
            super().__init__(settings, artifact_dir, site)
            self.browser_settings = settings
            self.manual_handler = None
            self.confirmation_handler = None
            self.defer_navigation = False
            captured_sessions.append(self)

    monkeypatch.setattr("jit_ui.runtime.service.explore", learned)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    runtime = RuntimeService(settings(tmp_path), session_factory=CapturingBrowser, provider_factory=lambda *args: object())

    # Task with interactive=False (default) on web
    web_task = TaskRequest(site_id="web", goal_id="web_search", target_url="https://example.com/",
                           task="Search example", parameters={"query": "test"}, interactive=False, mode="cold")
    await runtime.execute(RunRecord(request=web_task))
    assert len(captured_sessions) == 1
    assert captured_sessions[0].browser_settings.browser_headless is False
    assert captured_sessions[0].manual_handler is not None

    # Task with interactive=False (default) on amazon_in
    amazon_task = TaskRequest(site_id="amazon_in", goal_id="search_products",
                              task="Search backpacks", parameters={"query": "backpacks"}, interactive=False, mode="cold")
    await runtime.execute(RunRecord(request=amazon_task))
    assert len(captured_sessions) == 2
    assert captured_sessions[1].browser_settings.browser_headless is False
    assert captured_sessions[1].manual_handler is not None

    # In CI, ensure headless remains True to protect headless Linux runners
    monkeypatch.setenv("CI", "true")
    ci_task = TaskRequest(site_id="web", goal_id="web_search", target_url="https://example.com/",
                          task="Search CI", parameters={"query": "ci"}, interactive=False, mode="cold")
    await runtime.execute(RunRecord(request=ci_task))
    assert len(captured_sessions) == 3
    assert captured_sessions[2].browser_settings.browser_headless is True
    assert captured_sessions[2].manual_handler is not None
    runtime.repository.close()
