"""Bounded observe/decide/act loop; successful traces require an independent goal."""

from __future__ import annotations

import time
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

from jit_ui.browser import BrowserActionError, BrowserSession
from jit_ui.domain.models import ActionFrame, ActionSpec, Observation, TaskRequest
from jit_ui.providers.base import VLMProvider


class ExplorationError(RuntimeError):
    def __init__(self, message: str, code: str = "EXPLORATION_FAILED") -> None:
        super().__init__(message)
        self.code = code


class RunCancelled(ExplorationError):
    def __init__(self) -> None:
        super().__init__("Run was cancelled.", "CANCELLED")


def validate_proposal(action: ActionSpec, observation: Observation, task: TaskRequest, site: dict[str, Any]) -> ActionSpec:
    validated = action.model_copy(deep=True)
    if validated.action in {"click", "fill", "select", "adjust", "press"}:
        if validated.target_index is None or not any(element.index == validated.target_index for element in observation.elements):
            raise ExplorationError("Model proposed an element absent from the current observation.", "INVALID_MODEL_ACTION")
        validated.locators = []
        element = next(e for e in observation.elements if e.index == validated.target_index)
        if validated.action == "click" and (validated.value is not None or validated.value_ref):
            raise ExplorationError("Click does not type/select a value. Choose fill or select explicitly.", "INVALID_MODEL_ACTION")
        native_clickable = element.tag == "input" and element.role in {"button", "checkbox", "radio"}
        if task.site_id not in {"amazon_in", "web"} and validated.action == "click" and element.tag in {"input", "textarea", "select"} and not native_clickable:
            raise ExplorationError("This is an input control. Use fill for input/textarea or select for a select control; these actions handle focus automatically. Reserve click for buttons and links.", "INVALID_MODEL_ACTION")
        if validated.action == "fill" and (element.role in {"button", "checkbox", "radio", "slider"} or element.input_type == "range"
                or (element.tag not in {"input", "textarea"} and element.role not in {"textbox", "searchbox", "spinbutton"})):
            raise ExplorationError("Model proposed typing into a non-input control.", "INVALID_MODEL_ACTION")
        if validated.action == "select" and element.tag != "select":
            raise ExplorationError("Select actions require a native select control.", "INVALID_MODEL_ACTION")
        if validated.action == "adjust" and element.role != "slider" and element.input_type != "range":
            raise ExplorationError("Adjust requires a slider control.", "INVALID_MODEL_ACTION")
        if validated.action == "press" and validated.value not in {"Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Space", "Home", "End"}:
            raise ExplorationError("Model proposed an unsupported browser key.", "INVALID_MODEL_ACTION")
    if validated.action in {"scroll", "wait"}:
        if validated.locators:
            raise ExplorationError("Model wait/scroll actions cannot invent selectors.", "INVALID_MODEL_ACTION")
        try:
            int(validated.value or ("500" if validated.action == "scroll" else "250"))
        except ValueError as exc:
            raise ExplorationError("Scroll/wait values must be integer pixel/millisecond counts.", "INVALID_MODEL_ACTION") from exc
    allowed_refs = set(task.parameters) | set(site.get("credentials", {}))
    for key, reference in validated.url_refs.items():
        from jit_ui.runtime.browser_helpers import URL_PARAMETER_BINDINGS
        if URL_PARAMETER_BINDINGS.get(key) != reference or reference not in task.parameters:
            raise ExplorationError("Unknown URL parameter binding.", "INVALID_PARAMETER_REFERENCE")
    if validated.value_ref and validated.value_ref not in allowed_refs:
        raise ExplorationError("Model proposed an unknown parameter reference.", "INVALID_PARAMETER_REFERENCE")
    if validated.action in {"fill", "select", "adjust"} and not validated.value_ref:
        matches = [key for key, value in task.parameters.items() if str(value) == validated.value]
        if len(matches) == 1:
            validated.value_ref, validated.value = matches[0], None
    if validated.action in {"fill", "select", "adjust"} and validated.value is None and not validated.value_ref:
        raise ExplorationError("Input action is missing its value.", "INVALID_MODEL_ACTION")
    if validated.action in {"fill", "adjust"}:
        from jit_ui.providers.action_catalog import compatible_references, field_parameter_hint
        if validated.value_ref and validated.value_ref not in compatible_references(element, list(task.parameters)):
            raise ExplorationError("This control is incompatible with " + validated.value_ref + ". Choose the matching search or price control.", "INVALID_MODEL_ACTION")
        if field_parameter_hint(element) == "query" and any(key in task.parameters for key in ("query", "product_name")) and validated.value is not None:
            from jit_ui.runtime.browser_helpers import observed_number
            number = observed_number(validated.value)
            if number is not None and any(number == task.parameters.get(key) for key in ("min_price", "max_price")):
                raise ExplorationError("A price bound cannot be entered into the search field. Use the numbered price control.", "INVALID_MODEL_ACTION")
        if validated.action == "adjust":
            import math
            try:
                if not math.isfinite(float(task.parameters[validated.value_ref] if validated.value_ref else validated.value)):
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise ExplorationError("Slider adjustment needs a finite numeric value.", "INVALID_MODEL_ACTION") from exc
    if validated.action == "goto" and not validated.url:
        raise ExplorationError("Navigation action is missing its URL.", "INVALID_MODEL_ACTION")
    if validated.action == "goto":
        if task.site_id == "amazon_in":
            # Some structured models attach the query reference to navigation too.
            # Navigation bindings are derived from matching URL values below.
            validated.value_ref = None
            address = urlsplit(urljoin(site.get("start_url", ""), validated.url or ""))
            if address.scheme == "https" and address.netloc == "amazon.in":
                validated.url = address._replace(netloc="www.amazon.in").geturl()
        entry = urlsplit(site.get("start_url", ""))
        target = urlsplit(urljoin(site.get("start_url", ""), validated.url or ""))
        from jit_ui.runtime.browser_helpers import parameterize_search_navigation, public_amazon_path
        amazon_browse = task.site_id == "amazon_in" and public_amazon_path(target.path)
        from jit_ui.runtime.browser_helpers import permitted_url
        web_browse = task.site_id == "web" and permitted_url(target.geturl(), site)
        if not web_browse and (target.scheme != entry.scheme or target.netloc != entry.netloc or (not amazon_browse and (target.path != entry.path or target.query or target.fragment)) or validated.value_ref):
            message = ("Use https://www.amazon.in/ or a public /s, /b, /dp/ shopping URL; account and external URLs are outside this goal."
                       if task.site_id == "amazon_in" else "Cold and recovery navigation is limited to the registered entry page; submit dynamic searches through browser controls.")
            raise ExplorationError(message, "INVALID_MODEL_ACTION")
        if amazon_browse or web_browse:
            validated.value_ref = None
            validated = ActionSpec.model_validate(parameterize_search_navigation(validated.model_dump(mode="json"), task.parameters))
    validated.intent = validated.intent[:300] or validated.action
    if task.site_id == "admin" and validated.action == "click":
        element = next((e for e in observation.elements if e.index == validated.target_index), None)
        semantic = (validated.intent + " " + (element.name if element else "")).casefold()
        if "ship" in semantic or "dispatch" in semantic:
            validated.effect = "mutation"
    return validated


async def explore(
    session: BrowserSession,
    provider: VLMProvider,
    task: TaskRequest,
    on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
    cancelled: Callable[[], bool],
    max_calls: int = 20,
    max_actions: int = 30,
    initial_frames: list[ActionFrame] | None = None,
    max_site_recovery_calls: int = 3,
) -> list[ActionFrame]:
    frames: list[ActionFrame] = list(initial_frames or [])
    required_conditions = [condition.model_copy(deep=True) for condition in task.completion_conditions]
    from jit_ui.exploration.completion import final_submission_required

    def approved_submission():
        return not final_submission_required(task) or any(frame.action.requires_confirmation and not frame.error for frame in frames)
    attempts = 0
    rejected_completions = 0
    feedback = ""
    repeated_noops = 0
    rejected_proposals = 0
    malformed_responses = 0
    action_failures = 0
    site_recovery_calls = 0
    def missing_inputs() -> set[str]:
        if task.site_id == "web":
            # Literal choices can satisfy arbitrary sites. The compiler binds reusable
            # inputs and fixes unbound values so they cannot be replayed for new values.
            return set()
        recorded = {frame.action.value_ref for frame in frames}
        recorded.update(reference for frame in frames for reference in frame.action.url_refs.values())
        if task.site_id == "amazon_in":
            from urllib.parse import parse_qs
            from jit_ui.runtime.browser_helpers import normalize_query
            for frame in frames:
                submitted = parse_qs(urlsplit(frame.after_url).query).get("k", [""])[0]
                if normalize_query(submitted) == normalize_query(task.parameters.get("query", "")):
                    recorded.add("query")
            # Applied filters are independently verified; opaque facets can be cached as fixed values.
            return {"query"} - recorded
        return {name for name, value in task.parameters.items() if value is not None} - recorded

    for _ in range(max_actions):
        if cancelled():
            raise RunCancelled()
        passed, observed_goal = await session.verify_goal(task.goal_id, task.parameters)
        if attempts > 0 and passed and not missing_inputs() and approved_submission():
            return frames
        if attempts >= max_calls:
            raise ExplorationError("Cold-path model attempt budget is exhausted.", "MODEL_BUDGET_EXCEEDED")
        observation = await session.observe()
        if observation.page_issue:
            if site_recovery_calls >= max_site_recovery_calls:
                raise ExplorationError("The website is still temporarily unavailable after bounded model-directed recovery. Retry later; manual verification will not resolve a server outage.", "TEMPORARY_SITE_ERROR")
            site_recovery_calls += 1
            await on_event("site_recovery", {"message": "Temporary website error; the LLM is choosing a recovery action.",
                "attempt": site_recovery_calls, "max_attempts": max_site_recovery_calls, "issue": observation.page_issue})
        await on_event("screenshot", {"path": observation.screenshot_path, "observation_id": observation.observation_id, "url": observation.url, "element_count": len(observation.elements)})
        previous_attempts = int(getattr(provider, "attempts_total", 0))
        if hasattr(provider, "remaining_attempts"):
            provider.remaining_attempts = max_calls - attempts  # type: ignore[attr-defined]
        started = time.perf_counter()
        try:
            pending = sorted(missing_inputs())
            instruction = (
                "Record reusable input actions for every task parameter, including values already matching current defaults. "
                "Before completing, enter/select these remaining value_ref inputs: " + ", ".join(pending) + "."
                if pending else ""
            )
            entered = sorted({frame.action.value_ref for frame in frames
                              if frame.action.action in {"fill", "select", "adjust"} and frame.action.value_ref})
            instruction += " Already entered and verified input references: " + ", ".join(entered) + "."
            instruction += (" Independent goal verified: " + str(passed).lower()
                            + ". Observed applied state: " + json.dumps(observed_goal, ensure_ascii=False)[:1000]
                            + ". Typed inputs must also be submitted/applied through the UI. " + feedback)
            await on_event("provider_request", {"provider": task.provider, "model": getattr(provider, "model", task.provider),
                                                "max_attempts": max_calls - attempts, "decision_policy": "llm"})
            response = await provider.decide(observation, task, frames, instruction=instruction)
        except Exception as exc:
            count = max(0, int(getattr(provider, "attempts_total", previous_attempts)) - previous_attempts)
            if count:
                await on_event("provider_usage", {"attempts": count, "input_tokens": 0, "output_tokens": 0,
                    **getattr(exc, "usage", {}), "duration_ms": (time.perf_counter() - started) * 1000,
                    "usage_known": getattr(exc, "usage_known", count == 1 and bool(getattr(exc, "usage", {}))),
                    "model": getattr(provider, "model", task.provider), "failed": True})
            await on_event("provider_error", {"code": getattr(exc, "code", "PROVIDER_ERROR"),
                                              "validation_errors": getattr(exc, "validation_errors", [])})
            if getattr(exc, "code", "") == "INVALID_MODEL_RESPONSE":
                attempts += max(1, count)
                malformed_responses += 1
                if cancelled():
                    raise RunCancelled()
                if malformed_responses < 3 and attempts < max_calls:
                    issues = getattr(exc, "validation_errors", [])
                    feedback = ("Your response was rejected before execution. Select ONE candidate_id from the current menu with a short intent. "
                                "Include only fields required by that operation; never copy execution history fields. "
                                "Choose a fill candidate bound to the desired parameter, or a literal fill with value; click only clicks. "
                                "Validation issues: " + json.dumps(issues, separators=(",", ":"))[:400])
                    await on_event("response_retry", {"code": "INVALID_MODEL_RESPONSE", "attempt": malformed_responses,
                                                       "remaining_model_calls": max_calls - attempts})
                    continue
            raise
        attempts += response.attempts
        await on_event("provider_usage", response.model_dump(mode="json", exclude={"action"}))
        if cancelled():
            raise RunCancelled()
        if response.done:
            if task.site_id == "web":
                from jit_ui.exploration.completion import accept_completion_conditions
                try:
                    if not approved_submission():
                        raise ValueError("A booking cannot be reported complete before its final submission has been reviewed and approved.")
                    proposed = required_conditions + response.completion_conditions
                    if not proposed:
                        proposed = task.completion_conditions
                    unique = {condition.model_dump_json(): condition for condition in proposed}
                    accept_completion_conditions(task, session.site, list(unique.values()), observation)
                except ValueError as exc:
                    rejected_completions += 1
                    if rejected_completions >= 3 or attempts >= max_calls:
                        raise ExplorationError(str(exc), "COMPLETION_EVIDENCE_MISSING") from exc
                    feedback = str(exc) + " Return completion_conditions only after the requested outcome is visible."
                    await on_event("completion_rejected", {"error": str(exc), "goal_verified": False})
                    continue
            passed, observed_goal = await session.verify_goal(task.goal_id, task.parameters)
            pending = missing_inputs()
            if not passed or pending:
                rejected_completions += 1
                if attempts >= max_calls or rejected_completions >= 3:
                    if not passed:
                        raise ExplorationError("The model claimed completion, but the independent goal check failed.", "GOAL_NOT_VERIFIED")
                    raise ExplorationError("The trace has not recorded reusable inputs for: " + ", ".join(sorted(pending)) + ".", "PARAMETER_COVERAGE_MISSING")
                feedback = ("Your previous completion was rejected. Perform the remaining browser action to satisfy "
                            "the goal; do not repeat a completed mutation. Missing input references: "
                            + ", ".join(sorted(pending)) + ". Observed verification: "
                            + json.dumps(observed_goal, ensure_ascii=False, separators=(",", ":"))[:1100])
                await on_event("completion_rejected", {"goal_verified": passed, "missing_inputs": sorted(pending),
                                                       "observed_goal": observed_goal})
                continue
            return frames
        if response.action is None:
            raise ExplorationError("Provider returned neither an action nor completion.", "INVALID_MODEL_RESPONSE")
        try:
            action = validate_proposal(response.action, observation, task, session.site)
            target = next((element for element in observation.elements if element.index == action.target_index), None)
            input_focus = target and (target.goal_control == "query" or (task.site_id == "web" and target.tag in {"input", "textarea"} and target.role not in {"button", "checkbox", "radio"}))
            if action.action == "click" and input_focus:
                previous = frames[-1] if frames else None
                same_control = previous and previous.element and (previous.element.testid and previous.element.testid == target.testid
                    or previous.element.css and previous.element.css == target.css
                    or previous.element.name and previous.element.name == target.name and previous.element.role == target.role
                    or previous.element.goal_control == target.goal_control == "query")
                previous_focus = same_control and previous.action.action in {"click", "fill"} and not previous.error and previous.after_url == observation.url
                if previous_focus:
                    raise ExplorationError("This input is already focused or filled. Use fill to type, press to submit, or choose another required control.", "REDUNDANT_INPUT_FOCUS")
        except ExplorationError as exc:
            rejected_proposals += 1
            if rejected_proposals >= 3 or attempts >= max_calls:
                raise
            feedback = ("The proposed action was rejected BEFORE browser execution: " + str(exc)
                        + " Choose a current numbered element and provide its target_index. Do not invent controls.")
            await on_event("proposal_rejected", {"code": exc.code, "error": str(exc)})
            continue
        try:
            frame = await session.execute(action, task.parameters)
        except BrowserActionError as exc:
            if exc.frame:
                await on_event("action", {"frame": exc.frame.model_dump(mode="json"), "index": len(frames), "failed": True})
            if task.site_id == "amazon_in" and exc.code == "ACTION_OUTSIDE_GOAL":
                rejected_proposals += 1
                if rejected_proposals < 3 and attempts < max_calls:
                    feedback = ("The action was rejected before execution: " + str(exc)
                                + ". Use current search/filter/category/product controls or a public shopping URL, or dismiss an overlay.")
                    await on_event("proposal_rejected", {"code": exc.code, "error": str(exc)})
                    continue
            executed = exc.frame.action if exc.frame else action
            if not (action.requires_confirmation or action.effect == "mutation" or executed.requires_confirmation or executed.effect == "mutation"):
                action_failures += 1
                if action_failures < 3 and attempts < max_calls:
                    feedback = ("The selected operation could not be completed on the current page: " + str(exc)
                        + ". The page may have changed during inference. Observe the fresh controls and choose the next relevant operation; do not assume the old target still exists.")
                    await on_event("action_retry", {"error": str(exc), "attempt": action_failures,
                        "remaining_model_calls": max_calls - attempts})
                    continue
            raise
        action_failures = 0
        rejected_proposals = 0
        if (not observation.page_issue and frame.before_hash and frame.before_hash == frame.after_hash
                and any(prior.action.action == frame.action.action and prior.action.value_ref == frame.action.value_ref
                        and prior.action.locators == frame.action.locators for prior in frames)):
            repeated_noops += 1
            feedback = ("The last operation repeated a completed action and changed nothing. Select a different "
                        "required control or submit/apply the input. Completed reference: "
                        + str(frame.action.value_ref or frame.action.intent) + ".")
        else:
            repeated_noops = 0
        frames.append(frame)
        await on_event("action", {"frame": frame.model_dump(mode="json"), "index": len(frames) - 1})
        if repeated_noops >= 3:
            raise ExplorationError("The model repeated completed actions without changing browser state.", "EXPLORATION_STALLED")
        if frame.post_conditions and not await session.check_conditions(frame.post_conditions, task.parameters):
            raise ExplorationError("The observed action failed its post-condition.", "ACTION_VERIFICATION_FAILED")
    passed, _result = await session.verify_goal(task.goal_id, task.parameters)
    if passed and not missing_inputs() and approved_submission():
        return frames
    raise ExplorationError("Cold-path action budget is exhausted.", "ACTION_BUDGET_EXCEEDED")
