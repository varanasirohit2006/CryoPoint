"""Explicit simulated provider for deterministic local tests and labeled demos."""

from __future__ import annotations

from jit_ui.domain.models import ActionFrame, ActionSpec, ElementFingerprint, Observation, ProviderResponse, TaskRequest
from jit_ui.providers.base import ProviderError


class FixtureProvider:
    simulated = True
    model = "fixture:scripted-simulation"

    def __init__(self) -> None:
        self.attempts_total = 0

    async def decide(self, observation: Observation, task: TaskRequest, history: list[ActionFrame], instruction: str = "") -> ProviderResponse:
        if task.provider != "fixture" or task.site_id == "amazon_in":
            raise ProviderError("Fixture simulation is only available when explicitly selected for local testbed tasks.", "FIXTURE_NOT_ALLOWED")
        self.attempts_total += 1
        successful = [frame.action for frame in history if not frame.error]

        def find(names: tuple[str, ...], roles: tuple[str, ...] = ()) -> ElementFingerprint | None:
            return next((e for e in observation.elements if e.name.casefold() in {n.casefold() for n in names} and (not roles or e.role in roles)), None)

        def used(action: str, reference: str) -> bool:
            return any(a.action == action and a.value_ref == reference for a in successful)

        def clicked(intent: str) -> bool:
            return any(a.action == "click" and a.intent == intent for a in successful)

        def proposal(element: ElementFingerprint | None, action: str, intent: str, reference: str | None = None, value: str | None = None, effect: str = "read") -> ProviderResponse:
            if element is None:
                raise ProviderError(f"Fixture simulation cannot locate the labeled control for: {intent}.", "FIXTURE_CONTROL_MISSING")
            return ProviderResponse(action=ActionSpec(action=action, target_index=element.index, intent=intent, value_ref=reference, value=value, effect=effect), model=self.model)

        dismiss = find(("Dismiss notice", "Dismiss promotion", "Close promotion", "Close announcement", "Dismiss", "Close", "Continue browsing"), ("button",))
        if dismiss:
            return proposal(dismiss, "click", "Dismiss blocking notice")
        if task.site_id == "shop":
            query_ref = "product_name" if "product_name" in task.parameters else "query"
            if not used("fill", query_ref):
                return proposal(find(("Search products", "Find products", "Catalog search")), "fill", "Enter product query", query_ref)
            if not clicked("Submit product search"):
                return proposal(find(("Search", "Find products", "Find Products", "Search catalog"), ("button",)), "click", "Submit product search")
            if "max_price" in task.parameters and not used("fill", "max_price"):
                price = find(("Max price", "Maximum price", "Budget limit"))
                if price is None:
                    return proposal(find(("Refine results",), ("button",)), "click", "Open price filters")
                return proposal(price, "fill", "Enter maximum price", "max_price")
            if "max_price" in task.parameters and not clicked("Apply price filter"):
                return proposal(find(("Apply filters", "Apply filter", "Update results", "Update selection", "Apply budget"), ("button",)), "click", "Apply price filter")
            if not used("select", "sort_by"):
                return proposal(find(("Sort by", "Sort products", "Ranking")), "select", "Sort product results", "sort_by" if "sort_by" in task.parameters else None, None if "sort_by" in task.parameters else "rating_desc")
        else:
            username = find(("Username", "Email", "Demo username"))
            password = find(("Password", "Demo password"))
            if username and not used("fill", "username"):
                return proposal(username, "fill", "Enter demo username", "username")
            if password and not used("fill", "password"):
                return proposal(password, "fill", "Enter demo password", "password")
            login = find(("Log in", "Login", "Sign in"), ("button",))
            if login and not clicked("Log in to local admin"):
                return proposal(login, "click", "Log in to local admin")
            if not used("fill", "order_id"):
                return proposal(find(("Order ID", "Search orders", "Search order ID", "Find order")), "fill", "Enter order ID", "order_id")
            if not clicked("Find requested order"):
                return proposal(find(("Find order", "Search order", "Search", "Lookup order"), ("button",)), "click", "Find requested order")
            ship = find(("Mark shipped", "Mark Shipped", "Dispatch package", "Dispatch Package"), ("button",))
            if ship and not clicked("Mark requested order shipped"):
                return proposal(ship, "click", "Mark requested order shipped", effect="mutation")
            menu = find(("Order actions", "More actions", "Actions"), ("button",))
            if menu and not clicked("Open order actions"):
                return proposal(menu, "click", "Open order actions")
        return ProviderResponse(done=True, model=self.model)
