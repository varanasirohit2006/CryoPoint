"""Inspect one vision decision without applying it to the observed browser."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

from jit_ui.browser import BrowserSession  # noqa: E402
from jit_ui.config import Settings  # noqa: E402
from jit_ui.domain.models import TaskRequest  # noqa: E402
from jit_ui.domain.sites import site_definitions, validate_task  # noqa: E402
from jit_ui.providers import create_provider  # noqa: E402
from jit_ui.providers.base import ProviderError  # noqa: E402


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["gemini", "ollama"], required=True)
    arguments = parser.parse_args()
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PROJECT_ROOT / ".data" / "browsers"))
    settings = Settings()
    task = validate_task(TaskRequest(site_id="shop", goal_id="search_products", task="Search shoes under 5000 sorted by rating.",
        parameters={"product_name": "shoes", "max_price": 5000, "sort_by": "rating_desc"},
        provider=arguments.provider), settings)
    provider = create_provider(arguments.provider, settings)
    folder = settings.data_dir / "diagnostics" / uuid4().hex
    session = BrowserSession(settings, folder, site_definitions(settings)[task.site_id])
    try:
        await session.open()
        observation = await session.observe()
        response = await provider.decide(observation, task, [], instruction="Propose one current browser action. Do not claim completion.")
        report = {"status": "VALID_DECISION", "provider": arguments.provider, "model": response.model,
                  "action": response.action.model_dump(mode="json") if response.action else None,
                  "done": response.done, "input_tokens": response.input_tokens, "output_tokens": response.output_tokens}
    except ProviderError as exc:
        report = {"status": "REJECTED", "provider": arguments.provider, "code": exc.code,
                  "validation_errors": exc.validation_errors, **exc.usage}
    finally:
        await session.close()
        if hasattr(provider, "close"):
            await provider.close()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "decision.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
