"""Blocked runs can request a visible retry and resume a manual step from the dashboard."""
import copy
import json
import os
import socket
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright


def _dashboard_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5173), timeout=0.2):
            return True
    except (OSError, TimeoutError):
        return False


@pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed dashboard browser tests")
@pytest.mark.parametrize("compile_workflow", [True, False])
async def test_retry_with_browser_preserves_task_and_provider_and_selects_the_new_run(tmp_path, compile_workflow):
    if not _dashboard_available():
        pytest.skip("Dashboard dev server is not running at http://127.0.0.1:5173/")
    request = {"site_id":"web", "goal_id":"web_search", "task":"Search Google for sample topic",
        "parameters":{"query":"sample topic"}, "provider":"ollama", "mode":"auto", "interactive":False,
        "target_url":"https://www.google.com/", "automation_id":"test-set", "compile_workflow":compile_workflow}
    blocked = {"run_id":"manual-blocked", "request":request, "automation_id":"test-set", "status":"BLOCKED",
        "mode":"COLD", "created_at":"2026-11-22T12:00:00Z", "error_code":"SITE_BLOCKED",
        "error":"Google requires an access verification challenge.",
        "metrics":{"duration_ms":1000, "model_calls":1, "input_tokens":100, "output_tokens":10,
                   "actions":1, "healed_steps":0, "simulated":False, "token_usage_complete":True}}
    runs, submitted, confirmations = [blocked], [], []

    async def respond(route):
        path = urlparse(route.request.url).path
        if path == "/api/health":
            data = {"worker_running":True, "providers":{
                "gemini":{"configured":True, "model":"gemini-test"},
                "ollama":{"configured":True, "available":True, "model":"ollama-test"},
                "fixture":{"configured":True, "simulated":True}}}
        elif path == "/api/sites":
            data = [{"site_id":"web", "label":"Any website", "start_url":"", "goals":[{"goal_id":"browser_task", "label":"Browser task", "parameters":{}}]}]
        elif path == "/api/runs" and route.request.method == "POST":
            payload = route.request.post_data_json
            submitted.append(payload)
            new = copy.deepcopy(blocked)
            new.update(run_id="manual-retry", request=payload, status="AWAITING_CONFIRMATION", error=None, error_code=None,
                pending_confirmation={"manual":True, "label":"Manual verification required", "details":"Complete verification in the automation browser."})
            runs.insert(0,new)
            data = new
        elif path.endswith("/confirmation"):
            confirmations.append(route.request.post_data_json)
            runs[0].update(status="RUNNING", pending_confirmation=None)
            data = runs[0]
        elif path == "/api/runs":
            data = runs
        elif path == "/api/tasks/preview":
            data = {**route.request.post_data_json, "parameters":{}, "target_url":"https://www.google.com/"}
        else:
            data = []
        await route.fulfill(content_type="application/json", body=json.dumps(data))

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width":1440,"height":1050})
            await page.route("**/api/**", respond)
            await page.route_web_socket("**/api/runs/**/events*", lambda socket:None)
            await page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
            await page.get_by_role("button", name="Retry with browser", exact=True).click()
            assert len(submitted) == 1
            assert submitted[0] == {**request,"interactive":True,"mode":"auto" if compile_workflow else "cold"}
            await page.get_by_role("button", name="Resume automation", exact=True).click()
            assert confirmations == [{"approve":True}]
            await page.get_by_test_id("run-status").filter(has_text="Running").wait_for()
            assert runs[0]["run_id"] == "manual-retry"
            toggle = page.get_by_role("checkbox", name="Show browser for manual steps", exact=True)
            await toggle.check()
            await page.wait_for_function("localStorage.getItem('jit-ui.manual-browser') === 'true'")
            await page.reload(wait_until="domcontentloaded")
            assert await page.get_by_role("checkbox", name="Show browser for manual steps", exact=True).is_checked()
            await page.screenshot(path=str(tmp_path/"manual-retry-dashboard.jpg"))
        finally:
            await browser.close()
