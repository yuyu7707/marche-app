"""Stripe（決済）を呼ぶコードは、このファイルだけに書く。

views からは current_app.extensions["payment"] 経由で呼ぶ（テストではフェイクに差し替わる）。
"""

import time

import stripe
from flask import current_app, url_for

# Stripe の Checkout Session は30分より短くできない。時計のずれを見込んで31分。
# 在庫の押さえ（35分、services/inventory.py）より先に、決済ページが使えなくなる
CHECKOUT_EXPIRES_MINUTES = 31


class PaymentError(Exception):
    """決済サービスの呼び出しに失敗したとき。メッセージは画面に出せる日本語"""


def _client() -> stripe.StripeClient:
    return stripe.StripeClient(current_app.config["STRIPE_SECRET_KEY"])


def create_checkout_session(order) -> tuple[str, str]:
    """Checkout Session を作り、(session_id, 決済ページの URL) を返す"""
    line_items = [
        {
            "quantity": item.quantity,
            "price_data": {
                "currency": "jpy",
                # 金額は注文明細（DB）から取る。円のまま渡す（日本円に小数は無いので100倍しない）
                "unit_amount": item.unit_price,
                "product_data": {"name": item.product_name},
            },
        }
        for item in order.items
    ]
    complete_url = url_for("orders.complete", order_id=order.id, _external=True)
    try:
        session = _client().v1.checkout.sessions.create(
            {
                "mode": "payment",
                "line_items": line_items,
                "customer_email": order.user.email,
                "metadata": {"order_id": str(order.id)},
                # {CHECKOUT_SESSION_ID} は Stripe が実際の ID に置き換える
                # （f-string にすると Python が {} を解釈するので、+ でつなぐ）
                "success_url": complete_url + "?session_id={CHECKOUT_SESSION_ID}",
                "cancel_url": url_for(
                    "orders.cancel", order_id=order.id, _external=True
                ),
                "expires_at": int(time.time()) + CHECKOUT_EXPIRES_MINUTES * 60,
            }
        )
    except stripe.StripeError as e:
        current_app.logger.warning("Stripe の呼び出しに失敗: %r", e)
        raise PaymentError(
            "決済ページを開けませんでした。時間をおいてもう一度お試しください。"
        ) from e
    return session.id, session.url


def retrieve_session(session_id: str):
    """Checkout Session を Stripe から取得する（支払い状態の確認に使う）"""
    try:
        return _client().v1.checkout.sessions.retrieve(session_id)
    except stripe.StripeError as e:
        current_app.logger.warning("Stripe の呼び出しに失敗: %r", e)
        raise PaymentError(
            "支払いの状況を確認できませんでした。時間をおいてもう一度お試しください。"
        ) from e


def expire_session(session_id: str) -> None:
    """Checkout Session を期限切れにして、決済ページで支払えなくする"""
    try:
        _client().v1.checkout.sessions.expire(session_id)
    except stripe.StripeError as e:
        current_app.logger.warning("Stripe の呼び出しに失敗: %r", e)
        raise PaymentError(
            "決済ページを閉じられませんでした。支払いが済んでいる可能性があります。"
        ) from e
