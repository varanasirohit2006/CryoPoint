"""Evaluation must retain failures and distinguish simulation from AI evidence."""

import csv
import json

from benchmarks.report_generator import summarize, write_reports


def sample_suite():
    return {"suite_id": "test-report", "simulated": True, "trials": [
        {"trial_id": "cold", "site_id": "shop", "method": "jit_cold", "status": "SUCCEEDED", "duration_ms": 10000, "model_calls": 5},
        {"trial_id": "hot", "site_id": "shop", "method": "jit_hot", "status": "SUCCEEDED", "duration_ms": 500, "model_calls": 0},
        {"trial_id": "repair-failed", "site_id": "shop", "method": "jit_mutation", "status": "FAILED", "duration_ms": 20000, "healed_steps": 1, "error": "<script>alert('failed')</script>"},
        {"trial_id": "repair-ok", "site_id": "shop", "method": "jit_mutation", "status": "SUCCEEDED", "duration_ms": 2000, "healed_steps": 1},
    ]}


def test_failed_repairs_do_not_count_as_successful_healing():
    summary = summarize(sample_suite())
    assert summary["mutation_trials"] == 2
    assert summary["mutation_completion_rate"] == .5
    assert summary["successful_trials_with_healing"] == 1
    assert summary["successful_hot_runs_without_model_calls"] == 1
    assert summary["cold_hot_speedups"] == []
    assert not summary["required_trials_successful"]


def test_reports_keep_raw_failure_rows_and_escape_html(tmp_path):
    paths = write_reports(sample_suite(), tmp_path)
    raw = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert len(raw["trials"]) == 4
    with paths["csv"].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[2]["status"] == "FAILED"
    document = paths["html"].read_text(encoding="utf-8")
    assert "Offline simulation" in document
    assert "<script>" not in document
    assert "&lt;script&gt;" in document
    assert paths["svg"].is_file()
