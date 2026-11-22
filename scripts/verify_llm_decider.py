"""Check a real model's next-action choice on the live page without executing it."""
import argparse
import asyncio
import json

from jit_ui.browser import BrowserSession
from jit_ui.config import Settings
from jit_ui.domain.models import TaskRequest
from jit_ui.domain.sites import task_site, validate_task
from jit_ui.exploration import validate_proposal
from jit_ui.providers import create_provider


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["gemini", "ollama"], default="gemini")
    parser.add_argument("--url", default="https://www.flipkart.com/")
    parser.add_argument("--task", default="Search for formal wear for men age 20 minimum 500 maximum 1200")
    args = parser.parse_args()
    settings = Settings()
    task = validate_task(TaskRequest(site_id="web", target_url=args.url, task=args.task, provider=args.provider), settings)
    output = settings.data_dir / "diagnostics/llm-decider" / args.provider
    output.mkdir(parents=True, exist_ok=True)
    session = BrowserSession(settings, output, task_site(task, settings))
    provider = create_provider(args.provider, settings)
    try:
        await session.open()
        observation = await session.observe()
        response = await provider.decide(observation, task, [])
        if response.action:
            validate_proposal(response.action, observation, task, session.site)
        record = {"source": "live_model_selection_only", "url": observation.url, "task": task.task, "parameters": task.parameters,
                  "selection": response.model_dump(mode="json"),
                  "controls": [{"index": element.index, "name": element.name, "context": element.context,
                                "purpose": element.goal_control} for element in observation.elements],
                  "action_executed": False}
        (output / "selection.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"model": response.model, "candidate_id": response.candidate_id,
                          "action": response.action.model_dump(mode="json") if response.action else None,
                          "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                          "action_executed": False}, ensure_ascii=True))
    finally:
        await session.close()
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
