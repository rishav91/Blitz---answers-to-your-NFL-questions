"""Read access to the `games` Chroma collection built by data/ingest.py."""

from typing import Literal, Optional

import chromadb
from openai import OpenAI

CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "games"
EMBEDDING_MODEL = "text-embedding-3-small"

_collection = None
_openai_client = None


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        _collection = client.get_collection(COLLECTION_NAME)
    return _collection


def _embed(text: str) -> list[float]:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    response = _openai_client.embeddings.create(model=EMBEDDING_MODEL, input=[text])
    return response.data[0].embedding


def build_where(
    season: Optional[int], game_type: Optional[Literal["REG", "POST"]], week: Optional[int]
) -> Optional[dict]:
    clauses = []
    if season is not None:
        clauses.append({"season": season})
    if game_type is not None:
        clauses.append({"game_type": game_type})
    if week is not None:
        clauses.append({"week": week})
    if not clauses:
        return None
    # This chromadb version requires multi-key filters wrapped in $and —
    # implicit-AND on multiple top-level keys is rejected (see data/ingest.py).
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def query_games(semantic_query: str, where: Optional[dict], n_results: int) -> list[dict]:
    """Hybrid query: optional metadata `where` filter + semantic nearest-neighbor search (ADR-003)."""
    collection = _get_collection()
    kwargs = {"query_embeddings": [_embed(semantic_query)], "n_results": n_results}
    if where:
        kwargs["where"] = where
    result = collection.query(**kwargs)
    return [
        {"text": doc, "metadata": meta}
        for doc, meta in zip(result["documents"][0], result["metadatas"][0])
    ]
