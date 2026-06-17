class CVFilter:
    def __init__(
        self,
        job_post_text: str,
        entries: dict | list,
        grade: str = "light",
        user=None,
        entry_kind: str | None = None,
    ):
        assert isinstance(job_post_text, str)
        self.job_post_text = job_post_text
        if not entry_kind and isinstance(entries, dict):
            self.entries = entries
        elif entry_kind and isinstance(entries, list):
            self.entries = {entry_kind: entries}
        else:
            raise ValueError("entries must be a dict or list")
        self.grade = grade
        self.user = user

    def strong(self):
        entries = self.ai_conversational_filter()
        if not entries:
            return self.standard()

    def standard(self):
        entries = self.ai_filter()
        if not entries:
            entries = self.light()

    def light(self):
        return self.embed_filter()

    def embed_filter(self):
        pass

    def ai_filter(self):
        pass

    def ai_conversational_filter(self):
        pass
