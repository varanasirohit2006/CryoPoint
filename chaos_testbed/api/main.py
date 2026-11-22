"""A local-only shopping and order portal with independent goal observations."""

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from threading import RLock
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class TestbedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JIT_", env_file=".env", extra="ignore")
    fixture_username: str = "demo"
    fixture_password: str = "demo-password"


settings = TestbedSettings()


CATALOG = [
    {"id": "p01", "name": "Nike Air everyday shoes", "category": "Footwear", "price": 3499, "rating": 4.8, "color": "sand", "kind": "shoe"},
    {"id": "p02", "name": "Nike Flex running shoes", "category": "Footwear", "price": 2499, "rating": 4.6, "color": "sage", "kind": "shoe"},
    {"id": "p03", "name": "Adidas Cloud walking shoes", "category": "Footwear", "price": 1899, "rating": 4.5, "color": "blue", "kind": "shoe"},
    {"id": "p04", "name": "Puma Street casual shoes", "category": "Footwear", "price": 1299, "rating": 4.3, "color": "pink", "kind": "shoe"},
    {"id": "p05", "name": "Nike Trail premium shoes", "category": "Footwear", "price": 6999, "rating": 4.9, "color": "blue", "kind": "shoe"},
    {"id": "p06", "name": "Sony wireless headphones", "category": "Audio", "price": 4499, "rating": 4.7, "color": "sage", "kind": "headphones"},
    {"id": "p07", "name": "Sony studio headphones", "category": "Audio", "price": 2999, "rating": 4.4, "color": "sand", "kind": "headphones"},
    {"id": "p08", "name": "Sony premium headphones", "category": "Audio", "price": 7999, "rating": 4.9, "color": "blue", "kind": "headphones"},
    {"id": "p09", "name": "JBL travel headphones", "category": "Audio", "price": 1999, "rating": 4.2, "color": "pink", "kind": "headphones"},
    {"id": "p10", "name": "Northstar everyday backpack", "category": "Bags", "price": 1599, "rating": 4.5, "color": "sage", "kind": "backpack"},
    {"id": "p11", "name": "Northstar travel backpack", "category": "Bags", "price": 2799, "rating": 4.7, "color": "blue", "kind": "backpack"},
    {"id": "p12", "name": "Canvas commuter backpack", "category": "Bags", "price": 999, "rating": 4.1, "color": "sand", "kind": "backpack"},
]
ORDER_FIXTURES = [
    {"order_id": "1042", "customer": "Aarav Mehta", "item": "Nike Air everyday shoes", "total": 3499, "status": "Pending", "placed_at": "2026-11-18", "address": "Bengaluru, Karnataka"},
    {"order_id": "2089", "customer": "Priya Nair", "item": "Sony wireless headphones", "total": 4499, "status": "Pending", "placed_at": "2026-11-19", "address": "Kochi, Kerala"},
    {"order_id": "3015", "customer": "Rohan Shah", "item": "Northstar travel backpack", "total": 2799, "status": "Pending", "placed_at": "2026-11-19", "address": "Mumbai, Maharashtra"},
    {"order_id": "4027", "customer": "Ananya Rao", "item": "Adidas Cloud walking shoes", "total": 1899, "status": "Pending", "placed_at": "2026-11-20", "address": "Hyderabad, Telangana"},
    {"order_id": "5120", "customer": "Vikram Singh", "item": "Canvas commuter backpack", "total": 999, "status": "Pending", "placed_at": "2026-11-20", "address": "Jaipur, Rajasthan"},
]
MutationName = Literal["none", "selector_drift", "spatial_shift", "copy_mutation", "dom_nesting", "obstacle", "compound"]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResetRequest(Payload):
    seed: int = 42


class MutationRequest(Payload):
    site_id: Literal["shop", "admin"]
    mutation: MutationName
    seed: int = 1


class DismissRequest(Payload):
    site_id: Literal["shop", "admin"]
    generation: int


class LoginRequest(Payload):
    username: str
    password: str


class FixtureStore:
    def __init__(self) -> None:
        self.lock = RLock()
        self.reset(42)

    def reset(self, seed: int) -> None:
        with self.lock:
            self.seed = seed
            self.products = deepcopy(CATALOG)
            self.orders = {item["order_id"]: deepcopy(item) for item in ORDER_FIXTURES}
            self.sessions: set[str] = set()
            self.shipments: list[dict] = []
            self.shop = {"query": "", "max_price": None, "sort_by": "rating_desc", "product_ids": [item["id"] for item in self.products]}
            self.admin = {"query": "", "order_ids": list(self.orders)}
            self.mutations = {site: {"mutation": "none", "seed": seed, "generation": 0, "modal_visible": False} for site in ("shop", "admin")}

    def snapshot(self) -> dict:
        with self.lock:
            return deepcopy({"seed": self.seed, "shop": self.shop, "admin": self.admin, "orders": list(self.orders.values()), "shipments": self.shipments, "mutations": self.mutations})


store = FixtureStore()
app = FastAPI(title="JIT UI Chaos Testbed", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:3000", "http://localhost:3000", "http://127.0.0.1:5173", "http://localhost:5173"], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["*"])


def require_session(request: Request) -> None:
    if request.cookies.get("northstar_session") not in store.sessions:
        raise HTTPException(401, "Sign in to the fixture portal first")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "fixture": True, "seed": store.seed}


@app.get("/api/state")
def state() -> dict:
    return store.snapshot()


@app.post("/api/reset")
def reset(payload: ResetRequest = ResetRequest()) -> dict:
    store.reset(payload.seed)
    return store.snapshot()


@app.post("/api/mutations")
def mutate(payload: MutationRequest) -> dict:
    with store.lock:
        previous = store.mutations[payload.site_id]
        store.mutations[payload.site_id] = {"mutation": payload.mutation, "seed": payload.seed, "generation": previous["generation"] + 1, "modal_visible": payload.mutation in ("obstacle", "compound")}
    return store.snapshot()


@app.post("/api/mutations/dismiss")
def dismiss(payload: DismissRequest) -> dict:
    with store.lock:
        mutation = store.mutations[payload.site_id]
        if mutation["generation"] == payload.generation:
            mutation["modal_visible"] = False
    return store.snapshot()


@app.get("/api/products")
def products(q: str = "", max_price: float | None = None, sort_by: Literal["rating_desc", "price_asc"] = "rating_desc") -> dict:
    if max_price is not None and (not isfinite(max_price) or max_price < 0):
        raise HTTPException(422, "Maximum price must be finite and non-negative")
    tokens = q.strip().casefold().split()
    with store.lock:
        matched = [item for item in store.products if all(token in item["name"].casefold() for token in tokens) and (max_price is None or item["price"] <= max_price)]
        matched.sort(key=(lambda item: (-item["rating"], item["id"])) if sort_by == "rating_desc" else (lambda item: (item["price"], item["id"])))
        store.shop = {"query": q.strip(), "max_price": max_price, "sort_by": sort_by, "product_ids": [item["id"] for item in matched]}
        return {"products": deepcopy(matched), "query": q.strip(), "max_price": max_price, "sort_by": sort_by, "count": len(matched)}


@app.post("/api/login")
def login(payload: LoginRequest, response: Response) -> dict:
    if payload.username != settings.fixture_username or payload.password != settings.fixture_password:
        raise HTTPException(401, "Use the configured fixture credentials")
    token = uuid4().hex
    with store.lock:
        store.sessions.add(token)
    response.set_cookie("northstar_session", token, httponly=True, samesite="lax")
    return {"logged_in": True, "username": settings.fixture_username}


@app.get("/api/session")
def session(request: Request) -> dict:
    return {
        "logged_in": request.cookies.get("northstar_session") in store.sessions,
        "username": settings.fixture_username,
        "demo_credentials": settings.fixture_username == "demo" and settings.fixture_password == "demo-password",
    }


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    with store.lock:
        store.sessions.discard(request.cookies.get("northstar_session", ""))
    response.delete_cookie("northstar_session")
    return {"logged_in": False}


@app.get("/api/orders")
def orders(request: Request, q: str = "") -> dict:
    require_session(request)
    needle = q.strip().removeprefix("#")
    with store.lock:
        matched = [deepcopy(order) for order in store.orders.values() if not needle or needle == order["order_id"]]
        store.admin = {"query": needle, "order_ids": [order["order_id"] for order in matched]}
        return {"orders": matched, "query": needle, "count": len(matched)}


@app.post("/api/orders/{order_id}/ship")
def ship(order_id: str, request: Request) -> dict:
    require_session(request)
    with store.lock:
        order = store.orders.get(order_id)
        if not order:
            raise HTTPException(404, "Order not found")
        changed = order["status"] != "Shipped"
        if changed:
            order["status"] = "Shipped"
            store.shipments.append({"order_id": order_id, "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"order": deepcopy(order), "changed": changed, "shipment_count": len(store.shipments)}
