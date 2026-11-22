"""Measured usage and explicitly estimated savings against recorded learning runs."""

import csv
import io
import json

from jit_ui.domain.intent import filter_shape
from jit_ui.domain.models import RunMode, RunStatus


class AutomationLedger:
    def __init__(self, repository):
        self.repository = repository

    def default_set(self, task):
        shape = filter_shape(task.parameters) if task.site_id == "amazon_in" else ()
        signature = json.dumps([task.site_id, task.goal_id, shape, task.provider == "fixture", task.intent_key] if task.site_id == "web"
                               else [task.site_id, task.goal_id, shape, task.provider == "fixture"])
        labels = {"shop": "Shopping workflow", "admin": "Order dispatch", "amazon_in": "Amazon product search", "web": "Browser workflow"}
        if task.site_id == "web":
            from urllib.parse import urlparse
            labels["web"] = urlparse(task.target_url).netloc + " · " + task.goal_id.replace("_", " ")
        suffix = " · " + ", ".join(key.replace("_", " ") for key in shape) if shape else ""
        if task.provider == "fixture":
            suffix += " · simulation"
        return self.repository.create_automation(labels[task.site_id] + suffix, task.site_id, task.goal_id, signature)

    def prepare(self, task):
        if task.automation_id:
            group = self.repository.get_automation(task.automation_id)
            if not group or group["site_id"] != task.site_id or group["goal_id"] != task.goal_id:
                raise ValueError("Choose an automation set for the selected site and goal.")
            return group["automation_id"]
        return self.default_set(task)["automation_id"]

    def baseline(self, workflow):
        if workflow is None:
            return None
        identifier = workflow.metadata.get("learning_run_id")
        if not identifier:
            original = self.repository.get_workflow(workflow.workflow_id, 1)
            identifier = original.source_run_id if original else workflow.source_run_id
        run = self.repository.get_run(identifier) if identifier else None
        return run if run and run.status == RunStatus.SUCCEEDED and run.mode in {RunMode.COLD, RunMode.COMPILING} else None

    def account(self, run, workflow=None):
        metrics = run.metrics
        for field in ("baseline_run_id", "baseline_model", "baseline_tokens", "reference_tokens", "estimated_tokens_saved"):
            setattr(metrics, field, None)
        if metrics.simulated:
            return
        total = metrics.input_tokens + metrics.output_tokens
        if metrics.model_calls > 0 and total == 0:
            metrics.token_usage_complete = False
        baseline = self.baseline(workflow)
        if run.mode in {RunMode.COLD, RunMode.COMPILING} and run.status == RunStatus.SUCCEEDED:
            baseline = run
        if baseline is None and run.automation_id:
            candidates = [item for item in self.repository.list_runs(limit=10000)
                          if item.automation_id == run.automation_id and item.status == RunStatus.SUCCEEDED
                          and item.mode in {RunMode.COLD, RunMode.COMPILING} and not item.metrics.simulated
                          and item.metrics.model == metrics.model
                          and (run.request.site_id != "web" or item.request.intent_key == run.request.intent_key)
                          and filter_shape(item.request.parameters) == filter_shape(run.request.parameters)]
            baseline = min(candidates, key=lambda item: item.created_at) if candidates else None
        if (baseline is None or baseline.metrics.simulated or not baseline.metrics.token_usage_complete
                or not metrics.token_usage_complete):
            return
        baseline_tokens = baseline.metrics.input_tokens + baseline.metrics.output_tokens
        if baseline_tokens <= 0 or (metrics.model_calls > 0 and metrics.model != baseline.metrics.model):
            return
        metrics.baseline_run_id = baseline.run_id
        metrics.baseline_model = baseline.metrics.model
        metrics.baseline_tokens = baseline_tokens
        # Failed attempts cost tokens but receive no completed-task reference credit.
        metrics.reference_tokens = baseline_tokens if run.status == RunStatus.SUCCEEDED else 0
        metrics.estimated_tokens_saved = metrics.reference_tokens - total

    def migrate_history(self):
        records = self.repository.list_runs(limit=10000)
        for run in records:
            if any(event.kind == "provider_usage" and (event.data.get("usage_known") is False or event.data.get("attempts", 1) > 1 or
                   (event.data.get("failed") and not event.data.get("input_tokens") and not event.data.get("output_tokens")))
                   for event in self.repository.list_events(run.run_id)):
                run.metrics.token_usage_complete = False
            if run.automation_id is None:
                run.automation_id = self.prepare(run.request)
            self.repository.save_run(run)
        for workflow in self.repository.list_workflows():
            if self.repository.workflow_automation(workflow.workflow_id):
                continue
            source = self.baseline(workflow)
            group = source.automation_id if source else None
            if group:
                self.repository.associate_workflow(workflow.workflow_id, group)
        for run in sorted(records, key=lambda item: item.created_at):
            if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.AWAITING_CONFIRMATION}:
                workflow = self.repository.get_workflow(run.workflow_id) if run.workflow_id else None
                self.account(run, workflow)
                self.repository.save_run(run)

    def report(self, identifier):
        group = self.repository.get_automation(identifier)
        if not group:
            return None
        runs = sorted([run for run in self.repository.list_runs(limit=10000) if run.automation_id == identifier],
                      key=lambda run: run.created_at)
        comparable = [run for run in runs if run.metrics.estimated_tokens_saved is not None]
        reference = sum(run.metrics.reference_tokens or 0 for run in comparable)
        saved = sum(run.metrics.estimated_tokens_saved or 0 for run in comparable)
        models = {}
        for run in runs:
            if run.metrics.simulated:
                continue
            name = run.metrics.model or run.request.provider
            model = models.setdefault(name, {"model": name, "input_tokens": 0, "output_tokens": 0,
                                            "model_calls": 0, "runs": 0})
            for key in ("input_tokens", "output_tokens", "model_calls"):
                model[key] += getattr(run.metrics, key)
            model["runs"] += 1
        summary = {**group, "run_count": len(runs), "successful_runs": sum(run.status == RunStatus.SUCCEEDED for run in runs),
            "cold_runs": sum(run.mode in {RunMode.COLD, RunMode.COMPILING} for run in runs),
            "hot_runs": sum(run.mode == RunMode.HOT for run in runs),
            "healing_runs": sum(run.metrics.healed_steps > 0 for run in runs),
            "input_tokens": sum(run.metrics.input_tokens for run in runs if not run.metrics.simulated),
            "output_tokens": sum(run.metrics.output_tokens for run in runs if not run.metrics.simulated),
            "model_calls": sum(run.metrics.model_calls for run in runs if not run.metrics.simulated),
            "reference_tokens": reference if comparable else None,
            "estimated_tokens_saved": saved if comparable else None,
            "estimated_savings_percent": saved / reference * 100 if reference else None,
            "comparable_runs": len(comparable),
            "incomplete_usage_runs": sum(not run.metrics.token_usage_complete for run in runs if not run.metrics.simulated),
            "simulation_runs": sum(run.metrics.simulated for run in runs), "models": list(models.values())}
        summary["total_tokens"] = summary["input_tokens"] + summary["output_tokens"]
        return {"summary": summary, "runs": [run.model_dump(mode="json") for run in runs],
                "methodology": "Usage counts are measured. Savings are estimates against each workflow's recorded learning run; failed attempts receive no completion credit. Different inference models require separate baselines. Zero-inference replays use their source learning model as the reference. Incomplete usage and simulations receive no savings estimate."}

    def summaries(self):
        return [self.report(group["automation_id"])["summary"] for group in self.repository.list_automations()]

    def csv(self, identifier):
        report = self.report(identifier)
        if report is None:
            return None
        buffer = io.StringIO(newline="")
        columns = ["run_id", "automation_id", "status", "mode", "execution_strategy", "provider", "model", "input_tokens", "output_tokens",
                   "total_tokens", "baseline_run_id", "baseline_model", "baseline_tokens", "estimated_tokens_saved", "token_usage_complete", "task"]
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for run in report["runs"]:
            task = run["request"]["task"]
            if task.lstrip().startswith(("=", "+", "-", "@")):
                task = "'" + task
            writer.writerow({**run, **run["metrics"], "provider": run["request"]["provider"], "task": task,
                             "total_tokens": run["metrics"]["input_tokens"] + run["metrics"]["output_tokens"]})
        return buffer.getvalue()
