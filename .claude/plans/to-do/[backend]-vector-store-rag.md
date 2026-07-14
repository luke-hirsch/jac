# [backend] Qdrant vector store — a proper RAG read path for the career DB

**Branch:** `backend/vector-store-rag`

## Context / goal

Today `Embed._query()` (`backend/jac/llm_prompts.py`) re-embeds the **entire corpus** — every
flattened career entry plus the posting, N+1 inputs — on every generation run, and
`SnippetEmbed` does the same for cover-letter snippets. A classic RAG setup embeds the corpus
once at **ingest** time, persists the vectors, and embeds only the **query** per request.

This guide adds that split, deliberately as a full vector-DB artifact (jac is itself a CV
entry — the ingest → store → filtered-retrieval loop is the showcase, not just a cache):

- **Engine: Qdrant.** Self-hostable industry standard; the Python client has an *embedded local
  mode* (`QdrantClient(path=…)` / `":memory:"`) so dev needs no new process today and tests need
  no server; prod becomes one docker-compose service later (env flip, no code change). It also
  decouples vector storage from the relational engine (works for Postgres prod *and* a future
  SQLite-based local build).
- **Write path (ingest):** `post_save`/`post_delete` on career-DB models + `ResumeSnippet`
  enqueue a Celery task that reconciles the user's full corpora against the store.
- **Read path:** `Embed.ranked_vectors()` goes store-first — reconcile the run's entry subset by
  content hash (the correctness backstop), embed **only the query**, cosine-search scoped to
  exactly those ids. Any failure falls back to the classic full per-run embed: **a dead store
  never fails a run.**

**Scope boundary:** this only changes where embedding vectors come from. The `standard`/`strong`
rungs still put entry text into their prompts — the LLM there is a reranker/selector that must
read the text. `CVFilter` selection logic (propagation, floors, min-keep) is untouched, and
Qdrant's COSINE scores are the same cosine similarity `Embed._cos` computes, so the calibrated
per-section floors keep working unchanged.

**Deployment constraint (know it before you type):** embedded mode locks its storage directory —
**one process only**. All store access happens in the celery worker (signal task, generation
runs), a management command, or tests — never in runserver (signals only *enqueue*). In dev,
run the worker single-process (`--pool=solo`). The compose phase replaces the path with a server
URL and the constraint disappears.

## Affected files

New (you type):

| path | why |
| --- | --- |
| `backend/vector_store/apps.py` | app config for the new generic store app |
| `backend/vector_store/store.py` | Qdrant wrapper: client factory, collections, user-scoped point ops |
| `backend/jac/vectors.py` | the RAG paths: content hashes, `reconcile()`, `ranked_via_store()`, corpus builders |
| `backend/jac/signals.py` | ingest-on-write: entry/snippet saves queue a vector resync |
| `backend/jac/management/commands/vector_sync.py` | backfill / rebuild command |

Modified (you type):

| path | why |
| --- | --- |
| `backend/lukehirsch/settings.py` | `vector_store` in INSTALLED_APPS + `VECTOR_STORE` env setting |
| `backend/jac/apps.py` | `ready()` connects the signals |
| `backend/jac/llm_prompts.py` | `Embed`: `DOC_KIND`, store-first `ranked_vectors()`, `_query_text()` split |
| `backend/jac/tasks.py` | `sync_user_vectors` task |
| `requirements.txt` | `qdrant-client` |
| `README.md` | `VECTOR_STORE` env + solo-worker note |

Already on disk (AI-written): `backend/vector_store/__init__.py` (docstring marker so the test
package is discoverable) and the test files listed under **Tests**.

## The code

### 1. `requirements.txt`

Append (new section at the end):

```
# Vector store — jac's RAG read path (optional; enable via VECTOR_STORE)
qdrant-client>=1.10
```

Then `pip install -r requirements.txt` (only `qdrant-client` is new). `>=1.10` matters: the code
uses `query_points` / `collection_exists`.

### 2. `backend/lukehirsch/settings.py`

Add `"vector_store"` to `INSTALLED_APPS`, with the local apps:

```python
    # local apps
    "llm_connector",
    "vector_store",
    "jac",
    "spa",
```

Add the setting next to the `LLM` block:

```python
# Qdrant vector store — the RAG read path (jac/vectors.py). Empty = off: every run
# embeds the full corpus per request (the pre-store behaviour). A filesystem path
# runs qdrant-client embedded — SINGLE PROCESS ONLY (the dir is locked), so all
# store access lives in the celery worker; run it `--pool=solo` in dev. An http(s)
# URL targets a qdrant server (the docker-compose phase).
VECTOR_STORE = os.getenv("VECTOR_STORE", "")
```

### 3. `backend/vector_store/apps.py`

```python
from django.apps import AppConfig


class VectorStoreConfig(AppConfig):
    name = "vector_store"
```

(No models, so no migrations directory — Qdrant holds the data.)

### 4. `backend/vector_store/store.py`

Generic primitives, no jac imports (the portfolio generator can reuse them). `qdrant_client`
imports stay inside functions so the app imports cleanly when the optional dependency isn't
installed and the store is off — same "install only what you use" stance as the provider SDKs.

```python
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
    return qm.Filter(must=must)


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
```

### 5. `backend/jac/vectors.py`

The jac-side RAG logic. `reconcile()` is the one function both the ingest task and the query
path funnel through; `delete_orphans` is only safe on the **full** corpus (the query path passes
a domain/date-filtered *subset* and must leave the other points alone — that asymmetry is the
subtlest thing in this guide).

```python
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
from llm_connector.conf import get_alias_config, pick_alias

from vector_store import store

logger = logging.getLogger(__name__)

# The two corpora sharing a collection; `doc` in the point payload keeps them apart.
DOC_CV = "cv"
DOC_SNIPPET = "snippet"


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _uid(user):
    return getattr(user, "pk", user)


def collection_for(alias: str, user) -> str | None:
    """The alias's collection — it follows the resolved embed model (floors are
    calibrated per embedder; a model switch lands in a fresh collection).
    None -> unresolvable, caller falls back."""
    try:
        cfg = get_alias_config(alias, user=user)
    except Exception:  # noqa: BLE001 — unresolvable alias -> classic path
        return None
    model = cfg.get("embed_model") or cfg.get("model") or ""
    return store.collection_name(model) if model else None


def reconcile(user, alias: str, doc: str, desired: dict, *, delete_orphans=False) -> bool:
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
    name = collection_for(alias, user)
    if not name:
        return False
    uid = _uid(user)
    try:
        have = store.stored_hashes(client, name, uid, doc)
        stale = [
            eid for eid, text in desired.items() if have.get(eid) != content_hash(text)
        ]
        if stale:
            vecs = embed(inputs=[desired[eid] for eid in stale], alias=alias, user=user)
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


def ranked_via_store(query_text: str, entries: list, *, doc, user, alias) -> list | None:
    """Store-backed replacement for the per-run full embed. Reconciles the run's
    (possibly filtered) entry subset, embeds ONLY the query, and cosine-searches
    scoped to exactly these ids. Returns Embed.ranked_vectors' shape —
    [{id, score, vec}] in entry order — or None (caller runs the classic path).
    """
    if not store.is_enabled() or user is None or not entries:
        return None
    desired = {e["id"]: e.get("text") or "" for e in entries}
    if not reconcile(user, alias, doc, desired):
        return None
    client = store.get_client()
    name = collection_for(alias, user)
    try:
        qvecs = embed(inputs=[query_text], alias=alias, user=user)
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
        # a hole right after a successful reconcile is real inconsistency —
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


def snippet_corpus(user_id) -> dict:
    """{"kind:pk": content} for the user's active snippets (SnippetSelector's ids)."""
    from jac.models import ResumeSnippet

    return {
        f"{s.kind}:{s.pk}": s.content
        for s in ResumeSnippet.objects.filter(user=_uid(user_id), is_active=True)
    }


def sync_alias(user) -> str:
    """The alias background ingest embeds on: the user's light pin when present,
    else the zero-cost default — the same target the query path resolves to."""
    from jac.llm_prompts import Embed

    return pick_alias(Embed.PREFERRED_GRADE, fallback="default", user=user)
```

### 6. `backend/jac/llm_prompts.py` — `Embed` goes store-first

Three changes inside `class Embed` (and one line in `SnippetEmbed`); everything else in the
file stays untouched.

Add `DOC_KIND` next to `PREFERRED_GRADE`:

```python
    PREFERRED_GRADE: str | None = "light"
    # Which vector-store corpus this rung reads (see jac/vectors.py) — the CV
    # entries; SnippetEmbed overrides for the snippet corpus.
    DOC_KIND = "cv"
```

Replace `ranked_vectors()` (the import stays inside the method — llm_prompts is imported by
half the app and must not pull jac.vectors, and through it qdrant plumbing, at module load):

```python
    def ranked_vectors(self) -> list[dict]:
        """Like ranked_entries, but keeps each entry's raw vector (`vec`) so callers
        can measure entry-to-entry similarity (the cover-letter MMR pick).

        Store-first: when the vector store is enabled, doc vectors come from Qdrant
        and only the query is embedded (jac/vectors.py); on None — store off, no
        user, or any store failure — the classic full per-run embed below runs."""
        from jac.vectors import ranked_via_store

        stored = ranked_via_store(
            self._query_text(),
            self.entries,
            doc=self.DOC_KIND,
            user=self.user,
            alias=self.alias,
        )
        if stored is not None:
            return stored

        vectors = self._query()

        if len(vectors) != len(self.entries) + 1:
            return []
        query_vec, doc_vecs = vectors[0], vectors[1:]
        return [
            {"id": e.get("id"), "score": self._cos(query_vec, dv), "vec": dv}
            for e, dv in zip(self.entries, doc_vecs)
        ]
```

Split the query string out of `_query()` so both paths embed the identical instructed query:

```python
    def _query_text(self) -> str:
        """The instructed query string — the store path embeds ONLY this."""
        return f"Instruct: {self._EMBED_INSTRUCT}\nQuery:{self._cap_job_post()}\n"

    def _query(self) -> list:
        """Classic path: embed the query + every entry text in one batch."""
        return embed(
            inputs=[self._query_text()] + self.flatten_entries,
            alias=self.alias,
            user=self.user,
        )
```

In `SnippetEmbed`, add below `_EMBED_INSTRUCT`:

```python
    DOC_KIND = "snippet"
```

Nothing else changes: `SnippetSelector`'s alias-chain walk, the MMR pick, and `CVFilter` all
consume `ranked_vectors()`'s unchanged shape.

### 7. `backend/jac/tasks.py` — the ingest task

Append at the end of the file:

```python
@shared_task
def sync_user_vectors(user_id: int) -> None:
    """Ingest-on-write for the vector store: refresh one user's corpora (CV
    entries + snippets). Runs on the FULL sets, so orphan deletion is safe here —
    the query-time reconcile only ever upserts. No-op when the store is off;
    reconcile logs its own failures, so a broken store never errors the task."""
    from jac import vectors
    from vector_store import store

    if not store.is_enabled():
        return
    alias = vectors.sync_alias(user_id)
    vectors.reconcile(
        user_id, alias, vectors.DOC_CV, vectors.cv_corpus(user_id), delete_orphans=True
    )
    vectors.reconcile(
        user_id,
        alias,
        vectors.DOC_SNIPPET,
        vectors.snippet_corpus(user_id),
        delete_orphans=True,
    )
```

### 8. `backend/jac/signals.py`

```python
"""Ingest-on-write for the vector store: any change to a career entry or snippet
queues a full vector resync for its owner (see `sync_user_vectors`).

One trigger per save is enough: the task recomputes the complete corpora and
content hashes skip unchanged rows, so over-triggering is cheap. M2M edits that
slip past post_save (a PATCH that only touches `skills`) are caught by the
query-time reconcile — the backstop, not the signals, carries correctness.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save

from jac.models import (
    Certification,
    Education,
    Job,
    Language,
    Project,
    ResumeSnippet,
    Skill,
)

logger = logging.getLogger(__name__)

_WATCHED = (Job, Project, Education, Certification, Skill, Language, ResumeSnippet)


def _enqueue(user_id: int) -> None:
    from jac.tasks import sync_user_vectors

    try:
        sync_user_vectors.delay(user_id)
    except Exception:  # noqa: BLE001 — a down broker must never break a save
        logger.warning("vector sync enqueue failed for user %s", user_id)


def queue_vector_sync(sender, instance, **kwargs):
    from vector_store import store

    if not store.is_enabled():
        return
    user_id = instance.user_id
    transaction.on_commit(lambda: _enqueue(user_id))


def connect() -> None:
    for model in _WATCHED:
        post_save.connect(
            queue_vector_sync,
            sender=model,
            dispatch_uid=f"jac.vector_sync.save.{model.__name__}",
        )
        post_delete.connect(
            queue_vector_sync,
            sender=model,
            dispatch_uid=f"jac.vector_sync.delete.{model.__name__}",
        )
```

### 9. `backend/jac/apps.py`

```python
from django.apps import AppConfig


class JacConfig(AppConfig):
    name = 'jac'

    def ready(self):
        from jac import signals

        signals.connect()
```

### 10. `backend/jac/management/commands/vector_sync.py`

```python
"""Backfill / rebuild the Qdrant vector store from the career DB.

The batch counterpart of the save-signal ingest: bring every user's corpora
(CV entries + snippets) in line with the DB. `--drop` wipes the user's points
first — full rebuild after a schema change, a corrupted store, or an embedder
switch cleanup.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from vector_store import store

from jac import vectors


class Command(BaseCommand):
    help = "Sync the vector store with the career DB (all users or --user)."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="username or pk (default: every user)")
        parser.add_argument(
            "--drop",
            action="store_true",
            help="delete the user's stored points first (full rebuild)",
        )

    def handle(self, *args, **opts):
        if not store.is_enabled():
            raise CommandError("VECTOR_STORE is not configured — nothing to sync.")
        for user in self._users(opts.get("user")):
            alias = vectors.sync_alias(user)
            name = vectors.collection_for(alias, user)
            if opts["drop"] and name is not None:
                client = store.get_client()
                for doc in (vectors.DOC_CV, vectors.DOC_SNIPPET):
                    store.delete(client, name, user.pk, doc)
            corpora = {
                vectors.DOC_CV: vectors.cv_corpus(user.pk),
                vectors.DOC_SNIPPET: vectors.snippet_corpus(user.pk),
            }
            for doc, desired in corpora.items():
                ok = vectors.reconcile(user, alias, doc, desired, delete_orphans=True)
                status = f"{len(desired)} entries" if ok else "FAILED"
                self.stdout.write(f"{user.username} [{doc}]: {status}")

    def _users(self, ident):
        if not ident:
            return list(User.objects.order_by("pk"))
        qs = (
            User.objects.filter(pk=ident)
            if str(ident).isdigit()
            else User.objects.filter(username=ident)
        )
        user = qs.first()
        if user is None:
            raise CommandError(f"No user {ident!r}.")
        return [user]
```

### 11. `README.md`

In **Run (dev)**, extend the worker step (4) and add the env var:

```bash
# 4. Generation worker — REQUIRED for CV / cover-letter runs.
#    With VECTOR_STORE set to a path (embedded Qdrant), the worker must be the
#    ONLY process touching that path: run it --pool=solo.
cd backend && celery -A lukehirsch worker -l info --pool=solo
```

Plus a short paragraph (e.g. after the run block):

> **Vector store (optional):** set `VECTOR_STORE` to enable the RAG read path — a filesystem
> path (e.g. `~/.jac-qdrant`) runs Qdrant embedded (single process: worker only, `--pool=solo`),
> an `http(s)://` URL targets a Qdrant server (docker-compose phase; dashboard at
> `:6333/dashboard`). Unset = every run embeds the full corpus per request. Backfill with
> `python manage.py vector_sync` (stop the worker first in embedded mode — the dir is locked).

## Tests (already on disk, start red)

Run:

```bash
cd backend
python manage.py test vector_store jac.tests.test_vectors jac.tests.test_llm_rungs jac.tests.test_commands
python manage.py test   # full suite once green
```

| file | covers |
| --- | --- |
| `backend/vector_store/tests/test_store.py` | store primitives on the in-memory Qdrant: off/enabled client factory + singleton/reset, collection-name slug, deterministic point ids, upsert/overwrite, `stored_hashes`, search scoped to exactly the requested ids and never across user/doc, normalised vectors on request, scoped + whole-scope delete |
| `backend/jac/tests/test_vectors.py` | `reconcile()` embeds only missing/stale (hash-driven), orphan deletion only on demand and never for subsets; `ranked_via_store()` shape/order, **warm store embeds only the query**, per-user isolation, None on disabled/no-user/store-failure; ingest signals queue `sync_user_vectors` on save/delete (and stay silent when off / never break a save); the task ingests both corpora and drops orphans |
| `backend/jac/tests/test_llm_rungs.py` (appended `EmbedStorePathTests`) | `DOC_KIND` declarations; `Embed.ranked_vectors` skips the classic batch when the store serves; falls back to the classic path on a store miss; classic batch unchanged when the store is off (regression guard — this one is green pre-implementation, flagged) |
| `backend/jac/tests/test_commands.py` (appended `VectorSyncCommandTests`) | `vector_sync` refuses without `VECTOR_STORE`; backfills a user's corpus; `--drop` rebuilds |
| `backend/jac/tests/_helpers.py` (appended) | `FakeEmbed` (deterministic keyword-count embedder recording its calls) + `FAKE_EMBED_CFG` |

Red before implementation: everything importing `vector_store.store` / `jac.vectors` errors
(module missing), `DOC_KIND` asserts fail, the command tests fail on the unknown command.
One deliberate exception: `test_store_disabled_keeps_the_classic_batch` is a pre-existing-
behaviour regression guard and passes already.

## Verification (after red → green)

1. `pip install -r requirements.txt`, type the code, watch the four test targets go green, then
   the full suite (clean wall of dots — no stray warnings).
2. **Live ingest + warm read** (dev, embedded mode):
   ```bash
   export VECTOR_STORE=$HOME/.jac-qdrant
   cd backend && python manage.py vector_sync --user <you>   # worker stopped — dir lock
   ```
   Expect one line per corpus (`<you> [cv]: N entries`, `[snippet]: M`). Re-run it: same output,
   but instantly (hashes skip everything — watch `ollama ps`/logs: no embed traffic).
3. Start the stack with the worker as `celery -A lukehirsch worker -l info --pool=solo` (same
   `VECTOR_STORE` exported) and run a generation from the SPA — it must complete normally.
   Edit one skill in the CV UI → the worker log shows a `sync_user_vectors` task run.
4. **Fallback:** stop/break the store (`export VECTOR_STORE=http://localhost:9`, restart the
   worker) and run a generation — it completes on the classic path; the worker log shows the
   "vector reconcile failed"/"vector search failed" warning, nothing user-facing breaks.
5. Optional inspection (worker stopped, embedded mode):
   ```bash
   python manage.py shell -c "from vector_store import store; c = store.get_client(); print(c.get_collections())"
   ```
   When the compose phase lands, point `VECTOR_STORE` at the server URL and browse
   `http://localhost:6333/dashboard` instead.

## Results

<!-- Human fills after testing: raw test output, observed issues, what works. -->
