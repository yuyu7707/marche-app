from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileSize
from wtforms import (
    BooleanField,
    EmailField,
    IntegerField,
    PasswordField,
    RadioField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Regexp

from marche.models import Role

# email-validator パッケージを足さずに済むよう、形だけを正規表現で確かめる
EMAIL = Regexp(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="メールアドレスの形式で入力してください"
)


class RegisterForm(FlaskForm):
    name = StringField("名前", validators=[DataRequired(), Length(max=50)])
    email = EmailField(
        "メールアドレス", validators=[DataRequired(), EMAIL, Length(max=255)]
    )
    password = PasswordField("パスワード", validators=[DataRequired(), Length(min=8)])
    # 選択肢に admin を入れない。送られてきても RadioField の検証で弾かれる
    role = RadioField(
        "登録の種類",
        choices=[(Role.CUSTOMER, "購入者"), (Role.PRODUCER, "生産者")],
        default=Role.CUSTOMER,
    )


class LoginForm(FlaskForm):
    email = EmailField("メールアドレス", validators=[DataRequired(), EMAIL])
    password = PasswordField("パスワード", validators=[DataRequired()])


class ProductForm(FlaskForm):
    # 出品者（producer_id）はフォームに入れない。ビューでログイン中のユーザーを入れる
    name = StringField(
        "商品名",
        validators=[
            DataRequired(message="商品名を入力してください"),
            Length(max=50, message="50文字以内で入力してください"),
        ],
    )
    # 0 を入力できるように InputRequired を使う（DataRequired は 0 を空とみなす）
    price = IntegerField(
        "価格（円・税込み）",
        validators=[
            InputRequired(message="価格を入力してください"),
            NumberRange(min=50, max=100_000, message="50〜100,000円で入力してください"),
        ],
    )
    stock = IntegerField(
        "在庫数",
        validators=[
            InputRequired(message="在庫数を入力してください"),
            NumberRange(min=0, max=999, message="0〜999で入力してください"),
        ],
    )
    description = TextAreaField(
        "紹介文",
        validators=[Length(max=2000, message="2,000文字以内で入力してください")],
    )
    is_published = BooleanField("公開する")
    # 拡張子と大きさはここで確かめる。中身（本当に画像か）はビューで detect_media_type を使う
    image = FileField(
        "写真（JPEG・PNG・WebP、5MB まで）",
        validators=[
            FileAllowed(
                ["jpg", "jpeg", "png", "webp"],
                message="JPEG・PNG・WebP の画像を選んでください",
            ),
            FileSize(
                max_size=5 * 1024 * 1024, message="5MB 以下の画像を選んでください"
            ),
        ],
    )
