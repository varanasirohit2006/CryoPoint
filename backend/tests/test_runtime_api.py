"""Read API contract, event reconnection, and generated artifact boundaries."""

from fastapi.testclient import TestClient
import httpx
from unittest.mock import patch

from jit_ui.api.main import create_app
from jit_ui.config import Settings
from jit_ui.domain.models import RunRecord, RunStatus, TaskRequest
from jit_ui.runtime.service import RuntimeService


def test_provider_health_caches_probes_and_allows_an_explicit_refresh(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact")
    calls = []
    original_client = httpx.AsyncClient

    def probe(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"models": [{"name": settings.ollama_model}]})

    def local_client(**kwargs):
        assert kwargs["trust_env"] is False
        return original_client(**kwargs, transport=httpx.MockTransport(probe))

    with patch("jit_ui.api.main.httpx.AsyncClient", side_effect=local_client):
        with TestClient(create_app(settings)) as client:
            assert client.get("/api/ready").json()["worker_running"]
            assert calls == []
            assert client.get("/api/health").json()["providers"]["ollama"]["configured"]
            assert client.get("/api/health").json()["providers"]["ollama"]["configured"]
            assert calls == ["/api/tags"]
            assert client.get("/api/health?refresh=true").json()["providers"]["ollama"]["installed"]
            assert calls == ["/api/tags", "/api/tags"]


def test_api_validation_reconnect_and_artifact_boundaries(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_backend="exact")
    runtime = RuntimeService(settings)
    run = RunRecord(request=TaskRequest(site_id="shop", goal_id="search_products", task="Search shoes",
                     parameters={"product_name": "shoes"}, provider="fixture"), status=RunStatus.CANCELLED)
    runtime.repository.save_run(run)
    first = runtime.repository.add_event(run.run_id, "mode", {"mode": "COLD"})
    last = runtime.repository.add_event(run.run_id, "status", {"status": "CANCELLED"})
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "private.json").write_text('{"private":true}', encoding="utf-8")
    with TestClient(create_app(settings, service=runtime)) as client:
        assert len(client.get("/api/sites").json()) == 4
        assert client.get("/api/runs/missing").status_code == 404
        response = client.post("/api/runs", json={"site_id": "shop", "goal_id": "ship_order",
            "task": "Invalid goal", "parameters": {"product_name": "shoes"}, "provider": "fixture"})
        assert response.status_code == 422
        assert client.get("/api/runs").json()[0]["run_id"] == run.run_id
        with client.websocket_connect(f"/api/runs/{run.run_id}/events?after_seq={first.sequence}") as stream:
            assert stream.receive_json()["sequence"] == last.sequence
        assert client.get("/api/artifacts/cache/private.json").status_code == 404
        assert client.get("/api/artifacts/runs/%2e%2e/%2e%2e/cache/private.json").status_code == 404
