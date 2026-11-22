"""Real DOM regressions for Amazon search scoping; all network responses are local fixtures."""

import html
import importlib.util
import os
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import async_playwright

from jit_ui.browser import BrowserActionError, BrowserSession
from jit_ui.compiler import compile_trace, generate_python
from jit_ui.config import Settings
from jit_ui.domain.models import ActionSpec, LocatorSpec, ParameterSpec, ProviderResponse, TaskRequest
from jit_ui.exploration import explore, validate_proposal
from jit_ui.runtime.browser_helpers import ActionOutsideGoalError, apply_action


SITE = {"site_id": "amazon_in", "goal_id": "search_products", "start_url": "https://www.amazon.in/",
        "allowed_origin": "https://www.amazon.in"}
pytestmark = pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1",
                               reason="Enable JIT_RUN_BROWSER_TESTS=1 for the routed Chromium fixtures")


def fixture_html(query=""):
    query = html.escape(query, quote=True)
    results = (f'<div data-component-type="s-search-result" data-asin="B000TEST"><h2><a href="/dp/test">Example {query}</a></h2>'
               '<span class="a-price"><span class="a-offscreen">₹499</span></span></div>') if query else ""
    popup = ('<aside role="dialog" id="notice" style="position:fixed;bottom:0;right:0;padding:10px;background:white">'
             '<button data-testid="notice-close" aria-label="Close dialog" onclick="this.closest(\'aside\').remove()">'
             '<span>×</span></button></aside>') if not query else ""
    return f'''<!doctype html><html><body>
        <a role="button" id="nav-global-location-popover-link" href="#" onclick="window.locationClicks=(window.locationClicks||0)+1;return false">Delivering to Coimbatore 641004 Update location</a>
        <a id="nav-link-accountList" href="/ap/signin">Sign in</a><a id="nav-cart" href="/gp/cart/view.html">Cart</a>
        <form id="nav-search-bar-form" action="/s">
          <select aria-label="Department"><option>All</option></select>
          <input id="twotabsearchtextbox" name="k" data-testid="query" aria-label="Search Amazon.in" type="text" value="{query}">
          <div id="nav-search-submit"><input id="nav-search-submit-button" data-testid="search-submit" aria-label="Go" type="submit" value="Go"></div>
        </form>
        <form action="/ap/signin"><button id="account-search">Search</button></form>
        <form id="nav-search" action="https://example.invalid/s"><button id="foreign-search">Search</button></form>
        <button id="purchase">Buy now</button>{results}{popup}
    </body></html>'''


async def routed_context(browser):
    context = await browser.new_context(viewport={"width": 1280, "height": 800})

    async def respond(route):
        query = parse_qs(urlparse(route.request.url).query).get("k", [""])[0]
        await route.fulfill(content_type="text/html; charset=utf-8", body=fixture_html(query))

    await context.route("https://www.amazon.in/**", respond)
    return context


async def test_continue_shopping_interstitial_is_numbered_and_optional(tmp_path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            await context.route("https://www.amazon.in/**", lambda route: route.fulfill(content_type="text/html",
                body='<p>Click the button below to continue shopping</p><button onclick="this.remove()">Continue shopping</button>'))
            page = await context.new_page()
            await page.goto(SITE["start_url"])
            session = BrowserSession(Settings(_env_file=None), tmp_path, SITE)
            session.page = page
            observation = await session.observe()
            assert len(observation.elements) == 1
            element = observation.elements[0]
            assert element.goal_control == "dismiss"
            frame = await session.execute(ActionSpec(action="click", target_index=element.index), {})
            assert frame.action.optional
            assert await page.locator("button").count() == 0
        finally:
            await browser.close()


async def test_temporary_amazon_outage_is_observed_for_model_recovery(tmp_path):
    from jit_ui.runtime.browser_helpers import detect_blocking, temporary_page_error
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            await context.route("https://www.amazon.in/**", lambda route: route.fulfill(content_type="text/html",
                body="<h1>Oops!</h1><p>It's rush hour and traffic is piling up on that page.</p>"))
            page = await context.new_page()
            await page.goto(SITE["start_url"])
            await detect_blocking(page)
            assert (await temporary_page_error(page))["code"] == "TEMPORARY_SITE_ERROR"
            session = BrowserSession(Settings(_env_file=None), tmp_path, SITE)
            session.page = page
            assert (await session.observe()).page_issue["code"] == "TEMPORARY_SITE_ERROR"
        finally:
            await browser.close()


async def test_search_observations_allow_departments_but_omit_location_and_accounts(tmp_path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await routed_context(browser)
            page = await context.new_page()
            await page.goto(SITE["start_url"])
            session = BrowserSession(Settings(_env_file=None), tmp_path, SITE)
            session.page = page
            observation = await session.observe()
            assert {element.testid for element in observation.elements if element.testid} == {"query", "search-submit", "notice-close"}
            assert {element.goal_control for element in observation.elements} == {"query", "submit", "dismiss", "filter"}
            with pytest.raises(ActionOutsideGoalError, match="before submitting"):
                await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#nav-search-submit-button"}]}, {"query": "shoes"}, SITE)
            assert page.url == SITE["start_url"]
            for selector in ("#nav-global-location-popover-link", "#nav-link-accountList", "#nav-cart", "#purchase", "#account-search", "#foreign-search"):
                with pytest.raises(ActionOutsideGoalError):
                    await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": selector}]}, {}, SITE)
            with pytest.raises(BrowserActionError) as blocked:
                await session.execute(ActionSpec(action="click", locators=[LocatorSpec(
                    strategy="css", value="#nav-global-location-popover-link")]), {})
            assert blocked.value.code == "ACTION_OUTSIDE_GOAL"
            assert await page.evaluate("window.locationClicks || 0") == 0
            # Clicking a child of a longer-labelled close button must also work.
            await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": '[data-testid="notice-close"] span'}]}, {}, SITE)
            assert await page.locator("#notice").count() == 0
            submit = next(element for element in observation.elements if element.testid == "search-submit")
            assert submit.tag == "input" and submit.role == "button"
            task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Search shoes", parameters={"query": "shoes"})
            assert validate_proposal(ActionSpec(action="click", target_index=submit.index), observation, task, SITE).action == "click"
        finally:
            await browser.close()


async def test_real_price_sort_controls_filter_evidence_and_variable_export(tmp_path):
    from jit_ui.domain.sites import site_definitions
    from jit_ui.runtime.browser_helpers import verify_goal

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()

            async def respond(route):
                params = parse_qs(urlparse(route.request.url).query)
                query = params.get("k", [""])[0]
                body = fixture_html(query).replace('</body>', '''
                  <aside id="s-refinements"><form action="/s">
                    <input type="hidden" name="k" value="''' + html.escape(query, quote=True) + '''">
                    <label>Minimum price<input id="low-price" name="low-price"></label>
                    <label>Maximum price<input id="high-price" name="high-price"></label>
                    <select id="s-result-sort-select" name="s"><option value="relevanceblender">Featured</option><option value="price-asc-rank">Price low to high</option></select>
                    <button data-testid="apply-filters">Apply</button>
                    <a data-testid="age-filter" href="/s?k=toys&rh=n%3A123">Age 5 years</a>
                  </form></aside>
                  <div data-component-type="s-search-result" data-asin="BEXPENSIVE"><h2>Expensive sponsored item</h2><span class="a-price"><span class="a-offscreen">₹1,999</span></span></div>
                </body>''')
                await route.fulfill(content_type="text/html; charset=utf-8", body=body)

            await context.route("https://www.amazon.in/**", respond)
            page = await context.new_page()
            await page.goto("https://www.amazon.in/s?k=toys")
            params = {"query": "toys", "max_price": 500, "sort_by": "price-asc-rank"}
            passed, evidence = await verify_goal(page, SITE, "search_products", params)
            assert not passed and "not been applied" in evidence["reason"]
            session = BrowserSession(Settings(_env_file=None), tmp_path, SITE)
            session.page = page
            observation = await session.observe()
            assert next(item for item in observation.elements if item.css == "#high-price").goal_control == "filter"
            assert next(item for item in observation.elements if item.css == "#s-result-sort-select").goal_control == "sort"
            for operation, selector, ref in [("fill", "#high-price", "max_price"), ("select", "#s-result-sort-select", "sort_by")]:
                await session.execute(ActionSpec(action=operation, value_ref=ref, locators=[LocatorSpec(strategy="css", value=selector)]), params)
            await session.execute(ActionSpec(action="click", locators=[LocatorSpec(strategy="testid", value="apply-filters")]), params)
            passed, result = await verify_goal(page, SITE, "search_products", params)
            assert passed and result["applied_filters"]["max_price"] == 500
            assert len(result["products"]) == 1 and result["excluded_outside_budget"] == 1
            # Public URL navigation is learned from exploration, then variables are bound for replay.
            frame = await session.execute(ActionSpec(action="goto", url=page.url), params)
            task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Find toys below 500 cheapest first", parameters=params)
            specs = site_definitions(Settings(_env_file=None))["amazon_in"]["parameters"]
            workflow = compile_trace(task, [frame], specs, "price-learn")
            assert workflow.steps[0].actions[0].url_refs == {"k": "query", "high-price": "max_price", "s": "sort_by"}
            exported = tmp_path / "budget_workflow.py"
            exported.write_text(generate_python(workflow, SITE), encoding="utf-8")
            spec = importlib.util.spec_from_file_location("amazon_budget_export", exported)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = await module.run_workflow(page, {"query": "dolls", "max_price": 700, "sort_by": "price-asc-rank"})
            assert result["query"] == "dolls" and result["applied_filters"]["max_price"] == 700
        finally:
            await browser.close()


async def test_rejected_choice_search_and_optional_overlay_export_replay(tmp_path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await routed_context(browser)
            page = await context.new_page()
            await page.goto(SITE["start_url"])
            session = BrowserSession(Settings(_env_file=None), tmp_path / "observations", SITE)
            session.artifact_dir.mkdir()
            session.page = page
            task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Search shoes", parameters={"query": "shoes"})
            events = []

            async def event(kind, data):
                events.append(kind)

            class Provider:
                calls = 0

                async def decide(self, observation, task, history, instruction=""):
                    self.calls += 1
                    if self.calls == 1:
                        return ProviderResponse(action=ActionSpec(action="click", target_index=999, intent="Update location"))
                    testid, operation = [("notice-close", "click"), ("query", "fill"), ("search-submit", "click")][self.calls - 2]
                    element = next(element for element in observation.elements if element.testid == testid)
                    return ProviderResponse(action=ActionSpec(action=operation, target_index=element.index,
                        intent=operation, value_ref="query" if operation == "fill" else None))

            frames = await explore(session, Provider(), task, event, lambda: False, max_calls=4)
            assert len(frames) == 3 and frames[0].action.optional
            assert events.count("proposal_rejected") == 1
            passed, result = await session.verify_goal(task.goal_id, task.parameters)
            assert passed and result["products"][0]["name"] == "Example shoes"
            workflow = compile_trace(task, frames, {"query": ParameterSpec()}, "unit-search")
            exported = tmp_path / "workflow.py"
            exported.write_text(generate_python(workflow, SITE), encoding="utf-8")
            spec = importlib.util.spec_from_file_location("amazon_fixture_export", exported)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            replay = await context.new_page()
            await replay.goto(SITE["start_url"])
            # Subsequent pages need no overlay; the compiled guard must be optional.
            await replay.locator("#notice").evaluate("element => element.remove()")
            result = await module.run_workflow(replay, {"query": "backpacks"})
            assert result["query"] == "backpacks" and result["products"][0]["name"] == "Example backpacks"
        finally:
            await browser.close()
