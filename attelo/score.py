'''
Scoring decoding results
'''

from collections import (defaultdict, namedtuple)

import numpy
from sklearn.metrics import confusion_matrix

from .table import (UNRELATED, get_label_string)

# pylint: disable=too-few-public-methods


class Count(namedtuple('Count',
                       ['tpos_attach',
                        'tpos_label',
                        'tpos_fpos',
                        'tpos_fneg'])):
    """
    Things we would count during the scoring process
    """
    @classmethod
    def sum(cls, counts):
        """
        Count made of the total of all counts
        """
        return cls(sum(x.tpos_attach for x in counts),
                   sum(x.tpos_label for x in counts),
                   sum(x.tpos_fpos for x in counts),
                   sum(x.tpos_fneg for x in counts))


class EduCount(namedtuple('EduCount',
                          ['correct_attach',
                           'correct_label',
                           'total'])):
    """
    Things we would count during the scoring process
    """
    @classmethod
    def sum(cls, counts):
        """
        Count made of the total of all counts
        """
        return cls(sum(x.correct_attach for x in counts),
                   sum(x.correct_label for x in counts),
                   sum(x.total for x in counts))

    def __add__(self, other):
        return EduCount(self.correct_attach + other.correct_attach,
                        self.correct_label + other.correct_label,
                        self.total + other.total)


def score_edges(dpack, predictions):
    """basic eval: counting correct predicted edges (labelled or not)
    data contains the reference attachments
    labels the corresponding relations

    :rtype: :py:class:`attelo.report.Count`
    """
    tpos_attach = 0
    tpos_label = 0
    dict_predicted = {(arg1, arg2): rel for arg1, arg2, rel in predictions
                      if rel != UNRELATED}
    pack = dpack.attached_only()
    for edu_pair, ref_label in zip(pack.pairings, pack.target):
        edu1, edu2 = edu_pair
        pred_label = dict_predicted.get((edu1.id, edu2.id))
        if pred_label is not None:
            tpos_attach += 1
            if dpack.label_number(pred_label) == ref_label:
                tpos_label += 1

    return Count(tpos_attach=tpos_attach,
                 tpos_label=tpos_label,
                 tpos_fpos=len(dict_predicted.keys()),
                 tpos_fneg=len(dpack.attached_only().pairings))


def score_edus(dpack, predictions):
    """compute the number of edus

    1. with correct attachments to their heads (ie. given edu
    e, every reference (p, e) link is present, and only such
    links are present)
    2. with correct attachments to their heads and labels
    (ie. given edu e, every reference (p, e) link is present,
    with the correct label, and only such links are present)

    This score may quite low if we are predicted a multiheaded
    graph

    :rtype EduCount
    """

    e_predictions = defaultdict(list)
    for parent, edu, rel in predictions:
        if rel == UNRELATED:
            continue
        e_predictions[edu].append((parent, dpack.label_number(rel)))

    e_reference = defaultdict(list)
    unrelated = dpack.label_number(UNRELATED)
    for edu_pair, ref_label in zip(dpack.pairings, dpack.target):
        if ref_label == unrelated:
            continue
        parent, edu = edu_pair
        e_reference[edu.id].append((parent.id, int(ref_label)))

    correct_attach = 0
    correct_label = 0
    for edu in dpack.edus:
        pred = sorted(e_predictions.get(edu.id, []))
        ref = sorted(e_reference.get(edu.id, []))
        if [x[0] for x in pred] == [x[0] for x in ref]:
            correct_attach += 1
        if pred == ref:
            correct_label += 1
    assert correct_label <= correct_attach

    return EduCount(correct_attach=correct_attach,
                    correct_label=correct_label,
                    total=len(dpack.edus))


def score_edges_by_label(dpack, predictions):
    """
    Return (as a generator) a list of pairs associating each
    label with scores for that label.

    If you are scoring mutiple folds you could loop over the
    folds, combining pre-existing scores for each label within
    the fold with its counterpart in the other folds
    """
    predictions = [(e1, e2, r) for (e1, e2, r) in predictions
                   if r != UNRELATED]
    unrelated = dpack.label_number(UNRELATED)

    for target in dpack.target:
        if target == unrelated:
            continue
        label = dpack.get_label(target)
        # pylint: disable=no-member
        r_indices = numpy.where(dpack.target == target)[0]
        # pylint: disable=no-member
        r_dpack = dpack.selected(r_indices)
        r_predictions = [(e1, e2, r) for (e1, e2, r) in predictions
                         if r == label]
        yield label, score_edges(r_dpack, r_predictions)


def build_confusion_matrix(dpack, predictions):
    """return a confusion matrix show predictions vs desired labels
    """
    pred_target = [dpack.label_number(label) for _, _, label in predictions]
    # we want the confusion matrices to have the same shape regardless
    # of what labels happen to be used in the particular fold
    # pylint: disable=no-member
    labels = numpy.arange(1, len(dpack.labels) + 1)
    # pylint: enable=no-member
    return confusion_matrix(dpack.target, pred_target, labels)


def empty_confusion_matrix(dpack):
    """return a zero array that could be used to accumulate future
    confusion matrix results
    """
    llen = len(dpack.labels)
    # pylint: disable=no-member
    return numpy.zeros((llen, llen))
    # pylint: disable=no-member


def _best_feature_indices(vocab, model, class_index, top_n):
    """
    Return a list of strings representing the best features in
    a model for a given class index
    """
    weights = model.coef_[class_index]   # higher is better?
    # pylint: disable=no-member
    best_idxes = numpy.argsort(weights)[-top_n:][::-1]
    best_weights = numpy.take(weights, best_idxes)
    # pylint: enable=no-member
    return [(vocab[j], w)
            for j, w in zip(best_idxes, best_weights)]


def discriminating_features(models, labels, vocab, top_n):
    """return the most discriminating features (and their weights)
    for each label in the models; or None if the model does not
    support this sort of query

    See :pyfunc:`attelo.report.show_discriminating_features`

    :param top_n number of features to return
    :type top_n: int

    :type models: Team(model)

    :type labels: [string]

    :param sequence of string labels, ie. one for each possible feature
    :type vocab: [string]

    :rtype: [(string, [(string, float)])] or None
    """
    best_idxes = lambda m, i: _best_feature_indices(vocab, m, i, top_n)
    if not hasattr(models.relate, 'coef_'):
        return None
    rows = []
    rows.append((UNRELATED, best_idxes(models.attach, 0)))
    for i, class_ in enumerate(models.relate.classes_):
        label = get_label_string(labels, class_)
        rows.append((label, best_idxes(models.relate, i)))
    return rows
