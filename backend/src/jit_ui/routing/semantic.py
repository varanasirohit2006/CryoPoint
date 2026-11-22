"""Semantic candidates are hints; active, compatible IR is the authority."""

from __future__ import annotations

import re
from typing import Callable

from jit_ui.domain.models import TaskRequest, WorkflowIR
from jit_ui.routing.index import IndexUnavailable, WorkflowIndex
from jit_ui.routing.parameters import validate_parameters


SCHEMA_VERSION = 1
RUNTIME_VERSION = 1


def abstract_intent(task: TaskRequest) -> str:
    if task.site_id == "web" and task.intent_key:
        return task.intent_key
    text = task.task
    values = sorted(task.parameters.items(), key=lambda item: len(str(item[1])), reverse=True)
    for name, value in values:
        if value is None or isinstance(value, bool) or not str(value).strip():
            continue
        text = re.sub(
            r"(?<!\w)" + re.escape(str(value)) + r"(?!\w)",
            lambda _match, parameter=name: f"<{parameter}>",
            text,
            flags=re.IGNORECASE,
        )
    return f"{task.goal_id}: {' '.join(text.lower().split())}"


class SemanticRouter:
    def __init__(
        self,
        index: WorkflowIndex,
        lookup: Callable[[str], WorkflowIR | None],
        *,
        threshold: float = 0.65,
        candidate_limit: int = 5,
    ) -> None:
        if not -1 <= threshold <= 1:
            raise ValueError("Cosine threshold must lie between -1 and 1.")
        self.index = index
        self.lookup = lookup
        self.threshold = threshold
        self.candidate_limit = candidate_limit
        self.last_index_error: str | None = None

    def route(self, task: TaskRequest) -> WorkflowIR | None:
        self.last_index_error = None
        if task.mode == "cold":
            return None
        try:
            matches = self.index.search(
                abstract_intent(task),
                {"site_id": task.site_id, "goal_id": task.goal_id},
                self.candidate_limit,
            )
        except IndexUnavailable as exc:
            self.last_index_error = str(exc)
            return None
        for match in matches:
            if match.similarity < self.threshold:
                continue
            workflow = self.lookup(match.workflow_id)
            if workflow is None:
                continue
            if workflow.site_id != task.site_id or workflow.goal_id != task.goal_id:
                continue
            if workflow.schema_version != SCHEMA_VERSION or workflow.runtime_version != RUNTIME_VERSION:
                continue
            try:
                validate_parameters(task.parameters, workflow.parameters)
            except ValueError:
                continue
            return workflow
        return None
