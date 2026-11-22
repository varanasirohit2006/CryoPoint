"""Optional local Ollama vision adapter; never downloads or silently switches models."""

from __future__ import annotations

import time
import json
import base64
from io import BytesIO

import httpx

from jit_ui.domain.models import ActionFrame, Observation, ProviderResponse, TaskRequest
from jit_ui.providers.base import ProviderError, parse_decision
from jit_ui.providers.action_catalog import build_catalog, parse_selection, selection_prompt
from jit_ui.providers.staged_selection import CandidateChoice, argument_prompt, argument_schema, candidate_label, choice_schema


def local_screenshot(encoded: str, maximum_width: int) -> str:
    raw = base64.b64decode(encoded, validate=True)
    if not raw.startswith(b"\xff\xd8"):
        return encoded
    from PIL import Image

    with Image.open(BytesIO(raw)) as original:
        if original.width <= maximum_width:
            return encoded
        height = max(1, round(original.height * maximum_width / original.width))
        scaled = original.convert("RGB").resize((maximum_width, height), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        scaled.save(buffer, format="JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def ollama_decision_schema(task: TaskRequest, observation: Observation | None = None,
                           history: list[ActionFrame] | None = None, instruction: str = "") -> dict:
    """A flat wire format reduces nested-action confusion in small local models."""
    references = list(task.parameters)
    if task.site_id == "admin":
        references.extend(("username", "password"))
    available_actions = ["fill", "select", "click", "press", "scroll", "wait", "goto", "done"]
    candidates = observation.elements if observation else []
    inputs = [element for element in candidates if element.tag in {"input", "textarea"}
              and element.role not in {"button", "checkbox", "radio"}]
    selects = [element for element in candidates if element.tag == "select"]
    buttons = [element for element in candidates if element.role in {"button", "link", "checkbox", "radio", "option", "tab"} or element.tag == "button"]
    can_finish = True
    if candidates and task.site_id in {"shop", "admin", "amazon_in"} and "Repair ONLY" not in instruction:
        recent = history or []
        for index in range(len(recent) - 1, -1, -1):
            if recent[index].action.action == "goto":
                recent = recent[index + 1:]
                break

        def same_control(element, frame):
            if not frame.element:
                return False
            if element.testid and frame.element.testid:
                return element.testid == frame.element.testid
            return bool(element.name and element.name == frame.element.name and element.role == frame.element.role)

        completed_inputs = [frame for frame in recent if frame.action.action in {"fill", "select"}
                            and frame.action.value_ref and not frame.error]
        completed_buttons = [frame for frame in recent if frame.action.action in {"click", "press"}
                             and not frame.error and any(condition.kind in {"attribute_equals", "text_equals", "visible"}
                                                         for condition in frame.post_conditions)]
        filled = {frame.action.value_ref for frame in completed_inputs}
        pending = [reference for reference in references if reference not in filled]
        can_finish = not pending
        if pending:
            references = pending
        inputs = [element for element in candidates if element.tag in {"input", "textarea"}
                  and element.role not in {"button", "checkbox", "radio"}
                  and not any(same_control(element, frame) for frame in completed_inputs)]
        selects = [element for element in candidates if element.tag == "select"
                   and not any(same_control(element, frame) for frame in completed_inputs)]
        buttons = [element for element in candidates if element.role in {"button", "link", "checkbox", "radio", "option", "tab"} or element.tag == "button"]
        buttons = [element for element in buttons if not any(same_control(element, frame) for frame in completed_buttons)]
        ineffective = [frame for index, frame in enumerate(recent)
                       if frame.action.action == "click" and frame.before_hash
                       and frame.before_hash == frame.after_hash == observation.state_hash
                       and not any(later.action.action in {"fill", "select", "goto"} for later in recent[index + 1:])]
        buttons = [element for element in buttons if not any(same_control(element, frame) for frame in ineffective)]
        if task.site_id == "amazon_in" and "query" in pending:
            buttons = [element for element in buttons if element.goal_control != "submit"]
        available_actions = (["fill"] if inputs else []) + (["select"] if selects else []) + (["click"] if buttons else [])
        available_actions += ["press", "scroll", "wait", "goto", "done"]
        candidates = inputs + selects + buttons or candidates
    groups = {"fill": inputs, "select": selects, "click": buttons, "press": inputs + selects}
    branches = []
    for kind in available_actions:
        if kind == "done" and not can_finish:
            continue
        fields = {"action": {"const": kind}, "intent": {"type": "string", "maxLength": 120}}
        needed = ["action", "intent"]
        if kind in {"fill", "select", "click", "press"}:
            targets = groups[kind]
            if observation and observation.elements and not targets:
                continue
            fields["target_index"] = ({"type": "integer", "enum": [element.index for element in targets]}
                                      if targets else {"type": "integer", "minimum": 1})
            needed.append("target_index")
        if kind in {"fill", "select"}:
            if references:
                fields["value_ref"] = {"type": "string", "enum": references}
                fields["value"] = {"type": "string"}
            else:
                fields["value"] = {"type": "string"}
                needed.append("value")
        elif kind == "press":
            fields["value"] = {"enum": ["Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "Space"]}
            needed.append("value")
        elif kind in {"wait", "scroll"}:
            fields["value"] = {"type": "string"}
        elif kind == "goto":
            fields["url"] = {"type": "string"}
            needed.append("url")
        branch = {"type": "object", "properties": fields, "required": needed, "additionalProperties": False}
        if kind in {"fill", "select"} and references:
            branch["anyOf"] = [{"required": ["value_ref"]}, {"required": ["value"]}]
        branches.append(branch)
    return {"type": "object", "oneOf": branches}


def parse_ollama_decision(text: str):
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return parse_decision(text)
    if isinstance(payload, dict) and isinstance(payload.get("action"), str):
        completion_conditions = payload.pop("completion_conditions", [])
        if payload["action"] == "done":
            if set(payload) - {"action", "intent", "target_index", "value_ref"} or not isinstance(payload.get("intent"), str):
                raise ProviderError("Ollama returned an invalid completion object.", "INVALID_MODEL_RESPONSE")
            return parse_decision(json.dumps({"action": None, "done": True, "completion_conditions": completion_conditions}))
        if payload["action"] not in {"fill", "select"}:
            payload.pop("value_ref", None)
        return parse_decision(json.dumps({"action": payload, "done": False}))
    return parse_decision(text)


def ollama_wire_schema(task, observation, history, instruction):
    """Use a simple root grammar; conditional unions are unreliable in small local decoders.

    Action-specific target and parameter checks still run against the current DOM.
    """
    branches = ollama_decision_schema(task, observation, history, instruction)["oneOf"]
    fields = {"action": {"type": "string", "enum": [branch["properties"]["action"]["const"] for branch in branches]},
              "intent": {"type": "string", "maxLength": 120}}
    if task.site_id == "web":
        fields["requires_confirmation"] = {"type": "boolean"}
        locator = {"type": "object", "properties": {"strategy": {"type": "string", "enum": ["css", "role", "testid", "label", "text", "placeholder"]},
                                                    "value": {"type": "string"}, "name": {"type": "string"}},
                   "required": ["strategy", "value"], "additionalProperties": False}
        fields["completion_conditions"] = {"type": "array", "maxItems": 8, "items": {
            "type": "object", "properties": {"kind": {"type": "string", "enum": ["visible", "text_contains", "text_equals", "value_equals", "attribute_equals", "title_contains", "url_contains", "url_query_equals", "language_equals"]},
                                              "locator": locator, "expected": {"type": "string"}, "value_ref": {"type": "string"}, "attribute": {"type": "string"}},
            "required": ["kind"], "additionalProperties": False}}
    for name in ("target_index", "value_ref", "value", "url"):
        alternatives = [branch["properties"][name] for branch in branches if name in branch["properties"]]
        if not alternatives:
            continue
        if name in {"target_index", "value_ref"} and all("enum" in item for item in alternatives):
            fields[name] = {"type": "integer" if name == "target_index" else "string",
                            "enum": list(dict.fromkeys(value for item in alternatives for value in item["enum"]))}
        else:
            fields[name] = {"type": "integer" if name == "target_index" else "string"}
    return {"type": "object", "properties": fields, "required": ["action", "intent"], "additionalProperties": False}


class OllamaProvider:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen3-vl:2b-instruct", context: int = 4096, *, client: httpx.AsyncClient | None = None, max_output_tokens: int = 192, image_max_width: int = 960) -> None:
        self.model = model
        self.context = context
        self.max_output_tokens = max_output_tokens
        self.image_max_width = image_max_width
        self.client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=httpx.Timeout(180, connect=5), trust_env=False)
        self._owns_client = client is None
        self.attempts_total = 0
        self.remaining_attempts: int | None = None

    async def interpret_task(self, task):
        from jit_ui.providers.task_interpretation import TaskInterpretation, interpretation_prompt, parse_interpretation

        if self.remaining_attempts is not None and self.remaining_attempts < 1:
            raise ProviderError("Model attempt budget is exhausted.", "MODEL_BUDGET_EXCEEDED")
        started = time.perf_counter()
        self.attempts_total += 1
        data = None
        try:
            response = await self.client.post("/api/chat", json={"model": self.model, "stream": False,
                "messages": [{"role": "user", "content": interpretation_prompt(task)}],
                "format": TaskInterpretation.model_json_schema(),
                "options": {"num_ctx": self.context, "num_predict": 512, "temperature": 0}})
            if response.status_code == 404:
                raise ProviderError(f"Ollama model {self.model} is unavailable.", "MODEL_UNAVAILABLE")
            response.raise_for_status()
            data = response.json()
            interpreted = parse_interpretation(data.get("message", {}).get("content", ""))
            return interpreted, ProviderResponse(model=self.model, attempts=1,
                input_tokens=int(data.get("prompt_eval_count", 0)), output_tokens=int(data.get("eval_count", 0)),
                duration_ms=(time.perf_counter()-started)*1000,
                usage_known="prompt_eval_count" in data and "eval_count" in data)
        except (ProviderError, httpx.HTTPError, ValueError, TypeError) as exc:
            error = exc if isinstance(exc, ProviderError) else ProviderError("Ollama task interpretation failed.", "PROVIDER_UNAVAILABLE")
            error.usage = {"input_tokens": int((data or {}).get("prompt_eval_count", 0)), "output_tokens": int((data or {}).get("eval_count", 0))}
            error.usage_known = data is not None and "prompt_eval_count" in data and "eval_count" in data
            raise error

    async def decide(self, observation: Observation, task: TaskRequest, history: list[ActionFrame], instruction: str = "") -> ProviderResponse:
        catalog = build_catalog(observation, task, compact=task.goal_id != "browser_task")
        message: dict = {"role": "user", "content": selection_prompt(observation, task, history, instruction, catalog, compact=True, decision_only=True)}
        if observation.screenshot_base64:
            message["images"] = [local_screenshot(observation.screenshot_base64, self.image_max_width)]
        started = time.perf_counter()
        input_tokens = output_tokens = requests = completed = 0
        usage_known = True
        estimated_context = len(message["content"].encode("utf-8")) // 3 + (1600 if message.get("images") else 0) + 700
        context_size = max(self.context, 8192) if task.site_id == "web" and estimated_context > self.context else self.context

        async def infer(messages, schema, maximum):
            nonlocal input_tokens, output_tokens, requests, completed, usage_known
            if self.remaining_attempts is not None and requests >= self.remaining_attempts:
                raise ProviderError("The selected operation needs another model request, but the remaining attempt budget is exhausted.", "MODEL_BUDGET_EXCEEDED")
            self.attempts_total += 1
            requests += 1
            response = await self.client.post("/api/chat", json={
                "model": self.model, "messages": messages, "stream": False, "format": schema,
                "options": {"num_ctx": context_size, "num_predict": maximum, "temperature": 0},
            })
            if response.status_code == 404:
                raise ProviderError(f"Ollama model {self.model} is unavailable. Provision it explicitly before selecting Ollama.", "MODEL_UNAVAILABLE")
            response.raise_for_status()
            data = response.json()
            completed += 1
            input_tokens += int(data.get("prompt_eval_count", 0))
            output_tokens += int(data.get("eval_count", 0))
            usage_known = usage_known and "prompt_eval_count" in data and "eval_count" in data
            return data.get("message", {}).get("content", "")

        try:
            raw_choice = await infer([message], choice_schema(task, catalog), min(128, self.max_output_tokens) if task.site_id == "web" else self.max_output_tokens)
            try:
                choice = CandidateChoice.model_validate_json(raw_choice)
                candidate = next((item for item in catalog if item["id"] == choice.candidate_id or candidate_label(item) == choice.candidate_id), None)
                if candidate is None:
                    raise ValueError("Select a candidate_id from the current live menu.")
                arguments = argument_schema(candidate, task, catalog)
                combined = choice.model_dump()
                combined["candidate_id"] = candidate["id"]
                if arguments:
                    messages = [message, {"role": "assistant", "content": raw_choice},
                                {"role": "user", "content": argument_prompt(candidate, arguments)}]
                    raw_arguments = await infer(messages, arguments, max(512, self.max_output_tokens))
                    payload = json.loads(raw_arguments)
                    if not isinstance(payload, dict) or set(payload) != set(arguments["required"]):
                        raise ValueError("Return exactly the required argument fields for the selected operation.")
                    combined.update(payload)
                decision, candidate_id = parse_selection(json.dumps(combined), catalog, task)
            except (ValueError, TypeError) as exc:
                error = ProviderError("The model response did not match the selected operation; it was not executed.", "INVALID_MODEL_RESPONSE")
                error.validation_errors = ([{"type": item["type"], "loc": list(item["loc"])} for item in exc.errors(include_input=False)]
                    if hasattr(exc, "errors") else [{"type": "invalid_selection", "loc": ["candidate_id"], "message": str(exc)[:300]}])
                raise error from exc
            return ProviderResponse(action=decision.action, done=decision.done, candidate_id=candidate_id, completion_conditions=decision.completion_conditions, model=self.model,
                input_tokens=input_tokens, output_tokens=output_tokens, attempts=requests,
                duration_ms=(time.perf_counter() - started) * 1000,
                usage_known=usage_known)
        except ProviderError as exc:
            if completed:
                exc.usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
            exc.usage_known = usage_known and completed == requests
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            error = ProviderError("Ollama inference failed. Verify the local server, selected vision model, and available memory.", "PROVIDER_UNAVAILABLE")
            if completed:
                error.usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
            error.usage_known = False
            raise error from exc

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
