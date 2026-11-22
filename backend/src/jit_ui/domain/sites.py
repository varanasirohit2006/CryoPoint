"""Registered goals define outcomes, never prewritten browser action sequences."""

from urllib.parse import urlsplit

from jit_ui.config import Settings
from jit_ui.domain.models import ParameterSpec, TaskRequest
from jit_ui.routing import validate_parameters
from jit_ui.domain.intent import resolve_amazon_request
from jit_ui.domain.web_tasks import (
    infer_target_url,
    is_language_request,
    normalize_target_url,
    resolve_web_request,
    task_site,
)


def site_definitions(settings: Settings) -> dict[str, dict]:
    base = settings.testbed_url.rstrip("/")
    return {
        "shop": {"site_id": "shop", "label": "Chaos Shop", "start_url": base + "/shop",
                 "allowed_origin": base, "goal_id": "search_products", "goal_label": "Search, filter and sort",
                 "parameters": {
                     "product_name": ParameterSpec(description="Product keyword"),
                     "max_price": ParameterSpec(type="number", required=False, default=5000, minimum=0,
                                                description="Maximum price in INR"),
                     "sort_by": ParameterSpec(required=False, default="rating_desc",
                                              enum=["rating_desc", "price_asc"], description="Result ordering"),
                 }},
        "admin": {"site_id": "admin", "label": "Dispatch Portal", "start_url": base + "/admin",
                  "allowed_origin": base, "goal_id": "ship_order", "goal_label": "Find and ship an order",
                  "parameters": {"order_id": ParameterSpec(description="Fixture order number")},
                  "credentials": {"username": settings.fixture_username, "password": settings.fixture_password},
                  "credential_env": {"username": "JIT_FIXTURE_USERNAME", "password": "JIT_FIXTURE_PASSWORD"}},
        "amazon_in": {"site_id": "amazon_in", "label": "Amazon India", "start_url": "https://www.amazon.in/",
                      "allowed_origin": "https://www.amazon.in", "goal_id": "search_products",
                      "goal_label": "Search, filter and browse products", "parameters": {
                          "query": ParameterSpec(description="Search keywords, including age, gender or product details"),
                          "min_price": ParameterSpec(type="number", required=False, minimum=0, description="Minimum price in INR"),
                          "max_price": ParameterSpec(type="number", required=False, minimum=0, description="Maximum price in INR"),
                          "sort_by": ParameterSpec(required=False, enum=["relevanceblender", "price-asc-rank", "price-desc-rank", "review-rank", "date-desc-rank"], description="Amazon result ordering"),
                          "min_rating": ParameterSpec(type="number", required=False, minimum=1, maximum=5, description="Minimum customer stars"),
                      }},
    }


def public_sites(settings: Settings) -> list[dict]:
    result = []
    for site in site_definitions(settings).values():
        result.append({"site_id": site["site_id"], "label": site["label"], "start_url": site["start_url"],
                       "goals": [{"goal_id": site["goal_id"], "label": site["goal_label"],
                                  "parameters": {k: v.model_dump(mode="json") for k, v in site["parameters"].items()}}]})
    result.append({"site_id": "web", "label": "Any website", "start_url": "",
                   "goals": [{"goal_id": "browser_task", "label": "Describe any browser task", "parameters": {}}]})
    return result


def _has_external_web_target(task: TaskRequest) -> bool:
    if task.site_id == "web":
        return True
    if task.target_url:
        try:
            target = normalize_target_url(task.target_url)
            host = (urlsplit(target).hostname or "").lower()
            return host not in {"amazon.in", "www.amazon.in", "localhost", "127.0.0.1"}
        except ValueError:
            return False
    try:
        target = infer_target_url(task)
        host = (urlsplit(target).hostname or "").lower()
        return host not in {"amazon.in", "www.amazon.in", "localhost", "127.0.0.1"}
    except ValueError:
        return False


def validate_task(task: TaskRequest, settings: Settings) -> TaskRequest:
    if task.site_id == "web" or _has_external_web_target(task) or (task.site_id == "amazon_in" and is_language_request(task)):
        resolved = resolve_web_request(task)
        return validate_task(resolved, settings) if resolved.site_id == "amazon_in" else resolved
    if task.site_id == "amazon_in":
        task = resolve_amazon_request(task)
    site = task_site(task, settings)
    if task.goal_id != site["goal_id"]:
        raise ValueError("This goal is not registered for the selected site.")
    if task.provider == "fixture" and task.site_id == "amazon_in":
        raise ValueError("Offline simulations are supported only on local testbeds.")
    if not task.compile_workflow and task.mode != "cold":
        raise ValueError("Compilation can be disabled only for forced cold reference runs.")
    parameters = validate_parameters(task.parameters, site["parameters"])
    if task.site_id == "amazon_in":
        parameters = {key: value for key, value in parameters.items() if value is not None}
        if parameters.get("min_price", 0) > parameters.get("max_price", float("inf")):
            raise ValueError("The minimum price must not exceed the maximum price.")
    for value in parameters.values():
        if isinstance(value, str) and (not value.strip() or len(value) > 200):
            raise ValueError("Text parameters must contain 1–200 characters.")
    return task.model_copy(update={"parameters": parameters})
