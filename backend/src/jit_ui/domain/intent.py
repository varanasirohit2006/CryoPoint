"""Local interpretation of common shopping requests; replay needs no model call."""

import re


AMAZON_FILTER_KEYS = ("min_price", "max_price", "sort_by", "min_rating")
MONEY = r"(?:₹\s*|rs\.?\s*|inr\s*)?(\d[\d,]*(?:\.\d+)?)\s*(?:rupees?|rs\.?|inr)?"
SORT_PATTERNS = [
    (r"(?:sort(?:ed)?\s+(?:by\s+)?)?(?:price\s*:?\s*low\s*to\s*high|lowest\s+price(?:\s+first)?|cheapest(?:\s+first)?|price\s+ascending)", "price-asc-rank"),
    (r"(?:sort(?:ed)?\s+(?:by\s+)?)?(?:price\s*:?\s*high\s*to\s*low|highest\s+price(?:\s+first)?|most\s+expensive(?:\s+first)?|price\s+descending)", "price-desc-rank"),
    (r"(?:highest\s+rat(?:ing|ed)|best\s+rated|sort(?:ed)?\s+(?:by\s+)?(?:customer\s+reviews|rating(?:\s+descending)?))", "review-rank"),
    (r"(?:sort(?:ed)?\s+(?:by\s+)?)?(?:newest(?:\s+first)?|latest\s+arrivals)", "date-desc-rank"),
    (r"sort(?:ed)?\s+by\s+(?:relevance|featured)", "relevanceblender"),
]


def filter_shape(parameters):
    return tuple(key for key in AMAZON_FILTER_KEYS if parameters.get(key) is not None)


def looks_like_instruction(text):
    return bool(re.search(r"\b(?:go\s+to|search\s+for|find\s+me|which\s+are|under|below|rupees|minimum|minium|minimun|minmum|maximum|maximun)\b|₹", text, re.I))


def _price_constraints(text):
    parameters = {}
    spans = []

    def amount(value):
        return float(value.replace(",", ""))

    def money_context(match):
        # Ages, weight, and quantities should stay in the search keywords.
        return not re.match(r"\s*-?\s*(?:years?|months?|kg|grams?|litres?|liters?|ml|stars?)\b", text[match.end():], re.I)

    for match in re.finditer(r"\bbetween\s+" + MONEY + r"\s*(?:and|to|-)\s*" + MONEY, text, re.I):
        if money_context(match):
            parameters["min_price"], parameters["max_price"] = amount(match.group(1)), amount(match.group(2))
            spans.append(match.span())
    for key, pattern in [
        ("max_price", r"(?:under|below|less\s+than|up\s+to|at\s+most|not\s+more\s+than|(?:maximum|maximun|max)(?:\s+price)?|budget(?:\s+(?:of|is))?)\s*[:=]?\s*"),
        ("min_price", r"(?:above|over|more\s+than|at\s+least|(?:minimum|minium|minimun|minmum|min)(?:\s+price)?)\s*[:=]?\s*"),
    ]:
        for match in re.finditer(r"\b" + pattern + MONEY, text, re.I):
            if money_context(match):
                value = amount(match.group(1))
                parameters[key] = min(parameters.get(key, value), value) if key == "max_price" else max(parameters.get(key, value), value)
                spans.append(match.span())
    return parameters, spans


def _clean_constraint_suffix(text):
    # Removing a price/rating span can leave an instruction such as
    # "and filter it only the products with". Only strip shopping boilerplate.
    text = re.sub(r"(?:[.,;]\s*)?\b(?:and\s+|then\s+)?(?:filter|show|include|keep|return)\s+(?:(?:it|them|only|the|these|those|all|products?|results?|items?|with|which|that|are|is|having|have|a|an|price|mrp|cost|priced|costing|at|in)\b\s*)+(?=[\s,.!?]*$)", "", text, flags=re.I)
    text = re.sub(r"\b(?:which|that)\s+(?:(?:are|is|have|has|having|cost|costs|with|priced|costing|mrp|price|a|an)\b\s*)+(?=[\s,.!?]*$)", "", text, flags=re.I)
    text = re.sub(r"\b(?:which\s+are|that\s+are|that\s+cost|priced|costing)\s*(?=[\s,.!?]*$)", "", text, flags=re.I)
    return re.sub(r"\b(?:and|with)\s*(?=[\s,.!?]*$)", "", text, flags=re.I)


def interpret_price_query(text, fallback_query=""):
    """Separate shopping budgets from free keywords on any website."""
    text = text.strip()
    parameters, spans = _price_constraints(text)
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " * (end - start) + text[end:]
    if spans:
        text = _clean_constraint_suffix(text)
    parameters["query"] = " ".join(text.strip(" .,!?;:-").split()) or fallback_query.strip()
    if not parameters["query"]:
        raise ValueError("Specify the product or keywords to search for.")
    if parameters.get("min_price", 0) > parameters.get("max_price", float("inf")):
        raise ValueError("The minimum price must not exceed the maximum price.")
    return parameters


def interpret_amazon(text, fallback_query=""):
    """Preserve free keywords (including age/gender); separate price and sorting."""
    text = text.strip()
    parameters, spans = _price_constraints(text)
    for match in re.finditer(r"(?:at\s+least\s+|rated\s+|rating\s+)?([1-5](?:\.\d+)?)\s*(?:stars?\s*(?:and\s+up|or\s+(?:more|above)|\+)|stars?\s+minimum|\+\s*stars?)", text, re.I):
        parameters["min_rating"] = float(match.group(1))
        spans.append(match.span())
    for pattern, native in SORT_PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            parameters["sort_by"] = native
            spans.append(match.span())
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " * (end - start) + text[end:]
    text = re.sub(r"^\s*(?:please\s+)?(?:go\s+to|open|visit)\s+(?:the\s+)?(?:www\.)?amazon(?:\.in|\s+india)?\s*[.,;]?\s*(?:and\s+then|and|then)?\s*", "", text, flags=re.I)
    text = re.sub(r"^\s*(?:search|find|look\s+for|browse|show\s+me|recommend)\s+(?:me\s+)?(?:amazon(?:\.in|\s+india)?\s+for\s+|for\s+)?", "", text, flags=re.I)
    text = re.sub(r"\bon\s+amazon(?:\.in|\s+india)?\b", "", text, flags=re.I)
    text = _clean_constraint_suffix(text)
    query = " ".join(text.strip(" .,!?;:-").split())
    if query.casefold() in {"search", "find", "browse", "shopping", "shop"}:
        query = ""
    parameters["query"] = query or fallback_query.strip()
    if not parameters["query"]:
        raise ValueError("Describe what you want to find, or provide search keywords.")
    if parameters.get("min_price", 0) > parameters.get("max_price", float("inf")):
        raise ValueError("The minimum price must not exceed the maximum price.")
    return parameters


def resolve_amazon_request(task):
    unknown = set(task.parameters) - {"query", *AMAZON_FILTER_KEYS}
    if unknown:
        raise ValueError("Unknown Amazon parameters: " + ", ".join(sorted(unknown)))
    provided = {key: value for key, value in task.parameters.items() if value is not None and value != ""}
    query_text = " ".join(str(provided.get("query", "")).split())
    if task.parameter_source == "explicit":
        parsed = interpret_amazon(query_text, query_text) if looks_like_instruction(query_text) else {"query": query_text}
        parsed.update({key: value for key, value in provided.items() if key != "query"})
        return task.model_copy(update={"parameters": parsed})
    # Accept a complete command pasted into the old keyword field as well.
    description = query_text if looks_like_instruction(query_text) and query_text != task.task else task.task
    parsed = interpret_amazon(description, query_text)
    return task.model_copy(update={"parameters": parsed, "task": description})
