from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone

db = SQLAlchemy()

def utc_now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now_naive)
    alerts = db.relationship('PriceAlert', backref='user', lazy='dynamic')
    saved_products = db.relationship('SavedProduct', backref='user', lazy='dynamic')
    phone = db.Column(db.String(20), nullable=True)
    is_approved = db.Column(db.Boolean, default=True)
    is_blocked = db.Column(db.Boolean, default=False)
    is_frozen = db.Column(db.Boolean, default=False)
    activities = db.relationship('ActivityLog', backref='user', lazy='dynamic')


class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    image_url = db.Column(db.String(500))
    category = db.Column(db.String(100))
    brand = db.Column(db.String(100))
    rating = db.Column(db.Float, default=0)
    review_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utc_now_naive)
    updated_at = db.Column(db.DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    views = db.Column(db.Integer, default=0)
    is_refurbished = db.Column(db.Boolean, default=False)
    
    offers = db.relationship('Offer', backref='product', lazy=True, cascade='all, delete-orphan')
    price_history = db.relationship('PriceHistory', backref='product', lazy='dynamic', cascade='all, delete-orphan')

    __table_args__ = (
        db.Index('idx_product_views', views.desc()),
        db.Index('idx_product_name', name),
    )

class Offer(db.Model):
    __tablename__ = 'offers'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    seller = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='INR')
    availability = db.Column(db.String(50))
    url = db.Column(db.String(500))
    rating = db.Column(db.Float, default=0)
    review_count = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=utc_now_naive, onupdate=utc_now_naive)

class PriceHistory(db.Model):
    __tablename__ = 'price_history'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    seller = db.Column(db.String(100))
    price = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=lambda: datetime.now(timezone.utc).date())

class PriceAlert(db.Model):
    __tablename__ = 'price_alerts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    target_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now_naive)
    triggered = db.Column(db.Boolean, default=False)
    product = db.relationship('Product', backref='price_alerts_list')

class SavedProduct(db.Model):
    __tablename__ = 'saved_products'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    saved_at = db.Column(db.DateTime, default=utc_now_naive)
    product = db.relationship('Product', backref='saved_items')


class RestrictedPlatform(db.Model):
    __tablename__ = 'restricted_platforms'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    added_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=utc_now_naive)


class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=utc_now_naive)


class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(50), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name = db.Column(db.Text, nullable=False)
    product_image = db.Column(db.String(500), nullable=True)
    quantity = db.Column(db.Integer, default=1)
    price = db.Column(db.Float, nullable=False)
    platform_fee = db.Column(db.Float, default=0.0)
    delivery_fee = db.Column(db.Float, default=0.0)
    handling_fee = db.Column(db.Float, default=0.0)
    coupon_code = db.Column(db.String(50), nullable=True)
    discount_amount = db.Column(db.Float, default=0.0)
    address = db.Column(db.Text, nullable=False)
    phone = db.Column(db.String(15), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    payment_method = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(50), default='Placed')
    estimated_delivery_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now_naive)
    is_manual_status = db.Column(db.Boolean, default=False)
    user_rating = db.Column(db.Integer, nullable=True)
    user_review = db.Column(db.Text, nullable=True)

    user = db.relationship('User', backref='orders')

    @property
    def total_amount(self):
        return (self.price * self.quantity) + (self.platform_fee or 0.0) + (self.delivery_fee or 0.0) + (self.handling_fee or 0.0) - (self.discount_amount or 0.0)


class Address(db.Model):
    __tablename__ = 'addresses'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    street = db.Column(db.Text, nullable=False)
    city = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(100), nullable=False)
    pincode = db.Column(db.String(20), nullable=False)
    landmark = db.Column(db.String(200))
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now_naive)
    
    user = db.relationship('User', backref=db.backref('addresses', lazy=True, cascade='all, delete-orphan'))
