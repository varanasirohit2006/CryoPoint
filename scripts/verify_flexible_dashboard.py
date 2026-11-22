"""Check the built dashboard against the local API without submitting a task."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    output = Path(__file__).resolve().parents[1] / ".data/diagnostics/flexible-dashboard"
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto("http://127.0.0.1:5173")
            await page.get_by_test_id("site-select").select_option("amazon_in")
            await page.locator(".intent-preview").get_by_text("5 years girl toys", exact=True).wait_for()
            await page.locator(".intent-preview").get_by_text("500", exact=True).wait_for()
            await page.get_by_test_id("task-input").fill("Find dolls below 700 cheapest first")
            await page.locator(".intent-preview").get_by_text("dolls", exact=True).wait_for()
            await page.locator(".intent-preview").get_by_text("700", exact=True).wait_for()
            await page.screenshot(path=str(output / "natural-request.png"), full_page=True)
            await page.get_by_label("Set keywords and filters manually").check()
            assert await page.locator("#parameter-query").input_value() == "dolls"
            assert await page.locator("#parameter-max_price").input_value() == "700"
            await page.get_by_role("button", name="Evaluation", exact=True).click()
            await page.locator("#ledger-set").wait_for()
            await page.locator(".ledger-totals").wait_for()
            await page.screenshot(path=str(output / "token-ledger.png"), full_page=True)
            assert not errors, errors
            (output / "checks.json").write_text(json.dumps({"natural_preview": True, "manual_values": True,
                "automation_ledger": True, "browser_errors": errors}, indent=2), encoding="utf-8")
            print("Dashboard preview, manual fields, and automation token ledger passed.")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
