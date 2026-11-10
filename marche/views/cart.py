from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from marche.extensions import db
from marche.models import Product
from marche.services.inventory import MAX_QUANTITY, available_stock

bp = Blueprint("cart", __name__, url_prefix="/cart")


def get_cart() -> dict[int, int]:
    """セッションのカートを {商品ID: 数量} で返す（セッションのキーは文字列）"""
    raw = session.get("cart")
    if not isinstance(raw, dict):
        return {}
    cart = {}
    for key, value in raw.items():
        try:
            cart[int(key)] = int(value)
        except TypeError, ValueError:
            continue  # 壊れた値は捨てる
    return cart


def save_cart(cart: dict[int, int]) -> None:
    session["cart"] = {str(k): v for k, v in cart.items() if v > 0}


def max_quantity(product: Product) -> int:
    """1商品について、カートに入れられる上限。1回の上限といま買える数の小さい方"""
    return min(MAX_QUANTITY, available_stock(product))


def read_quantity() -> int | None:
    """フォームの数量を整数で返す。数字でなければ None"""
    return request.form.get("quantity", type=int)


@bp.route("/")
def show():
    lines = []
    total = 0
    cart = get_cart()
    removed = False
    for product_id, quantity in list(cart.items()):
        product = db.session.get(Product, product_id)
        if product is None or not product.is_published:
            # 非公開・削除された商品は、表示しないだけでなくカートからも外す
            # （外さないと、画面に出ない商品のせいで注文できなくなる）
            del cart[product_id]
            removed = True
            continue
        subtotal = product.price * quantity
        total += subtotal
        lines.append(
            {
                "product": product,
                "quantity": quantity,
                "subtotal": subtotal,
                "max": max(max_quantity(product), quantity),
            }
        )
    if removed:
        save_cart(cart)
        flash("販売を終了した商品をカートから外しました")
    return render_template("cart/show.html", lines=lines, total=total)


@bp.route("/add/<int:product_id>", methods=["POST"])
def add(product_id: int):
    product = db.get_or_404(Product, product_id)
    if not product.is_published:
        flash("この商品は購入できません")
        return redirect(url_for("shop.index"))

    quantity = read_quantity()
    cart = get_cart()
    limit = max_quantity(product)
    if quantity is None or quantity < 1:
        flash("数量を選んでください")
    elif cart.get(product_id, 0) + quantity > limit:
        flash(f"「{product.name}」はカートに合計{limit}個までしか入れられません")
    else:
        cart[product_id] = cart.get(product_id, 0) + quantity
        save_cart(cart)
        flash(f"「{product.name}」をカートに入れました")
        return redirect(url_for("cart.show"))
    return redirect(url_for("shop.show", product_id=product_id))


@bp.route("/update/<int:product_id>", methods=["POST"])
def update(product_id: int):
    cart = get_cart()
    if product_id not in cart:
        return redirect(url_for("cart.show"))

    quantity = read_quantity()
    if quantity is None:
        flash("数量は数字で入力してください")
        return redirect(url_for("cart.show"))

    if quantity <= 0:
        del cart[product_id]
        save_cart(cart)
        return redirect(url_for("cart.show"))

    product = db.session.get(Product, product_id)
    if product is None or not product.is_published:
        del cart[product_id]
        save_cart(cart)
        flash("販売を終了した商品をカートから外しました")
    elif quantity > max_quantity(product):
        limit = max_quantity(product)
        if limit == 0:
            flash(
                f"「{product.name}」は売り切れました。数量を0にしてカートから外してください"
            )
        else:
            flash(f"「{product.name}」は{limit}個までしか買えません")
    else:
        cart[product_id] = quantity
        save_cart(cart)
    return redirect(url_for("cart.show"))
