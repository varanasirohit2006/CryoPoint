"""Dashboard API; SQLite-backed websocket events can reconnect by sequence."""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from jit_ui.config import Settings
from jit_ui.domain.models import TaskRequest
from jit_ui.domain.sites import public_sites, validate_task
from jit_ui.runtime.service import QueueFullError, RuntimeService, TERMINAL


class Activation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: Literal["shop", "admin"]
    mutation: Literal["none", "selector_drift", "spatial_shift", "copy_mutation", "dom_nesting", "obstacle", "compound"]
    seed: int = 42


class Reset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = 42


class AutomationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    site_id: Literal["shop", "admin", "amazon_in", "web"]
    goal_id: str
    target_url: str | None = None


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


def create_app(settings: Settings | None = None, *, service: RuntimeService | None = None) -> FastAPI:
    settings = settings or Settings()
    runtime = service or RuntimeService(settings)
    ollama_status = None
    ollama_checked_at = 0.0
    ollama_status_lock = asyncio.Lock()

    async def check_ollama(refresh=False):
        nonlocal ollama_status, ollama_checked_at
        async with ollama_status_lock:
            if not refresh and ollama_status is not None and time.monotonic() - ollama_checked_at < 15:
                return ollama_status
            installed, available = False, False
            reason = "The local Ollama server is not running."
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(2, connect=.5), trust_env=False) as client:
                    response = await client.get(settings.ollama_url.rstrip("/") + "/api/tags")
                    response.raise_for_status()
                    available = True
                    installed = any(item.get("name") == settings.ollama_model or item.get("model") == settings.ollama_model
                                    for item in response.json().get("models", []))
                    reason = "" if installed else "The selected model is not installed in Ollama."
            except (httpx.HTTPError, ValueError):
                pass
            ollama_status = {"configured": available and installed, "available": available, "installed": installed,
                             "model": settings.ollama_model, "reason": reason}
            ollama_checked_at = time.monotonic()
            return ollama_status

    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="JIT-UI Workflow Agent", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/api/ready")
    async def ready():
        # Startup readiness must not depend on an optional inference server.
        return {"status": "ok", "worker_running": bool(runtime.worker and not runtime.worker.done())}

    @app.get("/api/health")
    async def health(refresh: bool = False):
        local_provider = await check_ollama(refresh)
        key_present = bool(settings.gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        return {"status": "ok", "worker_running": bool(runtime.worker and not runtime.worker.done()),
                "queue_size": runtime.queue.qsize(), "active_run_id": runtime.active_run_id,
                "routing": {"backend": settings.embedding_backend, "error": runtime.index_error},
                "providers": {"gemini": {"configured": key_present, "model": settings.gemini_model},
                              "ollama": local_provider,
                              "fixture": {"configured": True, "simulated": True,
                                          "model": "fixture:scripted-simulation"}}}

    @app.get("/api/sites")
    async def sites():
        return public_sites(settings)

    @app.post("/api/runs", status_code=202)
    async def submit(task: TaskRequest):
        try:
            return await runtime.submit(task)
        except QueueFullError as exc:
            raise HTTPException(429, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/tasks/preview")
    async def preview(task: TaskRequest):
        try:
            return validate_task(task, settings)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/automations")
    async def automations():
        return runtime.ledger.summaries()

    @app.post("/api/automations", status_code=201)
    async def new_automation(body: AutomationCreate):
        site = next(item for item in public_sites(settings) if item["site_id"] == body.site_id)
        supported = body.goal_id in {"browser_task", "web_search", "change_language"} if body.site_id == "web" else any(goal["goal_id"] == body.goal_id for goal in site["goals"])
        if not body.name.strip() or not supported:
            raise HTTPException(422, "Provide a name and a goal supported by this site.")
        return runtime.repository.create_automation(body.name.strip(), body.site_id, body.goal_id)

    @app.get("/api/automations/{identifier}")
    async def automation(identifier: str):
        report = runtime.ledger.report(identifier)
        if report is None:
            raise HTTPException(404, "Automation set not found.")
        return report

    @app.get("/api/automations/{identifier}/export")
    async def automation_export(identifier: str, format: Literal["json", "csv"] = "json"):
        report = await automation(identifier)
        content = runtime.ledger.csv(identifier) if format == "csv" else json.dumps(report, indent=2)
        return Response(content, media_type="text/csv" if format == "csv" else "application/json",
                        headers={"Content-Disposition": f'attachment; filename="automation-{identifier}.{format}"'})

    @app.get("/api/runs")
    async def list_runs(limit: int = Query(default=200, ge=1, le=10000)):
        return runtime.repository.list_runs(limit)

    @app.get("/api/runs/{identifier}")
    async def get_run(identifier: str):
        run = runtime.repository.get_run(identifier)
        if not run:
            raise HTTPException(404, "Run not found.")
        return run

    @app.post("/api/runs/{identifier}/cancel")
    async def cancel(identifier: str):
        run = await runtime.cancel(identifier)
        if not run:
            raise HTTPException(404, "Run not found.")
        return run

    @app.post("/api/runs/{identifier}/confirmation", status_code=202)
    async def confirm(identifier: str, body: Confirmation):
        try:
            return await runtime.confirm(identifier, body.approve)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs/{identifier}/events")
    async def events(identifier: str, after_seq: int = Query(default=0, ge=0)):
        await get_run(identifier)
        return runtime.repository.list_events(identifier, after_seq)

    @app.websocket("/api/runs/{identifier}/events")
    async def stream(websocket: WebSocket, identifier: str, after_seq: int = 0):
        await websocket.accept()
        if after_seq < 0 or runtime.repository.get_run(identifier) is None:
            await websocket.close(code=1008, reason="Invalid run or event sequence.")
            return
        cursor = after_seq
        try:
            while True:
                for event in runtime.repository.list_events(identifier, cursor):
                    await websocket.send_json(event.model_dump(mode="json"))
                    cursor = event.sequence
                run = runtime.repository.get_run(identifier)
                if run.status in TERMINAL:
                    await websocket.close(code=1000)
                    return
                await asyncio.sleep(.2)
        except (WebSocketDisconnect, RuntimeError):
            return

    @app.get("/api/workflows")
    async def workflows():
        return runtime.repository.list_workflows()

    @app.get("/api/workflows/{identifier}")
    async def workflow(identifier: str):
        item = runtime.repository.get_workflow(identifier)
        if item is None:
            raise HTTPException(404, "Workflow not found or artifact checksum failed.")
        return item

    @app.get("/api/workflows/{identifier}/versions")
    async def versions(identifier: str):
        items = runtime.repository.list_versions(identifier)
        if not items:
            raise HTTPException(404, "Workflow not found.")
        return items

    @app.get("/api/workflows/{identifier}/versions/{version}/export")
    async def export(identifier: str, version: int, format: Literal["python", "ir"] = "python"):
        artifact = runtime.repository.artifact(identifier, version, format)
        if not artifact:
            raise HTTPException(404, "Artifact not found or checksum failed.")
        return FileResponse(artifact, media_type="text/x-python" if format == "python" else "application/json",
                            filename=f"{identifier}-v{version}." + ("py" if format == "python" else "json"))

    async def activate_version(identifier: str, version: int):
        try:
            return await runtime.activate(identifier, version)
        except ValueError as exc:
            raise HTTPException(409 if runtime.busy else 404, str(exc)) from exc

    @app.post("/api/workflows/{identifier}/activate")
    async def activate(identifier: str, body: Activation):
        return await activate_version(identifier, body.version)

    @app.post("/api/workflows/{identifier}/versions/{version}/activate")
    async def activate_alias(identifier: str, version: int):
        return await activate_version(identifier, version)

    @app.get("/api/artifacts/{relative:path}")
    async def artifact(relative: str):
        root = settings.data_dir.resolve()
        path = (root / relative).resolve()
        # Serve generated public artifacts only; databases and caches stay private.
        if (not path.is_relative_to(root) or not relative.replace("\\", "/").startswith(("runs/", "workflows/", "reports/", "benchmarks/"))
                or not path.is_file() or path.suffix not in {".jpg", ".jpeg", ".png", ".json", ".py", ".csv", ".html", ".svg"}):
            raise HTTPException(404, "Artifact not found.")
        return FileResponse(path)

    @app.get("/api/benchmarks")
    async def benchmarks():
        results = []
        paths = list((settings.data_dir / "reports").glob("*.json"))
        paths.extend((settings.data_dir / "benchmarks").glob("*.json"))
        for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                if "suite_id" in report:
                    results.append(report)
            except (ValueError, OSError):
                continue
        return results

    async def proxy(path: str, payload: dict):
        if runtime.busy:
            raise HTTPException(409, "Wait for queued and active runs to finish before resetting or mutating the testbed.")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(settings.testbed_api_url.rstrip("/") + "/api/" + path, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(502, "The local testbed API is unavailable. Start it on port 8001.") from exc

    @app.post("/api/testbed/mutations")
    async def mutate(body: Mutation):
        return await proxy("mutations", body.model_dump())

    @app.post("/api/testbed/reset")
    async def reset(body: Reset):
        return await proxy("reset", body.model_dump())

    return app


app = create_app()


def serve():
    import uvicorn

    uvicorn.run("jit_ui.api.main:app", host="127.0.0.1", port=8000, loop="asyncio")
