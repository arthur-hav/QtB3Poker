#!/usr/bin/env python3

import socket
import json
import time
import itertools
from deuces import Card, evaluator
import os
from math import floor
import struct
from copy import deepcopy
import threading as mp
import queue
import select
from pymongo import MongoClient
import datetime
import re
import ssl
from collections import defaultdict
import yaml


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

    def send_message(self, message):
        pass


class Human(Player):
    def __init__(self, nick, conn):
        super().__init__()
        self.nick = nick
        self.conn = conn
        self.disconnected = False
        self.action_queue = queue.Queue()

    def listen(self):
        while True:
            ready = select.select([self.conn], [], [], 1)
            if ready[0]:
                try:
                    action = self.conn.recv(4096).decode('utf-8').strip()
                    if action == '':
                        self.action_queue.put('f')
                        self.disconnected = True
                    else:
                        self.action_queue.put(action)
                except (ConnectionError, OSError, ssl.SSLError):
                    self.action_queue.put('f')
                    self.disconnected = True

    def act(self, gamehand):
        if self.disconnected:
            action = 'f'
        else:
            try:
                action = self.action_queue.get(True, gamehand.timeout)
            except queue.Empty:
                action = 'f'

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

    def send_message(self, json_dict):
        try:
            self.conn.send(json.dumps(json_dict).encode("utf-8") + '\n', encoding='utf-8')
        except (OSError, ConnectionError, ssl.SSLError):
            self.disconnected = True


class GameHand:
    def __init__(self, players, observers, deck, mongo_db, tourney_id):
        self.players = players
        self.observers = observers
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

        if showdown:
            common['winning_hand'] = ''.join(Card.int_to_str(c) for c in showdown)
            for i, player in enumerate(self.players):
                if not player.is_folded:
                    common['players'][i]['holes'] = ''.join(Card.int_to_str(c) for c in player.hand)
            for player in self.players:
                player.send_state(common)
            for obs in self.observers:
                obs.send_state(common)
        else:
            for i, player in enumerate(self.players):
                full_state = deepcopy(common)
                if self.flop1:
                    min_raise = max(2 * (self.max_amount_bet - player.amount_bet), 10)
                else:   # preflop
                    min_raise = self.min_raise + self.max_amount_bet - player.amount_bet
                min_raise = min(min_raise, player.chips)
                to_call = min(self.max_amount_bet - player.amount_bet, player.chips)
                full_state['players'][i]['holes'] = ''.join(Card.int_to_str(c) for c in player.hand)
                full_state.update({'to_call': to_call,
                                   'min_raise': min_raise,
                                   'nl_raise': bool(self.flop1)
                                   })
                player.send_state(full_state)
            for obs in self.observers:
                obs.send_state(common)

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
                print('rev back!')
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
    def __init__(self, players, server_config):
        self.server_config = server_config
        start = {'start': True, 'players': [p.nick for p in players]}
        for p in players:
            p.chips = server_config['start_chips']
            p.send_message(start)

        self.start_time = datetime.datetime.utcnow()
        self.players = players
        self.observers = []
        self.mongo_conn = MongoClient()
        self.mongo_db = self.mongo_conn.bordeaux_poker_db
        self.tourney_id = self.mongo_db.tourneys.insert_one({'placements': {},
                                                             'date': self.start_time}).inserted_id
        self.blind_augment = self.start_time

    def check_eliminated(self):
        new_players = [p for p in self.players if p.chips > 0]
        place = len(new_players) + 1
        if len(new_players) < len(self.players):
            for p in self.players:
                if p not in new_players:
                    if not p.disconnected:
                        p.send_message({'finished': place})
                    self.observers.append(p)
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
                for player in self.players:
                    decrease = round(player.chips * self.server_config['blind_percent'] * 0.01 +
                                     self.server_config['skim_percent'] * 0.01 * avg_stack)
                    player.chips = player.chips - decrease
                    if not player.disconnected:
                        message = 'Tournament chips decrease.'
                        player.send_message({'log': message})
                        message = f'You were removed {decrease} chips'
                        player.send_message({'log': message})
            self.check_eliminated()
            hand = GameHand(self.players, self.observers, deck, self.mongo_db, self.tourney_id)
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
            time.sleep(3)
            self.check_eliminated()


def main():
    config = yaml.safe_load(open("server-conf.yml", "r"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if config["use_ssl"]:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(config["pem_chain"], config["pem_privkey"])
        s.bind((config["listen_address"], config["listen_port"]))
        s.listen()
        if config["use_ssl"]:
            ssock = context.wrap_socket(s, server_side=True)
        else:
            ssock = s
        rooms = defaultdict(list)
        confs = defaultdict(dict)
        while True:
            conn, addr = ssock.accept()
            conn.setblocking(0)
            ready = select.select([conn], [], [], 5)
            if not ready[0]:
                conn.close()
                continue
            try:
                mess = conn.recv(4096).decode('utf-8')
                mess = json.loads(mess)
                nick = mess['nick']
                nick = re.sub('[^A-Za-z0-9_-]+', '-', nick)[:16]
                room = mess['room_code']
                if 'config' in mess and room not in confs:
                    confs[room] = mess['config']
                if not confs[room]:
                    conn.close()
                    continue
            except (json.JSONDecodeError, KeyError, ssl.SSLError):
                conn.close()
                continue
            p = Human(nick, conn)
            listener = mp.Thread(target=p.listen)
            listener.start()
            rooms[room].append(p)
            rooms[room] = [p for p in rooms[room] if not p.disconnected]
            if len(rooms[room]) >= confs[room]['number_seats']:
                g = Game(rooms[room], confs[room])
                t = mp.Thread(target=g.run)
                t.start()
                del rooms[room]
                del confs[room]

main()