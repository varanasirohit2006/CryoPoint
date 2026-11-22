from pathlib import Path

import pytest

from jit_ui.routing.index import ChromaWorkflowIndex, IndexUnavailable, LexicalWorkflowIndex, MemoryWorkflowIndex
from jit_ui.domain.models import ParameterSpec, TaskRequest, WorkflowIR
from jit_ui.routing import SemanticRouter, abstract_intent, validate_parameters


def fixture_embeddings(texts):
    vectors = {
        "search products": [1.0, 0.0],
        "find products": [0.99, 0.01],
        "ship order": [0.0, 1.0],
    }
    return [vectors[text] for text in texts]


def test_index_filters_site_and_goal_before_matching():
    index = MemoryWorkflowIndex(fixture_embeddings)
    index.upsert("amazon", "search products", {"site_id": "amazon_in", "goal_id": "search"})
    index.upsert("shop", "search products", {"site_id": "shop", "goal_id": "search"})
    index.upsert("admin", "ship order", {"site_id": "shop", "goal_id": "ship"})

    matches = index.search("find products", {"site_id": "shop", "goal_id": "search"})

    assert [match.workflow_id for match in matches] == ["shop"]
    assert matches[0].similarity > 0.99


def test_index_upsert_replaces_derived_entry_and_delete_removes_it():
    index = MemoryWorkflowIndex(fixture_embeddings)
    index.upsert("workflow", "search products", {"version": 1})
    index.upsert("workflow", "ship order", {"version": 2})

    assert index.search("ship order", {"version": 1}) == []
    assert index.search("ship order", {"version": 2})[0].similarity == 1.0
    index.delete("workflow")
    assert index.search("ship order", {}) == []


def test_index_rejects_incompatible_embedding_dimensions():
    index = MemoryWorkflowIndex(lambda texts: [[1.0] if text == "stored" else [1.0, 0.0] for text in texts])
    index.upsert("workflow", "stored", {})

    with pytest.raises(ValueError, match="dimensions"):
        index.search("query", {})


def test_chroma_construction_is_lazy(tmp_path: Path):
    index = ChromaWorkflowIndex(tmp_path / "not_created")

    assert index._collection is None
    assert index._embedding_function is None
    assert not index.directory.exists()


def test_chroma_missing_provisioned_model_fails_before_loading_or_downloading(tmp_path):
    index = ChromaWorkflowIndex(tmp_path / "index", embedding_directory=tmp_path / "embeddings")
    with pytest.raises(IndexUnavailable, match="downloads never"):
        index.search("find products", {})
    assert index._client is None and index._embedding_function is None
    assert not index.directory.exists()


def test_lexical_index_reuses_parameterized_intents_with_filtered_cosine_scores():
    index = LexicalWorkflowIndex()
    index.upsert("shop", "search_products: find <product_name> below <max_price>", {"site_id": "shop"})
    index.upsert("other", "search_products: find <product_name> below <max_price>", {"site_id": "other"})
    index.upsert("ship", "ship_order: ship <order_id>", {"site_id": "shop"})
    matches = index.search("search_products: find <product_name> below <max_price>", {"site_id": "shop"})
    assert matches[0].workflow_id == "shop" and matches[0].similarity == pytest.approx(1)
    assert all(match.workflow_id != "other" for match in matches)
    assert matches[1].similarity == 0
    index.delete("shop")
    assert [match.workflow_id for match in index.search("find products", {"site_id": "shop"})] == ["ship"]


def workflow(**changes):
    values = {"workflow_id": "active", "site_id": "amazon_in", "goal_id": "search_products",
              "intent_signature": "search", "parameters": {"query": ParameterSpec()}, "steps": []}
    values.update(changes)
    return WorkflowIR(**values)


def search_request(**changes):
    values = {"site_id": "amazon_in", "goal_id": "search_products", "task": "Find backpacks", "parameters": {"query": "backpacks"}}
    values.update(changes)
    return TaskRequest(**values)


def matching_index():
    return MemoryWorkflowIndex(lambda texts: [[1.0, 0.0] for _text in texts])


def test_router_verifies_authoritative_workflow_instead_of_trusting_index():
    index = matching_index()
    index.upsert("stale", "search", {"site_id": "amazon_in", "goal_id": "search_products"})
    index.upsert("active", "search", {"site_id": "amazon_in", "goal_id": "search_products"})
    active = workflow(version=2)
    router = SemanticRouter(index, lambda identifier: active if identifier == "active" else None)

    assert router.route(search_request()) is active


@pytest.mark.parametrize("changes", [
    {"site_id": "shop"}, {"goal_id": "purchase"}, {"runtime_version": 2}, {"schema_version": 2},
    {"parameters": {"order_id": ParameterSpec(type="integer")}},
])
def test_router_rejects_incompatible_authoritative_ir(changes):
    index = matching_index()
    index.upsert("active", "search", {"site_id": "amazon_in", "goal_id": "search_products"})
    router = SemanticRouter(index, lambda _identifier: workflow(**changes))

    assert router.route(search_request()) is None


def test_router_cold_mode_skips_semantic_index():
    class ForbiddenIndex:
        def search(self, *_args):
            raise AssertionError("Cold requests should not query embeddings.")

    assert SemanticRouter(ForbiddenIndex(), lambda _identifier: None).route(search_request(mode="cold")) is None


def test_abstract_intent_removes_parameter_literals():
    assert abstract_intent(search_request()) == "search_products: find <query>"


@pytest.mark.parametrize("parameters", [{}, {"query": ""}, {"query": 123}, {"query": "x", "extra": 1}])
def test_strict_parameter_validation(parameters):
    with pytest.raises(ValueError):
        validate_parameters(parameters, {"query": ParameterSpec()})


def test_numeric_parameters_reject_booleans_and_nonfinite_values():
    with pytest.raises(ValueError):
        validate_parameters({"price": True}, {"price": ParameterSpec(type="integer")})
    with pytest.raises(ValueError):
        validate_parameters({"price": float("inf")}, {"price": ParameterSpec(type="number")})
