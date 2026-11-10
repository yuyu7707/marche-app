from types import SimpleNamespace

import pytest
import stripe

from marche.extensions import db
from marche.models import OrderStatus
from marche.services import inventory, payment
from marche.services.inventory import create_order
from marche.services.payment import PaymentError


def make_order(customer, make_product, quantity=1, **product_kwargs):
    product = make_product(**product_kwargs)
    return create_order(customer, {product.id: quantity})


# --- cancel（pending のときだけ取り消す） ---


def test_支払い待ちの注文は取り消せる(customer, make_product):
    order = make_order(customer, make_product)

    assert inventory.cancel(order) is True
    assert order.status == OrderStatus.CANCELLED


def test_支払い済みの注文は取り消せない(customer, make_product):
    order = make_order(customer, make_product)
    order.status = OrderStatus.PAID
    db.session.commit()

    assert inventory.cancel(order) is False
    assert order.status == OrderStatus.PAID


def test_取り消し済みの注文を取り消しても何も起きない(customer, make_product):
    order = make_order(customer, make_product)
    inventory.cancel(order)

    assert inventory.cancel(order) is False
    assert order.status == OrderStatus.CANCELLED


def test_取り消すと在庫の押さえが解ける(customer, make_product):
    product = make_product(stock=5)
    order = create_order(customer, {product.id: 2})
    assert inventory.available_stock(product) == 3

    inventory.cancel(order)

    assert inventory.available_stock(product) == 5


# --- 差し替えの仕組み ---


def test_テストではフェイクが差し込まれている(app, fake_payment):
    assert app.extensions["payment"] is fake_payment


def test_設定が無ければ本物のpaymentモジュールが入る(tmp_path):
    from marche import create_app

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        }
    )
    assert app.extensions["payment"] is payment


# --- payment.py（_client を差し替えて、本物の Stripe は呼ばない） ---


class FakeSessions:
    """client.v1.checkout.sessions の代わり。渡された引数を記録する"""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def create(self, params):
        self.calls.append(("create", params))
        if self.error:
            raise self.error

        class Session:
            id = "cs_test_abc"
            url = "https://checkout.stripe.test/cs_test_abc"

        return Session()

    def retrieve(self, session_id):
        self.calls.append(("retrieve", session_id))
        if self.error:
            raise self.error
        return {"id": session_id}

    def expire(self, session_id):
        self.calls.append(("expire", session_id))
        if self.error:
            raise self.error


def fake_client(sessions):
    """client.v1.checkout.sessions と同じ形のオブジェクトを作る"""
    return SimpleNamespace(
        v1=SimpleNamespace(checkout=SimpleNamespace(sessions=sessions))
    )


@pytest.fixture
def sessions(app, monkeypatch):
    fake = FakeSessions()
    monkeypatch.setattr(payment, "_client", lambda: fake_client(fake))
    return fake


def test_キーはStripeClientに渡す(app, monkeypatch):
    app.config["STRIPE_SECRET_KEY"] = "sk_test_dummy"
    made = {}

    def fake_stripe_client(key):
        made["key"] = key
        return "client"

    monkeypatch.setattr(payment.stripe, "StripeClient", fake_stripe_client)

    with app.app_context():
        assert payment._client() == "client"
    assert made["key"] == "sk_test_dummy"


def test_期限切れにするとStripeのexpireが呼ばれる(sessions):
    payment.expire_session("cs_test_abc")

    assert sessions.calls == [("expire", "cs_test_abc")]


def test_取得するとStripeのretrieveが呼ばれる(sessions):
    assert payment.retrieve_session("cs_test_abc") == {"id": "cs_test_abc"}


def test_Stripeのエラーは日本語のPaymentErrorになる(sessions):
    sessions.error = stripe.StripeError("boom")

    with pytest.raises(PaymentError) as e:
        payment.expire_session("cs_test_abc")
    assert "決済ページを閉じられませんでした" in str(e.value)

    with pytest.raises(PaymentError) as e:
        payment.retrieve_session("cs_test_abc")
    assert "支払いの状況を確認できませんでした" in str(e.value)


def test_Checkout_Sessionの金額は注文明細から円のまま渡す(
    app, customer, make_product, sessions
):
    spinach = make_product(name="ほうれん草", price=300)
    miso = make_product(name="手づくり味噌", price=1200)
    order = create_order(customer, {spinach.id: 2, miso.id: 1})

    with app.test_request_context():
        session_id, url = payment.create_checkout_session(order)

    assert (session_id, url) == (
        "cs_test_abc",
        "https://checkout.stripe.test/cs_test_abc",
    )
    (name, params) = sessions.calls[0]
    assert name == "create"
    assert params["mode"] == "payment"
    # 金額は明細の単価そのまま（100倍しない）、通貨は jpy
    assert params["line_items"] == [
        {
            "quantity": 2,
            "price_data": {
                "currency": "jpy",
                "unit_amount": 300,
                "product_data": {"name": "ほうれん草"},
            },
        },
        {
            "quantity": 1,
            "price_data": {
                "currency": "jpy",
                "unit_amount": 1200,
                "product_data": {"name": "手づくり味噌"},
            },
        },
    ]
    assert params["metadata"] == {"order_id": str(order.id)}
    assert params["customer_email"] == customer.email
    assert params["success_url"].endswith(
        f"/orders/{order.id}/complete?session_id={{CHECKOUT_SESSION_ID}}"
    )
    assert params["cancel_url"].endswith(f"/orders/{order.id}/cancel")


def test_決済ページの期限は31分後(app, customer, make_product, sessions):
    import time

    order = make_order(customer, make_product)

    before = int(time.time())
    with app.test_request_context():
        payment.create_checkout_session(order)

    expires_at = sessions.calls[0][1]["expires_at"]
    assert before + 31 * 60 <= expires_at <= int(time.time()) + 31 * 60


def test_Session作成の失敗は日本語のPaymentErrorになる(
    app, customer, make_product, sessions
):
    sessions.error = stripe.StripeError("boom")
    order = make_order(customer, make_product)

    with app.test_request_context(), pytest.raises(PaymentError) as e:
        payment.create_checkout_session(order)

    assert "決済ページを開けませんでした" in str(e.value)
