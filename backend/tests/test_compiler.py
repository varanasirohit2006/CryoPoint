import ast
import importlib.util
from urllib.parse import quote

import pytest

from jit_ui.compiler import compile_trace, generate_python
from jit_ui.compiler.locators import rank_locators
from jit_ui.compiler.pruning import prune_trace
from jit_ui.domain.models import ActionFrame, ActionSpec, ElementFingerprint, LocatorSpec, ParameterSpec, TaskRequest
from jit_ui.runtime.browser_helpers import apply_action, check_conditions, verify_goal


@pytest.fixture
def search_task():
    return TaskRequest(site_id="amazon_in", goal_id="search_products", task="Search for shoes", parameters={"query": "shoes"})


@pytest.fixture
def search_frames():
    return [
        ActionFrame(action=ActionSpec(action="goto", url="https://www.amazon.in/", intent="Open store")),
        ActionFrame(action=ActionSpec(action="fill", value="shoes", target_index=12, intent="Enter product query"),
                    element=ElementFingerprint(index=12, role="searchbox", name="Search", testid="search-input", css="#old-search")),
        ActionFrame(action=ActionSpec(action="click", intent="Submit search"),
                    element=ElementFingerprint(index=13, role="button", name="Search", testid="search-submit")),
    ]


def test_compiler_parameterizes_inputs_and_discards_observation_ids(search_task, search_frames):
    workflow = compile_trace(search_task, search_frames, {"query": ParameterSpec()}, "run-1")

    action = workflow.steps[1].actions[0]
    assert action.value_ref == "query"
    assert action.value is None
    assert action.target_index is None
    assert action.locators[0].strategy == "testid"
    assert workflow.steps[1].post_conditions[0].value_ref == "query"
    assert workflow.steps[1].post_conditions[0].locators == action.locators
    assert workflow.source_run_id == "run-1"
    assert search_frames[1].action.value == "shoes"


def test_locators_are_deduplicated_and_ranked_by_stability():
    element = ElementFingerprint(index=1, role="button", name="Search", testid="submit", label="Query", css=".hashed", xpath="//main/button")
    ranked = rank_locators([LocatorSpec(strategy="testid", value="submit")], element)

    assert [candidate.strategy for candidate in ranked] == ["testid", "role", "label", "xpath", "css"]
    assert [candidate.tier for candidate in ranked] == [1, 1, 2, 3, 4]


def cycle_frames(*, safe=True, mutation=False):
    return [
        ActionFrame(action=ActionSpec(action="goto", url="/wrong"), before_url="/home", before_hash="home",
                    after_url="/wrong", after_hash="wrong", safe_to_prune=safe),
        ActionFrame(action=ActionSpec(action="goto", url="/home", effect="mutation" if mutation else "read"),
                    before_url="/wrong", before_hash="wrong", after_url="/home", after_hash="home", safe_to_prune=safe),
    ]


def test_pruning_requires_explicit_safety_and_preserves_mutations():
    assert prune_trace(cycle_frames()) == []
    assert len(prune_trace(cycle_frames(safe=False))) == 2
    assert len(prune_trace(cycle_frames(mutation=True))) == 2


def test_compiler_rejects_ambiguous_parameters(search_task, search_frames):
    search_task.parameters["other"] = "shoes"
    with pytest.raises(ValueError, match="Ambiguous"):
        compile_trace(search_task, search_frames, {"query": ParameterSpec(), "other": ParameterSpec()}, "run")


def test_compiler_rejects_unresolved_errors_and_missing_locators(search_task):
    with pytest.raises(ValueError, match="unresolved"):
        compile_trace(search_task, [ActionFrame(action=ActionSpec(action="click"), error="not found")], {"query": ParameterSpec()}, "run")
    with pytest.raises(ValueError, match="locator"):
        compile_trace(search_task, [ActionFrame(action=ActionSpec(action="click"))], {"query": ParameterSpec()}, "run")


def test_credential_refs_remove_recorded_values(search_task):
    frames = [ActionFrame(action=ActionSpec(action="fill", value="do-not-export", value_ref="password"),
                          element=ElementFingerprint(index=1, label="Password"))]
    workflow = compile_trace(search_task, frames, {"query": ParameterSpec()}, "run")
    assert "do-not-export" not in workflow.model_dump_json()


def site_settings():
    return {"site_id": "amazon_in", "start_url": "https://www.amazon.in/", "allowed_origin": "https://www.amazon.in"}


def load_export(source, tmp_path):
    exported = tmp_path / "compiled.py"
    exported.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("compiled_fixture", exported)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_is_valid_standalone_code_and_omits_secrets(search_task, search_frames, tmp_path):
    workflow = compile_trace(search_task, search_frames, {"query": ParameterSpec()}, "run")
    workflow.metadata["untrusted_text"] = "'); __import__('os').system('no'); #\n"
    source = generate_python(workflow, {**site_settings(), "credentials": {"username": "private-user", "password": "private-secret"}})

    ast.parse(source)
    assert "private-user" not in source
    assert "private-secret" not in source
    exported = load_export(source, tmp_path)
    assert exported.SITE["credential_env"]["password"] == "JIT_FIXTURE_PASSWORD"
    assert exported.WORKFLOW["metadata"] == workflow.metadata
    assert exported.WORKFLOW["workflow_id"] == workflow.workflow_id


@pytest.mark.asyncio
async def test_export_opens_entry_page_for_automatically_started_cold_trace(search_task, search_frames, tmp_path):
    workflow = compile_trace(search_task, search_frames[1:], {"query": ParameterSpec()}, "run")
    site = {**site_settings(), "parameters": {"query": ParameterSpec()}}
    exported = load_export(generate_python(workflow, site), tmp_path)
    page = FakePage()
    result = await exported.run_workflow(page, {"query": "backpacks"})
    assert page.actions[0] == ("goto", "https://www.amazon.in/")
    assert result["query"] == "backpacks"


class FakeTarget:
    def __init__(self, page, name="search-input"):
        self.page, self.name = page, name

    @property
    def first(self):
        return self

    async def wait_for(self, **_kwargs):
        return None

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return True

    async def fill(self, value, **_kwargs):
        self.page.query = value
        self.page.actions.append(("fill", value))

    async def click(self, **_kwargs):
        self.page.actions.append(("click", self.name))
        self.page.url = "https://www.amazon.in/s?k=" + quote(self.page.query)

    async def input_value(self):
        return self.page.query

    async def inner_text(self, **_kwargs):
        return "Search results"

    async def evaluate(self, _javascript, *_args):
        if _args:
            return "Search"
        return {"tag": "input", "id": "twotabsearchtextbox", "type": "text", "name": "Search", "href": "", "inSearch": True,
                "isDismiss": False, "forbidden": False, "allowed": True, "observable": True,
                "purpose": "submit" if self.name in {"search-submit", "button"} else "query",
                "queryValue": self.page.query, "searchQuery": None}

    async def evaluate_all(self, _javascript):
        if self.name == "h1,h2":
            return ""
        return [{"name": self.page.query + " product", "url": "https://www.amazon.in/dp/1", "price": "₹499", "sponsored": False}]


class FakePage:
    def __init__(self):
        self.url, self.query, self.actions = "about:blank", "", []

    async def title(self):
        return "Amazon Search"

    async def goto(self, address, **_kwargs):
        self.url = address
        self.actions.append(("goto", address))

    def get_by_test_id(self, value):
        return FakeTarget(self, value)

    def get_by_role(self, role, **_kwargs):
        return FakeTarget(self, role)

    def locator(self, value):
        return FakeTarget(self, value)


@pytest.mark.asyncio
async def test_export_replays_changed_parameters_with_live_helper_parity(search_task, search_frames, tmp_path):
    workflow = compile_trace(search_task, search_frames, {"query": ParameterSpec()}, "run")
    exported = load_export(generate_python(workflow, site_settings()), tmp_path)
    live_page, exported_page = FakePage(), FakePage()
    parameters = {"query": "backpacks"}
    for step in workflow.steps:
        for action in step.actions:
            await apply_action(live_page, action.model_dump(), parameters, site_settings())
        assert await check_conditions(live_page, [condition.model_dump() for condition in step.post_conditions], parameters, site_settings())
    passed, live_result = await verify_goal(live_page, site_settings(), workflow.goal_id, parameters)
    export_result = await exported.run_workflow(exported_page, parameters)

    assert passed
    assert exported_page.actions == live_page.actions
    assert export_result == live_result
    assert export_result["query"] == "backpacks"
