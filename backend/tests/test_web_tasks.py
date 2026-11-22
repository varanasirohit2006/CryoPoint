"""General browser intentions are scoped to their selected website and observed completion."""

import pytest

from jit_ui.config import Settings
from jit_ui.domain.models import ActionSpec, ConditionSpec, LocatorSpec, ProviderResponse, RunRecord, RunStatus, TaskRequest
from jit_ui.domain.sites import public_sites, task_site, validate_task
from jit_ui.domain.web_tasks import needs_model_interpretation, normalize_target_url, parameter_specs, resolve_web_request, web_intent_key


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


def test_compound_converter_and_upload_task_resolves_search_query_and_website():
    text = (
        "in thehttps://www.ilovepdf.com/pdf_to_word search for i love pdf pdf to word converter "
        "and convert the pdf to word . use the upload to upload dummy_invoice pdf in the downloads dir "
        "and convert this pdf to  word .after this download the output word and save it in downloads.  please fix it ."
    )
    resolved = resolve_web_request(task(text))
    assert resolved.target_url == "https://www.ilovepdf.com/pdf_to_word"
    assert resolved.goal_id == "browser_task"
    assert resolved.parameters == {"query": "i love pdf pdf to word converter"}
    assert "<query>" in resolved.intent_key
    assert "i love pdf pdf to word converter" not in resolved.intent_key
    assert needs_model_interpretation(resolved)


def test_glued_www_target_url_resolves_cleanly():
    text = "in thewww.ilovepdf.com/pdf_to_word search for pdf to word and convert the document"
    resolved = resolve_web_request(task(text))
    assert resolved.target_url == "https://www.ilovepdf.com/pdf_to_word"
    assert resolved.parameters == {"query": "pdf to word"}
    assert resolved.goal_id == "browser_task"


def test_glued_preposition_domain_resolves_cleanly():
    text = "in theilovepdf.com/pdf_to_word search for pdf to word and convert the document"
    resolved = resolve_web_request(task(text))
    assert resolved.target_url == "https://ilovepdf.com/pdf_to_word"
    assert resolved.parameters == {"query": "pdf to word"}
    assert resolved.goal_id == "browser_task"


def test_validate_task_reroutes_external_url_from_other_presets():
    text = (
        "in thehttps://www.ilovepdf.com/pdf_to_word search for i love pdf pdf to word converter "
        "and convert the pdf to word . use the upload to upload dummy_invoice pdf in the downloads dir "
        "and convert this pdf to  word .after this download the output word and save it in downloads.  please fix it ."
    )
    settings = Settings(_env_file=None)
    for site_id in ("amazon_in", "shop"):
        validated = validate_task(TaskRequest(site_id=site_id, task=text), settings)
        assert validated.site_id == "web"
        assert validated.target_url == "https://www.ilovepdf.com/pdf_to_word"
        assert validated.goal_id == "browser_task"
        assert validated.parameters == {"query": "i love pdf pdf to word converter"}
        assert needs_model_interpretation(validated)


def test_compound_task_without_search_needs_model_interpretation():
    text = (
        "in thehttps://www.ilovepdf.com/pdf_to_word convert the pdf to word . use the upload to upload dummy_invoice pdf "
        "in the downloads dir and convert this pdf to word .after this download the output word and save it in downloads."
    )
    resolved = resolve_web_request(task(text))
    assert resolved.target_url == "https://www.ilovepdf.com/pdf_to_word"
    assert resolved.goal_id == "browser_task"
    assert "query" not in resolved.parameters
    assert needs_model_interpretation(resolved)


@pytest.mark.parametrize("typo_url,expected_url", [
    ("youtbe.com", "https://youtube.com/"),
    ("https://youtbe.com/", "https://youtube.com/"),
    ("www.youtbe.com", "https://www.youtube.com/"),
    ("https://www.youtbe.com/", "https://www.youtube.com/"),
    ("yotube.com", "https://youtube.com/"),
    ("uoutube.com", "https://youtube.com/"),
    ("youtub.com", "https://youtube.com/"),
    ("goole.com", "https://google.com/"),
    ("gogle.com", "https://google.com/"),
    ("gooogle.com", "https://google.com/"),
    ("www.goole.com", "https://www.google.com/"),
    ("amzon.com", "https://amazon.com/"),
    ("amazn.com", "https://amazon.com/"),
    ("amazone.com", "https://amazon.com/"),
    ("amzon.in", "https://amazon.in/"),
    ("amazn.in", "https://amazon.in/"),
    ("amazone.in", "https://amazon.in/"),
    ("www.amzon.in", "https://www.amazon.in/"),
    ("flikart.com", "https://flipkart.com/"),
    ("www.flikart.com", "https://www.flipkart.com/"),
    ("githb.com", "https://github.com/"),
    ("www.githb.com", "https://www.github.com/"),
    ("wikpedia.org", "https://wikipedia.org/"),
    ("www.wikpedia.org", "https://www.wikipedia.org/"),
    ("https://youtbe.com/watch?v=123", "https://youtube.com/watch?v=123"),
    ("http://youtbe.com", "http://youtube.com/"),
    ("http://www.youtbe.com", "http://www.youtube.com/"),
    ("youtbe.com:8080", "https://youtube.com:8080/"),
    ("m.youtbe.com", "https://m.youtube.com/"),
    ("music.youtbe.com", "https://music.youtube.com/"),
    ("en.wikpedia.org", "https://en.wikipedia.org/"),
    ("raw.githb.com", "https://raw.github.com/"),
])
def test_domain_typo_correction_in_normalize_target_url(typo_url, expected_url):
    assert normalize_target_url(typo_url) == expected_url


@pytest.mark.parametrize("prompt,expected_url,expected_query", [
    ("Open youtbe.com and search for songs", "https://youtube.com/", "songs"),
    ("Search for cat videos on https://youtbe.com/", "https://youtube.com/", "cat videos"),
    ("Search for cat videos in youtbe.com", "https://youtube.com/", "cat videos"),
    ("Go to youtbe and search for jazz", "https://www.youtube.com/", "jazz"),
    ("Search for laptops on flikart.com", "https://flipkart.com/", "laptops"),
    ("Open githb.com and look for python", "https://github.com/", "python"),
    ("Search for physics on wikpedia.org", "https://wikipedia.org/", "physics"),
    ("Search for shoes on amzon.in", "https://www.amazon.in/", "shoes"),
    ("Search for shoes at amzon.in", "https://www.amazon.in/", "shoes"),
    ("Search for shoes on amzon.com", "https://amazon.com/", "shoes"),
    ("Search for news on goole.com", "https://google.com/", "news"),
])
def test_infer_target_url_and_query_with_typo_domains(prompt, expected_url, expected_query):
    resolved = resolve_web_request(task(prompt))
    assert resolved.target_url == expected_url
    assert resolved.parameters.get("query") == expected_query


@pytest.mark.asyncio
async def test_apply_action_dns_error_raises_clean_step_execution_error():
    from unittest.mock import AsyncMock, MagicMock
    from jit_ui.runtime.browser_helpers import StepExecutionError, apply_action

    mock_page = MagicMock()
    mock_page.url = "about:blank"
    mock_page.title = AsyncMock(return_value="")
    mock_page.locator = MagicMock(return_value=MagicMock(inner_text=AsyncMock(return_value=""), first=MagicMock(wait_for=AsyncMock())))
    mock_page.goto = AsyncMock(side_effect=Exception(
        "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://unknown-nonexistent-domain.com/ Call log: - navigating..."
    ))
    site = {"site_id": "web", "start_url": "https://unknown-nonexistent-domain.com/", "allowed_origin": "https://unknown-nonexistent-domain.com"}
    action = {"action": "goto", "url": "https://unknown-nonexistent-domain.com/"}

    with pytest.raises(StepExecutionError) as exc_info:
        await apply_action(mock_page, action, {}, site)

    assert "Unable to resolve domain 'unknown-nonexistent-domain.com' (net::ERR_NAME_NOT_RESOLVED). Check the URL spelling and your internet connection." in str(exc_info.value)


@pytest.mark.asyncio
async def test_apply_action_typo_domain_attempts_correction_and_recovers():
    from unittest.mock import AsyncMock, MagicMock
    from jit_ui.runtime.browser_helpers import apply_action

    mock_page = MagicMock()
    mock_page.url = "about:blank"
    mock_page.title = AsyncMock(return_value="")
    mock_page.locator = MagicMock(return_value=MagicMock(inner_text=AsyncMock(return_value=""), first=MagicMock(wait_for=AsyncMock())))
    success_response = MagicMock(status=200)
    mock_page.goto = AsyncMock(side_effect=[
        Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://youtbe.com/"),
        success_response,
    ])
    site = {"site_id": "web", "start_url": "https://youtbe.com/", "allowed_origin": "https://youtbe.com"}
    action = {"action": "goto", "url": "https://youtbe.com/"}

    await apply_action(mock_page, action, {}, site)
    assert mock_page.goto.call_count == 2
    assert mock_page.goto.call_args_list[1][0][0] == "https://youtube.com/"
    assert action["url"] == "https://youtube.com/"
    assert site["_document_status"] == 200


@pytest.mark.asyncio
async def test_apply_action_typo_domain_fails_if_corrected_also_fails():
    from unittest.mock import AsyncMock, MagicMock
    from jit_ui.runtime.browser_helpers import StepExecutionError, apply_action

    mock_page = MagicMock()
    mock_page.url = "about:blank"
    mock_page.title = AsyncMock(return_value="")
    mock_page.locator = MagicMock(return_value=MagicMock(inner_text=AsyncMock(return_value=""), first=MagicMock(wait_for=AsyncMock())))
    mock_page.goto = AsyncMock(side_effect=[
        Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://youtbe.com/"),
        Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://youtube.com/"),
    ])
    site = {"site_id": "web", "start_url": "https://youtbe.com/", "allowed_origin": "https://youtbe.com"}
    action = {"action": "goto", "url": "https://youtbe.com/"}

    with pytest.raises(StepExecutionError) as exc_info:
        await apply_action(mock_page, action, {}, site)

    assert "Unable to resolve domain 'youtbe.com' (net::ERR_NAME_NOT_RESOLVED). Check the URL spelling and your internet connection." in str(exc_info.value)


@pytest.mark.asyncio
async def test_browser_session_open_dns_error_raises_clean_browser_action_error(tmp_path):
    from unittest.mock import AsyncMock, MagicMock, patch
    from jit_ui.browser.session import BrowserActionError, BrowserSession

    site = {"site_id": "web", "start_url": "https://nonexistent-site-test.com/", "allowed_origin": "https://nonexistent-site-test.com"}
    session = BrowserSession(Settings(_env_file=None), tmp_path, site)

    mock_page = MagicMock()
    mock_page.url = "about:blank"
    mock_page.title = AsyncMock(return_value="")
    mock_page.goto = AsyncMock(side_effect=Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://nonexistent-site-test.com/"))
    mock_page.on = MagicMock()
    mock_page.set_default_timeout = MagicMock()

    with patch("playwright.async_api.async_playwright") as mock_pw:
        pw_instance = AsyncMock()
        pw_instance.stop = AsyncMock()
        mock_pw.return_value.start = AsyncMock(return_value=pw_instance)
        browser = AsyncMock()
        browser.close = AsyncMock()
        pw_instance.chromium.launch = AsyncMock(return_value=browser)
        context = AsyncMock()
        browser.new_context = AsyncMock(return_value=context)
        context.new_page = AsyncMock(return_value=mock_page)
        cdp = MagicMock()
        cdp.on = MagicMock()
        cdp.detach = AsyncMock()
        cdp.send = AsyncMock()
        context.new_cdp_session = AsyncMock(return_value=cdp)

        with pytest.raises(BrowserActionError) as exc_info:
            await session.open()

        assert "Unable to resolve domain 'nonexistent-site-test.com' (net::ERR_NAME_NOT_RESOLVED). Check the URL spelling and your internet connection." in str(exc_info.value)


@pytest.mark.asyncio
async def test_browser_session_open_typo_domain_attempts_correction_and_recovers(tmp_path):
    from unittest.mock import AsyncMock, MagicMock, patch
    from jit_ui.browser.session import BrowserSession

    site = {"site_id": "web", "start_url": "https://youtbe.com/", "allowed_origin": "https://youtbe.com"}
    session = BrowserSession(Settings(_env_file=None), tmp_path, site)

    mock_page = MagicMock()
    mock_page.url = "about:blank"
    mock_page.title = AsyncMock(return_value="")
    mock_page.goto = AsyncMock(side_effect=[
        Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://youtbe.com/"),
        MagicMock(status=200),
    ])
    mock_page.on = MagicMock()
    mock_page.set_default_timeout = MagicMock()

    with patch("playwright.async_api.async_playwright") as mock_pw:
        pw_instance = AsyncMock()
        pw_instance.stop = AsyncMock()
        mock_pw.return_value.start = AsyncMock(return_value=pw_instance)
        browser = AsyncMock()
        browser.close = AsyncMock()
        pw_instance.chromium.launch = AsyncMock(return_value=browser)
        context = AsyncMock()
        browser.new_context = AsyncMock(return_value=context)
        context.new_page = AsyncMock(return_value=mock_page)
        cdp = MagicMock()
        cdp.on = MagicMock()
        cdp.detach = AsyncMock()
        cdp.send = AsyncMock()
        context.new_cdp_session = AsyncMock(return_value=cdp)

        await session.open()

        assert mock_page.goto.call_count == 2
        assert mock_page.goto.call_args_list[1][0][0] == "https://youtube.com/"
        assert session.site["start_url"] == "https://youtube.com/"
        assert session.site["allowed_origin"] == "https://youtube.com"
        await session.close()


@pytest.mark.asyncio
async def test_browser_session_open_typo_domain_fails_if_corrected_also_fails(tmp_path):
    from unittest.mock import AsyncMock, MagicMock, patch
    from jit_ui.browser.session import BrowserActionError, BrowserSession

    site = {"site_id": "web", "start_url": "https://youtbe.com/", "allowed_origin": "https://youtbe.com"}
    session = BrowserSession(Settings(_env_file=None), tmp_path, site)

    mock_page = MagicMock()
    mock_page.url = "about:blank"
    mock_page.title = AsyncMock(return_value="")
    mock_page.goto = AsyncMock(side_effect=[
        Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://youtbe.com/"),
        Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://youtube.com/"),
    ])
    mock_page.on = MagicMock()
    mock_page.set_default_timeout = MagicMock()

    with patch("playwright.async_api.async_playwright") as mock_pw:
        pw_instance = AsyncMock()
        pw_instance.stop = AsyncMock()
        mock_pw.return_value.start = AsyncMock(return_value=pw_instance)
        browser = AsyncMock()
        browser.close = AsyncMock()
        pw_instance.chromium.launch = AsyncMock(return_value=browser)
        context = AsyncMock()
        browser.new_context = AsyncMock(return_value=context)
        context.new_page = AsyncMock(return_value=mock_page)
        cdp = MagicMock()
        cdp.on = MagicMock()
        cdp.detach = AsyncMock()
        cdp.send = AsyncMock()
        context.new_cdp_session = AsyncMock(return_value=cdp)

        with pytest.raises(BrowserActionError) as exc_info:
            await session.open()

        assert "Unable to resolve domain 'youtbe.com' (net::ERR_NAME_NOT_RESOLVED). Check the URL spelling and your internet connection." in str(exc_info.value)


@pytest.mark.asyncio
async def test_browser_session_execute_dns_error_raises_clean_browser_action_error(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from jit_ui.browser.session import BrowserActionError, BrowserSession
    from jit_ui.domain.models import ActionSpec

    site = {"site_id": "web", "start_url": "https://example.org/", "allowed_origin": "https://example.org"}
    session = BrowserSession(Settings(_env_file=None), tmp_path, site)

    mock_page = MagicMock()
    mock_page.url = "about:blank"
    mock_page.title = AsyncMock(return_value="")
    mock_page.evaluate = AsyncMock(return_value="")
    mock_page.locator = MagicMock(return_value=MagicMock(inner_text=AsyncMock(return_value=""), first=MagicMock(wait_for=AsyncMock())))
    mock_page.goto = AsyncMock(side_effect=Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://bad-domain.example/"))
    session.page = mock_page

    action = ActionSpec(action="goto", url="https://bad-domain.example/")
    with pytest.raises(BrowserActionError) as exc_info:
        await session.execute(action, {})

    assert "Unable to resolve domain 'bad-domain.example' (net::ERR_NAME_NOT_RESOLVED). Check the URL spelling and your internet connection." in str(exc_info.value)
