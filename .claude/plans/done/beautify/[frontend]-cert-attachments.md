# [frontend] cert-attachments

> **Follow-up to `[frontend]-polished-render`.** The gold-standard document appends cert/credential PDFs
> after the CV (`\includepdf` in the LaTeX world). With react-pdf there's no server merge — we store the
> uploaded PDFs (`ApplicationAttachment`) and **merge them into the exported PDF client-side with
> `pdf-lib`**. No TeX, no server CPU. Branch: `frontend/cert-attachments` off `main`. One clean break.

## Context / goal

Users attach PDFs (Zeugnisse, transcripts, reference letters) to an application; the exported PDF should
be **CV/letter + those attachments, in order**. Backend stores them (owner-scoped, PDF-validated);
the export card merges the react-pdf output blob with the attachment PDFs via `pdf-lib` before download.

## Affected files

| path | change |
| --- | --- |
| `backend/jac/models.py` | **new** `ApplicationAttachment` model |
| `backend/jac/serializers.py` | **new** `ApplicationAttachmentSerializer` (PDF + size validation) |
| `backend/jac/views.py` | **new** `ApplicationAttachmentViewSet` (owner-scoped, multipart) |
| `backend/jac/urls.py` | register `attachments` |
| `backend/jac/migrations/000X_*.py` | `makemigrations jac` — `CreateModel ApplicationAttachment` |
| `frontend/package.json` | add `pdf-lib` |
| `frontend/src/lib/render/attachments.ts` | **new** — `mergePdfs()` + pure order helpers |
| `frontend/src/lib/queries/attachments.ts` | **new** — list / upload / delete / reorder hooks |
| `frontend/src/components/applications/attachments-card.tsx` | **new** — manager UI |
| `frontend/src/components/applications/export-card.tsx` | merge attachments into pdf/complete/cv exports |
| `frontend/src/routes/_authenticated/applications/$applicationId.tsx` | slot `<AttachmentsCard>` |

---

## The code

### 1. `backend/jac/models.py` — the model

Put it after `JobApplication`:

```python
class ApplicationAttachment(models.Model):
    """A PDF appended to the exported application (cert, transcript, reference letter). Merged in
    `position` order client-side (pdf-lib) at export time. Validated as a PDF on upload."""

    application = models.ForeignKey(
        JobApplication, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to="application_attachments")
    label = models.CharField(max_length=120, blank=True)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.label or f"attachment {self.pk}"
```

### 2. `backend/jac/serializers.py` — serializer

Import `ApplicationAttachment` in the `from jac.models import (...)` block; add:

```python
class ApplicationAttachmentSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
    """Owner-scoped attachment upload. `application` is validated to be the requester's (mixin);
    `file` must be a PDF under the size cap."""

    user_scoped_fields = ("application",)
    _MAX_BYTES = 10 * 1024 * 1024

    class Meta:
        model = ApplicationAttachment
        fields = ["id", "application", "file", "label", "position", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_file(self, f):
        if f.size > self._MAX_BYTES:
            raise serializers.ValidationError("Attachment too large (max 10 MB).")
        head = f.read(5)
        f.seek(0)
        if head[:5] != b"%PDF-":
            raise serializers.ValidationError("Only PDF attachments are supported.")
        return f
```

### 3. `backend/jac/views.py` — viewset

Add `from rest_framework.parsers import MultiPartParser, FormParser`, `ApplicationAttachment` to the
models import, `ApplicationAttachmentSerializer` to the serializers import, and:

```python
class ApplicationAttachmentViewSet(viewsets.ModelViewSet):
    """User's application attachments. Owner-scoped through the parent application; list is
    filterable by `?application=<pk>`."""

    serializer_class = ApplicationAttachmentSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ["application"]
    ordering_fields = ["position", "created_at"]

    def get_queryset(self):
        return ApplicationAttachment.objects.filter(
            application__user=self.request.user
        ).order_by("position", "id")
```

### 4. `backend/jac/urls.py`

```python
from jac.views import (..., ApplicationAttachmentViewSet)
router.register("attachments", ApplicationAttachmentViewSet, basename="attachment")
```

```bash
cd backend && python manage.py makemigrations jac && python manage.py migrate
```

### 5. `frontend/src/lib/render/attachments.ts` (new)

```ts
import { PDFDocument } from "pdf-lib";

/** Merge the react-pdf output with the attachment PDFs (fetched same-origin), in the given order.
 *  An unreachable attachment is skipped rather than failing the whole export. */
export async function mergePdfs(main: Blob, attachmentUrls: string[]): Promise<Blob> {
  const out = await PDFDocument.load(await main.arrayBuffer());
  for (const url of attachmentUrls) {
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) continue;
    const src = await PDFDocument.load(await res.arrayBuffer());
    const pages = await out.copyPages(src, src.getPageIndices());
    for (const p of pages) out.addPage(p);
  }
  return new Blob([await out.save()], { type: "application/pdf" });
}

export type AttachmentLike = { id: number; position: number };

/** Re-number a list to contiguous 0-based positions (mirrors cv-doc moveEntry). */
export function withPositions<T extends AttachmentLike>(items: T[]): T[] {
  return items.map((a, i) => ({ ...a, position: i }));
}

export function moveAttachment<T extends AttachmentLike>(
  items: T[],
  index: number,
  delta: -1 | 1,
): T[] {
  const target = index + delta;
  if (index < 0 || index >= items.length || target < 0 || target >= items.length) {
    return items;
  }
  const next = [...items];
  [next[index], next[target]] = [next[target], next[index]];
  return withPositions(next);
}
```

Add the dep: `cd frontend && npm i pdf-lib`.

### 6. `frontend/src/lib/queries/attachments.ts` (new)

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, csrfHeaders } from "@/lib/api";

export type Attachment = {
  id: number;
  application: number;
  file: string; // media URL
  label: string;
  position: number;
  created_at: string;
};

const key = (appId: number) => ["jac", "attachments", appId] as const;

export function useAttachments(appId: number) {
  return useQuery({
    queryKey: key(appId),
    queryFn: () =>
      api<Attachment[] | { results: Attachment[] }>(
        `/api/jac/attachments/?application=${appId}&page_size=100`,
      ).then((r) => ("results" in r ? r.results : r)),
  });
}

export function useUploadAttachment(appId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { file: File; label: string; position: number }) => {
      const fd = new FormData();
      fd.append("application", String(appId));
      fd.append("file", vars.file);
      fd.append("label", vars.label);
      fd.append("position", String(vars.position));
      const res = await fetch("/api/jac/attachments/", {
        method: "POST",
        headers: csrfHeaders(), // NOT Content-Type — the browser sets the multipart boundary
        credentials: "same-origin",
        body: fd,
      });
      if (!res.ok) throw new Error(`upload failed: HTTP ${res.status}`);
      return (await res.json()) as Attachment;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: key(appId) }),
  });
}

export function useDeleteAttachment(appId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api(`/api/jac/attachments/${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(appId) }),
  });
}

export function useReorderAttachment(appId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: number; position: number }) =>
      api(`/api/jac/attachments/${vars.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ position: vars.position }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(appId) }),
  });
}
```

### 7. `frontend/src/components/applications/attachments-card.tsx` (new)

File input + label, an ordered list with up/down + delete. Mirror the shadcn usage in `export-card.tsx`
and the `moveAttachment` helper (persist each moved row's new `position`). See the parked
`backlog/[fullstack]-latex-render.md` §12 for a ready-to-adapt component — identical UI, just drop the
LaTeX-specific copy.

### 8. `frontend/src/components/applications/export-card.tsx` — merge on export

Load attachments and merge them into the built blob before download (pdf/complete/cv scopes; a
letter-only export gets no attachments):

```tsx
import { useAttachments } from "@/lib/queries/attachments";
import { mergePdfs } from "@/lib/render/attachments";
```

```tsx
  const attachments = useAttachments(app.id);

  async function withAttachments(blob: Blob): Promise<Blob> {
    const urls = (attachments.data ?? [])
      .slice()
      .sort((a, b) => a.position - b.position)
      .map((a) => a.file);
    return urls.length && scope !== "letter" ? mergePdfs(blob, urls) : blob;
  }
```

In `onDownloadPdf`, wrap the blob: `downloadBlob(await withAttachments(built.blob), \`${stem}.pdf\`)`.
(Preview can stay attachment-free, or reuse `withAttachments` too — your call.)

### 9. Route — slot the card

Place `<AttachmentsCard app={app.data} />` above `<ExportCard app={app.data} />` in
`$applicationId.tsx`.

---

## Tests

- `backend/jac/tests/test_attachments.py` (**new**) — `ApplicationAttachment` API: upload a `%PDF-` file
  → 201; a non-PDF → 400; oversize (patched cap) → 400; list scoped to owner (another user sees none);
  a foreign `application` pk → 400/403; DELETE → 204. Mocked/pure (no pdf-lib, no browser).
- `frontend/tests/lib/attachments.test.ts` (**new**, pure) — `withPositions` renumbers to 0..n-1;
  `moveAttachment` swaps + renumbers and is a no-op at the ends / out of range. (`mergePdfs` is impure —
  fetch + pdf-lib — verified live, not unit-tested.)

Run: `cd backend && python manage.py test jac.tests.test_attachments` ·
`cd frontend && npx vitest run tests/lib/attachments.test.ts`

---

## Verification

1. `python manage.py migrate`; `npm i pdf-lib`; both test files green.
2. Upload two cert PDFs in the Attachments card, reorder them, **Download PDF** (complete scope) → the
   downloaded PDF is CV + letter followed by both certs in the chosen order.
3. Delete one → it drops from the next export. Letter-only export → no attachments appended.
4. A corrupt/non-PDF upload is rejected at upload with a clear error.

**Done looks like:** an application exports as one PDF — the polished react-pdf document plus the
user's cert PDFs merged in order — entirely client-side, no TeX/server compile.

## Results

<!-- Human fills this in after testing: raw test output, observed issues, what works. -->
