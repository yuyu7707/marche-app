from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from marche.decorators import role_required
from marche.extensions import db
from marche.forms import ProductForm
from marche.models import OrderItem, Product, Role
from marche.uploads import delete_image, detect_media_type, save_image

bp = Blueprint("producer", __name__, url_prefix="/producer")


def get_own_product_or_403(product_id: int) -> Product:
    """自分の商品だけを返す。無ければ 404、他の生産者の商品なら 403"""
    product = db.get_or_404(Product, product_id)
    if product.producer_id != current_user.id:
        abort(403)
    return product


def fill_product(product: Product, form: ProductForm) -> None:
    """フォームの値を商品に入れる。出品者（producer_id）はここでは触らない"""
    product.name = form.name.data.strip()
    product.price = form.price.data
    product.stock = form.stock.data
    product.description = (form.description.data or "").strip()
    product.is_published = form.is_published.data


def read_image(form: ProductForm) -> tuple[bytes, str] | None | bool:
    """写真が選ばれていれば (中身, 種類) を返す。選ばれていなければ None。
    画像でなければフォームにエラーを足して False を返す"""
    file = form.image.data
    if not file:
        return None
    data = file.read()
    media_type = detect_media_type(data)
    if media_type is None:
        form.image.errors.append("画像として読み込めないファイルです")
        return False
    return data, media_type


@bp.route("/products")
@role_required(Role.PRODUCER)
def index():
    products = db.session.scalars(
        db.select(Product)
        .filter_by(producer_id=current_user.id)
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    return render_template("producer/index.html", products=products)


@bp.route("/products/new", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def new():
    form = ProductForm()
    if form.validate_on_submit():
        image = read_image(form)
        if image is not False:
            product = Product(producer_id=current_user.id)
            fill_product(product, form)
            if image:
                product.image_filename = save_image(*image)
            db.session.add(product)
            db.session.commit()
            flash("商品を登録しました")
            return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template(
        "producer/form.html",
        form=form,
        product=None,
        title="商品の登録",
        submit_label="登録する",
    ), status


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def edit(product_id: int):
    product = get_own_product_or_403(product_id)
    form = ProductForm(obj=product)
    if form.validate_on_submit():
        image = read_image(form)
        if image is not False:
            old_filename = None
            fill_product(product, form)
            if image:
                old_filename = product.image_filename
                product.image_filename = save_image(*image)
            db.session.commit()
            # 保存が済んでから、差し替え前の古い写真を消す
            delete_image(old_filename)
            flash("商品を更新しました")
            return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template(
        "producer/form.html",
        form=form,
        product=product,
        title="商品の編集",
        submit_label="保存する",
    ), status


@bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@role_required(Role.PRODUCER)
def toggle(product_id: int):
    product = get_own_product_or_403(product_id)
    product.is_published = not product.is_published
    db.session.commit()
    flash("公開しました" if product.is_published else "非公開にしました")
    return redirect(url_for("producer.index"))


@bp.route("/products/<int:product_id>/delete", methods=["POST"])
@role_required(Role.PRODUCER)
def delete(product_id: int):
    product = get_own_product_or_403(product_id)
    # 注文の記録が指している商品は消さない（SQLite は外部キーを確かめないので、ここで確かめる）
    has_orders = db.session.scalar(
        db.select(OrderItem.id).where(OrderItem.product_id == product.id).limit(1)
    )
    if has_orders:
        flash("注文がある商品は削除できません。非公開にしてください")
        return redirect(url_for("producer.index"))
    filename = product.image_filename
    db.session.delete(product)
    db.session.commit()
    delete_image(filename)
    flash("商品を削除しました")
    return redirect(url_for("producer.index"))
