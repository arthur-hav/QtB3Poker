from flask import Flask, request
import requests
import jwt
import json
import datetime
from passlib.hash import sha256_crypt
import os
from pymongo import MongoClient
import multiprocessing as mp
from .server import SeatingListener


def token_required(f):
    def decorated_function(*args, **kwargs):
        try:
            auth_token = request.headers.get('Authorization')
            payload = jwt.decode(auth_token, os.getenv('JWT_TOKEN', 'unsafe_for_production'))
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
            return {'error': f'Missing or invalid authentication token: {str(e)}'}
        return f(payload['sub'], *args, **kwargs)
    return decorated_function


if __name__ == '__main__':
    mp.set_start_method('spawn')
app = Flask(__name__)


@app.route("/get_token", methods=["GET"])
def get_token(user_id):
    secret_key = os.getenv('JWT_TOKEN', 'unsafe_for_production')

    payload = {
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=2),
        'sub': str(user_id)
    }
    return jwt.encode(
        payload,
        secret_key,
        algorithm='HS256'
    )


@app.route('/register', methods=['POST'])
def register():
    pmc = MongoClient()
    db = pmc.bordeaux_poker_db
    user = request.form['user']
    if db.users.find_one({'login': user}):
        return {'status': 'fail', 'reason': 'User already exists. Please login'}
    pass_hash = sha256_crypt.hash(request.form['password'])
    user_id = str(db.users.insert_one({'login': user, 'pass_hash': pass_hash}).inserted_id)
    rabbitmq_admin_password = os.getenv('RABBITMQ_ADMIN_PASSWORD', 'unsafe_for_production')
    requests.put(f'http://localhost:15672/api/users/{user_id}',
                 auth=('admin', rabbitmq_admin_password),
                 data={"password": request.form['password'], "tags": ""})
    return {'status': 'success', 'data': user_id}


@app.route('/login', methods=['POST'])
def login():
    user = request.form['user']
    pmc = MongoClient()
    db = pmc.bordeaux_poker_db
    user = db.users.find_one({'login': user})
    if sha256_crypt.verify(request.form['password'], user['pass_hash']):
        return {'status': 'success', 'data': get_token(user['_id']).decode('utf-8')}
    return {'status': 'fail', 'reason': 'Bad username or password'}


@app.route("/create_game", methods=['POST'])
@token_required
def create_game(user_id):
    game_config = json.loads(request.form['server_config'])
    players_login = json.loads(request.form['players'])
    pmc = MongoClient()
    db = pmc.bordeaux_poker_db
    users = list(db.users.find({'login': {'$in': players_login}}))
    #p = mp.Process(target=server.SeatingListener, args=(game_config, users))
    #p.start()
    server.SeatingListener(game_config, users)
    return {'status': 'success', 'code': p.pid}