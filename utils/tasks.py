from apscheduler.schedulers.background import BackgroundScheduler
from utils.api_clients import fetch_offers_from_pricesapi, search_pricesapi_products
from models import db, Product, Offer, PriceHistory, PriceAlert
from datetime import date, datetime, timedelta, timezone
from flask_mail import Message
from flask import current_app

def update_all_products(app):
    """Run within app context."""
    with app.app_context():
        products = Product.query.all()
        for product in products:
            results = search_pricesapi_products(product.name, country='in')
            if not results:
                continue
            prod_id = str(results[0].get('id') or results[0].get('product_id', ''))
            if not prod_id:
                continue
            offers = fetch_offers_from_pricesapi(prod_id, country='in')
            if not offers:
                continue
            for offer_data in offers:
                price_val = offer_data.get('price')
                if not price_val:
                    continue
                try:
                    price = float(str(price_val).replace('₹', '').replace(',', ''))
                except:
                    continue

                off_rating = offer_data.get('rating')
                try: off_rating = float(off_rating) if off_rating else 0.0
                except: off_rating = 0.0
                
                off_reviews = offer_data.get('review_count')
                try: off_reviews = int(float(off_reviews)) if off_reviews else 0
                except: off_reviews = 0

                offer = Offer.query.filter_by(product_id=product.id, seller=offer_data['seller']).first()
                if offer:
                    offer.price = price
                    offer.availability = offer_data.get('availability', 'In Stock')
                    offer.url = offer_data.get('url')
                    offer.rating = off_rating
                    offer.review_count = off_reviews
                else:
                    offer = Offer(
                        product_id=product.id,
                        seller=offer_data['seller'],
                        price=price,
                        currency='INR',
                        availability=offer_data.get('availability', 'In Stock'),
                        url=offer_data.get('url'),
                        rating=off_rating,
                        review_count=off_reviews
                    )
                    db.session.add(offer)
                history = PriceHistory(
                    product_id=product.id,
                    seller=offer_data['seller'],
                    price=price,
                    date=date.today()
                )
                db.session.add(history)
            db.session.commit()
            check_price_alerts(product.id)

def cleanup_old_data(app):
    """Deletes price history older than 180 days (6 months) to free up DB space."""
    with app.app_context():
        try:
            cutoff = datetime.now(timezone.utc).date() - timedelta(days=180)
            deleted = PriceHistory.query.filter(PriceHistory.date < cutoff).delete()
            db.session.commit()
            print(f"🧹 Cleaned up {deleted} price history records older than 180 days.")
        except Exception as e:
            db.session.rollback()
            print(f"Cleanup error: {e}")

def check_price_alerts(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        return
    lowest_price = min((o.price for o in product.offers), default=None)
    if not lowest_price:
        return
    alerts = PriceAlert.query.filter_by(product_id=product_id, triggered=False).all()
    for alert in alerts:
        if lowest_price <= alert.target_price:
            alert.triggered = True
            send_alert_email(alert.user.email, product.name, lowest_price)
            db.session.commit()

scheduler = BackgroundScheduler()

def send_alert_email(user_email, product_name, price):
    mail = current_app.extensions.get('mail')
    msg = Message('Price Drop Alert!',
                  sender=current_app.config['MAIL_USERNAME'],
                  recipients=[user_email])
    msg.body = f"The price of {product_name} has dropped to ₹{price}. Check it out!"
    if mail:
        mail.send(msg)