"""Read-only browser check for connected providers and compiled artifacts."""

import asyncio
import os
import re
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    root = Path(__file__).resolve().parents[1]
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(root / ".data" / "browsers"))
    output = root / ".data" / "diagnostics" / "dashboard"
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto("http://127.0.0.1:5173", wait_until="networkidle")
            await page.get_by_text("Runtime connected", exact=False).wait_for()
            provider = page.get_by_test_id("provider-select")
            await provider.select_option("ollama")
            await page.locator(".provider-note").get_by_text("qwen3-vl:2b-instruct", exact=True).wait_for()
            await provider.select_option("gemini")
            await page.locator(".provider-note").get_by_text("gemini-3.5-flash-lite", exact=True).wait_for()
            await page.screenshot(path=str(output / "workbench.png"), full_page=True)
            await page.get_by_role("button", name=re.compile(r"^Workflow library")).click()
            await page.locator(".code-view").wait_for()
            workflows = await page.evaluate("async () => (await fetch('/api/workflows')).json()")
            repaired = next(workflow for workflow in workflows if workflow["version"] > 1)
            await page.locator(".workflow-item").filter(has_text=repaired["workflow_id"][:12]).click()
            await page.locator(".version-selector select").select_option(str(repaired["version"]))
            await page.get_by_role("button", name="Repair diff", exact=False).click()
            await page.locator(".code-diff").wait_for()
            await page.screenshot(path=str(output / "repair-diff.png"), full_page=True)
            await page.get_by_role("button", name="Evaluation", exact=True).click()
            await page.get_by_text("live-gemini-local-demo", exact=True).wait_for()
            await page.screenshot(path=str(output / "evaluation.png"), full_page=True)
            if errors:
                raise RuntimeError("Dashboard JavaScript errors: " + "; ".join(errors))
            print("Dashboard providers, workflow export, repair diff, and evaluation report passed.")
            print(output)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
