from marche.extensions import db
from marche.models import Order, OrderItem, Product, User


def test_公開中の商品だけが一覧に出る(client, make_product):
    make_product(name="公開のトマト", is_published=True)
    make_product(name="非公開のなす", is_published=False)
    response = client.get("/")
    assert response.status_code == 200
    assert "公開のトマト" in response.text
    assert "非公開のなす" not in response.text


def test_一覧に価格と生産者名と在庫が出る(client, producer, make_product):
    make_product(name="ほうれん草", price=1200, stock=7)
    text = client.get("/").text
    assert "1,200円" in text
    assert producer.name in text
    assert "在庫 7" in text


def test_在庫0は売り切れと出る(client, make_product):
    make_product(name="ピクルス", stock=0)
    assert "売り切れ" in client.get("/").text


def test_一覧には支払い待ちを引いた買える数が出る(client, customer, make_product):
    product = make_product(stock=5)
    order = Order(user=customer, total_amount=400)
    db.session.add(order)
    order.items.append(
        OrderItem(
            product=product,
            product_name=product.name,
            unit_price=200,
            quantity=2,
            subtotal=400,
        )
    )
    db.session.commit()
    assert "在庫 3" in client.get("/").text


def test_seedは確認なしでは実行されない(app, make_product):
    make_product(name="消えてはいけない商品")
    result = app.test_cli_runner().invoke(args=["seed"], input="n\n")
    assert result.exit_code != 0
    names = db.session.scalars(db.select(Product.name)).all()
    assert names == ["消えてはいけない商品"]


def test_公開中の詳細は誰でも見られる(client, make_product):
    product = make_product(name="味噌", description="香りがよいです")
    response = client.get(f"/products/{product.id}")
    assert response.status_code == 200
    assert "味噌" in response.text
    assert "香りがよいです" in response.text


def test_非公開の詳細は未ログインだと404(client, make_product):
    product = make_product(is_published=False)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の詳細は購入者だと404(client, customer, make_product, login):
    product = make_product(is_published=False)
    login(customer)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の詳細は他の生産者だと404(client, other_producer, make_product, login):
    product = make_product(is_published=False)
    login(other_producer)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の詳細は出品した生産者なら見られる(
    client, producer, make_product, login
):
    product = make_product(is_published=False)
    login(producer)
    assert client.get(f"/products/{product.id}").status_code == 200


def test_非公開の詳細は管理者なら見られる(client, admin, make_product, login):
    product = make_product(is_published=False)
    login(admin)
    assert client.get(f"/products/{product.id}").status_code == 200


def test_存在しない商品は404(client):
    assert client.get("/products/9999").status_code == 404


def test_紹介文のスクリプトは文字として出る(client, make_product):
    product = make_product(description="<script>alert(1)</script>")
    text = client.get(f"/products/{product.id}").text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


def test_seedで5人と8商品が入る(app):
    result = app.test_cli_runner().invoke(args=["seed", "--yes"])
    assert result.exit_code == 0
    assert len(db.session.scalars(db.select(User)).all()) == 5
    assert len(db.session.scalars(db.select(Product)).all()) == 8
