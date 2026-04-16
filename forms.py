from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, FloatField, SelectField, BooleanField, FileField, IntegerField, SelectMultipleField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional, NumberRange

class LoginForm(FlaskForm):
    username = StringField('Username or Email', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')

class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    is_admin = BooleanField('Register as Admin (Access Analytics)', validators=[Optional()])

class OTPForm(FlaskForm):
    otp = StringField('Enter OTP', validators=[DataRequired(), Length(min=6, max=6)])

class AlertForm(FlaskForm):
    product_id = SelectField('Product', coerce=int, validators=[DataRequired()])
    target_price = FloatField('Target Price (₹)', validators=[DataRequired()])

class SearchForm(FlaskForm):
    query = StringField('Product name or URL', validators=[Optional()])
    # country field removed

class FilterForm(FlaskForm):
    min_price = FloatField('Min Price', validators=[Optional(), NumberRange(min=0)])
    max_price = FloatField('Max Price', validators=[Optional(), NumberRange(min=0)])
    brands = SelectMultipleField('Brands', coerce=str, validators=[Optional()])
    sellers = SelectMultipleField('Sellers', coerce=str, validators=[Optional()])
    min_rating = SelectField('Min Rating', choices=[(0, 'Any'), (1, '1★'), (2, '2★'), (3, '3★'), (4, '4★')], coerce=float, validators=[Optional()])
    in_stock_only = BooleanField('In Stock Only')
    sort_by = SelectField('Sort By', choices=[
        ('relevance', 'Relevance'),
        ('price_low', 'Price: Low to High'),
        ('price_high', 'Price: High to Low'),
        ('rating', 'Rating'),
        ('popularity', 'Popularity')
    ], validators=[Optional()])

class AdminSettingsForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=20)])
    phone = StringField('Mobile Number', validators=[Optional(), Length(max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    new_password = PasswordField('New Password', validators=[Optional(), Length(min=6, message='Password must be at least 6 characters long')])
    confirm_password = PasswordField('Confirm New Password', validators=[Optional(), EqualTo('new_password', message='Passwords must match')])
    submit = SubmitField('Update Settings')

class AddressForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(max=100)])
    phone = StringField('Mobile Number', validators=[DataRequired(), Length(min=10, max=15)])
    street = StringField('Street Address', validators=[DataRequired()])
    city = StringField('City', validators=[DataRequired(), Length(max=100)])
    state = StringField('State', validators=[DataRequired(), Length(max=100)])
    pincode = StringField('Pincode', validators=[DataRequired(), Length(min=6, max=6)])
    landmark = StringField('Landmark', validators=[Optional(), Length(max=200)])
    is_default = BooleanField('Set as Default')
    submit = SubmitField('Save Address')