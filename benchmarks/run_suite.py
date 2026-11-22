"""Run isolated local browser trials and export measured JSON/CSV/HTML/SVG reports."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.report_generator import write_reports  # noqa: E402
from jit_ui.config import Settings  # noqa: E402
from jit_ui.runtime.browser_helpers import verify_goal  # noqa: E402


MUTATIONS = ("selector_drift", "spatial_shift", "copy_mutation", "dom_nesting", "obstacle")
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED", "INTERRUPTED"}


def task_for(site: str, provider: str, variant: int, **overrides: Any) -> dict[str, Any]:
    if site == "shop":
        products = ("shoes", "backpack", "headphones", "shoes")
        ceilings = (5000, 3500, 4500, 2000)
        parameters = {"product_name": products[variant % 4], "max_price": ceilings[variant % 4],
                      "sort_by": "price_asc" if variant % 2 else "rating_desc"}
        task = f"Search for {parameters['product_name']} under {parameters['max_price']} rupees, sorted by {parameters['sort_by']}."
        goal = "search_products"
    else:
        parameters = {"order_id": ("1042", "2089", "3015", "4027")[variant % 4]}
        task = f"Find order {parameters['order_id']} and mark it shipped."
        goal = "ship_order"
    return {"site_id": site, "goal_id": goal, "task": task, "parameters": parameters,
            "provider": provider, "mode": "auto", **overrides}


def environment() -> dict[str, Any]:
    dependencies = {}
    for name in ("fastapi", "playwright", "httpx", "google-genai", "chromadb", "onnxruntime"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    return {"python": platform.python_version(), "os": platform.platform(), "machine": platform.machine(),
            "processor": platform.processor(), "logical_cpus": os.cpu_count(), "dependencies": dependencies}


class SuiteRunner:
    def __init__(self, arguments: argparse.Namespace):
        self.arguments = arguments
        self.settings = Settings()
        self.runtime = httpx.AsyncClient(base_url=arguments.runtime_url.rstrip("/"), timeout=30)
        self.testbed = httpx.AsyncClient(base_url=arguments.testbed_api_url.rstrip("/"), timeout=30)
        self.safe_to_reset = True
        self.suite: dict[str, Any] = {
            "suite_id": "suite-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8],
            "created_at": datetime.now(timezone.utc).isoformat(), "provider": arguments.provider,
            "simulated": arguments.provider == "fixture", "environment": environment(),
            "configuration": {"sites": arguments.sites, "mutations": arguments.mutations,
                              "seeds_per_mutation": arguments.trials, "baseline_methods": not arguments.skip_baselines,
                              "gemini_model": self.settings.gemini_model if arguments.provider == "gemini" else None,
                              "ollama_model": self.settings.ollama_model if arguments.provider == "ollama" else None,
                              "run_timeout_seconds": arguments.timeout},
            "methodology": {
                "scope": "Seeded local testbeds only; Amazon is excluded.",
                "isolation": "Learn one clean version per app. Restore it before each mutation case; reset fixture data, sessions, and shipments before every method. Post-repair replay keeps the verified patch but reintroduces the same mutation and uses new parameters.",
                "baselines": "Static-selector Playwright uses fixed original test IDs; always-vision forces cold exploration with compilation disabled. JIT hot executes the clean workflow; JIT mutation permits scoped repair; post-repair hot must complete with zero model calls.",
                "timing": "Runtime duration excludes queue wait; client duration includes submission/polling. Static duration includes isolated browser startup and execution. Summary latency distributions include successful trials; failures are retained separately.",
                "cost": "Reported model calls and tokens are observed counters. No unconfigured cloud price or local compute cost is invented.",
                "claims": "Fixture runs are integration simulations. Real-provider outcomes require a separate live suite; no target speedup/recovery is assumed.",
            }, "trials": [],
        }

    async def request(self, client: httpx.AsyncClient, method: str, path: str, body: dict | None = None) -> Any:
        response = await client.request(method, path, json=body)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {str(detail)[:500]}")
        return response.json()

    async def reset(self, site: str, seed: int, mutation: str | None = None) -> None:
        await self.request(self.testbed, "POST", "/api/reset", {"seed": seed})
        if mutation:
            await self.request(self.testbed, "POST", "/api/mutations", {"site_id": site, "mutation": mutation, "seed": seed})

    async def activate(self, workflow_id: str, version: int) -> None:
        await self.request(self.runtime, "POST", f"/api/workflows/{workflow_id}/activate", {"version": version})

    async def cancel_and_wait(self, run_id: str) -> None:
        await self.request(self.runtime, "POST", f"/api/runs/{run_id}/cancel", {})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            run = await self.request(self.runtime, "GET", f"/api/runs/{run_id}")
            if run["status"] in TERMINAL:
                return
            await asyncio.sleep(.15)
        raise TimeoutError("Cancellation did not reach a terminal status; fixtures were left unchanged.")

    def row(self, site: str, method: str, mutation: str | None, seed: int) -> dict[str, Any]:
        return {"trial_id": uuid4().hex, "phase": "mutation" if mutation else "baseline", "site_id": site,
                "method": method, "mutation": mutation, "seed": seed, "provider": self.arguments.provider,
                "simulated": self.arguments.provider == "fixture", "status": "FAILED", "duration_ms": 0,
                "client_duration_ms": 0, "model_calls": 0, "input_tokens": 0, "output_tokens": 0,
                "actions": 0, "healed_steps": 0}

    def record(self, row: dict[str, Any]) -> dict[str, Any]:
        self.suite["trials"].append(row)
        label = f"{row['site_id']} / {row['method']} / {row.get('mutation') or 'clean'} / seed {row['seed']}"
        print(f"{label}: {row['status']} ({row['duration_ms'] / 1000:.2f}s; {row['model_calls']} model calls)", flush=True)
        return row

    async def run(self, request: dict[str, Any], method: str, mutation: str | None, seed: int) -> dict[str, Any]:
        row = self.row(request["site_id"], method, mutation, seed)
        started = time.perf_counter()
        run_id = None
        try:
            run = await self.request(self.runtime, "POST", "/api/runs", request)
            run_id = run["run_id"]
            row["run_id"] = run_id
            deadline = time.monotonic() + self.arguments.timeout
            while run["status"] not in TERMINAL:
                if time.monotonic() > deadline:
                    await self.request(self.runtime, "POST", f"/api/runs/{run_id}/cancel", {})
                    raise TimeoutError("Trial timed out and cancellation was requested.")
                await asyncio.sleep(.15)
                run = await self.request(self.runtime, "GET", f"/api/runs/{run_id}")
            row.update({"status": run["status"], "automation_id": run.get("automation_id"), "workflow_id": run.get("workflow_id"),
                        "workflow_version": run.get("workflow_version"), "error": run.get("error"),
                        "error_code": run.get("error_code"), **run.get("metrics", {})})
            if method == "jit_cold" and row["status"] == "SUCCEEDED" and (
                not row.get("workflow_id") or not row.get("workflow_version")
            ):
                row.update(status="FAILED", error_code="COMPILE_NOT_PUBLISHED", error="The cold goal succeeded without publishing a reusable workflow.")
            if method in ("jit_hot", "post_repair_hot") and row["status"] == "SUCCEEDED":
                if run.get("mode") != "HOT" or row["model_calls"] != 0:
                    row.update(status="FAILED", error_code="HOT_INFERENCE", error="Expected deterministic HOT replay with zero model calls.")
            if request["site_id"] == "admin" and row["status"] == "SUCCEEDED":
                state = await self.request(self.testbed, "GET", "/api/state")
                target = str(request["parameters"]["order_id"])
                shipments = state["shipments"]
                if len(shipments) != 1 or shipments[0]["order_id"] != target:
                    row.update(status="FAILED", error_code="SHIPMENT_ORACLE", error="Shipment count/order did not match the single requested mutation.")
            row["result"] = run.get("result")
        except Exception as exc:
            row.update(status="FAILED", error_code=type(exc).__name__, error=str(exc)[:1000])
            row["duration_ms"] = (time.perf_counter() - started) * 1000
            if run_id and run.get("status") not in TERMINAL:
                try:
                    await self.cancel_and_wait(run_id)
                except Exception as cancellation_error:
                    self.safe_to_reset = False
                    row["error"] += "; " + str(cancellation_error)
                    row["client_duration_ms"] = (time.perf_counter() - started) * 1000
                    self.record(row)
                    raise RuntimeError("The active run could not be stopped. End the run before resetting fixtures.") from cancellation_error
        except BaseException:
            if run_id:
                try:
                    await self.cancel_and_wait(run_id)
                except Exception:
                    self.safe_to_reset = False
            raise
        row["client_duration_ms"] = (time.perf_counter() - started) * 1000
        return self.record(row)

    async def static(self, request: dict[str, Any], mutation: str, seed: int) -> dict[str, Any]:
        from playwright.async_api import async_playwright, expect

        row = self.row(request["site_id"], "static_selector", mutation, seed)
        row.update(provider="static", simulated=False)
        started = time.perf_counter()
        try:
            async with async_playwright() as playwright:
                launch: dict[str, Any] = {"headless": True}
                if self.settings.chromium_path:
                    launch["executable_path"] = self.settings.chromium_path
                browser = await playwright.chromium.launch(**launch)
                try:
                    page = await browser.new_page()
                    page.set_default_timeout(1600)
                    site = request["site_id"]
                    await page.goto(self.arguments.testbed_url.rstrip("/") + "/" + site)
                    row["actions"] += 1
                    values = request["parameters"]

                    async def fill(test_id: str, value: Any) -> None:
                        await page.get_by_test_id(test_id).fill(str(value))
                        row["actions"] += 1

                    async def click(test_id: str) -> None:
                        await page.get_by_test_id(test_id).click()
                        row["actions"] += 1

                    if site == "shop":
                        await fill("search-input", values["product_name"])
                        await click("search-submit")
                        await expect(page.get_by_test_id("search-results")).to_have_attribute("data-query", values["product_name"], timeout=5000)
                        await fill("max-price", values["max_price"])
                        await click("apply-filter")
                        await expect(page.get_by_test_id("search-results")).to_have_attribute("data-max-price", str(values["max_price"]), timeout=5000)
                        await page.get_by_test_id("sort-select").select_option(values["sort_by"])
                        row["actions"] += 1
                    else:
                        await fill("username", self.settings.fixture_username)
                        await fill("password", self.settings.fixture_password)
                        await click("login-submit")
                        await fill("order-search", values["order_id"])
                        await click("order-search-submit")
                        await expect(page.get_by_test_id("order-detail")).to_have_count(1, timeout=5000)
                        await click("ship-action")
                    definition = {"site_id": site, "goal_id": request["goal_id"]}
                    deadline = time.monotonic() + 5
                    passed = False
                    while time.monotonic() < deadline:
                        passed, result = await verify_goal(page, definition, request["goal_id"], values)
                        if passed:
                            row.update(status="SUCCEEDED", result=result)
                            break
                        await asyncio.sleep(.1)
                    if not passed:
                        row.update(error_code="GOAL_NOT_VERIFIED", error="Static actions did not satisfy the registered browser goal.")
                finally:
                    await browser.close()
        except Exception as exc:
            row.update(error_code=type(exc).__name__, error=str(exc)[:1000])
        row["duration_ms"] = row["client_duration_ms"] = (time.perf_counter() - started) * 1000
        return self.record(row)

    async def execute(self) -> None:
        await self.request(self.runtime, "GET", "/api/health")
        await self.request(self.testbed, "GET", "/api/health")
        known = await self.request(self.runtime, "GET", "/api/runs")
        known = known if isinstance(known, list) else known.get("runs", [])
        if any(run["status"] in ("QUEUED", "RUNNING", "CANCELLING") for run in known):
            raise RuntimeError("Finish or cancel existing runs before benchmarking; fixture resets require exclusive local use.")
        self.suite["started_at"] = datetime.now(timezone.utc).isoformat()
        for site in self.arguments.sites:
            await self.reset(site, 42)
            cold = await self.run(task_for(site, self.arguments.provider, 0, mode="cold"), "jit_cold", None, 42)
            if cold["status"] != "SUCCEEDED" or not cold.get("workflow_id"):
                continue
            workflow_id = cold["workflow_id"]
            original_version = cold["workflow_version"]
            try:
                for mutation in self.arguments.mutations:
                    for seed in range(1, self.arguments.trials + 1):
                        await self.activate(workflow_id, original_version)
                        await self.reset(site, seed)
                        await self.run(task_for(site, self.arguments.provider, 1, mode="hot", workflow_id=workflow_id), "jit_hot", None, seed)
                        if not self.arguments.skip_baselines:
                            await self.reset(site, seed, mutation)
                            await self.static(task_for(site, self.arguments.provider, 2), mutation, seed)
                            await self.reset(site, seed, mutation)
                            await self.run(task_for(site, self.arguments.provider, 2, mode="cold", compile_workflow=False), "always_vision", mutation, seed)
                        await self.activate(workflow_id, original_version)
                        await self.reset(site, seed, mutation)
                        repaired = await self.run(task_for(site, self.arguments.provider, 2, mode="hot", workflow_id=workflow_id), "jit_mutation", mutation, seed)
                        if repaired["status"] == "SUCCEEDED":
                            await self.reset(site, seed, mutation)
                            await self.run(task_for(site, self.arguments.provider, 3, mode="hot", workflow_id=workflow_id), "post_repair_hot", mutation, seed)
                        else:
                            skipped = self.row(site, "post_repair_hot", mutation, seed)
                            skipped.update(status="SKIPPED", error="Mutation run failed; no verified patch to replay.")
                            self.record(skipped)
            finally:
                if self.safe_to_reset:
                    await self.activate(workflow_id, original_version)
                    await self.reset(site, 42)
        self.suite["finished_at"] = datetime.now(timezone.utc).isoformat()

    async def close(self) -> None:
        await self.runtime.aclose()
        await self.testbed.aclose()


def parser() -> argparse.ArgumentParser:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--provider", choices=("fixture", "gemini", "ollama"), default="fixture")
    arguments.add_argument("--trials", type=int, default=5, help="Seeds per mutation/app; 5 gives 50 mutation cases.")
    arguments.add_argument("--sites", nargs="+", choices=("shop", "admin"), default=["shop", "admin"])
    arguments.add_argument("--mutations", nargs="+", choices=(*MUTATIONS, "compound"), default=list(MUTATIONS))
    arguments.add_argument("--skip-baselines", action="store_true", help="Run only JIT cold/hot/mutation/post-repair checks.")
    arguments.add_argument("--runtime-url", default="http://127.0.0.1:8000")
    arguments.add_argument("--testbed-api-url", default="http://127.0.0.1:8001")
    arguments.add_argument("--testbed-url", default="http://127.0.0.1:3000")
    arguments.add_argument("--timeout", type=float, default=650)
    arguments.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / ".data" / "benchmarks")
    return arguments


async def main() -> int:
    arguments = parser().parse_args()
    if arguments.trials < 1 or arguments.timeout <= 0:
        raise SystemExit("--trials and --timeout must be positive.")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PROJECT_ROOT / ".data" / "browsers"))
    runner = SuiteRunner(arguments)
    failure = None
    try:
        await runner.execute()
    except Exception as exc:
        failure = str(exc)
        runner.suite["suite_error"] = failure
        print(f"Suite stopped: {failure}", file=sys.stderr)
    finally:
        await runner.close()
        reports = write_reports(runner.suite, arguments.output_dir)
        print(f"Report: {reports['html']}", flush=True)
    if failure or not runner.suite["summary"]["required_trials_successful"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
