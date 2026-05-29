"""Render a CV's filtered entries to Markdown.

Used by the cv_test management command as a smoke-test artifact. PDF/DOCX
rendering happens on the frontend from the API's structured CV payload.
"""

from __future__ import annotations

from jac.cv import CV


class CvRender:
    """Renders a CV snapshot to human-readable output formats.

    Currently supports Markdown via export_md(). Future formats (PDF, DOCX)
    are handled on the frontend from the API payload, not here.
    """

    SECTION_ORDER = [
        ("jobs", "Experience"),
        ("educations", "Education"),
        ("projects", "Projects"),
        ("skills", "Skills"),
        ("certifications", "Certifications"),
        ("languages", "Languages"),
    ]

    def __init__(self, cv: CV, name: str | None = None):
        """Args:
            cv: A CV instance (typically after filtering/ranking).
            name: Display name for the CV header. Derived from the user record if omitted.
        """
        self.cv = cv
        self.name = name or self._derive_name()

    def _derive_name(self) -> str:
        """Look up the user's full name, falling back to username then 'CV'."""
        from django.contrib.auth.models import User

        user = User.objects.filter(pk=self.cv.user).first()
        if not user:
            return "CV"
        full = f"{user.first_name} {user.last_name}".strip()
        return full or user.username

    # ---- shared section builders ------------------------------------------

    def _date_range(self, started, ended) -> str:
        """Format a start/end date pair as 'YYYY-MM-DD – YYYY-MM-DD' (or 'present')."""
        if not started and not ended:
            return ""
        start = started.isoformat() if started else "?"
        end = ended.isoformat() if ended else "present"
        return f"{start} – {end}"

    def _sections(self) -> list[tuple[str, list[tuple[str, list[str]]]]]:
        """Return [(section_title, [(heading, [body_lines]), ...]), ...]."""
        out: list[tuple[str, list[tuple[str, list[str]]]]] = []
        for key, title in self.SECTION_ORDER:
            entries = self.cv.entries.get(key) or []
            if not entries:
                continue
            items = [self._format_entry(key, e) for e in entries]
            out.append((title, items))
        return out

    def _format_entry(self, kind: str, e) -> tuple[str, list[str]]:
        """Return (heading, body_lines) for a single entry of the given section kind."""
        if kind == "jobs":
            heading = f"{e.title} — {e.company}"
            meta = self._date_range(e.started, e.ended)
            body = [meta] if meta else []
            skills = ", ".join(s.name for s in e.skills.all())
            if skills:
                body.append(f"Skills: {skills}")
            if e.description:
                body.append(e.description)
            return heading, [b for b in body if b]

        if kind == "educations":
            label = " ".join(p for p in (e.degree, e.field_of_study) if p).strip()
            heading = f"{label} @ {e.institution}" if label else e.institution
            body = []
            meta = self._date_range(e.started, e.ended)
            if meta:
                body.append(meta)
            if e.grade:
                body.append(f"Grade: {e.grade}")
            if e.description:
                body.append(e.description)
            return heading, body

        if kind == "projects":
            heading = e.name
            body = []
            meta = self._date_range(e.started, e.ended)
            if meta:
                body.append(meta)
            skills = ", ".join(s.name for s in e.skills.all())
            if skills:
                body.append(f"Skills: {skills}")
            if e.url:
                body.append(e.url)
            if e.description:
                body.append(e.description)
            return heading, body

        if kind == "skills":
            heading = e.name
            body = [f"{e.get_proficiency_display()} ({e.get_category_display()})"]
            domains = ", ".join(d.name for d in e.domains.all())
            if domains:
                body.append(f"Domains: {domains}")
            if e.description:
                body.append(e.description)
            return heading, body

        if kind == "certifications":
            heading = f"{e.name} — {e.issuer}"
            body = []
            if e.issued_on:
                body.append(f"Issued: {e.issued_on.isoformat()}")
            if e.expires_on:
                body.append(f"Expires: {e.expires_on.isoformat()}")
            if e.url:
                body.append(e.url)
            if e.description:
                body.append(e.description)
            return heading, body

        if kind == "languages":
            heading = e.name
            return heading, [e.get_fluency_display()]

        return str(e), []

    # ---- exporters --------------------------------------------------------

    def export_md(self) -> str:
        """Render the CV as a Markdown string."""
        lines: list[str] = [f"# {self.name}", ""]
        for title, items in self._sections():
            lines.append(f"## {title}")
            lines.append("")
            for heading, body in items:
                lines.append(f"### {heading}")
                for b in body:
                    lines.append(b)
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"
