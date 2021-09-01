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


def urand():
    return struct.unpack('I', os.urandom(4))[0] * 2 ** -32


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
    def __init__(self):
        self.hand = None
        self.amount_bet = 0
        self.street_amount_bet = 0
        self.is_folded = False
        self.acted_street = False
        self.chips = 0
        self.key = None

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
        if gamehand.max_amount_bet == self.amount_bet:
            return
        self.amount_bet = gamehand.max_amount_bet


class Human(Player):
    def __init__(self, nick, queue_id):
        super().__init__()
        self.nick = nick
        self.queue_id = queue_id
        self.disconnected = True
        self.action_queue = queue.Queue()

    def act(self, gamehand):

        if self.disconnected:
            action = 'f'
        else:
            try:
                action = self.action_queue.get(timeout=gamehand.timeout).decode('utf-8').strip()
            except queue.Empty:
                action = 'f'
                # self.disconnected = True

        if action.lower() == 'c':
            amount_called = min(gamehand.max_amount_bet - self.amount_bet, self.chips)
            self.amount_bet = gamehand.max_amount_bet
            self.street_amount_bet += amount_called
            self.chips -= amount_called
            gamehand.hand_document['actions'][gamehand.street_act].append({'code': 'C',
                                                                'player': self.nick,
                                                                'amount': amount_called})
            gamehand.last_action = 'check' if not amount_called else 'call'
        elif action.lower().startswith('r'):
            amount_raised = min(int(action.lower()[2:]), self.chips)
            self.amount_bet += amount_raised
            self.street_amount_bet += amount_raised
            self.chips -= amount_raised
            gamehand.hand_document['actions'][gamehand.street_act].append({'code': 'R',
                                                                'player': self.nick,
                                                                'amount': amount_raised})
            gamehand.last_action = 'raise'
        else:
            self.is_folded = True
            gamehand.hand_document['actions'][gamehand.street_act].append({'code': 'F',
                                                                'player': self.nick})
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
            self.hand_document['chips'][player.nick] = player.chips
            self.hand_document['winnings'][player.nick] = 0

    def _deal(self):
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
                self.hand_document['actions'][self.street_act].append({'code': 'SB', 'amount': p.amount_bet, 'player': p.nick})
            if i == 1:
                p.put_bb()
                self.hand_document['actions'][self.street_act].append({'code': 'BB', 'amount': p.amount_bet, 'player': p.nick})
            self.max_amount_bet = max(self.max_amount_bet, p.amount_bet)
            p.deal(self.deck)
            self.hand_document['hands'][p.nick] = ''.join(Card.int_to_str(c) for c in p.hand)

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

    def calc_pot(self):
        min_amount_bet_allin = 0
        for p in self.players:
            if not p.chips and p.amount_bet < self.max_amount_bet:
                min_amount_bet_allin = p.amount_bet
        if min_amount_bet_allin:
            pot = [0, 0]
            for p in self.players:
                pot[0] += min(p.amount_bet, min_amount_bet_allin)
                pot[1] += max(0, p.amount_bet - min_amount_bet_allin)
        else:
            pot = [0]
            for p in self.players:
                pot[0] += p.amount_bet
        return pot

    def calc_prev_street_pot(self):
        return sum(p.amount_bet - p.street_amount_bet for p in self.players)

    def get_btn_player(self):
        if len(self.players) == 2 and not self.flop1:
            return self.players[0]
        return self.players[-1]

    def send_state(self, to_act, showdown=None):
        btn_player = self.get_btn_player()
        common = {
            'board': ''.join(Card.int_to_str(c) for c in self.flop1+self.flop2+self.turn1+self.turn2+self.river),
            'active': to_act.nick if to_act else None,
            'prev_pot': self.calc_prev_street_pot(),
            'pot': self.calc_pot(),
            'players': [
                {'chips': p.chips,
                 'bet': p.street_amount_bet,
                 'name': p.nick,
                 'is_folded': p.is_folded,
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
                else:   # preflop
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
                self.hand_document['winnings'][p.nick] -= p.amount_bet
            winner.chips += self.calc_pot()[0]
            if len(self.players) == 2 and not self.flop1:
                self.players.reverse()
            self.hand_document['winnings'][winner.nick] += self.calc_pot()[0]
            self.mongo_db.insert_one(self.hand_document)
            self.send_state(None)
            time.sleep(1)
            return True
        return False

    def showdown(self):
        ev = evaluator.Evaluator()
        min_comb = None
        min_player = None
        min_rank = None

        for p in self.players:
            self.hand_document['winnings'][p.nick] -= p.amount_bet

        for i, pot in enumerate(self.calc_pot()):
            for player in self.players:
                if player.is_folded:
                    continue
                if i > 0 and player.amount_bet < self.max_amount_bet:
                    continue
                for comb in itertools.combinations(player.hand, 2):
                    for flop in self.flop1, self.flop2:
                        for turn in self.turn1, self.turn2:
                            for bcomb in itertools.combinations(flop + turn + self.river, 3):
                                rank = ev.evaluate(cards=list(comb), board=list(bcomb))
                                if min_rank is None or min_rank > rank:
                                    min_comb = list(comb) + list(bcomb)
                                    min_rank = rank
                                    min_player = player
            self.hand_document['winnings'][min_player.nick] += pot
            min_player.chips += pot

        self.send_state(None, showdown=min_comb)
        self.mongo_db.insert_one(self.hand_document)
        return min_player, min_comb


class Game:
    def __init__(self, players, code, credentials, server_config):
        self.connection = pika.BlockingConnection(pika.ConnectionParameters('localhost', 5672, code,
                                                                            credentials=credentials))
        self.server_config = server_config
        self.start_time = datetime.datetime.utcnow()
        self.players = players
        self.code = code
        self.mongo_conn = MongoClient()
        self.mongo_db = self.mongo_conn.bordeaux_poker_db
        self.tourney_id = self.mongo_db.tourneys.insert_one({'placements': {},
                                                             'players': [p.queue_id for p in players],
                                                             'game': os.getpid(),
                                                             'date': self.start_time}).inserted_id
        self.last_msg_private = {}
        self.last_msg_public = None
        self.blind_augment = self.start_time
        self.channel = self.connection.channel()
        self.channel.exchange_declare(exchange='poker_exchange', exchange_type='topic')
        self.channel.queue_declare('public', auto_delete=True)
        self.channel.queue_bind(exchange='poker_exchange',
                           queue='public',
                           routing_key='public')
        rabbit_consumer = threading.Thread(target=self.read_keys)
        rabbit_consumer.start()
        for p in players:
            p.chips = server_config['start_chips']

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
                if not p.key:
                    return
                self.last_msg_private[p.queue_id] = msg
                self.channel.basic_publish(exchange='poker_exchange',
                                           routing_key='public',
                                           body=json.dumps({'private_to': p_id,
                                                            'data': b64encode(p.key.encrypt(json.dumps(msg).encode('utf-8'))).decode('utf-8')}))

    def read_keys(self):
        rabbitmq_admin_password = os.getenv('RABBITMQ_ADMIN_PASSWORD', 'unsafe_for_production')
        credentials = pika.PlainCredentials('admin', rabbitmq_admin_password)
        connection = pika.BlockingConnection(pika.ConnectionParameters('localhost', credentials=credentials))
        channel = connection.channel()
        channel.queue_declare('keys-' + self.code, exclusive=True, auto_delete=True)
        channel.exchange_declare(exchange='poker_exchange', exchange_type='topic')
        channel.queue_bind(exchange='poker_exchange',
                           queue='keys-' + self.code,
                           routing_key='keys')
        channel.basic_consume(queue='keys-' + self.code,
                              on_message_callback=self.connect_player,
                              auto_ack=True)
        channel.start_consuming()

    def repeat(self, p_id):
        self.broadcast(self.last_msg_public)
        self.send_player(p_id, self.last_msg_private[p_id])

    def connect_player(self, ch, method, properties, body):
        key_dict = json.loads(body.decode('utf-8'))
        for player in self.players:
            if player.queue_id == key_dict['id']:
                player.disconnected = False
                player.key = Fernet(key_dict['key'])
                for i in range(3):
                    time.sleep(1)
                    self.repeat(player)

    def check_eliminated(self):
        eliminated_players = [p for p in self.players if p.chips <= 0]
        new_players = [p for p in self.players if p.chips > 0]
        place = len(new_players) + 1
        if len(new_players) < len(self.players):
            for p in eliminated_players:
                self.broadcast({'finished': p.nick,
                                'place': place})
                self.mongo_db.tourneys.update_one({'_id': self.tourney_id},
                                                  {'$set': {f'placements.{p.nick}': place}})
        self.players = new_players

    def run(self):
        nb_hands = 0

        while len(self.players) > 1:
            nb_hands += 1
            deck = Deck()
            deck.fisher_yates_shuffle_improved()
            now = datetime.datetime.utcnow()
            elapsed_time = (now - self.blind_augment).total_seconds()
            if elapsed_time > self.server_config['blind_timer']:
                self.blind_augment = self.blind_augment + datetime.timedelta(seconds=self.server_config['blind_timer'])
                avg_stack = sum(p.chips for p in self.players) / len(self.players)
                self.broadcast({'log': 'Tournament chips decrease.'})
                for player in self.players:
                    decrease = round(player.chips * self.server_config['blind_percent'] * 0.01 +
                                     self.server_config['skim_percent'] * 0.01 * avg_stack)
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
            time.sleep(5)
            self.check_eliminated()


class SeatingListener:
    def __init__(self, game_config, players):
        self.game_config = game_config
        self.code = str(os.getpid())
        rabbitmq_admin_password = os.getenv('RABBITMQ_ADMIN_PASSWORD', 'unsafe_for_production')
        requests.put(f'http://localhost:15672/api/vhosts/{self.code}',
                     auth=('admin', rabbitmq_admin_password))
        requests.put(f'http://localhost:15672/api/permissions/{self.code}/admin',
                     auth=('admin', rabbitmq_admin_password),
                     data=json.dumps({"configure": ".*", "write": '.*', "read": '.*'}))

        for p in players:
            requests.put(f'http://localhost:15672/api/permissions/{self.code}/{p["_id"]}',
                         auth=('admin', rabbitmq_admin_password),
                         data=json.dumps({"configure": ".*", "write": '.*', "read": '.*'}))
            requests.put(f'http://localhost:15672/api/topic-permissions/{self.code}/{p["_id"]}',
                         auth=('admin', rabbitmq_admin_password),
                         data=json.dumps({"exchange": "poker_exchange",
                                          "write": f'game.{p["_id"]}',
                                          "read": 'public'}))

        credentials = pika.PlainCredentials('admin', rabbitmq_admin_password)

        self.players = [Human(p['login'], str(p['_id'])) for p in players]
        try:
            g = Game(self.players, self.code, credentials, self.game_config)
            g.run()
        except Exception:

        finally:
            requests.delete(f'http://localhost:15672/api/vhosts/{self.code}', auth=('admin', rabbitmq_admin_password))
