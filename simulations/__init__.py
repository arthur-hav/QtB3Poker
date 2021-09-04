from deuces import Card, evaluator
import itertools
from math import floor
import random
import matplotlib.pyplot as plt
from collections import defaultdict
import networkx as nx

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


def suit_notation(hand):
    ranks = list(reversed('AKQJT98765432'))
    sorted_cards = sorted(hand, key=lambda c: Card.get_suit_int(c))

    if Card.get_suit_int(sorted_cards[0]) == Card.get_suit_int(sorted_cards[1]) == Card.get_suit_int(sorted_cards[2]):
        return '(' + ''.join(ranks[Card.get_rank_int(c)] for c in sorted(sorted_cards, key=lambda c: -Card.get_rank_int(c))) + ')'

    if Card.get_suit_int(sorted_cards[0]) == Card.get_suit_int(sorted_cards[1]):
        return '(' + ''.join(ranks[Card.get_rank_int(c)] for c in sorted(sorted_cards[:2], key=lambda c: -Card.get_rank_int(c))) + ')'\
               + ranks[Card.get_rank_int(sorted_cards[2])]

    if Card.get_suit_int(sorted_cards[1]) == Card.get_suit_int(sorted_cards[2]):
        return '(' + ''.join(ranks[Card.get_rank_int(c)] for c in sorted(sorted_cards[1:], key=lambda c: -Card.get_rank_int(c))) + ')'\
               + ranks[Card.get_rank_int(sorted_cards[0])]

    return ''.join(ranks[Card.get_rank_int(c)] for c in sorted(sorted_cards, key= lambda c: -Card.get_rank_int(c)))


def distance(d1, d2):
    return int(sum((d1_val - d2_val) ** 2 for d1_val, d2_val in zip(d1, d2)) ** 0.5)


def cluster(nb_clusters, plots, min_per_cluster=0, max_per_cluster=None):
    if max_per_cluster is None:
        max_per_cluster = nb_clusters
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

    clusters = {}
    for _it in range(20):
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
            cluster_vectors[cluster] = [sum(plots[n][i]/len(nodes) for n in nodes) for i in range(len(plots[0]))]
            for j, plot in enumerate(plots):
                g.remove_edge(f'plot_{j}', f'cluster_{cluster}')
                g.add_edge(f'plot_{j}', f'cluster_{cluster}', capacity=1,
                           weight=distance(cluster_vectors[cluster], plot))

    return clusters, clusters_nodes


def average_rank():
    ranks = defaultdict(list)
    hand_strs = []
    legend = []
    plots = []
    for h_i in range(30):
        d = Deck()
        d.fisher_yates_shuffle_improved()
        hand = (d.pop(), d.pop(), d.pop())
        for i in range(5000):
            d2 = Deck()
            for c in hand:
                d2.remove_card(c)
            d2.fisher_yates_shuffle_improved()
            s = Simulation(d2)
            rank, _ = s.eval(hand)
            ranks[hand].append(rank)
    for hand, rank_list in ranks.items():
        rank_list = sorted(rank_list)
        rank_list = [rank_list[(i * 5000) // 25 + 100] for i in range(25)]
        hand_str = suit_notation(hand)
        plots.append(rank_list)
        hand_strs.append(hand_str)

    clusters, cluster_nodes = cluster(6, plots, 20)
    for cluster_id, node_list in cluster_nodes.items():
        avg_cluster = [sum(plots[n][i] for n in node_list) / len(node_list) for i in range(25)]
        plt.plot(avg_cluster)
        legend.append(','.join(hand_strs[n] for n in node_list))
    plt.legend(legend)
    plt.show()

average_rank()