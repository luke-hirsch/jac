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
