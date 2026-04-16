import re
import requests
from urllib.parse import urlparse
import base64
import os
from werkzeug.utils import secure_filename

def is_url(string):
    """Check if string is a valid URL."""
    if not string or len(string.strip()) == 0:
        return False
    if not string.startswith('http'):
        return False
    try:
        result = urlparse(string)
        return all([result.scheme, result.netloc])
    except:
        return False

def extract_price(price_str):
    """Extract numeric price from string like ₹49,999."""
    match = re.search(r'[\d,]+', price_str.replace('₹', ''))
    if match:
        return float(match.group().replace(',', ''))
    return None