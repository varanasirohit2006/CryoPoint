from jit_ui.compiler.trace_compiler import parameterize_condition
from jit_ui.domain.models import ConditionSpec
from jit_ui.runtime.browser_helpers import resolve_value


def test_completion_binding_preserves_static_booking_proof_and_dynamic_values():
    original = ConditionSpec(kind="text_contains", expected="Booking confirmed for London on 2026-12-01")
    bound = parameterize_condition(original, {"city": "London", "date": "2026-12-01"})
    assert original.expected == "Booking confirmed for London on 2026-12-01"
    assert resolve_value(bound.model_dump(), {"city": "Paris", "date": "2026-12-02"}, {}) == "Booking confirmed for Paris on 2026-12-02"


def test_encoded_url_evidence_rebinds_keywords_without_reinterpreting_values():
    original = ConditionSpec(kind="url_contains", expected="/search?q=The+Hobbit")
    bound = parameterize_condition(original, {"query": "The Hobbit"})
    assert resolve_value(bound.model_dump(), {"query": "A {{literal}} book"}, {}) == "/search?q=A {{literal}} book"
