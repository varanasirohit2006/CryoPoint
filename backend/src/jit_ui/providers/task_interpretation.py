"""Decode compound objectives and search data without prescribing browser steps."""
import json
import hashlib

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jit_ui.providers.base import ProviderError


class TaskInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_query: str | None = Field(default=None, max_length=200)
    objectives: list[str] = Field(min_length=1, max_length=8)

    @field_validator("search_query")
    @classmethod
    def clean_query(cls, value):
        if value is not None:
            value = " ".join(value.split()).strip()
            if not value:
                raise ValueError("Search query cannot be empty.")
        return value

    @field_validator("objectives")
    @classmethod
    def clean_objectives(cls, values):
        if any(not value.strip() or len(value) > 250 for value in values):
            raise ValueError("Each objective needs a short nonempty description.")
        return [" ".join(value.split()) for value in values]


def interpretation_prompt(task):
    parameters = {key: value for key, value in task.parameters.items()
                  if not any(word in key.casefold() for word in ("password", "secret", "token", "username", "api_key"))}
    return (
        "Interpret the complete user browser request into requested outcomes and search data. "
        "Return only JSON with search_query (null when there is no search) and objectives. "
        "The query contains only what belongs in a search field, never later operations such as pressing Subscribe, "
        "converting, uploading, downloading, opening a result, saving, or sharing. Preserve quoted search terms, even when they contain action words. "
        "If the destination URL is already the dedicated tool or converter and no search is needed, set search_query to null. "
        "Omit conversational remarks such as 'please fix it' or 'thank you' from objectives. "
        "Include every requested outcome in objectives, including actions after searching. Describe what must be achieved; "
        "do not invent selectors, URLs, credentials, browser actions, or a fixed website procedure. "
        "Explicit parameter values are authoritative. Derived parameters are hints; correct them when they include instructions. "
        "Do not infer extra purchases or account changes.\n"
        + json.dumps({"task": task.task, "website": task.target_url, "parameters": parameters,
                      "parameter_source": task.parameter_source}, ensure_ascii=False, separators=(",", ":")))


def parse_interpretation(text):
    try:
        return TaskInterpretation.model_validate_json(text)
    except (ValueError, TypeError) as exc:
        raise ProviderError("The model could not interpret the task into valid search data and objectives.", "INVALID_TASK_INTERPRETATION") from exc


def interpretation_cache_key(task):
    data = {"task":task.task, "website":task.target_url, "parameters":task.parameters,
            "parameter_source":task.parameter_source,
            "conditions":[condition.model_dump(mode="json") for condition in task.completion_conditions]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def apply_interpretation(task, interpreted):
    from jit_ui.domain.web_tasks import web_intent_key

    parameters = dict(task.parameters)
    if interpreted.search_query is not None and (task.parameter_source != "explicit" or not parameters.get("query")):
        parameters["query"] = interpreted.search_query
    elif interpreted.search_query is None and task.parameter_source != "explicit":
        parameters.pop("query", None)
    result = task.model_copy(update={"parameters":parameters, "objectives":interpreted.objectives})
    return result.model_copy(update={"intent_key":web_intent_key(result)})
