"""Price handles, parameter binding, and image geometry must agree end to end."""
import json
import os
import importlib.util

import pytest
from playwright.async_api import async_playwright

from jit_ui.browser.session import BrowserSession
from jit_ui.compiler.trace_compiler import compile_trace
from jit_ui.compiler import generate_python
from jit_ui.config import Settings
from jit_ui.domain.models import ActionSpec, ConditionSpec, ElementFingerprint, Observation, ParameterSpec, TaskRequest
from jit_ui.exploration.completion import accept_completion_conditions
from jit_ui.exploration.explorer import ExplorationError, validate_proposal
from jit_ui.providers.action_catalog import build_catalog, parse_selection
from jit_ui.runtime.browser_helpers import apply_action, check_conditions, numeric_condition_values, verify_goal


def price_task():
    return TaskRequest(site_id="web", goal_id="web_search", target_url="https://store.example/",
        task="Search men formal wear minimum 500 maximum 1000",
        parameters={"query": "men formal wear", "min_price": 500, "max_price": 1000})


def test_search_and_price_fields_offer_only_compatible_parameters():
    task = price_task()
    observation = Observation(url="https://store.example/", elements=[
        ElementFingerprint(index=1, tag="input", role="searchbox", name="Search Amazon", css="#twotabsearchtextbox"),
        ElementFingerprint(index=2, tag="input", role="spinbutton", name="Minimum price"),
        ElementFingerprint(index=3, tag="input", role="slider", parameter_hint="max_price", name="Maximum price")])
    catalog = build_catalog(observation, task)
    bound = [item for item in catalog if item.get("parameter")]
    assert {(item["target"], item["parameter"], item["op"]) for item in bound} == {
        (1, "query", "fill"), (2, "min_price", "fill"), (3, "max_price", "adjust")}
    candidate = next(item for item in bound if item["op"] == "adjust")
    result, _ = parse_selection(json.dumps({"candidate_id": candidate["id"], "intent": "Set maximum price"}), catalog, task)
    assert result.action.value_ref == "max_price"
    assert result.action.value is None
    assert any(item.get("input") == "literal value" for item in catalog)


@pytest.mark.parametrize("action", [ActionSpec(action="fill", target_index=1, value_ref="min_price"),
    ActionSpec(action="fill", target_index=1, value="500"),
    ActionSpec(action="fill", target_index=1, value="1000.0"),
    ActionSpec(action="adjust", target_index=1, value_ref="max_price")])
def test_search_field_rejects_budget_and_slider_actions(action):
    observation = Observation(url="https://store.example/", elements=[ElementFingerprint(index=1, tag="input", role="searchbox", name="Search")])
    with pytest.raises(ExplorationError):
        validate_proposal(action, observation, price_task(), {})


@pytest.fixture
async def price_browser(tmp_path):
    if os.environ.get("JIT_RUN_BROWSER_TESTS") != "1":
        pytest.skip("Enable routed browser tests")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.route("https://store.example/**", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
            await page.goto("https://store.example/")
            session = BrowserSession(Settings(_env_file=None), tmp_path, {"site_id": "web", "start_url": "https://store.example/"})
            session.page = page
            yield session
        finally:
            await browser.close()


async def test_overlapping_native_and_open_shadow_handles_have_distinct_marks(price_browser):
    session = price_browser
    await session.page.set_content('''<style>
      #price {position:relative;width:320px;height:40px;margin:100px 30px}
      #price input {position:absolute;left:0;top:0;width:320px;pointer-events:none}
      #price input::-webkit-slider-thumb {pointer-events:auto}
      a {display:inline-block;font-size:12px;margin:1px}
      </style>''' + ''.join(f'<a href="/product/{i}">Product {i}</a>' for i in range(85)) + '''
      <div id="price"><input id="min" type="range" aria-label="Minimum price" min="0" max="1000" value="100">
      <input id="max" type="range" aria-label="Maximum price" min="0" max="1000" value="900"></div>
      <div id="shadow"></div><script>
      const root=document.querySelector('#shadow').attachShadow({mode:'open'});
      root.innerHTML='<div><span id="inner" role="slider" aria-label="Minimum price" aria-valuenow="500" aria-valuemin="0" aria-valuemax="1000" tabindex="0" style="display:inline-block;width:20px;height:20px;background:blue"></span></div>';
      </script>''')
    observation = await session.observe()
    sliders = [el for el in observation.elements if el.role == "slider"]
    assert len(sliders) == 3
    assert len({el.index for el in sliders}) == 3
    assert len(observation.elements) == 80
    assert {el.parameter_hint for el in sliders} == {"min_price", "max_price"}
    assert all(el.bounds["width"] <= 24 for el in sliders)
    for el in sliders:
        assert await session.page.locator(el.css).count() == 1
        assert await session.page.locator(el.css).get_attribute("data-jit-ui-mark") == f"{observation.observation_id}:{el.index}"
    _, before = await session._state()
    await session.page.locator("#shadow #inner").evaluate("el=>el.setAttribute('aria-valuenow','600')")
    _, after = await session._state()
    assert before != after


async def test_capture_retries_when_control_moves_and_new_control_appears(price_browser):
    session = price_browser
    page = session.page
    await page.set_content('<button id="go" style="margin:30px">Go</button>')

    class ReflowPage:
        captures = 0

        def __getattr__(self, name):
            return getattr(page, name)

        async def screenshot(self, **kwargs):
            self.captures += 1
            if self.captures == 1:
                await page.evaluate("() => { document.querySelector('#go').style.marginLeft='200px'; document.body.insertAdjacentHTML('beforeend','<input id=min>'); }")
            return await page.screenshot(**kwargs)

    session.page = ReflowPage()
    observation = await session.observe()
    assert session.page.captures == 2
    assert len(observation.elements) == 2
    go = next(el for el in observation.elements if el.css == "#go")
    actual = await page.locator("#go").bounding_box()
    assert go.bounds["x"] == actual["x"]
    saved = json.loads((session.artifact_dir / "observation-001.json").read_text(encoding="utf-8"))
    assert "screenshot_base64" not in saved
    assert saved["elements"][go.index-1]["bounds"] == go.bounds


async def test_capture_omits_continuously_changing_controls_and_keeps_subscribe(price_browser):
    session = price_browser
    page = session.page
    await page.set_content('''<button id="subscribe">Subscribe</button>
        <input id="seek" type="range" min="0" max="10000" aria-label="Playback position" value="10">
        <button id="moving" style="position:absolute;top:100px;left:100px">Moving recommendation</button>''')

    class PlayingPage:
        captures = 0

        def __getattr__(self, name):
            return getattr(page, name)

        async def screenshot(self, **kwargs):
            self.captures += 1
            await page.evaluate("() => { document.querySelector('#seek').value=Number(document.querySelector('#seek').value)+10; const el=document.querySelector('#moving');el.style.left=(parseInt(el.style.left)+10)+'px'; }")
            return await page.screenshot(**kwargs)

    session.page = PlayingPage()
    observation = await session.observe()
    assert session.page.captures == 4
    assert observation.capture_note
    assert [element.css for element in observation.elements] == ["#subscribe"]
    assert await page.locator("#seek").get_attribute("data-jit-ui-mark") is None
    assert await page.locator("#moving").get_attribute("data-jit-ui-mark") is None
    target = observation.elements[0]
    await session.execute(ActionSpec(action="click", target_index=target.index), {})


async def test_duplicate_video_ids_have_unique_replay_locators(price_browser):
    session = price_browser
    await session.page.set_content('<div id="results"><div><a id="video-title" href="/one">One</a></div><div><a id="video-title" href="/two">Two</a></div></div>')
    observation = await session.observe()
    assert len(observation.elements) == 2
    for element in observation.elements:
        assert await session.page.locator(element.css).count() == 1
        assert await session.page.locator(element.css).inner_text() == element.name


async def install_mapped_sliders(page):
    await page.set_content('''<h2>Price</h2>
      <input id="min" type="range" aria-label="Minimum price" min="0" max="6" value="0">
      <input id="max" type="range" aria-label="Maximum price" min="0" max="6" value="6">
      <script>
      (() => { const prices=[0,100,350,500,750,1000,2000];
      function setup(e) {
        e.setAttribute('aria-valuetext','$'+prices[+e.value]+(e.id==='max' && +e.value===6 ? '+' : ''));
        e.addEventListener('input',()=>{const replacement=e.cloneNode();e.replaceWith(replacement);setup(replacement);replacement.focus();});
      }
      document.querySelectorAll('input').forEach(setup);
      })();
      </script>''')


async def test_displayed_price_adjustment_survives_replacement_and_compiles_replay(price_browser):
    session = price_browser
    await install_mapped_sliders(session.page)
    task = price_task()
    frames = []
    for reference in ("min_price", "max_price"):
        observation = await session.observe()
        target = next(el for el in observation.elements if el.parameter_hint == reference)
        action = validate_proposal(ActionSpec(action="adjust", target_index=target.index, value_ref=reference), observation, task, session.site)
        frame = await session.execute(action, task.parameters)
        assert await session.check_conditions(frame.post_conditions, task.parameters)
        frames.append(frame)
    assert await session.page.locator("#min").input_value() == "3"
    assert await session.page.locator("#max").input_value() == "5"
    specs = {name: ParameterSpec(type="string" if name == "query" else "number") for name in task.parameters}
    task.completion_conditions = [ConditionSpec(kind="number_at_least", locator={"strategy":"css", "value":"#min"}, value_ref="min_price"),
        ConditionSpec(kind="number_at_most", locator={"strategy":"css", "value":"#max"}, value_ref="max_price")]
    workflow = compile_trace(task, frames, specs, "test-prices")
    assert not any(c.kind == "value_equals" for s in workflow.steps for c in s.post_conditions)
    assert "min_price" not in workflow.metadata["fixed_parameters"]
    assert "max_price" not in workflow.metadata["fixed_parameters"]
    await install_mapped_sliders(session.page)
    changed = {**task.parameters, "min_price": 525, "max_price": 975}
    for step in workflow.steps:
        for action in step.actions:
            await apply_action(session.page, action.model_dump(mode="json"), changed, session.site)
        assert await check_conditions(session.page, step.post_conditions, changed, session.site)
    assert await session.page.locator("#min").input_value() == "4"
    assert await session.page.locator("#max").input_value() == "4"
    exported_file = session.artifact_dir / "exported_prices.py"
    exported_file.write_text(generate_python(workflow, session.site), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("exported_prices", exported_file)
    exported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exported)
    await install_mapped_sliders(session.page)
    result = await exported.run_workflow(session.page, changed)
    assert all(condition["passed"] for condition in result["conditions"])
    assert await session.page.locator("#min").input_value() == "4"
    assert await session.page.locator("#max").input_value() == "4"


async def test_unbounded_maximum_does_not_pass_numeric_evidence(price_browser):
    session = price_browser
    await install_mapped_sliders(session.page)
    locators = [{"strategy": "css", "value": "#max"}]
    assert await numeric_condition_values(session.page, locators) == [None]
    assert not await check_conditions(session.page, [{"kind": "number_at_most", "locators": locators, "value_ref": "max_price"}], price_task().parameters, session.site)


def test_completion_requires_slider_evidence_instead_of_a_product():
    observation = Observation(url="https://store.example/", elements=[
        ElementFingerprint(index=1, role="slider", parameter_hint="max_price", css="#max"),
        ElementFingerprint(index=2, role="link", name="$700", css="#product")])
    task = price_task()
    task.parameters.pop("min_price")
    with pytest.raises(ValueError, match="one product price"):
        accept_completion_conditions(task, {}, [ConditionSpec(kind="number_at_most", target_index=2, value_ref="max_price")], observation)


async def test_amazon_price_handles_require_submitted_filters(price_browser):
    session = price_browser
    await session.page.route("https://www.amazon.com/**", lambda route: route.fulfill(body='<input id="min" value="500"><input id="max" value="1000">', content_type="text/html"))
    site = {"site_id": "web", "start_url": "https://www.amazon.com/", "completion_conditions": [
        {"kind":"number_at_least", "locator":{"strategy":"css", "value":"#min"}, "value_ref":"min_price"},
        {"kind":"number_at_most", "locator":{"strategy":"css", "value":"#max"}, "value_ref":"max_price"}]}
    await session.page.goto("https://www.amazon.com/s?k=men+formal+wear")
    passed, evidence = await verify_goal(session.page, site, "web_search", price_task().parameters)
    assert not passed
    assert evidence["applied_filters"] == {}
    await session.page.goto("https://www.amazon.com/s?k=men+formal+wear&rh=p_36:50000-100000")
    passed, evidence = await verify_goal(session.page, site, "web_search", price_task().parameters)
    assert passed
    assert evidence["applied_filters"] == {"min_price": 500, "max_price": 1000}
