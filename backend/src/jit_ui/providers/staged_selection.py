"""Small models choose an operation before generating its unbound arguments."""
import json

from pydantic import BaseModel, ConfigDict, Field

from jit_ui.providers.action_catalog import selection_schema


class CandidateChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str | int
    intent: str = Field(min_length=1, max_length=160)
    requires_confirmation: bool = False


def choice_schema(task, catalog):
    schema = selection_schema(task, catalog)
    schema["properties"] = {key: schema["properties"][key] for key in ("candidate_id", "intent", "requires_confirmation")}
    schema["properties"]["candidate_id"] = {"type": "string", "enum": [candidate_label(item) for item in catalog]}
    return schema


def candidate_label(candidate):
    label = candidate["op"]
    if candidate.get("name"):
        label += " " + candidate["name"]
    if candidate.get("parameter"):
        label += " using " + candidate["parameter"]
    for key in ("option", "key", "amount"):
        if key in candidate:
            label += " = " + str(candidate[key])
    return label + " [" + str(candidate["id"]) + "]"


def argument_schema(candidate, task, catalog):
    fields = selection_schema(task, catalog, strict_completion=True)["properties"]
    if candidate["op"] == "goto":
        key = "url"
    elif candidate["op"] in {"fill", "select", "adjust"} and candidate.get("input") == "literal value":
        key = "value"
    elif candidate["op"] == "done" and task.site_id == "web" and not task.completion_conditions:
        key = "completion_conditions"
        if not task.parameters:
            fields[key]["items"]["properties"].pop("value_ref", None)
    else:
        return None
    return {"type": "object", "properties": {key: fields[key]}, "required": [key], "additionalProperties": False}


def argument_prompt(candidate, schema):
    key = schema["required"][0]
    instruction = {
        "url": "Provide the URL you intend to visit for this selected navigation operation.",
        "value": "Provide the literal text/value to enter into this selected control for the user's task.",
        "completion_conditions": "Provide concrete evidence for every objective, tagged with objective_index when objectives are present. Each condition needs a nonempty expected string. Also use value_ref for known task parameters. url_query_equals compares the current URL, not a field; value_equals compares an input. search_submitted with value_ref=query proves a recorded search after leaving results. media_playing checks native playback (expected=playing); a title alone does not prove playback. Do not claim success on an error page.",
    }[key]
    return ("The operation has already been selected. " + instruction
            + " Return only JSON containing " + key + ". Do not return candidate_id, intent, another operation, or unused fields.\n"
            + json.dumps({"selected_operation": candidate}, ensure_ascii=False, separators=(",", ":")))
