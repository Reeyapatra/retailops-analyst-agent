"""Embedders. The default is a lexical TF-IDF model that needs no download or API key.

`FastEmbedEmbedder` (dense, local ONNX model) is an optional alternative installed with the
`rag` extra. It has NOT been run in this repository's tests; see README status table.
Vectors are sparse dicts {term: weight} for TF-IDF and dense lists for fastembed; both are
L2-normalised so a dot product is a cosine similarity.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Protocol

Vector = dict[str, float] | list[float]

_STOPWORD_TEXT = (
    "a an and are as at be but by do does for from how i if in is it its of on or so than "
    "that the their there these this to was we what when where which who why will with you "
    "your should can could would about into after before"
)
_STOPWORDS = frozenset(re.findall(r"\w+", _STOPWORD_TEXT))
_TOKEN = re.compile(r"[a-z0-9_]+")


def _stem(w: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def tokenize(text: str) -> list[str]:
    words = [_stem(w) for w in _TOKEN.findall(text.lower()) if w not in _STOPWORDS]
    bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
    return words + bigrams


class Embedder(Protocol):
    name: str

    def fit(self, texts: list[str]) -> None: ...
    def embed(self, text: str) -> Vector: ...
    def state(self) -> dict[str, Any]: ...


class TfidfEmbedder:
    """Unigram + bigram TF-IDF with sublinear term frequency. Deterministic, offline."""

    name = "tfidf"

    def __init__(self, idf: dict[str, float] | None = None) -> None:
        self.idf = idf or {}
        self.default_idf = max(self.idf.values(), default=1.0)

    def fit(self, texts: list[str]) -> None:
        df: Counter[str] = Counter()
        for t in texts:
            df.update(set(tokenize(t)))
        n = len(texts)
        self.idf = {term: math.log((1 + n) / (1 + c)) + 1.0 for term, c in df.items()}
        self.default_idf = max(self.idf.values(), default=1.0)

    def embed(self, text: str) -> dict[str, float]:
        counts = Counter(tokenize(text))
        vec = {t: (1.0 + math.log(c)) * self.idf[t] for t, c in counts.items() if t in self.idf}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        return {t: v / norm for t, v in vec.items()} if norm else {}

    def state(self) -> dict[str, Any]:
        return {"idf": self.idf}


class FastEmbedEmbedder:
    """Dense local embeddings via fastembed (optional extra; downloads a model on first use)."""

    name = "fastembed"

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as e:  # pragma: no cover - optional dependency
            raise ImportError("Install the extra: uv sync --extra rag") from e
        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name)

    def fit(self, texts: list[str]) -> None:
        return None

    def embed(self, text: str) -> list[float]:  # pragma: no cover - optional dependency
        return [float(x) for x in next(iter(self._model.embed([text])))]

    def state(self) -> dict[str, Any]:
        return {"model_name": self.model_name}


def cosine(a: Vector, b: Vector) -> float:
    if isinstance(a, dict) and isinstance(b, dict):
        if len(a) > len(b):
            a, b = b, a
        return sum(w * b.get(t, 0.0) for t, w in a.items())
    if isinstance(a, list) and isinstance(b, list):
        return sum(x * y for x, y in zip(a, b, strict=True))
    raise TypeError("cannot compare sparse and dense vectors")
