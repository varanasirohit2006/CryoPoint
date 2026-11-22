# Local chaos testbed

The shopping store and operations portal are deterministic browser fixtures. All products, accounts, and shipment actions are local demonstration data.

Run the API from the repository root:

```powershell
.venv\Scripts\python.exe -m uvicorn chaos_testbed.api.main:app --host 127.0.0.1 --port 8001
npm.cmd run dev:testbed
```

Open `http://127.0.0.1:3000/shop` or `http://127.0.0.1:3000/admin`. The portal account is `demo` / `demo-password`. Fixture order IDs include `1042`, `2089`, and `3015`.

## API contract

| Endpoint | Behavior |
| --- | --- |
| `GET /api/products?q=&max_price=&sort_by=` | Query catalog; sort by `rating_desc` or `price_asc`; expose applied search state |
| `POST /api/login` | Accept `{ "username": "demo", "password": "demo-password" }` and set an HTTP-only session cookie |
| `GET /api/orders?q=1042` | Find an order in an authenticated session |
| `POST /api/orders/1042/ship` | Update a fixture order; repeated calls do not duplicate shipments |
| `GET /api/state` | Independent fixture observations, shipment audit, and UI mutation state |
| `POST /api/reset` | Accept `{ "seed": 42 }`; restore fixtures and invalidate sessions |
| `POST /api/mutations` | Accept `{ "site_id": "shop", "mutation": "compound", "seed": 7 }` |

Mutations are `none`, `selector_drift`, `spatial_shift`, `copy_mutation`, `dom_nesting`, `obstacle`, or `compound`. The web application polls mutation state. These controls alter interaction targets while preserving result/status markers. A combined redesign adds a dismissible notice, changes target names and identifiers, and moves the relevant action behind an expandable menu.

## Goal observations

Shopping results expose `data-testid="search-results"` with `data-query`, `data-max-price`, and `data-sort-by`. Product cards expose `data-product-name`, `data-price`, and `data-rating`. These represent applied controls, rather than draft input values.

The portal exposes `data-testid="order-detail"`, `data-order-id`, and `data-testid="order-status"`. The status becomes `Shipped` only after the API accepts the shipment action.

The runtime discovers controls through its browser observations. It must not use testbed mutation endpoints as a solution oracle. Benchmark assertions may independently inspect fixture state.

Run the fixture API tests with:

```powershell
.venv\Scripts\python.exe -m pytest chaos_testbed/tests -q
```
