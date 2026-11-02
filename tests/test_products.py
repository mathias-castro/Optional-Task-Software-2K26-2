from fastapi.testclient import TestClient

from main import create_app

BOOK = {"name": "Libro", "price_cents": 2500, "stock": 5}


def test_product_crud(client):
    created = client.post("/products", json={**BOOK, "name": "  Libro  "})
    assert created.status_code == 201
    product = created.json()
    assert product == {"id": product["id"], **BOOK}

    assert client.get(f"/products/{product['id']}").json() == product

    updated = client.put(f"/products/{product['id']}", json={"name": "Libro 2", "price_cents": 3000, "stock": 1})
    assert updated.status_code == 200
    assert updated.json() == {"id": product["id"], "name": "Libro 2", "price_cents": 3000, "stock": 1}

    deleted = client.delete(f"/products/{product['id']}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(f"/products/{product['id']}").status_code == 404


def test_products_are_listed_by_id(client):
    ids = [client.post("/products", json={**BOOK, "name": name}).json()["id"] for name in ("B", "A", "C")]
    assert [product["id"] for product in client.get("/products").json()] == sorted(ids)


def test_missing_product_returns_404(client):
    assert client.get("/products/999").json() == {"detail": "Producto no encontrado"}
    assert client.put("/products/999", json=BOOK).status_code == 404
    assert client.delete("/products/999").status_code == 404


def test_invalid_product_returns_422(client):
    assert client.post("/products", json={**BOOK, "price_cents": 0}).status_code == 422
    assert client.post("/products", json={**BOOK, "stock": -1}).status_code == 422
    assert client.post("/products", json={**BOOK, "extra": True}).status_code == 422
    assert client.get("/products").json() == []


def test_product_in_order_cannot_be_deleted(client):
    product = client.post("/products", json=BOOK).json()
    customer = client.post("/customers", json={"name": "Ana", "email": "ana@example.com"}).json()
    client.post("/orders", json={"customer_id": customer["id"], "address": "Calle 1", "city": "Lima",
                                 "postal_code": "15001", "items": [{"product_id": product["id"], "quantity": 1}]})
    response = client.delete(f"/products/{product['id']}")
    assert response.status_code == 409
    assert client.get(f"/products/{product['id']}").status_code == 200


def test_products_survive_restart(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    with TestClient(create_app(path)) as client:
        product = client.post("/products", json=BOOK).json()
    with TestClient(create_app(path)) as client:
        assert client.get(f"/products/{product['id']}").json() == product


def test_unavailable_database_returns_503(tmp_path):
    client = TestClient(create_app(tmp_path / "missing" / "store.sqlite3"))
    response = client.get("/products")
    assert response.status_code == 503
    assert "detail" in response.json()
