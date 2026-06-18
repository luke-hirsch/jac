from llm_connector.conf import get_embed_floors

from jac.llm_prompts import Embed, Instruct


class CVFilter:
    """Turns per-entry relevance scores into a ranked, weakly-filtered CV."""

    # weights and handicaps for specific entries/categories

    _TIER = {
        "job": 0,
        "project": 0,
        "education": 0,
        "skill": 1,
        "certification": 2,
        "language": 3,
    }
    _ANCHOR_W = 0.85
    _FAVOURITE_BONUS = 0.05
    _SECTION_POLICY = {
        "job": {"drop_below": 0.20, "min_keep": 3},
        "education": {"drop_below": 0.15, "min_keep": 2},
        "skill": {"drop_below": 0.35, "min_keep": 5},
        "project": {"drop_below": 0.30, "min_keep": 0},
        "certification": {"drop_below": 0.30, "min_keep": 0},
        "language": {"drop_below": 0.00, "min_keep": None},
    }
    _LABEL_MAX = 3
    _KEEP_LABEL = 1

    def __init__(
        self,
        job_post_text: str,
        entries: list[dict],
        grade: str = "light",
        user=None,
        alias: str = "default",
    ):
        assert isinstance(job_post_text, str)
        self.job_post_text = job_post_text
        self.entries = entries
        self.grade = grade
        self.user = user
        self.alias = alias

    def output(self) -> dict:
        """Return {section: [entry dicts + score], ...}, each section ranked desc.

        Rungs differ in BOTH scorer and selection strategy:
          - light:    embedding cosine -> propagation + absolute section floors (_select).
          - standard: Instruct-LLM relevance labels -> keep-by-verdict (_select_ranked).
          - strong:   holistic selector (TBD) — currently reuses the standard scorer.
        Each LLM rung degrades to the light floor when its scorer returns nothing.
        """
        if self.grade in ("standard", "strong"):
            labels = self._standard_scores()
            if labels:
                return self._select_ranked(labels)
        return self._select(self._light_scores())

    # --- score sources (each returns {id: float} or {} on failure) ---------------------

    def _light_scores(self) -> dict:
        ranked = Embed(
            self.job_post_text, self.entries, user=self.user, alias=self.alias
        ).ranked_entries()
        return {r["id"]: r["score"] for r in ranked} if ranked else {}

    def _standard_scores(self) -> dict:
        """Instruct-LLM relevance labels {id: 0.._LABEL_MAX}. Empty on failure -> light fallback."""
        ranked = Instruct(
            self.job_post_text, self.entries, user=self.user, alias=self.alias
        ).ranked_entries()
        return {r["id"]: r["score"] for r in ranked} if ranked else {}

    def _strong_scores(self) -> dict:
        # TODO: Conversational LLM ranking. Returns {} until implemented -> falls back.
        return {}

    # --- shared selection layer --------------------------------------------------------

    def _propagate(self, base: dict) -> dict:
        """Single ascending-tier sweep: lift each node by its best higher-tier neighbour."""
        eff = {e["id"]: base.get(e["id"], 0.0) for e in self.entries}
        type_of = {e["id"]: e["type"] for e in self.entries}

        adj: dict[str, set[str]] = {}
        for e in self.entries:
            for r in e.get("refs", []):
                adj.setdefault(e["id"], set()).add(r)
                adj.setdefault(r, set()).add(e["id"])

        for tier in (1, 2, 3):
            for e in self.entries:
                if self._TIER.get(e["type"]) != tier:
                    continue
                eid = e["id"]
                higher = [
                    eff[n]
                    for n in adj.get(eid, ())
                    if self._TIER.get(type_of.get(n), 99) < tier
                ]
                if higher:
                    eff[eid] = max(eff[eid], self._ANCHOR_W * max(higher))
        return eff

    def _floors(self) -> dict:
        """Per-section cosine floors: config `embed_floors` over _SECTION_POLICY defaults.

        Cosine distributions differ between embedders, so the floors are an embedder
        property: a config may override any subset via its `embed_floors` key, and
        unspecified sections keep the calibrated default.
        """
        defaults = {s: p["drop_below"] for s, p in self._SECTION_POLICY.items()}
        return {**defaults, **get_embed_floors(self.alias, user=self.user)}

    def _select(self, base: dict) -> dict:
        """Apply propagation + per-section drop. Empty base -> keep everything unscored."""
        if not base:
            return self._group_all()

        eff = self._propagate(base)
        floors = self._floors()

        # Favourite nudge: small, post-propagation, so it tilts close calls without
        # lifting a ~0-scored entry over its section floor (see _FAVOURITE_BONUS).
        for e in self.entries:
            if e.get("favourite"):
                eid = e["id"]
                eff[eid] = eff.get(eid, 0.0) + self._FAVOURITE_BONUS

        by_section: dict[str, list[dict]] = {}
        for e in self.entries:
            by_section.setdefault(e["type"], []).append(e)

        out: dict[str, list[dict]] = {}
        for section, items in by_section.items():
            policy = self._SECTION_POLICY.get(
                section, {"drop_below": 0.0, "min_keep": 0}
            )
            items.sort(key=lambda e: eff.get(e["id"], 0.0), reverse=True)

            floor = floors.get(section, policy.get("drop_below", 0.0))
            min_keep = policy["min_keep"]
            if min_keep is None:
                keep = items
            else:
                keep = [e for e in items if eff.get(e["id"], 0.0) >= floor]
                if len(keep) < min_keep:
                    keep = items[:min_keep]

            out[section] = [
                {**e, "score": round(eff.get(e["id"], 0.0), 4)} for e in keep
            ]
        return out

    def _group_all(self) -> dict:
        """Fallback when scoring fails: every entry kept, score 0.0."""
        out: dict[str, list[dict]] = {}
        for e in self.entries:
            out.setdefault(e["type"], []).append({**e, "score": 0.0})
        return out

    def _select_ranked(self, labels: dict) -> dict:
        """Selection for LLM relevance *labels* (0.._LABEL_MAX) — the standard rung.

        Keep by the model's own verdict rather than an absolute floor, and do NOT propagate
        (the LLM already reasoned relationally from the entry text). Per section:
          - rank by label desc, stable (ties keep the CV's natural order — recency / name);
          - keep every entry rated >= _KEEP_LABEL, plus all favourites (pinned);
          - guarantee min_keep by topping up from the highest-ranked remainder;
          - languages (min_keep None) keep everything.
        Kept-count therefore varies with fit (intended) — never clamped to a target.
        """
        by_section: dict[str, list[dict]] = {}
        for e in self.entries:
            by_section.setdefault(e["type"], []).append(e)

        out: dict[str, list[dict]] = {}
        for section, items in by_section.items():
            policy = self._SECTION_POLICY.get(section, {"min_keep": 0})
            min_keep = policy["min_keep"]
            items.sort(key=lambda e: labels.get(e["id"], 0), reverse=True)

            if min_keep is None:
                keep = list(items)
            else:
                keep = [
                    e
                    for e in items
                    if labels.get(e["id"], 0) >= self._KEEP_LABEL or e.get("favourite")
                ]
                if len(keep) < min_keep:
                    kept_ids = {e["id"] for e in keep}
                    for e in items:  # already label-desc sorted
                        if e["id"] not in kept_ids:
                            keep.append(e)
                            kept_ids.add(e["id"])
                            if len(keep) >= min_keep:
                                break
                    keep.sort(key=lambda e: labels.get(e["id"], 0), reverse=True)

            out[section] = [{**e, "score": labels.get(e["id"], 0)} for e in keep]
        return out
