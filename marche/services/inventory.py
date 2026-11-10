from datetime import timedelta

from sqlalchemy import func, select

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, now_jst

# 支払い待ちの注文が在庫を押さえる時間（分）。決済ページの期限（31分）より少し長くする
PENDING_MINUTES = 35


def reserved_quantity(product: Product) -> int:
    """支払い待ち（PENDING_MINUTES 分以内）の注文で押さえられている数。

    支払い済みの分は、支払いが確定したときに product.stock から減らすので、ここでは数えない
    （数えると二重に引いてしまう）。取り消した注文も数えない。
    """
    since = now_jst() - timedelta(minutes=PENDING_MINUTES)
    total = db.session.scalar(
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order)
        .where(
            OrderItem.product_id == product.id,
            Order.status == OrderStatus.PENDING,
            Order.created_at >= since,
        )
    )
    return int(total)


def available_stock(product: Product) -> int:
    """いま買える数 = 在庫数 − 支払い待ちで押さえられている数（0 未満にはしない）"""
    return max(0, product.stock - reserved_quantity(product))
