from datetime import timedelta

from sqlalchemy import func, select, update

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, now_jst

# 支払い待ちの注文が在庫を押さえる時間（分）。決済ページの期限（31分）より少し長くする
PENDING_MINUTES = 35

# 1商品あたり、1回の注文で買える数
MAX_QUANTITY = 10


class OrderError(Exception):
    """注文を作れないときの例外。メッセージは画面にそのまま出せる日本語"""


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


def create_order(user, cart: dict[int, int]) -> Order:
    """カートの中身から支払い待ちの注文を作る。金額は DB の価格から計算する"""
    # 先に全部確認する。この間は DB に何も追加しない
    lines = []
    for product_id, quantity in cart.items():
        product = db.session.get(Product, product_id)
        if product is None or not product.is_published:
            raise OrderError(
                "販売を終了した商品がカートに入っています。カートを確認してください。"
            )
        if not 1 <= quantity <= MAX_QUANTITY:
            raise OrderError(
                f"「{product.name}」の数量は1〜{MAX_QUANTITY}で選んでください。"
            )
        available = available_stock(product)
        if quantity > available:
            raise OrderError(
                f"「{product.name}」の在庫が足りません（残り {available}）。"
            )
        lines.append((product, quantity))

    if not lines:
        raise OrderError("カートが空です。")

    # 確認がすべて通ってから、注文と明細を作る（commit は1回だけ）
    order = Order(user=user, status=OrderStatus.PENDING, total_amount=0)
    for product, quantity in lines:
        order.items.append(
            OrderItem(
                product=product,
                product_name=product.name,
                unit_price=product.price,
                quantity=quantity,
                subtotal=product.price * quantity,
            )
        )
    order.total_amount = sum(item.subtotal for item in order.items)
    db.session.add(order)
    db.session.commit()
    return order


def cancel(order: Order) -> bool:
    """支払い待ち（pending）の注文だけを取り消しにする。取り消せたら True。

    「読んで、pending なら書く」の2段階にせず、1つの UPDATE 文で条件ごと更新する。
    支払い済みや取り消し済みの注文は変えない（False を返す）。
    """
    result = db.session.execute(
        update(Order)
        .where(Order.id == order.id, Order.status == OrderStatus.PENDING)
        .values(status=OrderStatus.CANCELLED)
    )
    db.session.commit()
    db.session.refresh(order)
    return result.rowcount == 1
