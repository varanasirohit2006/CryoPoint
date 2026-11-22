"""Malformed responses must recover without execution, losing token usage, or restarting the browser."""
import json

import httpx
import pytest

from jit_ui.browser import BrowserActionError
from jit_ui.domain.models import ActionFrame, ActionSpec, ElementFingerprint, Observation, ProviderResponse, TaskRequest
from jit_ui.exploration import explore
from jit_ui.providers.base import ProviderError
from jit_ui.providers.ollama import OllamaProvider


class Session:
    site = {"site_id": "amazon_in", "start_url": "https://www.amazon.in/"}

    def __init__(self):
        self.actions = []

    async def verify_goal(self, goal, parameters):
        return bool(self.actions), {}

    async def observe(self):
        return Observation(url=self.site["start_url"], elements=[
            ElementFingerprint(index=2, tag="input", role="searchbox", goal_control="query")])

    async def execute(self, action, parameters):
        self.actions.append(action)
        return ActionFrame(action=action, element=ElementFingerprint(index=2, tag="input", goal_control="query"),
                           before_url=self.site["start_url"], after_url=self.site["start_url"],
                           before_hash="changing-ad-1", after_hash="changing-ad-2")

    async def check_conditions(self, conditions, parameters):
        return True


async def scenario(responses, budget=5, session_override=None):
    events, requests = [], []

    def respond(request):
        requests.append(json.loads(request.content))
        content = responses[min(len(requests) - 1, len(responses) - 1)]
        return httpx.Response(200, json={"message": {"content": content}, "prompt_eval_count": 100, "eval_count": 20})

    async def emit(kind, data):
        events.append((kind, data))

    session = session_override or Session()
    task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Find formals",
                       provider="ollama", parameters={"query": "formals"})
    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(respond)) as client:
        provider = OllamaProvider(client=client)
        try:
            frames = await explore(session, provider, task, emit, lambda: False, max_calls=budget)
            return frames, events, requests
        except ProviderError as exc:
            exc.events, exc.requests, exc.session = events, requests, session
            raise


async def test_retry_malformed_response_with_usage_then_execute_valid_action():
    malformed = '{"action":"fill","intent":"fill","target":2,"error":null,"value_ref":"query"}'
    valid = '{"candidate_id":1001,"intent":"Enter keywords"}'
    frames, events, requests = await scenario([malformed, valid])
    assert len(frames) == 1 and frames[0].action.action == "fill"
    assert len(requests) == 2
    assert "Validation issues" in requests[1]["messages"][0]["content"]
    usage = [data for kind, data in events if kind == "provider_usage"]
    assert sum(item["input_tokens"] + item["output_tokens"] for item in usage) == 240
    assert sum(item.get("attempts", 1) for item in usage) == 2
    assert sum(kind == "response_retry" for kind, _ in events) == 1


async def test_invalid_click_typing_is_rejected_until_the_model_chooses_fill():
    frames, _, requests = await scenario(['{"candidate_id":1003,"intent":"search","value":"formals"}',
                                         '{"candidate_id":1001,"intent":"type"}'])
    assert len(requests) == 2
    assert frames[0].action.action == "fill" and frames[0].action.value_ref == "query"


async def test_redundant_focus_is_rejected_despite_unrelated_dom_changes():
    focus = '{"candidate_id":1003,"intent":"focus"}'
    fill = '{"candidate_id":1001,"intent":"type"}'
    frames, events, requests = await scenario([focus, focus, fill])
    assert len(requests) == 3 and [frame.action.action for frame in frames] == ["click", "fill"]
    rejection = next(data for kind, data in events if kind == "proposal_rejected")
    assert rejection["code"] == "REDUNDANT_INPUT_FOCUS"


@pytest.mark.parametrize("budget,expected_calls", [(5, 3), (2, 2)])
async def test_repeated_invalid_responses_stop_at_recovery_or_global_budget(budget, expected_calls):
    with pytest.raises(ProviderError) as error:
        await scenario(['{"action":"fill","intent":"bad","target":2}'], budget)
    assert len(error.value.requests) == expected_calls
    assert error.value.session.actions == []
    assert sum(data["input_tokens"] for kind, data in error.value.events if kind == "provider_usage") == expected_calls * 100


async def test_read_action_failure_reobserves_and_lets_the_model_choose_the_new_control():
    class Changing(Session):
        attempts = 0
        targets = []

        async def observe(self):
            return Observation(url=self.site["start_url"], elements=[
                ElementFingerprint(index=2 if not self.attempts else 7, tag="input", role="searchbox", goal_control="query")])

        async def execute(self, action, parameters):
            self.attempts += 1
            self.targets.append(action.target_index)
            if self.attempts == 1:
                raise BrowserActionError("The old control was replaced", ActionFrame(action=action, error="Target detached"))
            return await super().execute(action, parameters)

    session = Changing()
    frames, events, requests = await scenario(['{"candidate_id":1001,"intent":"Enter query"}'], session_override=session)
    assert session.targets == [2, 7] and len(requests) == 2
    assert frames[0].action.target_index == 7
    assert any(kind == "action_retry" for kind, _ in events)
    assert "fresh controls" in requests[-1]["messages"][0]["content"]


async def test_failed_mutation_is_not_automatically_retried():
    class MutationSession(Session):
        attempts = 0

        async def observe(self):
            return Observation(url=self.site["start_url"], elements=[ElementFingerprint(index=2, tag="button", role="button")])

        async def execute(self, action, parameters):
            self.attempts += 1
            raise BrowserActionError("Unknown submission outcome", ActionFrame(action=action, error="Disconnected"))

    class Provider:
        async def decide(self, *args, **kwargs):
            return ProviderResponse(action=ActionSpec(action="click", target_index=2, effect="mutation", requires_confirmation=True))

    session = MutationSession()
    async def emit(*args):
        pass
    with pytest.raises(BrowserActionError):
        await explore(session, Provider(), TaskRequest(site_id="web", task="Submit an action"), emit, lambda: False)
    assert session.attempts == 1
