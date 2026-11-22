"""Native web navigation needs visible goal evidence and preserves explicit URL scope."""
import os
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import async_playwright

from jit_ui.domain.models import TaskRequest
from jit_ui.exploration.web_capabilities import supports_web_capability, web_capability_action, web_capability_conditions
from jit_ui.runtime.browser_helpers import (
    SiteBlockedError, StepExecutionError, apply_action, check_conditions, detect_blocking,
    navigation_url, parameterize_search_navigation, permitted_url, verify_goal,
)


def web_site(task):
    parsed = urlparse(task.target_url)
    return {"site_id": "web", "goal_id": task.goal_id, "start_url": task.target_url,
            "allowed_origin": parsed.scheme + "://" + parsed.netloc,
            "completion_conditions": [condition.model_dump(mode="json") for condition in web_capability_conditions(task)]}


def test_public_web_scope_keeps_explicit_local_entry_but_rejects_unrequested_private_hosts():
    site = {"site_id": "web", "start_url": "https://example.com/", "allowed_origin": "https://example.com"}
    for address in ("https://example.org/article", "https://www.google.co.in/search?q=toys", "http://8.8.8.8/", "https://[2606:4700:4700::1111]/"):
        assert permitted_url(address, site), address
    for address in ("file:///C:/private.txt", "javascript:alert(1)", "http://localhost/", "http://127.0.0.1/",
                    "http://192.168.1.1/", "http://169.254.169.254/latest/", "http://[::1]/", "http://[fc00::1]/",
                    "http://127.1/", "http://0177.0.0.1/", "http://2130706433/", "http://0x7f000001/",
                    "https://user:password@example.com/", "https://@example.com/", "http://printer.local/"):
        assert not permitted_url(address, site), address
    local_site = {"site_id": "web", "start_url": "http://127.0.0.1:8080/app", "allowed_origin": "http://127.0.0.1:8080"}
    assert permitted_url("http://127.0.0.1:8080/another", local_site)
    assert not permitted_url("http://127.0.0.1:8000/api/private", local_site)


def test_native_web_actions_bind_query_and_language_for_replay():
    google = TaskRequest(site_id="web", goal_id="web_search", target_url="https://www.google.co.in/", task="Find toys",
                         parameters={"query": "girl toys for 5 years"})
    assert supports_web_capability(google)
    action = web_capability_action(google).model_dump(mode="json")
    assert action["url"] == "https://www.google.co.in/search" and action["url_refs"] == {"q": "query"}
    assert parse_qs(urlparse(navigation_url(action, {"query": "men formals"}, web_site(google))).query) == {"q": ["men formals"]}
    compiled = parameterize_search_navigation({"action": "goto", "url": "https://www.google.com/search?q=toys"}, {"query": "toys"})
    assert compiled["url"] == "https://www.google.com/search" and compiled["url_refs"] == {"q": "query"}
    locale = parameterize_search_navigation({"action": "goto", "url": "https://www.amazon.in/?language=hi_IN"}, {"language": "Hindi"})
    assert locale["url_refs"] == {"language": "language"}
    hindi = TaskRequest(site_id="web", goal_id="change_language", target_url="https://www.amazon.in/", task="Switch to Hindi",
                        parameters={"language": "hi"})
    assert supports_web_capability(hindi)
    assert "language=en_IN" in navigation_url(web_capability_action(hindi).model_dump(mode="json"), {"language": "English"}, web_site(hindi))
    assert not supports_web_capability(hindi.model_copy(update={"parameters": {"language": "unsupported"}}))


browser_test = pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1",
                                 reason="Enable JIT_RUN_BROWSER_TESTS=1 for routed web Chromium checks")


@browser_test
async def test_google_native_search_requires_matching_query_and_real_visible_result():
    task = TaskRequest(site_id="web", goal_id="web_search", target_url="https://www.google.com/", task="Find toys",
                       parameters={"query": "toys for girls"})
    site = web_site(task)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            async def response(route):
                await route.fulfill(content_type="text/html", body='''<!doctype html><html lang="en"><head><title>Toys - Google Search</title></head><body>
                    <div id="search"><a href="https://example.com/toys"><h3>Example toy results</h3></a></div></body></html>''')
            await context.route("https://www.google.com/**", response)
            page = await context.new_page()
            await apply_action(page, web_capability_action(task).model_dump(mode="json"), task.parameters, site)
            passed, evidence = await verify_goal(page, site, task.goal_id, task.parameters)
            assert passed and all(item["passed"] for item in evidence["conditions"])
            assert await check_conditions(page, [{"kind": "title_contains", "expected": "Google Search"}], {}, site)
            assert not await check_conditions(page, [{"kind": "url_query_equals", "attribute": "q", "expected": "different query"}], {}, site)
            await page.locator("#search a").evaluate("element => element.remove()")
            passed, evidence = await verify_goal(page, site, task.goal_id, task.parameters)
            assert not passed, "A search URL and empty container are not proof of search results."
            assert evidence["conditions"][0]["passed"] and not evidence["conditions"][1]["passed"]
        finally:
            await browser.close()


@browser_test
async def test_amazon_native_language_requires_actual_html_locale_and_replays_english():
    task = TaskRequest(site_id="web", goal_id="change_language", target_url="https://www.amazon.in/", task="Change to Hindi",
                       parameters={"language": "Hindi"})
    site = web_site(task)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            async def response(route):
                locale = parse_qs(urlparse(route.request.url).query).get("language", ["en_IN"])[0].replace("_", "-")
                await route.fulfill(content_type="text/html", body=f'<!doctype html><html lang="{locale}"><head><title>Amazon language</title></head><body><p>Shopping home</p></body></html>')
            await context.route("https://www.amazon.in/**", response)
            page = await context.new_page()
            action = web_capability_action(task).model_dump(mode="json")
            await apply_action(page, action, task.parameters, site)
            assert (await verify_goal(page, site, task.goal_id, task.parameters))[0]
            await page.locator("html").evaluate("element => element.lang='en-IN'")
            assert not (await verify_goal(page, site, task.goal_id, task.parameters))[0], "Locale URL alone must not prove language changed."
            await apply_action(page, action, {"language": "English"}, site)
            assert (await verify_goal(page, site, task.goal_id, {"language": "English"}))[0]
            assert parse_qs(urlparse(page.url).query)["language"] == ["en_IN"]
            assert await check_conditions(page, [{"kind": "url_query_equals", "value_ref": "language", "attribute": "language"}], {"language": "en"}, site)
            assert await check_conditions(page, [{"kind": "url_query_equals", "value_ref": "language"}], {"language": "English"}, site)
            assert not await check_conditions(page, [{"kind": "url_query_equals", "value_ref": "language"}], {"language": "Hindi"}, site)
            missing = {**site, "completion_conditions": []}
            assert not (await verify_goal(page, missing, "browser_task", {}))[0]
            recursive = {**site, "completion_conditions": [{"kind": "goal", "expected": "browser_task"}]}
            assert not (await verify_goal(page, recursive, "browser_task", {}))[0]
        finally:
            await browser.close()


@browser_test
async def test_web_auth_page_is_available_but_shopping_auth_and_captcha_stay_blocked():
    site = {"site_id": "web", "start_url": "https://www.amazon.in/", "allowed_origin": "https://www.amazon.in"}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            await context.route("https://www.amazon.in/**", lambda route: route.fulfill(content_type="text/html",
                body='<html><body><label>Email<input type="email"></label><button>Continue</button></body></html>'))
            page = await context.new_page()
            await apply_action(page, {"action": "goto", "url": "https://www.amazon.in/ap/signin"}, {}, site)
            await detect_blocking(page, site)
            with pytest.raises(SiteBlockedError):
                await detect_blocking(page, {"site_id": "amazon_in"})
            with pytest.raises(StepExecutionError):
                await apply_action(page, {"action": "goto", "url": "http://127.0.0.1:8000/private"}, {}, site)
            await page.locator("body").evaluate("element => element.insertAdjacentHTML('beforeend', '<a id=\"private-link\" href=\"http://127.0.0.1:8000/private\">Private</a>')")
            with pytest.raises(StepExecutionError):
                await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#private-link"}]}, {}, site)
            await page.locator("body").evaluate("element => element.innerText=\"Verify that you're not a robot\"")
            with pytest.raises(SiteBlockedError):
                await detect_blocking(page, site)
        finally:
            await browser.close()
