from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QRect, QObject, pyqtSignal, QThread, QMutex
from PyQt5.QtGui import QPixmap, QFont, QIntValidator, QDoubleValidator
from PyQt5.QtMultimedia import QSound
import sentry_sdk
from tutorial import Tutorial
import time
import yaml
import pika
import pika.exceptions
import requests
from cryptography.fernet import Fernet, InvalidToken
import json
import base64
import traceback

sentry_sdk.init("https://bcdfdee5d8864d408aae8249eff6edc5@o968644.ingest.sentry.io/5919963")

players_layout_6m = [
    ((400, 480), (300, 450)),
    ((20, 300), (140, 310)),
    ((20, 120), (140, 150)),
    ((300, 20), (420, 80)),
    ((680, 120), (580, 140)),
    ((680, 300), (580, 310)),
]
players_layout_3m = [
    ((400, 480), (300, 450)),
    ((20, 120), (140, 150)),
    ((680, 120), (580, 140)),
]
players_layout_hu = [
    ((400, 480), (300, 450)),
    ((300, 20), (420, 80)),
]


class EndWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.end_text = QLabel()
        self.end_text.setParent(self)
        self.replay_btn = QPushButton()
        self.replay_btn.setParent(self)
        self.replay_btn.setText('Replay')


class PokerTimer(QObject):
    timer_sig = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.done = False

    def run(self):
        while not self.done:
            self.timer_sig.emit()
            time.sleep(0.5)


class GameStartListener(QObject):
    game_start = pyqtSignal(str)
    queue_update = pyqtSignal()

    def __init__(self, channel, player_id):
        super().__init__()
        self.channel = channel
        self.player_id = player_id

    def run(self):
        self.channel.basic_consume(f'public.{self.player_id}', on_message_callback=self.callback, auto_ack=True)
        self.channel.start_consuming()

    def callback(self, ch, method, properties, body):
        self.queue_update.emit()
        body = json.loads(body.decode('utf-8'))
        if 'game'in body and self.player_id in body['players']:
            self.game_start.emit(body['game'])

    def stop(self):
        if self.channel.is_open:
            try:
                self.channel.stop_consuming()
            except pika.exceptions.StreamLostError:
                pass


class Listener(QObject):
    gamestate = pyqtSignal(dict)
    private = pyqtSignal(dict)

    def __init__(self, connection, player_id, key):
        super().__init__()
        self.channel = connection.channel()
        self.key = key
        self.player_id = player_id

    def run(self):
        self.channel.queue_purge(f'public.{self.player_id}')
        self.channel.basic_consume(f'public.{self.player_id}', on_message_callback=self.callback, auto_ack=True)
        self.channel.start_consuming()

    def callback(self, ch, method, properties, body):
        body = json.loads(body.decode('utf-8'))
        if not body:
            return
        if 'private_to' in body:
            if body['private_to'] == self.player_id:
                try:
                    self.private.emit(json.loads(
                        Fernet(self.key.encode('utf-8')).decrypt(base64.b64decode(body['data'].encode('utf-8'))).decode(
                            'utf-8')))
                except InvalidToken:
                    pass
        else:
            self.gamestate.emit(body)

    def stop(self):
        if self.channel.is_open:
            try:
                self.channel.stop_consuming()
            except pika.exceptions.StreamLostError:
                pass


class Board(QWidget):

    def __init__(self):
        super().__init__()
        self.setFixedSize(320, 196)
        self.f1 = QLabel(parent=self)
        self.f2 = QLabel(parent=self)
        self.f2.move(70, 0)
        self.f3 = QLabel(parent=self)
        self.f3.move(0, 98)

        self.f4 = QLabel(parent=self)
        self.f4.move(70, 98)

        self.t1 = QLabel(parent=self)
        self.t1.move(160, 0)
        self.t2 = QLabel(parent=self)
        self.t2.move(160, 98)

        self.r = QLabel(parent=self)
        self.r.move(250, 51)
        self.imgs = [self.f1, self.f2, self.f3, self.f4, self.t1, self.t2, self.r]
        self.board_cards = []

    def setBoard(self, board_cards):
        for img in self.imgs:
            img.hide()
        for i, cards in enumerate(zip(board_cards[::2], board_cards[1::2])):
            img = ''.join(cards)
            pxmap = QPixmap()
            pxmap.load(f'images/{img}.png')
            pxmap = pxmap.scaled(68, 94, transformMode=1)
            self.imgs[i].setPixmap(pxmap)
            self.imgs[i].show()
        self.board_cards = [c + s for c, s in zip(board_cards[::2], board_cards[1::2])]


class ConnectRoomTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QFormLayout()
        self.setLayout(layout)
        self.room_code_label = QLabel("Room code:")
        self.room_code = QLineEdit()
        layout.addRow(self.room_code_label, self.room_code)
        self.connect_btn = QPushButton()
        self.connect_btn.setText('Connect')
        layout.addRow(self.connect_btn)


class MainConnectWindow(QWidget):
    press_tutorial = pyqtSignal()

    def __init__(self, default_nickname, default_server):
        super().__init__()
        layout = QVBoxLayout()

        self.tutorial_btn = QPushButton()
        self.tutorial_btn.setText("Tutorial (FR)")
        layout.addWidget(self.tutorial_btn)
        self.tutorial_btn.pressed.connect(self.press_tutorial.emit)

        self.top_window = TopConnectWindow(default_nickname, default_server)
        self.top_window.login_failure.connect(self.push_auth_fail)
        layout.addWidget(self.top_window)

        self.setLayout(layout)
        self.query_logs = EventLog()
        self.query_logs.setFixedHeight(60)
        layout.addWidget(self.query_logs)

    def push_auth_fail(self, msg):
        self.query_logs.push_message(msg)


class TopConnectWindow(QWidget):
    login_success = pyqtSignal(str, str, list, str, str)
    login_failure = pyqtSignal(str)
    register_success = pyqtSignal()

    def __init__(self, default_nickname, default_server):
        super().__init__()
        layout = QFormLayout()
        self.setLayout(layout)
        self.nickname = QLineEdit()
        self.nickname.setText(default_nickname)
        layout.addRow(QLabel("Your nickname"), self.nickname)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        layout.addRow(QLabel("Password"), self.password)
        self.fqdn = QLineEdit()
        self.fqdn.setText(default_server)
        layout.addRow(QLabel("Server name"), self.fqdn)
        self.login = QPushButton()
        self.login.setText('Register')
        self.login.pressed.connect(self.register_request)
        layout.addRow(self.login)
        self.login = QPushButton()
        self.login.setText('Login')
        self.login.pressed.connect(self.login_request)
        layout.addRow(self.login)

    def login_request(self):
        user = self.nickname.text()
        password = self.password.text()
        response = requests.post(f'https://{self.fqdn.text()}/login', data={'user': user, 'password': password})
        resp_data = response.json()
        if 'status' in resp_data and resp_data['status'] == 'success':
            self.login_success.emit(resp_data['token'], resp_data['key'],
                                    resp_data['games'], resp_data['id'], password)
        else:
            self.login_failure.emit(resp_data['reason'])

    def register_request(self):
        user = self.nickname.text()
        password = self.password.text()
        response = requests.post(f'https://{self.fqdn.text()}/register', data={'user': user, 'password': password})
        resp_data = response.json()
        if 'status' in resp_data and resp_data['status'] == 'success':
            self.register_success.emit()


class BetAmountWidget(QWidget):
    def __init__(self, nb_cols=2):
        super().__init__()
        self.setFixedSize(200, 200)
        text_font = QFont("Sans", 10)
        self.text_widget = QLabel()
        self.text_widget.setFont(text_font)
        self.text_widget.setStyleSheet("QLabel { color : white; }")

        self.text_widget.setParent(self)
        self.chips = []
        self.nb_cols = nb_cols
        for i in range(7):
            for j in range(nb_cols):
                self.chips.append(QLabel())
                self.chips[-1].setParent(self)
                self.chips[-1].move(0 + 30 * j, 28 - 4 * i)

    def set_amount(self, amount):
        if not amount:
            self.hide()
            return
        self.text_widget.setText(str(amount))
        self.text_widget.adjustSize()
        i = 0
        for chip in self.chips:
            chip.hide()
        nb_chips_needed = 0
        amount_est = amount
        for chip_val in (500, 100, 25, 5, 1):
            while amount_est >= chip_val:
                amount_est -= chip_val
                nb_chips_needed += 1
        for chip_val in (500, 100, 25, 5, 1):
            while amount >= chip_val and i < len(self.chips):
                amount -= chip_val
                chip = self.chips[i]
                pxmap = QPixmap()
                pxmap.load(f'images/chip_{chip_val}.png')
                pxmap = pxmap.scaled(28, 22, transformMode=1)
                chip.setPixmap(pxmap)
                chip.show()
                i += 1 if nb_chips_needed >= 8 else self.nb_cols
        self.text_widget.move(40 if nb_chips_needed < 8 else 8 + 30 * self.nb_cols, 28)
        self.show()


class HoleCardsWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(108, 38)
        self.cards = [QLabel(parent=self), QLabel(parent=self), QLabel(parent=self)]
        self.cards[0].setGeometry(0, 0, 68, 38)
        self.cards[1].setGeometry(20, 0, 68, 38)
        self.cards[2].setGeometry(40, 0, 68, 38)
        self.setCards([], True)
        self.codes = []

    def setCards(self, cards, is_folded):
        if not cards:
            pxmap = QPixmap()
            pxmap.load('images/back.png')
            rect2 = QRect(0, 0, 68, 38)
            pxmap = pxmap.scaled(68, 94, transformMode=1).copy(rect2)
            self.cards[0].setPixmap(pxmap)
            self.cards[1].setPixmap(pxmap)
            self.cards[2].setPixmap(pxmap)
        elif cards != self.codes:
            for i, card in enumerate(cards):
                pxmap = QPixmap()
                rect = QRect(0, 0, 68, 38)
                pxmap.load(f'images/{card}.png')
                pxmap = pxmap.scaled(68, 94, transformMode=1).copy(rect)
                self.cards[i].setPixmap(pxmap)
            self.codes = cards

        for card in self.cards:
            card.setGraphicsEffect(None)
        if is_folded:
            if not cards:
                Opacity_0 = QGraphicsOpacityEffect()
                Opacity_0.setOpacity(0)
                self.setGraphicsEffect(Opacity_0)
            else:
                Opacity_40 = QGraphicsOpacityEffect()
                Opacity_40.setOpacity(0.4)
                self.setGraphicsEffect(Opacity_40)
        else:
            self.setGraphicsEffect(None)
            self.show()


class EventLog(QLabel):
    def __init__(self):
        super().__init__()
        self.messages = []
        self.setStyleSheet('background-color: black; color: white')
        text_font = QFont("Sans", 9)
        self.setFont(text_font)
        self.setAlignment(Qt.AlignBottom)

    def push_message(self, message):
        self.messages.append(message)
        self.setText('\n'.join(self.messages[-5:]))


class PlayerWidget(QWidget):
    def __init__(self, nickname, **kwargs):
        super().__init__()
        layout = QVBoxLayout()
        self.setLayout(layout)
        self.nickname = nickname
        self.bet_amount_widget = None
        self.is_folded = False
        self.chips = 0
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hcards = HoleCardsWidget()
        layout.addWidget(self.hcards)

        player_bg = QLabel(parent=self)
        pxmap = QPixmap()
        pxmap.load('images/PlayerTile.png')
        player_bg.setPixmap(pxmap)
        player_bg.adjustSize()
        player_bg.move(0, 32)

        text_font = QFont("Sans", 10)
        self.text_area = QLabel()
        self.text_area.setText(nickname)
        self.text_area.setFont(text_font)
        self.text_area.setStyleSheet("QLabel { color : white; }")

        self.timer = QProgressBar(parent=self, textVisible=False, maximum=1000)
        self.timer.setGeometry(2, 58, 104, 8)
        self.timer.setStyleSheet("""QProgressBar:horizontal {padding: 2px; background: grey;}
                                 QProgressBar::chunk {background-color: #0588BB; }""")
        self.timer.setValue(1000)

        text_font = QFont("Sans", 12, weight=1)
        self.chip_count = QLabel()
        self.chip_count.setText('500')
        self.chip_count.setFont(text_font)
        self.chip_count.setStyleSheet("QLabel { color : white; }")

        layout.addWidget(self.text_area)
        layout.addWidget(self.chip_count)
        layout.setAlignment(self.text_area, Qt.AlignTop | Qt.AlignHCenter)
        layout.setAlignment(self.chip_count, Qt.AlignTop | Qt.AlignHCenter)
        self.setFixedSize(108, 94)

    def setHoles(self, cards):
        self.hcards.setCards(cards, self.is_folded)

    def set_bet_amount_widget(self, widget):
        self.bet_amount_widget = widget


class RaiseWidgetGroup(QWidget):
    raise_change = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.raise_size = 0
        self.min_raise = 0
        self.max_raise = 0

        self.slider = QSlider(orientation=Qt.Vertical)
        self.slider.setFixedHeight(160)
        self.slider.move(40, 0)
        self.slider.setParent(self)
        self.slider.actionTriggered.connect(self.slider_raise)
        self.slider.adjustSize()
        self.adjustSize()
        self.free_text = QLineEdit()
        self.free_text.setParent(self)
        self.free_text.setGeometry(30, 180, 40, 20)
        int_validator = QIntValidator()
        self.free_text.setValidator(int_validator)
        self.free_text.textEdited.connect(self.set_raise_amount)
        self.slider.setMaximum(130)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)

    def set_raise_amount(self):
        if not self.free_text.hasAcceptableInput():
            return
        amount = int(self.free_text.text())
        self.raise_size = amount
        self.raise_change.emit(self.raise_size)

        range_ratio = (self.max_raise - 125 / 2) / self.min_raise
        if self.raise_size == self.max_raise:
            v = self.slider.maximum()
        elif range_ratio < 1:
            v = round(self.raise_size * 2 / self.min_raise)
        else:
            exp_increment = range_ratio ** 0.008
            v = 0
            for i in range(self.slider.maximum()):
                if min(round(exp_increment ** i * self.min_raise + i / 2), self.max_raise) > self.raise_size:
                    v = i
                    break
        self.slider.setValue(max(v - 2, 0))

    def set_raise_range(self, min_raise, max_raise):
        self.min_raise = min_raise
        self.max_raise = max_raise
        self.raise_size = min_raise
        self.free_text.validator().setRange(self.min_raise, self.max_raise)
        self.free_text.setText(str(self.min_raise))
        self.slider.setValue(0)

    def slider_raise(self):
        value = max(self.slider.value() - 2, 0)
        incr_lin_tot = 125 / 2
        range_ratio = (self.max_raise - incr_lin_tot) / self.min_raise
        if range_ratio <= 1:
            self.raise_size = min(round(value / 2 * self.min_raise), self.max_raise)
        else:
            exp_increment = range_ratio ** 0.008
            self.raise_size = min(round(exp_increment ** value * self.min_raise + value / 2), self.max_raise)

        self.raise_change.emit(self.raise_size)
        self.free_text.setText(str(self.raise_size))


class BetActions(QWidget):
    def __init__(self):
        super().__init__()
        self.call = QPushButton()
        self.call.move(80, 220)
        self.call.setText('Call')
        self.call.setParent(self)
        self.fold = QPushButton()
        self.fold.move(00, 220)
        self.fold.setText('Fold')
        self.fold.setParent(self)
        self.bet = QPushButton()
        self.bet.move(160, 220)
        self.bet.setText('Raise')
        self.bet.setParent(self)
        self.raise_group = RaiseWidgetGroup()
        self.raise_group.setGeometry(160, 0, 100, 200)
        self.raise_group.raise_change.connect(self.raise_changed)
        self.raise_group.setParent(self)
        self.hide()

    def raise_changed(self, value):
        self.bet.setText(f'Raise {value}')


class PokerTableWidget(QWidget):
    die = pyqtSignal()

    def __init__(self, nickname, spectate_only):
        super().__init__()
        self.bg = QLabel(parent=self)
        pixmap = QPixmap()
        pixmap.load('images/Background.png')
        self.bg.setPixmap(pixmap)
        self.setFixedSize(800, 600)
        self.board = Board()
        self.board.setParent(self)
        self.board.move(240, 220)
        self.min_raise = 0
        self.raise_size = 0
        self.to_call = 0
        self.players = []
        self.nickname = nickname
        self.spectate_only = spectate_only
        self.pot_size = BetAmountWidget(nb_cols=3)
        self.pot_size.setParent(self)
        self.pot_size.move(340, 150)
        self.bet_actions = BetActions()
        self.bet_actions.setParent(self)
        self.bet_actions.move(560, 320)
        self.reconnect = QPushButton()
        self.reconnect.move(560, 540)
        self.reconnect.setText('Reconnect')
        self.reconnect.setParent(self)
        self.event_log = EventLog()
        self.event_log.setFixedSize(200, 78)
        self.event_log.setParent(self)
        self.event_log.move(20, 500)

    def closeEvent(self, *args, **kwargs):
        self.die.emit()
        super().closeEvent(*args, **kwargs)

    def setWinningHand(self, winning_hand):
        for player in self.players:
            if player.is_folded:
                continue
            if winning_hand and player.hcards.codes and all(card not in winning_hand for card in player.hcards.codes):
                Opacity_40 = QGraphicsOpacityEffect()
                Opacity_40.setOpacity(0.4)
                player.hcards.setGraphicsEffect(Opacity_40)
            else:
                for card, widget in zip(player.hcards.codes, player.hcards.cards):
                    if winning_hand and card not in winning_hand:
                        Opacity_40 = QGraphicsOpacityEffect()
                        Opacity_40.setOpacity(0.4)
                        widget.setGraphicsEffect(Opacity_40)
                    else:
                        widget.setGraphicsEffect(None)
        for card, widget in zip(self.board.board_cards, self.board.imgs):
            if winning_hand and card not in winning_hand:
                Opacity_40 = QGraphicsOpacityEffect()
                Opacity_40.setOpacity(0.4)
                widget.setGraphicsEffect(Opacity_40)
            else:
                widget.setGraphicsEffect(None)

    def setBoard(self, board):
        self.board.setBoard(board)

    def setActive(self, nickname, players):
        self.reconnect.hide()
        if nickname == self.nickname:
            self.bet_actions.show()
        else:
            self.bet_actions.hide()
            for p in players:
                if p['name'] == self.nickname and p['disconnected']:
                    self.reconnect.show()
        for p in self.players:
            if p.nickname == nickname:
                p.timer.setValue(1000)
                p.timer.show()
            else:
                p.timer.hide()

    def setToCall(self, amount):
        self.to_call = amount

    def setPlayers(self, players_list):
        for p in self.players:
            p.alive = False
        for p_dict in players_list:
            for p in self.players:
                if p.nickname == p_dict['name']:
                    p.chips = p_dict['chips']
                    p.chip_count.setText(str(p_dict['chips']))
                    p.bet_amount_widget.set_amount(p_dict['bet'])
                    p.is_folded = p_dict['is_folded']
                    if p_dict.get('holes'):
                        p.setHoles([p_dict['holes'][0:2], p_dict['holes'][2:4], p_dict['holes'][4:]])
                    elif p.nickname != self.nickname:
                        p.setHoles([])
                    p.alive = True
        for p in self.players:
            if not p.alive:
                p.hide()

    def playSounds(self, last_action):
        if not last_action:
            return
        sound = QSound(f'sounds/{last_action}.wav')
        sound.play(f'sounds/{last_action}.wav')

    def startup_table(self, nicks):
        if self.spectate_only:
            rotated_nicks = nicks
        else:
            idx_self = nicks.index(self.nickname)
            rotated_nicks = nicks[idx_self:] + nicks[:idx_self]

        players_layout = players_layout_6m
        if len(rotated_nicks) == 3:
            players_layout = players_layout_3m
        if len(rotated_nicks) == 2:
            players_layout = players_layout_hu

        for (player_position, player_baw_position), nick in zip(players_layout, rotated_nicks):
            player = PlayerWidget(nick)
            player.setParent(self)
            player.move(*player_position)
            baw = BetAmountWidget()
            player.set_bet_amount_widget(baw)
            baw.setParent(self)
            baw.move(*player_baw_position)
            player.text_area.setText(nick)
            self.players.append(player)
            player.show()

    def setPotSize(self, pot_size, prev_pot):
        self.pot_size.set_amount(prev_pot)
        self.pot_size.text_widget.setText(f'{" / ".join(str(p) for p in pot_size)}')
        self.pot_size.text_widget.adjustSize()

    def setRaiseSize(self, min_raise, nl_raise):
        if min_raise:
            self.bet_actions.raise_group.set_raise_range(min_raise, self.players[0].chips)
        if nl_raise:
            self.bet_actions.raise_group.show()
        else:
            self.bet_actions.raise_group.hide()


class Game(QObject):
    die = pyqtSignal(str)

    def __init__(self, nickname, l_connection, w_channel, spectate_only, user_id, key, game_id):
        super().__init__()
        self.nickname = nickname
        self.game_id = game_id
        self.user_id = user_id
        self.started = False
        self.spectate_only = spectate_only
        self.poker_table = PokerTableWidget(nickname, spectate_only)
        self.channel = w_channel
        self.listener_thread = QThread()
        self.listener = Listener(l_connection, user_id, key)
        self.listener.moveToThread(self.listener_thread)
        self.listener.gamestate.connect(self.on_recv)
        self.listener_thread.started.connect(self.listener.run)
        self.timer_mutex = QMutex()
        self.poker_timer = PokerTimer()
        self.poker_timer_thread = QThread()
        self.poker_timer.moveToThread(self.poker_timer_thread)
        self.poker_timer_thread.started.connect(self.poker_timer.run)
        self.poker_timer.timer_sig.connect(self.decrease_timer)
        self.poker_table.die.connect(self.done)
        if not spectate_only:
            self.listener.private.connect(self.on_recv_private)
            self.poker_table.bet_actions.call.pressed.connect(self.call_btn)
            self.poker_table.bet_actions.fold.pressed.connect(self.fold_btn)
            self.poker_table.bet_actions.bet.pressed.connect(self.bet_btn)
            self.poker_table.reconnect.pressed.connect(self.reconnect)
        self.poker_table.show()

    def create_game(self):
        # TODO
        server_config = {'start_chips': int(self.room_tab.start_chips.text()),
                         'blind_timer': int(self.room_tab.blind_timer.text()),
                         'blind_percent': float(self.room_tab.blind_percent.text()),
                         'skim_percent': float(self.room_tab.skim_percent.text()),
                         'number_seats': int(self.room_tab.number_seats.text())}

    def start(self):
        if self.channel:
            self.reconnect()
        self.listener_thread.start()

    def call_btn(self):
        self.channel.basic_publish(exchange='poker_exchange',
                                   routing_key=f'game.{self.user_id}',
                                   body=b'c')

    def fold_btn(self):
        self.channel.basic_publish(exchange='poker_exchange',
                                   routing_key=f'game.{self.user_id}',
                                   body=b'f')

    def bet_btn(self):
        self.channel.basic_publish(exchange='poker_exchange',
                                   routing_key=f'game.{self.user_id}',
                                   body=f'r {self.poker_table.bet_actions.raise_group.raise_size}'.encode('utf-8'))

    def reconnect(self):
        self.channel.basic_publish(exchange='poker_exchange',
                                   routing_key=f'game.{self.user_id}',
                                   body=b'reconnect')

    def decrease_timer(self):
        for player in self.poker_table.players:
            if player.timer.isVisible():
                ttime = 60 if self.poker_table.board.board_cards else 30
                decr = int(500 / ttime)
                player.timer.setValue(player.timer.value() - decr)
                player.timer.repaint()

    def on_recv(self, gamestate):
        if not self.started:
            self.poker_table.startup_table([p['name'] for p in gamestate.get('players')])
            self.poker_timer_thread.start()
            self.started = True
        if 'finished' in gamestate:
            self.poker_table.event_log.push_message(f"{gamestate['finished']} finished place {gamestate['place']}")
            return
        if 'log' in gamestate:
            self.poker_table.event_log.push_message(gamestate.get('log'))
            return
        self.timer_mutex.lock()
        self.poker_table.setBoard(gamestate.get('board'))
        self.poker_table.setPlayers(gamestate.get('players'))
        self.poker_table.setPotSize(gamestate.get('pot'), gamestate.get('prev_pot'))
        self.poker_table.setActive(gamestate.get('active'), gamestate.get('players'))
        self.poker_table.setWinningHand(gamestate.get('winning_hand', ''))
        self.poker_table.playSounds(gamestate.get('last_action'))
        self.timer_mutex.unlock()

    def on_recv_private(self, gamestate):
        self.poker_table.setToCall(gamestate.get('to_call'))
        self.poker_table.setRaiseSize(gamestate.get('min_raise'), gamestate.get('nl_raise'))
        self.poker_table.bet_actions.bet.setText(f'Raise {self.poker_table.bet_actions.raise_group.raise_size}')
        self.poker_table.bet_actions.call.setText(
            f'Call {self.poker_table.to_call}' if self.poker_table.to_call else 'Check')
        if not self.poker_table.to_call:
            self.poker_table.bet_actions.fold.hide()
        else:
            self.poker_table.bet_actions.fold.show()
        if 'holes' in gamestate:
            self.poker_table.players[0].setHoles(
                [gamestate['holes'][0:2], gamestate['holes'][2:4], gamestate['holes'][4:]])
        if 'log' in gamestate:
            self.poker_table.event_log.push_message(gamestate.get('log'))

    def done(self):
        self.listener.stop()
        self.poker_timer.done = True
        self.die.emit(self.game_id)


class ConnectBtnConnector:
    def __init__(self, callback, *args):
        self.callback = callback
        self.args = args

    def __call__(self):
        self.callback(*self.args)


class GamesWindow(QScrollArea):
    connect_signal = pyqtSignal(str, bool)
    queue_signal = pyqtSignal(str)

    def __init__(self, player_id, server, token):
        super().__init__()
        self.games = {}
        self.signals = []
        self.server = server
        self.token = token
        self.player_id = player_id
        self._layout = QGridLayout()
        self.setLayout(self._layout)
        self._layout.setAlignment(Qt.AlignTop)

    def query_games(self):
        self.games = {}
        self.signals = []
        for i in reversed(range(self._layout.count())):
            self._layout.itemAt(i).widget().setParent(None)
        resp = requests.get(f'https://{self.server}/list_games', headers={'Authorization': self.token})
        if not resp.json() or not resp.json()['status'] == 'success':
            return
        self._layout.addWidget(QLabel('Name'), 0, 1)
        self._layout.addWidget(QLabel('Players'), 0, 2)
        self._layout.addWidget(QLabel('Status'), 0, 3)
        self._layout.addWidget(QLabel('Action'), 0, 4)
        for i, (queue, data) in enumerate(resp.json()['queues'].items(), start=1):
            player_count = QLabel(f'{len(data["players"])} / {data["seats"]}')
            label = QLabel(queue)
            join = QPushButton()
            join.setText("Subscribe" if self.player_id not in data['players'] else "Unsubscribe")
            self.signals.append(ConnectBtnConnector(self.queue_signal.emit, queue))
            join.pressed.connect(self.signals[-1])
            self._layout.addWidget(label, i, 1)
            self._layout.addWidget(player_count, i, 2)
            self._layout.addWidget(QLabel('registering'), i, 3)
            self._layout.addWidget(join, i, 4)
            self.games[queue] = i
        next_to_add = len(self.games) + 1
        for i, (game, data) in enumerate(resp.json()['games'].items(), start=next_to_add):
            player_count = QLabel(str(len(data['players'])))
            label = QLabel(game)
            status = QLabel(data['status'])
            self._layout.addWidget(label, i, 1)
            self._layout.addWidget(player_count, i, 2)
            self._layout.addWidget(status, i, 3)
            if data['status'] == 'running':
                join = QPushButton()
                join.setText("Observe" if self.player_id not in data['players'] else "Take seat")
                self.signals.append(ConnectBtnConnector(self.connect_signal.emit, game, self.player_id not in data['players']))
                join.pressed.connect(self.signals[-1])
                self._layout.addWidget(join, i, 4)
            self.games[game] = i


class MainWindow(QMainWindow):

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setWindowTitle('Bordeaux 3')
        self.connect_window = MainConnectWindow(self.config.get("nickname", ''),
                                                self.config.get("server", ''))
        self.connect_window.top_window.login_success.connect(self.show_games)
        self.connect_window.top_window.register_success.connect(self.log_success_register)
        self.connect_window.press_tutorial.connect(self.set_tutorial)
        self.connect_window.setFixedSize(600, 400)
        self.setCentralWidget(self.connect_window)
        self.adjustSize()
        self.poker_timer = None
        self.user_id = None
        self.password = None
        self.game_listener_thread = QThread()
        self.games = {}
        self.launch_mutex = QMutex()

    def log_success_register(self):
        self.connect_window.query_logs.push_message("Successfully registered")

    def set_tutorial(self):
        self.t = Tutorial()
        self.t.show()

    def _done(self):
        self.setCentralWidget(self.connect_window)
        self.adjustSize()

    def show_games(self, token, key, games, user_id, password):
        self.token = token
        self.user_id = user_id
        self.password = password
        self.key = key
        self.connect_window.top_window.hide()
        self.games_listing = GamesWindow(self.user_id, self.connect_window.top_window.fqdn.text(), token)
        self.games_listing.connect_signal.connect(self.on_recv)
        self.games_listing.queue_signal.connect(self.queue)
        self.games_listing.query_games()
        self.connect_window.layout().insertWidget(2, self.games_listing)
        self.games_listing.show()
        auth = pika.PlainCredentials(user_id, password)
        conn = pika.BlockingConnection(pika.ConnectionParameters(self.connect_window.top_window.fqdn.text(),
                                                                 5672,
                                                                 'game_start',
                                                                 credentials=auth))
        channel = conn.channel()
        self.game_listener = GameStartListener(channel, user_id)
        self.game_listener.moveToThread(self.game_listener_thread)
        self.game_listener.game_start.connect(self.on_recv)
        self.game_listener.queue_update.connect(self.games_listing.query_games)
        self.game_listener_thread.started.connect(self.game_listener.run)
        self.game_listener_thread.start()
        for game in games:
            self.on_recv(game)

    def queue(self, queue_id):
        requests.get(f'https://{self.connect_window.top_window.fqdn.text()}/queue/{queue_id}',
                     headers={'Authorization': self.token})

    def on_recv(self, game_id, observe_only=False):
        self.launch_mutex.lock()
        game_id = str(game_id)
        auth = pika.PlainCredentials(self.user_id, self.password)
        nickname = self.connect_window.top_window.nickname.text()
        try:
            if observe_only:
                resp = requests.get(f'https://{self.connect_window.top_window.fqdn.text()}/spectate/{game_id}',
                                    headers={'Authorization': self.token})
                w_channel = None
            else:
                w_connection = pika.BlockingConnection(
                    pika.ConnectionParameters(self.connect_window.top_window.fqdn.text(),
                                              5672,
                                              game_id,
                                              credentials=auth))
                w_channel = w_connection.channel()
            l_connection = pika.BlockingConnection(pika.ConnectionParameters(self.connect_window.top_window.fqdn.text(),
                                                                             5672,
                                                                             game_id,
                                                                             credentials=auth))
            if game_id not in self.games:
                self.games[game_id] = Game(nickname, l_connection, w_channel, observe_only, self.user_id, self.key, game_id)
                self.games[game_id].die.connect(self.game_end)
                self.games[game_id].start()
                self.games_listing.query_games()
                index = self.games_listing.games[game_id]
                self.games_listing.layout().itemAtPosition(index, 4).widget().hide()
        except Exception as e:
            print(e)
        finally:
            self.launch_mutex.unlock()

    def game_end(self, game_id):
        if game_id in self.games:
            del self.games[game_id]
        self.games_listing.query_games()
        index = self.games_listing.games.get(game_id, None)
        if index is not None:
            self.games_listing.layout().itemAtPosition(index, 4).widget().show()


def main():
    app = QApplication([])
    try:
        config = yaml.safe_load(open("conf.yml", "r"))
    except (OSError, yaml.YAMLError):
        config = {}
    mw = MainWindow(config)
    mw.show()
    app.exec_()


main()
