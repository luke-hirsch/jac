# [fullstack] Profile identity + CV/letter contact & summary

> **Branch note (read first).** Per Lukas's instruction this guide does **not** cut its own branch —
> we are mid-flight on `fullstack/llm-config-check` with heavy uncommitted parallel work. Type this
> on the current branch; the usual `/wrap-up` "merge the guide branch into main" step does **not**
> apply. Several files this guide touches (`serializers.py`, `templates.tsx`, `letter-doc.ts`,
> `export-card.tsx`, `profile.tsx`, and both test files) are **already modified** on this branch — the
> code below is written to layer onto that in-flight state, not a clean `main`.

## 1. Context / goal

Three coupled asks, all "the profile feeds the exported CV/letter":

1. **Real name.** `User.first_name`/`last_name` aren't editable anywhere in the frontend — only a
   computed read-only `name` (display_name → first+last → username) is served. Make first/last name
   editable via the profile tab so the letter sender name resolves correctly when `display_name` is
   blank. Surface `username` read-only.
2. **Socials on the CV.** The CV template renders *only* the name + sections — no contact line at
   all. Add an opt-in profile toggle `show_socials`; when on, the exported CV gains a contact header
   (`email · phone · website · linkedin · github`).
3. **Bio.** `bio` is already editable in the profile form but never reaches the CV. Render it as a
   short summary paragraph under the name.

Not a roadmap item — a profile/export quality-of-life batch. Backend is already 90% there: the
`UserProfile` address fields, `bio`, and the cover-letter `_sender()` consumption all exist; the
gaps are the User-name write path, the `show_socials` field, and the frontend render + form.

### Design decisions (tunable — flagged so you can change them in one line)

- **`show_socials` is profile-level, not per-application** (your call: "add toggle to profile, share
  in application"). Every application's exported CV reads the one profile flag.
- **Email + phone are always in the CV contact header; only website/linkedin/github are gated** by
  `show_socials`. Rationale: a CV needs baseline contact; "socials" is the opt-in part. If you'd
  rather gate the whole header, drop `email`/`phone` out of the always-on list in `contactLine`.
- **Scope is CV-only for the socials header.** The *letter* footer keeps its current behaviour
  (`email · phone · website`, unconditional) — untouched here. `senderFromProfile` still starts
  mapping `linkedin`/`github` into the sender block (harmless to the letter, needed by the CV).
- **`username` is read-only.** Changing it has allauth/login implications not worth opening up.

## 2. Affected files

| file | change |
| --- | --- |
| `backend/spa/models.py` | add `show_socials = BooleanField(default=False)` to `UserProfile` |
| `backend/spa/migrations/000X_*` | generated migration for the new field |
| `backend/spa/serializers.py` | writable `first_name`/`last_name` (source `user.*`) + read-only `username` + `show_socials` in `fields`; drop `user = HiddenField`; custom `update()` saves the nested User |
| `backend/spa/tests/test_auth.py` | **(AI-written, red)** first/last write-through, username read-only, show_socials toggle |
| `frontend/src/lib/queries/profile.ts` | `ProfileRow` gains `username`, `first_name`, `last_name`, `bio`, `show_socials` |
| `frontend/src/lib/letter-doc.ts` | `senderFromProfile` maps `linkedin`/`github`; new pure `contactLine(sender, {socials})` |
| `frontend/src/lib/render/templates.tsx` | `CvPages` gains optional `contact`/`summary` props + styles + render |
| `frontend/src/components/applications/export-card.tsx` | compute `contact`/`summary` from profile, thread into `CvDocument`/`ApplicationDocument` |
| `frontend/src/routes/_authenticated/account/profile.tsx` | first/last name inputs, read-only username, `show_socials` checkbox (address section already added) |
| `frontend/tests/lib/letter-doc.test.ts` | **(AI-written, red)** `contactLine` + `senderFromProfile` linkedin/github |

## 3. The code (type in this order)

### 3.1 `backend/spa/models.py`

Add the field in the professional-contact block, right after `github_url`:

```python
    github_url = models.URLField(blank=True)
    # Opt-in: include website + social links as a contact header on the exported
    # CV (email/phone always show). Off by default. Consumed by the frontend
    # export card (show_socials → contactLine).
    show_socials = models.BooleanField(default=False)
```

Then generate + apply the migration:

```bash
python backend/manage.py makemigrations spa
python backend/manage.py migrate
```

### 3.2 `backend/spa/serializers.py`

Replace the whole `UserProfileSerializer` (this folds in the in-flight `name`/`email` spillover and
adds the new fields). The key subtlety is called out inline:

```python
class UserProfileSerializer(serializers.ModelSerializer):
    # Read-only User-model spillover for consumers that need the whole sender
    # identity in one fetch (the cover-letter editor's sender block). `name`
    # mirrors jac CoverLetter._candidate_name so both agree on the fallback chain.
    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    # Writable User fields. `source="user.*"` nests them under a "user" key in
    # validated_data — which is exactly why there is no `user = HiddenField(...)`
    # here any more (it wrote a User *instance* to the same "user" key and the two
    # collided). update() saves the nested User by hand.
    first_name = serializers.CharField(
        source="user.first_name", required=False, allow_blank=True, max_length=150
    )
    last_name = serializers.CharField(
        source="user.last_name", required=False, allow_blank=True, max_length=150
    )

    class Meta:
        model = UserProfile
        fields = (
            "id",
            "name",
            "username",
            "email",
            "first_name",
            "last_name",
            "display_name",
            "avatar",
            "bio",
            "phone",
            "website",
            "linkedin_url",
            "github_url",
            "show_socials",
            "timezone",
            "theme",
            "contrast",
            "email_reminders",
            "updated_at",
            "street",
            "address_line2",
            "zip",
            "city",
            "country",
        )
        read_only_fields = ("id", "name", "username", "email", "updated_at")

    def get_name(self, obj) -> str:
        if obj.display_name:
            return obj.display_name
        full = f"{obj.user.first_name} {obj.user.last_name}".strip()
        return full or obj.user.username

    def update(self, instance, validated_data):
        # first_name/last_name arrive nested under "user" (their source=). Pop and
        # persist them on the related User; the rest is a plain UserProfile update.
        # Popping before super().update() also sidesteps raise_errors_on_nested_writes.
        user_data = validated_data.pop("user", None)
        if user_data:
            for attr, value in user_data.items():
                setattr(instance.user, attr, value)
            instance.user.save(update_fields=list(user_data))
        return super().update(instance, validated_data)
```

> Why dropping `user = HiddenField(default=CurrentUserDefault())` is safe: the only consumer is
> `UserProfileView` (`RetrieveUpdateAPIView`) — there is no create path through this serializer
> (profiles are auto-created by the `post_save` signal), so `CurrentUserDefault` was never exercised.

### 3.3 `frontend/src/lib/queries/profile.ts`

Extend `ProfileRow` (keep the existing comment):

```ts
export type ProfileRow = {
  id: number;
  name: string;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  display_name: string;
  bio: string;
  phone: string;
  website: string;
  linkedin_url: string;
  github_url: string;
  show_socials: boolean;
  street: string;
  address_line2: string;
  zip: string;
  city: string;
  country: string;
};
```

### 3.4 `frontend/src/lib/letter-doc.ts`

Extend `senderFromProfile` (new param keys + two output keys) and add `contactLine` right after it:

```ts
/** The sender block a user profile implies — mirrors backend CoverLetter._sender(). */
export function senderFromProfile(p: {
  name: string;
  email: string;
  phone: string;
  street: string;
  address_line2: string;
  zip: string;
  city: string;
  country: string;
  website: string;
  linkedin_url: string;
  github_url: string;
}): Record<string, string> {
  return {
    name: p.name,
    email: p.email,
    phone: p.phone,
    street: p.street,
    address_line2: p.address_line2,
    zip: p.zip,
    city: p.city,
    country: p.country,
    website: p.website,
    linkedin: p.linkedin_url,
    github: p.github_url,
  };
}

/**
 * One-line contact header for the exported CV, assembled from the sender block.
 * Email + phone always show; website + social links only when `socials` is on
 * (the profile's show_socials opt-in). Blank fields drop out.
 */
export function contactLine(
  sender: Record<string, string>,
  opts: { socials: boolean },
): string {
  const parts = [sender.email, sender.phone];
  if (opts.socials) parts.push(sender.website, sender.linkedin, sender.github);
  return parts.filter(Boolean).join(" · ");
}
```

### 3.5 `frontend/src/lib/render/templates.tsx`

Add two styles to `cvStyles` (right after the `name` style):

```ts
    name: { fontSize: base * 2, marginBottom: base * 0.3, color: spec.colors.accent },
    contact: { color: spec.colors.muted, fontSize: base * 0.9, marginBottom: base },
    summary: { marginBottom: base, lineHeight: 1.4 },
```

(Note: `name`'s `marginBottom` shrinks to `base * 0.3` so the contact line sits tight under it.)

Then give `CvPages` the two optional props and render them under the name:

```tsx
export function CvPages({
  spec,
  name,
  content,
  db,
  contact,
  summary,
}: {
  spec: LayoutSpec;
  name: string;
  content: CvContent;
  db: CvEntriesResponse | undefined;
  contact?: string;
  summary?: string;
}) {
  const styles = cvStyles(spec);
  return (
    <Page size={spec.page.size} style={styles.page} wrap>
      <Text style={styles.name}>{name}</Text>
      {contact ? <Text style={styles.contact}>{contact}</Text> : null}
      {summary ? <Text style={styles.summary}>{summary}</Text> : null}
      {spec.cv.sections.map((s) => (
        <CvSectionView
          key={s}
          section={s as SectionKey}
          content={content}
          db={db}
          styles={styles}
        />
      ))}
      {spec.cv.sidebar.map((s) => (
        <CvSectionView
          key={s}
          section={s as SectionKey}
          content={content}
          db={db}
          styles={styles}
          compact
        />
      ))}
    </Page>
  );
}
```

`CvDocProps = Parameters<typeof CvPages>[0]`, so `CvDocument`, `ApplicationDocument`, and their
callers pick up `contact`/`summary` as optional props automatically — no other change in this file.
Leave `LetterPage` untouched (letter footer scope decision above).

### 3.6 `frontend/src/components/applications/export-card.tsx`

Add `contactLine` to the letter-doc import:

```ts
import {
  contactLine,
  fillBlanks,
  normalizeLetterMeta,
  senderFromProfile,
} from "@/lib/letter-doc";
```

Inside `buildPdf`, after `const db = careerDb.data;`, derive the header once:

```ts
    const socials = profile.data?.show_socials ?? false;
    const contact = contactLine(meta.sender, { socials });
    const summary = profile.data?.bio ?? "";
```

Thread them into every `CvDocument` (the fit-measuring one and the final doc) and the
`ApplicationDocument` cv prop:

```tsx
    const fit =
      scope === "letter"
        ? null
        : await fitCv(
            active,
            s.cv.pages,
            (c) =>
              pdfPages(
                <CvDocument
                  spec={s}
                  name={name}
                  content={c}
                  db={db}
                  contact={contact}
                  summary={summary}
                />,
              ),
            isFavouriteLookup(db),
          );
```

```tsx
    const doc =
      scope === "cv" ? (
        <CvDocument
          spec={s}
          name={name}
          content={fit!.content}
          db={db}
          contact={contact}
          summary={summary}
        />
      ) : scope === "letter" ? (
        <LetterDocument spec={s} meta={meta} body={app.cover_letter} />
      ) : (
        <ApplicationDocument
          cv={{ spec: s, name, content: fit!.content, db, contact, summary }}
          letter={{ spec: s, meta, body: app.cover_letter }}
        />
      );
```

> Passing `contact`/`summary` into the fit-measuring `CvDocument` too means `fitCv` counts pages
> with the header present — the drop-to-fit budget stays honest.

### 3.7 `frontend/src/routes/_authenticated/account/profile.tsx`

The address section is already in this file (earlier edit). Add the identity fields:

**`Profile` type** — add above `display_name`:

```ts
  username: string;
  first_name: string;
  last_name: string;
  display_name: string;
```

and add `show_socials` next to `email_reminders`:

```ts
  email_reminders: boolean;
  show_socials: boolean;
};
```

**`schema`** — add first/last (username is read-only, so it is *not* in the schema) and `show_socials`:

```ts
  first_name: z.string().max(150),
  last_name: z.string().max(150),
```

```ts
  email_reminders: z.boolean(),
  show_socials: z.boolean(),
});
```

**`ProfilePage` → `initial`** — add the three editable values, and pass `username` to the form:

```tsx
    <ProfileForm
      username={p.username}
      initial={{
        first_name: p.first_name,
        last_name: p.last_name,
        display_name: p.display_name,
        bio: p.bio,
        // …existing keys…
        email_reminders: p.email_reminders,
        show_socials: p.show_socials,
      }}
      onSubmit={(v) => patch.mutateAsync(v)}
      busy={patch.isPending}
    />
```

**`ProfileForm` signature** — accept `username`:

```tsx
function ProfileForm({
  username,
  initial,
  onSubmit,
  busy,
}: {
  username: string;
  initial: ProfileSchema;
  onSubmit: (v: ProfileSchema) => Promise<unknown>;
  busy: boolean;
}) {
```

**Form body** — read-only username at the top, then the name inputs before `display_name`:

```tsx
      <div className="space-y-1">
        <Label>Username</Label>
        <Input value={username} disabled readOnly />
      </div>
      <div className="grid grid-cols-2 gap-3">
        {text("first_name", "First name")}
        {text("last_name", "Last name")}
      </div>
      {text("display_name", "Display name")}
```

**Socials toggle** — next to the `email_reminders` checkbox:

```tsx
      <form.Field name="show_socials">
        {(field) => (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={field.state.value}
              onChange={(e) => field.handleChange(e.target.checked)}
            />
            Show my contact details & socials on the exported CV
          </label>
        )}
      </form.Field>
```

## 4. Tests (AI-written, land red)

- **`backend/spa/tests/test_auth.py`** — three cases added to `UserProfileViewTests`:
  `test_patch_writes_first_and_last_name_to_user` (write-through onto `auth.User` + `name` spillover
  now resolves through first/last), `test_username_is_read_only`, `test_show_socials_toggle_persists`.
  Red until the model field + serializer land.
- **`frontend/tests/lib/letter-doc.test.ts`** — extended: the `senderFromProfile` fixture gains
  `linkedin_url`/`github_url` and the "maps the whole sender block" case asserts `linkedin`/`github`;
  a new `describe("contactLine …")` covers socials off (email+phone only), socials on (adds
  website/linkedin/github), and blank-field dropping. Red until `senderFromProfile` maps the socials
  and `contactLine` exists.

Run them:

```bash
# backend
cd backend && python manage.py test spa.tests.test_auth.UserProfileViewTests -v2
# frontend
cd frontend && npx vitest run tests/lib/letter-doc.test.ts
```

## 5. Verification

1. **Backend green:** the three new `UserProfileViewTests` pass after the model+migration+serializer.
2. **Frontend unit green:** `letter-doc.test.ts` passes; `npx tsc -b` is clean (source only — tests
   are excluded from the build).
3. **Profile tab:** `/account/profile` shows a read-only Username, editable First/Last name, the
   Address section, and a "Show my contact details & socials on the exported CV" checkbox. Save →
   toast; reload → values persist. Set a first/last name with `display_name` blank.
4. **Sender name:** open an application whose letter sender name was blank → the sender block now
   fills from first+last (via `senderFromProfile` → `name` spillover).
5. **CV export, socials OFF:** Export card → Preview PDF (CV or Complete). The CV shows the name, a
   contact line of `email · phone` (if set), and the bio as a summary paragraph — no website/socials.
6. **CV export, socials ON:** flip the profile checkbox, save, re-preview → the contact line now also
   shows website · linkedin · github. Confirm the page-fit still behaves (drop toast unchanged).
7. **Empty profile:** a user with no bio/phone/socials exports a CV with just the name + sections
   (no empty contact line, no empty summary) — `contact`/`summary` fall to `""` and don't render.

## Results

<!-- Human fills after testing: raw test output, observed issues, what works. -->
