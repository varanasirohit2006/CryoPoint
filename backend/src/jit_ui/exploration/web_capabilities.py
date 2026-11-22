"""Verified public navigation capabilities; other browser tasks retain model exploration."""
from urllib.parse import urlparse, urlunparse

from jit_ui.domain.models import ActionSpec, ConditionSpec, LocatorSpec
from jit_ui.runtime.browser_helpers import AMAZON_LANGUAGE_LOCALES, normalize_language


GOOGLE_HOSTS = {"google.com", "www.google.com", "google.co.in", "www.google.co.in"}
AMAZON_HOSTS = {"amazon.in", "www.amazon.in"}


def supports_web_capability(task):
    if task.site_id != "web":
        return False
    target = urlparse(getattr(task, "target_url", None) or "")
    if target.scheme not in {"http", "https"} or target.username or target.password:
        return False
    host = (target.hostname or "").casefold()
    if task.goal_id == "web_search":
        return host in GOOGLE_HOSTS and bool(task.parameters.get("query")) and set(task.parameters) <= {"query"}
    if task.goal_id == "change_language":
        return host in AMAZON_HOSTS and normalize_language(task.parameters.get("language")) in AMAZON_LANGUAGE_LOCALES and set(task.parameters) <= {"language"}
    return False


def web_capability_action(task):
    if not supports_web_capability(task):
        raise ValueError("This request requires general browser exploration rather than a native web capability.")
    target = urlparse(task.target_url)
    if task.goal_id == "web_search":
        address = urlunparse(target._replace(path="/search", params="", query="", fragment=""))
        return ActionSpec(action="goto", intent="Search Google for the requested keywords", url=address, url_refs={"q": "query"})
    address = urlunparse(target._replace(path="/", params="", query="", fragment=""))
    return ActionSpec(action="goto", intent="Apply the requested Amazon display language", url=address,
                      url_refs={"language": "language"})


def web_capability_conditions(task):
    if not supports_web_capability(task):
        return list(getattr(task, "completion_conditions", []))
    if task.goal_id == "web_search":
        conditions = [ConditionSpec(kind="url_query_equals", attribute="q", value_ref="query"),
                      ConditionSpec(kind="visible", locators=[
                          LocatorSpec(strategy="css", value="#search a:has(h3) >> nth=0"),
                          LocatorSpec(strategy="css", value="#rso a:has(h3) >> nth=0")])]
    else:
        conditions = [ConditionSpec(kind="language_equals", value_ref="language")]
    return conditions + list(getattr(task, "completion_conditions", []))
