"""Persistent single-worker execution, deterministic replay, and verified repair."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from jit_ui.browser import BrowserActionError, BrowserSession, SiteBlocked
from jit_ui.compiler import compile_trace, generate_python
from jit_ui.config import Settings
from jit_ui.domain.models import ActionSpec, RunMode, RunRecord, RunStatus, TaskRequest, WorkflowIR, utc_now
from jit_ui.domain.sites import task_site, validate_task
from jit_ui.exploration.explorer import ExplorationError, explore
from jit_ui.healing.surgical import heal_step
from jit_ui.providers import create_provider
from jit_ui.routing.parameters import validate_parameters
from jit_ui.routing.semantic import SemanticRouter, abstract_intent
from jit_ui.storage.repository import Repository
from jit_ui.benchmarks.ledger import AutomationLedger
from jit_ui.domain.intent import filter_shape


TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.BLOCKED, RunStatus.INTERRUPTED}


class QueueFullError(ValueError):
    pass


class RuntimeService:
    def __init__(self, settings: Settings, *, repository=None, session_factory=BrowserSession,
                 provider_factory=create_provider, index=None):
        self.settings = settings
        self.repository = repository or Repository(settings)
        self.ledger = AutomationLedger(self.repository)
        self.session_factory = session_factory
        self.provider_factory = provider_factory
        self.index = index
        self.index_error: str | None = None
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.active_task: asyncio.Task | None = None
        self.active_run_id: str | None = None
        self.stopping = False
        self._cancelled: set[str] = set()
        self._confirmations: dict[str, asyncio.Future] = {}

    @property
    def busy(self) -> bool:
        return self.active_run_id is not None or self.queue.qsize() > 0

    async def start(self):
        if self.worker and not self.worker.done():
            return
        self.stopping = False
        self.repository.recover_interrupted()
        self.ledger.migrate_history()
        if self.index is None and self.settings.embedding_backend == "lexical":
            from jit_ui.routing.index import LexicalWorkflowIndex

            self.index = LexicalWorkflowIndex()
        elif self.index is None and self.settings.embedding_backend == "chroma":
            from jit_ui.routing.index import ChromaWorkflowIndex

            self.index = ChromaWorkflowIndex(self.settings.data_dir / "index")
        for workflow in self.repository.list_workflows():
            await self._index(workflow)
        for run in reversed(self.repository.list_runs(limit=10000)):
            if run.status == RunStatus.QUEUED:
                self.queue.put_nowait(run.run_id)
        self.worker = asyncio.create_task(self._work(), name="jit-ui-worker")

    async def stop(self):
        self.stopping = True
        if self.active_task:
            self.active_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.active_task
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        self.worker = None
        self.repository.close()

    async def submit(self, task: TaskRequest) -> RunRecord:
        task = validate_task(task, self.settings)
        if self.queue.qsize() >= self.settings.queue_capacity:
            raise QueueFullError("The run queue is full. Wait for a queued run to finish.")
        run = RunRecord(request=task)
        run.automation_id = self.ledger.prepare(task)
        run.request = task.model_copy(update={"automation_id": run.automation_id})
        run.metrics.simulated = task.provider == "fixture"
        self.repository.save_run(run)
        self.repository.add_event(run.run_id, "status", {"status": run.status, "mode": run.mode})
        self.queue.put_nowait(run.run_id)
        return run

    async def cancel(self, run_id: str) -> RunRecord | None:
        run = self.repository.get_run(run_id)
        if run is None or run.status in TERMINAL:
            return run
        self._cancelled.add(run_id)
        if run.status == RunStatus.QUEUED:
            run.status = RunStatus.CANCELLED
            run.finished_at = utc_now()
        else:
            run.status = RunStatus.CANCELLING
        self.repository.save_run(run)
        self.repository.add_event(run_id, "status", {"status": run.status, "mode": run.mode})
        if self.active_run_id == run_id and self.active_task:
            self.active_task.cancel()
        return run

    async def confirm(self, run_id: str, approve: bool):
        run = self.repository.get_run(run_id)
        future = self._confirmations.get(run_id)
        if not run or run.status != RunStatus.AWAITING_CONFIRMATION or future is None or future.done():
            raise ValueError("This run has no pending action to approve.")
        future.set_result(approve)
        return run

    async def _work(self):
        while True:
            identifier = await self.queue.get()
            try:
                run = self.repository.get_run(identifier)
                if run is None or run.status != RunStatus.QUEUED:
                    continue
                self.active_run_id = identifier
                self.active_task = asyncio.create_task(self.execute(run))
                try:
                    await self.active_task
                except asyncio.CancelledError:
                    if self.stopping:
                        raise
                    # Cancellation can arrive before the child coroutine starts.
                    pending = self.repository.get_run(identifier)
                    if pending and pending.status not in TERMINAL:
                        pending.status = RunStatus.CANCELLED
                        pending.finished_at = utc_now()
                        self.repository.save_run(pending)
                        self.repository.add_event(identifier, "status", {"status": pending.status})
            finally:
                self.active_run_id = None
                self.active_task = None
                self._cancelled.discard(identifier)
                self.queue.task_done()

    def sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, str):
            root = str(self.settings.data_dir.resolve())
            if value.startswith(root + os.sep):
                value = Path(value).relative_to(root).as_posix()
            for secret in (self.settings.gemini_api_key, self.settings.fixture_password):
                if secret:
                    value = value.replace(secret, "[credential]")
        return value

    async def _index(self, workflow: WorkflowIR):
        if self.index is None:
            return
        try:
            await asyncio.to_thread(self.index.upsert, workflow.workflow_id, workflow.intent_signature,
                                    {"site_id": workflow.site_id, "goal_id": workflow.goal_id,
                                     "simulated": bool(workflow.metadata.get("simulated", False))})
            self.index_error = None
        except Exception as exc:
            self.index_error = str(exc)

    def _compatible(self, workflow: WorkflowIR | None, task: TaskRequest) -> bool:
        if (workflow is None or workflow.site_id != task.site_id or workflow.goal_id != task.goal_id
                or workflow.schema_version != 1 or workflow.runtime_version != 1
                or bool(workflow.metadata.get("simulated", False)) != (task.provider == "fixture")):
            return False
        if workflow.metadata.get("execution_strategy") == "native" or str(workflow.metadata.get("model", "")).startswith("native-"):
            return False
        if task.automation_id and self.repository.workflow_automation(workflow.workflow_id) not in {None, task.automation_id}:
            return False
        if task.site_id == "amazon_in":
            actual = set(workflow.metadata.get("amazon_filters", []))
            if actual != set(filter_shape(task.parameters)):
                return False
            if any(task.parameters.get(key) != value for key, value in workflow.metadata.get("fixed_filter_values", {}).items()):
                return False
            if workflow.metadata.get("inspection_bound_query") not in {None, task.parameters.get("query")}:
                return False
        if task.site_id == "web":
            from jit_ui.domain.web_tasks import needs_model_interpretation
            if needs_model_interpretation(task) and not workflow.metadata.get("task_interpretation"):
                return False
            if task.objectives and task.objectives != workflow.metadata.get("objectives", []):
                return False
            from jit_ui.exploration.completion import is_playback_objective
            if any(is_playback_objective(objective) and not any(condition.objective_index == index and condition.kind == "media_playing" for condition in workflow.goal_conditions)
                   for index, objective in enumerate(task.objectives)):
                return False
            if (task.goal_id == "web_search" and any(task.parameters.get(bound) is not None for bound in ("min_price", "max_price"))
                    and workflow.metadata.get("budget_evidence_policy") != "visible-controls-v1"):
                return False
            if workflow.metadata.get("intent_key") != task.intent_key or workflow.metadata.get("target_url") != task.target_url:
                return False
            if any(task.parameters.get(key) != value for key, value in workflow.metadata.get("fixed_parameters", {}).items()):
                return False
            if task.completion_conditions:
                from jit_ui.compiler.trace_compiler import parameterize_condition
                compiled_conditions = [condition.model_dump(mode="json") for condition in workflow.goal_conditions]
                if any(parameterize_condition(condition, task.parameters).model_dump(mode="json") not in compiled_conditions
                       for condition in task.completion_conditions):
                    return False
            if not workflow.metadata.get("goal_parameters_bound") and workflow.metadata.get("execution_strategy") != "native":
                original = self.repository.get_run(workflow.metadata.get("learning_run_id") or workflow.source_run_id)
                if original and original.request.parameters != task.parameters:
                    return False
        try:
            validate_parameters(task.parameters, workflow.parameters)
        except ValueError:
            return False
        return True

    async def route(self, task: TaskRequest) -> WorkflowIR | None:
        if task.mode == "cold":
            return None
        if task.workflow_id:
            workflow = self.repository.get_workflow(task.workflow_id)
            if not self._compatible(workflow, task):
                raise ExplorationError("The selected workflow is unavailable or incompatible with this task and provider.",
                                       "WORKFLOW_INCOMPATIBLE")
            return workflow
        signature = abstract_intent(task)
        candidates = sorted(self.repository.list_workflows(), key=lambda item: item.created_at, reverse=True)
        for workflow in candidates:
            if (workflow.intent_signature == signature or (task.intent_key or "").endswith(":control_activated")) and self._compatible(workflow, task):
                return workflow
        # Amazon's registered goal is completely defined by its query argument.
        # Reuse the learned search function when users phrase the same request differently.
        if task.site_id == "amazon_in" and task.goal_id == "search_products":
            for workflow in candidates:
                if self._compatible(workflow, task):
                    return workflow
        if self.index is not None:
            def compatible_lookup(identifier):
                item = self.repository.get_workflow(identifier)
                return item if self._compatible(item, task) else None

            router = SemanticRouter(self.index, compatible_lookup,
                                    threshold=self.settings.similarity_threshold)
            workflow = await asyncio.to_thread(router.route, task)
            self.index_error = router.last_index_error
            if self._compatible(workflow, task):
                return workflow
        return None

    async def activate(self, identifier: str, version: int) -> WorkflowIR:
        if self.busy:
            raise ValueError("Wait for current runs to finish before changing an active workflow version.")
        self.repository.activate(identifier, version)
        workflow = self.repository.get_workflow(identifier)
        await self._index(workflow)
        return workflow

    async def execute(self, run: RunRecord) -> RunRecord:
        """Execute an existing run; queue worker and integration tests share this path."""
        started = time.perf_counter()
        session = None
        provider = None
        history = []
        recorded_frames = []
        execution_deadline = None
        task_interpretation = None
        run.metrics.simulated = run.request.provider == "fixture"
        run.automation_id = run.automation_id or self.ledger.prepare(run.request)
        run.request = run.request.model_copy(update={"automation_id": run.automation_id})
        workflow = None
        run_dir = self.settings.data_dir / "runs" / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        async def emit(kind: str, data: dict):
            if kind == "provider_usage":
                if not data.get("usage_known", True):
                    run.metrics.token_usage_complete = False
                run.metrics.model_calls += int(data.get("attempts", 1))
                run.metrics.input_tokens += int(data.get("input_tokens", 0))
                run.metrics.output_tokens += int(data.get("output_tokens", 0))
                run.metrics.model_duration_ms += float(data.get("duration_ms", 0))
                run.metrics.model = data.get("model") or run.metrics.model
            elif kind == "action":
                run.metrics.actions += 1
                if data.get("frame"):
                    recorded_frames.append(self.sanitize(data["frame"]))
            elif kind == "provider_request" and provider is not None:
                remaining = self.settings.max_model_calls - run.metrics.model_calls
                if remaining <= 0:
                    raise ExplorationError("Global model attempt budget is exhausted.", "MODEL_BUDGET_EXCEEDED")
                if hasattr(provider, "remaining_attempts"):
                    provider.remaining_attempts = min(int(data.get("max_attempts", remaining)), remaining)
            run.metrics.duration_ms = (time.perf_counter() - started) * 1000
            self.repository.save_run(run)
            self.repository.add_event(run.run_id, kind, self.sanitize(data))

        async def mode(value: RunMode):
            run.mode = value
            await emit("mode", {"mode": value})

        def cancelled():
            return run.run_id in self._cancelled or self.stopping

        def get_provider():
            nonlocal provider
            if provider is None:
                provider = self.provider_factory(run.request.provider, self.settings)
            return provider

        async def review_action(review):
            loop = asyncio.get_running_loop()
            deadline = execution_deadline.when() if execution_deadline is not None else None
            remaining = max(0, deadline-loop.time()) if deadline is not None else None
            manual_pause = review.get("manual") and execution_deadline is not None
            if manual_pause:
                execution_deadline.reschedule(None)
            future = asyncio.get_running_loop().create_future()
            self._confirmations[run.run_id] = future
            run.pending_confirmation = self.sanitize(review)
            run.status = RunStatus.AWAITING_CONFIRMATION
            await emit("confirmation_required", {"review": run.pending_confirmation, "status": run.status})
            try:
                approved = await future
            finally:
                if manual_pause and not execution_deadline.expired():
                    execution_deadline.reschedule(loop.time()+remaining if remaining is not None else None)
            self._confirmations.pop(run.run_id, None)
            if not approved:
                raise asyncio.CancelledError()
            run.pending_confirmation = None
            run.status = RunStatus.RUNNING
            await emit("confirmation_approved", {"label": review.get("label"), "status": run.status})

        try:
            run.status = RunStatus.RUNNING
            run.started_at = utc_now()
            await emit("status", {"status": run.status, "mode": run.mode})
            async with asyncio.timeout(self.settings.run_timeout_seconds) as execution_deadline:
                from jit_ui.domain.web_tasks import needs_model_interpretation
                from jit_ui.providers.task_interpretation import TaskInterpretation, apply_interpretation, interpretation_cache_key

                interpretation_key = interpretation_cache_key(run.request)
                if needs_model_interpretation(run.request):
                    for stored in self.repository.list_workflows():
                        cached = stored.metadata.get("task_interpretation")
                        if cached and cached.get("cache_key") == interpretation_key:
                            try:
                                interpreted = TaskInterpretation.model_validate(cached["result"])
                            except (ValueError, KeyError):
                                continue
                            run.request = apply_interpretation(run.request, interpreted)
                            task_interpretation = cached
                            await emit("task_interpreted", {"query":run.request.parameters.get("query"), "objectives":run.request.objectives, "cached":True})
                            break
                routing_started = time.perf_counter()
                workflow = await self.route(run.request)
                run.metrics.embedding_duration_ms = (time.perf_counter() - routing_started) * 1000
                if workflow is None and run.request.mode == "hot":
                    raise ExplorationError("No compatible workflow was found for a forced hot run. Learn it first.",
                                           "WORKFLOW_NOT_FOUND")
                if workflow:
                    existing_group = self.repository.workflow_automation(workflow.workflow_id)
                    if existing_group:
                        run.automation_id = existing_group
                    else:
                        self.repository.associate_workflow(workflow.workflow_id, run.automation_id)
                # Fail missing cloud credentials before opening a browser. Hot replay needs no provider.
                if workflow is None:
                    get_provider()
                    if needs_model_interpretation(run.request) and task_interpretation is None and hasattr(provider, "interpret_task"):
                        await emit("provider_request", {"provider":run.request.provider, "model":getattr(provider, "model", None),
                            "max_attempts":self.settings.max_model_calls-run.metrics.model_calls, "phase":"intent"})
                        before_attempts = getattr(provider, "attempts_total", 0)
                        intent_started = time.perf_counter()
                        try:
                            interpreted, usage = await provider.interpret_task(run.request)
                        except Exception as exc:
                            used_attempts = getattr(provider, "attempts_total", 0)-before_attempts
                            if used_attempts:
                                await emit("provider_usage", {**getattr(exc, "usage", {}), "attempts":used_attempts,
                                    "model":getattr(provider, "model", None), "usage_known":getattr(exc, "usage_known", False),
                                    "duration_ms":(time.perf_counter()-intent_started)*1000, "phase":"intent", "failed":True})
                            raise
                        await emit("provider_usage", {**usage.model_dump(mode="json"), "phase":"intent"})
                        run.request = apply_interpretation(run.request, interpreted)
                        task_interpretation = {"cache_key":interpretation_key, "result":interpreted.model_dump(mode="json")}
                        await emit("task_interpreted", {"query":run.request.parameters.get("query"), "objectives":run.request.objectives, "cached":False})
                site = task_site(run.request, self.settings)
                if workflow and run.request.site_id == "web":
                    run.request.completion_conditions = [condition.model_copy(deep=True) for condition in workflow.goal_conditions]
                    site["completion_conditions"] = workflow.metadata.get("completion_conditions", [])
                always_visible = run.request.site_id in ("web", "amazon_in")
                browser_settings = self.settings.model_copy(update={"browser_headless": False}) if run.request.interactive or always_visible else self.settings
                session = self.session_factory(browser_settings, run_dir, site)
                if run.request.site_id == "web":
                    session.confirmation_handler = review_action
                if run.request.interactive or always_visible:
                    session.manual_handler = review_action
                first_action = next((action for step in workflow.steps for action in step.actions), None) if workflow else None
                if run.request.site_id == "web" or (first_action and first_action.action == "goto"):
                    session.defer_navigation = True
                await session.open()
                if workflow is None:
                    await mode(RunMode.COLD)
                    bootstrap = []
                    if run.request.site_id == "web":
                        frame = await session.execute(ActionSpec(action="goto", url=run.request.target_url, intent="Open user-requested website"), run.request.parameters)
                        bootstrap = [frame]
                        await emit("action", {"frame": frame.model_dump(mode="json"), "origin": "user_request"})
                    history = await explore(session, get_provider(), run.request, emit, cancelled,
                                            self.settings.max_model_calls, self.settings.max_cold_actions,
                                            **({"initial_frames": bootstrap} if run.request.site_id == "web" else {}))
                    passed, result = await session.verify_goal(run.request.goal_id, run.request.parameters)
                    if not passed:
                        raise ExplorationError("Final goal verification failed.", "GOAL_NOT_VERIFIED")
                    if run.request.compile_workflow:
                        await mode(RunMode.COMPILING)
                        workflow = compile_trace(run.request, history, site["parameters"], run.run_id)
                        workflow.metadata.update({"simulated": run.metrics.simulated, "provider": run.request.provider,
                                                  "model": run.metrics.model, "learning_run_id": run.run_id,
                                                  "execution_strategy": run.metrics.execution_strategy})
                        workflow.metadata["decision_policy"] = "llm"
                        if task_interpretation is not None:
                            workflow.metadata["task_interpretation"] = task_interpretation
                        code = generate_python(workflow, site)
                        self.repository.publish(workflow, code)
                        self.repository.associate_workflow(workflow.workflow_id, run.automation_id)
                        await self._index(workflow)
                        await emit("compiled", {"workflow_id": workflow.workflow_id, "version": workflow.version,
                                                "stats": workflow.compile_stats})
                else:
                    run.metrics.execution_strategy = "replay"
                    await mode(RunMode.HOT)
                    run.workflow_id, run.workflow_version = workflow.workflow_id, workflow.version
                    run.metrics.model = str(workflow.metadata.get("model") or "deterministic-playwright")
                    baseline_version = workflow.version
                    repaired = False
                    for step_index in range(len(workflow.steps)):
                        if cancelled():
                            raise asyncio.CancelledError()
                        step = workflow.steps[step_index]
                        completed = []
                        try:
                            for action in step.actions:
                                frame = await session.execute(action, run.request.parameters)
                                history.append(frame)
                                completed.append(action)
                                await emit("action", {"frame": frame.model_dump(mode="json"), "step_id": step.step_id})
                            if step.post_conditions and not await session.check_conditions(step.post_conditions,
                                                                                           run.request.parameters):
                                raise BrowserActionError("The stored step post-condition no longer holds.")
                        except BrowserActionError as exc:
                            if exc.frame:
                                await emit("action", {"frame": exc.frame.model_dump(mode="json"),
                                                      "failed": True, "step_id": step.step_id})
                            await emit("step_failed", {"step_id": step.step_id, "error": str(exc)})
                            if run.metrics.healed_steps >= self.settings.max_repaired_steps:
                                raise ExplorationError("The run's repaired-step limit was reached.", "HEALING_LIMIT_EXCEEDED")
                            await mode(RunMode.HEALING)
                            patched, recovered = await heal_step(session, get_provider(), run.request, workflow,
                                                                 step_index, history, emit, cancelled, self.settings,
                                                                 completed_actions=completed)
                            history.extend(recovered)
                            if recovered:
                                run.metrics.healed_steps += 1
                                repaired = True
                                workflow = patched
                    passed, result = await session.verify_goal(run.request.goal_id, run.request.parameters)
                    if not passed:
                        raise ExplorationError("Replay finished without satisfying the independent goal.", "GOAL_NOT_VERIFIED")
                    if repaired:
                        workflow.parent_version = baseline_version
                        workflow.version = self.repository.next_version(workflow.workflow_id)
                        workflow.created_at = utc_now()
                        workflow.source_run_id = run.run_id
                        code = generate_python(workflow, site)
                        self.repository.publish(workflow, code, expected_version=baseline_version)
                        await self._index(workflow)
                        await emit("patch_published", {"workflow_id": workflow.workflow_id,
                                                       "parent_version": baseline_version, "version": workflow.version})
                if workflow:
                    run.workflow_id, run.workflow_version = workflow.workflow_id, workflow.version
                observation = await session.observe()
                await emit("screenshot", {"path": observation.screenshot_path, "url": observation.url,
                                          "element_count": len(observation.elements), "final": True})
                run.result = result
                run.status = RunStatus.SUCCEEDED
        except asyncio.CancelledError:
            run.status = RunStatus.INTERRUPTED if self.stopping else RunStatus.CANCELLED
            run.error_code = "PROCESS_INTERRUPTED" if self.stopping else "CANCELLED"
            run.error = "The execution service stopped." if self.stopping else "Run was cancelled."
        except TimeoutError:
            run.status, run.error_code = RunStatus.FAILED, "RUN_TIMEOUT"
            run.error = f"Run exceeded the {self.settings.run_timeout_seconds}-second timeout."
        except Exception as exc:
            run.error = self.sanitize(str(exc))[:2000]
            run.error_code = getattr(exc, "code", "EXECUTION_FAILED")
            run.status = RunStatus.BLOCKED if isinstance(exc, SiteBlocked) else (
                RunStatus.CANCELLED if run.error_code == "CANCELLED" else RunStatus.FAILED)
        finally:
            self._confirmations.pop(run.run_id, None)
            run.pending_confirmation = None
            if session:
                with suppress(Exception):
                    await session.close()
            if provider and hasattr(provider, "close"):
                with suppress(Exception):
                    await asyncio.wait_for(provider.close(), timeout=5)
            run.finished_at = utc_now()
            run.metrics.duration_ms = (time.perf_counter() - started) * 1000
            self.ledger.account(run, workflow)
            if recorded_frames:
                (run_dir / "trace.json").write_text(json.dumps(recorded_frames, indent=2), encoding="utf-8")
            self.repository.save_run(run)
            self.repository.add_event(run.run_id, "status", {"status": run.status, "mode": run.mode,
                "error": run.error, "error_code": run.error_code, "run": run.model_dump(mode="json")})
        return run
