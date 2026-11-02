import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.customers import CustomerInput
from app.database import Database
from app.orders import OrderInput, OrderService, calculate_totals
from app.products import ProductInput


@pytest.fixture
def service(tmp_path):
    database = Database(tmp_path / "rules.sqlite3")
    database.initialize()
    return OrderService(database)


def add_customer(service, category):
    with service.database.connect() as connection:
        return connection.execute(
            "INSERT INTO customers(name, email, category) VALUES (?, ?, ?)",
            ("Cliente", f"{category}@example.com", category),
        ).lastrowid


def add_product(service, price_cents, stock=10):
    with service.database.connect() as connection:
        return connection.execute(
            "INSERT INTO products(name, price_cents, stock) VALUES (?, ?, ?)", ("Producto", price_cents, stock),
        ).lastrowid


def order(customer_id, product_id, quantity=1, shipping="standard"):
    return OrderInput(customer_id=customer_id, address="Calle 1", city="Lima", postal_code="15001",
                      shipping=shipping, items=[{"product_id": product_id, "quantity": quantity}])


@pytest.mark.parametrize("category, discount", [("regular", 0), ("premium", 500), ("business", 750)])
def test_discount_by_category(service, category, discount):
    quote = service.quote(order(add_customer(service, category), add_product(service, 2500), quantity=2))
    assert quote["subtotal_cents"] == 5000
    assert quote["discount_cents"] == discount


@pytest.mark.parametrize("category, discount", [("premium", 99), ("business", 149)])
def test_discount_is_truncated_to_whole_cents(service, category, discount):
    quote = service.quote(order(add_customer(service, category), add_product(service, 999)))
    assert quote["discount_cents"] == discount


@pytest.mark.parametrize("shipping, subtotal, cost", [
    ("pickup", 500, 0),
    ("standard", 9_999, 1_000),
    ("standard", 10_000, 0),
    ("express", 50_000, 2_500),
])
def test_shipping_cost(service, shipping, subtotal, cost):
    quote = service.quote(order(add_customer(service, "regular"), add_product(service, subtotal), shipping=shipping))
    assert quote["shipping_cents"] == cost


def test_free_shipping_threshold_uses_subtotal_before_discount(service):
    quote = service.quote(order(add_customer(service, "business"), add_product(service, 10_000)))
    assert quote == {"subtotal_cents": 10_000, "discount_cents": 1_500, "shipping_cents": 0, "total_cents": 8_500}


def test_total_is_subtotal_minus_discount_plus_shipping(service):
    quote = service.quote(order(add_customer(service, "premium"), add_product(service, 1_234), quantity=3))
    assert quote == {"subtotal_cents": 3_702, "discount_cents": 370, "shipping_cents": 1_000, "total_cents": 4_332}


@pytest.mark.parametrize("shipping", ["pickup", "standard", "express"])
def test_saved_order_uses_same_totals_as_quote(service, shipping):
    data = order(add_customer(service, "business"), add_product(service, 3_333), quantity=3, shipping=shipping)
    saved = service.create(data)
    quote = service.quote(data)
    assert {key: saved[key] for key in quote} == quote


def test_quote_requires_existing_customer_and_products(service):
    product_id = add_product(service, 1000)
    with pytest.raises(HTTPException) as error:
        service.quote(order(999, product_id))
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        service.quote(order(add_customer(service, "regular"), 999))
    assert error.value.status_code == 404


def test_order_input_rejects_repeated_products():
    with pytest.raises(ValidationError):
        OrderInput(customer_id=1, address="Calle 1", city="Lima", postal_code="15001",
                   items=[{"product_id": 1, "quantity": 1}, {"product_id": 1, "quantity": 2}])


@pytest.mark.parametrize("change", [
    {"items": []},
    {"items": [{"product_id": 1, "quantity": 0}]},
    {"items": [{"product_id": 1, "quantity": "2"}]},
    {"shipping": "drone"},
    {"address": "   "},
    {"coupon": "GRATIS"},
])
def test_order_input_rejects_invalid_values(change):
    data = {"customer_id": 1, "address": "Calle 1", "city": "Lima", "postal_code": "15001",
            "items": [{"product_id": 1, "quantity": 1}]}
    with pytest.raises(ValidationError):
        OrderInput(**{**data, **change})


def test_order_input_defaults_to_standard_shipping():
    data = OrderInput(customer_id=1, address=" Calle 1 ", city="Lima", postal_code="15001",
                      items=[{"product_id": 1, "quantity": 1}])
    assert data.shipping == "standard"
    assert data.address == "Calle 1"


@pytest.mark.parametrize("change", [{"price_cents": 0}, {"stock": -1}, {"price_cents": "100"}, {"name": ""}])
def test_product_input_rejects_invalid_values(change):
    with pytest.raises(ValidationError):
        ProductInput(**{"name": "Libro", "price_cents": 100, "stock": 1, **change})


def test_customer_input_validation():
    assert CustomerInput(name="Ana", email="ana@example.com").category == "regular"
    with pytest.raises(ValidationError):
        CustomerInput(name="Ana", email="no-es-correo")
    with pytest.raises(ValidationError):
        CustomerInput(name="Ana", email="ana@example.com", category="vip")


@pytest.mark.parametrize("subtotal, category, shipping, expected", [
    (5_000, "premium", "standard", (5_000, 500, 1_000, 5_500)),
    (999, "business", "pickup", (999, 149, 0, 850)),
    (9_999, "regular", "standard", (9_999, 0, 1_000, 10_999)),
    (10_000, "business", "standard", (10_000, 1_500, 0, 8_500)),
    (20_000, "regular", "express", (20_000, 0, 2_500, 22_500)),
])
def test_calculate_totals(subtotal, category, shipping, expected):
    totals = calculate_totals(subtotal, category, shipping)
    assert tuple(totals.values()) == expected
    assert list(totals) == ["subtotal_cents", "discount_cents", "shipping_cents", "total_cents"]
