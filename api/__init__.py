from functools import wraps
from flask import Flask, g, request
import requests
import jwt
import json
import datetime
import os


def token_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            auth_token = request.authorization
            payload = jwt.decode(auth_token, os.getenv('SECRET_KEY', 'unsafe_for_production'))
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return {'error': 'Missing or invalid authentication token'}
        return f(*args, user=payload['sub'], **kwargs)
    return decorated_function


app = Flask(__name__)


@app.route("/get_token", methods=["GET"])
def get_token():
    secret_key = os.getenv('SECRET_KEY', 'unsafe_for_production')

    payload = {
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=2),
        'sub': user_id
    }
    return jwt.encode(
        payload,
        secret_key,
        algorithm='HS256'
    )

@app.route('/')
def login():
    return request.authorization

@token_required
@app.route("/create_game")
def create_game():
    requests.put()