from fastapi.testclient import TestClient

from chaos_testbed.api.main import app, settings


def test_product_filter_and_sort_are_observable():
    with TestClient(app) as client:
        client.post("/api/reset", json={"seed": 42})
        result = client.get("/api/products", params={"q": "shoes", "max_price": 3000, "sort_by": "price_asc"}).json()
        assert [item["price"] for item in result["products"]] == [1299, 1899, 2499]
        state = client.get("/api/state").json()
        assert state["shop"]["query"] == "shoes"
        assert state["shop"]["product_ids"] == [item["id"] for item in result["products"]]


def test_shipping_requires_login_and_is_idempotent():
    with TestClient(app) as client:
        client.post("/api/reset", json={"seed": 42})
        assert client.post("/api/orders/1042/ship").status_code == 401
        assert client.post("/api/login", json={"username": "demo", "password": "demo-password"}).status_code == 200
        assert client.post("/api/orders/1042/ship").json()["changed"] is True
        assert client.post("/api/orders/1042/ship").json()["changed"] is False
        assert len(client.get("/api/state").json()["shipments"]) == 1


def test_ui_mutations_preserve_business_fixtures():
    with TestClient(app) as client:
        before = client.post("/api/reset", json={"seed": 42}).json()
        after = client.post("/api/mutations", json={"site_id": "admin", "mutation": "compound", "seed": 7}).json()
        assert before["orders"] == after["orders"]
        assert after["mutations"]["admin"]["modal_visible"] is True
        generation = after["mutations"]["admin"]["generation"]
        client.post("/api/mutations/dismiss", json={"site_id": "admin", "generation": generation})
        assert client.get("/api/state").json()["mutations"]["admin"]["modal_visible"] is False


def test_stale_dismissal_cannot_remove_a_new_obstacle():
    with TestClient(app) as client:
        client.post("/api/reset", json={"seed": 42})
        first = client.post("/api/mutations", json={"site_id": "shop", "mutation": "obstacle"}).json()
        client.post("/api/mutations", json={"site_id": "shop", "mutation": "compound"})
        client.post("/api/mutations/dismiss", json={"site_id": "shop", "generation": first["mutations"]["shop"]["generation"]})
        assert client.get("/api/state").json()["mutations"]["shop"]["modal_visible"] is True


def test_nonfinite_prices_are_rejected_without_changing_goal_state():
    with TestClient(app) as client:
        original = client.post("/api/reset", json={"seed": 42}).json()["shop"]
        for price in ("nan", "inf", "-1"):
            assert client.get("/api/products", params={"max_price": price}).status_code == 422
        assert client.get("/api/state").json()["shop"] == original


def test_custom_credentials_match_runtime_configuration(monkeypatch):
    monkeypatch.setattr(settings, "fixture_username", "operator")
    monkeypatch.setattr(settings, "fixture_password", "custom-fixture-secret")
    with TestClient(app) as client:
        client.post("/api/reset", json={"seed": 42})
        assert client.post("/api/login", json={"username": "demo", "password": "demo-password"}).status_code == 401
        assert client.post("/api/login", json={"username": "operator", "password": "custom-fixture-secret"}).status_code == 200
        response = client.get("/api/session")
        assert response.json()["demo_credentials"] is False
        assert "custom-fixture-secret" not in response.text
