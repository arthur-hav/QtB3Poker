from flask import Flask, request
from functools import wraps
import requests
import jwt
import json
import datetime
from passlib.hash import sha256_crypt
import os
from pymongo import MongoClient
import multiprocessing as mp
from .server import SeatingListener
from cryptography.fernet import Fernet
import psutil
import pika
import redis
import yaml


def token_required(f):
    @wraps(f)
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


def get_db():
    read_yaml = yaml.safe_load(open('server/conf.yml'))
    mongodb_user, mongodb_password = read_yaml['mongodb']['user'], read_yaml['mongodb']['password']
    pmc = MongoClient('mongodb://%s:%s@127.0.0.1' % (mongodb_user, mongodb_password))
    return pmc.bordeaux_poker_db


def rand_key():
    return Fernet.generate_key()


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
    db = get_db()
    user = request.form['user']
    if db.users.find_one({'login': user}):
        return {'status': 'fail', 'reason': 'User already exists. Please login'}
    if len(request.form['password']) < 8:
        return {'status': 'fail', 'reason': 'Password must be at least 8 char long'}
    pass_hash = sha256_crypt.hash(request.form['password'])
    user_id = str(db.users.insert_one({'login': user, 'pass_hash': pass_hash}).inserted_id)
    rabbitmq_admin_password = os.getenv('RABBITMQ_ADMIN_PASSWORD', 'unsafe_for_production')
    requests.put(f'http://localhost:15672/api/users/{user_id}',
                 auth=('admin', rabbitmq_admin_password),
                 data=json.dumps({"password": request.form['password'], "tags": ""}))
    requests.put(f'http://localhost:15672/api/permissions/game_start/{user_id}',
                 auth=('admin', rabbitmq_admin_password),
                 data=json.dumps({"configure": f"$^", "write": "$^",
                                  "read": f"public.{user_id}"}))
    return {'status': 'success', 'user_id': user_id}


@app.route('/login', methods=['POST'])
def login():
    user = request.form['user']
    db = get_db()
    user = db.users.find_one({'login': user})
    key = rand_key()
    if sha256_crypt.verify(request.form['password'], user['pass_hash']):
        rabbitmq_admin_password = os.getenv('RABBITMQ_ADMIN_PASSWORD', 'unsafe_for_production')
        credentials = pika.PlainCredentials('admin', rabbitmq_admin_password)
        conn = pika.BlockingConnection(pika.ConnectionParameters('localhost',
                                                                 5672,
                                                                 'game_start',
                                                                 credentials=credentials))
        channel = conn.channel()
        channel.queue_declare(queue=f'public.{str(user["_id"])}')
        channel.queue_bind(queue=f'public.{str(user["_id"])}', exchange='public')

        games = list(db.tourneys.find({'players': {'$in': [str(user['_id'])]}, 'game': {'$exists': True}}))
        r = redis.Redis()
        r.set(f'session.{user["_id"]}.key', key)
        r.expire(f'session.{user["_id"]}.key', 60*60*24)  # One day key storing
        return {'status': 'success',
                'token': get_token(user['_id']).decode('utf-8'),
                'key': key.decode('utf-8'),
                'id': str(user['_id']),
                'games': [g['game'] for g in games if psutil.pid_exists(g['game'])]}
    return {'status': 'fail', 'reason': 'Bad username or password'}


@app.route("/create_game", methods=['POST'])
@token_required
def create_game(user_id):
    db = get_db()
    game_config = json.loads(request.form['server_config'])
    players_login = json.loads(request.form['players'])
    users = list(db.users.find({'login': {'$in': players_login}}))
    r = redis.Redis()
    p = mp.Process(target=server.SeatingListener, args=(game_config, users))
    p.start()
    r.hset('games.start', p.pid, datetime.datetime.utcnow().timestamp())
    return {'status': 'success', 'server_id': p.pid}


@app.route("/list_games")
@token_required
def list_games(user_id):
    r = redis.Redis()
    to_del = []
    to_return = []
    for k, v in r.hscan_iter('games.start'):
        if datetime.datetime.utcnow().timestamp() - float(v.decode('utf-8')) > 60*60*24:
            to_del.append(k)
        else:
            to_return.append(k)
    if to_del:
        r.hdel('games.start', *to_del)
    return {'status': 'success', 'games': [g.decode('utf-8') for g in to_return]}