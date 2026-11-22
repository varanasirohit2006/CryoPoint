"""Bounded repair of one logical step in its existing browser session."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from jit_ui.browser import BrowserActionError, BrowserSession
from jit_ui.compiler import compile_trace
from jit_ui.config.settings import Settings
from jit_ui.domain.models import ActionFrame, ActionSpec, ConditionSpec, TaskRequest, WorkflowIR
from jit_ui.exploration.explorer import validate_proposal
from jit_ui.providers.base import VLMProvider


class HealingError(RuntimeError):
    def __init__(self, message: str, code: str = "HEALING_FAILED") -> None:
        super().__init__(message)
        self.code = code


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise HealingError("Run was cancelled.", "CANCELLED")


def _dismissal(frame: ActionFrame) -> bool:
    semantic = (frame.action.intent + " " + (frame.element.name if frame.element else "")).casefold()
    control_name = frame.element.name.casefold().strip() if frame.element else ""
    control_id = frame.element.testid.casefold() if frame.element else ""
    return frame.action.action == "click" and (
        "dismiss" in semantic
        or any(word in control_id for word in ("dismiss", "close-notice", "close-promotion"))
        or control_name in {"continue browsing", "no thanks", "not now", "accept cookies"}
        or ("close" in semantic and any(word in semantic for word in ("popup", "notice", "promotion", "announcement", "overlay", "cookie")))
    )


def _preparatory(frame: ActionFrame, desired: ActionSpec) -> bool:
    if _dismissal(frame) or frame.action.action in {"scroll", "wait"}:
        return True
    semantic = (frame.action.intent + " " + (frame.element.name if frame.element else "")).casefold()
    desired_semantic = desired.intent.casefold()
    opening = any(word in semantic for word in ("open", "expand", "reveal", "more actions", "order actions", "refine results"))
    desired_opening = any(word in desired_semantic for word in ("open", "expand", "reveal"))
    return frame.action.action == "click" and opening and not desired_opening and frame.action.effect != "mutation"


def _same_operation(action: ActionSpec, desired: ActionSpec) -> bool:
    if action.action != desired.action:
        return False
    if desired.value_ref and action.value_ref != desired.value_ref:
        return False
    if desired.action in {"fill", "select", "adjust"} and not desired.value_ref:
        return action.value == desired.value
    return True


async def heal_step(
    session: BrowserSession,
    provider: VLMProvider,
    task: TaskRequest,
    workflow: WorkflowIR,
    step_index: int,
    history: list[ActionFrame],
    on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
    cancelled: Callable[[], bool],
    settings: Settings,
    completed_actions: list[ActionSpec] | None = None,
) -> tuple[WorkflowIR, list[ActionFrame]]:
    if not 0 <= step_index < len(workflow.steps):
        raise HealingError("Failed step index is outside the workflow.", "INVALID_HEALING_STEP")
    original = workflow.steps[step_index]
    prefix = [action.model_copy(deep=True) for action in (completed_actions or [])]
    if len(prefix) > len(original.actions) or not original.actions:
        raise HealingError("Completed action prefix is inconsistent with the workflow.", "INVALID_HEALING_STEP")
    if any(action.model_dump() != expected.model_dump() for action, expected in zip(prefix, original.actions)):
        raise HealingError("Completed action prefix does not match the workflow.", "INVALID_HEALING_STEP")
    desired = original.actions[min(len(prefix), len(original.actions) - 1)]
    _check_cancelled(cancelled)
    if original.effect == "mutation" or desired.effect == "mutation":
        passed, _result = await session.verify_goal(task.goal_id, task.parameters)
        meaningful = [condition for condition in original.post_conditions if condition.kind not in {"url_contains", "visible"}]
        if passed or (meaningful and await session.check_conditions(meaningful, task.parameters)):
            await on_event("healing_verified", {"step_id": original.step_id, "already_completed": True, "patched": False})
            return workflow.model_copy(deep=True), []
    if any(action.effect == "mutation" for action in prefix):
        raise HealingError("A completed mutation prefix needs independent state reconciliation.", "MUTATION_STATE_UNCERTAIN")

    instruction = (
        "Repair ONLY the failed logical step in the CURRENT browser state. "
        f"Stored intent: {original.intent}. Failed action intent: {desired.intent}. "
        f"Required operation: {desired.action}; required value_ref: {desired.value_ref or 'none'}. "
        "Earlier workflow steps and the recorded successful subprefix are already completed; do not repeat them. "
        "You may first dismiss a blocking notice or open a menu exposing the intended control. "
        "Do not restart, navigate away, advance unrelated workflow steps, or repeat a mutation already applied. "
        "Use only current numbered elements. Stop when this operation's expected state is established."
    )
    await on_event("healing_started", {"step_id": original.step_id, "intent": original.intent})
    recovered: list[ActionFrame] = []
    attempts, actions = 0, 0
    for _ in range(settings.max_repair_actions):
        _check_cancelled(cancelled)
        remaining = settings.max_repair_calls - attempts
        if remaining <= 0:
            raise HealingError("Scoped repair model attempt budget is exhausted.", "HEALING_MODEL_BUDGET_EXCEEDED")
        observation = await session.observe()
        await on_event("screenshot", {"path": observation.screenshot_path, "observation_id": observation.observation_id,
                                      "url": observation.url, "element_count": len(observation.elements), "scope": "healing"})
        if hasattr(provider, "remaining_attempts"):
            provider.remaining_attempts = remaining  # type: ignore[attr-defined]
        await on_event("provider_request", {"max_attempts": remaining, "scope": "healing", "step_id": original.step_id})
        if getattr(provider, "remaining_attempts", remaining) is not None and getattr(provider, "remaining_attempts", remaining) < 1:
            raise HealingError("Global model attempt budget is exhausted.", "MODEL_BUDGET_EXCEEDED")
        _check_cancelled(cancelled)
        before_attempts = int(getattr(provider, "attempts_total", 0))
        started = time.perf_counter()
        try:
            response = await provider.decide(observation, task, history + recovered, instruction=instruction)
        except Exception as exc:
            actual = max(0, int(getattr(provider, "attempts_total", before_attempts)) - before_attempts)
            attempts += actual
            if actual:
                await on_event("provider_usage", {"attempts": actual, "input_tokens": 0, "output_tokens": 0,
                                                  **getattr(exc, "usage", {}),
                                                  "usage_known": actual == 1 and bool(getattr(exc, "usage", {})),
                                                  "duration_ms": (time.perf_counter() - started) * 1000,
                                                  "model": getattr(provider, "model", task.provider), "failed": True, "scope": "healing"})
            raise HealingError(str(exc), getattr(exc, "code", "HEALING_PROVIDER_FAILED")) from exc
        actual = max(1, response.attempts, int(getattr(provider, "attempts_total", before_attempts)) - before_attempts)
        attempts += actual
        usage = response.model_dump(mode="json", exclude={"action"})
        usage.update({"attempts": actual, "scope": "healing"})
        await on_event("provider_usage", usage)
        _check_cancelled(cancelled)
        if attempts > settings.max_repair_calls:
            raise HealingError("Provider exceeded the scoped model attempt budget.", "HEALING_MODEL_BUDGET_EXCEEDED")
        if response.done or response.action is None:
            raise HealingError("Recovery completion lacked a verified replacement action.", "HEALING_NOT_VERIFIED")
        try:
            action = validate_proposal(response.action, observation, task, session.site)
        except Exception as exc:
            raise HealingError(str(exc), getattr(exc, "code", "INVALID_MODEL_ACTION")) from exc
        if action.action == "goto" and desired.action != "goto":
            raise HealingError("Scoped repair cannot restart the browser workflow.", "HEALING_SCOPE_VIOLATION")
        proposed_element = next((element for element in observation.elements if element.index == action.target_index), None)
        proposed_frame = ActionFrame(action=action, element=proposed_element)
        if not _same_operation(action, desired) and not _preparatory(proposed_frame, desired):
            raise HealingError("Proposed action advances outside the failed operation.", "HEALING_SCOPE_VIOLATION")
        actions += 1
        try:
            frame = await session.execute(action, task.parameters)
        except BrowserActionError as exc:
            if exc.frame:
                await on_event("action", {"frame": exc.frame.model_dump(mode="json"), "failed": True, "scope": "healing", "step_id": original.step_id})
            raise HealingError(str(exc), "HEALING_ACTION_FAILED") from exc
        if _dismissal(frame):
            frame.action.optional = True
        recovered.append(frame)
        await on_event("action", {"frame": frame.model_dump(mode="json"), "scope": "healing", "step_id": original.step_id})
        _check_cancelled(cancelled)
        compiled = compile_trace(task, [frame], workflow.parameters, workflow.source_run_id or "healing")
        replacement = compiled.steps[0]
        current_action = replacement.actions[0]
        if _preparatory(frame, desired) or not _same_operation(current_action, desired):
            continue
        fresh_passed = bool(replacement.post_conditions) and await session.check_conditions(replacement.post_conditions, task.parameters)
        goal_passed, _result = await session.verify_goal(task.goal_id, task.parameters)
        old_passed = bool(original.post_conditions) and await session.check_conditions(original.post_conditions, task.parameters)
        delta = bool(frame.after_hash and frame.after_hash != frame.before_hash) or frame.after_url != frame.before_url
        if current_action.action in {"fill", "select", "adjust"}:
            verified = fresh_passed
        elif current_action.action in {"click", "press"}:
            verified = delta and (goal_passed or old_passed)
        else:
            verified = goal_passed or old_passed
        if not verified:
            if current_action.effect == "mutation":
                raise HealingError("Mutation outcome is uncertain; it will not be repeated.", "MUTATION_STATE_UNCERTAIN")
            continue
        compiled_repair = compile_trace(task, recovered, workflow.parameters, workflow.source_run_id or "healing")
        patched = workflow.model_copy(deep=True)
        patched.parent_version, patched.version = workflow.version, workflow.version + 1
        patched.steps[step_index].actions = prefix + [action for step in compiled_repair.steps for action in step.actions]
        if current_action.action in {"fill", "select", "adjust"}:
            patched.steps[step_index].post_conditions = replacement.post_conditions
        elif not old_passed and goal_passed:
            patched.steps[step_index].post_conditions = [ConditionSpec(kind="goal", expected=task.goal_id)]
        else:
            patched.steps[step_index].post_conditions = [condition.model_copy(deep=True) for condition in original.post_conditions]
        patched.steps[step_index].effect = "mutation" if any(action.effect == "mutation" for action in patched.steps[step_index].actions) else "read"
        patched.metadata["last_repair"] = {"step_id": original.step_id, "model_attempts": attempts, "actions": actions}
        await on_event("healing_verified", {"step_id": original.step_id, "patched": True, "model_attempts": attempts, "actions": actions})
        return patched, recovered
    raise HealingError("Scoped repair action budget is exhausted.", "HEALING_ACTION_BUDGET_EXCEEDED")
