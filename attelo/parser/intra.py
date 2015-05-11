"""
An InterInterParser applies separate parsers on edges
within a sentence, and then on edges across sentences
"""

from __future__ import print_function
from collections import defaultdict, namedtuple

from abc import ABCMeta, abstractmethod
from six import with_metaclass
import numpy as np

from attelo.edu import (FAKE_ROOT_ID)
from attelo.table import (Graph,
                          UNRELATED,
                          for_intra,
                          locate_in_subpacks)
from .interface import (Parser)

# pylint: disable=too-few-public-methods


class IntraInterPair(namedtuple("IntraInterPair",
                                "intra inter")):
    """
    Any pair of the same sort of thing, but with one meant
    for intra-sentential decoding, and the other meant for
    intersentential
    """
    pass


def _zip_sentences(func, sent_parses):
    """
    Given a function that combines sentence predictions, and a
    given a list of predictions for each sentence (eg. the
    3 best predictions for each sentence), apply that function
    over each list that goes together (eg. on best predictions
    for each sentence, then the second best, then the third
    best).

    We assume that we have the same number of predictions for
    each sentence ::

         zip_sentences(foo, [[s1_1, s1_2, s1_3],
                             [s2_1, s2_2, s2_3]])
         ==
         [foo([s1_1, s2_1]),
          foo([s1_2, s2_2]),
          foo([s1_3, s3_3])]

    Remember that a prediction is itself a set of links

    :type func: [prediction] -> a
    :type sent_parses: [[prediction]]
    :rtype: [a]
    """
    return [func(list(xs)) for xs in zip(*sent_parses)]


def partition_subgroupings(dpack):
    """
    Return an iterable of datapacks, each pack consisting of
    pairings within the same subgrouping
    """
    sg_indices = defaultdict(list)
    for i, pair in enumerate(dpack.pairings):
        edu2 = pair[1]
        key = edu2.grouping, edu2.subgrouping
        sg_indices[key].append(i)
    for idxs in sg_indices.values():
        yield dpack.selected(idxs)


class IntraInterParser(with_metaclass(ABCMeta, Parser)):
    """
    Parser that performs attach, direction, and labelling tasks;
    but in two phases:

        1. by separately parsing edges within the same sentence
        2. and then combining the results to form a document

    This is an abstract class

    Notes
    -----
    /Cache keys/: Same as whatever included parsers would use.
    This parser will divide the dictionary into keys that
    have an 'intra:' prefix or not. The intra prefixed keys
    will be passed onto the intrasentential parser (with
    the prefix stripped). The other keys will be passed onto
    the intersentential parser
    """
    def __init__(self, parsers):
        """
        Parameters
        ----------
        parsers: IntraInterPair(Parser)
        """
        self._parsers = parsers

    @staticmethod
    def _split_cache(cache):
        """
        Returns
        -------
        caches: IntraInterPair(dict(string, filepath))
        """
        if cache is None:
            return IntraInterPair(None, None)
        else:
            intra_cache = {}
            inter_cache = {}
            pref_len = len('intra:')
            for key in cache:
                if key.startswith('intra:'):
                    intra_cache[key[pref_len:]] = cache[key]
                else:
                    inter_cache[key] = cache[key]
            return IntraInterPair(intra=intra_cache,
                                  inter=inter_cache)

    def fit(self, dpacks, targets, cache=None):
        caches = self._split_cache(cache)
        dpacks_intra, targets_intra = self.dzip(for_intra, dpacks, targets)
        self._parsers.intra.fit(dpacks_intra, targets_intra,
                                cache=caches.intra)
        self._parsers.inter.fit(dpacks, targets, cache=caches.inter)
        return self

    def transform(self, dpack):
        # intrasentential target links are slightly different
        # in the fakeroot case (this only really matters if we
        # are using an oracle)
        dpack = self.multiply(dpack)
        dpack_intra, _ = for_intra(dpack, dpack.target)
        dpacks = IntraInterPair(intra=dpack_intra,
                                inter=dpack)
        # parse each sentence
        spacks = [self._parsers.intra.transform(dpack)
                  for dpack in partition_subgroupings(dpacks.intra)]

        return self._recombine(dpacks.inter, spacks)

    @staticmethod
    def _mk_get_lbl(dpack, subpacks):
        """
        Return a function that retrieves the label for an
        item within one of the subpacks, or None if it's
        not present

        Return
        ------
        get_lbl: int -> int or None
        """
        sub_idxes = np.array(locate_in_subpacks(dpack, subpacks))

        def get_lbl(i):
            'retrieve lbl if present'
            if sub_idxes[i] is None:
                return None
            else:
                spack, j = sub_idxes[i]
                return spack.graph.prediction[j]
        return get_lbl

    @abstractmethod
    def _recombine(self, dpack, spacks):
        """
        Run the second phase of decoding combining the results
        from the first phase
        """
        return NotImplementedError


class SentOnlyParser(IntraInterParser):
    """
    Intra/inter parser with no sentence recombination.
    We also chop off any fakeroot connections
    """
    def _recombine(self, dpack, spacks):
        "join sentences by parsing their heads"
        unrelated_lbl = dpack.label_number(UNRELATED)
        sent_lbl = self._mk_get_lbl(dpack, spacks)

        def merged_lbl(i, pair):
            'doc label where relevant else sentence label'
            edu1, _ = pair
            lbl = sent_lbl(i)
            if lbl is None or edu1.id == FAKE_ROOT_ID:
                return unrelated_lbl
            else:
                return lbl
        # merge results
        prediction = np.fromiter((merged_lbl(i, pair)
                                  for i, pair in enumerate(dpack.pairings)),
                                 dtype=np.dtype(np.int16))
        graph = dpack.graph.tweak(prediction=prediction)
        return dpack.set_graph(graph)


class HeadToHeadParser(IntraInterParser):
    """
    Intra/inter parser in which sentence recombination consists of
    parsing with only sentence heads.
    """
    def _select_heads(self, dpack, spacks):
        """
        return datapack consisting only of links between sentence
        heads and each other or the fakeroot
        """
        # identify sentence heads
        unrelated_lbl = dpack.label_number(UNRELATED)
        sent_lbl = self._mk_get_lbl(dpack, spacks)
        head_ids = [edu2.id for i, (edu1, edu2) in enumerate(dpack.dpairings)
                    if edu1.id == FAKE_ROOT_ID and
                    sent_lbl(i) != unrelated_lbl]

        # pick out edges where both elements are
        # a sentence head (or the fake root)
        def is_head_or_root(edu):
            'true if an edu is the fake root or a sentence head'
            return edu.id == FAKE_ROOT_ID or edu.id in head_ids
        idxes = [i for i, (e1, e2) in enumerate(dpack.pairings)
                 if is_head_or_root(e1) and is_head_or_root(e2)]
        return dpack.selected(idxes)

    def _recombine(self, dpack, spacks):
        "join sentences by parsing their heads"
        dpack_inter = self._select_heads(dpack, spacks)
        dpack_inter = self._parsers.inter.transform(dpack_inter)
        doc_lbl = self._mk_get_lbl(dpack, [dpack_inter])
        sent_lbl = self._mk_get_lbl(dpack, spacks)

        def merged_lbl(i):
            'doc label where relevant else sentence label'
            lbl = doc_lbl(i)
            return sent_lbl(i) if lbl is None else lbl
        # merge results
        prediction = np.fromiter(merged_lbl(i) for i in range(len(dpack)))
        graph = dpack.graph.tweak(prediction=prediction)
        return dpack.set_graph(graph)


class SoftParser(IntraInterParser):
    """
    Intra/inter parser in which sentence recombination consists of

    1. passing intra-sentential edges through but
    2. marking 1.0 attachment probabilities if they are attached
       and 1.0 label probabilities on the resulting edge
    """
    def _recombine(self, dpack, spacks):
        "soft decoding - pass sentence edges through the prob dist"
        unrelated_lbl = dpack.label_number(UNRELATED)
        sent_lbl = self._mk_get_lbl(dpack, spacks)

        weights_a = np.copy(dpack.graph.attach)
        weights_l = np.copy(dpack.graph.label)
        for i in range(len(dpack)):
            lbl = sent_lbl(i)
            if lbl is not None and lbl != unrelated_lbl:
                weights_a[i] = 1.0
                weights_l[i] = np.zeros(len(dpack.labels))
                weights_l[i, lbl] = 1.0
        dpack.set_graph(Graph(prediction=dpack.graph.prediction,
                              attach=weights_a,
                              label=weights_l))
        return self._parsers.inter.transform(dpack)