# [fullstack] latex-render

> **PARKED (2026-07-23).** Superseded by `to-do/[frontend]-polished-render` — we're getting react-pdf to
> the moderncv look instead of compiling LaTeX (Lukas: *"I don't need TeX, I need it pretty"* — no TeX
> install, no server CPU). Kept here as a fallback / future "power-user: download `.tex`" option. The
> deploy notes live in `backlog/[infra]-latex-render-deploy.md`.

> **Sits between the portfolio stack and the three letter-pipeline rework guides**
> (`[backend]-letter-matrix-pipeline` → `[frontend]-letter-matrix-ui` → `[backend]-letter-eval-judge`).
> The gold-standard cover letter is a **LaTeX** document; this guide adds a **server-side lualatex
> render** of an application to PDF, alongside — not replacing — the existing react-pdf export. If it
> proves out, a later guide retires the frontend render. Volatile/dev phase, one clean break, no
> compat bridges ([[no-compat-clean-breaks]]). Branch: `fullstack/latex-render`
> (pointer already created off the current tip; `git branch -f fullstack/latex-render main` to
> re-base it on `main` if you prefer — the guide targets the code as it stands on the working tree).

## Context / goal

Lukas hand-built a real application (`~/Documents/01 Bewerbungen/dawndenim`) whose **gold standard is
one `moderncv` `.tex` = letter + CV + appended cert PDFs**: `\recipient/\subject/\opening/\makelettertitle`
→ body → `\makeletterclosing`, then the CV (`\cventry`/`\cvitem`), then
`\includepdf[pages=-]{media/udemy.pdf}` / `{media/zeugnisse.pdf}`. That file compiles **as-is** on the
`basictex` install on this Mac (moderncv, fontawesome6, pdfpages, setspace, ngerman babel, epstopdf are
all present).

We turn that into the app's render path with the smallest honest step:

- **The `.tex` is a styling shell; the whole tailored result drops in.** The flow is unchanged: create
  the application, run generation, edit the CV + cover letter **exactly as today**. Then, instead of
  react-pdf, the backend fills the *edited result* into the template as LaTeX-escaped `<<tokens>>`:
  - the **letter** — `recipient`/`subject`/`opening`/`enclosures` from `letter_meta`, body from
    `cover_letter`;
  - the **whole CV** — one `<<cv_body>>` token resolved from the edited `cv_content`: the same
    selection + order react-pdf renders (deselected dropped, sections localized, `\cventry` per
    job/project/education/cert and `\cvitem` per skill/language, à la moderncv).

  The template owns preamble / header / geometry / colours; the *content* is 100 % the tailored result.
  (LaTeX paginates natively, so there's no react-pdf-style page-fit drop here — every selected entry is
  rendered; mirroring the layout's `max_entries` cap is a possible follow-up.)
- **Attachments.** A new `ApplicationAttachment` (uploaded PDFs — certs, transcripts, references) is
  merged in order via `\includepdf[pages=-]` at an `<<attachments>>` marker.
- **Compile server-side** with **`lualatex`** (Lukas's engine, for the fonts) in a throwaway temp dir,
  `-no-shell-escape`, wall-clock timeout, then **wipe every intermediate** (`.aux/.log/.pdf/…`) — the
  `TemporaryDirectory` does this on exit.
- **Frontend:** a "PDF (LaTeX)" button in the export card + an attachments manager card. The react-pdf
  buttons stay exactly as they are.

**Security posture (this is authed + roadmap-public — [[public-site-posture]]):** every value
substituted into the template is LaTeX-escaped, so posting-derived text can never inject control
sequences; `lualatex` runs `-no-shell-escape` (no `\write18`) with a timeout; uploaded attachments are
validated as PDFs before they reach `\includepdf`. **The template itself is executable LaTeX** — it is
owner-authored and this endpoint is **owner-only**. Do **not** expose LaTeX render on the public
showcase without a sandbox; that stays a roadmap item.

## Affected files

| path | change |
| --- | --- |
| `backend/jac/latex.py` | **new** — escaping, template fill, attachment injection, `lualatex` compile + cleanup, `render_application_pdf(application)` |
| `backend/jac/resources/latex/gold_standard.tex` | **new** — the tokenised moderncv template (adapt from your `cv_Hirschhausen_de.tex`) |
| `backend/jac/models.py` | `ApplicationLayout.latex_template` FileField; **new** `ApplicationAttachment` model |
| `backend/jac/serializers.py` | `ApplicationLayoutSerializer`: expose `latex_template`; **new** `ApplicationAttachmentSerializer` (PDF + size validation) |
| `backend/jac/views.py` | `JobApplicationViewSet.render` GET action; **new** `ApplicationAttachmentViewSet` |
| `backend/jac/urls.py` | register `attachments` viewset |
| `backend/jac/management/commands/seed_system_defaults.py` | seed the default layout's `latex_template` from the resource |
| `backend/lukehirsch/settings.py` | `LATEX_BIN` / `LATEX_TIMEOUT_S` / `LATEX_ASSETS_DIR` |
| `backend/jac/migrations/000X_*.py` | `makemigrations jac` — `AddField latex_template` + `CreateModel ApplicationAttachment` |
| `frontend/src/lib/render/latex.ts` | **new** — `latexRenderUrl` / `fetchLatexPdf` + pure attachment-order helpers |
| `frontend/src/lib/queries/attachments.ts` | **new** — list / upload / delete / reorder attachment hooks |
| `frontend/src/components/applications/attachments-card.tsx` | **new** — attachments manager UI |
| `frontend/src/components/applications/export-card.tsx` | add the "PDF (LaTeX)" download button |
| `frontend/src/routes/_authenticated/applications/$applicationId.tsx` | slot `<AttachmentsCard>` |
| `.claude/plans/backlog/[infra]-latex-render-deploy.md` | **new (already written)** — the Dockerfile TeX package set |

---

## The code

### 1. `backend/jac/latex.py` (new)

```python
"""Server-side LaTeX render of a JobApplication to PDF via lualatex.

The gold-standard cover letter + CV is a moderncv .tex the user authors once and stores on their
ApplicationLayout (`latex_template`). This module fills the per-application variables (recipient,
subject, opening, letter body, enclosures) into that template, appends any uploaded attachment PDFs
(certs / credentials) via \\includepdf, compiles with lualatex in a throwaway temp dir, and returns
the PDF bytes — every intermediate file goes with the temp dir on exit.

SECURITY (authed + roadmap-public surface — see [[public-site-posture]]):
  * Every value substituted into the template is LaTeX-escaped (`latex_escape`), so posting-derived
    text can never inject a control sequence.
  * lualatex runs `-no-shell-escape` (no \\write18) with a wall-clock timeout. A hostile/looping
    template is bounded by the timeout + -halt-on-error, not trusted to terminate.
  * The template is executable LaTeX; it is OWNER-authored and the render endpoint is owner-only. Do
    NOT expose LaTeX render publicly without a sandbox.
  * Uploaded attachments are validated as PDFs before they reach \\includepdf (the serializer gates
    on upload; is_pdf() re-checks at render time).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

from jac.models import Certification, Education, Job, Language, Project, Skill

logger = logging.getLogger(__name__)


class LatexError(RuntimeError):
    """Compile could not produce a PDF (missing template, non-zero exit, timeout, no output)."""


# --- escaping ---------------------------------------------------------------------------------

_ESCAPE = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_ESCAPE_RE = re.compile("|".join(re.escape(k) for k in _ESCAPE))


def latex_escape(text: str) -> str:
    """Escape the ten LaTeX-special characters in a plain-text value. Single-pass re.sub, so our
    own replacement braces are never re-scanned / double-escaped."""
    if not text:
        return ""
    return _ESCAPE_RE.sub(lambda m: _ESCAPE[m.group()], text)


def latex_paragraphs(text: str) -> str:
    """Escape a multi-paragraph plain-text block, preserving blank-line paragraph breaks (a blank
    line is LaTeX's \\par). Single newlines inside a paragraph collapse to a space."""
    blocks = re.split(r"\n\s*\n", (text or "").strip())
    out: list[str] = []
    for b in blocks:
        joined = " ".join(line.strip() for line in b.splitlines() if line.strip())
        if joined:
            out.append(latex_escape(joined))
    return "\n\n".join(out)


# --- template fill ----------------------------------------------------------------------------

# Distinctive token unlikely to collide with LaTeX braces/percents. Author writes <<subject>>,
# <<letter_body>>, ... and exactly one <<attachments>>. Unknown tokens are left verbatim so the
# author sees them in the PDF and fixes the template.
_TOKEN_RE = re.compile(r"<<\s*([a-z_]+)\s*>>")


def fill_template(template: str, context: dict[str, str]) -> str:
    return _TOKEN_RE.sub(lambda m: context.get(m.group(1), m.group(0)), template)


def attachments_block(count: int) -> str:
    """`count` \\includepdf lines pointing at att_1.pdf … att_<count>.pdf (written by compile_pdf)."""
    return "\n".join(rf"\includepdf[pages=-]{{att_{i}.pdf}}" for i in range(1, count + 1))


_ENCLOSURE_DEFAULT = {"de": "Lebenslauf", "en": "curriculum vitae"}


def build_context(application, attachments) -> dict[str, str]:
    """Escaped per-application substitution values from letter_meta + cover_letter. `attachments`
    is the already-validated (PDF) list, in render order — its labels drive the enclosure line."""
    meta = application.letter_meta or {}
    rcp = meta.get("recipient") or {}
    lang = (getattr(application.posting, "language", "") or "en")[:2].lower()

    addr_lines = [
        rcp.get("street"),
        rcp.get("address_line2"),
        " ".join(p for p in (rcp.get("zip"), rcp.get("city")) if p),
        rcp.get("country"),
    ]
    # \\ is a structural LaTeX line break inside \recipient's 2nd arg — join, don't escape it.
    recipient_address = r" \\ ".join(latex_escape(line) for line in addr_lines if line)

    labels = [a.label for a in attachments if (a.label or "").strip()]
    enclosures = ", ".join(latex_escape(x) for x in labels) or latex_escape(
        _ENCLOSURE_DEFAULT.get(lang, _ENCLOSURE_DEFAULT["en"])
    )

    return {
        "recipient_name": latex_escape(rcp.get("company") or rcp.get("contact_name") or ""),
        "recipient_address": recipient_address,
        "subject": latex_escape(meta.get("subject") or getattr(application.posting, "title", "") or ""),
        "opening": latex_escape(meta.get("salutation") or ""),
        "letter_body": latex_paragraphs(application.cover_letter or ""),
        "enclosures": enclosures,
    }


# --- compile ----------------------------------------------------------------------------------

def _bin() -> str:
    return (
        getattr(settings, "LATEX_BIN", "")
        or shutil.which("lualatex")
        or "/Library/TeX/texbin/lualatex"
    )


def _timeout() -> int:
    return int(getattr(settings, "LATEX_TIMEOUT_S", 60))


def _assets_dir() -> Path | None:
    d = getattr(settings, "LATEX_ASSETS_DIR", None)
    return Path(d) if d else None


def is_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def layout_template_text(layout) -> str:
    """The raw .tex on a layout's `latex_template`, or '' when it has none."""
    if layout and getattr(layout, "latex_template", None):
        try:
            with layout.latex_template.open("rb") as fh:
                return fh.read().decode("utf-8")
        except (OSError, ValueError):
            return ""
    return ""


def _run_lualatex(cwd: Path) -> None:
    try:
        proc = subprocess.run(
            [
                _bin(),
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "main.tex",
            ],
            cwd=str(cwd),
            capture_output=True,
            timeout=_timeout(),
        )
    except FileNotFoundError as exc:
        raise LatexError(f"lualatex not found at {_bin()!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LatexError("lualatex timed out") from exc
    if proc.returncode != 0:
        log = cwd / "main.log"
        tail = (
            log.read_text(errors="replace")[-2000:]
            if log.exists()
            else proc.stdout.decode(errors="replace")[-2000:]
        )
        logger.warning("lualatex failed (rc=%s):\n%s", proc.returncode, tail)
        raise LatexError("lualatex failed")


def compile_pdf(source: str, *, assets_dir: Path | None, attachment_paths: list[Path]) -> bytes:
    """Compile a filled .tex string to PDF bytes. Assets (signature, images) are copied next to
    main.tex; attachments land as att_1.pdf … . Two passes (moderncv letter title + refs). The
    TemporaryDirectory removes main.tex + .aux/.log/.pdf + copies on exit — total cleanup."""
    with tempfile.TemporaryDirectory(prefix="jaclatex-") as tmp:
        d = Path(tmp)
        if assets_dir and assets_dir.is_dir():
            for f in assets_dir.iterdir():
                if f.is_file() and f.suffix.lower() != ".tex":
                    shutil.copy2(f, d / f.name)
        (d / "main.tex").write_text(source, encoding="utf-8")
        for i, src in enumerate(attachment_paths, start=1):
            shutil.copy2(src, d / f"att_{i}.pdf")
        for _ in range(2):
            _run_lualatex(d)
        out = d / "main.pdf"
        if not out.exists():
            raise LatexError("lualatex produced no PDF")
        return out.read_bytes()


# --- CV body (the tailored cv_content → moderncv) ---------------------------------------------

_ENTRY_MODELS = {
    "job": Job,
    "project": Project,
    "education": Education,
    "certification": Certification,
    "skill": Skill,
    "language": Language,
}
# (cv_content key, entry type, is_compact). Block sections = one \cventry per row; compact
# (skills/languages) = joined \cvitem lines — mirrors the react-pdf section/sidebar split.
_SECTIONS = [
    ("jobs", "job", False),
    ("educations", "education", False),
    ("projects", "project", False),
    ("certifications", "certification", False),
    ("skills", "skill", True),
    ("languages", "language", True),
]
_SECTION_TITLES = {
    "en": {"jobs": "Experience", "educations": "Education", "projects": "Projects",
           "certifications": "Certifications", "skills": "Skills", "languages": "Languages"},
    "de": {"jobs": "Berufserfahrung", "educations": "Ausbildung", "projects": "Projekte",
           "certifications": "Zertifikate", "skills": "Kenntnisse", "languages": "Sprachen"},
}
_ID_RE = re.compile(r"^([a-z_]+):(\d+)$")
_SKILL_CATEGORIES = [c[0] for c in Skill.Category.choices]


def _resolve_rows(user, etype, entries):
    """cv_content entries of one type → owned career-DB rows, in cv_content order — missing and
    foreign ids drop out (the `user=` filter enforces ownership; deselected are pre-filtered)."""
    pks = []
    for e in entries:
        m = _ID_RE.match(e.get("id", "") or "")
        if m and m.group(1) == etype:
            pks.append(int(m.group(2)))
    if not pks:
        return []
    by_pk = {r.pk: r for r in _ENTRY_MODELS[etype].objects.filter(user=user, pk__in=pks)}
    return [by_pk[pk] for pk in pks if pk in by_pk]


def _years(started, ended) -> str:
    if not started and not ended:
        return ""
    start = started.isoformat() if started else "?"
    end = ended.isoformat() if ended else "present"
    return latex_escape(f"{start} – {end}")


def _city(row) -> str:
    loc = getattr(row, "location", None)
    return loc.city if loc else ""


def _desc(text) -> str:
    # One escaped run (newlines collapsed). Rendering bullet lists as \begin{itemize} is a follow-up.
    return latex_escape(" ".join((text or "").split()))


def _cventry(*args) -> str:
    return r"\cventry" + "".join("{%s}" % a for a in args)


def _entry_line(etype, row) -> str:
    esc = latex_escape
    if etype == "job":
        return _cventry(_years(row.started, row.ended), esc(row.title), esc(row.company),
                        esc(_city(row)), "", _desc(row.description))
    if etype == "project":
        return _cventry(_years(row.started, row.ended), esc(row.name), esc(_city(row)),
                        "", "", _desc(row.description))
    if etype == "education":
        label = " ".join(p for p in (row.degree or "", row.field_of_study or "") if p).strip()
        return _cventry(_years(row.started, row.ended), esc(row.institution), esc(_city(row)),
                        esc(label), esc(row.grade or ""), "")
    if etype == "certification":
        year = row.issued_on.isoformat() if row.issued_on else ""
        return _cventry(esc(year), esc(row.name), esc(row.issuer), "", "", _desc(row.description))
    return ""


def _compact_items(section_key, rows) -> str:
    esc = latex_escape
    if section_key == "skills":
        groups: dict[str, list[str]] = {}
        for r in rows:
            groups.setdefault(r.category, []).append(esc(r.name))
        labels = dict(Skill.Category.choices)
        lines = [
            r"\cvitem{%s}{%s}" % (esc(str(labels.get(cat, cat))), ", ".join(groups[cat]))
            for cat in _SKILL_CATEGORIES
            if groups.get(cat)
        ]
        return "\n".join(lines)
    if section_key == "languages":
        names = [f"{esc(r.name)} ({esc(r.get_fluency_display())})" for r in rows]
        return r"\cvitem{Languages}{%s}" % ", ".join(names) if names else ""
    return ""


def cv_body(application) -> str:
    r"""The whole tailored CV as moderncv source: localized \section headers + \cventry/\cvitem from
    the edited cv_content, in its order, deselected dropped, empty sections skipped."""
    content = application.cv_content or {}
    lang = (getattr(application.posting, "language", "") or "en")[:2].lower()
    titles = _SECTION_TITLES.get(lang, _SECTION_TITLES["en"])
    parts: list[str] = []
    for section_key, etype, compact in _SECTIONS:
        entries = [e for e in (content.get(section_key) or []) if not e.get("deselected")]
        rows = _resolve_rows(application.user, etype, entries)
        if not rows:
            continue
        body = (
            _compact_items(section_key, rows)
            if compact
            else "\n".join(_entry_line(etype, r) for r in rows)
        )
        if not body:
            continue
        parts.append(r"\section{%s}" % latex_escape(titles.get(section_key, section_key)))
        parts.append(body)
    return "\n".join(parts)


# --- entry point ------------------------------------------------------------------------------

def render_application_pdf(application) -> bytes:
    """Fill the application's layout template with the tailored letter + CV + attachment injection,
    then compile. Raises LatexError on any failure. Caller owns auth/ownership + the stub gate."""
    template = layout_template_text(application.layout)
    if not template:
        raise LatexError("layout has no LaTeX template")
    valid = [a for a in application.attachments.all() if is_pdf(Path(a.file.path))]
    ctx = build_context(application, valid)
    ctx["attachments"] = attachments_block(len(valid))
    ctx["cv_body"] = cv_body(application)
    source = fill_template(template, ctx)
    return compile_pdf(
        source, assets_dir=_assets_dir(), attachment_paths=[Path(a.file.path) for a in valid]
    )
```

### 2. `backend/jac/resources/latex/gold_standard.tex` (new)

Adapt your `cv_Hirschhausen_de.tex` — the diff is small and mechanical:

- **Drop** `\usepackage[utf8]{inputenc}` (pdflatex-only; lualatex is UTF-8 native).
- **Tokenise the letter head** (`\recipient`/`\subject`/`\opening`/`\enclosure`) + `<<letter_body>>`;
  **replace the two fixed `\includepdf` lines** with a single `<<attachments>>` marker.
- **Replace the whole CV body** (`\section{…}` + every `\cventry`/`\cvitem`) with one `<<cv_body>>`
  token — the backend fills it from the edited `cv_content`, localized section headers included. Keep
  the `\cvheader` and geometry.
- **Signature:** drop a `signature.pdf` into `LATEX_ASSETS_DIR` (the media folder, below — out of git;
  convert your `Unterschrift.eps` once, you already have `Unterschrift-eps-converted-to.pdf`). The
  render copies it next to `main.tex`, so `\includegraphics{signature.pdf}` just resolves — no
  `\IfFileExists`. (If it's absent the compile 502s — the honest nudge to add it.)
- Keep everything else (preamble, `\cvheader`, babel) **verbatim**.

```latex
\documentclass[10pt,a4paper,sans]{moderncv}
\moderncvstyle{classic}
\moderncvcolor{black}

\usepackage[scale=0.8,margin=2cm]{geometry}
\setlength{\makecvtitlenamewidth}{10cm}
\usepackage[ngerman]{babel}
\usepackage{setspace}
\usepackage{pdfpages}
\usepackage{fontawesome6}
\setstretch{1.25}

\firstname{Lukas}
\familyname{von Hirschhausen}
\mobile{+49 173 715 2471}
\email{lukas@von-hirschhausen.com}
\homepage{github.com/luke-hirsch}

% ---- custom CV header (unchanged from your gold standard) ----
\newsavebox{\cvhnameline}
\newsavebox{\cvhmailline}
\newcommand{\cvheader}{%
  \sbox{\cvhnameline}{\LARGE Lukas von Hirschhausen}%
  \sbox{\cvhmailline}{\small\faEnvelope~lukas@von-hirschhausen.com}%
  \begin{minipage}[t]{0.60\textwidth}
    \raggedright
    {\LARGE\color{color1}Lukas von Hirschhausen}\\[3pt]
    {\color{color2}Lebenslauf}
  \end{minipage}%
  \vspace{0.6cm}
}

\begin{document}

% ==== LETTER (per-application, filled by the backend) ====
\recipient{<<recipient_name>>}{<<recipient_address>>}
\date{\today}
\subject{<<subject>>}
\opening{<<opening>>}
\closing{Beste Grüße\vspace{0.3cm} \\ \includegraphics[width=4.5cm]{signature.pdf}\vspace{-1.0cm}}
\enclosure[Anlagen]{<<enclosures>>}
\makelettertitle

<<letter_body>>

\vspace{12pt}
\makeletterclosing

% ==== CV (the tailored result — filled from the edited cv_content) ====
\newgeometry{margin=1.5cm}
\setlength{\parskip}{0pt}
\cvheader
\setstretch{1}
\vspace{-14pt}
<<cv_body>>

% ==== ATTACHMENTS (uploaded certs, merged in order by the backend) ====
<<attachments>>

\end{document}
```

> `<<cv_body>>` expands to the whole tailored CV — localized `\section{…}` headers + `\cventry`/`\cvitem`
> from the edited `cv_content`, so the template no longer hard-codes sections or entries. The
> `<<attachments>>` marker must sit **before** `\end{document}`. If a second language needs a different
> *preamble*, store it on a second `ApplicationLayout` and pick it per application.

### 3. `backend/jac/models.py`

On `ApplicationLayout`, beside `template`:

```python
    # A moderncv .tex template with <<tokens>> filled per-application by jac.latex. Separate from
    # `template` (the react-pdf JSON spec) — the two renderers coexist. Executable LaTeX: owner-only.
    latex_template = models.FileField(upload_to="latex_templates", blank=True)
```

New model (put it after `JobApplication`, before `GenerationRun`, so the FK target exists):

```python
class ApplicationAttachment(models.Model):
    """A PDF appended to the LaTeX-rendered application (cert, transcript, reference letter).
    Merged in `position` order via \\includepdf at render time. Validated as a PDF on upload."""

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

### 4. `backend/jac/serializers.py`

Add `latex_template` to the layout serializer:

```python
    class Meta:
        model = ApplicationLayout
        fields = ["id", "name", "template", "latex_template", "is_default", "user"]
        read_only_fields = ["id", "is_default"]
```

New serializer (near the other application serializers); import `ApplicationAttachment` in the
`from jac.models import (...)` block:

```python
class ApplicationAttachmentSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
    """Owner-scoped attachment upload. `application` is validated to be the requester's (mixin);
    `file` must be a PDF under the size cap — a bad file would break the \\includepdf at render."""

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

### 5. `backend/jac/views.py`

Imports: add `from django.http import HttpResponse`, `from rest_framework.parsers import MultiPartParser,
FormParser`, `from jac.latex import LatexError, render_application_pdf`, and add `ApplicationAttachment`
to the `jac.models` import + `ApplicationAttachmentSerializer` to the `jac.serializers` import.

`render` action on `JobApplicationViewSet` (beside `rewrite`/`chat`/`transition`):

```python
    @extend_schema(responses=OpenApiResponse(description="application/pdf (rendered application)"))
    @action(detail=True, methods=["get"])
    def render(self, request, pk=None):
        """Server-side LaTeX render of the SAVED application — the letter filled into the layout's
        moderncv template, attachment PDFs appended, compiled with lualatex. Reads saved content
        (the client saves edits first). Owner-only: the template is executable LaTeX (not a public
        surface). 400 if the letter still carries the stub or the layout has no LaTeX template;
        502 if the compile fails."""
        application = self.get_object()
        from jac.cover_letter import PERSONAL_STUB

        if PERSONAL_STUB in (application.cover_letter or ""):
            return Response(
                {"detail": "Replace the personal-paragraph stub before rendering."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not (application.layout and application.layout.latex_template):
            return Response(
                {"detail": "This layout has no LaTeX template."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            pdf = render_application_pdf(application)
        except LatexError:
            logger.warning(
                "latex render failed for application %s", application.pk, exc_info=True
            )
            return Response(
                {"detail": "The LaTeX render failed — check the template and try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="application-{application.pk}.pdf"'
        return resp
```

New viewset (near `ApplicationLayoutViewSet`):

```python
class ApplicationAttachmentViewSet(viewsets.ModelViewSet):
    """User's application attachments (PDFs merged into the LaTeX render). Owner-scoped through the
    parent application; list is filterable by `?application=<pk>`."""

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

### 6. `backend/jac/urls.py`

Import `ApplicationAttachmentViewSet` and register it:

```python
router.register("attachments", ApplicationAttachmentViewSet, basename="attachment")
```

### 7. `backend/lukehirsch/settings.py`

Add `import shutil` at the top if absent, then (near `MEDIA_ROOT`):

```python
# --- LaTeX render (server-side lualatex) ------------------------------------------------------
# Dev: basictex on the Mac. Prod: texlive in the Docker image (see plans/backlog/[infra]-latex-render-deploy).
LATEX_BIN = os.getenv("LATEX_BIN", "") or shutil.which("lualatex") or "/Library/TeX/texbin/lualatex"
LATEX_TIMEOUT_S = int(os.getenv("LATEX_TIMEOUT_S", "60"))
# Files copied next to main.tex at compile time (signature.pdf, images). Keep the signature OUT of git.
LATEX_ASSETS_DIR = os.getenv("LATEX_ASSETS_DIR", "") or str(MEDIA_ROOT / "latex_assets")
```

Create `backend/media/latex_assets/` and drop `signature.pdf` there (gitignored).

### 8. `backend/jac/management/commands/seed_system_defaults.py`

Seed the default layout's LaTeX template alongside the JSON one. Add the resource path near
`DEFAULT_LAYOUTS`:

```python
DEFAULT_LATEX_TEMPLATE = RESOURCES / "latex" / "gold_standard.tex"
```

In the layout loop, after the JSON `template` is set, seed the `.tex` on the `default` layout only
(idempotent, same refresh pattern as the JSON):

```python
            if name == "default" and DEFAULT_LATEX_TEMPLATE.exists():
                tex = DEFAULT_LATEX_TEMPLATE.read_bytes()
                current_tex = None
                if layout.latex_template:
                    with layout.latex_template.open("rb") as fh:
                        current_tex = fh.read()
                if current_tex != tex:
                    if layout.latex_template:
                        layout.latex_template.delete(save=False)
                    layout.latex_template.save(
                        DEFAULT_LATEX_TEMPLATE.name, ContentFile(tex)
                    )
```

### 9. Migration

```bash
cd backend
python manage.py makemigrations jac   # AddField latex_template + CreateModel ApplicationAttachment
python manage.py migrate
python manage.py seed_system_defaults  # stamps the default layout's latex_template
```

### 10. `frontend/src/lib/render/latex.ts` (new)

```ts
/** Server-side LaTeX render + attachment-order helpers. The download is impure (fetch → blob);
 *  the URL builder and the reorder helpers are pure and unit-tested. */
export function latexRenderUrl(appId: number): string {
  return `/api/jac/applications/${appId}/render/`;
}

/** GET the rendered PDF as a blob. Throws with the server `detail` on non-2xx. */
export async function fetchLatexPdf(appId: number): Promise<Blob> {
  const res = await fetch(latexRenderUrl(appId), { credentials: "same-origin" });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = ((await res.json()) as { detail?: string })?.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.blob();
}

export type AttachmentLike = { id: number; position: number };

/** Re-number a list to contiguous 0-based positions (mirrors cv-doc moveEntry semantics). */
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

### 11. `frontend/src/lib/queries/attachments.ts` (new)

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, csrfHeaders } from "@/lib/api";

export type Attachment = {
  id: number;
  application: number;
  file: string;
  label: string;
  position: number;
  created_at: string;
};

const key = (appId: number) => ["jac", "attachments", appId] as const;

export function useAttachments(appId: number) {
  return useQuery({
    queryKey: key(appId),
    queryFn: () =>
      api<Attachment[]>(`/api/jac/attachments/?application=${appId}&page_size=100`).then(
        // list endpoints are paginated; unwrap if the API returns {results}
        (r) => ((r as unknown as { results?: Attachment[] }).results ?? r) as Attachment[],
      ),
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
    mutationFn: (id: number) =>
      api(`/api/jac/attachments/${id}/`, { method: "DELETE" }),
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

> Check how your list endpoints paginate (`usePagedList`/`useFullList` in `queries/jac.ts`) and unwrap
> `results` the same way — the snippet above tolerates either shape.

### 12. `frontend/src/components/applications/attachments-card.tsx` (new)

A small card: file input + label, an ordered list with up/down + delete, and reorder persists each
moved row's new `position`. Mirror the shadcn `Card`/`Button`/`Input` usage in `export-card.tsx`.

```tsx
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { ApplicationRow } from "@/lib/queries/applications";
import {
  useAttachments,
  useDeleteAttachment,
  useReorderAttachment,
  useUploadAttachment,
} from "@/lib/queries/attachments";
import { moveAttachment } from "@/lib/render/latex";

export function AttachmentsCard({ app }: { app: ApplicationRow }) {
  const list = useAttachments(app.id);
  const upload = useUploadAttachment(app.id);
  const remove = useDeleteAttachment(app.id);
  const reorder = useReorderAttachment(app.id);
  const fileRef = useRef<HTMLInputElement>(null);
  const [label, setLabel] = useState("");

  const items = list.data ?? [];

  function onAdd() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    if (file.type !== "application/pdf") {
      toast.error("PDF only.");
      return;
    }
    upload.mutate(
      { file, label: label || file.name.replace(/\.pdf$/i, ""), position: items.length },
      {
        onSuccess: () => {
          setLabel("");
          if (fileRef.current) fileRef.current.value = "";
        },
        onError: () => toast.error("Upload failed."),
      },
    );
  }

  function onMove(index: number, delta: -1 | 1) {
    const next = moveAttachment(items, index, delta);
    if (next === items) return;
    // Persist only the rows whose position changed.
    next.forEach((a, i) => {
      if (a.position !== items[i]?.position || a.id !== items[i]?.id) {
        reorder.mutate({ id: a.id, position: i });
      }
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Attachments (certs, transcripts)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          PDFs appended to the LaTeX render, in this order.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Input ref={fileRef} type="file" accept="application/pdf" className="w-56" />
          <Input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Label (e.g. Zeugnisse)"
            className="w-44"
          />
          <Button size="sm" onClick={onAdd} disabled={upload.isPending}>
            {upload.isPending ? "Uploading…" : "Add"}
          </Button>
        </div>
        <ul className="space-y-1">
          {items.map((a, i) => (
            <li key={a.id} className="flex items-center gap-2 text-sm">
              <span className="flex-1 truncate">{a.label || `attachment ${a.id}`}</span>
              <Button size="sm" variant="ghost" onClick={() => onMove(i, -1)} disabled={i === 0}>
                ↑
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onMove(i, 1)}
                disabled={i === items.length - 1}
              >
                ↓
              </Button>
              <Button size="sm" variant="ghost" onClick={() => remove.mutate(a.id)}>
                ✕
              </Button>
            </li>
          ))}
          {items.length === 0 && (
            <li className="text-xs text-muted-foreground">No attachments yet.</li>
          )}
        </ul>
      </CardContent>
    </Card>
  );
}
```

### 13. `frontend/src/components/applications/export-card.tsx` — add the LaTeX button

Import the helpers and add a button next to "Download PDF". It reuses the existing `blockedBy` stub gate
and `downloadBlob`:

```tsx
import { fetchLatexPdf } from "@/lib/render/latex";
```

Inside the component, a handler beside `onDownloadPdf`:

```tsx
  function onDownloadLatex() {
    if (blockedBy("pdf")) return;
    void withBusy(async () => {
      const blob = await fetchLatexPdf(app.id);
      downloadBlob(blob, `${stem}-latex.pdf`);
    });
  }
```

And the button in the button row (after "Download PDF"):

```tsx
          <Button size="sm" variant="secondary" onClick={onDownloadLatex} disabled={busy}>
            PDF (LaTeX)
          </Button>
```

> The LaTeX render uses the **saved** application content and ignores the react-pdf scope/fit — it
> renders the whole document (filled letter + tailored CV from `cv_content` + attachments). Keep the
> scope select for the react-pdf buttons; the LaTeX button always renders the full doc.

### 14. `frontend/src/routes/_authenticated/applications/$applicationId.tsx` — slot the card

Import and place `<AttachmentsCard app={app.data} />` just above `<ExportCard app={app.data} />`.

---

## Tests

Landed to disk (red until the code above exists). Backend uses the mocked-`subprocess` path — **no
TeX needed to run the suite**; one guarded live-compile test skips unless `lualatex` is on PATH.

- `backend/jac/tests/test_latex.py` (**new topic file** — the LaTeX render subsystem, like
  `test_pipeline.py`):
  - `LatexEscapeTests` — every special char; empty → `''`; a realistic mixed string; `latex_paragraphs`
    keeps blank-line breaks and joins single newlines.
  - `FillTemplateTests` — token substitution; unknown `<<x>>` left verbatim; `attachments_block(n)`
    yields n ordered `\includepdf` lines and `''` for 0.
  - `BuildContextTests` — from an application + `letter_meta`: escaped recipient/subject/opening/body;
    address lines joined with ` \\ ` (not escaped); enclosures from attachment labels, else the
    language default.
  - `CvBodyTests` — the tailored-CV resolver: `cv_body` renders `\section{…}` (localized) + `\cventry`
    for selected jobs, `\cvitem` for skills; escapes (`ACME \& Co`); **excludes deselected** entries;
    **skips** missing/foreign ids and empty sections.
  - `CompilePdfTests` — patch `subprocess.run` to write a fake `main.pdf` + return rc 0: asserts the
    argv carries `-no-shell-escape` / `-interaction=nonstopmode` / `main.tex`, cwd is a temp dir, PDF
    bytes come back, **and the temp dir is gone afterward**; rc≠0 → `LatexError`; timeout → `LatexError`.
  - `RenderApplicationTests` — patch `compile_pdf`; asserts a layout without `latex_template` raises
    `LatexError`, and that only valid-PDF attachments reach the injection/order.
  - `LiveCompileTests` — `@skipUnless(shutil.which("lualatex"), …)`: compiles the seeded gold-standard
    template for real and asserts `%PDF-` output (your local green check).
  - `RenderEndpointTests` (API, kept in this file so the subsystem's tests travel together) — patch
    `jac.latex.render_application_pdf`: GET `…/render/` → 200 `application/pdf` + `Content-Disposition`;
    another user's app → 404; stub in body → 400; no template → 400; `LatexError` → 502.
  - `AttachmentApiTests` — POST multipart (a tiny `%PDF-` file) → 201; a non-PDF → 400; oversize → 400;
    list scoped to owner (bob can't see alice's); another user's `application` pk → 400; DELETE.

Run: `cd backend && python manage.py test jac.tests.test_latex`

- `frontend/tests/lib/latex.test.ts` (**new**, pure) — `latexRenderUrl(7)` path; `withPositions`
  renumbers to 0..n-1; `moveAttachment` swaps + renumbers and is a no-op at the ends / out of range.

Run: `cd frontend && npx vitest run tests/lib/latex.test.ts`

---

## Verification

1. `python manage.py makemigrations jac && python manage.py migrate && python manage.py seed_system_defaults`
   — the default layout reports `latex_template` set.
2. Put `signature.pdf` in `backend/media/latex_assets/`. Save your adapted `gold_standard.tex` to
   `backend/jac/resources/latex/` and re-seed.
3. `cd backend && python manage.py test jac.tests.test_latex` — green (the live-compile test runs
   because `lualatex` is on your PATH; others' CI skips it).
4. **Live end-to-end:** open an application, run/edit the CV + letter as usual, upload a cert PDF in the
   Attachments card, hit **PDF (LaTeX)** in Export → a PDF downloads = filled letter, your **tailored
   CV** (the edited `cv_content` — reorder/deselect an entry and confirm it moves/vanishes), then the
   cert page(s). Check umlauts and `&`/`%` in the recipient/subject/CV render correctly (escaping).
5. Corrupt the template (unbalanced brace) → the button toasts an error and the endpoint returns 502;
   confirm **no** `jaclatex-*` temp dir survives under the system temp dir (cleanup).
6. Confirm the react-pdf **Preview / Download PDF / Markdown / JSON** buttons still behave exactly as
   before — this guide only adds a path.

**Done looks like:** a saved application renders to the gold-standard `moderncv` PDF (letter **and CV
both filled from the edited result**, cert attachments merged) via a single owner-only endpoint, with
escaping + `-no-shell-escape` + full temp-dir cleanup, and the existing react-pdf export untouched.

## Deployment / Dockerfile (backlog — see `plans/backlog/[infra]-latex-render-deploy.md`)

The prod image needs a TeX toolchain. On `basictex` the gold standard needs these on top:

```bash
sudo tlmgr update --self
sudo tlmgr install moderncv geometry setspace pdfpages fontawesome6 xkeyval etoolbox cm-super
```

For Docker prefer `texlive` packages (or a `texlive-full` base) providing `lualatex` + the above; set
`LATEX_BIN=/usr/bin/lualatex`, give the app a writable `TEXMFVAR` (luaotfload font cache) and a tmpfs
for the compile dir, and cap CPU/time. Details + the exact package set live in the backlog stub.

## Results

<!-- Human fills this in after testing: raw test output, observed issues, what works. -->
