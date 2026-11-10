import pytest
from sqlalchemy import func, select

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus
from marche.services.inventory import MAX_QUANTITY, OrderError, create_order


def count(model):
    return db.session.scalar(select(func.count()).select_from(model))


def test_合計はDBの価格かける数量になる(customer, make_product):
    spinach = make_product(name="ほうれん草", price=300)
    miso = make_product(name="手づくり味噌", price=1200)

    order = create_order(customer, {spinach.id: 2, miso.id: 1})

    assert order.status == OrderStatus.PENDING
    assert order.user_id == customer.id
    assert order.total_amount == 300 * 2 + 1200
    assert len(order.items) == 2


def test_明細に注文時点の商品名と単価が入る(customer, make_product):
    product = make_product(name="ほうれん草", price=300)

    order = create_order(customer, {product.id: 2})
    product.name = "名前を変更"
    product.price = 999
    db.session.commit()

    item = db.session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
    assert item.product_name == "ほうれん草"
    assert item.unit_price == 300
    assert item.quantity == 2
    assert item.subtotal == 600


def test_在庫を超えるとOrderErrorで注文は作られない(customer, make_product):
    product = make_product(stock=3)

    with pytest.raises(OrderError, match="在庫が足りません"):
        create_order(customer, {product.id: 4})

    assert count(Order) == 0
    assert count(OrderItem) == 0


def test_支払い待ちで押さえられた分は買えない(customer, make_product):
    product = make_product(stock=3)
    create_order(customer, {product.id: 2})

    with pytest.raises(OrderError, match="残り 1"):
        create_order(customer, {product.id: 2})

    assert count(Order) == 1


def test_非公開の商品はOrderError(customer, make_product):
    product = make_product(is_published=False)

    with pytest.raises(OrderError, match="販売を終了"):
        create_order(customer, {product.id: 1})

    assert count(Order) == 0


def test_存在しない商品はOrderError(customer):
    with pytest.raises(OrderError, match="販売を終了"):
        create_order(customer, {9999: 1})

    assert count(Order) == 0


@pytest.mark.parametrize("quantity", [0, -1, MAX_QUANTITY + 1])
def test_数量が範囲外ならOrderError(customer, make_product, quantity):
    product = make_product(stock=100)

    with pytest.raises(OrderError, match="数量は1〜10"):
        create_order(customer, {product.id: quantity})

    assert count(Order) == 0


def test_数量の上限ちょうどは注文できる(customer, make_product):
    product = make_product(stock=100, price=100)

    order = create_order(customer, {product.id: MAX_QUANTITY})

    assert order.total_amount == 100 * MAX_QUANTITY


def test_空のカートはOrderError(customer):
    with pytest.raises(OrderError, match="カートが空"):
        create_order(customer, {})

    assert count(Order) == 0


def test_途中で失敗したら先の商品の注文も残らない(customer, make_product):
    ok = make_product(name="ほうれん草", stock=10)
    ng = make_product(name="味噌", stock=1)

    with pytest.raises(OrderError):
        create_order(customer, {ok.id: 2, ng.id: 5})

    assert count(Order) == 0
    assert count(OrderItem) == 0


# ---- 画面（カートと注文）----


def set_cart(client, cart):
    with client.session_transaction() as sess:
        sess["cart"] = cart


def get_cart_session(client):
    with client.session_transaction() as sess:
        return sess.get("cart")


def test_カートに入れて注文すると合計はDBの価格で計算される(
    client, customer, make_product, login
):
    product = make_product(price=300, stock=10)
    login(customer)
    client.post(f"/cart/add/{product.id}", data={"quantity": 2})
    assert get_cart_session(client) == {str(product.id): 2}

    response = client.post("/orders/", data={"total_amount": 1})

    order = db.session.scalars(select(Order)).one()
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/orders/{order.id}")
    assert order.total_amount == 600
    assert order.user_id == customer.id
    assert not get_cart_session(client)


def test_未ログインの注文はログイン画面へ戻される(client, make_product):
    product = make_product()
    set_cart(client, {str(product.id): 1})

    response = client.post("/orders/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert count(Order) == 0


def test_注文詳細は本人と管理者だけが見られる(
    client, customer, producer, admin, make_product, login
):
    product = make_product()
    order = create_order(customer, {product.id: 1})

    assert "/login" in client.get(f"/orders/{order.id}").headers["Location"]

    login(producer)
    assert client.get(f"/orders/{order.id}").status_code == 403
    client.get("/logout")

    login(customer)
    assert client.get(f"/orders/{order.id}").status_code == 200
    client.get("/logout")

    login(admin)
    assert client.get(f"/orders/{order.id}").status_code == 200


def test_存在しない注文は404(client, customer, login):
    login(customer)
    assert client.get("/orders/9999").status_code == 404


def test_未ログインのカートにはログインして注文するリンクが出る(client, make_product):
    product = make_product()
    client.post(f"/cart/add/{product.id}", data={"quantity": 1})

    text = client.get("/cart/").text

    assert "ログインして注文する" in text
    assert "/login?next=/cart/" in text
    assert "注文して支払いへ進む" not in text


def test_ログイン済みのカートには注文ボタンが出る(
    client, customer, make_product, login
):
    product = make_product()
    login(customer)
    client.post(f"/cart/add/{product.id}", data={"quantity": 1})

    text = client.get("/cart/").text

    assert "注文して支払いへ進む" in text
    assert "ログインして注文する" not in text


def test_カートに入れた商品が一覧に出る(client, make_product):
    product = make_product(name="ほうれん草", price=300)
    client.post(f"/cart/add/{product.id}", data={"quantity": 3})

    text = client.get("/cart/").text

    assert "ほうれん草" in text
    assert "900円" in text


def test_在庫を超える数はカートに入らない(client, make_product):
    product = make_product(stock=3)

    response = client.post(
        f"/cart/add/{product.id}", data={"quantity": 4}, follow_redirects=True
    )

    assert "合計3個までしか入れられません" in response.text
    assert not get_cart_session(client)


def test_11個以上はカートに入らない(client, make_product):
    product = make_product(stock=100)

    response = client.post(
        f"/cart/add/{product.id}", data={"quantity": 11}, follow_redirects=True
    )

    assert "合計10個までしか入れられません" in response.text
    assert not get_cart_session(client)


def test_合計が上限を超える追加はカートに入らない(client, make_product):
    product = make_product(stock=100)
    client.post(f"/cart/add/{product.id}", data={"quantity": 8})

    response = client.post(
        f"/cart/add/{product.id}", data={"quantity": 3}, follow_redirects=True
    )

    assert "合計10個までしか入れられません" in response.text
    assert get_cart_session(client) == {str(product.id): 8}


@pytest.mark.parametrize("quantity", ["abc", "", "0", "-1"])
def test_数量が不正なら追加されず500にもならない(client, make_product, quantity):
    product = make_product()

    response = client.post(
        f"/cart/add/{product.id}", data={"quantity": quantity}, follow_redirects=True
    )

    assert response.status_code == 200
    assert "数量を選んでください" in response.text
    assert not get_cart_session(client)


def test_非公開の商品はカートに追加できない(client, make_product):
    product = make_product(is_published=False)

    response = client.post(
        f"/cart/add/{product.id}", data={"quantity": 1}, follow_redirects=True
    )

    assert "この商品は購入できません" in response.text
    assert not get_cart_session(client)


def test_存在しない商品はカートに追加できない(client):
    assert client.post("/cart/add/9999", data={"quantity": 1}).status_code == 404


def test_数量を変更できる(client, make_product):
    product = make_product(stock=10)
    set_cart(client, {str(product.id): 1})

    client.post(f"/cart/update/{product.id}", data={"quantity": 4})

    assert get_cart_session(client) == {str(product.id): 4}


def test_数量を0にするとカートから外れる(client, make_product):
    product = make_product()
    set_cart(client, {str(product.id): 2})

    response = client.post(f"/cart/update/{product.id}", data={"quantity": 0})

    assert response.status_code == 302
    assert not get_cart_session(client)


def test_変更の数量が数字以外でも500にならず変わらない(client, make_product):
    product = make_product()
    set_cart(client, {str(product.id): 2})

    response = client.post(
        f"/cart/update/{product.id}", data={"quantity": "abc"}, follow_redirects=True
    )

    assert response.status_code == 200
    assert "数量は数字で入力してください" in response.text
    assert get_cart_session(client) == {str(product.id): 2}


def test_変更で在庫を超える数にはできない(client, make_product):
    product = make_product(stock=3)
    set_cart(client, {str(product.id): 1})

    response = client.post(
        f"/cart/update/{product.id}", data={"quantity": 5}, follow_redirects=True
    )

    assert "3個までしか買えません" in response.text
    assert get_cart_session(client) == {str(product.id): 1}


def test_カートにない商品の変更は何も起きない(client, make_product):
    product = make_product()

    response = client.post(f"/cart/update/{product.id}", data={"quantity": 3})

    assert response.status_code == 302
    assert not get_cart_session(client)


def test_非公開になった商品はカートに表示されない(client, make_product):
    product = make_product(name="消える商品")
    set_cart(client, {str(product.id): 1})
    product.is_published = False
    db.session.commit()

    response = client.get("/cart/")

    assert response.status_code == 200
    assert "消える商品" not in response.text
    assert "カートは空です" in response.text


def test_非公開になった商品は注文できずカートへ戻る(
    client, customer, make_product, login
):
    product = make_product()
    login(customer)
    client.post(f"/cart/add/{product.id}", data={"quantity": 1})
    product.is_published = False
    db.session.commit()

    response = client.post("/orders/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/cart/")
    assert count(Order) == 0
    followed = client.get("/cart/")
    assert "販売を終了した商品がカートに入っています" in followed.text


def test_空のカートで注文するとカートへ戻る(client, customer, login):
    login(customer)

    response = client.post("/orders/", follow_redirects=True)

    assert "カートが空です" in response.text
    assert count(Order) == 0


def test_商品詳細は買える数があればカートに入れるが出る(client, make_product):
    product = make_product(stock=5)

    assert "カートに入れる" in client.get(f"/products/{product.id}").text


def test_商品詳細は売り切れならカートに入れるが出ない(client, make_product):
    product = make_product(stock=0)

    text = client.get(f"/products/{product.id}").text

    assert "売り切れ" in text
    assert "カートに入れる" not in text


@pytest.mark.parametrize(
    "broken",
    [
        {"abc": 1},
        {"1": "x"},
        {"1": None},
        {"1": [1]},
        {"": ""},
    ],
)
def test_壊れたカートでも500にならない(client, customer, make_product, login, broken):
    make_product()
    login(customer)
    set_cart(client, broken)

    assert client.get("/cart/").status_code == 200
    response = client.post("/orders/", follow_redirects=True)
    assert response.status_code == 200
    assert count(Order) == 0


def test_非公開の商品が混ざっても外されて注文できる(
    client, customer, make_product, login
):
    ok = make_product(name="ほうれん草", price=300)
    gone = make_product(name="消える商品", price=500)
    login(customer)
    client.post(f"/cart/add/{ok.id}", data={"quantity": 1})
    client.post(f"/cart/add/{gone.id}", data={"quantity": 1})
    gone.is_published = False
    db.session.commit()

    # カートを開くと、非公開の商品はセッションからも外れ、メッセージが出る
    response = client.get("/cart/")
    assert "販売を終了した商品をカートから外しました" in response.text
    with client.session_transaction() as sess:
        assert sess["cart"] == {str(ok.id): 1}

    # そのまま注文できる
    response = client.post("/orders/")
    assert response.status_code == 302
    order = db.session.scalar(select(Order))
    assert order.total_amount == 300


def test_売り切れた商品の数量変更は分かりやすいメッセージが出る(
    client, customer, make_product, login
):
    product = make_product(stock=3)
    login(customer)
    client.post(f"/cart/add/{product.id}", data={"quantity": 1})
    product.stock = 0
    db.session.commit()

    client.post(f"/cart/update/{product.id}", data={"quantity": 1})

    response = client.get("/cart/")
    assert "売り切れました" in response.text


def test_カートのセッションが辞書でなくても500にならない(client):
    with client.session_transaction() as sess:
        sess["cart"] = [1, 2]

    assert client.get("/cart/").status_code == 200
