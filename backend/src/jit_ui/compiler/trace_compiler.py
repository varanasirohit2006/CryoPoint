"""Validated traces become data contracts, never model-generated Python."""

import re
from urllib.parse import parse_qs, urlparse, unquote_plus

from jit_ui.compiler.locators import rank_locators
from jit_ui.compiler.pruning import prune_trace
from jit_ui.domain.models import ActionFrame, ActionSpec, ConditionSpec, ParameterSpec, TaskRequest, WorkflowIR, WorkflowStep
from jit_ui.routing.parameters import validate_parameters
from jit_ui.routing.semantic import abstract_intent
from jit_ui.runtime.browser_helpers import parameterize_search_navigation


CREDENTIAL_REFS = {"username", "password"}


def parameterize_condition(condition, parameters):
    result = condition.model_copy(deep=True)
    if result.value_ref or result.expected_template is not None or not isinstance(result.expected, str):
        return result
    text = unquote_plus(result.expected) if result.kind == "url_contains" else result.expected
    url_prefix = ""
    if result.kind == "url_contains" and "?" in text:
        url_prefix, text = text.split("?", 1)
        url_prefix += "?"
    matches = [(name, str(value)) for name, value in parameters.items()
               if value is not None and str(value).strip() and isinstance(value, (str, int, float))
               and "{" not in name and "}" not in name]
    references = {value: name for name, value in matches if sum(other == value for _, other in matches) == 1}
    used = set()
    if references:
        pattern = r"(?<!\w)(?:" + "|".join(re.escape(value) for value in sorted(references, key=len, reverse=True)) + r")(?!\w)"
        def substitute(match):
            name = references[match.group()]
            used.add(name)
            return "{{" + name + "}}"
        text = re.sub(pattern, substitute, text)
    if used:
        result.expected, result.expected_template = None, url_prefix + text
    return result


def parameterize_action(
    action: ActionSpec, parameters: dict, specs: dict[str, ParameterSpec]
) -> ActionSpec:
    result = action.model_copy(deep=True)
    if result.action == "goto":
        return ActionSpec.model_validate(parameterize_search_navigation(result.model_dump(mode="json"), parameters))
    if result.value_ref:
        if result.value_ref not in specs and result.value_ref not in CREDENTIAL_REFS:
            raise ValueError(f"Unknown parameter reference: {result.value_ref}")
        result.value = None
        return result
    if result.action not in {"fill", "select", "adjust"} or result.value is None:
        return result
    matches = [
        name for name, value in parameters.items()
        if value is not None and str(value) == result.value and name in specs
    ]
    if len(matches) == 1:
        result.value_ref = matches[0]
        result.value = None
    elif len(matches) > 1:
        raise ValueError("Ambiguous input parameterization; record an explicit value_ref.")
    return result


def compile_trace(
    task: TaskRequest,
    frames: list[ActionFrame],
    parameter_specs: dict[str, ParameterSpec],
    source_run_id: str,
) -> WorkflowIR:
    parameters = validate_parameters(task.parameters, parameter_specs)
    optimized = prune_trace(frames)
    if not optimized:
        raise ValueError("A successful nonempty trace is required for compilation.")
    steps = []
    for index, frame in enumerate(optimized, start=1):
        if frame.error:
            raise ValueError("Cannot compile an action with an unresolved execution error.")
        source_action = frame.action
        if task.site_id == "amazon_in" and source_action.action == "click" and frame.element and frame.element.tag == "a" and frame.after_url != frame.before_url and "/s" in frame.after_url:
            source_action = ActionSpec(action="goto", intent=source_action.intent, url=frame.after_url)
        action = parameterize_action(source_action, parameters, parameter_specs)
        action.target_index = None
        action.locators = rank_locators(action.locators, frame.element)
        if action.action in {"click", "fill", "select", "adjust", "press"} and not action.locators:
            raise ValueError(f"Action {index} has no reusable locator.")
        conditions = []
        for condition in frame.post_conditions:
            condition = condition.model_copy(deep=True)
            if condition.value_ref and condition.value_ref not in parameter_specs and condition.value_ref not in CREDENTIAL_REFS:
                raise ValueError(f"Unknown condition parameter reference: {condition.value_ref}")
            if condition.kind == "value_equals" and action.value_ref:
                condition.value_ref = action.value_ref
                condition.expected = None
            elif condition.value_ref:
                condition.expected = None
            if condition.kind == "value_equals" and action.locators:
                condition.locators = [locator.model_copy(deep=True) for locator in action.locators]
            conditions.append(condition)
        if action.action in {"fill", "select"} and not any(c.kind == "value_equals" for c in conditions):
            conditions.append(ConditionSpec(
                kind="value_equals", locator=action.locators[0],
                locators=action.locators,
                expected=action.value, value_ref=action.value_ref,
            ))
        if action.action == "adjust" and not any(c.kind in {"number_at_least", "number_at_most"} for c in conditions):
            directions = ["number_at_least"] if action.value_ref == "min_price" else ["number_at_most"] if action.value_ref == "max_price" else ["number_at_least", "number_at_most"]
            conditions.extend(ConditionSpec(kind=kind, locator=action.locators[0], locators=action.locators, expected=action.value, value_ref=action.value_ref) for kind in directions)
        steps.append(WorkflowStep(
            step_id=f"step_{index}", intent=action.intent or action.action,
            actions=[action], post_conditions=conditions, effect=action.effect,
        ))
    goals = [parameterize_condition(condition, parameters) for condition in task.completion_conditions] if task.site_id == "web" else [ConditionSpec(kind="goal", expected=task.goal_id)]
    metadata = {}
    if task.site_id == "web":
        repeatable = {action.value_ref for step in steps for action in step.actions if action.value_ref and action.action in {"fill", "select", "adjust"}}
        repeatable.update(reference for step in steps for action in step.actions for reference in action.url_refs.values())
        metadata = {"target_url": task.target_url, "intent_key": task.intent_key,
                    "objectives": task.objectives,
                    "budget_evidence_policy": "visible-controls-v1",
                    "completion_conditions": [condition.model_dump(mode="json") for condition in goals],
                    "goal_parameters_bound": True, "learned_parameters": parameters,
                    "fixed_parameters": {key: value for key, value in parameters.items() if key not in repeatable}}
        for key, value in parameters.items():
            if value is None or not isinstance(value, (str, int, float)) or not str(value).strip():
                continue
            for condition in goals:
                literal = re.sub(r"\{\{[^{}]+\}\}", "", condition.expected_template or str(condition.expected or ""))
                if not condition.value_ref and re.search(r"(?<!\w)" + re.escape(str(value)) + r"(?!\w)", literal):
                    metadata["fixed_parameters"][key] = value
    if task.site_id == "amazon_in":
        from jit_ui.domain.intent import filter_shape
        repeatable = {a.value_ref for step in steps for a in step.actions if a.action in {"fill", "select", "adjust"}}
        repeatable.update(reference for step in steps for a in step.actions for reference in a.url_refs.values())
        metadata = {"amazon_filters": list(filter_shape(parameters)),
                    "fixed_filter_values": {key: parameters[key] for key in filter_shape(parameters) if key not in repeatable}}
        if any("/dp/" in frame.after_url or "/gp/product/" in frame.after_url for frame in frames):
            metadata["inspection_bound_query"] = parameters["query"]
        # Category, brand, age, and other static facets may be specific to the learned keywords.
        for frame in frames:
            address = parse_qs(urlparse(frame.after_url).query)
            facets = address.get("rh", [""])[0].split(",")
            if address.get("node") or any(facet and not facet.startswith(("p_36:", "p_72:")) for facet in facets):
                metadata["inspection_bound_query"] = parameters["query"]
    return WorkflowIR(
        site_id=task.site_id, goal_id=task.goal_id, intent_signature=abstract_intent(task),
        parameters=parameter_specs, steps=steps,
        goal_conditions=goals,
        source_run_id=source_run_id,
        compile_stats={"raw_actions": len(frames), "compiled_actions": len(optimized), "pruned_actions": len(frames) - len(optimized)},
        metadata=metadata,
    )
