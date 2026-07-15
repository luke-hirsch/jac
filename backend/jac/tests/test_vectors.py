"""Vector-store RAG paths (jac/vectors.py): content-hash reconciliation, the
store-backed retrieval, the ingest signals, and the sync task.

Everything runs on the in-memory Qdrant (no server) with a deterministic fake
embedder patched over `jac.vectors.embed` (keyword-count vectors — predictable
ranking, no Ollama). `jac.vectors.get_alias_config` is patched alongside so no
test depends on settings.LLM or logs the missing-LLMConfig fallback warning.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from jac import vectors
from jac.models import ResumeSnippet, Skill
from jac.tasks import sync_user_vectors
from vector_store import store

from ._helpers import FAKE_EMBED_CFG, FakeEmbed, _entry, _muted


def _entries():
    return [
        _entry("skill:1", "skill", text="python django backend"),
        _entry("skill:2", "skill", text="kitchen cooking"),
    ]


def _desired():
    return {e["id"]: e["text"] for e in _entries()}


class _StoreCase(TestCase):
    """Shared plumbing: fresh in-memory store, fake embedder, patched alias
    config. No test methods of its own."""

    def setUp(self):
        super().setUp()
        store.reset_client()
        self.addCleanup(store.reset_client)
        self.fake = FakeEmbed()
        for target, kwargs in (
            ("jac.vectors.embed", {"new": self.fake}),
            ("jac.vectors.get_alias_config", {"return_value": FAKE_EMBED_CFG}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _stored(self, doc=vectors.DOC_CV, user=7):
        name = vectors.collection_for("default", user)
        return store.stored_hashes(store.get_client(), name, user, doc)


@override_settings(VECTOR_STORE=":memory:")
class ReconcileTests(_StoreCase):
    """reconcile(): hashes decide what gets (re-)embedded; orphans only on demand."""

    def test_first_reconcile_embeds_every_entry(self):
        self.assertTrue(vectors.reconcile(7, "default", vectors.DOC_CV, _desired()))
        self.assertEqual(
            sorted(self.fake.embedded_texts), sorted(_desired().values())
        )
        self.assertEqual(set(self._stored()), {"skill:1", "skill:2"})

    def test_unchanged_corpus_embeds_nothing(self):
        vectors.reconcile(7, "default", vectors.DOC_CV, _desired())
        calls_before = len(self.fake.calls)
        self.assertTrue(vectors.reconcile(7, "default", vectors.DOC_CV, _desired()))
        self.assertEqual(len(self.fake.calls), calls_before)

    def test_changed_text_reembeds_only_that_entry(self):
        vectors.reconcile(7, "default", vectors.DOC_CV, _desired())
        changed = _desired()
        changed["skill:1"] = "python django and kitchen duty"
        self.assertTrue(vectors.reconcile(7, "default", vectors.DOC_CV, changed))
        self.assertEqual(self.fake.calls[-1], [changed["skill:1"]])

    def test_subset_reconcile_keeps_the_other_points(self):
        # The query path passes a domain/date-filtered subset — the points of
        # entries outside the subset must survive untouched.
        vectors.reconcile(7, "default", vectors.DOC_CV, _desired())
        subset = {"skill:1": _desired()["skill:1"]}
        self.assertTrue(vectors.reconcile(7, "default", vectors.DOC_CV, subset))
        self.assertEqual(set(self._stored()), {"skill:1", "skill:2"})

    def test_orphans_dropped_only_on_demand(self):
        vectors.reconcile(7, "default", vectors.DOC_CV, _desired())
        subset = {"skill:1": _desired()["skill:1"]}
        self.assertTrue(
            vectors.reconcile(7, "default", vectors.DOC_CV, subset, delete_orphans=True)
        )
        self.assertEqual(set(self._stored()), {"skill:1"})

    def test_docs_reconcile_independently(self):
        vectors.reconcile(7, "default", vectors.DOC_CV, _desired())
        vectors.reconcile(
            7, "default", vectors.DOC_SNIPPET, {"intro:1": "I like my kitchen."}
        )
        # a full-cv resync with orphan deletion must not touch the snippet corpus
        vectors.reconcile(7, "default", vectors.DOC_CV, _desired(), delete_orphans=True)
        self.assertEqual(set(self._stored(doc=vectors.DOC_SNIPPET)), {"intro:1"})

    def test_off_or_userless_is_false(self):
        with override_settings(VECTOR_STORE=""):
            self.assertFalse(vectors.reconcile(7, "default", vectors.DOC_CV, _desired()))
        self.assertFalse(vectors.reconcile(None, "default", vectors.DOC_CV, _desired()))

    def test_embedder_failure_is_contained(self):
        with _muted(), patch("jac.vectors.embed", side_effect=RuntimeError("down")):
            self.assertFalse(vectors.reconcile(7, "default", vectors.DOC_CV, _desired()))


@override_settings(VECTOR_STORE=":memory:")
class RankedViaStoreTests(_StoreCase):
    """ranked_via_store(): the RAG read path behind Embed.ranked_vectors."""

    def _ranked(self, query, user=7, entries=None):
        # `entries=[]` must reach ranked_via_store as an empty list (the
        # "empty corpus -> None" case); only a missing arg means "use the
        # default two". `entries or _entries()` would swallow the explicit [].
        if entries is None:
            entries = _entries()
        return vectors.ranked_via_store(
            query, entries, doc=vectors.DOC_CV, user=user, alias="default"
        )

    def test_disabled_store_returns_none(self):
        with override_settings(VECTOR_STORE=""):
            self.assertIsNone(self._ranked("python"))

    def test_userless_or_empty_returns_none(self):
        self.assertIsNone(self._ranked("python", user=None))
        self.assertIsNone(self._ranked("python", entries=[]))

    def test_output_matches_ranked_vectors_shape_in_entry_order(self):
        out = self._ranked("python django posting")
        self.assertEqual([r["id"] for r in out], ["skill:1", "skill:2"])
        for r in out:
            self.assertIsInstance(r["score"], float)
            self.assertTrue(r["vec"])
        by_id = {r["id"]: r["score"] for r in out}
        self.assertGreater(by_id["skill:1"], by_id["skill:2"])

    def test_warm_store_embeds_only_the_query(self):
        self._ranked("python django")
        calls_before = len(self.fake.calls)
        out = self._ranked("kitchen work")
        # the RAG win: one embed call, one input — the query, never the corpus
        self.assertEqual(self.fake.calls[calls_before:], [["kitchen work"]])
        by_id = {r["id"]: r["score"] for r in out}
        self.assertGreater(by_id["skill:2"], by_id["skill:1"])

    def test_users_get_their_own_corpora(self):
        self._ranked("python", user=7)
        texts_before = len(self.fake.embedded_texts)
        self.assertIsNotNone(self._ranked("python", user=8))
        # nothing shared with user 7: user 8's two entries + their query
        self.assertEqual(len(self.fake.embedded_texts) - texts_before, 3)

    def test_store_failure_returns_none(self):
        with _muted(), patch(
            "jac.vectors.store.search", side_effect=RuntimeError("qdrant down")
        ):
            self.assertIsNone(self._ranked("python"))


@override_settings(VECTOR_STORE=":memory:")
class VectorSyncSignalTests(TestCase):
    """Career-DB / snippet writes queue a vector resync for their owner."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="vecsignal")

    def test_entry_save_queues_a_resync(self):
        with patch("jac.tasks.sync_user_vectors.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                Skill.objects.create(user=self.user, name="Python")
        delay.assert_called_once_with(self.user.pk)

    def test_entry_delete_queues_a_resync(self):
        skill = Skill.objects.create(user=self.user, name="Django")
        with patch("jac.tasks.sync_user_vectors.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                skill.delete()
        delay.assert_called_once_with(self.user.pk)

    def test_snippet_save_queues_a_resync(self):
        with patch("jac.tasks.sync_user_vectors.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                ResumeSnippet.objects.create(user=self.user, title="t", content="c")
        delay.assert_called_once_with(self.user.pk)

    def test_disabled_store_stays_silent(self):
        with override_settings(VECTOR_STORE=""):
            with patch("jac.tasks.sync_user_vectors.delay") as delay:
                with self.captureOnCommitCallbacks(execute=True):
                    Skill.objects.create(user=self.user, name="Vue")
        delay.assert_not_called()

    def test_broker_failure_never_breaks_the_save(self):
        with _muted(), patch(
            "jac.tasks.sync_user_vectors.delay", side_effect=RuntimeError("broker down")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                Skill.objects.create(user=self.user, name="Kafka")
        self.assertTrue(Skill.objects.filter(user=self.user, name="Kafka").exists())


@override_settings(VECTOR_STORE=":memory:")
class SyncUserVectorsTaskTests(_StoreCase):
    """sync_user_vectors: full-corpus ingest with orphan cleanup."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="vectask")
        self.skill = Skill.objects.create(user=self.user, name="Python")
        self.snippet = ResumeSnippet.objects.create(
            user=self.user,
            title="Intro",
            content="I cook.",
            kind=ResumeSnippet.Kind.intro,
        )

    def test_ingests_both_corpora(self):
        sync_user_vectors(self.user.pk)
        self.assertIn(
            f"skill:{self.skill.pk}", self._stored(user=self.user.pk)
        )
        self.assertIn(
            f"intro:{self.snippet.pk}",
            self._stored(doc=vectors.DOC_SNIPPET, user=self.user.pk),
        )

    def test_resync_drops_orphans(self):
        sync_user_vectors(self.user.pk)
        sid = f"skill:{self.skill.pk}"
        self.skill.delete()
        sync_user_vectors(self.user.pk)
        self.assertNotIn(sid, self._stored(user=self.user.pk))

    def test_inactive_snippets_are_not_ingested(self):
        self.snippet.is_active = False
        self.snippet.save()
        sync_user_vectors(self.user.pk)
        self.assertEqual(self._stored(doc=vectors.DOC_SNIPPET, user=self.user.pk), {})

    def test_disabled_store_is_a_noop(self):
        with override_settings(VECTOR_STORE=""):
            sync_user_vectors(self.user.pk)  # must not raise
        self.assertEqual(self.fake.calls, [])
