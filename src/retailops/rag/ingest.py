"""Build the vector index from knowledge/*.md.  Run: python -m retailops.rag.ingest"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from retailops.config import get_settings
from retailops.rag.chunking import chunk_directory
from retailops.rag.retriever import VectorIndex


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ingest the knowledge base into the vector index.")
    p.add_argument("--knowledge-dir", default="knowledge")
    p.add_argument("--store-dir", default=get_settings().vectorstore_path)
    p.add_argument("--embedder", choices=["tfidf", "fastembed"], default="tfidf")
    args = p.parse_args(argv)

    chunks = chunk_directory(Path(args.knowledge_dir))
    if args.embedder == "fastembed":
        from retailops.rag.embeddings import FastEmbedEmbedder

        index = VectorIndex.build(chunks, FastEmbedEmbedder())
    else:
        index = VectorIndex.build(chunks)
    path = index.save(Path(args.store_dir))
    docs = len({c.doc_id for c in chunks})
    print(f"indexed {len(chunks)} chunks from {docs} documents ({args.embedder}) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
