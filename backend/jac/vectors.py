"""RAG read/write paths for the career DB, on top of the `vector_store` app.

Classic-RAG split: the corpus (career entries, snippets) is embedded at INGEST
time and persisted in Qdrant; a run embeds only its QUERY (the job posting) and
retrieves cosine scores from the store. Ingest-on-write (signals ->
`sync_user_vectors`) keeps the store warm; `reconcile()` at query time is the
correctness backstop (content hashes decide what is stale). Every failure path
returns None/False and the caller degrades to the pre-store full per-run embed —
a dead store must never fail a run.
"""

import hashlib
import logging

from llm_connector import embed
from vector_store import store

logger = logging.getLogger(__name__)

# The two corpora sharing a collection; `doc` in the point payload keeps them apart.
DOC_CV = "cv"


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _uid(user):
    return getattr(user, "pk", user)


def collection_for() -> str | None:
    """The tower embed model's collection (floors + vectors are embedder-specific;
    a model switch on the HirschAI row lands in a fresh collection). None -> off."""
    from llm_connector.conf import hirschai_row

    try:
        cfg = hirschai_row().to_config_dict()
    except Exception:  # noqa: BLE001 — no row -> classic path
        return None
    model = cfg.get("embed_model") or cfg.get("model") or ""
    return store.collection_name(model) if model else None


def reconcile(user, doc: str, desired: dict, delete_orphans=False) -> bool:
    """Bring the user's `doc` corpus in line with `desired` ({entry_id: text}):
    embed and upsert entries whose content hash is missing or stale; optionally
    drop points absent from `desired`.

    `delete_orphans` is only correct when `desired` is the user's FULL set (the
    sync task / vector_sync command). The query path passes a filtered subset
    (domain/date filters) and must leave the other points alone.
    """
    client = store.get_client()
    if client is None or user is None:
        return False
    name = collection_for()
    if not name:
        return False
    uid = _uid(user)
    try:
        have = store.stored_hashes(client, name, uid, doc)
        stale = [
            eid for eid, text in desired.items() if have.get(eid) != content_hash(text)
        ]
        if stale:
            vecs = embed(
                inputs=[desired[eid] for eid in stale],
            )
            if len(vecs) != len(stale):
                return False
            store.ensure_collection(client, name, dim=len(vecs[0]))
            store.upsert(
                client,
                name,
                uid,
                doc,
                [
                    {"id": eid, "hash": content_hash(desired[eid]), "vec": vec}
                    for eid, vec in zip(stale, vecs)
                ],
            )
        if delete_orphans:
            orphans = [eid for eid in have if eid not in desired]
            if orphans:
                store.delete(client, name, uid, doc, orphans)
        return True
    except Exception:  # noqa: BLE001 — any store/embed failure -> classic path
        logger.exception("vector reconcile failed (doc=%s)", doc)
        return False


def ranked_via_store(
    query_text: str,
    entries: list,
    doc,
    user,
) -> list | None:
    """Store-backed replacement for the per-run full embed. Reconciles the run's
    (possibly filtered) entry subset, embeds ONLY the query, and cosine-searches
    scoped to exactly these ids. Returns Embed.ranked_vectors' shape —
    [{id, score, vec}] in entry order — or None (caller runs the classic path).
    """
    if not store.is_enabled() or user is None or not entries:
        return None
    desired = {e["id"]: e.get("text") or "" for e in entries}
    if not reconcile(user, doc, desired):
        return None
    client = store.get_client()
    name = collection_for() or ""
    try:
        qvecs = embed(
            inputs=[query_text],
        )
        if len(qvecs) != 1:
            return None
        hits = store.search(
            client, name, _uid(user), doc, qvecs[0], list(desired), with_vectors=True
        )
    except Exception:  # noqa: BLE001 — any store/embed failure -> classic path
        logger.exception("vector search failed (doc=%s)", doc)
        return None
    by_id = {h["id"]: h for h in hits}
    if set(by_id) != set(desired):
        # a hole right after a succfessful reconcile is real inconsistency —
        # don't guess scores, run the classic path
        logger.warning("vector search incomplete (doc=%s) — falling back", doc)
        return None
    return [
        {"id": e["id"], "score": by_id[e["id"]]["score"], "vec": by_id[e["id"]]["vec"]}
        for e in entries
    ]


def cv_corpus(user_id) -> dict:
    """{entry_id: text} for the user's FULL flattened career DB (no filters)."""
    from jac.cv import CV

    return {e["id"]: e["text"] for e in CV(user_pk=_uid(user_id))._flatten_entries()}
