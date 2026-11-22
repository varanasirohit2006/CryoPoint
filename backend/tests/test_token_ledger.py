from fastapi.testclient import TestClient

from jit_ui.api.main import create_app
from jit_ui.benchmarks.ledger import AutomationLedger
from jit_ui.config import Settings
from jit_ui.domain.models import RunMetrics, RunMode, RunRecord, RunStatus, TaskRequest
from jit_ui.runtime.service import RuntimeService
from jit_ui.storage.repository import Repository


def request(**kwargs):
    return TaskRequest(site_id="amazon_in", goal_id="search_products", task="Find toys under 500",
                       parameters={"query": "toys", "max_price": 500}, **kwargs)


def test_measured_usage_savings_failures_model_isolation_and_persistence(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact")
    repository = Repository(settings)
    ledger = AutomationLedger(repository)
    group = ledger.prepare(request())
    assert ledger.prepare(request().model_copy(update={"parameters": {"query": "dolls", "max_price": 700}})) == group
    assert ledger.prepare(request().model_copy(update={"parameters": {"query": "dolls"}})) != group
    cold = RunRecord(request=request(), automation_id=group, mode=RunMode.COMPILING, status=RunStatus.SUCCEEDED,
        metrics=RunMetrics(model="gemini-test", model_calls=3, input_tokens=900, output_tokens=100))
    ledger.account(cold)
    repository.save_run(cold)
    assert cold.metrics.estimated_tokens_saved == 0
    hot = RunRecord(request=request(), automation_id=group, mode=RunMode.HOT, status=RunStatus.SUCCEEDED,
                    metrics=RunMetrics(model="gemini-test"))
    ledger.account(hot)
    repository.save_run(hot)
    assert hot.metrics.estimated_tokens_saved == 1000
    failure = RunRecord(request=request(), automation_id=group, mode=RunMode.HEALING, status=RunStatus.FAILED,
                       metrics=RunMetrics(model="gemini-test", model_calls=1, input_tokens=180, output_tokens=20))
    ledger.account(failure)
    repository.save_run(failure)
    assert failure.metrics.estimated_tokens_saved == -200
    for model, complete, simulated in [("ollama-test", True, False), ("gemini-test", False, False), ("gemini-test", True, True)]:
        run = RunRecord(request=request(), automation_id=group, status=RunStatus.SUCCEEDED, mode=RunMode.HEALING,
            metrics=RunMetrics(model=model, token_usage_complete=complete, simulated=simulated, model_calls=1, input_tokens=10))
        ledger.account(run)
        assert run.metrics.estimated_tokens_saved is None
    report = ledger.report(group)
    assert report["summary"]["total_tokens"] == 1200
    assert report["summary"]["estimated_tokens_saved"] == 800
    assert report["summary"]["comparable_runs"] == 3
    repository.close()
    repository = Repository(settings)
    assert AutomationLedger(repository).report(group)["summary"]["estimated_tokens_saved"] == 800
    repository.close()


def test_preview_set_and_exports_do_not_start_automation(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact")
    runtime = RuntimeService(settings)
    with TestClient(create_app(settings, service=runtime)) as client:
        preview = client.post("/api/tasks/preview", json=request().model_dump(mode="json"))
        assert preview.status_code == 200
        assert preview.json()["parameters"] == {"query": "toys", "max_price": 500}
        assert client.get("/api/runs").json() == []
        response = client.post("/api/automations", json={"name": "Toys budget", "site_id": "amazon_in", "goal_id": "search_products"})
        assert response.status_code == 201
        identifier = response.json()["automation_id"]
        assert client.get(f"/api/automations/{identifier}").json()["summary"]["run_count"] == 0
        for format in ("csv", "json"):
            assert client.get(f"/api/automations/{identifier}/export?format={format}").status_code == 200
        assert client.get("/api/automations/missing").status_code == 404


def test_history_reaccounting_persists_incomplete_flags_and_removes_stale_savings(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    repository = Repository(settings)
    ledger = AutomationLedger(repository)
    run = RunRecord(request=request(), automation_id=ledger.prepare(request()), mode=RunMode.COLD,
                    status=RunStatus.SUCCEEDED, metrics=RunMetrics(model="test", model_calls=1, input_tokens=900))
    ledger.account(run)
    assert run.metrics.estimated_tokens_saved == 0
    repository.save_run(run)
    repository.add_event(run.run_id, "provider_usage", {"usage_known": False, "input_tokens": 900})
    ledger.migrate_history()
    record = repository.get_run(run.run_id)
    assert not record.metrics.token_usage_complete
    assert record.metrics.estimated_tokens_saved is None
    assert ledger.report(run.automation_id)["summary"]["comparable_runs"] == 0
    repository.close()
