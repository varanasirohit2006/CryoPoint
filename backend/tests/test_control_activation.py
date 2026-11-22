"""A requested button must be activated, not merely found on the current page."""
import os

import pytest

from jit_ui.browser import BrowserSession
from jit_ui.config import Settings
from jit_ui.domain.models import ActionSpec
from jit_ui.runtime.browser_helpers import verify_goal


pytestmark = pytest.mark.skipif(os.environ.get("JIT_RUN_BROWSER_TESTS") != "1", reason="Enable routed browser checks")


async def test_control_activation_rejects_other_controls_and_survives_navigation(tmp_path):
    site = {"site_id": "web", "start_url": "https://example.com/", "allowed_origin": "https://example.com",
            "completion_conditions": [{"kind": "control_activated", "value_ref": "control_name"}]}
    session = BrowserSession(Settings(_env_file=None), tmp_path, site)
    session.defer_navigation = True
    try:
        await session.open()
        await session._context.route("https://example.com/**", lambda route: route.fulfill(content_type="text/html",
            body='<html><body><button>Other</button><a href="/section">A category</a><label>A category<input></label></body></html>'))
        await session.page.goto(site["start_url"])
        parameters = {"control_name": "A category"}
        assert not (await verify_goal(session.page, site, "browser_task", parameters))[0]
        observation = await session.observe()
        other = next(element for element in observation.elements if element.name == "Other")
        await session.execute(ActionSpec(action="click", target_index=other.index), parameters)
        assert not (await verify_goal(session.page, site, "browser_task", parameters))[0]
        observation = await session.observe()
        textbox = next(element for element in observation.elements if element.tag == "input")
        await session.execute(ActionSpec(action="click", target_index=textbox.index), parameters)
        assert not (await verify_goal(session.page, site, "browser_task", parameters))[0]
        observation = await session.observe()
        category = next(element for element in observation.elements if element.role == "link")
        await session.execute(ActionSpec(action="click", target_index=category.index), parameters)
        assert session.page.url == "https://example.com/section"
        assert (await verify_goal(session.page, site, "browser_task", parameters))[0]
        assert not (await verify_goal(session.page, site, "browser_task", {"control_name": "Another category"}))[0]
    finally:
        await session.close()
