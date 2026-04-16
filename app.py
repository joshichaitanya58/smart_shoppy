import os
import re
import random
from dotenv import load_dotenv
load_dotenv()
import html
import logging
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from datetime import datetime, timedelta, timezone
import time
from sqlalchemy import text, inspect, desc, func
from sqlalchemy.exc import OperationalError, IntegrityError

from config import Config
from models import db, User, Product, Offer, PriceHistory, PriceAlert, SavedProduct, RestrictedPlatform, ActivityLog, Order, Address
from forms import LoginForm, RegisterForm, AlertForm, SearchForm, FilterForm, OTPForm, AdminSettingsForm, AddressForm
from utils.api_clients import search_products, extract_product_name_from_url
from utils.helpers import is_url
from utils.tasks import scheduler, update_all_products

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY

db.init_app(app)

# ---------- Currency Symbols (only used for display) ----------
CURRENCY_SYMBOLS = {
    'INR': '₹',
    'USD': '$',
    'EUR': '€',
    'GBP': '£',
    'JPY': '¥',
    'CAD': 'C$',
    'AUD': 'A$',
    # add more if needed
}

# ---------- Security: Custom Rate Limiter ----------
_rate_limits = {}
def check_rate_limit(ip_address, limit=15, window=60):
    """Limits requests to protect APIs from abuse."""
    now = time.time()
    if ip_address not in _rate_limits:
        _rate_limits[ip_address] = []
    # Remove timestamps older than the window
    _rate_limits[ip_address] = [t for t in _rate_limits[ip_address] if now - t < window]
    if len(_rate_limits[ip_address]) >= limit:
        return False
    _rate_limits[ip_address].append(now)
    return True

TRUSTED_PLATFORMS = ['amazon', 'flipkart', 'myntra', 'croma', 'reliance', 'tata', 'ajio', 'nykaa', 'jiomart', 'apple', 'samsung', 'oneplus']

@app.context_processor
def inject_globals():
    try:
        rp_db = RestrictedPlatform.query.all()
        restricted_platforms = [rp.name.lower() for rp in rp_db]
    except Exception:
        restricted_platforms = []
        
    def is_restricted(seller):
        return any(r in (seller or '').lower() for r in restricted_platforms)

    return {
        'CURRENCY_SYMBOLS': CURRENCY_SYMBOLS,
        'TRUSTED_PLATFORMS': TRUSTED_PLATFORMS,
        'is_restricted': is_restricted,
        'ADMIN_UPI_ID': os.environ.get('UPI_ID', '').replace('"', '').replace("'", '').strip(),
        'PLATFORM_FEE': float(os.environ.get('PLATFORM_FEE', 50)),
        'DELIVERY_FEE': float(os.environ.get('DELIVERY_FEE', 100)),
        'HANDLING_FEE': float(os.environ.get('HANDLING_FEE', 50)),
        'HANDLING_FEE_THRESHOLD': float(os.environ.get('HANDLING_FEE_THRESHOLD', 10000))
    }

mail = Mail(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

with app.app_context():
    try:
        db.create_all() # Ensure new tables are created
        inspector = inspect(db.engine)
        if inspector.has_table("products"):
            columns = [col['name'] for col in inspector.get_columns("products")]
            if "slug" not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE products ADD COLUMN slug VARCHAR(200)"))
                    conn.commit()
            if "category" not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE products ADD COLUMN category VARCHAR(100)"))
                    conn.commit()
            if "is_refurbished" not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE products ADD COLUMN is_refurbished BOOLEAN DEFAULT FALSE"))
                    conn.commit()
        for t_name in ["user", "users"]:
            if inspector.has_table(t_name):
                cols = [col['name'] for col in inspector.get_columns(t_name)]
                if "phone" not in cols:
                    with db.engine.connect() as conn:
                        conn.execute(text(f"ALTER TABLE {t_name} ADD COLUMN phone VARCHAR(20)"))
                        conn.commit()
                if "is_approved" not in cols:
                    with db.engine.connect() as conn:
                        conn.execute(text(f"ALTER TABLE {t_name} ADD COLUMN is_approved BOOLEAN DEFAULT 1"))
                        conn.commit()
                if "is_blocked" not in cols:
                    with db.engine.connect() as conn:
                        conn.execute(text(f"ALTER TABLE {t_name} ADD COLUMN is_blocked BOOLEAN DEFAULT 0"))
                        conn.commit()
                if "is_frozen" not in cols:
                    with db.engine.connect() as conn:
                        conn.execute(text(f"ALTER TABLE {t_name} ADD COLUMN is_frozen BOOLEAN DEFAULT 0"))
                        conn.commit()
            if inspector.has_table("offers"):
                cols = [col['name'] for col in inspector.get_columns("offers")]
                with db.engine.connect() as conn:
                    if "rating" not in cols:
                        conn.execute(text("ALTER TABLE offers ADD COLUMN rating FLOAT DEFAULT 0"))
                    if "review_count" not in cols:
                        conn.execute(text("ALTER TABLE offers ADD COLUMN review_count INTEGER DEFAULT 0"))
                    conn.commit()
            if inspector.has_table("orders"):
                cols = [col['name'] for col in inspector.get_columns("orders")]
                with db.engine.connect() as conn:
                    if "platform_fee" not in cols:
                        conn.execute(text("ALTER TABLE orders ADD COLUMN platform_fee FLOAT DEFAULT 0"))
                    if "delivery_fee" not in cols:
                        conn.execute(text("ALTER TABLE orders ADD COLUMN delivery_fee FLOAT DEFAULT 0"))
                    if "handling_fee" not in cols:
                        conn.execute(text("ALTER TABLE orders ADD COLUMN handling_fee FLOAT DEFAULT 0"))
                    if "coupon_code" not in cols:
                        conn.execute(text("ALTER TABLE orders ADD COLUMN coupon_code VARCHAR(50)"))
                    if "discount_amount" not in cols:
                        conn.execute(text("ALTER TABLE orders ADD COLUMN discount_amount FLOAT DEFAULT 0"))
                    if "is_manual_status" not in cols:
                        conn.execute(text("ALTER TABLE orders ADD COLUMN is_manual_status BOOLEAN DEFAULT 0"))
                    if "user_rating" not in cols:
                        conn.execute(text("ALTER TABLE orders ADD COLUMN user_rating INTEGER"))
                    if "user_review" not in cols:
                        conn.execute(text("ALTER TABLE orders ADD COLUMN user_review TEXT"))
                    conn.commit()
    except Exception as e:
        print(f"Schema check warning: {e}")

# Start scheduler (only once, and strictly not on Vercel Serverless environment)
if not os.environ.get('VERCEL') and (not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'):
    import warnings
    warnings.filterwarnings('ignore', category=UserWarning, module='apscheduler')
    scheduler.add_job(func=lambda: update_all_products(app), trigger='interval', hours=6)
    scheduler.add_job(func=lambda: __import__('utils.tasks').tasks.cleanup_old_data(app), trigger='interval', days=1)
    scheduler.start()

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def log_activity(user_id, action, details=None):
    try:
        log = ActivityLog(user_id=user_id, action=action, details=details)
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Failed to log activity: {e}")

def generate_order_id():
    today = datetime.now(timezone.utc).strftime('%Y%m%d')
    candidate = f"ORD-{datetime.now(timezone.utc).year}-{today}-{random.randint(1000, 9999)}"
    while Order.query.filter_by(order_id=candidate).first():
        candidate = f"ORD-{datetime.now(timezone.utc).year}-{today}-{random.randint(1000, 9999)}"
    return candidate


def validate_coupon_logic(code, user_id, subtotal, payment_method):
    code = str(code).strip().upper()
    if not code:
        return 0.0, "Please enter a coupon code."
    
    used = Order.query.filter(Order.user_id == user_id, Order.coupon_code == code, Order.status != 'Cancelled').first()
    if used:
        return 0.0, f"You have already used the coupon '{code}'."
        
    if code == 'SAVE10':
        return min(subtotal * 0.10, 500.0), None
    elif code == 'FESTIVE250':
        if 'UPI' not in payment_method:
            return 0.0, "FESTIVE250 is only valid for UPI payments."
        return 250.0, None
    else:
        return 0.0, "Invalid or expired coupon code."

def send_order_confirmation_email(user, order):
    try:
        estimated = order.estimated_delivery_date.strftime('%d %b %Y')
        image_html = f"<div style='text-align:center; margin-bottom:15px;'><img src='{order.product_image}' alt='Product Image' style='max-width:150px; max-height:150px; object-fit:contain; border-radius:8px; border:1px solid #e2e8f0; padding:5px; background:#fff;'></div>" if order.product_image else ""
        html_body = f"""
        <div style='font-family: Inter, Arial, sans-serif; color:#1f2937; background:#f8fafc; padding:20px;'>
        <div style='max-width:620px; margin:auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; overflow:hidden;'>
          <div style='background:#0d6efd; color:#fff; padding:20px; text-align:center;'>
            <h1 style='margin:0;font-size:22px;'>SmartShoppy Order Confirmation</h1>
            <p style='margin:5px 0 0; font-size:14px;'>Order ID: {order.order_id}</p>
          </div>
          <div style='padding:20px;'>
            <p>Hi {user.username},</p>
            <p>Thanks for placing your order! Your purchase has been <strong>Placed</strong> and is being prepared for dispatch.</p>
            <h3 style='margin-top:20px;font-size:16px;'>🧾 Order Summary</h3>
            {image_html}
            <table width='100%' cellpadding='8' cellspacing='0' style='border-collapse:collapse;'>
              <tr style='background:#f1f5f9;'>
                <td><strong>Product</strong></td><td>{order.product_name}</td>
              </tr>
              <tr><td><strong>Price per unit</strong></td><td>₹{order.price:.2f}</td></tr>
              <tr><td><strong>Quantity</strong></td><td>{order.quantity}</td></tr>
              <tr><td><strong>Subtotal</strong></td><td>₹{order.price * order.quantity:.2f}</td></tr>
              <tr><td><strong>Delivery Charge</strong></td><td>₹{order.delivery_fee:.2f}</td></tr>
              <tr><td><strong>Platform Fee</strong></td><td>₹{order.platform_fee:.2f}</td></tr>
              {f"<tr><td><strong>Handling Fee</strong></td><td>₹{order.handling_fee:.2f}</td></tr>" if order.handling_fee > 0 else ""}
              {f"<tr><td><strong>Discount ({order.coupon_code})</strong></td><td style='color:#198754;'>-₹{order.discount_amount:.2f}</td></tr>" if order.discount_amount and order.discount_amount > 0 else ""}
              <tr><td><strong>Grand Total</strong></td><td><strong>₹{order.total_amount:.2f}</strong></td></tr>
              <tr><td><strong>Status</strong></td><td>{order.status}</td></tr>
              <tr><td><strong>Estimated Delivery</strong></td><td>{estimated}</td></tr>
            </table>

            <h3 style='margin-top:20px;font-size:16px;'>📦 Delivery Address</h3>
            <p style='margin:0; line-height:1.5;'>
              {order.address.replace('\n','<br>')}<br>
              {order.phone} • {order.email}
            </p>

            <a href='{url_for('order_detail', order_id=order.order_id, _external=True)}' style='display:inline-block;margin-top:20px;background:#0d6efd;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;'>Track Order</a>

            <p style='font-size:12px;color:#6b7280;margin-top:10px;'>Need help? Contact us at support@smartshoppy.example or reply to this email.</p>
          </div>
          <div style='background:#f8fafc; padding:14px; text-align:center; color:#6b7280;'>SmartShoppy © {datetime.now(timezone.utc).year}</div>
        </div>
        </div>
        """
        msg = Message(
            subject=f"Your SmartShoppy Order #{order.order_id} is Confirmed",
            sender=app.config.get('MAIL_USERNAME'),
            recipients=[order.email]
        )
        msg.html = html_body
        mail.send(msg)
    except Exception as e:
        print(f"Order confirmation email failed: {e}")

def send_order_delivered_email(user, order):
    try:
        image_html = f"<div style='text-align:center; margin-bottom:15px;'><img src='{order.product_image}' alt='Product Image' style='max-width:150px; max-height:150px; object-fit:contain; border-radius:8px; border:1px solid #e2e8f0; padding:5px; background:#fff;'></div>" if order.product_image else ""
        html_body = f"""
        <div style='font-family: Inter, Arial, sans-serif; color:#1f2937; background:#f8fafc; padding:20px;'>
        <div style='max-width:620px; margin:auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; overflow:hidden;'>
          <div style='background:#198754; color:#fff; padding:20px; text-align:center;'>
            <h1 style='margin:0;font-size:22px;'>Order Delivered! 🎉</h1>
            <p style='margin:5px 0 0; font-size:14px;'>Order ID: {order.order_id}</p>
          </div>
          <div style='padding:20px;'>
            <p>Hi {user.username},</p>
            <p>Great news! Your order for <strong>{order.product_name}</strong> has been successfully delivered.</p>
            
            <h3 style='margin-top:20px;font-size:16px;'>📦 Order Details</h3>
            {image_html}
            <table width='100%' cellpadding='8' cellspacing='0' style='border-collapse:collapse;'>
              <tr style='background:#f1f5f9;'>
                <td><strong>Product</strong></td><td>{order.product_name}</td>
              </tr>
              <tr><td><strong>Quantity</strong></td><td>{order.quantity}</td></tr>
              <tr><td><strong>Total Paid</strong></td><td>₹{order.total_amount:.2f}</td></tr>
              <tr><td><strong>Payment Method</strong></td><td>{order.payment_method}</td></tr>
            </table>

            <h3 style='margin-top:20px;font-size:16px;'>📍 Delivered To</h3>
            <p style='margin:0; line-height:1.5; color:#4b5563;'>
              {order.address.replace('\n','<br>')}<br>
              {order.phone}
            </p>

            <a href='{url_for('order_detail', order_id=order.order_id, _external=True)}' style='display:inline-block;margin-top:20px;background:#198754;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;'>View Order Details</a>

          </div>
          <div style='background:#f8fafc; padding:14px; text-align:center; color:#6b7280;'>SmartShoppy © {datetime.now(timezone.utc).year}</div>
        </div>
        </div>
        """
        msg = Message(
            subject=f"Delivered: Your SmartShoppy Order #{order.order_id}",
            sender=app.config.get('MAIL_USERNAME'),
            recipients=[order.email]
        )
        msg.html = html_body
        mail.send(msg)
    except Exception as e:
        print(f"Order delivered email failed: {e}")

def send_order_cancelled_email(user, order):
    try:
        image_html = f"<div style='text-align:center; margin-bottom:15px;'><img src='{order.product_image}' alt='Product Image' style='max-width:150px; max-height:150px; object-fit:contain; border-radius:8px; border:1px solid #e2e8f0; padding:5px; background:#fff;'></div>" if order.product_image else ""
        html_body = f"""
        <div style='font-family: Inter, Arial, sans-serif; color:#1f2937; background:#f8fafc; padding:20px;'>
        <div style='max-width:620px; margin:auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; overflow:hidden;'>
          <div style='background:#dc3545; color:#fff; padding:20px; text-align:center;'>
            <h1 style='margin:0;font-size:22px;'>Order Cancelled</h1>
            <p style='margin:5px 0 0; font-size:14px;'>Order ID: {order.order_id}</p>
          </div>
          <div style='padding:20px;'>
            <p>Hi {user.username},</p>
            <p>Your order for <strong>{order.product_name}</strong> has been successfully cancelled as per your request.</p>
            {image_html}
            <p>If you had already paid for this order, the refund process will be initiated shortly according to the payment method policies.</p>
            <a href='{url_for('orders', _external=True)}' style='display:inline-block;margin-top:20px;background:#dc3545;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;'>View Orders</a>
          </div>
          <div style='background:#f8fafc; padding:14px; text-align:center; color:#6b7280;'>SmartShoppy © {datetime.now(timezone.utc).year}</div>
        </div>
        </div>
        """
        msg = Message(
            subject=f"Cancelled: Your SmartShoppy Order #{order.order_id}",
            sender=app.config.get('MAIL_USERNAME'),
            recipients=[order.email]
        )
        msg.html = html_body
        mail.send(msg)
    except Exception as e:
        print(f"Order cancelled email failed: {e}")

def simulate_order_status(order):
    if not order or not order.created_at:
        return
    if getattr(order, 'is_manual_status', False):
        return
    age = (datetime.now(timezone.utc).replace(tzinfo=None) - order.created_at).days
    
    old_status = order.status
    
    if order.status == 'Placed' and age >= 1:
        order.status = 'Processing'
    elif order.status == 'Processing' and age >= 2:
        order.status = 'Shipped'
    elif order.status == 'Shipped' and age >= 4:
        order.status = 'Delivered'
        
    if old_status != 'Delivered' and order.status == 'Delivered':
        user = db.session.get(User, order.user_id)
        if user:
            send_order_delivered_email(user, order)


def make_unique_slug(base_name):
    """Create a URL-safe unique slug for a product name."""
    slug = re.sub(r'[^\w\s-]', '', base_name.lower())
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')[:180]
    original = slug
    count = 1
    while Product.query.filter_by(slug=slug).first():
        slug = f"{original}-{count}"
        count += 1
    return slug

def save_search_results(products_data):
    """Save searched products and their offers to DB. Returns list of saved product IDs."""
    saved_ids = []
    for prod_data in products_data:
        try:
            existing = Product.query.filter(
                Product.name.ilike(prod_data['name'])
            ).first()
        
            if existing:
                product = existing
                product.views = (product.views or 0) + 1
                if not product.image_url and prod_data.get('image'):
                    product.image_url = prod_data['image']
                if not product.category and prod_data.get('category'):
                    product.category = str(prod_data['category'])[:100]
                if prod_data.get('rating', 0) > 0:
                    product.rating = float(prod_data['rating'])
                    product.review_count = int(prod_data.get('review_count', 0))
                if prod_data.get('is_refurbished'):
                    product.is_refurbished = True
            else:
                slug = make_unique_slug(prod_data['name'])
                product = Product(
                    name=prod_data['name'],
                    slug=slug,
                    image_url=prod_data.get('image') or '',
                    brand=prod_data.get('brand', ''),
                    category=str(prod_data.get('category', ''))[:100],
                    rating=float(prod_data.get('rating', 0.0)),
                    review_count=int(prod_data.get('review_count', 0)),
                    is_refurbished=prod_data.get('is_refurbished', False)
                )
                db.session.add(product)
                db.session.flush()
        
            for offer_data in prod_data.get('offers', []):
                price_val = offer_data.get('price')
                try:
                    price_float = float(str(price_val).replace('₹', '').replace(',', ''))
                except (TypeError, ValueError):
                    continue
                if price_float <= 0:
                    continue
        
                off_rating = offer_data.get('rating')
                try: off_rating = float(off_rating) if off_rating else 0.0
                except: off_rating = 0.0
                
                off_reviews = offer_data.get('review_count')
                try: off_reviews = int(float(off_reviews)) if off_reviews else 0
                except: off_reviews = 0

                offer = Offer.query.filter_by(
                    product_id=product.id,
                    seller=offer_data['seller']
                ).first()
                if offer:
                    offer.price = price_float
                    offer.availability = offer_data.get('availability', 'In Stock')
                    offer.url = offer_data.get('url', '')
                    offer.rating = off_rating
                    offer.review_count = off_reviews
                else:
                    offer = Offer(
                        product_id=product.id,
                        seller=offer_data['seller'],
                        price=price_float,
                        currency=offer_data.get('currency', 'INR'),
                        availability=offer_data.get('availability', 'In Stock'),
                        url=offer_data.get('url', ''),
                        rating=off_rating,
                        review_count=off_reviews
                    )
                    db.session.add(offer)
        
                last_history = PriceHistory.query.filter_by(
                    product_id=product.id,
                    seller=offer_data['seller']
                ).order_by(PriceHistory.date.desc()).first()
        
                if not last_history or last_history.date != datetime.now(timezone.utc).date():
                    history = PriceHistory(
                        product_id=product.id,
                        seller=offer_data['seller'],
                        price=price_float,
                        date=datetime.now(timezone.utc).date()
                    )
                    db.session.add(history)
        
            db.session.commit()
            saved_ids.append(product.id)
        except (OperationalError, IntegrityError) as e:
            db.session.rollback()
            print(f"DB error saving product '{prod_data.get('name')}': {e}")
        except Exception as e:
            db.session.rollback()
            print(f"Unexpected error saving product: {e}")

    return saved_ids

# ---------- Routes ----------

@app.route('/', methods=['GET', 'POST'])
def index():
    if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
        return redirect(url_for('admin_dashboard'))

    form = SearchForm()
    trending = Product.query.filter(
        Product.views > 0,
        Product.image_url != None,
        Product.image_url != ''
    ).order_by(Product.views.desc()).limit(6).all()
    
    recent_products = []
    if current_user.is_authenticated:
        recent_ids = session.get('recent_searches', [])
        if recent_ids:
            products_obj = Product.query.filter(Product.id.in_(recent_ids)).all()
            p_dict = {p.id: p for p in products_obj}
            # Maintain correct chronological order and limit to 6
            recent_products = [p_dict[pid] for pid in recent_ids if pid in p_dict][:6]

    if request.method == 'POST':
        # Support multi-tab form: text query or URL
        query_text = request.form.get('query', '').strip()

        extracted_name = None

        if query_text:
            if is_url(query_text):
                extracted_name = extract_product_name_from_url(query_text)
            else:
                extracted_name = query_text

        if not extracted_name:
            flash('Could not identify product. Please try again.')
            return redirect(url_for('index'))

        # Always search with India (INR)
        products_data = search_products(extracted_name, max_products=8, country='in')
        if not products_data:
            flash('No products found. Try a different search term.')
            return redirect(url_for('index'))

        saved_ids = save_search_results(products_data)
        session['last_search_ids'] = saved_ids
        session['last_query'] = extracted_name

        if current_user.is_authenticated and saved_ids:
            recent = session.get('recent_searches', [])
            for pid in reversed(saved_ids[:5]):  # add top 5 searches
                if pid in recent:
                    recent.remove(pid)
                recent.insert(0, pid)
            session['recent_searches'] = recent[:15]

        return redirect(url_for('results', q=extracted_name))

    return render_template('index.html', form=form, trending=trending, recent_products=recent_products)

@app.route('/results')
def results():
    query = request.args.get('q', '')
    if not query:
        return redirect(url_for('index'))

    # Always India
    country = 'in'

    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)
    brands = request.args.getlist('brand')
    sellers = request.args.getlist('seller')
    min_rating = request.args.get('min_rating', type=float, default=0)
    in_stock_only = request.args.get('in_stock_only') == 'on'
    sort_by = request.args.get('sort_by', 'relevance')
    
    restricted_platforms_db = RestrictedPlatform.query.all()
    restricted_names = [rp.name.lower() for rp in restricted_platforms_db]

    product_ids = session.get('last_search_ids', [])
    last_query = session.get('last_query', '')
    
    products = []
    if product_ids and last_query.lower() == query.lower():
        # Order preserve karna taaki exact matches pehle aayein
        prods = Product.query.filter(Product.id.in_(product_ids)).all()
        p_dict = {p.id: p for p in prods}
        products = [p_dict[pid] for pid in product_ids if pid in p_dict]
    else:
        words = query.strip().split()
        if words:
            filters = [Product.name.ilike(f'%{w}%') for w in words]
            products = Product.query.filter(*filters).all()

    if not products:
        products_data = search_products(query, max_products=10, country='in')
        if products_data:
            saved_ids = save_search_results(products_data)
            session['last_search_ids'] = saved_ids
            session['last_query'] = query
            prods = Product.query.filter(Product.id.in_(saved_ids)).all()
            p_dict = {p.id: p for p in prods}
            products = [p_dict[pid] for pid in saved_ids if pid in p_dict]
        else:
            flash('No products found. Please try a different search.')
            return redirect(url_for('index'))

    if current_user.is_authenticated and products:
        recent = session.get('recent_searches', [])
        for p in reversed(products[:5]):
            if p.id in recent:
                recent.remove(p.id)
            recent.insert(0, p.id)
        session['recent_searches'] = recent[:15]

    display_items = []
    for product in products:
        if brands and product.brand not in brands:
            continue
        if min_rating and (product.rating or 0) < min_rating:
            continue

        product_offers = [o for o in product.offers if o.price]
        lowest_product_price = min((o.price for o in product_offers), default=0)

        for offer in product.offers:
            if not offer.price:
                continue
            if any(r_name in offer.seller.lower() for r_name in restricted_names):
                continue
            if min_price and offer.price < min_price:
                continue
            if max_price and offer.price > max_price:
                continue
            if sellers and not any(s.lower() in offer.seller.lower() for s in sellers):
                continue
            if in_stock_only and 'In Stock' not in (offer.availability or ''):
                continue

            display_items.append({
                'product': product,
                'offer': offer,
                'is_best': (offer.price == lowest_product_price and lowest_product_price > 0)
            })

    def get_seller_priority(seller):
        s = (seller or '').lower()
        if any(t in s for t in TRUSTED_PLATFORMS):
            return 1
        return 0

    if sort_by == 'price_low':
        display_items.sort(key=lambda x: (0 if x['is_best'] else 1, x['offer'].price, -get_seller_priority(x['offer'].seller)))
    elif sort_by == 'price_high':
        display_items.sort(key=lambda x: (1 if x['is_best'] else 0, x['offer'].price, get_seller_priority(x['offer'].seller)), reverse=True)
    elif sort_by == 'rating':
        display_items.sort(key=lambda x: (1 if x['is_best'] else 0, get_seller_priority(x['offer'].seller), x['offer'].rating or 0, x['product'].rating or 0), reverse=True)
    elif sort_by == 'popularity':
        display_items.sort(key=lambda x: (1 if x['is_best'] else 0, get_seller_priority(x['offer'].seller), x['product'].views or 0), reverse=True)
    else:
        # Relevance Sorting (Taaki exact product pehle dikhe, Accessories niche jayein)
        query_lower = query.lower()
        query_words = set(query_lower.split())
        accessories = ['case', 'cover', 'protector', 'cable', 'charger', 'skin', 'glass', 'strap', 'band']
        
        def relevance_score(item):
            name = item['product'].name.lower()
            score = 0
            if query_lower == name:
                score += 10000
            elif name.startswith(query_lower):
                score += 8000
            elif query_lower in name:
                score += 6000
                
            for w in query_words:
                if w in name:
                    score += 1000
                    
                    
            if not any(acc in query_lower for acc in accessories):
                for acc in accessories:
                    if f" {acc} " in f" {name} " or name.endswith(f" {acc}"):
                        score -= 20000
            return score
            
        display_items.sort(key=lambda item: (1 if item['is_best'] else 0, relevance_score(item), get_seller_priority(item['offer'].seller), -item['offer'].price), reverse=True)

    all_brands = db.session.query(Product.brand).distinct().all()
    all_brands = sorted([b[0] for b in all_brands if b[0]])
    all_sellers_db = db.session.query(Offer.seller).distinct().all()
    all_sellers = sorted([s[0] for s in all_sellers_db if s[0] and any(t in s[0].lower() for t in TRUSTED_PLATFORMS)])

    return render_template('results.html',
                           query=query,
                           items=display_items,
                           brands=all_brands,
                           sellers=all_sellers,
                           selected_brands=brands,
                           selected_sellers=sellers)

@app.route('/product/<slug>')
def product_detail(slug):
    product = Product.query.filter_by(slug=slug).first_or_404()
    product.views = (product.views or 0) + 1
    db.session.commit()

    if current_user.is_authenticated:
        recent = session.get('recent_searches', [])
        if product.id in recent:
            recent.remove(product.id)
        recent.insert(0, product.id)
        session['recent_searches'] = recent[:15]

    days = request.args.get('days', 90, type=int)
    since = datetime.now(timezone.utc).date() - timedelta(days=days)
    history = PriceHistory.query.filter(
        PriceHistory.product_id == product.id,
        PriceHistory.date >= since
    ).order_by(PriceHistory.date).all()

    dates = sorted(set(h.date for h in history))
    sellers_in_history = sorted(set(h.seller for h in history if h.seller))
    datasets = []
    for seller in sellers_in_history:
        data = []
        for d in dates:
            rec = PriceHistory.query.filter_by(
                product_id=product.id, seller=seller, date=d
            ).first()
            data.append(rec.price if rec else None)
        datasets.append({'label': seller, 'data': data})

    def get_seller_priority(seller):
        s = (seller or '').lower()
        if any(t in s for t in TRUSTED_PLATFORMS):
            return 1
        return 0
        
    restricted_platforms_db = RestrictedPlatform.query.all()
    restricted_names = [rp.name.lower() for rp in restricted_platforms_db]
    
    valid_offers = []
    for o in product.offers:
        if not any(r_name in o.seller.lower() for r_name in restricted_names):
            valid_offers.append(o)
            
    sorted_offers = sorted(valid_offers, key=lambda o: (-get_seller_priority(o.seller), o.price))

    return render_template('product.html',
                           product=product,
                           sorted_offers=sorted_offers,
                           history_dates=[d.strftime('%d %b') for d in dates],
                           datasets=datasets,
                           days=days)

@app.route('/trending')
def trending_products():
    products = Product.query.filter(
        Product.views > 0,
        Product.image_url != None,
        Product.image_url != ''
    ).order_by(Product.views.desc()).limit(24).all()
    return render_template('trending.html', products=products)

@app.route('/api/product/<int:product_id>/history')
def product_history_api(product_id):
    days = request.args.get('days', 90, type=int)
    since = datetime.now(timezone.utc).date() - timedelta(days=days)
    history = PriceHistory.query.filter(
        PriceHistory.product_id == product_id,
        PriceHistory.date >= since
    ).order_by(PriceHistory.date).all()

    dates = sorted(set(h.date for h in history))
    sellers_in_history = sorted(set(h.seller for h in history if h.seller))
    datasets = []
    for seller in sellers_in_history:
        data = []
        for d in dates:
            rec = PriceHistory.query.filter_by(
                product_id=product_id, seller=seller, date=d
            ).first()
            data.append(rec.price if rec else None)
        datasets.append({'label': seller, 'data': data})

    return jsonify({
        'dates': [d.strftime('%d %b') for d in dates],
        'datasets': datasets
    })

@app.route('/api/fetch-url', methods=['POST'])
def api_fetch_url():
    """Async route to extract product name from URL via Frontend JS."""
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if not check_rate_limit(client_ip, limit=10, window=60):
        return jsonify({'success': False, 'error': 'Too many requests. Please slow down.'}), 429

    try:
        data = request.get_json() or {}
        url = data.get('url')

        if not url or len(url.strip()) == 0:
            return jsonify({'success': False, 'error': 'URL is required'}), 400
            
        if not url.startswith('http'):
            return jsonify({'success': False, 'error': 'Invalid URL format. Please enter a valid link.'}), 400

        url = html.escape(url.strip())

        extracted_name = extract_product_name_from_url(url)
        if not extracted_name:
            return jsonify({'success': False, 'error': 'Could not extract product details. Try normal search.'}), 400
            
        return jsonify({'success': True, 'product_name': extracted_name})
    except Exception as e:
        logging.error(f"API Fetch URL Error: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred.'}), 500

# ---------- API Routes for SmartShoppy UI ----------

@app.route('/api/suggestions')
def api_suggestions():
    """Autocomplete suggestions for the search bar."""
    q = request.args.get('q', '').strip().lower()
    if len(q) < 2:
        return jsonify([])

    products = Product.query.filter(
        Product.name.ilike(f'%{q}%')
    ).order_by(Product.views.desc()).limit(8).all()

    suggestions = list(dict.fromkeys([p.name for p in products]))[:8]
    return jsonify(suggestions)

@app.route('/api/wishlist/count')
@login_required
def api_wishlist_count():
    """Return number of saved products for navbar badge."""
    count = SavedProduct.query.filter_by(user_id=current_user.id).count()
    return jsonify({'count': count})

@app.route('/api/auth-status')
def api_auth_status():
    """Return current auth status."""
    return jsonify({
        'logged_in': current_user.is_authenticated,
        'username': current_user.username if current_user.is_authenticated else None
    })

# ---------- Compare ----------

@app.route('/compare', methods=['GET', 'POST'])
@login_required
def compare():
    if request.method == 'POST':
        product_ids = request.form.getlist('product_ids')
        if len(product_ids) < 2:
            flash('Please select at least 2 products to compare.')
            return redirect(url_for('compare'))
        products = Product.query.filter(Product.id.in_(product_ids)).all()
        return render_template('compare.html', products=products)
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('compare_select.html', products=products)

@app.route('/dashboard')
@login_required
def dashboard():
    saved = SavedProduct.query.filter_by(user_id=current_user.id)\
        .order_by(SavedProduct.saved_at.desc()).all()
    alerts = PriceAlert.query.filter_by(user_id=current_user.id)\
        .order_by(PriceAlert.created_at.desc()).all()
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).limit(5).all()

    changed = False
    for order in orders:
        prev_status = order.status
        simulate_order_status(order)
        if prev_status != order.status:
            changed = True
    if changed:
        db.session.commit()

    total_orders = Order.query.filter_by(user_id=current_user.id).count()
    return render_template('dashboard.html', saved=saved, alerts=alerts, orders=orders, total_orders=total_orders)


@app.route('/checkout/<int:offer_id>', methods=['GET', 'POST'])
def checkout(offer_id):
    offer = Offer.query.get_or_404(offer_id)
    product = offer.product

    if not current_user.is_authenticated:
        return redirect(url_for('login', next=url_for('checkout', offer_id=offer_id)))

    addresses = Address.query.filter_by(user_id=current_user.id).order_by(Address.is_default.desc(), Address.created_at.desc()).all()

    # Get and sort valid offers for selection
    restricted_platforms_db = RestrictedPlatform.query.all()
    restricted_names = [rp.name.lower() for rp in restricted_platforms_db]
    valid_offers = [o for o in product.offers if o.price and not any(r_name in o.seller.lower() for r_name in restricted_names)]
    def get_seller_priority(seller):
        s = (seller or '').lower()
        if any(t in s for t in TRUSTED_PLATFORMS): return 1
        return 0
    sorted_offers = sorted(valid_offers, key=lambda o: (-get_seller_priority(o.seller), o.price))

    step = int(request.args.get('step', 1))
    data = session.get('checkout_data', {
        'product_id': product.id,
        'offer_id': offer.id,
        'quantity': 1,
        'name': current_user.username,
        'email': current_user.email,
        'phone': getattr(current_user, 'phone', ''),
        'street': '', 'city': '', 'state': '', 'pincode': '', 'landmark': '',
        'payment_method': 'Cash on Delivery'
    })

    # Purana session mix na ho iske liye check
    if request.method == 'GET' and step == 1 and data.get('offer_id') != offer.id:
        data['product_id'] = product.id
        data['offer_id'] = offer.id
        data['quantity'] = 1
        session['checkout_data'] = data

    if request.method == 'POST':
        current_step = int(request.form.get('current_step', 1))

        if current_step == 1:
            selected_offer_id = request.form.get('selected_offer_id')
            if selected_offer_id and int(selected_offer_id) != offer.id:
                offer = Offer.query.get_or_404(int(selected_offer_id))
                offer_id = offer.id
                
            if offer.availability and 'out of stock' in offer.availability.lower():
                flash('The selected platform is currently out of stock. Please choose another.', 'danger')
                return redirect(url_for('checkout', offer_id=offer.id, step=1))
                
            quantity = max(1, min(10, int(request.form.get('quantity', 1))))
            data['quantity'] = quantity
            data['product_id'] = product.id
            data['offer_id'] = offer.id
            session['checkout_data'] = data
            return redirect(url_for('checkout', offer_id=offer_id, step=2))

        if current_step == 2:
            selected_address_id = request.form.get('address_id')
            
            if selected_address_id and selected_address_id != 'new':
                addr = Address.query.filter_by(id=selected_address_id, user_id=current_user.id).first()
                if addr:
                    name = addr.name
                    phone = addr.phone
                    email = current_user.email
                    street = addr.street
                    city = addr.city
                    state = addr.state
                    pincode = addr.pincode
                    landmark = addr.landmark
                else:
                    flash('Invalid address selected.', 'danger')
                    return render_template('checkout.html', step=2, product=product, offer=offer, data=data, addresses=addresses)
            else:
                name = request.form.get('name', '').strip()
                phone = request.form.get('phone', '').strip()
                email = request.form.get('email', '').strip()
                street = request.form.get('street', '').strip()
                city = request.form.get('city', '').strip()
                state = request.form.get('state', '').strip()
                pincode = request.form.get('pincode', '').strip()
                landmark = request.form.get('landmark', '').strip()

                if not all([name, phone, email, street, city, state, pincode]):
                    flash('Please complete all mandatory delivery fields.', 'danger')
                    return render_template('checkout.html', step=2, product=product, offer=offer, data=data, addresses=addresses)
                if not re.match(r'^[6-9]\d{9}$', phone):
                    flash('Please enter a valid 10-digit Indian mobile number (starts with 6-9).', 'danger')
                    return render_template('checkout.html', step=2, product=product, offer=offer, data=data, addresses=addresses)
                if not re.match(r'^[1-9][0-9]{5}$', pincode):
                    flash('Please enter a valid 6-digit pincode.', 'danger')
                    return render_template('checkout.html', step=2, product=product, offer=offer, data=data, addresses=addresses)

                # Check if this exact address is already saved to prevent duplicates
                existing_addr = Address.query.filter_by(
                    user_id=current_user.id,
                    name=name, phone=phone, street=street, city=city,
                    state=state, pincode=pincode
                ).first()

                if not existing_addr:
                    new_addr = Address(
                        user_id=current_user.id,
                        name=name, phone=phone, street=street, city=city,
                        state=state, pincode=pincode, landmark=landmark,
                        is_default=not bool(addresses)
                    )
                    db.session.add(new_addr)
                    db.session.commit()
                
            data.update({
                'name': name,
                'phone': phone,
                'email': email,
                'street': street,
                'city': city,
                'state': state,
                'pincode': pincode,
                'landmark': landmark,
            })
            session['checkout_data'] = data
            return redirect(url_for('checkout', offer_id=offer_id, step=3))

        if current_step == 3:
            payment_method = request.form.get('payment_method', 'Cash on Delivery')
            if payment_method not in ['Credit/Debit Card', 'UPI', 'Net Banking', 'Cash on Delivery']:
                flash('Invalid payment method selected.', 'danger')
                return render_template('checkout.html', step=3, product=product, offer=offer, data=data, addresses=addresses)
            
            detailed_method = payment_method
            if payment_method == 'Credit/Debit Card':
                card_num = request.form.get('card_number', '').replace(' ', '')
                if len(card_num) >= 4:
                    detailed_method = f"Credit/Debit Card (ending in {card_num[-4:]})"
            elif payment_method == 'UPI':
                upi_id = request.form.get('upi_id', '')
                if upi_id:
                    detailed_method = f"UPI ({upi_id})"
            elif payment_method == 'Net Banking':
                bank = request.form.get('bank_name', '')
                if bank:
                    detailed_method = f"Net Banking ({bank})"
                    
            data['payment_method'] = detailed_method
            data['coupon_code'] = request.form.get('applied_coupon', '').strip().upper()
            session['checkout_data'] = data
            return redirect(url_for('checkout', offer_id=offer_id, step=4))

        if current_step == 4:
            # Place order
            if not data.get('name') or not data.get('phone'):
                flash('Checkout data missing; please restart the checkout flow.', 'danger')
                session.pop('checkout_data', None)
                return redirect(url_for('checkout', offer_id=offer_id))

            if offer.availability and 'out of stock' in offer.availability.lower():
                flash('Sorry, the product just went out of stock.', 'danger')
                return redirect(url_for('checkout', offer_id=offer.id, step=1))

            order_id = generate_order_id()
            qty = int(data.get('quantity', 1))
            total_price = float(offer.price)
            estimated_delivery = (datetime.now(timezone.utc).date() + timedelta(days=random.randint(3, 7)))
            full_address = f"{data['street']}, {data['city']}, {data['state']} - {data['pincode']}"
            if data.get('landmark'):
                full_address += f", Landmark: {data['landmark']}"

            pf = float(os.environ.get('PLATFORM_FEE', 50))
            df = float(os.environ.get('DELIVERY_FEE', 100))
            hf = float(os.environ.get('HANDLING_FEE', 50)) if (total_price * qty) > float(os.environ.get('HANDLING_FEE_THRESHOLD', 10000)) else 0.0

            coupon_code = data.get('coupon_code', '')
            discount_amount = 0.0
            if coupon_code:
                discount_amount, err = validate_coupon_logic(coupon_code, current_user.id, total_price * qty, data.get('payment_method', ''))
                if err:
                    flash(err, 'danger')
                    return redirect(url_for('checkout', offer_id=offer_id, step=3))

            order = Order(
                order_id=order_id,
                user_id=current_user.id,
                product_id=product.id,
                product_name=product.name,
                product_image=product.image_url,
                quantity=qty,
                price=total_price,
                platform_fee=pf,
                delivery_fee=df,
                handling_fee=hf,
                coupon_code=coupon_code if coupon_code else None,
                discount_amount=discount_amount,
                address=full_address,
                phone=data['phone'],
                email=data['email'],
                payment_method=data['payment_method'],
                status='Placed',
                estimated_delivery_date=estimated_delivery,
            )
            db.session.add(order)
            db.session.commit()

            send_order_confirmation_email(current_user, order)
            log_activity(current_user.id, 'order_placed', f"Order {order.order_id} placed for {product.name}")
            session.pop('checkout_data', None)

            return redirect(url_for('order_success', order_id=order.order_id))

    # GET or reload
    if step == 1:
        data.setdefault('quantity', 1)
    elif step == 2:
        data.setdefault('street', '')
        data.setdefault('city', '')
        data.setdefault('state', '')
        data.setdefault('pincode', '')
        data.setdefault('landmark', '')
    elif step == 3:
        data.setdefault('payment_method', 'Cash on Delivery')
    elif step == 4:
        subtotal = float(offer.price) * int(data.get('quantity', 1))
        coupon_code = data.get('coupon_code', '')
        discount = 0.0
        if coupon_code:
            discount, err = validate_coupon_logic(coupon_code, current_user.id, subtotal, data.get('payment_method', ''))
            if err:
                flash(err, 'warning')
                data['coupon_code'] = ''
                discount = 0.0
        data['discount_amount'] = discount

    session['checkout_data'] = data
    return render_template('checkout.html', step=step, product=product, offer=offer, data=data, addresses=addresses, sorted_offers=sorted_offers)


@app.route('/order/success/<order_id>')
@login_required
def order_success(order_id):
    order = Order.query.filter_by(order_id=order_id, user_id=current_user.id).first_or_404()
    return render_template('order_success.html', order=order)


@app.route('/orders')
@login_required
def orders():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    changed = False
    for order in orders:
        previous_status = order.status
        simulate_order_status(order)
        if order.status != previous_status:
            changed = True
    if changed:
        db.session.commit()
    return render_template('orders.html', orders=orders)


@app.route('/order/<order_id>')
@login_required
def order_detail(order_id):
    order = Order.query.filter_by(order_id=order_id, user_id=current_user.id).first_or_404()
    previous_status = order.status
    simulate_order_status(order)
    if order.status != previous_status:
        db.session.commit()
    return render_template('order_detail.html', order=order)


@app.route('/order/<order_id>/invoice')
@login_required
def order_invoice(order_id):
    order = Order.query.filter_by(order_id=order_id, user_id=current_user.id).first_or_404()
    return render_template('order_invoice.html', order=order)

@app.route('/order/<order_id>/cancel', methods=['POST'])
@login_required
def cancel_order(order_id):
    order = Order.query.filter_by(order_id=order_id, user_id=current_user.id).first_or_404()
    
    # Allow cancellation only if order is not shipped/delivered
    if order.status in ['Placed', 'Processing']:
        order.status = 'Cancelled'
        db.session.commit()
        send_order_cancelled_email(current_user, order)
        log_activity(current_user.id, 'order_cancelled', f"Order {order.order_id} cancelled.")
        flash(f'Order {order.order_id} has been cancelled successfully.', 'success')
    else:
        flash(f'Order cannot be cancelled as it is already {order.status}.', 'danger')
        
    return redirect(request.referrer or url_for('orders'))


@app.route('/order/<order_id>/review', methods=['POST'])
@login_required
def submit_review(order_id):
    order = Order.query.filter_by(order_id=order_id, user_id=current_user.id).first_or_404()
    
    if order.status != 'Delivered':
        flash('You can only review delivered orders.', 'danger')
        return redirect(url_for('order_detail', order_id=order.order_id))
        
    rating = request.form.get('rating', type=int)
    review_text = request.form.get('review_text', '').strip()
    
    if rating and 1 <= rating <= 5:
        order.user_rating = rating
        order.user_review = review_text
        db.session.commit()
        flash('Thank you for your feedback! Your review has been submitted.', 'success')
    else:
        flash('Please select a valid rating between 1 and 5.', 'danger')
        
    return redirect(url_for('order_detail', order_id=order.order_id))

@app.route('/api/validate-coupon', methods=['POST'])
@login_required
def api_validate_coupon():
    data = request.get_json() or {}
    code = data.get('code', '')
    subtotal = float(data.get('subtotal', 0))
    payment_method = data.get('payment_method', '')
    
    discount, error = validate_coupon_logic(code, current_user.id, subtotal, payment_method)
    if error:
        return jsonify({'success': False, 'message': error})
    return jsonify({'success': True, 'discount': discount, 'code': code.upper()})

@app.route('/api/order/create', methods=['POST'])
@login_required
def api_order_create():
    payload = request.get_json() or {}
    offer_id = payload.get('offer_id')
    quantity = int(payload.get('quantity', 1))

    offer = Offer.query.get_or_404(offer_id)
    product = offer.product
    
    if offer.availability and 'out of stock' in offer.availability.lower():
        return jsonify({'success': False, 'error': 'Product is out of stock on this platform'}), 400

    required = ['name', 'phone', 'email', 'street', 'city', 'state', 'pincode', 'payment_method']
    for field in required:
        if not payload.get(field):
            return jsonify({'success': False, 'error': f'{field} is required'}), 400

    if not re.match(r'^[6-9]\d{9}$', payload['phone']):
        return jsonify({'success': False, 'error': 'Invalid phone format'}), 400
    if not re.match(r'^[1-9][0-9]{5}$', payload['pincode']):
        return jsonify({'success': False, 'error': 'Invalid pincode'}), 400

    order_id = generate_order_id()
    estimated_delivery = (datetime.now(timezone.utc).date() + timedelta(days=random.randint(3, 7)))
    address = f"{payload['street']}, {payload['city']}, {payload['state']} - {payload['pincode']}"
    if payload.get('landmark'):
        address += f", Landmark: {payload.get('landmark')}."
        
    pf = float(os.environ.get('PLATFORM_FEE', 50))
    df = float(os.environ.get('DELIVERY_FEE', 100))
    hf = float(os.environ.get('HANDLING_FEE', 50)) if (float(offer.price) * quantity) > float(os.environ.get('HANDLING_FEE_THRESHOLD', 10000)) else 0.0

    order = Order(
        order_id=order_id,
        user_id=current_user.id,
        product_id=product.id,
        product_name=product.name,
        product_image=product.image_url,
        quantity=quantity,
        price=float(offer.price),
        platform_fee=pf,
        delivery_fee=df,
        handling_fee=hf,
        address=address,
        phone=payload['phone'],
        email=payload['email'],
        payment_method=payload['payment_method'],
        status='Placed',
        estimated_delivery_date=estimated_delivery,
    )
    db.session.add(order)
    db.session.commit()
    send_order_confirmation_email(current_user, order)

    return jsonify({'success': True, 'order_id': order.order_id, 'estimated_delivery': order.estimated_delivery_date.strftime('%Y-%m-%d')})


@app.route('/api/order/history')
@login_required
def api_order_history():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    result = []
    for o in orders:
        result.append({
            'order_id': o.order_id,
            'product_name': o.product_name,
            'quantity': o.quantity,
            'price': o.price,
            'total': o.total_amount,
            'status': o.status,
            'estimated_delivery_date': o.estimated_delivery_date.isoformat(),
            'created_at': o.created_at.isoformat()
        })
    return jsonify({'orders': result})


@app.route('/api/order/<order_id>')
@login_required
def api_order_get(order_id):
    o = Order.query.filter_by(order_id=order_id, user_id=current_user.id).first_or_404()
    return jsonify({
        'order_id': o.order_id,
        'product_name': o.product_name,
        'quantity': o.quantity,
        'price': o.price,
        'total': o.total_amount,
        'address': o.address,
        'phone': o.phone,
        'email': o.email,
        'payment_method': o.payment_method,
        'status': o.status,
        'estimated_delivery_date': o.estimated_delivery_date.isoformat(),
        'created_at': o.created_at.isoformat()
    })


@app.route('/alert/new', methods=['GET', 'POST'])
@login_required
def new_alert():
    form = AlertForm()
    form.product_id.choices = [(p.id, p.name) for p in Product.query.order_by(Product.name).all()]
    if form.validate_on_submit():
        alert = PriceAlert(
            user_id=current_user.id,
            product_id=form.product_id.data,
            target_price=form.target_price.data
        )
        db.session.add(alert)
        db.session.commit()
        flash('Price alert created!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('alert_form.html', form=form)

@app.route('/save/<int:product_id>')
@login_required
def save_product(product_id):
    if not SavedProduct.query.filter_by(user_id=current_user.id, product_id=product_id).first():
        sp = SavedProduct(user_id=current_user.id, product_id=product_id)
        db.session.add(sp)
        db.session.commit()
        flash('Product saved to your list!', 'success')
    else:
        flash('Already saved.', 'info')
    p = db.session.get(Product, product_id)
    return redirect(url_for('product_detail', slug=p.slug))

@app.route('/unsave/<int:product_id>')
@login_required
def unsave_product(product_id):
    sp = SavedProduct.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if sp:
        db.session.delete(sp)
        db.session.commit()
    p = db.session.get(Product, product_id)
    return redirect(url_for('product_detail', slug=p.slug))

@app.route('/alert/delete/<int:alert_id>', methods=['POST'])
@login_required
def delete_alert(alert_id):
    alert = PriceAlert.query.get_or_404(alert_id)
    if alert.user_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('dashboard'))
    db.session.delete(alert)
    db.session.commit()
    flash('Alert deleted.', 'success')
    return redirect(url_for('alerts'))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def user_settings():
    if getattr(current_user, 'is_admin', False):
        return redirect(url_for('admin_settings'))
        
    form = AdminSettingsForm(obj=current_user)
    
    if form.validate_on_submit():
        needs_otp = False
        new_data = {}

        if form.username.data != current_user.username:
            if User.query.filter(User.username == form.username.data, User.id != current_user.id).first():
                flash('Username already taken.', 'danger')
                return render_template('settings.html', form=form)
            new_data['username'] = form.username.data

        if form.phone.data and getattr(current_user, 'phone', '') != form.phone.data:
            new_data['phone'] = form.phone.data

        if form.email.data != current_user.email:
            if User.query.filter(User.email == form.email.data, User.id != current_user.id).first():
                flash('Email already registered.', 'danger')
                return render_template('settings.html', form=form)
            new_data['email'] = form.email.data
            needs_otp = True

        if form.new_password.data:
            new_data['password_hash'] = generate_password_hash(form.new_password.data)
            needs_otp = True

        if not new_data:
            flash('No changes detected.', 'info')
            return redirect(url_for('user_settings'))

        if needs_otp:
            otp = str(random.randint(100000, 999999))
            new_data['otp'] = otp
            session['update_settings_data'] = new_data
            
            msg = Message('Your Settings Update Verification OTP', sender=app.config.get('MAIL_USERNAME'), recipients=[current_user.email])
            msg.body = f"Your OTP to update your settings is {otp}. Please do not share it with anyone."
            try:
                mail.send(msg)
                flash('An OTP has been sent to your current email. Please verify to apply critical changes.', 'info')
                return redirect(url_for('verify_otp', action='update_settings'))
            except Exception as e:
                flash('Failed to send OTP email. Please try again later.', 'danger')
                return render_template('settings.html', form=form)
        else:
            for key, val in new_data.items():
                setattr(current_user, key, val)
            db.session.commit()
            flash('Your settings have been updated successfully!', 'success')
            return redirect(url_for('user_settings'))
            
    form.username.data = current_user.username
    form.email.data = current_user.email
    form.phone.data = getattr(current_user, 'phone', '')
    return render_template('settings.html', form=form)

@app.route('/alerts')
@login_required
def alerts():
    user_alerts = PriceAlert.query.filter_by(user_id=current_user.id)\
        .order_by(PriceAlert.created_at.desc()).all()
    return render_template('alerts.html', alerts=user_alerts)

@app.route('/api/recommendations')
def recommendations():
    rec_type = request.args.get('type', 'trending')
    if rec_type == 'trending':
        products = Product.query.filter(Product.views > 0).order_by(Product.views.desc()).limit(6).all()
    elif rec_type == 'best_value':
        products = Product.query.filter(Product.rating >= 4).all()
        products.sort(key=lambda p: min((o.price for o in p.offers), default=float('inf')))
        products = products[:6]
    elif rec_type == 'similar':
        slug = request.args.get('slug')
        q = request.args.get('q')
        products = []
        is_ai_powered = False
        
        target_name = None
        target_brand = None
        target_price = 0
        target_id = None
        target_cat = None
        
        if slug:
            p = Product.query.filter_by(slug=slug).first()
            if p:
                target_name = p.name
                target_brand = p.brand
                p_prices = [o.price for o in p.offers if o.price]
                target_price = min(p_prices) if p_prices else 0
                target_id = p.id
                target_cat = p.category
        elif q:
            target_name = q
            
        if target_name:
            cache_key = f"ai_sim_v3_{slug or q}"
            ai_names = session.get(cache_key)
            
            if not ai_names:
                from utils.ai_helpers import call_groq
                import json
                prompt = f"Provide 6 alternative products from DIFFERENT brands with similar specifications and price range to '{target_name}'. Return strictly a JSON array of strings containing only the product names. Do not include the original brand if known. No markdown, no extra text."
                try:
                    resp = call_groq(prompt)
                    if resp:
                        cleaned = resp.replace('```json', '').replace('```', '').strip()
                        ai_names = json.loads(cleaned)
                        if isinstance(ai_names, list):
                            session[cache_key] = ai_names
                except Exception as e:
                    print(f"AI Similar Error: {e}")
                    ai_names = []
            
            if ai_names and isinstance(ai_names, list):
                for name in ai_names:
                    words = name.split()
                    if len(words) >= 2:
                        search_term = f"%{words[0]} {words[1]}%"
                    else:
                        search_term = f"%{name}%"
                        
                    query_filter = Product.name.ilike(search_term)
                    if target_id:
                        query_filter = db.and_(query_filter, Product.id != target_id)
                        
                    match = Product.query.filter(query_filter).first()
                    if match and match not in products:
                        products.append(match)
                        
            if len(products) > 0:
                is_ai_powered = True
                
            if len(products) < 6 and target_id:
                filters = [Product.id != target_id]
                if target_cat and target_cat != 'Unknown':
                    filters.append(Product.category == target_cat)
                else:
                    name_lower = target_name.lower()
                    if 'tv' in name_lower or 'television' in name_lower: filters.append(Product.name.ilike('%tv%'))
                    elif 'laptop' in name_lower or 'macbook' in name_lower: filters.append(Product.name.ilike('%laptop%'))
                    elif 'watch' in name_lower: filters.append(Product.name.ilike('%watch%'))
                    elif '5g' in name_lower or 'phone' in name_lower: filters.append(Product.name.ilike('%5g%'))
                
                if target_brand and target_brand != 'Unknown':
                    filters.append(Product.brand != target_brand)
                    filters.append(Product.brand != '')
                    
                candidates = Product.query.filter(*filters).all()
                if target_price > 0:
                    def price_diff(prod):
                        prices = [o.price for o in prod.offers if o.price]
                        prod_price = min(prices) if prices else 0
                        if prod_price == 0 or prod_price < (target_price * 0.5) or prod_price > (target_price * 1.5):
                            return float('inf')
                        return abs(prod_price - target_price)
                        
                    candidates = [c for c in candidates if price_diff(c) != float('inf')]
                    candidates.sort(key=price_diff)
                else:
                    candidates.sort(key=lambda x: x.views or 0, reverse=True)
                    
                for c in candidates:
                    if c not in products:
                        products.append(c)
                    if len(products) >= 6:
                        break
                        
            if len(products) < 4 and not target_id and q:
                query_word = q.split()[0]
                more = Product.query.filter(Product.name.ilike(f'%{query_word}%')).order_by(Product.views.desc()).limit(6).all()
                for m in more:
                    if m not in products:
                        products.append(m)
                        if len(products) >= 6:
                            break
                            
            products = products[:6]
    else:
        products = []
        is_ai_powered = False
        
    data = []
    for p in products:
        prices = [o.price for o in p.offers if o.price]
        lowest_price = min(prices) if prices else 0
        data.append({'id': p.id, 'name': p.name, 'slug': p.slug, 'image': p.image_url, 'rating': p.rating, 'price': lowest_price, 'is_ai': is_ai_powered})
    return jsonify(data)

@app.route('/api/product/<slug>/analyze')
def product_analyze(slug):
    product = Product.query.filter_by(slug=slug).first_or_404()
    from utils.ai_helpers import call_groq
    
    # Check session cache so we don't call AI multiple times
    cache_key = f"analysis_{product.id}"
    if cache_key in session:
        return jsonify({'analysis': session[cache_key]})
        
    prompt = (
        f"Act as an expert Indian shopping analyst. Provide a professional analysis for '{product.name}' "
        f"specifically for Indian consumers. Consider Indian market pricing in INR (₹), availability on "
        f"platforms like Amazon.in, Flipkart, Meesho, Croma, Reliance Digital, Tata Cliq, and Myntra. "
        f"Factor in Indian usage conditions (heat, humidity, power fluctuations), after-sales service "
        f"availability in India, and value for money in the Indian context. "
        "Return ONLY raw HTML. Do not use markdown blocks. Structure exactly like this:\n"
        "<p><strong>📝 Executive Summary:</strong> [Brief 2-3 sentences explaining the product and its main appeal for Indian buyers]</p>\n"
        "<p><strong>🎯 Best Suited For:</strong> [Target audience in India / Who should buy this]</p>\n"
        "<p><strong>⚖️ Pros & Cons:</strong></p>\n"
        "<ul style='list-style-type:none; padding-left:0;'>\n"
        "<li style='margin-bottom:4px;'>✅ [Pro 1 relevant to Indian users]</li>\n"
        "<li style='margin-bottom:4px;'>✅ [Pro 2 relevant to Indian users]</li>\n"
        "<li style='margin-bottom:4px;'>❌ [Con 1 for Indian market]</li>\n"
        "<li style='margin-bottom:4px;'>❌ [Con 2 for Indian market]</li>\n"
        "</ul>\n"
        "<p><strong>🇮🇳 India-Specific Notes:</strong> [Mention warranty, service centres in India, ISI/BIS certification if relevant, or India-specific features like dual-SIM, voltage compatibility etc.]</p>\n"
        "<p><strong>💎 Value for Money (India):</strong> [Score out of 10] - [Brief reason considering INR pricing and Indian alternatives]</p>\n"
        "<hr style='border-top:1px solid #e9ecef; margin:15px 0;'>\n"
        "<p style='margin-bottom:0;'><strong>🏆 Final Verdict:</strong> [Clear buy/don't buy recommendation for Indian shoppers]</p>"
    )
    analysis = call_groq(prompt)
    if not analysis:
        analysis = "Analysis currently unavailable. Please try again later."
    else:
        analysis = analysis.replace('```html', '').replace('```', '').strip()
        session[cache_key] = analysis
        
    return jsonify({'analysis': analysis})

@app.route('/api/product/<int:product_id>/ai-info')
def product_ai_info(product_id):
    product = Product.query.get_or_404(product_id)
    cache_key = f"ai_info_v2_{product.id}"
    if cache_key in session:
        return jsonify(session[cache_key])
        
    from utils.ai_helpers import call_groq
    import json
    
    prompt = f"Identify the exact Brand, a specific Category, and 3-4 key Specifications for this product: '{product.name}'. The specifications should be relevant to the category (e.g., RAM/Storage/Processor for Electronics, Material/Dimensions for Furniture, Capacity/Power for Home Appliances). Return strictly a valid JSON object with keys 'brand', 'category', and 'specs' (where 'specs' is a key-value dictionary of strings). Do not return any other text or markdown."
    
    response = call_groq(prompt)
    try:
        cleaned = response.replace('```json', '').replace('```', '').strip()
        data = json.loads(cleaned)
        
        updated = False
        if not product.brand and data.get('brand') and data['brand'] != 'Unknown':
            product.brand = data['brand']
            updated = True
        if not product.category and data.get('category') and data['category'] != 'Unknown':
            product.category = data['category']
            updated = True
        if updated:
            db.session.commit()
            
        session[cache_key] = data
        return jsonify(data)
    except Exception as e:
        return jsonify({'brand': product.brand or 'Unknown', 'category': product.category or 'Unknown', 'specs': {}})

@app.route('/api/share/product', methods=['POST'])
def share_product():
    data = request.get_json()
    slug = data.get('product_id') # Note: JS passes slug here
    email_to = data.get('email')
    
    from utils.ai_helpers import call_groq
    if current_user.is_authenticated and not email_to:
        email_to = current_user.email
        
    if not email_to:
        return jsonify({'success': False, 'error': 'Email is required'}), 400
        
    product = Product.query.filter_by(slug=slug).first()
    if not product:
        return jsonify({'success': False, 'error': 'Product not found'}), 404
        
    # Get analysis from session or generate it
    cache_key = f"analysis_{product.id}"
    analysis = session.get(cache_key)
    if not analysis:
        prompt = (
            f"Act as an expert Indian shopping analyst. Provide a professional analysis for '{product.name}' "
            f"specifically for Indian consumers. Consider Indian market pricing in INR (₹), availability on "
            f"platforms like Amazon.in, Flipkart, Meesho, Croma, Reliance Digital, Tata Cliq, and Myntra. "
            f"Factor in Indian usage conditions (heat, humidity, power fluctuations), after-sales service "
            f"availability in India, and value for money in the Indian context. "
            "Return ONLY raw HTML. Do not use markdown blocks. Structure exactly like this:\n"
            "<p><strong>📝 Executive Summary:</strong> [Brief 2-3 sentences explaining the product and its main appeal for Indian buyers]</p>\n"
            "<p><strong>🎯 Best Suited For:</strong> [Target audience in India / Who should buy this]</p>\n"
            "<p><strong>⚖️ Pros & Cons:</strong></p>\n"
            "<ul style='list-style-type:none; padding-left:0;'>\n"
            "<li style='margin-bottom:4px;'>✅ [Pro 1 relevant to Indian users]</li>\n"
            "<li style='margin-bottom:4px;'>✅ [Pro 2 relevant to Indian users]</li>\n"
            "<li style='margin-bottom:4px;'>❌ [Con 1 for Indian market]</li>\n"
            "<li style='margin-bottom:4px;'>❌ [Con 2 for Indian market]</li>\n"
            "</ul>\n"
            "<p><strong>🇮🇳 India-Specific Notes:</strong> [Mention warranty, service centres in India, ISI/BIS certification if relevant, or India-specific features like dual-SIM, voltage compatibility etc.]</p>\n"
            "<p><strong>💎 Value for Money (India):</strong> [Score out of 10] - [Brief reason considering INR pricing and Indian alternatives]</p>\n"
            "<hr style='border-top:1px solid #e9ecef; margin:15px 0;'>\n"
            "<p style='margin-bottom:0;'><strong>🏆 Final Verdict:</strong> [Clear buy/don't buy recommendation for Indian shoppers]</p>"
        )
        analysis = call_groq(prompt)
        if not analysis:
            analysis = "Analysis currently unavailable."
        else:
            analysis = analysis.replace('```html', '').replace('```', '').strip()
            
    msg = Message(f"SmartShoppy: Best Prices for {product.name[:40]}",
                  sender=app.config.get('MAIL_USERNAME'),
                  recipients=[email_to])
                  
    # Get ALL offers sorted by price
    all_offers = sorted([o for o in product.offers if o.price], key=lambda o: o.price)
    best_offer = all_offers[0] if all_offers else None
    csymbol = '₹'
    best_price_text = f"₹{best_offer.price:,.0f}" if best_offer else "N/A"
    best_seller_text = best_offer.seller if best_offer else "N/A"
    buy_link = best_offer.url if best_offer and best_offer.url else request.host_url

    # Product image
    image_html = (
        f'<img src="{product.image_url}" alt="{product.name}" '
        f'style="max-width:180px; max-height:180px; object-fit:contain; border-radius:10px; '
        f'border:1px solid #e9ecef; padding:8px; background:#fff;">'
    ) if product.image_url else ''

    # Star rating helper
    def star_html(rating):
        if not rating:
            return '<span style="color:#aaa;">No ratings</span>'
        full = int(rating)
        half = 1 if (rating - full) >= 0.5 else 0
        empty = 5 - full - half
        stars = '★' * full + ('½' if half else '') + '☆' * empty
        return f'<span style="color:#f59e0b; font-size:14px;">{stars}</span> <span style="color:#555; font-size:12px;">({rating:.1f})</span>'

    # Build offers rows for each platform
    offer_rows_html = ''
    for idx, offer in enumerate(all_offers):
        is_best = idx == 0
        row_bg = '#f0fff4' if is_best else ('#ffffff' if idx % 2 == 0 else '#f8f9fa')
        badge = '<span style="background:#16a34a; color:#fff; font-size:10px; padding:2px 7px; border-radius:20px; font-weight:bold; margin-left:6px;">BEST PRICE</span>' if is_best else ''
        o_price = f"₹{offer.price:,.0f}"
        o_seller = offer.seller or 'Unknown'
        o_avail = offer.availability or 'In Stock'
        avail_color = '#16a34a' if 'stock' in o_avail.lower() else '#dc2626'
        o_rating_html = star_html(offer.rating) if offer.rating else '<span style="color:#aaa;font-size:12px;">—</span>'
        o_reviews = f'{offer.review_count:,} reviews' if offer.review_count else '—'
        o_link = offer.url or request.host_url
        offer_rows_html += f"""
        <tr style="background:{row_bg};">
          <td style="padding:12px 10px; border-bottom:1px solid #e9ecef; font-weight:{'700' if is_best else '400'}; color:#1e293b;">
            {o_seller}{badge}
          </td>
          <td style="padding:12px 10px; border-bottom:1px solid #e9ecef; font-size:18px; font-weight:700; color:{'#16a34a' if is_best else '#0d6efd'}; white-space:nowrap;">
            {o_price}
          </td>
          <td style="padding:12px 10px; border-bottom:1px solid #e9ecef;">
            {o_rating_html}<br>
            <span style="color:#64748b; font-size:11px;">{o_reviews}</span>
          </td>
          <td style="padding:12px 10px; border-bottom:1px solid #e9ecef; font-size:12px; color:{avail_color}; font-weight:600;">
            {o_avail}
          </td>
          <td style="padding:12px 10px; border-bottom:1px solid #e9ecef; text-align:center;">
            <a href="{o_link}" style="background:{'#16a34a' if is_best else '#0d6efd'}; color:#fff; text-decoration:none; padding:7px 16px; border-radius:5px; font-size:13px; font-weight:600; white-space:nowrap;">Buy Now →</a>
          </td>
        </tr>"""

    # Product meta
    product_rating_html = star_html(product.rating) if product.rating else ''
    total_reviews = f'{product.review_count:,} ratings' if product.review_count else ''
    brand_html = f'<span style="color:#64748b; font-size:13px;">Brand: <strong>{product.brand}</strong></span> &nbsp;|&nbsp;' if product.brand else ''
    category_html = f'<span style="color:#64748b; font-size:13px;">Category: <strong>{product.category}</strong></span>' if product.category else ''

    # Fallback markdown bold fix
    html_analysis = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', analysis)

    from datetime import datetime
    import pytz

    ist = pytz.timezone('Asia/Kolkata')
    time_now = datetime.now(ist).strftime('%d %b %Y, %I:%M %p')

    # ---------- PRICE ANALYSIS CALCULATIONS ----------

    prices = [o.price for o in product.offers if o.price]

    current_price = min(prices) if prices else 0
    previous_price = getattr(product, "previous_price", current_price)  # fallback

    price_change_val = current_price - previous_price

    if previous_price > 0:
        price_change_percent = (price_change_val / previous_price) * 100
    else:
        price_change_percent = 0

    # Format text
    price_change = f"{price_change_percent:.2f}%"
    price_color = "#16a34a" if price_change_val < 0 else "#dc2626"

    lowest_price = min(prices) if prices else current_price
    avg_price = sum(prices) / len(prices) if prices else current_price
    highest_price = max(prices) if prices else current_price

    savings_amount = f"₹{highest_price - current_price:,.0f}" if prices else "₹0"

    # ---------- RECOMMENDATION ENGINE ----------

    if price_change_percent <= -5:
        recommendation = "🟢 BUY NOW"
        rec_bg = "#dcfce7"
        rec_color = "#166534"
        trend_text = "Prices are dropping significantly"
        insight_text = "This is one of the best recent deals"
        prediction_text = "Price may drop slightly more, but current deal is strong"

    elif price_change_percent >= 3:
        recommendation = "🔴 BUY SOON"
        rec_bg = "#fee2e2"
        rec_color = "#991b1b"
        trend_text = "Prices are increasing"
        insight_text = "Waiting may cost you more"
        prediction_text = "Price likely to increase further"

    else:
        recommendation = "🟡 WAIT"
        rec_bg = "#fef9c3"
        rec_color = "#854d0e"
        trend_text = "Price is stable"
        insight_text = "No major fluctuations observed"
        prediction_text = "Price may fluctuate slightly"

    # Format currency
    current_price = f"₹{current_price:,.0f}"
    previous_price = f"₹{previous_price:,.0f}"
    lowest_price = f"₹{lowest_price:,.0f}"
    avg_price = f"₹{avg_price:,.0f}"

    from datetime import datetime
    import pytz

    ist = pytz.timezone('Asia/Kolkata')
    time_now = datetime.now(ist).strftime('%d %b %Y, %I:%M %p')

    msg.html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background:#f4f6fb; font-family:Arial, sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="padding:30px 10px;">
<tr><td align="center">

<table width="600" style="background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 4px 12px rgba(0,0,0,0.08);">

<!-- HEADER -->
<tr>
<td style="background:#0f172a; padding:20px; text-align:center;">
<h1 style="color:#ffffff; margin:0; font-size:22px;">🛍️ SmartShoppy</h1>
<p style="color:#cbd5e1; margin:4px 0 0; font-size:13px;">Smart Price Analysis for Indian Shoppers</p>
</td>
</tr>

<!-- PRODUCT -->
<tr>
<td style="padding:24px; border-bottom:1px solid #e2e8f0;">
<table width="100%">
<tr>

<td width="140" style="text-align:center;">
{image_html}
</td>

<td style="padding-left:15px;">
<h2 style="margin:0 0 6px; font-size:18px; color:#0f172a;">{product.name}</h2>
<p style="margin:0 0 10px; font-size:13px; color:#64748b;">
{product_rating_html} {total_reviews}
</p>

<div style="background:#ecfdf5; padding:10px; border-radius:6px;">
<p style="margin:0; font-size:13px; color:#166534;">Best Price</p>
<h2 style="margin:4px 0; color:#16a34a;">{best_price_text}</h2>
<p style="margin:0; font-size:12px;">on {best_seller_text}</p>
<p style="margin:2px 0 0; font-size:12px;">💸 Save {savings_amount}</p>
</div>

<div style="margin-top:10px;">
<span style="background:{rec_bg}; color:{rec_color}; padding:4px 10px; border-radius:4px; font-size:12px; font-weight:bold;">
{recommendation}
</span>
</div>

</td>
</tr>
</table>

<div style="margin-top:15px;">
<a href="{buy_link}" style="background:#2563eb; color:#fff; padding:10px 18px; text-decoration:none; border-radius:6px; font-size:14px;">
Buy Now →
</a>
</div>

</td>
</tr>

<!-- PRICE ANALYSIS -->
<tr>
<td style="padding:20px; border-bottom:1px solid #e2e8f0;">
<h3 style="margin:0 0 10px; font-size:16px;">📊 Price Analysis</h3>

<table width="100%">
<tr>
<td style="font-size:13px;">Current</td>
<td style="font-size:13px;">Previous</td>
<td style="font-size:13px;">Change</td>
<td style="font-size:13px;">Lowest</td>
</tr>

<tr>
<td><b>{current_price}</b></td>
<td><b>{previous_price}</b></td>
<td style="color:{price_color};"><b>{price_change}</b></td>
<td><b>{lowest_price}</b></td>
</tr>
</table>

<p style="margin-top:8px; font-size:12px; color:#64748b;">
Average Price: {avg_price}
</p>

</td>
</tr>

<!-- COMPARISON -->
<tr>
<td style="padding:20px; border-bottom:1px solid #e2e8f0;">
<h3 style="margin:0 0 10px; font-size:16px;">🛒 Compare Prices</h3>

<table width="100%" border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; font-size:13px;">
<tr style="background:#f1f5f9;">
<th>Seller</th>
<th>Price</th>
<th>Action</th>
</tr>

{offer_rows_html}

</table>
</td>
</tr>

<!-- AI INSIGHTS -->
<tr>
<td style="padding:20px; background:#f8fafc;">
<h3 style="margin:0 0 10px; font-size:16px;">🤖 Smart Insights</h3>

<ul style="padding-left:18px; margin:0; font-size:13px;">
<li><b>Trend:</b> {trend_text}</li>
<li><b>Insight:</b> {insight_text}</li>
<li><b>Recommendation:</b> {recommendation}</li>
</ul>

<p style="margin-top:10px; font-size:13px;">
🔮 Prediction: {prediction_text}
</p>

</td>
</tr>

<!-- CTA -->
<tr>
<td style="text-align:center; padding:20px;">
<a href="{request.host_url}dashboard" style="background:#0f172a; color:#fff; padding:10px 18px; text-decoration:none; border-radius:6px; font-size:14px;">
Set Price Alert
</a>
</td>
</tr>

<!-- FOOTER -->
<tr>
<td style="background:#0f172a; color:#94a3b8; text-align:center; padding:15px; font-size:12px;">
<p style="margin:0;">© {datetime.now().year} SmartShoppy</p>
<p style="margin:4px 0;">Last updated: {time_now} IST</p>
</td>
</tr>

</table>

</td></tr>
</table>

</body>
</html>
    """
    try:
        mail.send(msg)
        return jsonify({'success': True, 'message': 'Analysis sent to your email!'})
    except Exception as e:
        print(f"Mail error: {e}")
        return jsonify({'success': False, 'error': 'Failed to send email. Check server configuration.'}), 500

# ---------- Auth Routes ----------

@app.route('/login', methods=['GET', 'POST'])
def login():
    # 🔹 Already logged-in user redirect
    if current_user.is_authenticated:
        if getattr(current_user, 'is_admin', False):
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('index'))

    # 🔹 Get login type (user/admin)
    user_type = request.args.get('type', 'user')  # default = user

    form = LoginForm()
    
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if request.method == 'POST' and not check_rate_limit(f"login_{client_ip}", limit=5, window=300):
        flash('Too many login attempts. Please try again after 5 minutes.', 'danger')
        return render_template('login.html', form=form, user_type=user_type)

    if form.validate_on_submit():
        user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.username.data)
        ).first()

        if user and check_password_hash(user.password_hash, form.password.data):

            # 🔴 ROLE VALIDATION (IMPORTANT)
            if getattr(user, 'is_blocked', False):
                flash('Your account has been permanently blocked by the administrator.', 'danger')
                log_activity(user.id, 'failed_login_blocked', f"Blocked account login attempt by {user.email}.")
                return redirect(url_for('login', type=user_type))
            
            if getattr(user, 'is_frozen', False):
                flash('Your account is temporarily frozen. Please contact support.', 'warning')
                log_activity(user.id, 'failed_login_frozen', f"Frozen account login attempt by {user.email}.")
                return redirect(url_for('login', type=user_type))
                
            if user_type == 'admin' and not getattr(user, 'is_admin', False):
                flash('This is not an admin account.', 'danger')
                return redirect(url_for('login', type='admin'))

            if user_type == 'admin' and getattr(user, 'is_admin', False):
                if not getattr(user, 'is_approved', True) and user.email != 'joshichaitanya354@gmail.com':
                    flash('Your admin account is pending approval by the Main Admin.', 'warning')
                    return redirect(url_for('login', type='admin'))

            if user_type == 'user' and getattr(user, 'is_admin', False):
                flash('Please use admin login.', 'warning')
                return redirect(url_for('login', type='user'))

            # 🔹 Generate OTP
            otp = str(random.randint(100000, 999999))

            session['login_data'] = {
                'user_id': user.id,
                'otp': otp,
                'remember': form.remember.data,
                'user_type': user_type   # ✅ store type
            }

            # 🔹 Send OTP Email
            msg = Message(
                'Your Login Verification OTP',
                sender=app.config.get('MAIL_USERNAME'),
                recipients=[user.email]
            )
            msg.body = f"Your OTP for login is {otp}. Please do not share it with anyone."

            try:
                mail.send(msg)

                flash('An OTP has been sent to your registered email. Please verify to login.', 'info')

                next_page = request.args.get('next')
                if next_page:
                    session['next_page'] = next_page

                return redirect(url_for('verify_otp', action='login'))

            except Exception as e:
                flash('Failed to send OTP email. Please try again later.', 'danger')
                print(f"Mail error: {e}")
                return render_template('login.html', form=form, user_type=user_type)

        flash('Invalid username or password.', 'danger')

    return render_template('login.html', form=form, user_type=user_type)

@app.route('/register', methods=['GET', 'POST'])
def register():
    # 🔹 Already logged-in user redirect
    if current_user.is_authenticated:
        if getattr(current_user, 'is_admin', False):
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('index'))

    # 🔹 Get type from URL (user/admin)
    user_type = request.args.get('type', 'user')  # default = user

    form = RegisterForm()

    if form.validate_on_submit():

        # 🔴 Username check
        if User.query.filter_by(username=form.username.data).first():
            flash('Username already taken.', 'danger')
            return render_template('register.html', form=form, user_type=user_type)

        # 🔴 Email check
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already registered.', 'danger')
            return render_template('register.html', form=form, user_type=user_type)

        # 🔹 Hash password
        hashed = generate_password_hash(form.password.data)

        # 🔹 Decide admin/user from URL (NOT form)
        is_admin = True if user_type == 'admin' else False

        # 🔹 Generate OTP
        otp = str(random.randint(100000, 999999))

        session['reg_data'] = {
            'username': form.username.data,
            'email': form.email.data,
            'password_hash': hashed,
            'is_admin': is_admin,   # ✅ controlled by URL
            'otp': otp,
            'user_type': user_type  # optional (future use)
        }

        # 🔹 Send OTP
        msg = Message(
            'Your Registration Verification OTP',
            sender=app.config.get('MAIL_USERNAME'),
            recipients=[form.email.data]
        )
        msg.body = f"Your OTP for registration is {otp}. It is valid for this session."

        try:
            mail.send(msg)

            flash('An OTP has been sent to your email. Please verify to complete registration.', 'info')

            return redirect(url_for('verify_otp', action='register'))

        except Exception as e:
            flash('Failed to send OTP email. Please try again later.', 'danger')
            print(f"Mail error: {e}")
            return render_template('register.html', form=form, user_type=user_type)

    return render_template('register.html', form=form, user_type=user_type)

@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    action = request.args.get('action')
    if action not in ['login', 'register', 'update_settings']:
        flash('Invalid action.', 'danger')
        return redirect(url_for('index'))

    if current_user.is_authenticated and action != 'update_settings':
        return redirect(url_for('index'))
        
    if action == 'register' and 'reg_data' not in session:
        flash('Session expired. Please register again.', 'warning')
        return redirect(url_for('register'))
        
    if action == 'login' and 'login_data' not in session:
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('login'))
        
    if action == 'update_settings' and 'update_settings_data' not in session:
        flash('Session expired. Please try updating again.', 'warning')
        return redirect(url_for('admin_settings' if getattr(current_user, 'is_admin', False) else 'user_settings'))
        
    form = OTPForm()
    if form.validate_on_submit():
        entered_otp = form.otp.data
        
        if action == 'register':
            reg_data = session['reg_data']
            if entered_otp == reg_data.get('otp'):
                is_admin = reg_data.get('is_admin', False)
                email = reg_data['email']
                # Main admin auto-approves, everyone else requires approval
                is_approved = True if not is_admin or email == 'joshichaitanya354@gmail.com' else False

                user = User(
                    username=reg_data['username'], 
                    email=email, 
                    password_hash=reg_data['password_hash'],
                    is_admin=is_admin
                )
                user.is_approved = is_approved

                db.session.add(user)
                db.session.commit()
                session.pop('reg_data', None)
                if is_admin and not is_approved:
                    flash('Registration successful! Your admin account is pending approval by the Main Admin.', 'info')
                else:
                    flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('login', type='admin' if is_admin else 'user'))
            else:
                flash('Invalid OTP. Please try again.', 'danger')
                
        elif action == 'login':
            login_data = session['login_data']
            if entered_otp == login_data.get('otp'):
                user = db.session.get(User, login_data['user_id'])
                if user:
                    if getattr(user, 'is_blocked', False) or getattr(user, 'is_frozen', False):
                        flash('Your account is currently blocked or frozen.', 'danger')
                        return redirect(url_for('login'))
                        
                    login_user(user, remember=login_data.get('remember', False))
                    session.pop('login_data', None)
                    flash('Logged in successfully.', 'success')
                    log_activity(user.id, 'login_success', f"User {user.email} logged in.")
                    if user.is_admin:
                        return redirect(url_for('admin_dashboard'))
                    next_page = session.pop('next_page', None)
                    return redirect(next_page or url_for('index'))
                else:
                    flash('User not found.', 'danger')
                    return redirect(url_for('login'))
            else:
                flash('Invalid OTP. Please try again.', 'danger')
                
        elif action == 'update_settings':
            update_data = session['update_settings_data']
            if entered_otp == update_data.get('otp'):
                update_data.pop('otp', None)
                for key, value in update_data.items():
                    setattr(current_user, key, value)
                db.session.commit()
                session.pop('update_settings_data', None)
                flash('Your settings have been updated successfully!', 'success')
                return redirect(url_for('admin_settings' if getattr(current_user, 'is_admin', False) else 'user_settings'))
            else:
                flash('Invalid OTP. Please try again.', 'danger')
                
    return render_template('verify_otp.html', form=form, action=action)

@app.route('/resend_otp', methods=['POST'])
def resend_otp():
    try:
        data = request.get_json() or {}
        action = data.get('action')
        
        email = None
        session_data = None
        
        if action == 'register':
            session_data = session.get('reg_data')
            if session_data:
                email = session_data.get('email')
        elif action == 'login':
            session_data = session.get('login_data')
            if session_data:
                user = db.session.get(User, session_data.get('user_id'))
                if user:
                    email = user.email
        elif action == 'update_settings':
            session_data = session.get('update_settings_data')
            if session_data:
                email = current_user.email
                
        if not email or not session_data:
            return jsonify({'success': False, 'message': 'Session expired. Please start over.'}), 400
            
        new_otp = str(random.randint(100000, 999999))
        session_data['otp'] = new_otp
        session.modified = True  # Ensure the updated dictionary is saved
            
        msg = Message('Your Verification OTP (Resent)',
                      sender=app.config.get('MAIL_USERNAME'),
                      recipients=[email])
        msg.body = f"Your new OTP is {new_otp}. Please do not share it with anyone."
        mail.send(msg)
        
        return jsonify({'success': True, 'message': 'OTP resent successfully.'})
    except Exception as e:
        print(f"Resend OTP Error: {e}")
        return jsonify({'success': False, 'message': 'Failed to resend OTP. Try again.'}), 500

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('recent_searches', None)
    return redirect(url_for('index'))

# ---------- Admin Analytics Route ----------

@app.route('/admin')
@login_required
def admin_dashboard():
    if not getattr(current_user, 'is_admin', False):
        flash('Access denied. Administrator privileges required.', 'danger')
        return redirect(url_for('index'))
        
    total_users = User.query.count()
    total_products = Product.query.count()
    total_alerts = PriceAlert.query.count()
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    top_products = Product.query.order_by(Product.views.desc()).limit(5).all()
    
    pending_admins = User.query.filter_by(is_admin=True, is_approved=False).all() if current_user.email == 'joshichaitanya354@gmail.com' else []
    
    main_admin_regular_users = User.query.filter_by(is_admin=False).order_by(User.created_at.desc()).all() if current_user.email == 'joshichaitanya354@gmail.com' else []
    main_admin_admins = User.query.filter(User.is_admin==True, User.email != 'joshichaitanya354@gmail.com').order_by(User.created_at.desc()).all() if current_user.email == 'joshichaitanya354@gmail.com' else []
    restricted_platforms = RestrictedPlatform.query.order_by(RestrictedPlatform.created_at.desc()).all() if current_user.email == 'joshichaitanya354@gmail.com' else []
    recent_logs = ActivityLog.query.order_by(ActivityLog.timestamp.desc()).limit(30).all() if current_user.email == 'joshichaitanya354@gmail.com' else []
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(10).all()

    return render_template('admin/dashboard.html', 
                           total_users=total_users, total_products=total_products, 
                           total_alerts=total_alerts, recent_users=recent_users, 
                           top_products=top_products, pending_admins=pending_admins,
                           main_admin_regular_users=main_admin_regular_users, main_admin_admins=main_admin_admins,
                           restricted_platforms=restricted_platforms, recent_logs=recent_logs,
                           recent_orders=recent_orders)

@app.route('/admin/orders')
@login_required
def admin_orders():
    if not getattr(current_user, 'is_admin', False):
        flash('Access denied. Administrator privileges required.', 'danger')
        return redirect(url_for('index'))
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders)

@app.route('/admin/order/<order_id>/update_status', methods=['POST'])
@login_required
def admin_update_order_status(order_id):
    if not getattr(current_user, 'is_admin', False):
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
        
    order = Order.query.filter_by(order_id=order_id).first_or_404()
    new_status = request.form.get('status')
    
    if new_status and new_status != order.status:
        order.status = new_status
        order.is_manual_status = True
        db.session.commit()
        
        # Send corresponding emails if needed
        user = db.session.get(User, order.user_id)
        if user:
            if new_status == 'Delivered':
                send_order_delivered_email(user, order)
            elif new_status == 'Cancelled':
                send_order_cancelled_email(user, order)
                
        log_activity(current_user.id, 'order_status_updated', f"Order {order_id} status updated to {new_status}")
        flash(f'Order {order_id} status updated to {new_status}.', 'success')
        
    return redirect(request.referrer or url_for('admin_orders'))

@app.route('/analytics')
@login_required
def analytics():
    if not current_user.is_admin:
        flash('Access denied. Administrator privileges required.', 'danger')
        return redirect(url_for('index'))
        
    # Passing complete stats to the analytics dashboard
    total_users = User.query.count()
    total_products = Product.query.count()
    total_offers = Offer.query.count()
    total_searches = db.session.query(func.sum(Product.views)).scalar() or 0
    top_products = Product.query.order_by(Product.views.desc()).limit(5).all()
    
    pending_admins = User.query.filter_by(is_admin=True, is_approved=False).all() if current_user.email == 'joshichaitanya354@gmail.com' else []
    
    main_admin_regular_users = User.query.filter_by(is_admin=False).order_by(User.created_at.desc()).all() if current_user.email == 'joshichaitanya354@gmail.com' else []
    main_admin_admins = User.query.filter(User.is_admin==True, User.email != 'joshichaitanya354@gmail.com').order_by(User.created_at.desc()).all() if current_user.email == 'joshichaitanya354@gmail.com' else []
    restricted_platforms = RestrictedPlatform.query.order_by(RestrictedPlatform.created_at.desc()).all() if current_user.email == 'joshichaitanya354@gmail.com' else []
    recent_logs = ActivityLog.query.order_by(ActivityLog.timestamp.desc()).limit(30).all() if current_user.email == 'joshichaitanya354@gmail.com' else []

    return render_template('admin/analytics.html', 
                           total_users=total_users, total_products=total_products, 
                           total_offers=total_offers, total_searches=total_searches, top_products=top_products, pending_admins=pending_admins,
                           main_admin_regular_users=main_admin_regular_users, main_admin_admins=main_admin_admins,
                           restricted_platforms=restricted_platforms, recent_logs=recent_logs)

@app.route('/admin/users')
@login_required
def admin_users():
    if not getattr(current_user, 'is_admin', False):
        flash('Access denied. Administrator privileges required.', 'danger')
        return redirect(url_for('index'))
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/products')
@login_required
def admin_products():
    if not getattr(current_user, 'is_admin', False):
        flash('Access denied. Administrator privileges required.', 'danger')
        return redirect(url_for('index'))
    products = Product.query.order_by(Product.views.desc()).all()
    return render_template('admin/products.html', products=products)

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    if not getattr(current_user, 'is_admin', False):
        flash('Access denied. Administrator privileges required.', 'danger')
        return redirect(url_for('index'))

    form = AdminSettingsForm(obj=current_user) # Pre-populate form with current user's email
    
    if form.validate_on_submit():
        needs_otp = False
        new_data = {}

        if form.username.data != current_user.username:
            if User.query.filter(User.username == form.username.data, User.id != current_user.id).first():
                flash('Username already taken by another user.', 'danger')
                return render_template('admin/settings.html', form=form)
            new_data['username'] = form.username.data

        if form.phone.data and getattr(current_user, 'phone', '') != form.phone.data:
            new_data['phone'] = form.phone.data

        if form.email.data != current_user.email:
            if User.query.filter(User.email == form.email.data, User.id != current_user.id).first():
                flash('Email already registered by another user.', 'danger')
                return render_template('admin/settings.html', form=form)
            new_data['email'] = form.email.data
            needs_otp = True

        if form.new_password.data:
            new_data['password_hash'] = generate_password_hash(form.new_password.data)
            needs_otp = True

        if not new_data:
            flash('No changes detected.', 'info')
            return redirect(url_for('admin_settings'))

        if needs_otp:
            otp = str(random.randint(100000, 999999))
            new_data['otp'] = otp
            session['update_settings_data'] = new_data
            
            msg = Message('Your Settings Update Verification OTP', sender=app.config.get('MAIL_USERNAME'), recipients=[current_user.email])
            msg.body = f"Your OTP to update your settings is {otp}. Please do not share it with anyone."
            try:
                mail.send(msg)
                flash('An OTP has been sent to your current email. Please verify to apply critical changes.', 'info')
                return redirect(url_for('verify_otp', action='update_settings'))
            except Exception as e:
                flash('Failed to send OTP email. Please try again later.', 'danger')
                return render_template('admin/settings.html', form=form)
        else:
            for key, val in new_data.items():
                setattr(current_user, key, val)
            db.session.commit()
            flash('Your settings have been updated successfully!', 'success')
            return redirect(url_for('admin_settings'))
    
    # For GET requests, ensure details are pre-filled
    form.username.data = current_user.username
    form.email.data = current_user.email
    form.phone.data = getattr(current_user, 'phone', '')
    return render_template('admin/settings.html', form=form)

@app.route('/admin/approvals')
@login_required
def admin_approvals():
    if current_user.email != 'joshichaitanya354@gmail.com':
        flash('Access denied. Only the Main Admin can view this page.', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    pending_admins = User.query.filter_by(is_admin=True, is_approved=False).all()
    return render_template('admin_approvals.html', admins=pending_admins)

@app.route('/admin/approve/<int:user_id>', methods=['POST'])
@login_required
def approve_admin(user_id):
    if current_user.email != 'joshichaitanya354@gmail.com':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
        
    user = User.query.get_or_404(user_id)
    user.is_approved = True
    db.session.commit()
    
    try:
        msg = Message('Admin Account Approved - SmartShoppy', sender=app.config.get('MAIL_USERNAME'), recipients=[user.email])
        msg.html = f"""
<div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
  
  <p>Dear {user.username},</p>
  
  <p>
    Congratulations! We are pleased to inform you that your request for an 
    <strong>Admin account</strong> on <strong>SmartShoppy</strong> has been 
    <span style="color: green; font-weight: bold;">successfully approved</span>.
  </p>
  
  <p>
    You can now log in to your admin dashboard and start managing your responsibilities.
  </p>
  
  <p>
    If you have any questions or need assistance, feel free to reach out to our support team.
  </p>
  
  <br>
  
  <p>Best regards,<br>
  <strong>SmartShoppy Team</strong></p>
  
  <hr style="border:none; border-top:1px solid #eee; margin-top:20px;">
  
  <p style="font-size: 12px; color: #888;">
    This is an automated message. Please do not reply directly to this email.
  </p>
  
</div>
"""
        mail.send(msg)
    except Exception as e:
        print(f"Approval email failed: {e}")
        
    log_activity(current_user.id, 'admin_approved', f"Approved admin account for {user.email}")
    flash(f'Admin {user.username} has been approved.', 'success')
    return redirect(request.referrer or url_for('admin_approvals'))

@app.route('/admin/reject/<int:user_id>', methods=['POST'])
@login_required
def reject_admin(user_id):
    if current_user.email != 'joshichaitanya354@gmail.com':
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
        
    user = User.query.get_or_404(user_id)
    email = user.email
    username = user.username
    
    db.session.delete(user)
    db.session.commit()
    
    try:
        msg = Message('Admin Account Rejected - SmartShoppy', sender=app.config.get('MAIL_USERNAME'), recipients=[email])
        msg.html = f"""
<div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
  
  <p>Dear {username},</p>
  
  <p>
    Thank you for your interest in joining <strong>SmartShoppy</strong> as an admin.
  </p>
  
  <p>
    After careful review, we regret to inform you that your admin account registration request 
    has not been approved at this time.
  </p>
  
  <p>
    If you believe this decision was made in error or would like further clarification, 
    please feel free to contact our support team.
  </p>
  
  <br>
  
  <p>Best regards,<br>
  <strong>SmartShoppy Team</strong></p>
  
  <hr style="border:none; border-top:1px solid #eee; margin-top:20px;">
  
  <p style="font-size: 12px; color: #888;">
    This is an automated message. Please do not reply directly to this email.
  </p>
  
</div>
"""
        mail.send(msg)
    except Exception as e:
        print(f"Rejection email failed: {e}")
        
    log_activity(current_user.id, 'admin_rejected', f"Rejected admin account request for {email}")
    flash(f'Admin {username} has been rejected and removed.', 'success')
    return redirect(request.referrer or url_for('admin_approvals'))

@app.route('/admin/user/<int:user_id>/toggle_status/<action>', methods=['POST'])
@login_required
def toggle_user_status(user_id, action):
    if current_user.email != 'joshichaitanya354@gmail.com':
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    user = User.query.get_or_404(user_id)
    if user.email == 'joshichaitanya354@gmail.com':
        flash("Action denied. Main Admin account cannot be modified.", "danger")
        return redirect(request.referrer or url_for('admin_dashboard'))
        
    if action == 'block':
        user.is_blocked = True
        user.is_frozen = False
        flash(f"User {user.username} has been permanently blocked.", "success")
        log_activity(current_user.id, 'user_blocked', f"Blocked user {user.email}")
    elif action == 'freeze':
        user.is_frozen = True
        user.is_blocked = False
        flash(f"User {user.username} has been frozen.", "success")
        log_activity(current_user.id, 'user_frozen', f"Froze user {user.email}")
    elif action == 'unfreeze':
        user.is_frozen = False
        flash(f"User {user.username} has been unfrozen and can log in again.", "success")
        log_activity(current_user.id, 'user_unfrozen', f"Unfroze user {user.email}")
    elif action == 'unblock':
        user.is_blocked = False
        flash(f"User {user.username} has been unblocked.", "success")
        log_activity(current_user.id, 'user_unblocked', f"Unblocked user {user.email}")
        
    db.session.commit()
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/addresses')
@login_required
def manage_addresses():
    addresses = Address.query.filter_by(user_id=current_user.id).order_by(Address.is_default.desc(), Address.created_at.desc()).all()
    form = AddressForm()
    return render_template('addresses.html', addresses=addresses, form=form)

@app.route('/address/add', methods=['POST'])
@login_required
def add_address():
    form = AddressForm()
    if form.validate_on_submit():
        if form.is_default.data or not Address.query.filter_by(user_id=current_user.id).first():
            Address.query.filter_by(user_id=current_user.id).update({'is_default': False})
        addr = Address(
            user_id=current_user.id,
            name=form.name.data,
            phone=form.phone.data,
            street=form.street.data,
            city=form.city.data,
            state=form.state.data,
            pincode=form.pincode.data,
            landmark=form.landmark.data,
            is_default=form.is_default.data or not Address.query.filter_by(user_id=current_user.id).first()
        )
        db.session.add(addr)
        db.session.commit()
        flash('Address added successfully.', 'success')
    else:
        flash('Error adding address. Please check all details and try again.', 'danger')
    return redirect(url_for('manage_addresses'))

@app.route('/address/edit/<int:addr_id>', methods=['POST'])
@login_required
def edit_address(addr_id):
    addr = Address.query.get_or_404(addr_id)
    if addr.user_id != current_user.id:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('manage_addresses'))
        
    form = AddressForm()
    if form.validate_on_submit():
        if form.is_default.data and not addr.is_default:
            Address.query.filter_by(user_id=current_user.id).update({'is_default': False})
            
        addr.name = form.name.data
        addr.phone = form.phone.data
        addr.street = form.street.data
        addr.city = form.city.data
        addr.state = form.state.data
        addr.pincode = form.pincode.data
        addr.landmark = form.landmark.data
        if form.is_default.data:
            addr.is_default = True
            
        db.session.commit()
        flash('Address updated successfully.', 'success')
    else:
        flash('Error updating address. Please check your inputs.', 'danger')
    return redirect(url_for('manage_addresses'))

@app.route('/address/delete/<int:addr_id>', methods=['POST'])
@login_required
def delete_address(addr_id):
    addr = Address.query.get_or_404(addr_id)
    if addr.user_id == current_user.id:
        db.session.delete(addr)
        db.session.commit()
        flash('Address deleted successfully.', 'success')
    return redirect(url_for('manage_addresses'))

@app.route('/address/default/<int:addr_id>', methods=['POST'])
@login_required
def set_default_address(addr_id):
    addr = Address.query.get_or_404(addr_id)
    if addr.user_id == current_user.id:
        Address.query.filter_by(user_id=current_user.id).update({'is_default': False})
        addr.is_default = True
        db.session.commit()
        flash('Default delivery address updated.', 'success')
    return redirect(url_for('manage_addresses'))

@app.route('/admin/platform/restrict', methods=['POST'])
@login_required
def restrict_platform():
    if current_user.email != 'joshichaitanya354@gmail.com':
        return redirect(url_for('admin_dashboard'))
        
    name = request.form.get('platform_name', '').strip().lower()
    if name:
        if RestrictedPlatform.query.filter_by(name=name).first():
            flash(f"Platform '{name}' is already restricted.", "info")
        else:
            rp = RestrictedPlatform(name=name, added_by=current_user.id)
            db.session.add(rp)
            db.session.commit()
            flash(f"Platform '{name}' has been restricted globally.", "success")
            log_activity(current_user.id, 'platform_restricted', f"Restricted platform: {name}")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/platform/unrestrict/<int:plat_id>', methods=['POST'])
@login_required
def unrestrict_platform(plat_id):
    if current_user.email != 'joshichaitanya354@gmail.com':
        return redirect(url_for('admin_dashboard'))
        
    rp = RestrictedPlatform.query.get_or_404(plat_id)
    name = rp.name
    db.session.delete(rp)
    db.session.commit()
    flash(f"Platform '{name}' has been unrestricted.", "success")
    log_activity(current_user.id, 'platform_unrestricted', f"Unrestricted platform: {name}")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/sw.js')
def serve_sw():
    return app.send_static_file('sw.js')

# ---------- Error Handlers ----------

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, msg='Page Not Found'), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, msg='Access Forbidden'), 403

@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500, msg='Internal Server Error'), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)