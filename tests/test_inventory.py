from datetime import timedelta

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, now_jst
from marche.services.inventory import PENDING_MINUTES, available_stock


def add_order(user, product, quantity, status=OrderStatus.PENDING, created_at=None):
    """注文を作る処理はまだ無いので、テストの中で Order と OrderItem を直接作る"""
    order = Order(
        user=user,
        status=status,
        total_amount=product.price * quantity,
        created_at=created_at or now_jst(),
    )
    order.items.append(
        OrderItem(
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
            subtotal=product.price * quantity,
        )
    )
    db.session.add(order)
    db.session.commit()
    return order


def test_注文が無ければ在庫の数のまま(make_product):
    product = make_product(stock=5)
    assert available_stock(product) == 5


def test_支払い待ちの注文があると買える数が減る(customer, make_product):
    product = make_product(stock=5)
    add_order(customer, product, 2)
    assert available_stock(product) == 3


def test_古い支払い待ちは在庫を押さえない(customer, make_product):
    product = make_product(stock=5)
    old = now_jst() - timedelta(minutes=PENDING_MINUTES + 1)
    add_order(customer, product, 2, created_at=old)
    assert available_stock(product) == 5


def test_35分以内の支払い待ちは在庫を押さえる(customer, make_product):
    product = make_product(stock=5)
    recent = now_jst() - timedelta(minutes=PENDING_MINUTES - 1)
    add_order(customer, product, 2, created_at=recent)
    assert available_stock(product) == 3


def test_別の商品の注文は数えない(customer, make_product):
    product = make_product(stock=5)
    other = make_product(name="味噌", stock=5)
    add_order(customer, other, 2)
    assert available_stock(product) == 5


def test_取り消した注文は在庫を押さえない(customer, make_product):
    product = make_product(stock=5)
    add_order(customer, product, 2, status=OrderStatus.CANCELLED)
    assert available_stock(product) == 5


def test_支払い済みの注文は二重に引かない(customer, make_product):
    # 支払い済みの分は、支払いが確定したときに stock から減らす。ここでは引かない
    product = make_product(stock=5)
    add_order(customer, product, 2, status=OrderStatus.PAID)
    assert available_stock(product) == 5


def test_買える数は0より小さくならない(customer, make_product):
    product = make_product(stock=1)
    add_order(customer, product, 3)
    assert available_stock(product) == 0
