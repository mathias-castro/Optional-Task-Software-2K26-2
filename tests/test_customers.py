ANA = {"name": "Ana", "email": "ana@example.com", "category": "premium"}


def test_customer_crud(client):
    created = client.post("/customers", json=ANA)
    assert created.status_code == 201
    customer = created.json()
    assert customer == {"id": customer["id"], **ANA}

    assert client.get(f"/customers/{customer['id']}").json() == customer

    updated = client.put(f"/customers/{customer['id']}", json={**ANA, "category": "business"})
    assert updated.status_code == 200
    assert updated.json()["category"] == "business"

    assert client.delete(f"/customers/{customer['id']}").status_code == 204
    assert client.get(f"/customers/{customer['id']}").status_code == 404


def test_customer_category_defaults_to_regular(client):
    response = client.post("/customers", json={"name": "Luis", "email": "luis@example.com"})
    assert response.json()["category"] == "regular"


def test_customers_are_listed_by_id(client):
    ids = [client.post("/customers", json={"name": name, "email": f"{name}@example.com"}).json()["id"]
           for name in ("b", "a", "c")]
    assert [customer["id"] for customer in client.get("/customers").json()] == sorted(ids)


def test_email_must_be_unique(client):
    client.post("/customers", json=ANA)
    other = client.post("/customers", json={"name": "Luis", "email": "luis@example.com"}).json()

    assert client.post("/customers", json={**ANA, "name": "Otra"}).status_code == 409
    assert client.put(f"/customers/{other['id']}", json={**ANA, "name": "Luis"}).status_code == 409
    assert client.get(f"/customers/{other['id']}").json()["email"] == "luis@example.com"
    assert len(client.get("/customers").json()) == 2


def test_missing_customer_returns_404(client):
    assert client.get("/customers/999").json() == {"detail": "Cliente no encontrado"}
    assert client.put("/customers/999", json=ANA).status_code == 404
    assert client.delete("/customers/999").status_code == 404


def test_invalid_customer_returns_422(client):
    assert client.post("/customers", json={**ANA, "email": "ana"}).status_code == 422
    assert client.post("/customers", json={**ANA, "category": "vip"}).status_code == 422
    assert client.post("/customers", json={**ANA, "name": ""}).status_code == 422


def test_customer_with_orders_cannot_be_deleted(client):
    customer = client.post("/customers", json=ANA).json()
    product = client.post("/products", json={"name": "Libro", "price_cents": 2500, "stock": 5}).json()
    client.post("/orders", json={"customer_id": customer["id"], "address": "Calle 1", "city": "Lima",
                                 "postal_code": "15001", "items": [{"product_id": product["id"], "quantity": 1}]})
    assert client.delete(f"/customers/{customer['id']}").status_code == 409
    assert client.get(f"/customers/{customer['id']}").status_code == 200
