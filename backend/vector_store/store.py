"""Thin Qdrant wrapper: client factory, collections, user-scoped point operations.

Generic retrieval infra (no jac imports) — jac's `vectors.py` builds the RAG
read/write paths on top. Points carry a `{user_id, doc, entry_id, content_hash}`
payload; every operation here is scoped by user_id + doc, so corpora of different
users (and the "cv" vs "snippet" corpora of one user) can share a collection
without ever leaking into each other's results.
"""

import logging
import re
import threading
import uuid

from django.conf import settings

logger = logging.getLogger(__name__)

# Deterministic point ids: Qdrant only accepts uint64/UUID ids and our entry ids
# are "type:pk" strings — uuid5 maps (user, entry) to a stable UUID, so a
# re-upsert overwrites its point instead of duplicating it.
_POINT_NS = uuid.UUID("8f0e2f9c-1f7b-4d7a-9f63-5f0d3a2b1c4e")

_client = None
_client_target = None
_lock = threading.Lock()


def target() -> str:
    """The configured store location: '' (off), ':memory:', a path, or a URL."""
    return (getattr(settings, "VECTOR_STORE", "") or "").strip()


def is_enabled() -> bool:
    return bool(target())


def get_client():
    """Process-wide QdrantClient singleton for settings.VECTOR_STORE, or None when
    the store is off. Rebuilt when the setting changes (tests flip it via
    override_settings). Embedded mode (a path) locks its directory: one process
    only — all store access runs in the celery worker / a command / tests."""
    global _client, _client_target
    t = target()
    if not t:
        return None
    with _lock:
        if _client is None or _client_target != t:
            from qdrant_client import QdrantClient

            if _client is not None:
                try:
                    _client.close()
                except Exception:  # noqa: BLE001 — a dead client may fail to close
                    pass
            if t == ":memory:":
                _client = QdrantClient(":memory:")
            elif t.startswith(("http://", "https://")):
                _client = QdrantClient(url=t)
            else:
                _client = QdrantClient(path=t)
            _client_target = t
    return _client


def reset_client() -> None:
    """Close and drop the singleton (tests; embedded mode holds the dir lock)."""
    global _client, _client_target
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
        _client = None
        _client_target = None


def collection_name(embed_model: str) -> str:
    """One collection per embed model: dimensions differ between embedders and
    the cosine floors are calibrated per embedder (`embed_floors`), so a model
    switch must land in a fresh collection, never mix vectors."""
    slug = re.sub(r"[^a-z0-9]+", "-", (embed_model or "").lower()).strip("-")
    return f"entries__{slug}"


def point_id(user_id, entry_id: str) -> str:
    return str(uuid.uuid5(_POINT_NS, f"{user_id}:{entry_id}"))


def _scope(user_id, doc: str, entry_ids=None):
    """user+doc payload filter; `entry_ids` narrows it to exactly those entries."""
    from qdrant_client import models as qm

    must = [
        qm.FieldCondition(key="user_id", match=qm.MatchValue(value=int(user_id))),
        qm.FieldCondition(key="doc", match=qm.MatchValue(value=doc)),
    ]
    if entry_ids is not None:
        must.append(
            qm.FieldCondition(key="entry_id", match=qm.MatchAny(any=list(entry_ids)))
        )
    return qm.Filter(must=must)  # \: ignore


def ensure_collection(client, name: str, dim: int) -> None:
    from qdrant_client import models as qm

    if not client.collection_exists(name):
        client.create_collection(
            name,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )


def upsert(client, name: str, user_id, doc: str, items: list[dict]) -> None:
    """items: [{"id": entry_id, "hash": content hash, "vec": [float]}]."""
    from qdrant_client import models as qm

    client.upsert(
        name,
        points=[
            qm.PointStruct(
                id=point_id(user_id, it["id"]),
                vector=it["vec"],
                payload={
                    "user_id": int(user_id),
                    "doc": doc,
                    "entry_id": it["id"],
                    "content_hash": it["hash"],
                },
            )
            for it in items
        ],
    )


def delete(client, name: str, user_id, doc: str, entry_ids=None) -> None:
    """Delete the user's `doc` points; `entry_ids=None` wipes the whole scope."""
    from qdrant_client import models as qm

    if not client.collection_exists(name):
        return
    client.delete(
        name, points_selector=qm.FilterSelector(filter=_scope(user_id, doc, entry_ids))
    )


def stored_hashes(client, name: str, user_id, doc: str) -> dict:
    """{entry_id: content_hash} for every stored point in the user's `doc` corpus."""
    if not client.collection_exists(name):
        return {}
    out: dict = {}
    offset = None
    while True:
        points, offset = client.scroll(
            name,
            scroll_filter=_scope(user_id, doc),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            out[p.payload["entry_id"]] = p.payload.get("content_hash", "")
        if offset is None:
            return out


def search(
    client, name: str, user_id, doc: str, query_vec, entry_ids, with_vectors=False
) -> list[dict]:
    """Cosine-ranked [{id, score, vec}] restricted to exactly `entry_ids`.

    COSINE collections normalise vectors at upsert and score by dot product, so
    `score` is the same cosine similarity `Embed._cos` computes — the calibrated
    per-section floors keep working. Returned vectors are the normalised copies;
    cosine is scale-invariant, so the cover-letter MMR overlap math is unchanged.
    """
    res = client.query_points(
        name,
        query=query_vec,
        query_filter=_scope(user_id, doc, entry_ids),
        limit=len(entry_ids),
        with_payload=True,
        with_vectors=with_vectors,
    )
    return [
        {"id": p.payload["entry_id"], "score": p.score, "vec": list(p.vector or [])}
        for p in res.points
    ]
