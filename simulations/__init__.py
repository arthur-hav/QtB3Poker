from deuces import Card, evaluator
import itertools
from math import floor
import random
import matplotlib.pyplot as plt
from collections import defaultdict
import networkx as nx
import Levenshtein as lev
import re

ev = evaluator.Evaluator()

player_ranks = {}
players_comb = {}


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
            i = int(floor(random.random() * amnt_to_shuffle))
            amnt_to_shuffle -= 1
            self.cards[i], self.cards[amnt_to_shuffle] = self.cards[amnt_to_shuffle], self.cards[i]

    def remove_card(self, card):
        self.cards.remove(card)


class Simulation:
    def __init__(self, deck):
        self.flop1 = [deck.pop(), deck.pop()]
        self.flop2 = [deck.pop(), deck.pop()]
        self.turn1 = [deck.pop()]
        self.turn2 = [deck.pop()]
        self.river = [deck.pop()]

    def eval(self, *hands):
        for i, hand in enumerate(hands):
            rank = None
            hand_index = None
            for comb in itertools.combinations(hand, 2):
                for flop in self.flop1, self.flop2:
                    for turn in self.turn1, self.turn2:
                        for bcomb in itertools.combinations(flop + turn + self.river, 3):
                            eval = ev.evaluate(cards=list(comb), board=list(bcomb))
                            if not rank or eval < rank:
                                hand_index = i
                                rank = eval
        return rank, hand_index


def suit_group_notation(hand, grouped=True):
    ranks = list(reversed('AKQJTBBMMMLLL')) if grouped else list(reversed('AKQJT98765432'))
    sorted_cards = sorted(hand, key=lambda c: Card.get_suit_int(c))
    pair_prefix = 'b' if len(set(Card.get_rank_int(c) for c in hand)) == 1 else 'p' if len(
        set(Card.get_rank_int(c) for c in hand)) < 3 else ''

    if Card.get_suit_int(sorted_cards[0]) == Card.get_suit_int(sorted_cards[1]) == Card.get_suit_int(sorted_cards[2]):
        return pair_prefix + '(' + ''.join(
            ranks[Card.get_rank_int(c)] for c in sorted(sorted_cards, key=lambda c: -Card.get_rank_int(c))) + ')'

    if Card.get_suit_int(sorted_cards[0]) == Card.get_suit_int(sorted_cards[1]):
        return pair_prefix + '(' + ''.join(
            ranks[Card.get_rank_int(c)] for c in sorted(sorted_cards[:2], key=lambda c: -Card.get_rank_int(c))) + ')' \
               + ranks[Card.get_rank_int(sorted_cards[2])]

    if Card.get_suit_int(sorted_cards[1]) == Card.get_suit_int(sorted_cards[2]):
        return pair_prefix + '(' + ''.join(
            ranks[Card.get_rank_int(c)] for c in sorted(sorted_cards[1:], key=lambda c: -Card.get_rank_int(c))) + ')' \
               + ranks[Card.get_rank_int(sorted_cards[0])]

    return pair_prefix + ''.join(
        ranks[Card.get_rank_int(c)] for c in sorted(sorted_cards, key=lambda c: -Card.get_rank_int(c)))


def distance(d1, d2):
    return int((sum(d1) - sum(d2)) ** 2)


def cluster(nb_clusters, plots, min_per_cluster=0, max_per_cluster=None):
    if max_per_cluster is None:
        max_per_cluster = len(plots)
    g = nx.DiGraph()
    g.add_node('_p')
    g.add_node('_s')
    cluster_vectors = []
    for i in range(nb_clusters):
        cluster_vectors.append([0] * len(plots[0]))
        for j in range(len(plots[0])):
            cluster_vectors[i][j] = random.choice([plot[j] for plot in plots])

    for i, plot in enumerate(plots):
        g.add_edge('_s', f'plot_{i}', capacity=1)
        for j in range(nb_clusters):
            g.add_edge(f'plot_{i}', f'cluster_{j}', capacity=1, weight=distance(cluster_vectors[j], plot))
    for i in range(nb_clusters):
        g.add_edge(f'cluster_{i}', '_p', capacity=max_per_cluster)

    print('Clustering...')
    clusters = {}
    for _it in range(2):
        print(_it)
        clusters_nodes = defaultdict(list)
        flow = nx.max_flow_min_cost(g, '_s', '_p')
        for origin, flow_dict in flow.items():
            for extr, flow_value in flow_dict.items():
                if origin.startswith('plot') and extr.startswith('cluster') and flow_value > 0:
                    origin_id = int(origin.replace('plot_', ''))
                    extr_id = int(extr.replace('cluster_', ''))
                    clusters[origin_id] = extr_id
                    clusters_nodes[extr_id].append(origin_id)
        # new weights
        for cluster, nodes in clusters_nodes.items():
            cluster_vectors[cluster] = [sum(plots[n][i] / len(nodes) for n in nodes) for i in range(len(plots[0]))]
            for j, plot in enumerate(plots):
                g.remove_edge(f'plot_{j}', f'cluster_{cluster}')
                g.add_edge(f'plot_{j}', f'cluster_{cluster}', capacity=1,
                           weight=distance(cluster_vectors[cluster], plot))

    return list(clusters_nodes.values())


def group_hand(hand, hand_list):
    for other in hand_list:
        editops = lev.editops(hand, other)
        if len(editops) == 1:
            char_index = editops[0][1]
            return hand[:char_index] + '[' + hand[char_index] + other[char_index] + ']' + hand[char_index + 1:]
    return hand


class RankGroup:
    rank_group = 'AKQJTBML'
    reverse_lookup = {
        'L': ['2', '3', '4'],
        'M': ['5', '6', '7'],
        'B': ['8', '9'],
        'T': ['T'],
        'J': ['J'],
        'Q': ['Q'],
        'K': ['K'],
        'A': ['A']
    }

    def __init__(self, char):
        self.min_rank = self.rank_group.index(char)
        self.max_rank = self.rank_group.index(char)

    def dist(self, other):
        if self.max_rank >= other.min_rank >= self.min_rank or self.max_rank >= other.max_rank >= self.min_rank:
            return 0
        diff = min(abs(other.min_rank - self.max_rank), abs(other.max_rank - self.min_rank))
        if diff > 0:
            diff += 1
        return diff

    def merge(self, other):
        self.min_rank = min(self.min_rank, other.min_rank)
        self.max_rank = max(self.max_rank, other.max_rank)

    def __str__(self):
        if self.min_rank == self.max_rank:
            return self.rank_group[self.max_rank]
        return '[' + self.rank_group[self.min_rank] + self.rank_group[self.max_rank] + ']'

    @classmethod
    def from_str(cls, string):
        if len(string) == 1:
            return cls(string[0])
        instance = cls(string[1])
        instance.merge(cls(string[2]))
        return instance

    def iter_ranks(self, dead_ranks=''):
        for i in range(self.min_rank, self.max_rank + 1):
            letter = self.rank_group[i]
            for rank in self.reverse_lookup[letter]:
                if rank not in dead_ranks:
                    yield rank


class HandGroup:
    @classmethod
    def from_cards(cls, cards):
        instance = cls()
        instance.suited = '(' in cards
        instance.tripled = RankGroup(cards[1]) if cards[0] == 'b' else None
        if instance.suited:
            instance.suit_rank = RankGroup(cards[cards.index('(') + 1])

        else:
            instance.suit_rank = None
        instance.paired = cards[0] == 'p'
        if instance.tripled:
            ranks = []
        elif instance.paired:
            for card in cards:
                if cards.count(card) >= 2:
                    instance.paired = RankGroup(card)
                if cards.count(card) == 3 or cards.count(card) == 1 and card in RankGroup.rank_group:
                    ranks = [card]
        else:
            ranks = [c for c in cards if c not in ('p', 'b', '(', ')')]
        instance.ranks = [RankGroup(c) for c in sorted(ranks, key=lambda c: RankGroup.rank_group.index(c))]
        return instance

    def __init__(self):
        self.span = 0
        self.tripled = None
        self.paired = None
        self.ranks = []
        self.suited = False
        self.suit_rank = None

    def try_merge(self, hand_group, span=8):
        if self.tripled != hand_group.tripled:
            return False
        elif self.tripled:
            return True
        if self.suited != hand_group.suited:
            return False
        if self.paired and not hand_group.paired or not self.paired and hand_group.paired:
            return False
        if self.paired and hand_group.paired and self.paired.min_rank != hand_group.paired.min_rank:
            return False
        diff = 0
        for rank1, rank2 in zip(hand_group.ranks, self.ranks):
            diff += rank1.dist(rank2)
        if self.suited:
            diff += self.suit_rank.dist(hand_group.suit_rank) / 2
        if diff > span - self.span:
            return False
        for rank, other in zip(self.ranks, hand_group.ranks):
            rank.merge(other)
        if self.suited:
            self.suit_rank.merge(hand_group.suit_rank)
        self.span += diff
        return True

    def __str__(self):
        build_str = []
        added_suit = False
        if self.tripled:
            return f'<b{str(self.tripled)}>'
        if self.paired:
            build_str.append(f'p{str(self.paired)}')
            if self.suited and self.suit_rank.min_rank == self.paired.min_rank:
                added_suit = True
                build_str.append('s')
            build_str.append(str(self.ranks[0]))
            if self.suited and not added_suit:
                build_str.append('s')
        else:
            for i, rank in enumerate(self.ranks):
                build_str.append(str(rank))
                if self.suited and not added_suit and self.suit_rank.min_rank == rank.min_rank and self.suit_rank.max_rank == rank.max_rank:
                    added_suit = True
                    build_str.append('s')
        return f'<{"".join(build_str)}>'

    @classmethod
    def from_str(cls, string):
        if not string.startswith('<') or not string.endswith('>'):
            raise ValueError(f'Unexpected string {string}')
        string = string[1:-1]
        instance = cls()
        if string.startswith('b'):
            instance.tripled = string[1]
            return instance
        if string.startswith('p'):
            instance.paired = RankGroup.from_str(string[1])
            if 's' in string:
                instance.suited = True
                prev_sym = re.search(r'([AKQJTBML]|(\[[AKQJTBML]+\]))s', string).group(1)
                instance.suit_rank = RankGroup.from_str(prev_sym)
                string = string.replace('s', '')
            instance.ranks = [RankGroup.from_str(string[2:])]
            return instance
        if 's' in string:
            instance.suited = True
            prev_sym = re.search(r'([AKQJTBML]|(\[[AKQJTBML]+\]))s', string).group(1)
            instance.suit_rank = RankGroup.from_str(prev_sym)
            string = string.replace('s', '')

        instance.ranks = [RankGroup.from_str(match[0])
                          for match in re.findall(r'([AKQJTBML]|(\[[AKQJTBML]+\]))', string)]
        return instance

    def fit_hand(self, hand):
        to_suit_group_notation = suit_group_notation(hand)
        return self.try_merge(HandGroup.from_cards(to_suit_group_notation), 0)

    def iter_hands(self):
        if self.tripled:
            for rank in self.tripled.iter_ranks():
                yield (Card.new(rank + 's'), Card.new(rank + 'd'), Card.new(rank + 'c'))
                return
        if self.paired:
            suits = 'hdc'
            if self.suited:
                suits = 'hhd'
            for rank_pair in self.paired.iter_ranks():
                for rank_dangling in self.ranks[0].iter_ranks(rank_pair):
                    yield (Card.new(rank_dangling+ suits[0]), Card.new(rank_pair+ suits[1]), Card.new(rank_pair+suits[2]))
            return
        suits = 'hcd'
        if self.suited:
            if self.ranks[0].min_rank == self.suit_rank.min_rank and self.ranks[0].max_rank == self.suit_rank.max_rank:
                suits = 'hhc'
            else:
                suits = 'chh'
        for rank1 in self.ranks[0].iter_ranks():
            for rank2 in self.ranks[1].iter_ranks(rank1):
                for rank3 in self.ranks[2].iter_ranks(rank1+rank2):
                    yield (Card.new(rank1 + suits[0]), Card.new(rank2 + suits[1]), Card.new(rank3 + suits[2]))

def average_rank():
    first_group = [HandGroup.from_str(g)
                   for g in ['<[AK]s[JT][TB]>', '<pA[ML]>', '<pAs[BL]>',
                             '<pBAs>', '<pB[AM]>', '<pB[JL]>', '<pB[QM]s>',
                             '<pBs[BL]>', '<pJ[BL]>', '<pJ[QM]s>',
                             '<pK[AM]>', '<pK[JL]>', '<pKs[QB]>', '<pLM>',
                             '<pL[AJ]s>', '<pL[KQ]s>', '<pL[ML]s>',
                             '<pM[AK]s>', '<pM[AM]>', '<pM[JL]s>',
                             '<pM[QM]s>', '<pQK>', '<pQs[BL]>', '<pT[AM]>', '<pT[QL]>', '<pTs[BL]>']]

    hands_gigaset = set()
    rankings = defaultdict(list)
    nb_sims = 0
    #for group in first_group:

    d = Deck()

    for hand in itertools.combinations(d.cards, 3):
        hand_str = suit_group_notation(hand, grouped=False)
        if hand_str in hands_gigaset:
            continue
        nb_sims += 1
        ranks = []
        for i in range(1000):
            d2 = Deck()
            for c in hand:
                d2.remove_card(c)
            d2.fisher_yates_shuffle_improved()
            s = Simulation(d2)
            rank, _ = s.eval(hand)
            ranks.append(rank)
        nb_beat = len([r for r in ranks if r < 2860])
        hands_gigaset.add(hand_str)
        rankings[nb_beat].append(hand_str)

    # hand_strs = list(ranks.keys())
    # cluster_nodes = cluster(8, plots, max_per_cluster=nb_sims / 8)
    # hand_group_clusters = {}
    # for i, node_list in enumerate(cluster_nodes):
    #     avg_cluster = [sum(plots[n][i] for n in node_list) / len(node_list) for i in range(len(plots[0]))]
        #
        # hands = set(HandGroup(hand_strs[n]) for n in node_list)
        # grouped_hands = set()
        # for hand in hands:
        #     added = False
        #     for group in grouped_hands:
        #         if group.try_merge(hand):
        #             added = True
        #             break
        #     if not added:
        #         grouped_hands.add(hand)


        # sum_avg = int(sum(avg_cluster) / 1000.0)
        # print([hand_strs[n] for n in node_list])
        # hand_group_clusters[i] = f'{sum_avg}' + ', '.join(sorted(set(hand_strs[n] for n in node_list)))
    import pprint
    pprint.pprint(rankings)

# average_rank()

import rankings

r = rankings.rankings
for