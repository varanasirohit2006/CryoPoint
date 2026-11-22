"""Search data and later outcomes stay separate, with measured and cached decoding."""
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from jit_ui.browser import BrowserSession
from jit_ui.config import Settings
from jit_ui.domain.models import ActionSpec, ConditionSpec, ProviderResponse, TaskRequest
from jit_ui.domain.sites import validate_task
from jit_ui.domain.web_tasks import needs_model_interpretation
from jit_ui.exploration.completion import accept_completion_conditions
from jit_ui.providers.base import ProviderError
from jit_ui.providers.gemini import GeminiProvider
from jit_ui.providers.ollama import OllamaProvider
from jit_ui.providers.task_interpretation import TaskInterpretation, apply_interpretation
from jit_ui.runtime.service import RuntimeService


TEXT = "open youtube.com and search for gta 6 trailer . press subscribe the account"
RESULT = {"search_query":"gta 6 trailer", "objectives":["Search for gta 6 trailer", "Press Subscribe on the matching channel"]}


@pytest.mark.parametrize("suffix", [
    ". press subscribe the account",
    "and then subscribe to the channel",
    "; tap the subscribe button",
    "then follow the account",
    "\npress Subscribe",
    ".after this download the output file",
    "and convert the pdf to word",
    ". use the upload to upload file",
])
def test_followup_operations_are_not_search_keywords(suffix):
    task = validate_task(TaskRequest(site_id="web", task="open youtube.com and search for gta 6 trailer " + suffix), Settings(_env_file=None))
    assert task.goal_id == "browser_task"
    assert task.parameters == {"query":"gta 6 trailer"}
    assert needs_model_interpretation(task)
    assert task.task.endswith(suffix)


@pytest.mark.parametrize("query", ['"how to search and then press subscribe"', '"Subscribe . press play"', '"cats and dogs"'])
def test_quoted_action_words_remain_search_data(query):
    task = validate_task(TaskRequest(site_id="web", task="Open youtube.com and search for " + query), Settings(_env_file=None))
    assert task.goal_id == "web_search"
    assert task.parameters["query"] == query.strip('"')
    assert not needs_model_interpretation(task)


async def test_gemini_decoding_uses_requested_model_and_reports_usage():
    async def generate(**kwargs):
        assert kwargs["model"] == "gemini-3.5-flash-lite"
        assert "objectives" in kwargs["config"].response_json_schema["properties"]
        return SimpleNamespace(text=json.dumps(RESULT), usage_metadata=SimpleNamespace(prompt_token_count=120, candidates_token_count=40))
    provider = GeminiProvider("", client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))))
    interpreted, usage = await provider.interpret_task(TaskRequest(site_id="web", task=TEXT))
    assert interpreted.search_query == "gta 6 trailer"
    assert usage.input_tokens + usage.output_tokens == 160
    assert usage.attempts == provider.attempts_total == 1 and usage.usage_known


async def test_ollama_decoding_preserves_usage_and_explicit_search_values():
    def transport(request):
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3-vl:2b-instruct"
        assert "objectives" in payload["format"]["properties"]
        return httpx.Response(200, json={"message":{"content":json.dumps(RESULT)}, "prompt_eval_count":120, "eval_count":40})
    async with httpx.AsyncClient(base_url="http://local", transport=httpx.MockTransport(transport)) as client:
        provider = OllamaProvider(client=client)
        original = TaskRequest(site_id="web", task=TEXT, target_url="https://youtube.com/", parameter_source="explicit", parameters={"query":"official trailer"})
        interpreted, usage = await provider.interpret_task(original)
        applied = apply_interpretation(original, interpreted)
        assert applied.parameters["query"] == "official trailer"
        assert applied.task == TEXT and applied.objectives == RESULT["objectives"]
        assert usage.input_tokens + usage.output_tokens == 160 and usage.attempts == 1


def test_apply_interpretation_prunes_derived_query_when_search_query_is_null():
    text = (
        "in thehttps://www.ilovepdf.com/pdf_to_word search for i love pdf pdf to word converter "
        "and convert the pdf to word . use the upload to upload dummy_invoice pdf in the downloads dir "
        "and convert this pdf to  word .after this download the output word and save it in downloads.  please fix it ."
    )
    derived = validate_task(TaskRequest(site_id="web", task=text), Settings(_env_file=None))
    assert derived.parameters.get("query") == "i love pdf pdf to word converter"

    # LLM determines destination page is already the converter and needs no in-page search
    null_search_interpretation = TaskInterpretation(
        search_query=None,
        objectives=[
            "upload dummy_invoice pdf in downloads",
            "convert pdf to word",
            "download output word and save it in downloads",
        ]
    )
    applied = apply_interpretation(derived, null_search_interpretation)
    assert "query" not in applied.parameters
    assert applied.objectives == null_search_interpretation.objectives
    assert "<query>" not in applied.intent_key
    assert "i love pdf pdf to word converter" in applied.intent_key

    # Explicit parameters must not be pruned
    explicit = derived.model_copy(update={"parameter_source": "explicit", "parameters": {"query": "my_query"}})
    applied_explicit = apply_interpretation(explicit, null_search_interpretation)
    assert applied_explicit.parameters["query"] == "my_query"


def test_search_completion_cannot_skip_the_requested_subscription():
    task = apply_interpretation(TaskRequest(site_id="web", target_url="https://youtube.com/", task=TEXT), TaskInterpretation(**RESULT))
    search = ConditionSpec(kind="url_contains", expected="results", objective_index=0)
    with pytest.raises(ValueError, match="1: Press Subscribe"):
        accept_completion_conditions(task, {}, [search])
    visible = ConditionSpec(kind="visible", locator={"strategy":"css", "value":"#subscribe"}, objective_index=1)
    with pytest.raises(ValueError, match="seeing its control"):
        accept_completion_conditions(task, {}, [search, visible])
    pressed = ConditionSpec(kind="control_activated", expected="Subscribe", objective_index=1)
    accept_completion_conditions(task, {}, [search, pressed])


HTML = '''<html><body><form onsubmit="event.preventDefault();document.querySelector('#results').textContent=document.querySelector('#query').value">
<input id="query" type="search" aria-label="Search"><button id="search">Search</button></form>
<div id="results"></div><button id="subscribe" onclick="this.textContent='Subscribed'">Subscribe</button></body></html>'''


class Routed(BrowserSession):
    async def open(self):
        self.defer_navigation = True
        await super().open()
        await self._context.route("**/*", lambda route: route.fulfill(content_type="text/html", body=HTML))
        return self


@pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed browser tests")
async def test_compound_learning_counts_decoding_and_cached_replay_needs_no_model(tmp_path):
    class Provider:
        model = "mock-vision"
        attempts_total = 0
        remaining_attempts = None
        interpretations = 0

        async def interpret_task(self, task):
            self.attempts_total += 1
            self.interpretations += 1
            # Also check reuse when AI corrects the locally derived keyword phrase.
            return TaskInterpretation(search_query="Grand Theft Auto VI trailer", objectives=RESULT["objectives"]), ProviderResponse(model=self.model, input_tokens=100, output_tokens=20)

        async def decide(self, observation, task, history, instruction=""):
            self.attempts_total += 1
            successful = [frame for frame in history if frame.action.action != "goto" and not frame.error]
            operations = [("fill", "#query", "query"), ("click", "#search", None), ("click", "#subscribe", None)]
            if len(successful) < len(operations):
                operation, css, reference = operations[len(successful)]
                target = next(element for element in observation.elements if element.css == css)
                return ProviderResponse(model=self.model, input_tokens=100, output_tokens=20,
                    action=ActionSpec(action=operation, target_index=target.index, value_ref=reference))
            return ProviderResponse(done=True, model=self.model, input_tokens=100, output_tokens=20, completion_conditions=[
                ConditionSpec(kind="text_equals", locator={"strategy":"css", "value":"#results"}, value_ref="query", objective_index=0),
                ConditionSpec(kind="text_equals", locator={"strategy":"css", "value":"#subscribe"}, expected="Subscribed", objective_index=1)])

    provider = Provider()
    constructions = []
    def factory(*args):
        constructions.append(True)
        return provider
    runtime = RuntimeService(Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact"), session_factory=Routed, provider_factory=factory)
    original = TaskRequest(site_id="web", task=TEXT, target_url="https://video.example/", provider="gemini", mode="cold")
    try:
        learned = await runtime.execute(await runtime.submit(original))
        assert learned.status == "SUCCEEDED", learned.error
        assert learned.metrics.model_calls == provider.attempts_total == 5
        assert learned.metrics.input_tokens + learned.metrics.output_tokens == 600
        assert learned.request.parameters["query"] == "Grand Theft Auto VI trailer"
        replay = await runtime.execute(await runtime.submit(original.model_copy(update={"mode":"auto"})))
        assert replay.status == "SUCCEEDED" and replay.mode == "HOT", replay.error
        assert replay.metrics.model_calls == 0 and replay.metrics.estimated_tokens_saved == 600
        assert replay.request.parameters == learned.request.parameters
        assert provider.interpretations == 1 and len(constructions) == 1
    finally:
        runtime.repository.close()


@pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed browser tests")
async def test_invalid_intent_keeps_model_usage_and_executes_no_browser_actions(tmp_path):
    class Provider:
        model = "mock-vision"
        attempts_total = 0
        remaining_attempts = None

        async def interpret_task(self, task):
            self.attempts_total += 1
            error = ProviderError("Invalid interpretation", "INVALID_TASK_INTERPRETATION")
            error.usage = {"input_tokens":100, "output_tokens":20}
            error.usage_known = True
            raise error
    runtime = RuntimeService(Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact"), session_factory=Routed, provider_factory=lambda *args:Provider())
    try:
        run = await runtime.execute(await runtime.submit(TaskRequest(site_id="web", task=TEXT, provider="gemini", mode="cold")))
        assert run.error_code == "INVALID_TASK_INTERPRETATION"
        assert run.metrics.model_calls == 1 and run.metrics.input_tokens + run.metrics.output_tokens == 120
        assert run.metrics.token_usage_complete and run.metrics.actions == 0
    finally:
        runtime.repository.close()
