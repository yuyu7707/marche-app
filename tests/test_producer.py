import io
from pathlib import Path

from marche.extensions import db
from marche.models import Order, OrderItem, Product

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def product_data(**kwargs):
    """正しい商品フォームの値。引数で上書きできる"""
    data = {"name": "トマト", "price": 300, "stock": 5, "description": "甘いです"}
    data.update(kwargs)
    return data


def post_new(client, **kwargs):
    return client.post(
        "/producer/products/new",
        data=product_data(**kwargs),
        content_type="multipart/form-data",
    )


def uploaded_files(app):
    folder = Path(app.config["UPLOAD_FOLDER"])
    return list(folder.glob("*")) if folder.exists() else []


def test_生産者は商品を登録できる_非公開で作られる(client, producer, login):
    login(producer)
    response = post_new(client)
    assert response.status_code == 302
    product = db.session.scalars(db.select(Product)).one()
    assert product.name == "トマト"
    assert product.price == 300
    assert product.stock == 5
    assert product.producer_id == producer.id
    assert product.is_published is False


def test_価格が50円未満なら登録できない(client, producer, login):
    login(producer)
    response = post_new(client, price=49)
    assert response.status_code == 422
    assert "50〜100,000円で入力してください" in response.text
    assert db.session.scalars(db.select(Product)).all() == []


def test_他の生産者の商品は編集できない(
    client, producer, other_producer, make_product, login
):
    product = make_product(owner=other_producer, name="元の名前", price=500)
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(name="乗っ取り", price=100),
    )
    assert response.status_code == 403
    assert client.get(f"/producer/products/{product.id}/edit").status_code == 403
    db.session.refresh(product)
    assert product.name == "元の名前"
    assert product.price == 500


def test_他の生産者の商品は削除できない(
    client, producer, other_producer, make_product, login
):
    product = make_product(owner=other_producer)
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 403
    assert db.session.get(Product, product.id) is not None


def test_注文がある商品は削除できない(client, producer, customer, make_product, login):
    product = make_product()
    order = Order(user=customer, total_amount=200)
    db.session.add(order)
    order.items.append(
        OrderItem(
            product=product,
            product_name=product.name,
            unit_price=200,
            quantity=1,
            subtotal=200,
        )
    )
    db.session.commit()
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/delete", follow_redirects=True
    )
    assert response.status_code == 200
    assert "注文がある商品は削除できません" in response.text
    assert db.session.get(Product, product.id) is not None


def test_他の生産者の商品は公開を切り替えられない(
    client, producer, other_producer, make_product, login
):
    product = make_product(owner=other_producer, is_published=True)
    login(producer)
    response = client.post(f"/producer/products/{product.id}/toggle")
    assert response.status_code == 403
    db.session.refresh(product)
    assert product.is_published is True


def test_公開と非公開を切り替えられる(client, producer, make_product, login):
    product = make_product(is_published=True)
    login(producer)
    response = client.post(f"/producer/products/{product.id}/toggle")
    assert response.status_code == 302
    db.session.refresh(product)
    assert product.is_published is False

    client.post(f"/producer/products/{product.id}/toggle")
    db.session.refresh(product)
    assert product.is_published is True


def test_PNGの写真を登録できる(app, client, producer, login):
    login(producer)
    response = post_new(client, image=(io.BytesIO(PNG), "photo.png"))
    assert response.status_code == 302
    product = db.session.scalars(db.select(Product)).one()
    assert product.image_filename
    assert product.image_filename != "photo.png"
    assert product.image_filename.endswith(".png")
    saved = Path(app.config["UPLOAD_FOLDER"]) / product.image_filename
    assert saved.read_bytes() == PNG
    assert len(uploaded_files(app)) == 1


def test_写真を差し替えると古いファイルが消える(app, client, producer, login):
    login(producer)
    post_new(client, image=(io.BytesIO(PNG), "old.png"))
    product = db.session.scalars(db.select(Product)).one()
    old_filename = product.image_filename

    new_png = b"\x89PNG\r\n\x1a\n" + b"1" * 20
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(image=(io.BytesIO(new_png), "new.png")),
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    db.session.refresh(product)
    assert product.image_filename != old_filename
    folder = Path(app.config["UPLOAD_FOLDER"])
    assert not (folder / old_filename).exists()
    assert (folder / product.image_filename).read_bytes() == new_png
    assert len(uploaded_files(app)) == 1


def test_拡張子だけ画像のファイルは登録できない(app, client, producer, login):
    login(producer)
    response = post_new(client, image=(io.BytesIO(b"<?php echo 1; ?>"), "photo.png"))
    assert response.status_code == 422
    assert "画像として読み込めない" in response.text
    assert db.session.scalars(db.select(Product)).all() == []
    assert uploaded_files(app) == []


def test_gifは登録できない(app, client, producer, login):
    login(producer)
    response = post_new(client, image=(io.BytesIO(b"GIF89a" + b"0" * 20), "photo.gif"))
    assert response.status_code == 422
    assert "JPEG・PNG・WebP の画像を選んでください" in response.text
    assert db.session.scalars(db.select(Product)).all() == []
    assert uploaded_files(app) == []
