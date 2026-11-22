"""Budget success requires observed numbers, not budget words or missing results."""
import os

import pytest
from playwright.async_api import async_playwright

from jit_ui.domain.models import ConditionSpec, ElementFingerprint, Observation, TaskRequest
from jit_ui.exploration.completion import accept_completion_conditions
from jit_ui.runtime.browser_helpers import check_conditions, observed_number


@pytest.mark.parametrize("text, expected", [("₹1,200", 1200), ("4.8 stars 20 reviews ₹999 ₹1,999", 999),
    ("500.0", 500), ("£12.50", 12.5), ("", None), ("NaN", None), ("Infinity", None),
    ("20 reviews", None), ("2026-11-22", None)])
def test_numeric_evidence_rejects_unrelated_numbers(text, expected):
    assert observed_number(text) == expected


def test_budget_completion_resolves_live_target_and_rejects_wrong_comparison():
    task = TaskRequest(site_id="web", goal_id="web_search", task="Find coats under 1200", parameters={"query": "coats", "max_price": 1200})
    observation = Observation(url="https://example.com/", elements=[ElementFingerprint(index=2, tag="select", css="#max", name="Maximum price")])
    condition = ConditionSpec(kind="number_at_most", target_index=2, value_ref="max_price")
    site = {}
    accept_completion_conditions(task, site, [condition], observation)
    assert condition.target_index is None and condition.locators
    assert site["completion_conditions"][0]["value_ref"] == "max_price"
    with pytest.raises(ValueError, match="actual filter/result evidence"):
        accept_completion_conditions(task, {}, [condition.model_copy(update={"kind": "number_at_least"})])
    with pytest.raises(ValueError, match="absent"):
        accept_completion_conditions(task, {}, [ConditionSpec(kind="number_at_most", target_index=9, value_ref="max_price")], observation)


def test_one_in_budget_product_cannot_replace_evidence_of_an_available_price_control():
    task = TaskRequest(site_id="web", goal_id="web_search", task="Find coats under 1200", parameters={"query": "coats", "max_price": 1200})
    observation = Observation(url="https://example.com/", elements=[
        ElementFingerprint(index=1, tag="a", role="link", name="₹601", css="#product"),
        ElementFingerprint(index=2, tag="select", css="#max", current_value="2000", options=[{"value": "1000", "label": "1000"}, {"value": "Max", "label": "3000+"}])])
    with pytest.raises(ValueError, match="one product price"):
        accept_completion_conditions(task, {}, [ConditionSpec(kind="number_at_most", target_index=1, value_ref="max_price")], observation)
    condition = ConditionSpec(kind="number_at_most", target_index=2, value_ref="max_price")
    accept_completion_conditions(task, {}, [condition], observation)
    assert condition.locator.value == "#max"


@pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed browser tests")
async def test_numeric_completion_checks_every_visible_price_and_native_option_value():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content('''<select id="max"><option value="1000" selected>₹1,000</option></select>
                <input id="min" value="500"><div class="price">4.8 stars ₹599</div>
                <div class="price">₹999</div><div class="price" style="display:none">₹9999</div>''')
            parameters = {"min_price": 500.0, "max_price": 1200.0}
            prices = {"kind": "number_at_most", "locator": {"strategy": "css", "value": ".price"}, "value_ref": "max_price"}
            assert await check_conditions(page, [prices], parameters, {})
            assert await check_conditions(page, [{**prices, "kind": "number_at_least", "value_ref": "min_price"}], parameters, {})
            assert await check_conditions(page, [{**prices, "locator": {"strategy": "css", "value": "#max"}}], parameters, {})
            equality = {"kind": "value_equals", "locator": {"strategy": "css", "value": "#min"}, "value_ref": "min_price"}
            assert await check_conditions(page, [equality], parameters, {})
            assert not await check_conditions(page, [equality], {"min_price": "500.0"}, {})
            await page.locator(".price").nth(1).evaluate("e => e.innerText='₹1,201'")
            assert not await check_conditions(page, [prices], parameters, {})
            await page.locator(".price").nth(1).evaluate("e => e.innerText='20 reviews'")
            assert not await check_conditions(page, [prices], parameters, {})
            assert not await check_conditions(page, [{**prices, "locators": [{"strategy": "css", "value": ".price"}, {"strategy": "css", "value": "#max"}]}], parameters, {})
            assert not await check_conditions(page, [{**prices, "locator": {"strategy": "css", "value": ".missing"}}], parameters, {})
        finally:
            await browser.close()
