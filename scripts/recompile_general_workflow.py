"""Publish a new compiled version from an existing verified general-browser trace."""
import argparse
import json

from jit_ui.compiler import compile_trace, generate_python
from jit_ui.config import Settings
from jit_ui.domain.models import ActionFrame, RunStatus
from jit_ui.domain.sites import task_site
from jit_ui.storage.repository import Repository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_id")
    args = parser.parse_args()
    settings = Settings()
    repository = Repository(settings)
    try:
        previous = repository.get_workflow(args.workflow_id)
        if not previous or previous.site_id != "web":
            raise ValueError("Choose a verified general-browser workflow.")
        source_id = previous.metadata.get("learning_run_id") or previous.source_run_id
        source = repository.get_run(source_id)
        if not source or source.status != RunStatus.SUCCEEDED:
            raise ValueError("The successful source run is unavailable.")
        path = settings.data_dir / "runs" / source.run_id / "trace.json"
        frames = [ActionFrame.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
        site = task_site(source.request, settings)
        compiled = compile_trace(source.request, frames, site["parameters"], source.run_id)
        compiled.workflow_id = previous.workflow_id
        compiled.version, compiled.parent_version = previous.version + 1, previous.version
        compiled.metadata = {**previous.metadata, **compiled.metadata}
        repository.publish(compiled, generate_python(compiled, site), expected_version=previous.version)
        print(f"Recompiled general workflow {compiled.workflow_id} as version {compiled.version}.")
    finally:
        repository.close()


if __name__ == "__main__":
    main()
