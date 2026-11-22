"""Emit a standalone deterministic Playwright script from validated IR data."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from jit_ui.domain.models import WorkflowIR


def generate_python(workflow: WorkflowIR, site: dict) -> str:
    package = Path(__file__).resolve().parents[1]
    helper_source = (package / "runtime" / "browser_helpers.py").read_text(encoding="utf-8")
    parameter_source = (package / "routing" / "parameter_helpers.py").read_text(encoding="utf-8")
    exported_site = {
        key: site[key] for key in ("site_id", "start_url", "allowed_origin", "allowed_origins", "completion_conditions", "condition_timeout_ms") if key in site
    }
    if site.get("credentials") or site.get("credential_env"):
        exported_site["credential_env"] = {
            "username": "JIT_FIXTURE_USERNAME", "password": "JIT_FIXTURE_PASSWORD"
        }
    exported_site["goal_id"] = workflow.goal_id
    if workflow.site_id == "web":
        exported_site["completion_conditions"] = workflow.metadata.get("completion_conditions", [])
    templates = Path(__file__).resolve().parents[3] / "templates"
    environment = Environment(
        loader=FileSystemLoader(templates), undefined=StrictUndefined,
        autoescape=False, keep_trailing_newline=True,
    )
    source = environment.get_template("workflow.py.j2").render(
        browser_helpers=helper_source,
        parameter_helpers=parameter_source,
        workflow_literal=repr(json.dumps(workflow.model_dump(mode="json"), ensure_ascii=False)),
        site_literal=repr(json.dumps(exported_site, ensure_ascii=False)),
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            raise ValueError("Generated scripts may not evaluate arbitrary code.")
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("jit_ui"):
            raise ValueError("Standalone scripts may not import the project package.")
        if isinstance(node, ast.Import) and any(alias.name.startswith("jit_ui") for alias in node.names):
            raise ValueError("Standalone scripts may not import the project package.")
    return source
