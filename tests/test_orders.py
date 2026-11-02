import pytest
from fastapi.testclient import TestClient

from main import create_app


def prepare(client, stock=5):
    customer = client.post("/customers", json={"name": "Ana", "email": "ana@example.com", "category": "premium"})
    assert customer.status_code == 201
    product = client.post("/products", json={"name": "Libro", "price_cents": 2500, "stock": stock})
    assert product.status_code == 201
    return {"customer_id": customer.json()["id"], "address": "Calle 10", "city": "Lima",
            "postal_code": "15001", "shipping": "standard",
            "items": [{"product_id": product.json()["id"], "quantity": 2}]}


def test_confirmation_keeps_price_and_deducts_stock_once(client):
    payload = prepare(client)
    created = client.post("/orders", json=payload)
    assert created.status_code == 201
    order = created.json()
    assert order["total_cents"] == 5500
    product_id = payload["items"][0]["product_id"]
    assert client.put(f"/products/{product_id}", json={"name": "Libro", "price_cents": 9000, "stock": 5}).status_code == 200
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    assert client.get(f"/products/{product_id}").json()["stock"] == 3
    assert client.get(f"/orders/{order['id']}").json()["total_cents"] == 5500
    assert len(client.get(f"/orders/{order['id']}/notifications").json()) == 1


def test_insufficient_stock_rolls_back_all_lines(client):
    payload = prepare(client)
    second = client.post("/products", json={"name": "Agenda", "price_cents": 1000, "stock": 0}).json()
    payload["items"].append({"product_id": second["id"], "quantity": 1})
    order = client.post("/orders", json=payload).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    assert client.get(f"/products/{payload['items'][0]['product_id']}").json()["stock"] == 5
    assert client.get(f"/orders/{order['id']}").json()["status"] == "pending"
    assert client.get(f"/orders/{order['id']}/notifications").json() == []


def test_duplicate_lines_are_rejected(client):
    payload = prepare(client)
    payload["items"].append(payload["items"][0])
    assert client.post("/orders", json=payload).status_code == 422


def test_orders_survive_restart(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    with TestClient(create_app(path)) as client:
        payload = prepare(client)
        order = client.post("/orders", json=payload).json()
    with TestClient(create_app(path)) as client:
        assert client.get(f"/orders/{order['id']}").json()["total_cents"] == 5500


def test_quote_does_not_create_order_or_reserve_stock(client):
    payload = prepare(client)
    response = client.post("/orders/quote", json=payload)
    assert response.json() == {"subtotal_cents": 5000, "discount_cents": 500, "shipping_cents": 1000, "total_cents": 5500}
    assert client.get("/orders").json() == []
    assert client.get(f"/products/{payload['items'][0]['product_id']}").json()["stock"] == 5


def test_creating_order_does_not_check_or_reserve_stock(client):
    payload = prepare(client, stock=1)
    order = client.post("/orders", json=payload)
    assert order.status_code == 201
    assert order.json()["status"] == "pending"
    assert client.get(f"/products/{payload['items'][0]['product_id']}").json()["stock"] == 1
    assert client.post(f"/orders/{order.json()['id']}/confirm").status_code == 409


def test_order_keeps_category_after_customer_changes(client):
    payload = prepare(client)
    order = client.post("/orders", json=payload).json()
    client.put(f"/customers/{payload['customer_id']}", json={"name": "Ana", "email": "ana@example.com", "category": "regular"})
    saved = client.get(f"/orders/{order['id']}").json()
    assert saved["customer_category"] == "premium"
    assert saved["discount_cents"] == 500


def test_order_list_is_summary_sorted_by_id(client):
    payload = prepare(client)
    ids = [client.post("/orders", json=payload).json()["id"] for _ in range(3)]
    orders = client.get("/orders").json()
    assert [order["id"] for order in orders] == ids
    assert all("items" not in order for order in orders)
    assert client.get(f"/orders/{ids[0]}").json()["items"] == [
        {"product_id": payload["items"][0]["product_id"], "quantity": 2, "unit_price_cents": 2500}]


def test_put_replaces_order_with_current_prices_and_category(client):
    payload = prepare(client)
    order = client.post("/orders", json=payload).json()
    product_id = payload["items"][0]["product_id"]
    other = client.post("/products", json={"name": "Agenda", "price_cents": 4000, "stock": 5}).json()
    client.put(f"/products/{product_id}", json={"name": "Libro", "price_cents": 3000, "stock": 5})
    client.put(f"/customers/{payload['customer_id']}", json={"name": "Ana", "email": "ana@example.com", "category": "business"})

    replaced = client.put(f"/orders/{order['id']}", json={
        **payload, "shipping": "express", "city": "Cusco",
        "items": [{"product_id": product_id, "quantity": 1}, {"product_id": other["id"], "quantity": 2}]})
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["id"] == order["id"]
    assert body["customer_category"] == "business"
    assert body["city"] == "Cusco"
    assert (body["subtotal_cents"], body["discount_cents"], body["shipping_cents"], body["total_cents"]) == (11000, 1650, 2500, 11850)
    assert body["items"] == [{"product_id": product_id, "quantity": 1, "unit_price_cents": 3000},
                             {"product_id": other["id"], "quantity": 2, "unit_price_cents": 4000}]
    assert client.get(f"/orders/{order['id']}").json() == body


def test_put_with_missing_references_keeps_order(client):
    payload = prepare(client)
    order = client.post("/orders", json=payload).json()
    assert client.put("/orders/999", json=payload).status_code == 404
    assert client.put(f"/orders/{order['id']}", json={**payload, "customer_id": 999}).status_code == 404
    missing_product = {**payload, "items": [{"product_id": 999, "quantity": 1}]}
    assert client.put(f"/orders/{order['id']}", json=missing_product).status_code == 404
    assert client.get(f"/orders/{order['id']}").json() == order


def test_create_order_with_missing_references_returns_404(client):
    payload = prepare(client)
    assert client.post("/orders", json={**payload, "customer_id": 999}).json() == {"detail": "Cliente no encontrado"}
    assert client.post("/orders", json={**payload, "items": [{"product_id": 999, "quantity": 1}]}).status_code == 404
    assert client.post("/orders/quote", json={**payload, "customer_id": 999}).status_code == 404
    assert client.get("/orders").json() == []


def test_invalid_order_returns_422(client):
    payload = prepare(client)
    assert client.post("/orders", json={**payload, "items": []}).status_code == 422
    assert client.post("/orders", json={**payload, "shipping": "drone"}).status_code == 422
    assert client.post("/orders", json={**payload, "items": [{"product_id": 1, "quantity": 0}]}).status_code == 422
    assert client.get("/orders").json() == []


def test_delete_pending_order(client):
    order = client.post("/orders", json=prepare(client)).json()
    response = client.delete(f"/orders/{order['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/orders/{order['id']}").status_code == 404
    assert client.delete(f"/orders/{order['id']}").status_code == 404


def test_cancel_pending_order_does_not_change_stock(client):
    payload = prepare(client)
    order = client.post("/orders", json=payload).json()
    assert client.post(f"/orders/{order['id']}/cancel").json() == {"id": order["id"], "status": "cancelled"}
    assert client.get(f"/orders/{order['id']}").json()["status"] == "cancelled"
    assert client.get(f"/products/{payload['items'][0]['product_id']}").json()["stock"] == 5
    assert client.get(f"/orders/{order['id']}/notifications").json() == []


@pytest.mark.parametrize("final", ["confirm", "cancel"])
def test_final_orders_cannot_change(client, final):
    payload = prepare(client)
    order = client.post("/orders", json=payload).json()
    assert client.post(f"/orders/{order['id']}/{final}").status_code == 200
    saved = client.get(f"/orders/{order['id']}").json()

    assert client.put(f"/orders/{order['id']}", json=payload).status_code == 409
    assert client.delete(f"/orders/{order['id']}").status_code == 409
    assert client.post(f"/orders/{order['id']}/cancel").status_code == 409
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    assert client.get(f"/orders/{order['id']}").json() == saved


def test_confirmation_notifies_customer(client):
    payload = prepare(client)
    order = client.post("/orders", json=payload).json()
    response = client.post(f"/orders/{order['id']}/confirm").json()
    assert response["status"] == "confirmed"
    notifications = client.get(f"/orders/{order['id']}/notifications").json()
    assert [(n["recipient"], n["message"]) for n in notifications] == [("ana@example.com", response["message"])]


def test_missing_order_returns_404(client):
    assert client.get("/orders/999").json() == {"detail": "Pedido no encontrado"}
    assert client.delete("/orders/999").status_code == 404
    assert client.post("/orders/999/confirm").status_code == 404
    assert client.post("/orders/999/cancel").status_code == 404
    assert client.get("/orders/999/notifications").status_code == 404


def test_confirmation_survives_restart(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    with TestClient(create_app(path)) as client:
        payload = prepare(client)
        order = client.post("/orders", json=payload).json()
        client.post(f"/orders/{order['id']}/confirm")
    with TestClient(create_app(path)) as client:
        assert client.get(f"/orders/{order['id']}").json()["status"] == "confirmed"
        assert client.get(f"/products/{payload['items'][0]['product_id']}").json()["stock"] == 3
        assert len(client.get(f"/orders/{order['id']}/notifications").json()) == 1
