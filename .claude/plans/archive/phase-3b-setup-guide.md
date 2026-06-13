# Phase 3b setup guide — backend API ergonomics (jac)

Goal: make the jac API say in one request what the frontend currently fakes with N. By the end of this phase every jac list resource answers `POST /api/jac/<resource>/bulk/` with a single transactional bulk **delete** or bulk **domain add/remove**, replacing the client-side `Promise.all` fan-out in `useBulkDestroy` / `useBulkPatchDomains`; every viewset declares an explicit `ordering_fields` allow-list (including `updated_at`, which the `/cv` dashboard sorts on but no viewset currently permits); and the `Domain` serializer carries a read-only `is_default` flag so a picker can tell a shared system default from a user-owned tag. The frontend's two bulk hooks are rewired to the new endpoint — same signatures, so no call site changes.

This is **Phase 3b only** — three tightly-coupled backend refactors (one new shared viewset action, an ordering allow-list, one serializer field) plus the thin frontend rewire of the two existing hooks. It deliberately does **not** touch: inline-Skill-create from the picker (a frontend editor improvement — next frontend slice), multipart/avatar upload (folded into 3c hardening, which already owns file-upload validation), throttles / N+1 / security headers (3c), the SPA backend (3d), or any new model (3a shipped the last of those). If you find yourself adding a dependency or a model, you're out of scope for 3b.

Run every command from `backend/` (frontend steps from `frontend/`) unless stated otherwise. If a step's "verify" check fails, stop and fix before moving on.

---

## 0. Preflight

Phase 3a must be committed and green. As of writing 3a's code is in the working tree **uncommitted** — commit it first (it is its own sub-phase per the working-style rule); never stack 3b on top of an uncommitted 3a.

```bash
cd backend
git log --oneline -1            # expect the "Phase 3a: …" commit — NOT 3814ab3
python manage.py makemigrations --check --dry-run
# expect "No changes detected" — 3b adds no model fields, so this must stay clean start to finish
python manage.py test
# expect "Ran N tests … OK" (N > 163 after 3a's additions)
```

Confirm the surfaces we're extending answer (server running in another terminal). These are **anonymous** `curl`s — no session — so they only probe *route existence*, which is all we need here: a route that exists but needs auth answers `401`/`403`, a route that doesn't exist answers `404`. (Authenticated behaviour is verified later through the DRF browsable API / the frontend, where your browser session actually applies — a terminal `curl` has none of your browser cookies.)

```bash
# the list endpoint we'll add `bulk` to — exists, needs auth → 401/403 (NOT 404)
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/jac/skills/
# the bulk route does NOT exist yet → 404
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/jac/skills/bulk/
```

`404` on `bulk/` now is the baseline; by the end the same anonymous call answers `401`/`403` (route exists, auth required). If `makemigrations --check` reports drift, resolve it before starting.

---

## 1. The contract you're coding against

Three changes, each with a precise API consequence. Pin them before writing code.

### 1.1. `POST /api/jac/<resource>/bulk/`

One `detail=False` action on every jac viewset, dispatching on an `action` field. Two operations, the exact two the frontend does today:

```jsonc
// bulk delete — every resource
POST /api/jac/skills/bulk/
{ "action": "delete", "ids": [7, 12, 19] }
// 200 → { "deleted": 3 }

// bulk domain add/remove — only the domain-bearing resources (skills, jobs, projects)
POST /api/jac/jobs/bulk/
{ "action": "patch_domains", "ids": [3, 4], "add": [1], "remove": [2] }
// 200 → { "updated": 2 }
```

Rules, all of which the tests in §6 pin:

- **User-scoped + transactional.** The action operates only over `self.get_queryset()` (the request user's own rows), wrapped in `transaction.atomic()` — all-or-nothing.
- **Unknown / foreign IDs are a hard error, not a silent skip.** If any requested `id` is not in the user's queryset, return `400` (`{"ids": ["…not found…"]}`). Silently dropping foreign IDs hides bugs and makes "I deleted 3 but 2 vanished" un-debuggable. The count-mismatch check is the single line that enforces user isolation here — write it deliberately.
- **`patch_domains` is gated to domain-bearing resources.** On a resource without a `domains` M2M (locations, certifications, education, languages, domains, resume-snippets) it returns `400` (`{"action": ["patch_domains not supported for this resource"]}`). The `add`/`remove` domain IDs are themselves scoped to `Domain.objects.for_user(user)` (own + system defaults); a foreign domain id → `400`.
- **Set semantics match the current client.** `patch_domains` adds the `add` set and removes the `remove` set from each row's existing domains (it does **not** replace) — identical to the `new Set(row.domains)` merge the hook does today.
- **`action` outside `{delete, patch_domains}` → `400`.**

> Two non-obvious choices: (1) one `bulk` action with an `action` discriminator rather than two routes (`bulk-delete` + `bulk-domains`) — it keeps the shared mixin to a single `@action`, and the frontend already branches on operation anyway. (2) `200` (not `204`) on delete so we can return the count the toast wants ("Deleted 3 skills").

### 1.2. Explicit `ordering_fields`

The global `OrderingFilter` is on for every viewset ([settings.py](backend/lukehirsch/settings.py) `DEFAULT_FILTER_BACKENDS`). Where a viewset omits `ordering_fields`, DRF falls back to the serializer's fields — and `updated_at` / `created_at` are **not** serializer fields on the entry models, so the `/cv` dashboard's `?ordering=-updated_at` currently silently does nothing. Lock every viewset to an explicit allow-list that includes the audit timestamps where the model has them.

Effective list after this phase (model-field names, not serializer names — `OrderingFilter` orders by model fields):

| Viewset | `ordering_fields` |
|---|---|
| Domain | `["name"]` (no timestamps on the model) |
| Location | `["city", "country"]` (no timestamps) |
| Education | `["started", "ended", "institution", "field_of_study", "created_at", "updated_at"]` |
| Certification | `["issued_on", "expires_on", "name", "issuer", "created_at", "updated_at"]` |
| Skill | `["name", "first_used", "proficiency", "experience_since", "created_at", "updated_at"]` |
| Job | `["started", "ended", "title", "company", "created_at", "updated_at"]` |
| Project | `["started", "ended", "name", "created_at", "updated_at"]` |
| Language | `["name", "fluency", "created_at", "updated_at"]` |
| ResumeSnippet | unchanged — already `["kind", "title", "created_at", "updated_at"]` |

`?ordering=foo` for a field not on the list now returns the default order (DRF ignores disallowed fields) — the allow-list is the security/stability boundary, not a 400.

### 1.3. `Domain.is_default`

A read-only boolean on `DomainSerializer`: `true` when the row is owned by the `SYSTEM_USER_USERNAME` sentinel (the shared taxonomy), `false` for user-owned rows. The frontend's `DomainPicker` uses it to render a "default" badge / split the list; nothing about scoping changes (writes are still blocked on defaults by `DomainViewSet.get_queryset`).

```jsonc
GET /api/jac/domains/
{ "results": [
  { "id": 1, "name": "Backend", "description": "", "is_default": true  },
  { "id": 9, "name": "My niche", "description": "", "is_default": false }
] }
```

Spec mirror after the changes: <http://localhost:8000/api/docs/> — `is_default` appears on the Domain schema; the `bulk` action shows on each resource (annotate it with `@extend_schema` so the request body type is documented, see §3).

---

## 2. Stack additions

**None.** `transaction.atomic`, `@action`, and `SerializerMethodField` are all stock Django + DRF. No new dependency.

---

## 3. Backend — the bulk action mixin

[backend/jac/views.py](backend/jac/views.py). Write the action **once** as a mixin and apply it to every jac `ModelViewSet`, so the behaviour (scoping, transaction, error shape) can't drift between resources.

Add the imports at the top:

```python
from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers, status
from rest_framework.decorators import action

from jac.models import Domain  # already imported — reuse
```

Then the mixin (place above `DomainViewSet`):

```python
class BulkActionsMixin:
    """Adds `POST <resource>/bulk/` to a ModelViewSet: one transactional
    request for bulk delete or bulk domain add/remove, scoped to the request
    user's own rows via `get_queryset()`.

    `patch_domains` only applies to resources whose model has a `domains`
    M2M (skills/jobs/projects); elsewhere it 400s. Domain IDs in add/remove are
    scoped to the user's own + system-default domains, same as the serializers.
    """

    @extend_schema(
        request=inline_serializer(
            "BulkAction",
            {
                "action": drf_serializers.ChoiceField(["delete", "patch_domains"]),
                "ids": drf_serializers.ListField(child=drf_serializers.IntegerField()),
                "add": drf_serializers.ListField(
                    child=drf_serializers.IntegerField(), required=False
                ),
                "remove": drf_serializers.ListField(
                    child=drf_serializers.IntegerField(), required=False
                ),
            },
        ),
        responses=OpenApiResponse(description="{'deleted': n} or {'updated': n}"),
    )
    @action(detail=False, methods=["post"])
    def bulk(self, request):
        op = request.data.get("action")
        ids = request.data.get("ids") or []
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            return Response(
                {"ids": ["Expected a list of integer IDs."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Scope to the user's own rows; any id outside this set is a hard error.
        qs = self.get_queryset().filter(pk__in=ids)
        found = {obj.pk for obj in qs}
        missing = [i for i in ids if i not in found]
        if missing:
            return Response(
                {"ids": [f"Not found or not yours: {missing}"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if op == "delete":
            with transaction.atomic():
                count, _ = qs.delete()
            return Response({"deleted": len(found)})

        if op == "patch_domains":
            model = self.get_queryset().model
            if not hasattr(model, "domains"):
                return Response(
                    {"action": ["patch_domains not supported for this resource."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            allowed = set(
                Domain.objects.for_user(request.user).values_list("pk", flat=True)
            )
            add = request.data.get("add") or []
            remove = request.data.get("remove") or []
            bad = [d for d in (*add, *remove) if d not in allowed]
            if bad:
                return Response(
                    {"domains": [f"Not found or not yours: {bad}"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            with transaction.atomic():
                for obj in qs:
                    if add:
                        obj.domains.add(*add)
                    if remove:
                        obj.domains.remove(*remove)
            return Response({"updated": len(found)})

        return Response(
            {"action": ['Expected "delete" or "patch_domains".']},
            status=status.HTTP_400_BAD_REQUEST,
        )
```

> `hasattr(model, "domains")` is the gate: `ManyToManyField` puts a descriptor on the model class, so this is `True` for Skill/Job/Project and `False` for the rest — no per-viewset config to keep in sync.

Mix it into every concrete viewset (it's harmless on the domain-less ones — they just 400 `patch_domains`). Apply it to all so bulk-delete is uniform:

```python
class DomainViewSet(BulkActionsMixin, viewsets.ModelViewSet):
    ...
class LocationViewSet(BulkActionsMixin, viewsets.ModelViewSet):
    ...
# …and so on for Education, Certification, Skill, Job, Project, Language, ResumeSnippet
```

Add the `ordering_fields` from §1.2 to each viewset in the same edit (Domain + Location gain one for the first time).

**Verify.** These need a real session, so use the **DRF browsable API** — log into the SPA in your browser first (so the session + CSRF cookies are set), then open <http://localhost:8000/api/jac/skills/bulk/>. DRF renders a POST form whose "Content" box takes the JSON body and whose submit carries your session + CSRF automatically — no `cookies.txt`, no hand-copied token. (Equivalently, drive it from the frontend's Network tab, or write it as a test per §6 — but the browsable API is the fastest manual check.)

- `{"action":"delete","ids":[<a throwaway skill id you own>]}` → `200 {"deleted":1}`; the skill is gone.
- `{"action":"delete","ids":[999999]}` (nonexistent) → `400`; nothing deleted.
- At <http://localhost:8000/api/jac/languages/bulk/> (no `domains` M2M): `{"action":"patch_domains","ids":[<a language id>],"add":[1],"remove":[]}` → `400`.

The authoritative versions of all three live in §6 as tests, where the session is the test client's — run those if the browsable API is fiddly.

---

## 4. Backend — `Domain.is_default`

[backend/jac/serializers.py](backend/jac/serializers.py). Add the computed field; compare against the configured sentinel username (don't hardcode the string).

```python
from django.conf import settings


class DomainSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    is_default = serializers.SerializerMethodField()

    class Meta:
        model = Domain
        fields = ["id", "name", "description", "is_default", "user"]
        read_only_fields = ["id", "is_default"]
        validators = [
            UniqueTogetherValidator(
                queryset=Domain.objects.all(),
                fields=["user", "name"],
            )
        ]

    def get_is_default(self, obj) -> bool:
        return obj.user.username == settings.SYSTEM_USER_USERNAME
```

> `obj.user.username` touches the user FK. The Domain list isn't `select_related("user")` today; the N+1 cleanup for it lives in 3c's audit. Flagged there, not fixed here — keep 3b's blast radius to the three contracts. (For a 50-row page it's 50 cheap PK lookups; acceptable for one phase.)

**Verify:** `GET /api/jac/domains/` → each row has `is_default`; the seeded system defaults read `true`, your own tags `false`. `PATCH`-ing `is_default` is ignored (read-only).

---

## 5. Frontend — rewire the two hooks

[frontend/src/lib/queries/jac.ts](frontend/src/lib/queries/jac.ts). The hook **signatures don't change**, so no call site (BulkBar etc.) is touched — only the `mutationFn` bodies collapse from a fan-out to one request. Add a `bulkUrl` helper and rewrite both:

```typescript
function bulkUrl(key: ResourceKey) {
  return `${R[key].url}bulk/`;
}

export function useBulkDestroy(key: ResourceKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: number[]) =>
      api<{ deleted: number }>(bulkUrl(key), {
        method: "POST",
        body: JSON.stringify({ action: "delete", ids }),
      }),
    onSuccess: () => invalidateResource(qc, key),
  });
}

export function useBulkPatchDomains(
  key: Extract<ResourceKey, "skills" | "jobs" | "projects">,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      ids,
      add,
      remove,
    }: {
      ids: number[];
      add: number[];
      remove: number[];
    }) =>
      api<{ updated: number }>(bulkUrl(key), {
        method: "POST",
        body: JSON.stringify({ action: "patch_domains", ids, add, remove }),
      }),
    onSuccess: () => invalidateResource(qc, key),
  });
}
```

Add `is_default` to the `DomainRow` type so the picker can read it:

```typescript
export type DomainRow = {
  id: number;
  name: string;
  description: string;
  is_default: boolean;
};
```

> The badge/split UI in `DomainPicker` is optional polish — wiring the field through is the 3b deliverable; whether the picker renders a Badge can ride along or wait for the next frontend slice. Don't expand 3b to restyle the picker.

**Verify:**

```bash
cd frontend
npx tsc -b        # zero output — DomainRow + hook return types still satisfy call sites
```

In the app: select several rows on `/cv/skills`, Delete via the BulkBar → **Network tab shows one** `POST /api/jac/skills/bulk/` (not N DELETEs) → rows vanish, toast fires. Same for bulk add-domain on `/cv/jobs` — one `POST …/bulk/`.

---

## 6. Tests

[backend/jac/tests.py](backend/jac/tests.py). Mirror the file's existing idioms exactly — they're already established by the 3a tests right above where these land:

- `APITestCase`, fixtures in a `setUpTestData` classmethod, `self.client.force_login(...)` in `setUp`.
- list responses are paginated → read `r.data["results"]`; write requests pass `format="json"`.
- users via `User.objects.create_user(username=..., password="pass")`.

Add four test classes (after the Phase 3a block, with a `# Phase 3b` banner comment like the existing section dividers). The imports they need — `from django.conf import settings`, `from django.utils import timezone`, and `import datetime` — go at the top of the file alongside the existing ones.

### 6.1. Bulk delete

The core: the **pre-flight scope check rejects the whole batch before any row is touched**, so a single bad id aborts the lot. (`transaction.atomic` around `qs.delete()` is belt-and-suspenders for a failure *during* a valid delete — the all-or-nothing you can observe in tests comes from the early `400`, not a mid-delete rollback. Say it that way so the test's intent is clear.)

```python
class BulkDeleteAPITests(APITestCase):
    """`POST <resource>/bulk/ {"action":"delete"}` removes the user's own rows
    in one request, and refuses the whole batch if any id isn't theirs.
    """

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="bulk_alice", password="pass")
        cls.bob = User.objects.create_user(username="bulk_bob", password="pass")
        cls.s1 = Skill.objects.create(user=cls.alice, name="Python")
        cls.s2 = Skill.objects.create(user=cls.alice, name="Django")
        cls.bob_skill = Skill.objects.create(user=cls.bob, name="Rust")

    def setUp(self):
        self.client.force_login(self.alice)

    def test_bulk_delete_removes_own_rows(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": [self.s1.pk, self.s2.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"deleted": 2})
        self.assertFalse(Skill.objects.filter(pk__in=[self.s1.pk, self.s2.pk]).exists())

    def test_nonexistent_id_aborts_whole_batch(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": [self.s1.pk, 999999]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("ids", r.data)
        self.assertTrue(Skill.objects.filter(pk=self.s1.pk).exists())  # nothing deleted

    def test_cannot_delete_another_users_row(self):
        # bob's id is "missing" from alice's get_queryset() → 400, his row intact.
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": [self.s1.pk, self.bob_skill.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Skill.objects.filter(pk=self.bob_skill.pk).exists())

    def test_unknown_action_is_400(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "nope", "ids": [self.s1.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_non_integer_ids_is_400(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": ["not-an-int"]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
```

`test_cannot_delete_another_users_row` is the one that earns its place — if it ever passes by *succeeding* (200), the scope filter has been dropped and bulk has become a cross-user delete primitive.

### 6.2. Bulk patch_domains

Set semantics (add the `add` set, remove the `remove` set, **keep everything else**), domain-id scoping (own + system defaults allowed, foreign rejected), and the unsupported-resource gate.

```python
class BulkPatchDomainsAPITests(APITestCase):
    """`patch_domains` merges domains onto the user's rows (add/remove, not
    replace), only accepts domains the user may see, and only exists on
    resources that actually carry a `domains` M2M.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="bpd_user", password="pass")
        cls.other = User.objects.create_user(username="bpd_other", password="pass")
        # A system-default domain (owned by the sentinel) is visible to everyone.
        cls.system = User.objects.create_user(username=settings.SYSTEM_USER_USERNAME)
        cls.d_keep = Domain.objects.create(user=cls.user, name="Keep")
        cls.d_remove = Domain.objects.create(user=cls.user, name="Remove")
        cls.d_add = Domain.objects.create(user=cls.user, name="Add")
        cls.d_default = Domain.objects.create(user=cls.system, name="Backend")
        cls.foreign = Domain.objects.create(user=cls.other, name="Foreign")
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Co", started=date(2022, 1, 1)
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.job.domains.set([self.d_keep, self.d_remove])

    def test_add_and_remove_preserve_the_rest(self):
        r = self.client.post(
            "/api/jac/jobs/bulk/",
            {
                "action": "patch_domains",
                "ids": [self.job.pk],
                "add": [self.d_add.pk],
                "remove": [self.d_remove.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"updated": 1})
        self.assertEqual(
            set(self.job.domains.values_list("pk", flat=True)),
            {self.d_keep.pk, self.d_add.pk},  # kept Keep, gained Add, lost Remove
        )

    def test_system_default_domain_is_allowed(self):
        r = self.client.post(
            "/api/jac/jobs/bulk/",
            {"action": "patch_domains", "ids": [self.job.pk],
             "add": [self.d_default.pk], "remove": []},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.d_default.pk, self.job.domains.values_list("pk", flat=True))

    def test_foreign_domain_is_rejected(self):
        r = self.client.post(
            "/api/jac/jobs/bulk/",
            {"action": "patch_domains", "ids": [self.job.pk],
             "add": [self.foreign.pk], "remove": []},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertNotIn(self.foreign.pk, self.job.domains.values_list("pk", flat=True))

    def test_patch_domains_unsupported_on_domainless_resource(self):
        lang = Language.objects.create(user=self.user, name="German")
        r = self.client.post(
            "/api/jac/languages/bulk/",
            {"action": "patch_domains", "ids": [lang.pk], "add": [], "remove": []},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
```

### 6.3. Ordering allow-list

Use `.update()` (which bypasses `auto_now`) to plant deterministic `updated_at` values — otherwise two rows created microseconds apart give a flaky order. Then assert the *allowed* field actually sorts and the *disallowed* one is silently ignored (falls back to the viewset's default order, **not** a 400).

```python
class OrderingFieldsAPITests(APITestCase):
    """`updated_at` is now an allowed ordering (the `/cv` dashboard relies on
    it); a field outside the allow-list is ignored, not honoured.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="ord_user", password="pass")
        # b is the more-recently-started one, so default order (-started) is [b, a].
        cls.a = Job.objects.create(
            user=cls.user, title="Older", company="Co", started=date(2020, 1, 1)
        )
        cls.b = Job.objects.create(
            user=cls.user, title="Newer", company="Co", started=date(2023, 1, 1)
        )
        now = timezone.now()
        # a was updated most recently; b a day earlier.
        Job.objects.filter(pk=cls.a.pk).update(updated_at=now)
        Job.objects.filter(pk=cls.b.pk).update(updated_at=now - datetime.timedelta(days=1))

    def setUp(self):
        self.client.force_login(self.user)

    def test_ordering_by_updated_at_is_honoured(self):
        r = self.client.get("/api/jac/jobs/?ordering=-updated_at")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertEqual(ids, [self.a.pk, self.b.pk])  # most-recently-updated first

    def test_disallowed_ordering_field_falls_back_to_default(self):
        # `title` is not in ordering_fields. If it were honoured we'd get
        # [Newer, Older] alpha-desc or [Older, Newer] alpha-asc; instead we get
        # the default -started order: [b, a].
        r = self.client.get("/api/jac/jobs/?ordering=title")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertEqual(ids, [self.b.pk, self.a.pk])  # default -started, title ignored
```

> If you'd rather not reason about the default-order fallback, the weaker-but-fine assertion is just `assertEqual(r.status_code, 200)` for the disallowed field — the point is it doesn't 400 and doesn't sort by the rejected column.

### 6.4. `Domain.is_default`

```python
class DomainIsDefaultAPITests(APITestCase):
    """`is_default` is a read-only flag: true for the sentinel-owned shared
    taxonomy, false for the user's own tags, and never writable.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="def_user", password="pass")
        cls.system = User.objects.create_user(username=settings.SYSTEM_USER_USERNAME)
        cls.own = Domain.objects.create(user=cls.user, name="Mine")
        cls.default = Domain.objects.create(user=cls.system, name="Backend")

    def setUp(self):
        self.client.force_login(self.user)

    def test_flag_distinguishes_default_from_own(self):
        r = self.client.get("/api/jac/domains/")
        self.assertEqual(r.status_code, 200)
        flags = {row["id"]: row["is_default"] for row in r.data["results"]}
        self.assertFalse(flags[self.own.pk])
        self.assertTrue(flags[self.default.pk])

    def test_is_default_is_read_only(self):
        r = self.client.patch(
            f"/api/jac/domains/{self.own.pk}/",
            {"is_default": True},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["is_default"])  # computed, not taken from the body
```

> `DomainViewSet.get_queryset` already unions in the sentinel's defaults for `list`, so the default domain shows up in the user's list response without any extra setup — that's what makes `test_flag_distinguishes_default_from_own` able to see both rows at once.

Run:

```bash
python manage.py test
# expect a count above the 3a total (180 → ~195), all OK
```

If a foreign-id bulk test passes when it should 400, the `missing`/count-mismatch check isn't scoping through `get_queryset()` — recheck the mixin. If `test_system_default_domain_is_allowed` 400s, the `add`/`remove` validation is filtering against `Domain.objects.filter(user=...)` instead of `Domain.objects.for_user(...)`.

---

## 7. End-to-end verification — the full loop

Backend running, logged in as a verified user; a second user with their own rows for isolation.

1. **Bulk delete, one request.** `/cv/skills` → select 3 → Delete → Network shows a single `POST …/skills/bulk/` → `{"deleted":3}` → rows gone, toast correct. Reload → still gone (persisted).
2. **All-or-nothing.** In the browsable API at `…/skills/bulk/`, bulk-delete a list mixing a valid id and `999999` → `400` → the valid row is **still there** (reload `/cv/skills`).
3. **Bulk add domain, one request.** `/cv/jobs` → select 2 → add a domain via BulkBar → single `POST …/jobs/bulk/` → both jobs show the domain; a domain not in the `remove` set is untouched.
4. **Cross-user guard.** This one is awkward to do by hand (you'd need two browser sessions) — the authoritative check is the cross-user test in §6 (user A's bulk-delete including a user-B id → `400`, B's row intact). Run the suite; don't fake it manually.
5. **Dashboard sort works.** `/cv` (or `?ordering=-updated_at` on a list) → edit one entry → it jumps to the top of the recently-updated sort. Before 3b this did nothing.
6. **Default badge data.** `GET /api/jac/domains/` → system defaults `is_default:true`, your tags `false`; `tsc -b` clean.
7. **Spec.** <http://localhost:8000/api/docs/> → each resource lists a `bulk` POST; Domain schema shows `is_default`.
8. **Suite.** `python manage.py test` green; `npx tsc -b` zero output.

All eight pass → 3b is done.

---

## 8. What you should have at the end

```
backend/jac/
├── views.py          # BulkActionsMixin (one @action bulk); mixed into every viewset; explicit ordering_fields everywhere
├── serializers.py     # DomainSerializer.is_default (read-only SerializerMethodField)
└── tests.py           # bulk delete/patch_domains (+ isolation + rollback), ordering allow-list, is_default

frontend/src/lib/queries/jac.ts   # useBulkDestroy / useBulkPatchDomains → single POST …/bulk/; DomainRow.is_default
```

No migration (no model changes). Re-run the suite, then commit code + this guide together:

```bash
python manage.py test
cd ../frontend && npx tsc -b && cd ../backend
git add backend/jac/ frontend/src/lib/queries/jac.ts .claude/plans/phase-3b-setup-guide.md CLAUDE.md ../.claude/plans/roadmap-2026-06-02.md
git commit -m "Phase 3b: bulk endpoints + ordering_fields lockdown + Domain is_default"
```

(Update the CLAUDE.md jac/API rows + roadmap "Shipped" list to note the `bulk` action, the ordering allow-list, and `Domain.is_default` before committing.)

---

## 9. Known gaps to revisit

Don't fix in 3b — log for the named later phase:

- **Inline Skill create from the picker (next frontend slice).** The `SkillPicker`'s "create new" path still routes users to `/cv/skills`. Now that Skills carry category + proficiency + the 3a fields, wire a tiny inline mini-form (name + category + proficiency). Pure frontend — out of this backend slice.
- **Multipart parser + avatar upload (3c).** Folded into 3c hardening alongside file-upload validation: `MultiPartParser` on the profile view, `UserProfile.avatar` upload path, a multipart helper in `api.ts`, server-side content-type + size checks.
- **N+1 on Domain list (3c).** `is_default` reads `obj.user.username` without `select_related("user")` — fix in 3c's N+1 audit, not here.
- **`DomainPicker` default badge UI (next frontend slice).** The `is_default` field is now delivered; rendering a Badge / splitting the list is optional polish that can ride the next frontend slice.
- **Bulk action for non-domain bulk edits (later).** `bulk` does delete + domain add/remove only — the two things the UI does today. A general bulk-PATCH (e.g. set `is_active` on many snippets) is speculative; add it when a UI needs it.
- **Filter-input debouncing / markdown raw-HTML safety / dedicated `/cv/locations` editor.** Standing frontend deferrals from 2c — unchanged by 3b.

---

## What's next

**3c — pre-deployment backend hardening**: DRF throttle classes (scoped + anon) on write-heavy and soon-to-be-public endpoints, the N+1 audit (`select_related`/`prefetch_related` on the M2M-heavy jac viewsets — including the `Domain.is_default` lookup this phase introduced), security headers + secure-cookie flags gated on `DEBUG`, tightened serializer/file-upload validation **including the multipart + avatar-upload path relocated here from the original 3b list**, and wider `did_recently_authenticate` reauth coverage across destructive endpoints.
