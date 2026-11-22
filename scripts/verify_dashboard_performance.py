"""Check local provider selection, typing responsiveness, and idle polling."""
import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright


async def main():
    root = Path(__file__).resolve().parents[1]
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(root / ".data/browsers"))
    output = root / ".data/diagnostics/dashboard-performance"
    output.mkdir(parents=True, exist_ok=True)
    errors, counts = [], Counter()
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        response = await client.get("http://127.0.0.1:8000/api/health?refresh=true")
        response.raise_for_status()
        assert response.json()["providers"]["ollama"]["configured"], "Ollama must be available"
        durations = []
        for _ in range(3):
            start = time.perf_counter()
            response = await client.get("http://127.0.0.1:8000/api/health")
            response.raise_for_status()
            durations.append(round((time.perf_counter() - start) * 1000, 2))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: counts.update([urlparse(request.url).path]) if "/api/" in request.url else None)
            await page.goto("http://127.0.0.1:5173", wait_until="domcontentloaded")
            await page.wait_for_function("!document.querySelector('#provider-select option[value=ollama]').disabled")
            provider = page.get_by_test_id("provider-select")
            await provider.select_option("ollama")
            await page.locator(".provider-note").get_by_text("qwen3-vl:2b-instruct", exact=True).wait_for()
            await page.wait_for_timeout(2000)
            counts.clear()
            timings = await page.evaluate("""async () => {
                const input = document.querySelector('#task-input');
                const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
                setter.call(input, 'Go to Google and search for ');
                input.dispatchEvent(new Event('input', {bubbles:true}));
                const timings = [];
                for (const letter of 'browser automation') {
                    const start = performance.now();
                    setter.call(input, input.value + letter);
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                    await new Promise(requestAnimationFrame);
                    timings.push(performance.now() - start);
                }
                return timings;
            }""")
            await page.wait_for_timeout(20000)
            assert await provider.input_value() == "ollama", "Polling must preserve the provider selection"
            assert not errors, errors
            assert counts["/api/benchmarks"] == 0, "Idle workbench must not fetch benchmark reports"
            assert counts["/api/runs"] <= 4, "Idle refreshes must not overlap or poll every 2.5 seconds"
            await page.screenshot(path=str(output / "workbench.png"), full_page=True)
            report = {"ollama_model": "qwen3-vl:2b-instruct", "ollama_selectable": True,
                      "cached_health_ms": durations, "typing_frame_ms": [round(value, 2) for value in timings],
                      "idle_window_seconds": 20, "api_requests": dict(counts), "javascript_errors": errors}
            (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
