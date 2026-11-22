"""Export selected existing runs as a measured demonstration, without rerunning inference."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.report_generator import write_reports  # noqa: E402
from benchmarks.run_suite import environment  # noqa: E402
from jit_ui.config import Settings  # noqa: E402
from jit_ui.storage import Repository  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs=4, required=True, metavar="RUN_ID",
                        help="Cold, hot, mutation, and post-repair run identifiers, in that order.")
    parser.add_argument("--name", required=True)
    arguments = parser.parse_args()
    if not arguments.name.replace("-", "").replace("_", "").isalnum():
        parser.error("--name must contain letters, numbers, underscores, or hyphens")
    settings = Settings()
    repository = Repository(settings)
    try:
        records = [repository.get_run(identifier) for identifier in arguments.runs]
        if any(record is None for record in records):
            parser.error("A selected run was not found")
        providers = {record.request.provider for record in records}
        if len(providers) != 1:
            parser.error("Select four runs with the same provider")
        suite = {"suite_id": arguments.name, "created_at": datetime.now(timezone.utc).isoformat(),
                 "provider": records[0].request.provider, "simulated": records[0].metrics.simulated,
                 "environment": environment(), "configuration": {"run_ids": arguments.runs},
                 "methodology": {"scope": "One recorded local demonstration; no new actions or inference.",
                    "limitations": "Single samples demonstrate functionality, not a statistically controlled recovery benchmark.",
                    "export": "The generated deterministic Python was separately executed with new parameters."}, "trials": []}
        for record, method in zip(records, ["jit_cold", "jit_hot", "jit_mutation", "post_repair_hot"]):
            suite["trials"].append({"trial_id": record.run_id, "run_id": record.run_id,
                "site_id": record.request.site_id, "method": method, "provider": record.request.provider,
                "status": record.status, "workflow_id": record.workflow_id, "workflow_version": record.workflow_version,
                "error": record.error, "error_code": record.error_code, **record.metrics.model_dump(mode="json")})
        paths = write_reports(suite, settings.data_dir / "benchmarks")
        print(paths["html"])
    finally:
        repository.close()


if __name__ == "__main__":
    main()
