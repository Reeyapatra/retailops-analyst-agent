"""Persisted vector index and a retriever that returns scored chunks or abstains."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from retailops.rag.chunking import Chunk, chunk_directory
from retailops.rag.embeddings import Embedder, FastEmbedEmbedder, TfidfEmbedder, Vector, cosine

INDEX_FILE = "index.json"
DEFAULT_K = 5
# Minimum cosine similarity of the best chunk for the retriever to answer rather than abstain.
# Chosen on the dev split of evals/datasets/rag_gold.jsonl; see docs/DECISIONS.md.
DEFAULT_MIN_SCORE = 0.12


class ScoredChunk(BaseModel):
    chunk: Chunk
    score: float


class RetrievalResult(BaseModel):
    query: str
    hits: list[ScoredChunk]
    abstain: bool  # True when nothing passed the relevance threshold
    top_score: float


class VectorIndex:
    def __init__(self, chunks: list[Chunk], vectors: list[Vector], embedder: Embedder) -> None:
        self.chunks = chunks
        self.vectors = vectors
        self.embedder = embedder

    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder | None = None) -> VectorIndex:
        embedder = embedder or TfidfEmbedder()
        embedder.fit([c.text for c in chunks])
        return cls(chunks, [embedder.embed(c.text) for c in chunks], embedder)

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / INDEX_FILE
        payload: dict[str, Any] = {
            "embedder": self.embedder.name,
            "embedder_state": self.embedder.state(),
            "chunks": [c.model_dump() for c in self.chunks],
            "vectors": self.vectors,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    @classmethod
    def load(cls, directory: Path) -> VectorIndex:
        payload = json.loads((directory / INDEX_FILE).read_text(encoding="utf-8"))
        name = payload["embedder"]
        embedder: Embedder
        if name == "tfidf":
            embedder = TfidfEmbedder(payload["embedder_state"]["idf"])
        elif name == "fastembed":
            embedder = FastEmbedEmbedder(payload["embedder_state"]["model_name"])
        else:
            raise ValueError(f"unknown embedder '{name}' in index")
        return cls([Chunk(**c) for c in payload["chunks"]], payload["vectors"], embedder)


class Retriever:
    def __init__(
        self, index: VectorIndex, *, k: int = DEFAULT_K, min_score: float = DEFAULT_MIN_SCORE
    ) -> None:
        self.index = index
        self.k = k
        self.min_score = min_score

    @classmethod
    def from_directory(cls, directory: Path, **kwargs: Any) -> Retriever:
        return cls(VectorIndex.load(directory), **kwargs)

    def retrieve(self, query: str, k: int | None = None) -> RetrievalResult:
        q = self.index.embedder.embed(query)
        scored = sorted(
            (
                ScoredChunk(chunk=c, score=round(cosine(q, v), 4))
                for c, v in zip(self.index.chunks, self.index.vectors, strict=True)
            ),
            key=lambda s: (-s.score, s.chunk.chunk_id),
        )[: k or self.k]
        top = scored[0].score if scored else 0.0
        hits = [s for s in scored if s.score >= self.min_score]
        return RetrievalResult(query=query, hits=hits, abstain=not hits, top_score=top)


def build_index_from_knowledge(knowledge_dir: Path, store_dir: Path) -> VectorIndex:
    index = VectorIndex.build(chunk_directory(knowledge_dir))
    index.save(store_dir)
    return index
