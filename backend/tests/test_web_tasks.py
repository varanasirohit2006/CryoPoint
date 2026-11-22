"""General browser intentions are scoped to their selected website and observed completion."""

import pytest

from jit_ui.config import Settings
from jit_ui.domain.models import ActionSpec, ConditionSpec, LocatorSpec, ProviderResponse, RunRecord, RunStatus, TaskRequest
from jit_ui.domain.sites import public_sites, task_site, validate_task
from jit_ui.domain.web_tasks import normalize_target_url, parameter_specs, resolve_web_request, web_intent_key


def task(text, **kwargs):
    return TaskRequest(site_id="web", task=text, **kwargs)


@pytest.mark.parametrize("text,url,goal,parameters", [
    ("Go to google and search for Python tutorials", "https://www.google.com/", "web_search", {"query": "Python tutorials"}),
    ("Open goole and search for cricket scores.", "https://www.google.com/", "web_search", {"query": "cricket scores"}),
    ("Search for pandas documentation on Google", "https://www.google.com/", "web_search", {"query": "pandas documentation"}),
    ("Search for pandas documentation on Google.", "https://www.google.com/", "web_search", {"query": "pandas documentation"}),
    ("Open YouTube and search for chess openings", "https://www.youtube.com/", "web_search", {"query": "chess openings"}),
    ("Go to https://example.org/catalog and search for red shoes", "https://example.org/catalog", "web_search", {"query": "red shoes"}),
    ("Visit docs.example.org and expand the installation section", "https://docs.example.org/", "browser_task", {}),
    ("Change the language on Amazon to Hindi", "https://www.amazon.in/", "change_language", {"language": "hi"}),
    ("Switch Amazon to Hindi", "https://www.amazon.in/", "change_language", {"language": "hi"}),
    ("Change Google language from Hindi to English", "https://www.google.com/", "change_language", {"language": "en"}),
    ("Switch language to हिंदी on Amazon", "https://www.amazon.in/", "change_language", {"language": "hi"}),
])
def test_infers_site_and_intention_without_fixed_action_sequences(text, url, goal, parameters):
    resolved = resolve_web_request(task(text))
    assert resolved.target_url == url
    assert resolved.goal_id == goal
    assert resolved.parameters == parameters
    assert resolved.intent_key == web_intent_key(resolved)


@pytest.mark.parametrize("control,url", [("Electronics", "https://www.amazon.in/"), ("Books", "https://example.org/"), ("Account details", "https://another.example/")])
def test_named_control_tasks_require_real_activation_without_an_action_plan(control, url):
    resolved = resolve_web_request(task('Go to the website and press the "' + control + '" button .', target_url=url))
    assert resolved.parameters["control_name"] == control.casefold()
    assert resolved.completion_conditions[0].kind == "control_activated"
    assert resolved.completion_conditions[0].value_ref == "control_name"


def test_control_aliases_share_a_process_but_compound_tasks_keep_their_full_intent():
    first = resolve_web_request(task("Go to Amazon and press the Electronics button."))
    alias = resolve_web_request(task("Open Amazon and click the electronics link"))
    assert first.intent_key == alias.intent_key and first.parameters == alias.parameters
    compound = resolve_web_request(task("Read the article and then press the Electronics button", target_url="https://example.org/"))
    assert "control_name" not in compound.parameters and not compound.completion_conditions


def test_arbitrary_site_generic_parameters_and_completion_conditions_survive():
    original = task("Expand the history section and choose the date range", target_url="https://custom.example/app",
        parameter_source="explicit", parameters={"query": "audit", "days": 7, "include_archived": True,
                                                 "filters": ["sent", "received"], "options": {"sort": "date"}},
        completion_conditions=[ConditionSpec(kind="visible", locator=LocatorSpec(strategy="testid", value="history"))])
    resolved = validate_task(original, Settings(_env_file=None))
    assert resolved.goal_id == "browser_task"
    assert resolved.task == original.task
    assert resolved.parameters == original.parameters
    site = task_site(resolved, Settings(_env_file=None))
    assert site["allowed_origin"] == "https://custom.example"
    assert site["allowed_origins"] == ["https://custom.example"]
    assert site["completion_conditions"][0]["kind"] == "visible"
    assert site["parameters"]["filters"].type == "array"
    assert site["parameters"]["options"].type == "object"
    assert site["parameters"]["days"].type == "integer"
    assert site["parameters"]["include_archived"].type == "boolean"


def test_search_followed_by_another_operation_stays_generic():
    resolved = resolve_web_request(task("Search for blue jackets on https://shop.example and open the first result"))
    assert resolved.goal_id == "browser_task"
    assert resolved.parameters == {"query": "blue jackets"}
    manual = resolve_web_request(task("Search for blue jackets and open the first result", target_url="https://shop.example",
                                      parameter_source="explicit", parameters={"query": "blue jackets"}))
    assert manual.goal_id == "browser_task" and manual.parameters == {"query": "blue jackets"}


@pytest.mark.parametrize("goal", ["browser_task", "change_language"])
def test_compound_language_and_search_keep_the_entire_requested_operation(goal):
    text = "Go to Amazon and change language to Hindi and search for toys"
    resolved = validate_task(task(text, goal_id=goal), Settings(_env_file=None))
    assert resolved.site_id == "web" and resolved.goal_id == "browser_task"
    assert resolved.task == text
    assert resolved.parameters == {"language": "hi", "query": "toys"}


def test_compound_search_language_and_purchase_remain_generic_with_manual_values():
    text = "Go to Amazon and search for toys and change language to Hindi and purchase the first item"
    resolved = validate_task(task(text, parameter_source="explicit", parameters={"query": "dolls", "language": "en"}), Settings(_env_file=None))
    assert resolved.site_id == "web" and resolved.goal_id == "browser_task"
    assert resolved.task == text
    assert resolved.parameters == {"query": "dolls", "language": "en"}


def test_any_website_atomic_amazon_budget_search_uses_registered_shopping_goal():
    resolved = validate_task(task("Go to Amazon search toys under 500"), Settings(_env_file=None))
    assert resolved.site_id == "amazon_in" and resolved.goal_id == "search_products"
    assert resolved.parameters == {"query": "toys", "max_price": 500}
    manual = validate_task(task("Go to Amazon and search toys under 500", parameter_source="explicit",
                                parameters={"query": "dolls", "min_price": 100, "max_price": 700}), Settings(_env_file=None))
    assert manual.parameters == {"query": "dolls", "min_price": 100, "max_price": 700}
    explicit_url = validate_task(task("Go to https://www.amazon.in and search for toys under 500"), Settings(_env_file=None))
    assert explicit_url.site_id == "amazon_in"
    assert explicit_url.parameters == {"query": "toys", "max_price": 500}


def test_amazon_search_with_extra_manual_parameters_stays_generic_and_retains_them():
    resolved = validate_task(task("Go to Amazon and search for toys", parameter_source="explicit",
                                 parameters={"query": "dolls", "brand": "Example Brand"}), Settings(_env_file=None))
    assert resolved.site_id == "web" and resolved.goal_id == "browser_task"
    assert resolved.parameters == {"query": "dolls", "brand": "Example Brand"}


@pytest.mark.parametrize("operation", ["buy", "book", "reserve", "pay", "confirm", "checkout", "open"])
def test_any_website_search_followed_by_another_operation_is_not_native(operation):
    text = f"Go to Amazon and search for toys under 500 and {operation} the first item"
    resolved = validate_task(task(text), Settings(_env_file=None))
    assert resolved.site_id == "web" and resolved.goal_id == "browser_task"
    assert resolved.task == text


@pytest.mark.parametrize("url", ["https://example.org/search?q=widgets", "https://example.org/settings/change-language"])
def test_url_path_words_do_not_become_the_requested_operation(url):
    resolved = resolve_web_request(task("Open " + url))
    assert resolved.goal_id == "browser_task"


def test_search_query_can_itself_contain_a_url():
    resolved = resolve_web_request(task("Search for https://docs.example.org/reference", target_url="https://www.google.com"))
    assert resolved.goal_id == "web_search"
    assert resolved.parameters == {"query": "https://docs.example.org/reference"}


def test_amazon_language_instruction_redirects_before_product_search_parsing():
    original = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Change Amazon language to Hindi",
                           parameters={"query": "shoes", "max_price": 500})
    resolved = validate_task(original, Settings(_env_file=None))
    assert resolved.site_id == "web" and resolved.goal_id == "change_language"
    assert resolved.parameters == {"language": "hi"}


def test_manual_search_and_language_values_override_descriptive_text():
    search = resolve_web_request(task("Search Google for stale terms", parameter_source="explicit", parameters={"query": "  new   keywords  "}))
    assert search.parameters == {"query": "new keywords"}
    language = resolve_web_request(task("Change Google language to Hindi", parameter_source="explicit", parameters={"language": "EN"}))
    assert language.parameters == {"language": "en"}


@pytest.mark.parametrize("value", ["https://", "ftp://example.org", "javascript:alert(1)", "file:///C:/Windows",
                                   "https://username:password@example.org", "https://example.org:99999", "https://bad host.org"])
def test_invalid_entry_urls_are_rejected(value):
    with pytest.raises(ValueError):
        resolve_web_request(task("Open this page", target_url=value))


def test_url_normalization_retains_navigation_data_and_explicit_local_testbeds():
    assert normalize_target_url("HTTPS://Example.org:443/path?q=blue#section") == "https://example.org/path?q=blue#section"
    local = resolve_web_request(task("Open the settings panel", target_url="localhost:3000/settings", provider="fixture"))
    assert local.target_url == "http://localhost:3000/settings"
    with pytest.raises(ValueError, match="localhost"):
        resolve_web_request(task("Open the settings panel", target_url="https://example.org", provider="fixture"))


def test_generic_intent_key_reuses_parameters_but_keeps_site_and_goal_separate():
    first = resolve_web_request(task("Open invoice 1042", target_url="https://portal.example", parameters={"invoice_id": "1042"}))
    second = resolve_web_request(task("Open invoice 2089", target_url="https://portal.example", parameters={"invoice_id": "2089"}))
    assert first.intent_key == second.intent_key
    other = resolve_web_request(task("Open invoice 1042", target_url="https://other.example", parameters={"invoice_id": "1042"}))
    assert first.intent_key != other.intent_key
    changed_operation = resolve_web_request(task("Delete invoice 1042", target_url="https://portal.example", parameters={"invoice_id": "1042"}))
    assert first.intent_key != changed_operation.intent_key


def test_general_contracts_and_public_site_acceptance():
    conditions = [ConditionSpec(kind="language_equals", expected="hi"),
                  ConditionSpec(kind="title_contains", expected="Settings"),
                  ConditionSpec(kind="url_query_equals", attribute="q", value_ref="query")]
    assert ProviderResponse(done=True, completion_conditions=conditions).completion_conditions == conditions
    run = RunRecord(request=task("Open https://example.org"), status=RunStatus.AWAITING_CONFIRMATION,
                    pending_confirmation={"action": "purchase", "description": "Buy selected item"})
    assert run.pending_confirmation["action"] == "purchase"
    assert ActionSpec(action="click", requires_confirmation=True).model_dump()["requires_confirmation"] is True
    assert any(site["site_id"] == "web" for site in public_sites(Settings(_env_file=None)))
    assert parameter_specs({"optional": None})["optional"].required is False


def test_completion_conditions_validate_references_and_do_not_recurse_into_goal():
    for condition in (ConditionSpec(kind="goal", expected="browser_task"),
                      ConditionSpec(kind="url_query_equals", expected="anything"),
                      ConditionSpec(kind="language_equals", value_ref="missing")):
        with pytest.raises(ValueError):
            resolve_web_request(task("Open https://example.org", completion_conditions=[condition]))
