"""Amazon's public keyword/price/sort navigation capability, independently verified."""
from jit_ui.domain.models import ActionSpec


def supports_native_search(task):
    return (task.site_id == "amazon_in" and task.goal_id == "search_products"
            and set(task.parameters) <= {"query", "min_price", "max_price", "sort_by"})


def native_search_action(parameters):
    refs = {"k": "query"}
    refs.update({key: name for key, name in (("low-price", "min_price"), ("high-price", "max_price"), ("s", "sort_by"))
                 if parameters.get(name) is not None})
    return ActionSpec(action="goto", intent="Apply Amazon keywords and requested price/sort filters",
                      url="https://www.amazon.in/s", url_refs=refs)


async def execute_native_search(session, task, emit, cancelled):
    if cancelled():
        from jit_ui.exploration.explorer import RunCancelled
        raise RunCancelled()
    action = native_search_action(task.parameters)
    await emit("native_search", {"strategy": "native", "parameters": task.parameters,
                                 "message": "Apply public Amazon search parameters and verify matching results."})
    frame = await session.execute(action, task.parameters)
    await emit("action", {"frame": frame.model_dump(mode="json"), "index": 0, "strategy": "native"})
    page = getattr(session, "page", None)
    if page is not None:
        try:
            await page.wait_for_function("""() => {
                const text = document.body?.innerText || '';
                return [...document.querySelectorAll('[data-component-type="s-search-result"][data-asin]')]
                    .some(e => e.getClientRects().length > 0)
                    || /no results for|couldn't find any results|traffic is piling up|robot check|automated access/i.test(text);
            }""", timeout=10000)
        except Exception as exc:
            from jit_ui.exploration.explorer import ExplorationError
            raise ExplorationError("Amazon search results did not finish loading. Retry when the site is available.", "SEARCH_RESULTS_NOT_READY") from exc
    if cancelled():
        from jit_ui.exploration.explorer import RunCancelled
        raise RunCancelled()
    return [frame]
