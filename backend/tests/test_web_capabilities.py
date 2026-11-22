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


@browser_test
async def test_inpage_actions_and_relative_navigation_permitted_on_web():
    site = {"site_id": "web", "start_url": "https://example.com/", "allowed_origin": "https://example.com"}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            html = """<!doctype html><html><body>
                <a id="hash-link" href="#section">Hash link</a>
                <a id="js-link" href="javascript:void(0)">JS link</a>
                <a id="empty-link" href="">Empty link</a>
                <a id="mailto-link" href="mailto:test@example.com">Mailto link</a>
                <a id="rel-link" href="/relative/path">Relative link</a>
                <a id="public-link" href="https://example.org/public">Public link</a>
                <a id="private-ip-link" href="http://192.168.1.1/admin">Private IP link</a>
                <form id="rel-form" action="/search">
                    <button id="form-btn" type="submit">Search</button>
                    <button id="formaction-btn" type="submit" formaction="/custom/search">Custom Search</button>
                    <button id="private-formaction-btn" type="submit" formaction="http://192.168.1.1/blocked">Private Submit</button>
                </form>
                <form id="no-action-form">
                    <button id="no-action-btn" type="submit">Submit</button>
                </form>
                <form id="detached-form" action="/detached-query"></form>
                <button id="detached-btn" form="detached-form" type="submit">Detached Button</button>
                <form id="named-action-form" action="/query">
                    <input name="action" value="shadowed">
                    <input id="input-enter" name="q" value="test">
                    <button id="named-action-btn" type="submit">Submit Query</button>
                </form>
                <div style="margin:20px 0">
                    <svg width="200" height="50"><a id="svg-link" href="/svg/target"><text x="10" y="25">SVG Link</text></a></svg>
                </div>
            </body></html>"""
            await context.route("**", lambda route: route.fulfill(content_type="text/html", body=html))
            page = await context.new_page()
            await apply_action(page, {"action": "goto", "url": "https://example.com/"}, {}, site)

            # In-page actions must never be blocked
            for link_id in ("#hash-link", "#js-link", "#empty-link", "#mailto-link"):
                await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": link_id}]}, {}, site)

            # Relative links, SVG links, and forms must resolve against page.url and succeed
            await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#rel-link"}]}, {}, site)
            await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#public-link"}]}, {}, site)
            await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#svg-link"}]}, {}, site)
            await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#form-btn"}]}, {}, site)
            await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#formaction-btn"}]}, {}, site)
            await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#detached-btn"}]}, {}, site)
            await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#no-action-btn"}]}, {}, site)
            await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#named-action-btn"}]}, {}, site)
            await apply_action(page, {"action": "press", "value": "Enter", "locators": [{"strategy": "css", "value": "#input-enter"}]}, {}, site)

            # Links and form actions to private destinations must still be blocked
            with pytest.raises(StepExecutionError, match="outside permitted public navigation"):
                await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#private-ip-link"}]}, {}, site)
            with pytest.raises(StepExecutionError, match="outside permitted public navigation"):
                await apply_action(page, {"action": "click", "locators": [{"strategy": "css", "value": "#private-formaction-btn"}]}, {}, site)
        finally:
            await browser.close()


@browser_test
async def test_detect_blocking_recognizes_various_robot_and_human_challenges():
    site = {"site_id": "web", "start_url": "https://example.com/", "allowed_origin": "https://example.com"}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            await context.route("**", lambda route: route.fulfill(content_type="text/html", body="<html><body><p>Clean page</p></body></html>"))
            page = await context.new_page()
            await apply_action(page, {"action": "goto", "url": "https://example.com/"}, {}, site)
            await detect_blocking(page, site)  # Should not raise

            # Text challenges
            for challenge_phrase in (
                "I am not robot",
                "I'm not a robot",
                "Please verify you are human",
                "Confirm that you are human",
                "Press and hold to show you're not a robot",
                "Unusual traffic from your computer network",
            ):
                await page.locator("body").evaluate("(element, phrase) => { element.innerText = phrase; }", challenge_phrase)
                with pytest.raises(SiteBlockedError):
                    await detect_blocking(page, site)

            # Title challenges
            await page.locator("body").evaluate("element => { element.innerText = 'Some neutral content'; }")
            for challenge_title in ("Just a moment...", "Attention Required! | Cloudflare", "Robot Check", "Human Verification", "Are you a robot", "Captcha"):
                await page.evaluate("(title) => { document.title = title; }", challenge_title)
                with pytest.raises(SiteBlockedError):
                    await detect_blocking(page, site)
        finally:
            await browser.close()


async def test_check_blocked_labels_robot_vs_general_manual_verification(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from jit_ui.browser import BrowserSession
    from jit_ui.config import Settings

    captured_reviews = []

    async def mock_manual_handler(review):
        captured_reviews.append(review)

    site = {"site_id": "web", "start_url": "https://example.com/", "allowed_origin": "https://example.com"}
    session = BrowserSession(Settings(testbed_url="http://127.0.0.1:3000", artifact_dir=tmp_path / "artifacts"), tmp_path, site)
    session.manual_handler = mock_manual_handler

    mock_page = MagicMock()
    mock_page.url = "https://example.com/challenge"
    mock_page.screenshot = AsyncMock()
    mock_page.locator.return_value = MagicMock()
    session.page = mock_page

    # 1. Robot challenge: initial notification must be labeled "I am not a robot verification"
    call_count = 0

    async def mock_detect_robot(page, s):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise SiteBlockedError("The website presented an 'I am not a robot' verification challenge.")

    monkeypatch.setattr("jit_ui.runtime.browser_helpers.detect_blocking", mock_detect_robot)
    await session._check_blocked()

    assert len(captured_reviews) == 2
    assert captured_reviews[0]["label"] == "I am not a robot verification"
    assert "I am not a robot" in captured_reviews[0]["details"]
    assert captured_reviews[1]["label"] == "Verification still pending"

    # 2. General manual verification: labeled "Manual verification required"
    captured_reviews.clear()
    call_count = 0

    async def mock_detect_general(page, s):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise SiteBlockedError("Fixture requires a manual verification step.")

    monkeypatch.setattr("jit_ui.runtime.browser_helpers.detect_blocking", mock_detect_general)
    await session._check_blocked()

    assert len(captured_reviews) == 1
    assert captured_reviews[0]["label"] == "Manual verification required"

