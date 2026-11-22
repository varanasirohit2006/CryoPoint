"""Document replacement can interrupt reads without making the browser unusable."""
import pytest

from jit_ui.browser import BrowserSession
from jit_ui.config import Settings


async def test_read_retries_document_replacement_without_navigating(tmp_path):
    class Page:
        waits = 0

        def is_closed(self):
            return False

        async def wait_for_load_state(self, *args, **kwargs):
            self.waits += 1

        async def wait_for_timeout(self, *args):
            pass

    session = BrowserSession(Settings(_env_file=None), tmp_path, {})
    session.page = Page()
    reads = 0
    async def read():
        nonlocal reads
        reads += 1
        if reads == 1:
            raise RuntimeError("Page.evaluate: Execution context was destroyed, most likely because of a navigation")
        return "new document"
    assert await session._stable_read(read) == "new document"
    assert reads == 2 and session.page.waits == 1


async def test_unrelated_read_error_is_not_swallowed(tmp_path):
    class Page:
        def is_closed(self):
            return False

    session = BrowserSession(Settings(_env_file=None), tmp_path, {})
    session.page = Page()
    async def read():
        raise RuntimeError("Permission denied")
    with pytest.raises(RuntimeError, match="Permission denied"):
        await session._stable_read(read)
