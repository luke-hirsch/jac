"""Store primitives on the in-memory Qdrant — no server, real engine behaviour.

Every operation in `store` is scoped by user_id + doc; the point of half these
tests is proving that scope never leaks (users can't see each other's vectors,
the cv and snippet corpora stay apart) even though everything shares a collection.
"""

import math

from django.test import SimpleTestCase, override_settings

from vector_store import store


def _vec(*xs):
    return list(xs)


@override_settings(VECTOR_STORE=":memory:")
class StoreClientTests(SimpleTestCase):
    """Client factory: off/enabled, per-process singleton, test reset."""

    def setUp(self):
        store.reset_client()
        self.addCleanup(store.reset_client)

    def test_off_without_setting(self):
        with override_settings(VECTOR_STORE=""):
            self.assertFalse(store.is_enabled())
            self.assertIsNone(store.get_client())

    def test_enabled_yields_a_singleton(self):
        self.assertTrue(store.is_enabled())
        client = store.get_client()
        self.assertIsNotNone(client)
        self.assertIs(store.get_client(), client)

    def test_reset_drops_the_client(self):
        client = store.get_client()
        store.reset_client()
        self.assertIsNot(store.get_client(), client)


class NamingTests(SimpleTestCase):
    """Collection naming + point-id determinism (no client needed)."""

    def test_collection_name_slugs_the_embed_model(self):
        self.assertEqual(
            store.collection_name("qwen3-embedding:0.6b"),
            "entries__qwen3-embedding-0-6b",
        )

    def test_point_id_is_deterministic_and_user_scoped(self):
        pid = store.point_id(1, "job:3")
        self.assertEqual(pid, store.point_id(1, "job:3"))
        self.assertNotEqual(pid, store.point_id(2, "job:3"))
        self.assertNotEqual(pid, store.point_id(1, "job:4"))


@override_settings(VECTOR_STORE=":memory:")
class PointOpsTests(SimpleTestCase):
    """upsert / stored_hashes / search / delete, scoped by user and doc."""

    NAME = "entries__test"

    def setUp(self):
        store.reset_client()
        self.addCleanup(store.reset_client)
        self.client = store.get_client()
        store.ensure_collection(self.client, self.NAME, dim=3)
        store.upsert(
            self.client,
            self.NAME,
            1,
            "cv",
            [
                {"id": "job:1", "hash": "h1", "vec": _vec(1.0, 0.0, 0.0)},
                {"id": "skill:2", "hash": "h2", "vec": _vec(0.0, 1.0, 0.0)},
            ],
        )
        # Same entry id for another user, and another doc for the same user —
        # neither may ever surface in user 1's cv scope.
        store.upsert(
            self.client,
            self.NAME,
            2,
            "cv",
            [{"id": "job:1", "hash": "other", "vec": _vec(0.0, 0.0, 1.0)}],
        )
        store.upsert(
            self.client,
            self.NAME,
            1,
            "snippet",
            [{"id": "intro:9", "hash": "s1", "vec": _vec(0.0, 0.0, 1.0)}],
        )

    def test_ensure_collection_is_idempotent(self):
        store.ensure_collection(self.client, self.NAME, dim=3)  # no raise

    def test_stored_hashes_scoped_to_user_and_doc(self):
        self.assertEqual(
            store.stored_hashes(self.client, self.NAME, 1, "cv"),
            {"job:1": "h1", "skill:2": "h2"},
        )
        self.assertEqual(
            store.stored_hashes(self.client, self.NAME, 2, "cv"), {"job:1": "other"}
        )
        self.assertEqual(
            store.stored_hashes(self.client, self.NAME, 1, "snippet"), {"intro:9": "s1"}
        )

    def test_stored_hashes_on_missing_collection_is_empty(self):
        self.assertEqual(store.stored_hashes(self.client, "entries__nope", 1, "cv"), {})

    def test_upsert_overwrites_instead_of_duplicating(self):
        store.upsert(
            self.client,
            self.NAME,
            1,
            "cv",
            [{"id": "job:1", "hash": "h1b", "vec": _vec(1.0, 0.0, 0.0)}],
        )
        hashes = store.stored_hashes(self.client, self.NAME, 1, "cv")
        self.assertEqual(hashes, {"job:1": "h1b", "skill:2": "h2"})

    def test_search_returns_cosine_scores(self):
        hits = store.search(
            self.client, self.NAME, 1, "cv", _vec(1.0, 0.0, 0.0), ["job:1", "skill:2"]
        )
        by_id = {h["id"]: h["score"] for h in hits}
        self.assertEqual(set(by_id), {"job:1", "skill:2"})
        self.assertAlmostEqual(by_id["job:1"], 1.0, places=5)
        self.assertAlmostEqual(by_id["skill:2"], 0.0, places=5)

    def test_search_restricts_to_the_requested_ids(self):
        hits = store.search(
            self.client, self.NAME, 1, "cv", _vec(1.0, 0.0, 0.0), ["job:1"]
        )
        self.assertEqual([h["id"] for h in hits], ["job:1"])

    def test_search_never_crosses_user_or_doc(self):
        # The query vector matches user 2's point and user 1's snippet perfectly;
        # inside user 1's cv scope both are invisible.
        hits = store.search(
            self.client,
            self.NAME,
            1,
            "cv",
            _vec(0.0, 0.0, 1.0),
            ["job:1", "skill:2", "intro:9"],
        )
        self.assertEqual({h["id"] for h in hits}, {"job:1", "skill:2"})
        self.assertTrue(all(h["score"] < 0.99 for h in hits))

    def test_search_returns_vectors_on_request(self):
        without = store.search(
            self.client, self.NAME, 1, "cv", _vec(1.0, 0.0, 0.0), ["job:1"]
        )
        self.assertEqual(without[0]["vec"], [])
        hits = store.search(
            self.client,
            self.NAME,
            1,
            "cv",
            _vec(1.0, 0.0, 0.0),
            ["job:1"],
            with_vectors=True,
        )
        vec = hits[0]["vec"]
        self.assertTrue(vec)
        self.assertAlmostEqual(math.sqrt(sum(x * x for x in vec)), 1.0, places=5)

    def test_delete_specific_then_whole_scope(self):
        store.delete(self.client, self.NAME, 1, "cv", ["skill:2"])
        self.assertEqual(
            set(store.stored_hashes(self.client, self.NAME, 1, "cv")), {"job:1"}
        )
        store.delete(self.client, self.NAME, 1, "cv")  # entry_ids=None -> whole scope
        self.assertEqual(store.stored_hashes(self.client, self.NAME, 1, "cv"), {})
        # the sibling doc and the other user survive
        self.assertEqual(
            set(store.stored_hashes(self.client, self.NAME, 1, "snippet")), {"intro:9"}
        )
        self.assertEqual(
            set(store.stored_hashes(self.client, self.NAME, 2, "cv")), {"job:1"}
        )

    def test_delete_on_missing_collection_is_a_noop(self):
        store.delete(self.client, "entries__nope", 1, "cv")  # no raise
