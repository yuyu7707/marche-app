from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from marche.extensions import db
from marche.models import Order, OrderStatus
from marche.services import inventory
from marche.services.inventory import OrderError
from marche.services.payment import PaymentError
from marche.views.cart import get_cart

bp = Blueprint("orders", __name__, url_prefix="/orders")


def get_own_order_or_403(order_id: int) -> Order:
    """本人（と管理者）の注文だけを返す。無ければ 404、他人の注文なら 403"""
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return order


def close_checkout_session(session_id: str) -> bool:
    """決済ページを使えない状態にする。使えなくなったら True。

    先に使えなくしてから注文を取り消す
    （取り消したあとに、ブラウザの戻るで決済ページに戻って支払えないように）。
    expire に失敗したときは、Stripe に状態を問い合わせて分ける。
    - 期限切れ（expired）: もう支払えないので True（取り消してよい）
    - それ以外（支払い済みの complete など）、または問い合わせも失敗: False（取り消さない）
    """
    payment = current_app.extensions["payment"]
    try:
        payment.expire_session(session_id)
    except PaymentError:
        try:
            return payment.retrieve_session(session_id)["status"] == "expired"
        except PaymentError:
            return False
    return True


@bp.route("/", methods=["POST"])
@login_required
def create():
    try:
        # 送るのはカート（商品IDと数量）だけ。金額はフォームから受け取らない
        order = inventory.create_order(current_user, get_cart())
    except OrderError as e:
        flash(str(e))
        return redirect(url_for("cart.show"))

    payment = current_app.extensions["payment"]
    try:
        session_id, checkout_url = payment.create_checkout_session(order)
    except PaymentError as e:
        # 決済ページを作れなかったら、作った注文を取り消す（カートは残す）
        inventory.cancel(order)
        flash(str(e))
        return redirect(url_for("cart.show"))

    order.stripe_checkout_session_id = session_id
    db.session.commit()
    session.pop("cart", None)
    # POST の結果として別の URL を GET で開かせるので 303
    return redirect(checkout_url, code=303)


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template("orders/show.html", order=order)


@bp.route("/<int:order_id>/cancel", methods=["GET", "POST"])
@login_required
def cancel(order_id: int):
    """Stripe の決済ページで「戻る」を押したときの画面"""
    order = get_own_order_or_403(order_id)
    if request.method == "POST":
        needs_closing = (
            order.status == OrderStatus.PENDING and order.stripe_checkout_session_id
        )
        if needs_closing and not close_checkout_session(
            order.stripe_checkout_session_id
        ):
            flash(
                "決済ページを閉じられませんでした。支払いが済んでいる可能性があります。"
            )
            return redirect(url_for("orders.show", order_id=order.id))
        if inventory.cancel(order):
            flash("注文を取り消しました")
        else:
            flash("この注文は取り消せません")
        return redirect(url_for("orders.show", order_id=order.id))
    return render_template("orders/cancel.html", order=order)


@bp.route("/<int:order_id>/complete")
@login_required
def complete(order_id: int):
    """支払いのあとに戻ってくる画面。いまは仮（支払いの確定は 08 章）"""
    order = get_own_order_or_403(order_id)
    return render_template("orders/complete.html", order=order)
