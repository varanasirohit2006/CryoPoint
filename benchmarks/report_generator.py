"""Summarize recorded trials without treating simulated decisions as AI evidence."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def summarize(suite: dict[str, Any]) -> dict[str, Any]:
    rows = suite.get("trials", [])
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["site_id"], row["method"])].append(row)
    groups = []
    for (site, method), items in sorted(grouped.items()):
        successful = [item for item in items if item["status"] == "SUCCEEDED"]
        durations = [float(item["duration_ms"]) for item in successful]
        groups.append({
            "site_id": site, "method": method, "trials": len(items), "successful": len(successful),
            "completion_rate": len(successful) / len(items),
            "median_ms": statistics.median(durations) if durations else None,
            "p95_ms": percentile(durations, .95),
            "model_calls": sum(item.get("model_calls", 0) for item in items),
            "input_tokens": sum(item.get("input_tokens", 0) for item in items),
            "output_tokens": sum(item.get("output_tokens", 0) for item in items),
            "estimated_tokens_saved": (sum(item.get("estimated_tokens_saved") or 0 for item in items)
                                       if any(item.get("estimated_tokens_saved") is not None for item in items) else None),
            "comparable_runs": sum(item.get("estimated_tokens_saved") is not None for item in items),
            "incomplete_usage_runs": sum(item.get("token_usage_complete") is False for item in items),
        })
    mutated = [row for row in rows if row["method"] == "jit_mutation"]
    healed = [row for row in mutated if row["status"] == "SUCCEEDED" and row.get("healed_steps", 0) > 0]
    clean_hot = [row for row in rows if row["method"] in ("jit_hot", "post_repair_hot") and row["status"] == "SUCCEEDED"]
    speedups = []
    if not suite.get("simulated", False):
        for site in sorted({row["site_id"] for row in rows}):
            cold = [row["duration_ms"] for row in rows if row["site_id"] == site and row["method"] == "jit_cold" and row["status"] == "SUCCEEDED"]
            hot = [row["duration_ms"] for row in rows if row["site_id"] == site and row["method"] == "jit_hot" and row["status"] == "SUCCEEDED"]
            if cold and hot and statistics.median(hot) > 0:
                speedups.append({"site_id": site, "ratio": statistics.median(cold) / statistics.median(hot), "cold_samples": len(cold), "hot_samples": len(hot)})
    required = [row for row in rows if row["method"].startswith("jit_") or row["method"] == "post_repair_hot"]
    return {
        "groups": groups,
        "mutation_trials": len(mutated),
        "mutation_successes": sum(row["status"] == "SUCCEEDED" for row in mutated),
        "mutation_completion_rate": sum(row["status"] == "SUCCEEDED" for row in mutated) / len(mutated) if mutated else None,
        "successful_trials_with_healing": len(healed),
        "healed_steps": sum(row.get("healed_steps", 0) for row in mutated),
        "successful_hot_runs": len(clean_hot),
        "successful_hot_runs_without_model_calls": sum(row.get("model_calls", 0) == 0 for row in clean_hot),
        "cold_hot_speedups": speedups,
        "required_trials_successful": bool(required) and all(row["status"] == "SUCCEEDED" for row in required),
        "failed_trials": sum(row["status"] not in ("SUCCEEDED", "SKIPPED") for row in rows),
        "skipped_trials": sum(row["status"] == "SKIPPED" for row in rows),
    }


def _text(value: Any) -> str:
    return html.escape(str(value), quote=True)


def chart_svg(groups: list[dict[str, Any]], title: str) -> str:
    visible = [group for group in groups if group["median_ms"] is not None]
    height = 85 + max(1, len(visible)) * 43
    maximum = max([group["median_ms"] for group in visible], default=1)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {height}" role="img" aria-labelledby="title"><title id="title">{_text(title)}</title><rect width="1000" height="{height}" fill="#151c19" rx="10"/><text x="28" y="32" fill="#d5e5c9" font-family="Arial,sans-serif" font-size="16">{_text(title)}</text><text x="28" y="52" fill="#8eaa7a" font-family="Arial,sans-serif" font-size="11">Successful-trial median; failures remain in the table below. Lower is faster.</text>']
    if not visible:
        parts.append('<text x="28" y="100" fill="#8eaa7a" font-family="Arial,sans-serif" font-size="13">No successful trials to plot.</text>')
    for index, group in enumerate(visible):
        y = 78 + index * 43
        width = max(2, group["median_ms"] / maximum * 510)
        color = "#b5d793" if group["method"] in ("jit_hot", "post_repair_hot") else "#9eaec5" if group["method"] == "always_vision" else "#b7a370"
        label = f'{group["site_id"]} / {group["method"]}'
        parts.append(f'<text x="28" y="{y + 16}" fill="#becfb1" font-family="Arial,sans-serif" font-size="11">{_text(label)}</text><rect x="258" y="{y}" width="{width:.2f}" height="23" rx="3" fill="{color}"/><text x="{275 + width:.2f}" y="{y + 16}" fill="#c8dab8" font-family="Arial,sans-serif" font-size="11">{group["median_ms"] / 1000:.2f}s · {group["successful"]}/{group["trials"]} completed</text>')
    return "".join(parts) + "</svg>"


def write_reports(suite: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suite["summary"] = summarize(suite)
    identifier = suite["suite_id"]
    paths = {format_: output_dir / f"{identifier}.{format_}" for format_ in ("json", "csv", "html", "svg")}
    paths["json"].write_text(json.dumps(suite, indent=2, ensure_ascii=False), encoding="utf-8")
    columns = ["trial_id", "phase", "site_id", "method", "mutation", "seed", "provider", "model", "execution_strategy", "simulated", "status", "duration_ms", "client_duration_ms", "model_duration_ms", "embedding_duration_ms", "model_calls", "input_tokens", "output_tokens", "token_usage_complete", "baseline_run_id", "baseline_model", "baseline_tokens", "reference_tokens", "estimated_tokens_saved", "automation_id", "estimated_cost_usd", "actions", "healed_steps", "run_id", "workflow_id", "workflow_version", "error_code", "error"]
    with paths["csv"].open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(suite["trials"])
    title = "Simulation timings — not AI performance evidence" if suite.get("simulated") else "Recorded execution durations"
    svg = chart_svg(suite["summary"]["groups"], title)
    paths["svg"].write_text(svg, encoding="utf-8")
    group_rows = "".join(f'<tr><td>{_text(group["site_id"])}</td><td>{_text(group["method"])}</td><td>{group["successful"]}/{group["trials"]}</td><td>{"—" if group["median_ms"] is None else f"{group['median_ms'] / 1000:.2f}s"}</td><td>{"—" if group["p95_ms"] is None else f"{group['p95_ms'] / 1000:.2f}s"}</td><td>{group["model_calls"]}</td><td>{group["input_tokens"] + group["output_tokens"]}</td></tr>' for group in suite["summary"]["groups"])
    failed_rows = [row for row in suite["trials"] if row["status"] != "SUCCEEDED"]
    failures = "".join(f'<tr><td>{_text(row["trial_id"])}</td><td>{_text(row["method"])}</td><td>{_text(row["status"])}</td><td>{_text(row.get("error") or "")}</td></tr>' for row in failed_rows)
    banner = "Offline simulation: browser actions are real, but decisions are scripted. No AI speedup or recovery claim follows from these measurements." if suite.get("simulated") else "Real-model evaluation: these are measured outcomes, not guaranteed performance targets."
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>JIT UI evaluation · {_text(identifier)}</title><style>body{{margin:0;background:#101714;color:#c8d9bd;font:14px/1.65 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:40px 28px}}h1{{font-size:31px;letter-spacing:-1px;font-weight:500}}h2{{font-size:18px;font-weight:500;margin-top:30px}}.badge{{padding:12px 16px;border:1px solid #536346;border-radius:7px;background:#22301c;color:#bbd4a2}}p{{color:#8fa87b}}svg{{width:100%;height:auto;margin:20px 0}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #2d3f24;vertical-align:top}}th{{color:#91ad79}}pre{{white-space:pre-wrap;word-break:break-word;padding:18px;background:#151f12;border-radius:7px;font-size:11px;color:#91af77}}a{{color:#bedc9d}}.scroll{{overflow:auto}}</style></head><body><main><p>JIT UI / REPRODUCIBLE EVALUATION</p><h1>{_text(identifier)}</h1><div class="badge">{_text(banner)}</div>{svg}<h2>All methods</h2><div class="scroll"><table><thead><tr><th>Site</th><th>Method</th><th>Completed</th><th>Median</th><th>p95</th><th>Model calls</th><th>Tokens</th></tr></thead><tbody>{group_rows}</tbody></table></div><h2>Summary</h2><pre>{_text(json.dumps(suite["summary"], indent=2))}</pre><h2>Failures and skipped trials</h2><div class="scroll"><table><thead><tr><th>Trial</th><th>Method</th><th>Status</th><th>Reason</th></tr></thead><tbody>{failures or '<tr><td colspan="4">None recorded.</td></tr>'}</tbody></table></div><h2>Methodology and environment</h2><pre>{_text(json.dumps({"methodology": suite.get("methodology"), "environment": suite.get("environment"), "configuration": suite.get("configuration")}, indent=2))}</pre><p>Raw files: <a href="{_text(paths['json'].name)}">JSON</a> · <a href="{_text(paths['csv'].name)}">CSV</a> · <a href="{_text(paths['svg'].name)}">SVG</a></p></main></body></html>'''
    paths["html"].write_text(document, encoding="utf-8")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Existing raw JSON suite report")
    parser.add_argument("--output-dir", type=Path)
    arguments = parser.parse_args()
    suite = json.loads(arguments.report.read_text(encoding="utf-8"))
    paths = write_reports(suite, arguments.output_dir or arguments.report.parent)
    print(paths["html"])


if __name__ == "__main__":
    main()
