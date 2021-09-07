#!/usr/bin/env python3

import json
import time
import itertools
from deuces import Card, evaluator
import os
from math import floor
import struct
import queue
from pymongo import MongoClient
import datetime
import requests
import threading
from cryptography.fernet import Fernet
import pika
from base64 import b64encode
import redis
import logging
import yaml
import importlib

logger = logging.getLogger()


def urand():
    return struct.unpack('I', os.urandom(4))[0] * 2 ** -32


def get_db():
    read_yaml = yaml.safe_load(open('server/conf.yml'))
    mongodb_user, mongodb_password = read_yaml['mongodb']['user'], read_yaml['mongodb']['password']
    pmc = MongoClient('mongodb://%s:%s@127.0.0.1' % (mongodb_user, mongodb_password))
    return pmc.bordeaux_poker_db


class Deck:
    def __init__(self):
        self.cards = []

        for i, r in enumerate('23456789TJQKA'):
            for j, color in enumerate('hdsc'):
                self.cards.append(Card.new(r + color))

    def pop(self):
        return self.cards.pop(0)

    def fisher_yates_shuffle_improved(self):
        amnt_to_shuffle = len(self.cards)
        while amnt_to_shuffle > 1:
            i = int(floor(urand() * amnt_to_shuffle))
            amnt_to_shuffle -= 1
            self.cards[i], self.cards[amnt_to_shuffle] = self.cards[amnt_to_shuffle], self.cards[i]

    def remove_card(self, card):
        self.cards.remove(card)


class Player:
    def __init__(self, game, nick, queue_id, key, chips):
        super().__init__()
        self.hand = None
        self.amount_bet = 0
        self.street_amount_bet = 0
        self.is_folded = False
        self.acted_street = False
        self.chips = chips
        if key:
            self.key = Fernet(key)
            self.disconnected = False
        else:
            self.key = None
            self.disconnected = True
        self.game = game
        self.nick = nick
        self.queue_id = queue_id
        self.disconnected = True
        self.action_queue = queue.Queue()

    def deal(self, deck):
        self.hand = [deck.pop(), deck.pop(), deck.pop()]

    def put_sb(self):
        amount_sb = min(5, self.chips)
        self.amount_bet += amount_sb
        self.street_amount_bet += amount_sb
        self.chips -= amount_sb

    def put_bb(self):
        amount_bb = min(10, self.chips)
        self.amount_bet += amount_bb
        self.street_amount_bet += amount_bb
        self.chips -= amount_bb

    def act(self, gamehand):
        if self.disconnected:
            timeout = 0
        else:
            timeout = gamehand.timeout
        try:
            action = self.action_queue.get(timeout=timeout).decode('utf-8').strip()
            self.disconnected = False
        except queue.Empty:
            action = 'f'
            self.disconnected = True

        if action.lower() == 'c':
            amount_called = min(gamehand.max_amount_bet - self.amount_bet, self.chips)
            self.amount_bet += amount_called
            self.street_amount_bet += amount_called
            self.chips -= amount_called
            gamehand.hand_document['actions'][gamehand.street_act].append({'code': 'C',
                                                                           'player': self.queue_id,
                                                                           'amount': amount_called})
            gamehand.last_action = 'check' if not amount_called else 'call'
        elif action.lower().startswith('r'):
            amount_raised = min(int(action.lower()[2:]), self.chips)
            self.amount_bet += amount_raised
            self.street_amount_bet += amount_raised
            self.chips -= amount_raised
            gamehand.hand_document['actions'][gamehand.street_act].append({'code': 'R',
                                                                           'player': self.queue_id,
                                                                           'amount': amount_raised})
            gamehand.last_action = 'raise'
        else:
            self.is_folded = True
            gamehand.hand_document['actions'][gamehand.street_act].append({'code': 'F',
                                                                           'player': self.queue_id})
            gamehand.last_action = 'fold'

    def read_queue(self, code, credentials):
        connection = pika.BlockingConnection(pika.ConnectionParameters('localhost', 5672, code,
                                                                       credentials=credentials))
        channel = connection.channel()
        channel.queue_declare('game' + '.' + self.queue_id, auto_delete=True)
        channel.queue_bind(exchange='poker_exchange',
                           queue='game.' + self.queue_id,
                           routing_key='game.' + self.queue_id)
        channel.basic_consume(queue='game.' + self.queue_id, on_message_callback=self.put_action, auto_ack=True)
        channel.start_consuming()

    def put_action(self, ch, method, properties, body):
        if body == b'reconnect':
            self.disconnected = False
            r = redis.Redis()
            key = r.get(f'session.{self.queue_id}.key')
            self.key = Fernet(key) if key else self.key
            self.game.repeat(self.queue_id)
            return
        self.action_queue.put(body)


class GameHand:
    def __init__(self, players, game, deck, mongo_db, tourney_id):
        self.players = players
        self.game = game
        self.flop1 = []
        self.flop2 = []
        self.turn1 = []
        self.turn2 = []
        self.river = []
        self.deck = deck
        self.max_amount_bet = 0
        self.prev_raise = 10
        self.min_raise = 15
        self.timeout = 30
        self.mongo_db = mongo_db.hands

        self.last_action = None
        self.hand_document = {'actions': {'blinds': [], 'preflop': [], 'flop': [], 'turn': [], 'river': []},
                              'winnings': {},
                              'hands': {},
                              'chips': {},
                              'board': [],
                              'tourney_id': tourney_id,
                              'date': datetime.datetime.utcnow()}
        self.street_act = 'blinds'
        for player in self.players:
            player.to_act = False
            self.hand_document['chips'][player.queue_id] = player.chips
            self.hand_document['winnings'][player.queue_id] = 0

    def _deal(self):
        self.last_action = None
        for p in list(self.players):
            if not p.chips and not p.amount_bet:
                self.players.remove(p)
        for p in self.players:
            p.acted_street = False
            p.street_amount_bet = 0

    def deal_preflop(self):
        self._deal()
        self.players.append(self.players.pop(0))
        if len(self.players) == 2:
            self.players.reverse()
        for i, p in enumerate(self.players):
            p.is_folded = False
            p.amount_bet = 0
            if i == 0:
                p.put_sb()
                self.hand_document['actions'][self.street_act].append(
                    {'code': 'SB', 'amount': p.amount_bet, 'player': p.queue_id})
            if i == 1:
                p.put_bb()
                self.hand_document['actions'][self.street_act].append(
                    {'code': 'BB', 'amount': p.amount_bet, 'player': p.queue_id})
            self.max_amount_bet = max(self.max_amount_bet, p.amount_bet)
            p.deal(self.deck)
            self.hand_document['hands'][p.queue_id] = ''.join(Card.int_to_str(c) for c in p.hand)

        players_actable = [p for p in self.players if p.chips and not p.is_folded]
        if len(players_actable) < 2:
            if len(self.players) == 2:
                self.players.reverse()
            return None
        i = 2
        while self.players[i % len(self.players)].chips == 0:
            i += 1
        self.street_act = 'preflop'
        to_act = self.players[i % len(self.players)]
        return to_act

    def act(self, player):
        player.act(self)
        if player.amount_bet > self.max_amount_bet and not self.flop1:
            self.min_raise, self.prev_raise = self.prev_raise + self.min_raise, self.min_raise

        self.max_amount_bet = max(self.max_amount_bet, player.amount_bet)
        player.acted_street = True
        index = self.players.index(player)
        for p in self.players[index + 1:] + self.players[:index]:
            if p.is_folded:
                continue
            if not p.chips:
                continue
            if p.amount_bet == self.max_amount_bet and p.acted_street:
                continue
            return p
        return None

    def calc_bet_pot(self):
        bet_amounts = []
        for p in self.players:
            if not p.chips and p.amount_bet < self.max_amount_bet:
                bet_amounts.append(p.amount_bet)
        bet_amounts.append(self.max_amount_bet)
        pot_amounts = [0] * len(bet_amounts)
        for player in self.players:
            prev_bet = 0
            for i, amount in enumerate(bet_amounts):
                amount = max(min(amount - prev_bet, player.amount_bet - prev_bet), 0)
                pot_amounts[i] += amount
                prev_bet = bet_amounts[i]
        return list(zip(bet_amounts, pot_amounts))

    def calc_prev_street_pot(self):
        return sum(p.amount_bet - p.street_amount_bet for p in self.players)

    def get_btn_player(self):
        if len(self.players) == 2 and not self.flop1:
            return self.players[0]
        return self.players[-1]

    def send_state(self, to_act, showdown=None):
        btn_player = self.get_btn_player()
        common = {
            'board': ''.join(
                Card.int_to_str(c) for c in self.flop1 + self.flop2 + self.turn1 + self.turn2 + self.river),
            'active': to_act.nick if to_act else None,
            'prev_pot': self.calc_prev_street_pot(),
            'pot': [bp[1] for bp in self.calc_bet_pot()],
            'players': [
                {'chips': p.chips,
                 'bet': p.street_amount_bet,
                 'name': p.nick,
                 'is_folded': p.is_folded,
                 'disconnected': p.disconnected,
                 'btn': p == btn_player} for p in self.players
            ],
            'last_action': self.last_action,
        }
        private = {}

        if showdown:
            common['winning_hand'] = ''.join(Card.int_to_str(c) for c in showdown)
            for i, player in enumerate(self.players):
                if not player.is_folded:
                    common['players'][i]['holes'] = ''.join(Card.int_to_str(c) for c in player.hand)
        else:
            for i, player in enumerate(self.players):
                if self.flop1:
                    min_raise = max(2 * (self.max_amount_bet - player.amount_bet), 10)
                else:  # preflop
                    min_raise = self.min_raise + self.max_amount_bet - player.amount_bet
                min_raise = min(min_raise, player.chips)
                to_call = min(self.max_amount_bet - player.amount_bet, player.chips)
                private[player.queue_id] = {'to_call': to_call,
                                            'min_raise': min_raise,
                                            'nl_raise': bool(self.flop1),
                                            'holes': ''.join(Card.int_to_str(c) for c in player.hand)
                                            }
        self.game.broadcast(common)
        for p_id, v in private.items():
            self.game.send_player(p_id, v)

    def deal_flop(self):
        if len(self.players) == 2:
            self.players.reverse()
        self.timeout = 60
        self._deal()
        self.flop1 = [self.deck.pop(), self.deck.pop()]
        self.flop2 = [self.deck.pop(), self.deck.pop()]
        self.hand_document['board'].append(''.join(Card.int_to_str(c) for c in self.flop1) + '/' +
                                           ''.join(Card.int_to_str(c) for c in self.flop2))
        players_actable = [p for p in self.players if p.chips and not p.is_folded]
        self.street_act = 'flop'
        return players_actable[0] if len(players_actable) > 1 else None

    def deal_turn(self):
        self._deal()
        self.turn1 = [self.deck.pop()]
        self.turn2 = [self.deck.pop()]
        self.hand_document['board'].append(''.join(Card.int_to_str(c) for c in self.turn1) + '/' +
                                           ''.join(Card.int_to_str(c) for c in self.turn2))
        players_actable = [p for p in self.players if p.chips and not p.is_folded]
        self.street_act = 'turn'
        return players_actable[0] if len(players_actable) > 1 else None

    def deal_river(self):
        self._deal()
        self.river = [self.deck.pop()]
        players_actable = [p for p in self.players if p.chips and not p.is_folded]
        self.hand_document['board'].append(''.join(Card.int_to_str(c) for c in self.river))
        self.street_act = 'river'
        return players_actable[0] if len(players_actable) > 1 else None

    def check_all_folded(self):
        if len([p for p in self.players if not p.is_folded]) == 1:
            winner = [p for p in self.players if not p.is_folded][0]
            for p in self.players:
                self.hand_document['winnings'][p.queue_id] -= p.amount_bet
            winner.chips += self.calc_bet_pot()[0][1]
            if len(self.players) == 2 and not self.flop1:
                self.players.reverse()
            self.hand_document['winnings'][winner.queue_id] += self.calc_bet_pot()[0][1]
            self.mongo_db.insert_one(self.hand_document)
            self.send_state(None)
            time.sleep(1)
            return True
        return False

    def showdown(self):
        ev = evaluator.Evaluator()

        for p in self.players:
            self.hand_document['winnings'][p.queue_id] -= p.amount_bet

        player_ranks = {}
        players_comb = {}
        for player in self.players:
            if player.is_folded:
                continue
            for comb in itertools.combinations(player.hand, 2):
                for flop in self.flop1, self.flop2:
                    for turn in self.turn1, self.turn2:
                        for bcomb in itertools.combinations(flop + turn + self.river, 3):
                            rank = ev.evaluate(cards=list(comb), board=list(bcomb))
                            if not player_ranks.get(player.queue_id) or rank < player_ranks[player.queue_id]:
                                player_ranks[player.queue_id] = rank
                                players_comb[player.queue_id] = list(comb) + list(bcomb)

        last_amount_bet = 0
        for i, (bet, pot) in enumerate(self.calc_bet_pot()):
            min_rank = None
            min_player = None
            for player in self.players:
                if player.is_folded or player.amount_bet < bet - last_amount_bet:
                    player.amount_bet = 0
                    continue
                if min_rank is None or player_ranks[player.queue_id] < min_rank:
                    min_rank = player_ranks[player.queue_id]
                    min_player = player
                player.amount_bet -= bet - last_amount_bet
            last_amount_bet = bet
            self.hand_document['winnings'][min_player.queue_id] += pot
            min_player.chips += pot
            self.send_state(None, showdown=players_comb[min_player.queue_id])
            time.sleep(3.5)

        self.mongo_db.insert_one(self.hand_document)


class Game:
    def __init__(self, players, code, credentials, game_config):
        r = redis.Redis()
        r.set(f'games.{code}.status', 'scheduled')
        self.credentials = credentials
        self.players = [Player(self,
                               p['login'],
                               str(p['_id']),
                               r.get(f'session.{p["_id"]}.key'),
                               int(game_config['start_chips']))
                        for p in players]
        self.connection = pika.BlockingConnection(pika.ConnectionParameters('localhost', 5672, code,
                                                                            credentials=credentials))
        self.game_config = game_config
        self.start_time = datetime.datetime.utcnow()
        self.code = code
        self.mongo_db = get_db()
        self.tourney_id = self.mongo_db.tourneys.insert_one({'placements': {},
                                                             'players': [p.queue_id for p in self.players],
                                                             'game': os.getpid(),
                                                             'date': self.start_time}).inserted_id
        self.last_msg_private = {}
        self.last_msg_public = None
        self.blind_augment = None
        self.channel = self.connection.channel()
        self.channel.exchange_declare(exchange='poker_exchange', exchange_type='topic')
        self.channel.queue_declare('public', auto_delete=True)
        self.channel.queue_bind(exchange='poker_exchange',
                                queue='public',
                                routing_key='public')
        for p in self.players:
            self.channel.queue_declare(f'public.{p.queue_id}')
            self.channel.queue_bind(exchange='poker_exchange',
                                    queue=f'public.{p.queue_id}',
                                    routing_key='public')
            rabbit_consumer = threading.Thread(target=p.read_queue, args=(code, credentials,))
            rabbit_consumer.start()

    def broadcast(self, msg):
        self.last_msg_public = msg
        self.channel.basic_publish(exchange='poker_exchange',
                                   routing_key='public',
                                   body=json.dumps(msg).encode('utf-8'))

    def send_player(self, p_id, msg):
        for p in self.players:
            if p.queue_id == p_id:
                self.last_msg_private[p.queue_id] = msg
                if not p.key:
                    return
                msg_encrypted = b64encode(p.key.encrypt(json.dumps(msg).encode('utf-8'))).decode('utf-8')
                msg_dict = {'private_to': p_id, 'data': msg_encrypted}
                self.channel.basic_publish(exchange='poker_exchange',
                                           routing_key='public',
                                           body=json.dumps(msg_dict).encode('utf-8'))

    def game_start_send(self, credentials):
        connection = pika.BlockingConnection(pika.ConnectionParameters('localhost', 5672, 'game_start',
                                                                       credentials=credentials))
        channel = connection.channel()
        channel.exchange_declare(exchange='public', exchange_type='fanout')
        channel.basic_publish(exchange='public',
                              routing_key='public',
                              body=json.dumps({'game': self.code,
                                               'players': [p.queue_id for p in self.players]}).encode('utf-8'))

    def repeat(self, p_id):
        if self.last_msg_public:
            self.broadcast(self.last_msg_public)
        if p_id in self.last_msg_private:
            self.send_player(p_id, self.last_msg_private[p_id])

    def connect_player(self, ch, method, properties, body):
        key_dict = json.loads(body.decode('utf-8'))
        for player in self.players:
            if player.queue_id == key_dict['id']:
                player.disconnected = False
                player.key = Fernet(key_dict['key'])

    def check_eliminated(self):
        eliminated_players = [p for p in self.players if p.chips <= 0]
        new_players = [p for p in self.players if p.chips > 0]
        place = len(new_players) + 1
        if len(new_players) < len(self.players):
            for p in eliminated_players:
                self.broadcast({'finished': p.nick,
                                'place': place})
                self.mongo_db.tourneys.update_one({'_id': self.tourney_id},
                                                  {'$set': {f'placements.{p.queue_id}': place}})
        self.players = new_players

    def run(self):
        nb_hands = 0
        start_time = datetime.datetime.utcfromtimestamp(self.game_config.get('start_time', 0))
        now = datetime.datetime.utcnow()
        while now < start_time:
            time.sleep(2)
            self.connection.process_data_events()
            now = datetime.datetime.utcnow()
        r = redis.Redis()
        r.set(f'games.{self.code}.status', 'sitting in')
        self.game_start_send(self.credentials)
        all_connect_timeout = self.game_config['all_connect_timeout']
        while [p for p in self.players if p.disconnected]:
            time.sleep(2)
            self.connection.process_data_events()
            all_connect_timeout -= 2
            if all_connect_timeout <= 0:
                return
        r.set(f'games.{self.code}.status', 'running')
        self.blind_augment = datetime.datetime.utcnow()
        while len(self.players) > 1:
            nb_hands += 1
            deck = Deck()
            deck.fisher_yates_shuffle_improved()
            now = datetime.datetime.utcnow()
            elapsed_time = (now - self.blind_augment).total_seconds()
            if elapsed_time > self.game_config['blind_timer']:
                self.blind_augment = self.blind_augment + datetime.timedelta(seconds=self.game_config['blind_timer'])
                avg_stack = sum(p.chips for p in self.players) / len(self.players)
                self.broadcast({'log': 'Tournament chips decrease.'})
                for player in self.players:
                    decrease = round(player.chips * self.game_config['blind_percent'] * 0.01 +
                                     self.game_config['skim_percent'] * 0.01 * avg_stack)
                    player.chips = player.chips - decrease
                    if not player.disconnected:
                        message = f'You were removed {decrease} chips'
                        self.send_player(player.queue_id, {'log': message})
            self.check_eliminated()
            hand = GameHand(self.players, self, deck, self.mongo_db, self.tourney_id)
            if len(self.players) <= 1:
                break
            to_act = hand.deal_preflop()
            next_hand = False
            while to_act:
                hand.send_state(to_act)
                to_act = hand.act(to_act)
                if hand.check_all_folded():
                    next_hand = True
                    break
            if next_hand:
                continue
            to_act = hand.deal_flop()
            if not to_act:
                hand.send_state(None)
                time.sleep(1)
            while to_act:
                hand.send_state(to_act)
                to_act = hand.act(to_act)
                if hand.check_all_folded():
                    next_hand = True
                    break
            if next_hand:
                continue
            to_act = hand.deal_turn()
            if not to_act:
                hand.send_state(None)
                time.sleep(1)
            while to_act:
                hand.send_state(to_act)
                to_act = hand.act(to_act)
                if hand.check_all_folded():
                    next_hand = True
                    break
            if next_hand:
                continue
            to_act = hand.deal_river()
            if not to_act:
                hand.send_state(None)
                time.sleep(1)
            while to_act:
                hand.send_state(to_act)
                to_act = hand.act(to_act)
                if hand.check_all_folded():
                    next_hand = True
                    break
            if next_hand:
                continue
            hand.showdown()
            time.sleep(1)
            self.check_eliminated()
        if self.players:
            self.send_player(self.players[0].queue_id, {'log': 'You won the tournament!'})


class SeatingListener:
    def __init__(self, game_config, players):
        self.game_config = game_config
        self.code = str(os.getpid())

        handler = logging.FileHandler('/var/log/poker/game.log')
        formatter = logging.Formatter('[pid %(process)d] - %(message)s')
        handler.setFormatter(formatter)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        rabbitmq_admin_password = os.getenv('RABBITMQ_ADMIN_PASSWORD', 'unsafe_for_production')
        requests.put(f'http://localhost:15672/api/vhosts/{self.code}',
                     auth=('admin', rabbitmq_admin_password))
        requests.put(f'http://localhost:15672/api/permissions/{self.code}/admin',
                     auth=('admin', rabbitmq_admin_password),
                     data=json.dumps({"configure": ".*", "write": '.*', "read": '.*'}))

        for p in players:
            requests.put(f'http://localhost:15672/api/permissions/{self.code}/{p["_id"]}',
                         auth=('admin', rabbitmq_admin_password),
                         data=json.dumps({"configure": "$^",
                                          "write": f'poker_exchange',
                                          "read": f'public.{p["_id"]}'}))
            requests.put(f'http://localhost:15672/api/topic-permissions/{self.code}/{p["_id"]}',
                         auth=('admin', rabbitmq_admin_password),
                         data=json.dumps({"exchange": "poker_exchange",
                                          "write": f'game.{p["_id"]}',
                                          "read": 'public'}))

        credentials = pika.PlainCredentials('admin', rabbitmq_admin_password)

        try:
            g = Game(players, self.code, credentials, self.game_config)
            g.run()
        finally:
            r = redis.Redis()
            r.hdel('games.start', self.code)
            if 'plugin' in self.game_config:
                m = importlib.import_module('plugins.' + self.game_config['plugin'])
                m.post_game_hook(self.code)
            requests.delete(f'http://localhost:15672/api/vhosts/{self.code}', auth=('admin', rabbitmq_admin_password))
