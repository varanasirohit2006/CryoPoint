"""Resolve user-selected websites and reusable intentions without canned action sequences."""

import ipaddress
import json
import re
from urllib.parse import urlsplit, urlunsplit

from jit_ui.domain.models import ParameterSpec
from jit_ui.domain.intent import interpret_price_query
from jit_ui.runtime.browser_helpers import DOMAIN_TYPO_MAP, correct_domain_typo

__all__ = ["DOMAIN_TYPO_MAP", "correct_domain_typo"]


LANGUAGES = {
    "hindi": "hi", "हिंदी": "hi", "हिन्दी": "hi", "hi": "hi",
    "english": "en", "अंग्रेजी": "en", "en": "en",
    "tamil": "ta", "தமிழ்": "ta", "ta": "ta",
    "telugu": "te", "తెలుగు": "te", "te": "te",
    "kannada": "kn", "ಕನ್ನಡ": "kn", "kn": "kn",
    "malayalam": "ml", "മലയാളം": "ml", "ml": "ml",
    "bengali": "bn", "বাংলা": "bn", "bn": "bn",
    "marathi": "mr", "मराठी": "mr", "mr": "mr",
    "gujarati": "gu", "ગુજરાતી": "gu", "gu": "gu",
    "punjabi": "pa", "ਪੰਜਾਬੀ": "pa", "pa": "pa",
    "french": "fr", "fr": "fr", "german": "de", "de": "de",
    "spanish": "es", "es": "es", "italian": "it", "it": "it",
    "japanese": "ja", "ja": "ja", "chinese": "zh", "zh": "zh",
    "portuguese": "pt", "pt": "pt", "arabic": "ar", "ar": "ar",
}
WEB_GOALS = {"browser_task", "web_search", "change_language"}
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.I)
DOMAIN_PATTERN = re.compile(r"(?<![\w@])(?:localhost(?::\d+)?|(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?::\d+)?)(?:/[^\s<>\"']*)?", re.I)
SEARCH_OPERATION = re.compile(
    r"\b(?:search(?:\s+(?:google|goole|gogle|gooogle|amazon|amzon|amazn|amazone|youtube|youtbe|yotube|uoutube|youtub|flipkart|flikart|github|githb|wikipedia|wikpedia))?(?:\s+for)?|look\s+for|find\s+me)\b",
    re.I
)
LANGUAGE_OPERATION = re.compile(r"\b(?:change|switch|set|select|choose)\b", re.I)
GLUED_PREPOSITION_DOMAIN = re.compile(
    r"\b(?:in|on|at|to|open|visit|goto)\s+the([a-z0-9-]+\.[a-z]{2,}(?:/[^\s<>\"']*)?)",
    re.I
)

FOLLOW_ON_TRANSITIONS = r"(?:and(?:\s+(?:then|also|now|after(?:\s+(?:this|that|which))?|afterwards|next|subsequently|finally))?|then|also|now|later|after(?:\s+(?:this|that|which))?|afterwards|next|subsequently|finally)"
FOLLOW_ON_VERBS = (
    r"buy|purchase|book|reserve|pay|confirm|checkout|check\s+out|order|apply|save|delete|remove|cancel|clear|"
    r"open|view|read|click|press|tap|type|enter|input|select|pick|choose|add|insert|go|navigate|visit|"
    r"change|switch|set|download|upload|convert|transform|use|attach|import|export|extract|generate|create|"
    r"fill|complete|submit|send|search|look\s+for|find\s+me|find|"
    r"subscribe|unsubscribe|follow|unfollow|like|share|play|watch|listen|pause|stop|resume|compare|scroll|wait"
)
FOLLOW_ON_OPERATION = re.compile(
    r"(?:\b" + FOLLOW_ON_TRANSITIONS + r"\s+|[.,;]\s*(?:" + FOLLOW_ON_TRANSITIONS + r"\s+)?|\n\s*)(?:" + FOLLOW_ON_VERBS + r")\b",
    re.I
)


def normalize_target_url(value):
    value = str(value or "").strip()
    if not value or re.search(r"\s", value):
        raise ValueError("Provide a valid website URL without spaces.")
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        if re.match(r"^(?:javascript|data|file|ftp|mailto):", value, re.I):
            raise ValueError("Website URLs must use HTTP or HTTPS.")
        local = re.match(r"^(?:localhost|127\.0\.0\.1)(?::|/|$)", value, re.I)
        value = ("http://" if local else "https://") + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Website URLs must use HTTP or HTTPS and include a host.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Website URLs must not contain credentials.")
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        host = correct_domain_typo(host)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in host.split(".")):
                raise ValueError("Provide a valid website host.")
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Provide a valid website port.")
        authority = ("[" + host + "]") if ":" in host else host
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            authority += ":" + str(port)
        return urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, parsed.fragment))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(str(exc) or "Provide a valid website URL.") from exc


def infer_target_url(task):
    if task.target_url:
        return normalize_target_url(task.target_url)
    match = URL_PATTERN.search(task.task) or re.search(r"www\.[^\s<>\"']+", task.task, re.I)
    if match:
        return normalize_target_url(match.group().rstrip(".,;!)"))
    glued = GLUED_PREPOSITION_DOMAIN.search(task.task)
    if glued:
        return normalize_target_url(glued.group(1).rstrip(".,;!)"))
    match = DOMAIN_PATTERN.search(task.task)
    if match:
        return normalize_target_url(match.group().rstrip(".,;!)"))
    if task.site_id == "amazon_in" or re.search(r"\b(?:amazon|amzon|amazn|amazone)\b", task.task, re.I):
        return "https://www.amazon.in/"
    if re.search(r"\b(?:google|goole|gogle|gooogle)\b", task.task, re.I):
        return "https://www.google.com/"
    if re.search(r"\b(?:youtube|youtbe|yotube|uoutube|youtub)\b", task.task, re.I):
        return "https://www.youtube.com/"
    if re.search(r"\b(?:flipkart|flikart)\b", task.task, re.I):
        return "https://www.flipkart.com/"
    if re.search(r"\b(?:github|githb)\b", task.task, re.I):
        return "https://github.com/"
    if re.search(r"\b(?:wikipedia|wikpedia)\b", task.task, re.I):
        return "https://www.wikipedia.org/"
    raise ValueError("Specify the website URL in the task or target URL field.")


def _instruction_text(text):
    # URLs can themselves contain words such as /search or /change-language.
    # Mask them for intention detection while preserving match positions.
    text = URL_PATTERN.sub(lambda match: " " * len(match.group()), text)
    text = re.sub(r"www\.[^\s<>\"']+", lambda match: " " * len(match.group()), text, flags=re.I)
    text = GLUED_PREPOSITION_DOMAIN.sub(lambda match: " " * len(match.group()), text)
    text = DOMAIN_PATTERN.sub(lambda match: " " * len(match.group()), text)
    return re.sub(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d|(?<!\w)\x27[^\x27\n]*\x27(?!\w)', lambda match: " " * len(match.group()), text)


def is_language_request(task):
    if task.goal_id == "change_language":
        return True
    text = _instruction_text(task.task).lower()
    language_words = "|".join(re.escape(name) for name in LANGUAGES if len(name) > 2)
    return (bool(re.search(r"\b(?:change|switch|set|select|choose)\b.*\blanguage\b|\blanguage\b.*\b(?:change|switch|set|to)\b", text))
            or bool(re.search(r"\b(?:change|switch)\b.*(?<!\w)(?:" + language_words + r")(?!\w)", text))
            or ("भाषा" in text and any(word in text for word in ("बदल", "हिंदी", "हिन्दी", "अंग्रेजी"))))


def _language_code(task):
    supplied = task.parameters.get("language")
    if task.parameter_source == "explicit" and supplied is not None:
        value = str(supplied).strip().lower()
        if value in LANGUAGES:
            return LANGUAGES[value]
        if re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2})?", value):
            return value
        raise ValueError("Provide a target language name or language code, such as Hindi or hi.")
    instruction = _instruction_text(task.task)
    matches = list(re.finditer(r"(?<!\w)(" + "|".join(re.escape(name) for name in sorted(LANGUAGES, key=len, reverse=True)) + r")(?!\w)", instruction, re.I))
    preferred = [match for match in matches if re.search(r"\b(?:to|into|as)\s+(?:the\s+)?$", instruction[:match.start()], re.I)]
    if matches:
        return LANGUAGES[(preferred[-1] if preferred else matches[-1]).group().lower()]
    if supplied is not None:
        value = str(supplied).strip().lower()
        if value in LANGUAGES:
            return LANGUAGES[value]
        if re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2})?", value):
            return value
    raise ValueError("Specify the target language, such as Hindi or English.")


def _search_query(task):
    supplied = task.parameters.get("query")
    instruction = _instruction_text(task.task)
    operation = SEARCH_OPERATION.search(instruction)
    remainder = task.task[operation.end():] if operation else ""
    # A request that continues with another browser operation stays a generic task.
    if remainder and task.goal_id != "web_search" and FOLLOW_ON_OPERATION.search(_instruction_text(remainder)):
        return None
    query = remainder.lstrip(" \t:=-").strip(" .,!?;\"'") if operation else None
    if task.parameter_source == "explicit" and supplied is not None and (task.goal_id == "web_search" or operation):
        supplied_text = " ".join(str(supplied).strip(" .,!?;\"'").split())
        if SEARCH_OPERATION.search(_instruction_text(supplied_text)) and re.match(r"^(?:please\s+)?(?:go\s+to|open|visit|search|look\s+for|find\s+me)\b", supplied_text, re.I):
            return _search_query(task.model_copy(update={"task": supplied_text, "parameter_source": "task", "goal_id": "web_search"}))
        return supplied_text
    if not operation:
        return str(supplied).strip() if supplied is not None and task.goal_id == "web_search" else None
    query = re.sub(
        r"\s+(?:on|in|at)\s+(?:the\s+)?(?:google|goole|gogle|gooogle|amazon|amzon|amazn|amazone|youtube|youtbe|yotube|uoutube|youtub|flipkart|flikart|github|githb|wikipedia|wikpedia)(?:\.[a-z.]+)?\s*$",
        "",
        query,
        flags=re.I
    )
    query = re.sub(r"\s+(?:on|in|at)\s+https?://[^\s]+\s*$", "", query, flags=re.I)
    query = re.sub(r"\s+(?:on|in|at)\s+(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?\s*$", "", query, flags=re.I)
    return " ".join(query.strip(" .,!?;\"'").split())


def _search_parameters(task, query):
    parsed = interpret_price_query(query)
    parameters = dict(task.parameters)
    if task.parameter_source != "explicit":
        parameters.pop("min_price", None)
        parameters.pop("max_price", None)
    for name, value in parsed.items():
        if name == "query" or task.parameter_source != "explicit" or parameters.get(name) is None:
            parameters[name] = value
    for name in ("min_price", "max_price"):
        value = parameters.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            raise ValueError(name.replace("_", " ").capitalize() + " must be a non-negative number.")
    minimum, maximum = parameters.get("min_price"), parameters.get("max_price")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("The minimum price must not exceed the maximum price.")
    return parameters


def _compound_details(task, language_request):
    if _named_control(task.task):
        return False, dict(task.parameters)
    instruction = _instruction_text(task.task)
    search = SEARCH_OPERATION.search(instruction)
    language = LANGUAGE_OPERATION.search(instruction) if language_request else None
    primary = min((match.start() for match in (search, language) if match), default=None)
    if primary is not None:
        following = [match for match in FOLLOW_ON_OPERATION.finditer(instruction) if match.start() > primary]
    else:
        matches = list(FOLLOW_ON_OPERATION.finditer(instruction))
        initial_verb = re.search(r"\b(?:" + FOLLOW_ON_VERBS + r")\b", instruction, re.I)
        if initial_verb and matches:
            following = [match for match in matches if match.start() > initial_verb.start()]
        elif len(matches) >= 2:
            following = matches[1:]
        else:
            following = []
    if not following:
        return False, dict(task.parameters)
    parameters = dict(task.parameters)
    if language:
        boundary = next((match.start() for match in following if match.start() > language.start()), len(task.task))
        try:
            code = _language_code(task.model_copy(update={"task": task.task[:boundary]}))
            if task.parameter_source != "explicit" or "language" not in parameters:
                parameters["language"] = code
        except ValueError:
            pass
    if search:
        boundary = next((match.start() for match in following if match.start() > search.start()), len(task.task))
        query = _search_query(task.model_copy(update={"task": task.task[:boundary], "goal_id": "web_search"}))
        if query and (task.parameter_source != "explicit" or "query" not in parameters):
            parameters = _search_parameters(task.model_copy(update={"parameters":parameters}), query)
    return True, parameters


def needs_model_interpretation(task):
    return task.site_id == "web" and task.goal_id == "browser_task" and _compound_details(task, is_language_request(task))[0]


def parameter_specs(parameters):
    specs = {}
    for name, value in parameters.items():
        kind = ("boolean" if isinstance(value, bool) else "integer" if isinstance(value, int)
                else "number" if isinstance(value, float) else "array" if isinstance(value, list)
                else "object" if isinstance(value, dict) else "string")
        specs[name] = ParameterSpec(type=kind, required=value is not None, description=name.replace("_", " "))
    return specs


def _named_control(text):
    activation = re.search(r"\b(?:click|press|tap)(?:\s+on)?\s+(?:the\s+)?([^\n.!?]{1,100}?)\s+(?:button|link|tab|menu item)\s*[.!?]*\s*$", text, re.I)
    if not activation or re.search(r"\b(?:and|then|after)\b", activation.group(1), re.I):
        return None
    prefix = text[:activation.start()].strip()
    if prefix:
        if (not re.match(r"^(?:please\s+)?(?:go\s+to|open|visit|navigate\s+to)\b", prefix, re.I)
                or FOLLOW_ON_OPERATION.search(_instruction_text(prefix))
                or re.search(r"\b(?:read|search|find|fill|type|enter|select|change|save|download|expand|book|reserve|buy|pay|submit|send|scroll)\b", _instruction_text(prefix), re.I)):
            return None
    return " ".join(activation.group(1).strip().strip('\"\'').split()).casefold()


def web_intent_key(task):
    target = infer_target_url(task)
    authority = urlsplit(target).netloc.lower()
    signature = task.goal_id
    if task.goal_id == "browser_task":
        if _named_control(task.task) and any(condition.kind == "control_activated" for condition in task.completion_conditions):
            return "web:" + authority + ":control_activated"
        text = task.task
        for name, value in sorted(task.parameters.items(), key=lambda item: len(str(item[1])), reverse=True):
            if value is not None and not isinstance(value, bool) and str(value).strip():
                pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in str(value).split()) + r"(?!\w)"
                text = re.sub(pattern, lambda _match, parameter=name: "<" + parameter + ">", text, flags=re.I)
        signature += ":" + " ".join(text.lower().split())
    return "web:" + authority + ":" + signature


def resolve_web_request(task):
    target = infer_target_url(task)
    goal = task.goal_id if task.goal_id in WEB_GOALS else "browser_task"
    parameters = dict(task.parameters)
    conditions = [condition.model_copy(deep=True) for condition in task.completion_conditions]
    language_request = is_language_request(task)
    compound, compound_parameters = _compound_details(task, language_request)
    if compound:
        goal, parameters = "browser_task", compound_parameters
    elif language_request:
        goal, parameters = "change_language", {"language": _language_code(task)}
    else:
        query = _search_query(task)
        if goal == "web_search" or (goal == "browser_task" and query is not None):
            if not query:
                raise ValueError("Specify the keywords to search for.")
            goal, parameters = "web_search", _search_parameters(task, query)
    if (goal == "web_search" and not compound and not task.completion_conditions
            and urlsplit(target).hostname in {"amazon.in", "www.amazon.in"}):
        from jit_ui.domain.intent import AMAZON_FILTER_KEYS, resolve_amazon_request
        if set(task.parameters) <= {"query", *AMAZON_FILTER_KEYS}:
            supplied = dict(parameters)
            supplied["query"] = query
            amazon = task.model_copy(update={"site_id": "amazon_in", "goal_id": "search_products",
                "task": "Search for " + query, "parameters": supplied,
                "target_url": "https://www.amazon.in/", "intent_key": None})
            resolved = resolve_amazon_request(amazon)
            return resolved
        goal, parameters = "browser_task", {**task.parameters, "query": parameters["query"]}
    if goal == "browser_task" and not compound:
        control = _named_control(task.task)
        if control:
            parameters.setdefault("control_name", control)
            if not any(condition.kind == "control_activated" for condition in conditions):
                from jit_ui.domain.models import ConditionSpec
                conditions.append(ConditionSpec(kind="control_activated", value_ref="control_name"))
    try:
        json.dumps(parameters, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("Browser parameters must be valid JSON values.") from exc
    for condition in conditions:
        if condition.kind == "goal":
            raise ValueError("Completion conditions must describe observed page state, not reference the goal itself.")
        if condition.value_ref and condition.value_ref not in parameters:
            raise ValueError("Completion condition references an unknown parameter: " + condition.value_ref)
        if condition.kind == "url_query_equals" and not condition.attribute:
            raise ValueError("A URL query condition needs its query key in attribute.")
    if task.provider == "fixture":
        host = urlsplit(target).hostname
        try:
            local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            raise ValueError("Offline web simulations are supported only on explicitly requested localhost testbeds.")
    if not task.compile_workflow and task.mode != "cold":
        raise ValueError("Compilation can be disabled only for forced cold reference runs.")
    resolved = task.model_copy(update={"site_id": "web", "target_url": target, "goal_id": goal, "parameters": parameters, "completion_conditions": conditions})
    return resolved.model_copy(update={"intent_key": web_intent_key(resolved)})


def task_site(task, settings):
    if task.site_id != "web":
        from jit_ui.domain.sites import site_definitions
        return site_definitions(settings)[task.site_id]
    target = infer_target_url(task)
    parsed = urlsplit(target)
    origin = parsed.scheme + "://" + parsed.netloc
    return {"site_id": "web", "label": parsed.netloc, "start_url": target, "allowed_origin": origin,
            "condition_timeout_ms": settings.condition_timeout_ms,
            "allowed_origins": [origin], "goal_id": task.goal_id, "goal_label": task.goal_id.replace("_", " "),
            "parameters": parameter_specs(task.parameters), "target_url": target,
            "intent_key": task.intent_key or web_intent_key(task),
            "completion_conditions": [condition.model_dump(mode="json") for condition in task.completion_conditions]}
