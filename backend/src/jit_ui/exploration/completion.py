"""Check model-proposed completion evidence before publishing a general workflow."""

import re


def is_playback_objective(objective):
    return bool(re.search(r"^(?:play|watch|listen|resume)\b|^(?:start|begin)\s+(?:the\s+)?(?:playback|playing|video|audio)", objective.strip(), re.I))


def final_submission_required(task):
    return task.site_id == "web" and bool(re.search(
        r"\b(?:book|reserve)\s+(?:me\s+)?(?:(?:a|an|the|some)\s+)?(?:tickets?|flights?|trains?|buses?|hotels?|rooms?|reservations?)\b",
        task.task, re.I))


def accept_completion_conditions(task, site, conditions, observation=None):
    if not 1 <= len(conditions) <= 8:
        raise ValueError("Provide one to eight concrete completion conditions for the requested outcome.")
    if task.objectives:
        covered = {condition.objective_index for condition in conditions if condition.objective_index is not None}
        if any(index >= len(task.objectives) for index in covered):
            raise ValueError("Completion evidence refers to an unknown objective.")
        missing = [f"{index}: {objective}" for index, objective in enumerate(task.objectives) if index not in covered]
        if missing:
            raise ValueError("Provide separate completion evidence using objective_index for: " + "; ".join(missing))
        for index, objective in enumerate(task.objectives):
            if not any(condition.objective_index == index and condition.kind != "visible" for condition in conditions):
                raise ValueError("Prove the resulting state or actual activation for objective " + str(index) + ": " + objective + "; seeing its control alone does not prove completion.")
            if is_playback_objective(objective) and not any(condition.objective_index == index and condition.kind == "media_playing" for condition in conditions):
                raise ValueError("Use media_playing to prove playback for objective " + str(index) + "; a title or visible player does not prove that media is playing.")
    if final_submission_required(task) and all(condition.kind == "visible" for condition in conditions):
        raise ValueError("Booking completion needs receipt/status evidence, not just a visible control.")
    for position, condition in enumerate(conditions):
        if condition.target_index is not None:
            element = next((element for element in observation.elements if element.index == condition.target_index), None) if observation else None
            if element is None:
                raise ValueError("Completion evidence refers to a control absent from the current page.")
            from jit_ui.browser.session import fingerprint_locators
            condition.locators = fingerprint_locators(element)
            condition.locator = condition.locators[0] if condition.locators else None
            condition.target_index = None
        if condition.kind == "goal":
            raise ValueError("Completion conditions must check page evidence, not refer to the goal.")
        if condition.value_ref and condition.value_ref not in task.parameters:
            raise ValueError("Completion evidence references an unknown parameter.")
        expected = task.parameters.get(condition.value_ref) if condition.value_ref else condition.expected
        if condition.kind not in {"visible", "media_playing"} and (expected is None or str(expected).strip() == ""):
            raise ValueError("Completion condition " + str(position) + " (" + condition.kind + ") needs expected or value_ref. "
                + ("Use value_ref='query' for the requested search; use value_equals for an input or search_submitted after opening a result." if "query" in task.parameters else "State the literal value that proves the requested outcome."))
        if condition.kind == "url_query_equals" and not condition.attribute:
            raise ValueError("url_query_equals needs attribute naming the current URL query key.")
        if condition.kind == "attribute_equals" and not condition.attribute:
            raise ValueError("attribute_equals needs attribute naming the actual DOM attribute.")
        if condition.kind == "url_contains" and str(expected).strip() in {"/", "http", "https", "https://", "http://"}:
            raise ValueError("A generic URL fragment does not prove task completion.")
        if condition.kind in {"visible", "text_contains", "text_equals", "value_equals", "attribute_equals", "number_at_most", "number_at_least"}:
            locators = condition.locators or ([condition.locator] if condition.locator else [])
            if not locators:
                raise ValueError("Provide a locator for the actual outcome control or result.")
            if condition.kind == "visible" and any(locator.strategy == "css" and locator.value.strip() in {"body", "html", "*", ":root"} for locator in locators):
                raise ValueError("The page being visible does not prove task completion.")
    if task.goal_id == "web_search":
        for bound in ("min_price", "max_price"):
            direction = "number_at_least" if bound == "min_price" else "number_at_most"
            kinds = {direction, "value_equals", "attribute_equals", "url_query_equals"}
            if task.parameters.get(bound) is not None and not any(condition.value_ref == bound and condition.kind in kinds for condition in conditions):
                raise ValueError("Provide actual filter/result evidence using value_ref=" + bound + "; keywords alone do not prove the requested budget.")
            if observation and task.parameters.get(bound) is not None:
                from jit_ui.browser.session import fingerprint_locators
                marker = "min" if bound == "min_price" else "max"
                controls = [element for element in observation.elements if (element.tag in {"input", "select"} or element.role == "slider")
                    and (element.parameter_hint == bound or any(option["value"].casefold() in {marker, marker + "imum"} for option in element.options)
                         or re.search(r"\b" + marker + r"(?:imum)?(?:\s+price|\s+amount|\s+budget)\b", element.name + " " + element.label, re.I))]
                for control in controls:
                    def locator_identity(locator):
                        return locator.strategy, locator.value, locator.name, locator.exact
                    control_locators = {locator_identity(locator) for locator in fingerprint_locators(control)}
                    if not any(condition.value_ref == bound and condition.kind in kinds
                        and any(locator_identity(locator) in control_locators for locator in condition.locators or ([condition.locator] if condition.locator else []))
                        for condition in conditions):
                        raise ValueError("The page has an applied " + bound + " control (target_index=" + str(control.index)
                            + "). Prove that control using " + direction + " and value_ref=" + bound
                            + "; one product price does not prove that its range filter satisfies the request.")
    task.completion_conditions = [condition.model_copy(deep=True) for condition in conditions]
    site["completion_conditions"] = [condition.model_dump(mode="json") for condition in conditions]
