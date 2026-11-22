from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from jit_ui.browser import BrowserActionError, BrowserSession
from jit_ui.config.settings import Settings
from jit_ui.domain.models import ActionFrame, ActionSpec, ElementFingerprint, LocatorSpec, Observation, ProviderResponse, TaskRequest
from jit_ui.exploration import ExplorationError, explore, validate_proposal
from jit_ui.providers.base import ProviderError
from jit_ui.providers.fixture import FixtureProvider
from jit_ui.providers.gemini import GeminiProvider, gemini_decision_schema
from jit_ui.providers.ollama import OllamaProvider
from jit_ui.providers.rate_limit import RequestPacer, retry_after_seconds
from jit_ui.tracing import redact_url


def test_concurrent_attempts_are_paced_at_twelve_requests_per_minute() -> None:
    now = 0.0
    attempts: list[float] = []

    async def advance(delay: float) -> None:
        nonlocal now
        now += delay
        await asyncio.sleep(0)

    async def scenario() -> None:
        pacer = RequestPacer(clock=lambda: now, sleep=advance)

        async def request() -> None:
            await pacer.acquire()
            attempts.append(now)

        await asyncio.gather(*(request() for _ in range(13)))

    asyncio.run(scenario())
    assert attempts == [float(second) for second in range(0, 65, 5)]


def test_retry_after_numeric_values_and_rpm_cap() -> None:
    assert retry_after_seconds("17") == 17
    assert retry_after_seconds("unknown") is None
    with pytest.raises(ValueError, match="between 1 and 12"):
        RequestPacer(13)


def test_gemini_honors_retry_after_and_counts_all_attempts() -> None:
    now = 0.0
    waits: list[float] = []
    call_count = 0

    class RateLimited(Exception):
        code = 429
        response = SimpleNamespace(headers={"Retry-After": "17"})

    async def sleep(delay: float) -> None:
        nonlocal now
        waits.append(delay)
        now += delay

    async def generate(**kwargs):
        nonlocal call_count
        call_count += 1
        assert kwargs["model"] == "gemini-3.5-flash-lite"
        if call_count == 1:
            raise RateLimited()
        return SimpleNamespace(text=json.dumps({"candidate_id": kwargs["config"].response_json_schema["properties"]["candidate_id"]["enum"][-1], "intent":"Complete"}), parsed=None, usage_metadata=SimpleNamespace(prompt_token_count=123, candidates_token_count=4))

    provider = GeminiProvider("", client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))), pacer=RequestPacer(clock=lambda: now, sleep=sleep), sleep=sleep)
    response = asyncio.run(provider.decide(Observation(url="http://local/shop"), TaskRequest(site_id="shop", goal_id="search_products", task="Search"), []))
    assert response.done and response.attempts == 2
    assert (response.input_tokens, response.output_tokens) == (123, 4)
    assert provider.attempts_total == 2 and call_count == 2
    assert waits == [17]


def test_gemini_omits_unsupported_schema_fields_and_preserves_local_validation():
    from jit_ui.providers.base import parse_decision

    schema = gemini_decision_schema()
    assert "additionalProperties" not in json.dumps(schema)
    assert "ActionSpec" in schema["$defs"]
    with pytest.raises(ProviderError) as error:
        parse_decision('{"done":true,"action":null,"unexpected":"data"}')
    assert error.value.code == "INVALID_MODEL_RESPONSE"


@pytest.mark.parametrize("details,code", [
    ({"reason": "API_KEY_INVALID"}, "INVALID_API_KEY"),
    ({"message": "Unknown name at response_schema"}, "PROVIDER_SCHEMA_REJECTED"),
    ({"message": "Invalid argument"}, "PROVIDER_BAD_REQUEST"),
])
def test_gemini_bad_requests_have_actionable_nonsecret_error_codes(details, code):
    class BadRequest(Exception):
        def __init__(self):
            self.code, self.details = 400, details

    async def generate(**kwargs):
        raise BadRequest()

    provider = GeminiProvider("", client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))))
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.decide(Observation(url="http://local/shop"), TaskRequest(site_id="shop", goal_id="search_products", task="Search"), []))
    assert error.value.code == code


def test_gemini_429_retries_are_bounded_and_long_retry_after_fails_without_retry() -> None:
    class RateLimited(Exception):
        code = 429
        response = SimpleNamespace(headers={"Retry-After": "180"})

    async def generate(**kwargs):
        raise RateLimited()

    async def forbidden_sleep(delay: float) -> None:
        raise AssertionError("A long server retry delay must fail explicitly")

    provider = GeminiProvider("", client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))), sleep=forbidden_sleep)
    with pytest.raises(ProviderError) as failure:
        asyncio.run(provider.decide(Observation(url="http://local/shop"), TaskRequest(site_id="shop", goal_id="search_products", task="Search"), []))
    assert failure.value.code == "RATE_LIMITED" and failure.value.retry_after == 180
    assert provider.attempts_total == 1


def test_gemini_temporary_overload_retries_and_accounts_for_all_requests():
    calls = 0
    now = 0.0

    class Overloaded(Exception):
        code = 503

    async def sleep(delay):
        nonlocal now
        now += delay

    async def generate(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Overloaded()
        return SimpleNamespace(text=json.dumps({"candidate_id": kwargs["config"].response_json_schema["properties"]["candidate_id"]["enum"][-1], "intent":"Complete"}), parsed=None, usage_metadata=None)

    provider = GeminiProvider("", client=SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate))), sleep=sleep,
        pacer=RequestPacer(clock=lambda: now, sleep=sleep))
    response = asyncio.run(provider.decide(Observation(url="http://local/shop"),
        TaskRequest(site_id="shop", goal_id="search_products", task="Search"), []))
    assert response.done and response.attempts == 2 and provider.attempts_total == 2
    assert now >= 5


def test_ollama_uses_explicit_model_structured_output_and_usage() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3-vl:2b-instruct"
        assert payload["options"]["num_ctx"] == 4096
        assert payload["options"]["num_predict"] == 192
        assert payload["stream"] is False
        assert payload["messages"][0]["images"] == ["aW1hZ2U="]
        return httpx.Response(200, json={"message": {"content": json.dumps({"candidate_id":payload["format"]["properties"]["candidate_id"]["enum"][-1],"intent":"Complete"})}, "prompt_eval_count": 91, "eval_count": 7})

    async def scenario() -> ProviderResponse:
        async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(transport)) as client:
            provider = OllamaProvider(client=client)
            return await provider.decide(Observation(url="http://local/shop", screenshot_base64="aW1hZ2U="), TaskRequest(site_id="shop", goal_id="search_products", task="Search", provider="ollama"), [])

    response = asyncio.run(scenario())
    assert response.done and (response.input_tokens, response.output_tokens) == (91, 7)


def test_ollama_missing_model_and_invalid_json_are_explicit_failures() -> None:
    async def scenario(status: int, data: dict) -> None:
        async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(lambda request: httpx.Response(status, json=data))) as client:
            await OllamaProvider(client=client).decide(Observation(url="http://local/shop"), TaskRequest(site_id="shop", goal_id="search_products", task="Search", provider="ollama"), [])

    with pytest.raises(ProviderError) as missing:
        asyncio.run(scenario(404, {"error": "model unavailable"}))
    assert missing.value.code == "MODEL_UNAVAILABLE"
    with pytest.raises(ProviderError) as invalid:
        asyncio.run(scenario(200, {"message": {"content": "click 99"}}))
    assert invalid.value.code == "INVALID_MODEL_RESPONSE"


def test_ollama_rejected_output_keeps_actual_token_usage_and_uses_flat_grammar():
    def transport(request):
        payload = json.loads(request.content)
        assert payload["format"]["type"] == "object"
        assert "candidate_id" in payload["format"]["properties"]
        assert "oneOf" not in payload["format"] and "anyOf" not in json.dumps(payload["format"])
        return httpx.Response(200, json={"message": {"content": '{"done":false,"action":null}'},
                                        "prompt_eval_count": 1200, "eval_count": 15})

    async def scenario():
        async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(transport)) as client:
            await OllamaProvider(client=client).decide(Observation(url="http://local/shop"),
                TaskRequest(site_id="shop", goal_id="search_products", task="Search", provider="ollama"), [])

    with pytest.raises(ProviderError) as error:
        asyncio.run(scenario())
    assert error.value.usage == {"input_tokens": 1200, "output_tokens": 15}


def test_ollama_flat_actions_convert_to_strict_shared_contract():
    from jit_ui.providers.ollama import parse_ollama_decision

    decision = parse_ollama_decision('{"action":"fill","intent":"Enter query","target_index":5,"value_ref":"product_name"}')
    assert decision.action.action == "fill" and decision.action.value_ref == "product_name"
    assert parse_ollama_decision('{"action":"done","intent":"Finished"}').done
    assert parse_ollama_decision('{"action":"done","intent":"Finished","target_index":5}').done
    with pytest.raises(ProviderError):
        parse_ollama_decision('{"action":"fill","target_index":5,"intent":"Enter query"}')
    with pytest.raises(ProviderError):
        parse_ollama_decision('{"action":"done","intent":"Finished","unexpected":5}')


def test_manual_amazon_prompt_excludes_contradictory_sentence_and_history_response_fields():
    from jit_ui.providers.base import decision_prompt
    task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Search for girl toys below 500",
        parameter_source="explicit", parameters={"query": "men formals", "min_price": 500, "max_price": 1000})
    history = [ActionFrame(action=ActionSpec(action="click", intent="focus"),
                           element=ElementFingerprint(index=2, name="Search Amazon.in"))]
    for compact in (True, False):
        prompt = decision_prompt(Observation(url="https://www.amazon.in/"), task, history, "", compact=compact)
        assert "girl toys" not in prompt and "men formals" in prompt
        assert '"target":' not in prompt and '"error":' not in prompt


def test_local_wire_grammar_is_flat_and_uses_only_current_targets_and_references():
    from jit_ui.providers.ollama import ollama_wire_schema
    task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Search toys", parameters={"query": "toys"})
    observation = Observation(url="https://www.amazon.in/", elements=[
        ElementFingerprint(index=2, tag="input", role="searchbox", goal_control="query"),
        ElementFingerprint(index=8, tag="button", role="button", goal_control="dismiss")])
    schema = ollama_wire_schema(task, observation, [], "")
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["target_index"]["enum"]) == {2, 8}
    assert schema["properties"]["value_ref"]["enum"] == ["query"]
    assert "oneOf" not in json.dumps(schema)


def test_local_grammar_requires_a_current_numbered_target():
    from jit_ui.providers.ollama import ollama_decision_schema

    task = TaskRequest(site_id="shop", goal_id="search_products", task="Search", parameters={"product_name": "shoes"})
    schema = ollama_decision_schema(task, Observation(url="http://local/shop", elements=[
        ElementFingerprint(index=5, tag="input"), ElementFingerprint(index=7, tag="button")]))
    branches = {branch["properties"]["action"]["const"]: branch for branch in schema["oneOf"]}
    assert branches["fill"]["properties"]["target_index"]["enum"] == [5]
    assert branches["click"]["properties"]["target_index"]["enum"] == [7]
    assert "target_index" in branches["fill"]["required"]
    assert branches["fill"]["anyOf"] == [{"required": ["value_ref"]}, {"required": ["value"]}]


def test_local_progress_guidance_leaves_scoped_repair_targets_available():
    from jit_ui.providers.ollama import ollama_decision_schema

    task = TaskRequest(site_id="shop", goal_id="search_products", task="Search",
                       parameters={"product_name": "shoes", "max_price": 5000})
    query = ElementFingerprint(index=5, tag="input", role="searchbox", name="Search products", testid="query")
    price = ElementFingerprint(index=7, tag="input", role="spinbutton", name="Max price", testid="price")
    observation = Observation(url="http://local/shop", elements=[query, price])
    history = [ActionFrame(action=ActionSpec(action="fill", value_ref="product_name"), element=query)]
    schema = ollama_decision_schema(task, observation, history)
    fill = next(branch for branch in schema["oneOf"] if branch["properties"]["action"]["const"] == "fill")
    assert fill["properties"]["target_index"]["enum"] == [7]
    assert fill["properties"]["value_ref"]["enum"] == ["max_price"]
    repair = ollama_decision_schema(task, observation, history, "Repair ONLY the failed logical step")
    fill = next(branch for branch in repair["oneOf"] if branch["properties"]["action"]["const"] == "fill")
    assert fill["properties"]["target_index"]["enum"] == [5, 7]
    assert "product_name" in fill["properties"]["value_ref"]["enum"]


def test_amazon_guidance_requires_query_binding_before_offering_submit():
    from jit_ui.providers.ollama import ollama_decision_schema

    task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Search shoes", parameters={"query": "shoes"})
    query = ElementFingerprint(index=1, tag="input", role="textbox", testid="query", goal_control="query")
    submit = ElementFingerprint(index=2, tag="input", role="button", goal_control="submit")
    observation = Observation(url="https://www.amazon.in/", elements=[query, submit])
    schema = ollama_decision_schema(task, observation)
    assert "click" not in [branch["properties"]["action"]["const"] for branch in schema["oneOf"]]
    frame = ActionFrame(action=ActionSpec(action="fill", value_ref="query"), element=query)
    schema = ollama_decision_schema(task, observation, [frame])
    click = next(branch for branch in schema["oneOf"] if branch["properties"]["action"]["const"] == "click")
    assert click["properties"]["target_index"]["enum"] == [2]


def test_local_screenshot_limits_image_dimensions_and_preserves_aspect_ratio():
    from PIL import Image
    from jit_ui.providers.ollama import local_screenshot

    original = BytesIO()
    Image.new("RGB", (1280, 800)).save(original, format="JPEG")
    reduced = local_screenshot(base64.b64encode(original.getvalue()).decode("ascii"), 960)
    with Image.open(BytesIO(base64.b64decode(reduced))) as image:
        assert image.size == (960, 600)


def test_exploration_stops_repeated_completed_inputs_without_state_changes():
    class Session:
        site = {}
        calls = 0

        async def verify_goal(self, goal, parameters):
            return False, {"query": ""}

        async def observe(self):
            return Observation(url="http://local/shop", elements=[ElementFingerprint(index=1, tag="input")])

        async def execute(self, action, parameters):
            self.calls += 1
            return ActionFrame(action=action, before_hash="empty" if self.calls == 1 else "filled", after_hash="filled")

        async def check_conditions(self, conditions, parameters):
            return True

    class Provider:
        async def decide(self, observation, task, history, instruction=""):
            return ProviderResponse(action=ActionSpec(action="fill", target_index=1, value_ref="product_name"))

    events = []

    async def event(kind, data):
        events.append(kind)

    with pytest.raises(ExplorationError) as error:
        asyncio.run(explore(Session(), Provider(), TaskRequest(site_id="shop", goal_id="search_products",
            task="Search shoes", parameters={"product_name": "shoes"}), event, lambda: False, max_calls=10))
    assert error.value.code == "EXPLORATION_STALLED"
    assert events.count("action") == 4


def test_gemini_rejected_output_keeps_actual_token_usage():
    async def generate(**kwargs):
        return SimpleNamespace(text='{"action":null,"done":false}', parsed=None,
                               usage_metadata=SimpleNamespace(prompt_token_count=1400, candidates_token_count=19))

    provider = GeminiProvider("", client=SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate))))
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.decide(Observation(url="http://local/shop"),
            TaskRequest(site_id="shop", goal_id="search_products", task="Search"), []))
    assert error.value.usage == {"input_tokens": 1400, "output_tokens": 19}


def test_rejected_completion_supplies_feedback_and_allows_a_verified_next_action():
    class Session:
        site = {}
        complete = False

        async def verify_goal(self, goal, parameters):
            return self.complete, {"query": "shoes" if self.complete else ""}

        async def observe(self):
            return Observation(url="http://local/shop")

        async def execute(self, action, parameters):
            self.complete = True
            return ActionFrame(action=action)

        async def check_conditions(self, conditions, parameters):
            return True

    class Provider:
        instructions = []

        async def decide(self, observation, task, history, instruction=""):
            self.instructions.append(instruction)
            if len(self.instructions) == 1:
                return ProviderResponse(done=True)
            return ProviderResponse(action=ActionSpec(action="wait", value="0"))

    events = []

    async def event(kind, data):
        events.append((kind, data))

    provider = Provider()
    frames = asyncio.run(explore(Session(), provider,
        TaskRequest(site_id="shop", goal_id="search_products", task="Search shoes"),
        event, lambda: False, max_calls=3))
    assert len(frames) == 1
    assert "completion was rejected" in provider.instructions[1]
    assert [kind for kind, _ in events].count("completion_rejected") == 1


def test_proposals_require_observed_targets_and_preserve_parameter_provenance() -> None:
    task = TaskRequest(site_id="shop", goal_id="search_products", task="Search headphones", parameters={"product_name": "headphones"})
    observation = Observation(url="http://local/shop", elements=[ElementFingerprint(index=2, role="searchbox", name="Search products")])
    action = validate_proposal(ActionSpec(action="fill", target_index=2, value="headphones"), observation, task, {})
    assert action.value is None and action.value_ref == "product_name"
    with pytest.raises(ExplorationError, match="absent"):
        validate_proposal(ActionSpec(action="click", target_index=99), observation, task, {})
    with pytest.raises(ExplorationError, match="unknown parameter"):
        validate_proposal(ActionSpec(action="fill", target_index=2, value_ref="invented"), observation, task, {})


def test_native_inputs_use_the_focus_handling_input_operation():
    observation = Observation(url="http://local/shop", elements=[ElementFingerprint(index=5, tag="input", role="searchbox")])
    task = TaskRequest(site_id="shop", goal_id="search_products", task="Search shoes", parameters={"product_name": "shoes"})
    with pytest.raises(ExplorationError, match="handle focus automatically"):
        validate_proposal(ActionSpec(action="click", target_index=5), observation, task, {})
    assert validate_proposal(ActionSpec(action="fill", target_index=5, value_ref="product_name"), observation, task, {}).action == "fill"


def test_proposal_correction_limit_resets_after_a_valid_operation():
    class Session:
        site = {}

        async def verify_goal(self, goal, parameters):
            return True, {}

        async def observe(self):
            return Observation(url="http://local/shop", elements=[ElementFingerprint(index=1, tag="input"),
                                                                    ElementFingerprint(index=2, tag="select")])

        async def execute(self, action, parameters):
            return ActionFrame(action=action)

        async def check_conditions(self, conditions, parameters):
            return True

    class Provider:
        index = 0
        decisions = [ActionSpec(action="click", target_index=1),
                     ActionSpec(action="fill", target_index=1, value_ref="product_name"),
                     ActionSpec(action="click", target_index=1),
                     ActionSpec(action="fill", target_index=1, value_ref="max_price"),
                     ActionSpec(action="click", target_index=2),
                     ActionSpec(action="select", target_index=2, value_ref="sort_by")]

        async def decide(self, observation, task, history, instruction=""):
            action = self.decisions[self.index]
            self.index += 1
            return ProviderResponse(action=action)

    async def event(kind, data):
        pass

    frames = asyncio.run(explore(Session(), Provider(), TaskRequest(site_id="shop", goal_id="search_products",
        task="Search shoes", parameters={"product_name": "shoes", "max_price": 5000, "sort_by": "rating_desc"}),
        event, lambda: False, max_calls=6))
    assert len(frames) == 3


def test_credentials_and_network_secrets_are_not_persisted() -> None:
    session = BrowserSession(Settings(), Path("unused-artifact-directory"), {"credentials": {"username": "private-user", "password": "private-password"}})
    action = session._safe_action(ActionSpec(action="fill", value="private-password"), ElementFingerprint(index=1, role="textbox", name="Password"))
    assert action.value_ref == "password" and action.value is None
    assert "private-password" not in action.model_dump_json()
    cleaned = redact_url("https://user:password@example.com/search?q=headphones&session_token=secret&username=private-user")
    assert "secret" not in cleaned and "private-user" not in cleaned
    assert "user:password" not in cleaned and "q=headphones" in cleaned


async def test_browser_shutdown_continues_when_a_component_does_not_respond(tmp_path):
    calls = []

    class Components:
        async def detach(self):
            calls.append("detach")
            await asyncio.Event().wait()

        async def close(self):
            calls.append("close")
            await asyncio.Event().wait()

        async def stop(self):
            calls.append("stop")

    session = BrowserSession(Settings(_env_file=None, browser_close_timeout_seconds=.01), tmp_path, {})
    session._cdp = session._browser = session._playwright = Components()
    session.page = object()
    await asyncio.wait_for(session.close(), timeout=1)
    assert calls == ["detach", "close", "stop"] and session.page is None


async def test_amazon_scope_rejection_recovers_without_compiling_failed_actions():
    class Session:
        site = {"site_id": "amazon_in"}
        submitted = False
        query = ""

        async def verify_goal(self, goal, parameters):
            return self.submitted and self.query == parameters["query"], {}

        async def observe(self):
            return Observation(url="https://www.amazon.in/", elements=[
                ElementFingerprint(index=1, tag="input", role="searchbox"),
                ElementFingerprint(index=2, tag="input", role="button"),
                ElementFingerprint(index=3, tag="a", role="button", name="Update location")])

        async def execute(self, action, parameters):
            if action.target_index == 3:
                failure = BrowserActionError("Outside product search", ActionFrame(action=action, error="Rejected"))
                failure.code = "ACTION_OUTSIDE_GOAL"
                raise failure
            if action.action == "fill":
                self.query = parameters[action.value_ref]
            elif action.action == "click":
                self.submitted = True
            return ActionFrame(action=action)

        async def check_conditions(self, conditions, parameters):
            return True

    class Provider:
        index = 0
        actions = [ActionSpec(action="click", target_index=3),
                   ActionSpec(action="fill", target_index=1, value_ref="query"),
                   ActionSpec(action="click", target_index=2)]

        async def decide(self, observation, task, history, instruction=""):
            action = self.actions[self.index]
            self.index += 1
            return ProviderResponse(action=action)

    events = []

    async def event(kind, data):
        events.append(kind)

    frames = await explore(Session(), Provider(), TaskRequest(site_id="amazon_in", goal_id="search_products",
        task="Search shoes", parameters={"query": "shoes"}), event, lambda: False, max_calls=3)
    assert len(frames) == 2 and not any(frame.error for frame in frames)
    assert events.count("proposal_rejected") == 1


def test_explorer_rejects_false_completion_and_checks_goal_after_last_allowed_action() -> None:
    task = TaskRequest(site_id="shop", goal_id="search_products", task="Search headphones")
    events: list[tuple[str, dict]] = []

    async def event(kind: str, data: dict) -> None:
        events.append((kind, data))

    class Session:
        site = {}
        complete = False

        async def verify_goal(self, goal_id, parameters):
            return self.complete, {}

        async def observe(self):
            return Observation(url="http://local/shop")

        async def execute(self, action, parameters):
            self.complete = True
            return ActionFrame(action=action)

        async def check_conditions(self, conditions, parameters):
            return True

    class LyingProvider:
        async def decide(self, observation, task, history, instruction=""):
            return ProviderResponse(done=True)

    class CompletingProvider:
        async def decide(self, observation, task, history, instruction=""):
            return ProviderResponse(action=ActionSpec(action="wait", value="0"))

    with pytest.raises(ExplorationError) as failure:
        asyncio.run(explore(Session(), LyingProvider(), task, event, lambda: False, max_calls=1, max_actions=1))
    assert failure.value.code == "GOAL_NOT_VERIFIED"
    frames = asyncio.run(explore(Session(), CompletingProvider(), task, event, lambda: False, max_calls=1, max_actions=1))
    assert len(frames) == 1


def test_explorer_records_parameter_inputs_even_when_default_goal_already_holds():
    task = TaskRequest(site_id="shop", goal_id="search_products", task="Find shoes", parameters={"product_name": "shoes", "max_price": 5000})

    class Session:
        site = {}

        async def verify_goal(self, goal_id, parameters):
            return True, {}

        async def observe(self):
            return Observation(url="http://local/shop", elements=[ElementFingerprint(index=1, tag="input", role="textbox")])

        async def execute(self, action, parameters):
            return ActionFrame(action=action)

        async def check_conditions(self, conditions, parameters):
            return True

    class RecordingProvider:
        def __init__(self):
            self.instructions = []

        async def decide(self, observation, task, history, instruction=""):
            self.instructions.append(instruction)
            reference = "product_name" if not history else "max_price"
            return ProviderResponse(action=ActionSpec(action="fill", target_index=1, value_ref=reference))

    async def event(kind, data):
        pass

    provider = RecordingProvider()
    frames = asyncio.run(explore(Session(), provider, task, event, lambda: False, max_actions=3))
    assert [frame.action.value_ref for frame in frames] == ["product_name", "max_price"]
    assert "max_price" in provider.instructions[0]

    class PrematureProvider:
        async def decide(self, observation, task, history, instruction=""):
            return ProviderResponse(done=True)

    with pytest.raises(ExplorationError) as failure:
        asyncio.run(explore(Session(), PrematureProvider(), task, event, lambda: False))
    assert failure.value.code == "PARAMETER_COVERAGE_MISSING"


def test_fixture_requires_explicit_local_simulation() -> None:
    with pytest.raises(ProviderError) as failure:
        asyncio.run(FixtureProvider().decide(Observation(url="https://www.amazon.in"), TaskRequest(site_id="amazon_in", goal_id="search_products", task="Search", provider="fixture"), []))
    assert failure.value.code == "FIXTURE_NOT_ALLOWED"


@pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable JIT_RUN_BROWSER_TESTS=1 after provisioning Playwright Chromium")
def test_real_browser_waits_for_hydration_records_provenance_and_uses_condition_fallback(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b'''<!doctype html><html><body>Loading<script>
              setTimeout(()=>document.body.innerHTML=`<input type="search" aria-label="Search products" data-testid="search-input">
              <label for="pw">Password</label><input id="pw" type="password" data-testid="password">
              <div data-testid="search-results" data-query="initial"></div>
              <aside class="lab-dock"><button data-testid="fixture-reset" onclick="window.fixtureReset=true">Reset fixtures</button></aside>`,250);
            </script></body></html>''')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    async def scenario() -> None:
        session = BrowserSession(Settings(), tmp_path, {"site_id": "shop", "start_url": origin + "/shop", "allowed_origin": origin, "credentials": {"password": "unit-test-secret"}})
        try:
            await session.open()
            observation = await session.observe()
            assert observation.screenshot_base64 and Path(observation.screenshot_path).exists()
            assert await session.page.locator("[data-jit-ui-overlay]").count() == 0
            assert not any(element.testid == "fixture-reset" for element in observation.elements)
            with pytest.raises(BrowserActionError, match="outside the registered browser goal"):
                await session.execute(ActionSpec(action="click", locators=[LocatorSpec(strategy="testid", value="fixture-reset")]), {})
            assert not await session.page.evaluate("Boolean(window.fixtureReset)")
            search = next(element for element in observation.elements if element.role == "searchbox")
            frame = await session.execute(ActionSpec(action="fill", target_index=search.index, value_ref="product_name"), {"product_name": "headphones"})
            assert frame.action.value_ref == "product_name" and frame.action.value is None
            await session.page.get_by_test_id("search-input").evaluate("element=>element.setAttribute('data-testid','renamed-search')")
            assert await session.check_conditions(frame.post_conditions, {"product_name": "headphones"})
            _, before = await session._state()
            await session.page.get_by_test_id("search-results").evaluate("element=>element.setAttribute('data-query','changed')")
            _, after = await session._state()
            assert before != after
            with pytest.raises(BrowserActionError, match="absent"):
                await session.execute(ActionSpec(action="click", target_index=999), {})
        finally:
            await session.close()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
