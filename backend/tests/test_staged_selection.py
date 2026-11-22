"""An operation is selected before its arguments; all requests keep measured usage."""
import json

import httpx
import pytest

from jit_ui.domain.models import ElementFingerprint, Observation, TaskRequest
from jit_ui.providers.base import ProviderError
from jit_ui.providers.ollama import OllamaProvider


@pytest.mark.parametrize("operation", ["click", "goto", "fill", "adjust", "done"])
async def test_ollama_separates_selection_from_only_the_required_data(operation):
    calls = []
    observation = Observation(url="https://example.com/", elements=[
        ElementFingerprint(index=1, tag="a", role="link", name="A category"),
        ElementFingerprint(index=2, tag="input", role="textbox", name="Search"),
        ElementFingerprint(index=3, tag="input", role="slider", name="Maximum price")])
    task = TaskRequest(site_id="web", task="Open the category and use the form")

    def transport(request):
        payload = json.loads(request.content)
        calls.append(payload)
        properties = payload["format"]["properties"]
        if "candidate_id" in properties:
            assert set(properties) == {"candidate_id", "intent", "requires_confirmation"}
            context = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])
            selected = next(candidate for candidate in context["menu"] if candidate["op"] == operation)
            response = {"candidate_id": next(choice for choice in properties["candidate_id"]["enum"] if choice.endswith("[" + str(selected["id"]) + "]")), "intent": "The model selects " + operation}
        elif operation == "goto":
            assert set(properties) == {"url"}
            response = {"url": "https://example.com/category"}
        elif operation in {"fill", "adjust"}:
            assert set(properties) == {"value"}
            response = {"value": "500" if operation == "adjust" else "Books"}
        else:
            assert set(properties) == {"completion_conditions"}
            response = {"completion_conditions": [{"kind": "url_contains", "expected": "category"}]}
        return httpx.Response(200, json={"message": {"content": json.dumps(response)}, "prompt_eval_count": 100, "eval_count": 20})

    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(transport)) as client:
        response = await OllamaProvider(client=client).decide(observation, task, [])
    expected = 1 if operation == "click" else 2
    assert len(calls) == response.attempts == expected
    assert response.input_tokens + response.output_tokens == expected * 120 and response.usage_known
    if operation == "click":
        assert response.action.action == "click" and response.action.target_index == 1 and response.action.url is None
    elif operation == "done":
        assert response.done and response.completion_conditions[0].expected == "category"
    else:
        assert response.action.action == operation


async def test_second_stage_cannot_change_the_selected_operation_and_keeps_both_usage_counts():
    replies = iter(['{"candidate_id":1001,"intent":"Navigate"}', '{"url":"https://example.com/","candidate_id":1002}'])
    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(lambda request: httpx.Response(200,
        json={"message": {"content": next(replies)}, "prompt_eval_count": 100, "eval_count": 20}))) as client:
        provider = OllamaProvider(client=client)
        with pytest.raises(ProviderError) as error:
            await provider.decide(Observation(url="https://example.com/"), TaskRequest(site_id="web", task="Navigate"), [])
        assert error.value.code == "INVALID_MODEL_RESPONSE" and provider.attempts_total == 2
        assert error.value.usage == {"input_tokens": 200, "output_tokens": 40} and error.value.usage_known


async def test_staged_operation_respects_the_remaining_global_request_budget():
    def transport(request):
        return httpx.Response(200, json={"message": {"content": '{"candidate_id":1001,"intent":"Navigate"}'}, "prompt_eval_count": 100, "eval_count": 20})
    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(transport)) as client:
        provider = OllamaProvider(client=client)
        provider.remaining_attempts = 1
        with pytest.raises(ProviderError) as error:
            await provider.decide(Observation(url="https://example.com/"), TaskRequest(site_id="web", task="Navigate"), [])
        assert error.value.code == "MODEL_BUDGET_EXCEEDED" and provider.attempts_total == 1
        assert error.value.usage == {"input_tokens": 100, "output_tokens": 20}
