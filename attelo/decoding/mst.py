'''
Created on Jun 27, 2012

@author: stergos


'''

from collections import defaultdict
from math import log
import sys

from depparse.graph import Digraph


def get_root(_):
    """ This is used for the construction of the graph. Since we are
    using the Chu-Liu Edmonds algorithm in it's dependency parsing
    incarnation, there should be a node that is the root, i.e.  a node
    that has no incoming edges. This function is supposed to return
    that node. For the moment it always returns the first
    node. Syntactic analysis should be sufficient to provide the real
    head node.
    """
    return '1'


def _remove_local_cycles(instances):
    """
    Filter instances of the form `(source, target, prob, rel)`
    so that every pair of nodes is represented by only one instance,
    the one with the highest probability.

    If we have some `(foo, bar, p1, r1)` and `(bar, foo, p2, r2)`,
    we only take one of the two.

    The intended use is to remove local cycles from the instance
    graph before feeding them to the Chu-Liu/Edmonds algorithm
    """
    buckets = defaultdict(list)
    for instance in instances:
        src = instance[0].id
        tgt = instance[1].id
        key = (min(src, tgt), max(src, tgt))
        buckets[key].append(instance)

    def max_by_prob(instances):
        "the best instance by its probability"
        return max(instances, key=lambda x: x[2])

    return list(map(max_by_prob, buckets.values()))


def _graph(instances, root='1', use_prob=True):
    """ instances are quadruplets of the form:

            source, target, probability_of_attachment, relation

        root is the "root" of the graph, that is the node that has no incoming
        nodes

        returns the Maximum Spanning Tree
    """

    targets = defaultdict(list)
    labels = dict()
    scores = dict()

    for source, target, prob, rel in _remove_local_cycles(instances):
        src = source.id
        tgt = target.id
        if tgt == root:
            continue
        scores[src, tgt] = prob
        labels[src, tgt] = rel
        if use_prob:  # probability scores
            scores[src, tgt] = log(prob if prob != 0.0 else sys.float_info.min)
        targets[src].append(tgt)

    return Digraph(targets,
                   lambda s, t: scores[s, t],
                   lambda s, t: labels[s, t]).mst()


def _list_edges(instances, root='1', use_prob=True):
    """ instances are quadruplets of the form:

            source, target, probability_of_attachment, relation

        root is the "root" of the graph, that is the node that has no incoming
        nodes

        returns a list of edges for the MST graph
    """
    mst = _graph(instances, root, use_prob=use_prob)

    return [(src, tgt, mst.get_label(src, tgt))
            for src, tgt in mst.iteredges()]


def mst_decoder(*args, **kwargs):
    """ attach in such a way that the resulting subgraph is a
    maximum spanning tree of the original
    """
    return _list_edges(*args, **kwargs)
