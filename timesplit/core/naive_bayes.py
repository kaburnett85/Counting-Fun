"""A small multinomial Naive Bayes, in pure Python.

Chosen over anything heavier for four concrete reasons:

* It is genuinely incremental. A correction is "add w to a handful of counts" --
  there is no training pass to run, so the dashboard's reclassify button is
  instant even after years of data.
* It works at n=30. Gradient methods need thousands of examples; this app has
  to be useful in week one, from a handful of corrections.
* It is inspectable. "Which words made this School?" is a real question the
  dashboard answers, and that falls out of the counts directly.
* It needs no dependencies, which keeps the resident footprint small.

Scoring is done in log space with a logsumexp normalisation, so a long title
cannot underflow the posterior to zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Prediction:
    category_id: int | None
    confidence: float
    posteriors: dict[int, float] = field(default_factory=dict)
    #: The features that pushed hardest toward the winning category.
    evidence: list[tuple[str, float]] = field(default_factory=list)


class NaiveBayes:
    def __init__(self, alpha: float = 0.2):
        self.alpha = alpha
        #: category_id -> {token: count}
        self.tokens: dict[int, dict[str, float]] = {}
        #: category_id -> (documents, total token count)
        self.classes: dict[int, tuple[float, float]] = {}
        #: Pending changes not yet written to the database.
        self._token_delta: dict[int, dict[str, float]] = {}
        self._class_delta: dict[int, tuple[float, float]] = {}

    # ---- state ---------------------------------------------------------

    def load(
        self,
        tokens: dict[int, dict[str, float]],
        classes: dict[int, tuple[float, float]],
    ) -> None:
        self.tokens = {c: dict(t) for c, t in tokens.items()}
        self.classes = dict(classes)
        self._token_delta.clear()
        self._class_delta.clear()

    @property
    def total_docs(self) -> float:
        return sum(docs for docs, _ in self.classes.values())

    @property
    def vocabulary(self) -> set[str]:
        vocab: set[str] = set()
        for toks in self.tokens.values():
            vocab.update(toks)
        return vocab

    def is_warm(self, min_docs: int) -> bool:
        """Below this the model stays silent -- an honest 'unknown' beats a guess."""
        return self.total_docs >= min_docs and len(self.classes) >= 2

    # ---- learning ------------------------------------------------------

    def learn(self, features: list[str], category_id: int, weight: float = 1.0) -> None:
        if weight <= 0 or not features:
            return
        bucket = self.tokens.setdefault(category_id, {})
        delta = self._token_delta.setdefault(category_id, {})
        for token in features:
            bucket[token] = bucket.get(token, 0.0) + weight
            delta[token] = delta.get(token, 0.0) + weight
        docs, total = self.classes.get(category_id, (0.0, 0.0))
        self.classes[category_id] = (docs + 1.0, total + weight * len(features))
        d_docs, d_total = self._class_delta.get(category_id, (0.0, 0.0))
        self._class_delta[category_id] = (d_docs + 1.0, d_total + weight * len(features))

    def unlearn(self, features: list[str], category_id: int, weight: float = 1.0) -> None:
        """Undo a previous label. Counts clamp at zero rather than going negative."""
        if weight <= 0 or not features or category_id not in self.classes:
            return
        bucket = self.tokens.setdefault(category_id, {})
        delta = self._token_delta.setdefault(category_id, {})
        removed = 0.0
        for token in features:
            have = bucket.get(token, 0.0)
            take = min(have, weight)
            if take <= 0:
                continue
            bucket[token] = have - take
            if bucket[token] <= 0:
                bucket.pop(token, None)
            delta[token] = delta.get(token, 0.0) - take
            removed += take
        docs, total = self.classes[category_id]
        self.classes[category_id] = (max(0.0, docs - 1.0), max(0.0, total - removed))
        d_docs, d_total = self._class_delta.get(category_id, (0.0, 0.0))
        self._class_delta[category_id] = (d_docs - 1.0, d_total - removed)

    def take_delta(self) -> tuple[dict[int, dict[str, float]], dict[int, tuple[float, float]]]:
        """Hand the pending changes to the store and reset them."""
        tokens = {c: dict(t) for c, t in self._token_delta.items() if t}
        classes = dict(self._class_delta)
        self._token_delta.clear()
        self._class_delta.clear()
        return tokens, classes

    def seed(self, features: list[str], category_id: int, weight: float = 5.0) -> None:
        """Pseudo-counts from a rule, so the model starts with a sensible prior.

        Seeding does not count as a document: it shapes the model's leanings
        without pretending the user has confirmed anything.
        """
        if not features:
            return
        bucket = self.tokens.setdefault(category_id, {})
        delta = self._token_delta.setdefault(category_id, {})
        for token in features:
            bucket[token] = bucket.get(token, 0.0) + weight
            delta[token] = delta.get(token, 0.0) + weight
        docs, total = self.classes.get(category_id, (0.0, 0.0))
        self.classes[category_id] = (docs, total + weight * len(features))
        d_docs, d_total = self._class_delta.get(category_id, (0.0, 0.0))
        self._class_delta[category_id] = (d_docs, d_total + weight * len(features))

    # ---- prediction ----------------------------------------------------

    def predict(self, features: list[str], allowed: list[int] | None = None) -> Prediction:
        candidates = [c for c in self.classes if allowed is None or c in allowed]
        if not features or len(candidates) < 2:
            return Prediction(None, 0.0)

        # Drop features this model has never seen in any candidate category.
        # They carry no information about which job this is, but textbook
        # multinomial NB still lets them move the posterior -- whichever
        # category has seen fewer tokens overall gets a slightly higher
        # smoothed likelihood for each one, and over a title's worth of unseen
        # words that compounds into a confidently wrong answer. Ignoring them
        # is what keeps an unfamiliar window in the review queue instead.
        known = [
            f for f in features
            if any(f in self.tokens.get(c, {}) for c in candidates)
        ]
        if not known:
            return Prediction(None, 0.0)
        features = known

        vocab_size = max(1, len(self.vocabulary))
        total_docs = sum(self.classes[c][0] for c in candidates)
        scores: dict[int, float] = {}
        contributions: dict[int, list[tuple[str, float]]] = {}

        for cat in candidates:
            docs, token_total = self.classes[cat]
            # A category with no documents still gets a floor prior, so seeded
            # leanings are usable before the first correction arrives.
            prior = (docs + 1.0) / (total_docs + len(candidates))
            score = math.log(prior)
            per_feature: list[tuple[str, float]] = []
            bucket = self.tokens.get(cat, {})
            denom = token_total + self.alpha * vocab_size
            for token in features:
                likelihood = (bucket.get(token, 0.0) + self.alpha) / denom
                contribution = math.log(likelihood)
                score += contribution
                per_feature.append((token, contribution))
            scores[cat] = score
            contributions[cat] = per_feature

        posteriors = _softmax(scores)
        best = max(posteriors, key=lambda c: posteriors[c])
        evidence = _evidence_for(best, contributions)
        return Prediction(best, posteriors[best], posteriors, evidence)

    def explain(self, features: list[str], category_id: int, limit: int = 8) -> list[tuple[str, float]]:
        bucket = self.tokens.get(category_id, {})
        scored = [(f, bucket.get(f, 0.0)) for f in features]
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return scored[:limit]


def _softmax(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    top = max(scores.values())
    exps = {c: math.exp(s - top) for c, s in scores.items()}
    total = sum(exps.values()) or 1.0
    return {c: v / total for c, v in exps.items()}


def _evidence_for(
    winner: int, contributions: dict[int, list[tuple[str, float]]]
) -> list[tuple[str, float]]:
    """Features where the winner beat the runner-up by the widest margin."""
    others = [c for c in contributions if c != winner]
    if not others:
        return []
    win = dict(contributions[winner])
    margins: list[tuple[str, float]] = []
    for token, value in win.items():
        rival_best = max(dict(contributions[o]).get(token, -99.0) for o in others)
        margins.append((token, value - rival_best))
    margins.sort(key=lambda kv: kv[1], reverse=True)
    return [m for m in margins[:5] if m[1] > 0]


def rebuild(corrections: list[dict], alpha: float = 0.2) -> NaiveBayes:
    """Reconstruct a model from the correction log.

    The repair path for a damaged model, and the property the tests assert:
    replaying every correction must produce exactly the incremental model.
    """
    model = NaiveBayes(alpha=alpha)
    for c in corrections:
        feats = c.get("features") or []
        if not feats:
            continue
        old = c.get("old_category_id")
        weight = float(c.get("weight", 1.0))
        if old is not None and old != c["new_category_id"]:
            model.unlearn(feats, int(old), weight)
        model.learn(feats, int(c["new_category_id"]), weight)
    return model


def correction_weight(duration_s: float) -> float:
    """A 90-minute session teaches more than a 30-second one, within reason."""
    return min(3.0, 1.0 + max(0.0, duration_s) / 1800.0)
