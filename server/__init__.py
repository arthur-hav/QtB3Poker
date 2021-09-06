from flask import Flask, request
from functools import wraps
import requests
import jwt
import json
import datetime
from passlib.hash import sha256_crypt
import os
from pymongo import MongoClient
from bson.objectid import ObjectId
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


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            auth_token = request.headers.get('Authorization')
            payload = jwt.decode(auth_token, os.getenv('JWT_TOKEN', 'unsafe_for_production'))
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
            return {'error': f'Missing or invalid authentication token: {str(e)}'}
        db = get_db()
        admin_user = db.users.find_one({'_id': ObjectId(payload['sub'])})
        if not admin_user or 'admin' not in admin_user:
            return {'status': 'fail', 'reason': 'Require privileged user'}
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


def update_send(queue_id):
    rabbitmq_admin_password = os.getenv('RABBITMQ_ADMIN_PASSWORD', 'unsafe_for_production')
    credentials = pika.PlainCredentials('admin', rabbitmq_admin_password)
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost', 5672, 'game_start',
                                                                   credentials=credentials))
    r = redis.Redis()
    players = [p.decode('utf-8') for p in r.sscan_iter(f'queue.{queue_id}.players')]
    channel = connection.channel()
    channel.exchange_declare(exchange='public', exchange_type='fanout')
    channel.basic_publish(exchange='public',
                          routing_key='public',
                          body=json.dumps({'queue': queue_id, 'players': players}).encode('utf-8'))


def logout_player():
    pass


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
        # r.sadd(f'session.keys', str(user['_id']))  ## Might be useful for later
        r.set(f'session.{user["_id"]}.key', key)
        r.expire(f'session.{user["_id"]}.key', 60 * 60 * 24)  # One day key storing
        return {'status': 'success',
                'token': get_token(str(user['_id'])).decode('utf-8'),
                'key': key.decode('utf-8'),
                'id': str(user['_id']),
                'games': [g['game'] for g in games if psutil.pid_exists(g['game'])]}
    return {'status': 'fail', 'reason': 'Bad username or password'}


@app.route("/create_game", methods=['POST'])
@admin_required
def create_game(user_id):
    db = get_db()
    game_config = json.loads(request.form['server_config'])
    players_login = json.loads(request.form['players'])
    users = list(db.users.find({'login': {'$in': players_login}}))
    r = redis.Redis()
    p = mp.Process(target=server.SeatingListener, args=(game_config, users))
    p.start()
    r.hset('games.start', p.pid, datetime.datetime.utcnow().timestamp())
    r.lpush(f'games.{p.pid}.players', *[str(u['_id']) for u in users])
    return {'status': 'success', 'server_id': p.pid}


@app.route("/list_games")
@token_required
def list_games(user_id):
    r = redis.Redis()
    to_del = []
    games = []
    game_data = {}
    queue_data = {}
    for k, v in r.hscan_iter('games.start'):
        if datetime.datetime.utcnow().timestamp() - float(v.decode('utf-8')) > 60 * 60 * 24:
            to_del.append(k)
        else:
            games.append(k.decode('utf-8'))
    for queue_bytes in r.sscan_iter('queue.keys'):
        queue_id = queue_bytes.decode('utf-8')
        nb_seats = int(r.hget(f'queue.{queue_id}.config', b'nb_seats'))
        queue_data[queue_id] = {'players': [player.decode('utf-8')
                                            for player in r.sscan_iter(f'queue.{queue_id}.players')],
                                'seats': nb_seats}
    if to_del:
        r.hdel('games.start', *to_del)
        r.delete(*[f'games.{key}.players' for key in to_del])
    for game in games:
        game_data[game] = [p.decode('utf-8') for p in r.sscan_iter(f'games.{game}.players')]
    return {'status': 'success', 'games': game_data, 'queues': queue_data}


@app.route("/spectate/<game>", methods=["POST"])
@token_required
def spectate(user_id, game):
    rabbitmq_admin_password = os.getenv('RABBITMQ_ADMIN_PASSWORD', 'unsafe_for_production')
    requests.put(f'http://localhost:15672/api/permissions/{game}/{user_id}',
                 auth=('admin', rabbitmq_admin_password),
                 data=json.dumps({"configure": "$^",
                                  "write": '$^',
                                  "read": f'public.{user_id}'}))
    credentials = pika.PlainCredentials('admin', rabbitmq_admin_password)
    conn = pika.BlockingConnection(pika.ConnectionParameters('localhost',
                                                             5672,
                                                             str(game),
                                                             credentials=credentials))
    channel = conn.channel()
    channel.queue_declare(f'public.{user_id}')
    channel.queue_bind(exchange='poker_exchange',
                       queue=f'public.{user_id}',
                       routing_key='public')

    return {'status': 'success'}


@app.route('/queue/<queue_key>')
@token_required
def queue(user_id, queue_key):
    update_send(queue_key)
    r = redis.Redis()
    if r.sismember(f'queue.{queue_key}.players', user_id):
        r.srem(f'queue.{queue_key}.players', user_id)
        return {'status': 'success'}
    r.sadd(f'queue.{queue_key}.players', user_id)
    nb_seats = int(r.hget(f'queue.{queue_key}.config', b'nb_seats'))
    user_list = list(r.sscan_iter(f'queue.{queue_key}.players'))
    if len(user_list) >= nb_seats:
        db = get_db()
        users = list(db.users.find({'_id': {'$in': [ObjectId(u.decode('utf-8')) for u in user_list]}}))
        game_config = dict((k.decode('utf-8'), float(v.decode('utf-8')))
                           for k, v in r.hscan_iter(f'queue.{queue_key}.config'))

        p = mp.Process(target=server.SeatingListener, args=(game_config, users))
        p.start()
        r.hset('games.start', p.pid, datetime.datetime.utcnow().timestamp())
        r.sadd(f'games.{p.pid}.players', *user_list)
        r.delete(f'queue.{queue_key}.players')
    return {'status': 'success'}


@app.route('/create_queue/<queue_key>', methods=["POST"])
@admin_required
def create_queue(user_id, queue_key):
    game_config = json.loads(request.form['server_config'])
    r = redis.Redis()
    r.sadd('queue.keys', queue_key)
    r.hset(f'queue.{queue_key}.config', mapping=game_config)
    return {'status': 'success'}


## Work in progress endpoints

@app.route('/transfer_currency/<currency>', methods=["POST"])
@admin_required
def transfer_currency(user_id, currency):
    ledger = json.loads(request.form['data'])
    updated_players = []
    for player, value in ledger.items():
        player = db.users.find_one({'login': player})
        if player.get(currency, 0) + value < 0:
            continue
        db.users.update_one({'login': player}, {currency: {'$set': player.get(currency, 0) + value}})
        updated_players.append(str(player['_id']))
    return {'status': 'success', 'updated_players': updated_players}


@app.route('/get_balance/<currency>')
@token_required
def get_balance(user_id, currency):
    db = get_db()
    user = db.users.find_one({'_id': ObjectId(user_id)})
    if user:
        return {'status': 'success', 'balance': user.get(currency, 0)}