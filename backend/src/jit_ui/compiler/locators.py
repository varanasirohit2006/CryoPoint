"""Compile several explicit, ordered ways to identify the observed target."""

from jit_ui.domain.models import ElementFingerprint, LocatorSpec


TIERS = {"testid": 1, "role": 1, "label": 2, "placeholder": 2, "text": 2, "xpath": 3, "css": 4}
ORDER = {"testid": 0, "role": 1, "label": 2, "placeholder": 3, "text": 4, "xpath": 5, "css": 6}


def rank_locators(
    existing: list[LocatorSpec], element: ElementFingerprint | None
) -> list[LocatorSpec]:
    candidates = [candidate.model_copy(deep=True) for candidate in existing]
    if element is not None:
        if element.testid:
            candidates.append(LocatorSpec(strategy="testid", value=element.testid))
        if element.role:
            candidates.append(LocatorSpec(strategy="role", value=element.role, name=element.name or None))
        for strategy, value in (
            ("label", element.label),
            ("placeholder", element.placeholder),
            ("text", element.text),
            ("xpath", element.xpath),
            ("css", element.css),
        ):
            if value:
                candidates.append(LocatorSpec(strategy=strategy, value=value))
    unique: dict[tuple[str, str, str | None, bool], LocatorSpec] = {}
    for candidate in candidates:
        candidate.tier = TIERS[candidate.strategy]
        key = (candidate.strategy, candidate.value, candidate.name, candidate.exact)
        if candidate.value.strip() and key not in unique:
            unique[key] = candidate
    return sorted(unique.values(), key=lambda item: (item.tier, ORDER[item.strategy]))
