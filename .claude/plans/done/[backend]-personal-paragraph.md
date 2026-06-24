# Setup guide — cover-letter personal paragraph (company research × personality)

> Branch: `backend/personal-paragraph` (shared with the prerequisite guide).
> **Prerequisite: `[backend]-personality-questionnaire.md`** — this guide consumes
> `spa.models.PersonalityProfile.ensure_dossier()`. Implement that first.
> Tests for this guide are on disk (red), distributed across the per-app `tests/` packages by
> topic (see the Tests section): `llm_connector/tests/test_adapters.py` (web-search capability),
> `jac/tests/test_llm_rungs.py` (writer + grounding), `jac/tests/test_cover_letter.py` (researcher
> + build slot).

## Context / goal

The cover letter reads flat: it's a relevance-weave of CV-anchored snippets. This adds **one extra
paragraph** that proves the candidate researched *this specific company* and that it fits *who they
are* — a thing a snippet can't carry (must be unique per company, not anchored in the CV). It's
generated from three inputs the pipeline lacks today:

1. **Live company research** — an LLM with web search "googles" the employer (NOT the posting — the
   point is showing research *beyond* the ad).
2. **The candidate's personality dossier** — from the prerequisite guide.
3. The posting + role (for relevance only).

Roadmap: sub-item under #1 (frontend render of CV + cover letter), extending the otherwise-"done"
cover-letter backend.

Decisions (from planning): research engine = AI-only, **provider-native web search** exposed as a
`web_search` capability on the connector, **not hardcoded to one provider** — adapters carry a
`supports_web_search` flag. **Anthropic, OpenAI, and Google all flip it `True`**: Anthropic via its
server-side `web_search` tool on `messages.create`; OpenAI via the **Responses API**
(`responses.create`) `web_search` tool — recommended on `gpt-5.x` with `reasoning_effort="high"`
(`gpt-5` with `minimal` reasoning is unsupported for web search); Google via Gemini's **Google Search
grounding** tool (sources come from `candidates[].grounding_metadata.grounding_chunks[].web.uri`). (Ollama / open-source web search is
deliberately **out of scope here**: a "web-search-capable" local model still doesn't search by itself
— it only knows how to *call* a search tool, so it needs a search backend. Ollama's own backend is a
hosted cloud API + key, which doesn't prove the self-hosted thesis. Doing it properly — a tool loop
wiring a self-hostable model to a *self-hostable* search backend — is its own roadmap item (see Notes
/ deferred). Until then a self-hosted standard run simply stubs, which the flow already handles.)

The paragraph slot is **opt-in (`--personal`) but capability-driven, not grade-gated**: it's filled
with a real, researched paragraph **only when the selected model can actually search the web**;
otherwise it emits a **loud placeholder stub** the human must replace (and a send-time safeguard, far
off, must never let a stub ship). Stub conditions:
- `light` grade → **always** stub (the weak showcase tier never researches — see
  [[project-purpose-cv-showcase]]);
- `standard`/`strong` with a **non-web-capable** model → stub (a weak standard model counts);
- research returns nothing, or there's no personality dossier → stub.
Real paragraph only when: grade ≠ `light` **and** `can_web_search(alias)` **and** research ok **and**
personality present.

(The general HTTP/Firecrawl-style `scraper` app for keyless/self-hosted research is parked for
later — see [[project-purpose-cv-showcase]].)

### Two invariants this must not break

- **Grounding honesty.** Today `FaithfulnessCheck` audits the woven body *against snippets*. The
  personal paragraph has **zero snippet support** — it must NOT flow through that check or it'd read
  as fully hallucinated. It gets its **own** check (`ParagraphGroundingCheck`) against its **own**
  sources (research + personality). The snippet body and its grounding are untouched.
  See [[cover-letter-grounding-metric]].
- **`ai_share`.** The paragraph is ~100% machine-authored; its word count folds into `ai_share`
  (numerator and denominator) so provenance stays truthful.

The new `web_search` is an *optional capability* mirroring `embed()` (base raises
`NotImplementedError`; only capable providers implement it). A `supports_web_search` class flag lets
callers check capability *without* a doomed call. It returns free prose + a URL list, not JSON —
see [[no-json-llm-io]].

## Architecture / data flow

```
--personal on?─no─► (no slot)
      │yes
grade == light? ─yes─────────────────────────────► STUB
      │no
PersonalityProfile.ensure_dossier() ─empty───────► STUB   (free check first)
      │dossier
CompanyResearcher.research() ─not ok──────────────► STUB
      │   (research() returns ok=False if the alias can't web-search,
      │    via can_web_search — so "incapable model" lands here too)
      │ok (dossier + source URLs)
PersonalParagraphWriter ─''──────────────────────► STUB
      │paragraph
(opt-in) ParagraphGroundingCheck ─► {count, claims}
      │
CoverLetter.build(): personal_paragraph (+is_stub +sources +grounding)
      │
render_markdown: inserted after snippet body, before closing
      │
_ai_share: real paragraph words count as AI; a STUB counts 0
```

## Affected files

- `backend/llm_connector/base.py` — `web_search()` stub raising `NotImplementedError` +
  `supports_web_search = False` class flag.
- `backend/llm_connector/providers/anthropic.py` — implement `web_search()` (server-side tool;
  concat text blocks, collect citation/result URLs) + `supports_web_search = True`.
- `backend/llm_connector/providers/openai.py` — implement `web_search()` via the **Responses API**
  (`responses.create` with the `web_search` tool; collect `url_citation` annotation URLs) +
  `supports_web_search = True`.
- `backend/llm_connector/providers/google.py` — implement `web_search()` with Gemini's **Google
  Search grounding** tool (legacy `google-generativeai` SDK; collect
  `grounding_metadata.grounding_chunks[].web.uri`) + `supports_web_search = True`.
- `backend/llm_connector/client.py` — `LLMClient.web_search()` (delegate + log like `complete`) +
  a `supports_web_search` property.
- `backend/llm_connector/__init__.py` — module-level `web_search()` + `can_web_search()` shorthands.
- `backend/jac/research.py` (new) — `CompanyResearcher`.
- `backend/jac/llm_prompts.py` — `PersonalParagraphWriter` + `ParagraphGroundingCheck`.
- `backend/jac/cover_letter.py` — new build stage, render insertion, `_ai_share` update, result keys.
- `backend/jac/management/commands/cover_letter.py` — `--personal`, `--research-llm` flags + output.

## The code

### 1. `llm_connector/base.py` — new optional capability + flag

Add the flag as a class attribute (so capability is knowable without a doomed call) and the method:

```python
class LLMAdapter(ABC):
    # ... existing ...
    supports_web_search: bool = False  # providers with a server-side search tool flip this True

    def web_search(self, messages: list[dict], **kwargs) -> dict:
        """Run a completion with provider-native web search.

        Optional capability — only providers with supports_web_search = True implement it.
        Returns {"text": str, "sources": [str]}. Raises NotImplementedError otherwise (mirrors
        embed()); the flag is the clean pre-check, this is the backstop.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support web search.")
```

### 2. `llm_connector/providers/anthropic.py` — implement it + set the flag

Set `supports_web_search = True` as a class attribute on `AnthropicAdapter`, then:

```python
def web_search(self, messages: list[dict], **kwargs) -> dict:
    system, api_msgs = self._split_system(
        messages, kwargs.pop("system", self.config.get("system"))
    )
    max_uses = kwargs.pop("max_uses", 5)
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}]
    params = dict(
        model=self._model, max_tokens=self._max_tokens,
        messages=api_msgs, tools=tools, **kwargs,
    )
    if system:
        params["system"] = system
    response = self._client.messages.create(**params)

    texts: list[str] = []
    sources: list[str] = []
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            texts.append(block.text)
            for cit in getattr(block, "citations", None) or []:
                url = getattr(cit, "url", None)
                if url:
                    sources.append(url)
        elif btype == "web_search_tool_result":
            for r in getattr(block, "content", None) or []:
                url = getattr(r, "url", None)
                if url:
                    sources.append(url)
    return {
        "text": "\n".join(t for t in texts if t).strip(),
        "sources": list(dict.fromkeys(sources)),  # dedupe, keep order
    }
```

> The existing `complete()` reads only `content[0].text`; with web search the response is a *list*
> of blocks (text + `server_tool_use` + `web_search_tool_result`), so this needs its own method
> rather than passing `tools=` through `complete()`.

### 2b. `llm_connector/providers/openai.py` — implement it via the Responses API + set the flag

The existing adapter speaks **Chat Completions** (`chat.completions.create`), but OpenAI's web search
is a **Responses-API** server-side tool — not available on chat.completions for general models. So
`web_search()` calls `self._client.responses.create` (the same `OpenAI()` instance exposes both).
Set `supports_web_search = True` as a class attribute on `OpenAIAdapter`, then:

```python
def web_search(self, messages: list[dict], **kwargs) -> dict:
    """Run a completion with OpenAI's native web search (Responses API).

    Web search is a Responses-API server-side tool, not available on chat.completions
    for general models — hence responses.create here, not the complete() path. For
    gpt-5.x set reasoning_effort='high' in the LLMConfig (recommended for web search;
    gpt-5 with 'minimal' reasoning is unsupported).
    """
    effort = kwargs.pop("reasoning_effort", self._reasoning_effort)
    params: dict = dict(
        model=self._model,
        input=messages,                       # Responses API takes `input`, not `messages`
        tools=[{"type": "web_search"}],
        **kwargs,
    )
    if self._max_tokens:
        params.setdefault("max_output_tokens", self._max_tokens)
    if effort:
        params.setdefault("reasoning", {"effort": effort})   # gpt-5.x reasoning models only
    response = self._client.responses.create(**params)

    sources: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", None) or []:
            for ann in getattr(block, "annotations", None) or []:
                if getattr(ann, "type", None) == "url_citation":
                    url = getattr(ann, "url", None)
                    if url:
                        sources.append(url)
    return {
        "text": (getattr(response, "output_text", "") or "").strip(),
        "sources": list(dict.fromkeys(sources)),   # dedupe, keep order
    }
```

> The Responses API returns `output` as a *list* of items; the `message` item's `content` blocks
> carry `url_citation` annotations (`.url`, `.title`). `response.output_text` is the SDK's
> convenience concat of the text blocks — use it for the prose, walk `output` for the citation URLs.
> `reasoning_effort` is sent only when configured (as `reasoning={"effort": …}`), so non-reasoning
> web-search models (`gpt-4.1`, `gpt-4.1-mini`) still work — just leave it unset for those.

### 2c. `llm_connector/providers/google.py` — implement it with Gemini Search grounding + set the flag

Web search *is* Google's home turf — Gemini exposes it as the **Google Search grounding** tool. The
existing adapter uses the legacy `google-generativeai` SDK (`GenerativeModel` + `start_chat`), so
`web_search()` mirrors `complete()` but attaches the grounding tool and harvests source URLs from the
response's `grounding_metadata`. First widen `_make_model` to accept tools:

```python
def _make_model(self, system: str | None, tools=None):
    """Construct a GenerativeModel with optional system instruction, token cap, and tools."""
    kwargs = {"model_name": self._model_name}
    if system:
        kwargs["system_instruction"] = system
    if self._max_tokens:
        import google.generativeai.types as gtypes
        kwargs["generation_config"] = gtypes.GenerationConfig(max_output_tokens=self._max_tokens)
    if tools is not None:
        kwargs["tools"] = tools
    return self._genai.GenerativeModel(**kwargs)
```

Then set `supports_web_search = True` as a class attribute on `GoogleAdapter` and add:

```python
# Gemini 2.x grounding tool. (Gemini 1.5 uses the dynamic-retrieval variant
# "google_search_retrieval"; override via the `search_tool` config key if needed.)
def web_search(self, messages: list[dict], **kwargs) -> dict:
    """Run a completion grounded with Google Search (Gemini). Returns {"text", "sources"}.

    Uses the legacy google-generativeai SDK to match complete(); sources come from each
    candidate's grounding_metadata.grounding_chunks[].web.uri.
    """
    tool = self.config.get("search_tool", "google_search")
    history, system = _to_google_messages(messages)
    model = self._make_model(system, tools=tool)
    last = history.pop()
    chat = model.start_chat(history=history)
    response = chat.send_message(last["parts"][0], **kwargs)

    sources: list[str] = []
    for cand in getattr(response, "candidates", None) or []:
        meta = getattr(cand, "grounding_metadata", None)
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            if uri:
                sources.append(uri)
    return {
        "text": (getattr(response, "text", "") or "").strip(),
        "sources": list(dict.fromkeys(sources)),  # dedupe, keep order
    }
```

> Grounding metadata lives on each *candidate* (`response.candidates[i].grounding_metadata`), not at
> the top level — `grounding_chunks[].web.uri` (+ `.web.title`) are the cited pages. `response.text`
> still gives the woven prose. The grounding URLs are often Vertex redirect links
> (`vertexaisearch.cloud.google.com/...`) rather than raw publisher URLs — fine as sources, just
> don't assume they're the bare domain. **SDK caveat:** this targets the legacy
> `google-generativeai` package the repo uses today; the newer `google-genai` client changes both
> the tool config (`types.Tool(google_search=types.GoogleSearch())`) and the traversal — revisit if
> the project migrates SDKs.

### 3. `llm_connector/client.py` — `LLMClient.web_search()`

Mirror `complete()` (try/finally logging; log `result["text"]` as `response_text`):

```python
def web_search(self, prompt: str | None = None, *, messages: list[dict] | None = None, **kwargs) -> dict:
    """Run a web-search-backed completion. Returns {"text": str, "sources": [str]}."""
    msgs = _normalise_messages(prompt, messages)
    start = time.monotonic()
    error_text = ""
    result: dict = {"text": "", "sources": []}
    try:
        result = self._adapter.web_search(msgs, **kwargs)
        return result
    except Exception as exc:
        error_text = str(exc)
        raise
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        if logging_enabled():
            self._write_log(msgs, result.get("text", ""), error_text, None, None, latency_ms)

@property
def supports_web_search(self) -> bool:
    return getattr(self._adapter, "supports_web_search", False)
```

### 4. `llm_connector/__init__.py` — shorthands

```python
def web_search(prompt=None, *, messages=None, alias="default", user=None, **kwargs) -> dict:
    """Run a single web-search-backed completion. {"text": str, "sources": [str]}."""
    return get_client(alias, user=user).web_search(prompt=prompt, messages=messages, **kwargs)


def can_web_search(alias="default", user=None) -> bool:
    """True if the alias resolves to a provider with native web search (no call made)."""
    return get_client(alias, user=user).supports_web_search
```

### 5. `jac/research.py` (new)

```python
import logging

from llm_connector import can_web_search, web_search

logger = logging.getLogger(__name__)


class CompanyResearcher:
    """Research the employer with a web-search-capable LLM and return a factual dossier + sources.

    The posting is given only as light context (so the model knows which company/role) — the goal is
    facts found ONLINE, beyond the ad. Degrades gracefully: an empty company, a non-web-capable alias
    (can_web_search False, no call made), blank text, or any failure -> {"ok": False, "dossier": "",
    "sources": []}.
    """

    _INSTRUCTION = (
        "Research the company named below using web search. Write a concise factual dossier (4-7 "
        "sentences) covering: what the company does, its mission/values, notable recent news or "
        "direction, and its culture or reputation. State ONLY what you can find online; if something "
        "is uncertain, omit it. Do not mention the job posting. No headers, no markdown."
    )

    def __init__(self, company, posting_text, *, alias="default", user=None,
                 language="en", max_uses=5):
        self.company = (company or "").strip()
        self.posting_text = posting_text or ""
        self.alias = alias
        self.user = user
        self.language = language
        self.max_uses = max_uses

    def research(self) -> dict:
        if not self.company:
            return self._empty()
        if not can_web_search(self.alias, self.user):
            logger.info("CompanyResearcher: alias %s has no web search; skipping", self.alias)
            return self._empty()
        try:
            res = web_search(prompt=self._prompt(), alias=self.alias,
                             user=self.user, max_uses=self.max_uses)
        except NotImplementedError:  # backstop if a flag is wrong
            return self._empty()
        except Exception:
            logger.exception("CompanyResearcher: web search failed")
            return self._empty()
        text = (res.get("text") or "").strip()
        if not text:
            return self._empty()
        return {"ok": True, "dossier": text, "sources": res.get("sources", [])}

    @staticmethod
    def _empty() -> dict:
        return {"ok": False, "dossier": "", "sources": []}

    def _prompt(self) -> str:
        ctx = self.posting_text[:600]
        return (
            f"{self._INSTRUCTION}\nWrite the dossier in {self.language}.\n\n"
            f"COMPANY: {self.company}\n\n"
            f"(role context, do not quote): {ctx}\n\nDOSSIER:"
        )
```

### 6. `jac/llm_prompts.py` — writer + grounding check

```python
class PersonalParagraphWriter:
    """Write ONE cover-letter paragraph on why the candidate is a genuine fit for THIS company.

    Sources: the company research dossier (company facts) + the personality dossier (who they are).
    The job posting is only light role context. Every company fact must come from RESEARCH, every
    trait from PERSONALITY — invent nothing. Free prose; any failure -> '' (caller omits it).
    """

    _INSTRUCTION = (
        "Write ONE short paragraph (3-5 sentences) for a cover letter: why this candidate is "
        "personally drawn to and a strong fit for THIS company. Connect a specific thing about the "
        "company (from RESEARCH) to who the candidate is (from PERSONALITY). Use ONLY facts from "
        "RESEARCH for company claims and ONLY traits from PERSONALITY for the candidate — invent "
        "nothing, add no skills/employers/numbers. First person, genuine, not fawning. No salutation, "
        "no sign-off, no markdown, no headers — just the paragraph."
    )

    def __init__(self, *, posting_text="", title="", language="en",
                 company_dossier="", personality_dossier="", alias="default", user=None):
        self.posting_text = posting_text
        self.title = title
        self.language = language
        self.company_dossier = company_dossier
        self.personality_dossier = personality_dossier
        self.alias = alias
        self.user = user

    def write(self) -> str:
        if not self.company_dossier or not self.personality_dossier:
            return ""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("PersonalParagraphWriter: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        return (
            f"{self._INSTRUCTION}\nWrite in {self.language}.\n\n"
            f"ROLE: {self.title}\n\n"
            f"RESEARCH (company facts — the only source for company claims):\n{self.company_dossier}\n\n"
            f"PERSONALITY (the candidate — the only source for who they are):\n{self.personality_dossier}\n\n"
            f"PARAGRAPH:"
        )


class ParagraphGroundingCheck:
    """Faithfulness audit for the personal paragraph. Mirrors FaithfulnessCheck, but the source of
    truth is RESEARCH + PERSONALITY (never snippets, never the posting). Same line format and the
    same honesty rule: count=None on any audit failure, never 0."""

    _INSTRUCTION = (
        "You are fact-checking a cover-letter PARAGRAPH against two sources: RESEARCH (company facts) "
        "and PERSONALITY (the candidate). A claim is UNSUPPORTED if neither source states or clearly "
        "implies it. List every unsupported factual claim.\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'UNSUPPORTED <n>';\n"
        "  - then ONE line per claim, '- <claim>' (<=20 words), worst first;\n"
        "  - if all grounded, write 'UNSUPPORTED 0' and nothing else.\n"
        "Do not flag tone, opinion, or first-person framing — only checkable facts. No prose, no JSON."
    )
    _COUNT_RE = re.compile(r"\bUNSUPPORTED\s+(\d+)\b", re.IGNORECASE)
    _CLAIM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")

    def __init__(self, paragraph, company_dossier, personality_dossier, user=None, alias="default"):
        self.paragraph = paragraph
        self.company_dossier = company_dossier
        self.personality_dossier = personality_dossier
        self.user = user
        self.alias = alias

    def critique(self) -> dict:
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("ParagraphGroundingCheck: LLM call failed")
            return {"count": None, "claims": []}
        return _parse_unsupported(raw, self._COUNT_RE, self._CLAIM_RE)

    def _prompt(self) -> str:
        return (
            f"{self._INSTRUCTION}\n\n"
            f"RESEARCH:\n{self.company_dossier or '(none)'}\n\n"
            f"PERSONALITY:\n{self.personality_dossier or '(none)'}\n\n"
            f"PARAGRAPH:\n{self.paragraph}\n\nAUDIT:"
        )
```

> **`_parse` is NOT inherited — define the shared parser, or `critique()` raises `AttributeError`.**
> `ParagraphGroundingCheck` subclasses nothing, so there is no `_parse` to call. Lift the body of the
> existing `FaithfulnessCheck._parse` into one **module-level** function and have *both* audits call
> it — so the "listed-claims-win / only-explicit-0-is-clean / else-None" honesty rule (see
> [[cover-letter-grounding-metric]]) lives in exactly one place:
>
> ```python
> def _parse_unsupported(raw: str, count_re, claim_re) -> dict:
>     """Parse a line-format faithfulness audit into {'count': int | None, 'claims': [str]}.
>
>     Honesty rule: listed claim lines win (trust their length over the declared n); only an
>     explicit 'UNSUPPORTED 0' is a clean verdict; anything unreadable -> count=None ('not
>     checked'), never 0.
>     """
>     text = raw or ""
>     cm = count_re.search(text)
>     claims: list[str] = []
>     for line in text.splitlines():
>         if count_re.search(line):          # don't read the count line as a claim
>             continue
>         m = claim_re.match(line)
>         if m:
>             claims.append(m.group(1).strip()[:200])
>     if claims:
>         return {"count": len(claims), "claims": claims}
>     if cm and cm.group(1) == "0":
>         return {"count": 0, "claims": []}
>     return {"count": None, "claims": []}
> ```
>
> Then **change `FaithfulnessCheck.critique()` too**: replace its `return self._parse(raw)` with
> `return _parse_unsupported(raw, self._COUNT_RE, self._CLAIM_RE)` and delete the now-duplicated
> `FaithfulnessCheck._parse`. Both classes keep their own `_COUNT_RE`/`_CLAIM_RE` (identical), so the
> only shared thing is the parse logic. (If you'd rather not touch the shipped `FaithfulnessCheck`,
> the minimal alternative is to give `ParagraphGroundingCheck` its own `_parse` copied verbatim — but
> that duplicates the honesty rule, which is exactly what the memory warns against.)

### 7. `jac/cover_letter.py` — integration

Imports: add `PersonalParagraphWriter, ParagraphGroundingCheck` and
`from jac.research import CompanyResearcher`. (The capability check lives inside
`CompanyResearcher.research()`, which returns `ok=False` for a non-web-capable alias — so
`cover_letter` just stubs on `not research["ok"]`, no separate `can_web_search` call here.)

Constructor: add `personal_paragraph: bool = False` and `research_alias: str | None = None`.

Module-level stub constant (loud, unmissable — must never ship; a send-time safeguard is a far-off
TODO):

```python
# Visible placeholder when the machine can't research the company (light grade, a non-web-capable
# model, no research, or no personality). Deliberately jarring so it can't be sent by accident.
PERSONAL_STUB = "⚠️⚠️ WRITE A PERSONAL PARAGRAPH YOU LAZY PIECE OF SHIT ⚠️⚠️"
```

In `build()`, after the snippet/grounding `result` dict and before `result["text"] = ...`:

```python
pp = self._personal_paragraph(language, title)
result["personal_paragraph"] = pp["text"]
result["personal_paragraph_is_stub"] = pp["is_stub"]
result["personal_paragraph_sources"] = pp["sources"]
result["personal_paragraph_grounding"] = pp["grounding"]
result["ai_share"] = self._ai_share(
    sel["ordered"], language, body_is_ai_fallback,
    personal_words=0 if pp["is_stub"] else len(pp["text"].split()),  # a stub isn't AI prose
)
result["text"] = self.render_markdown(result)
return result
```

New helpers — the slot is real-or-stub, capability-driven (not grade-gated):

```python
def _stub(self) -> dict:
    return {"text": PERSONAL_STUB, "is_stub": True, "sources": [],
            "grounding": {"count": None, "claims": []}}

def _personal_paragraph(self, language, title) -> dict:
    blank = {"text": "", "is_stub": False, "sources": [],
             "grounding": {"count": None, "claims": []}}
    if not self.personal_paragraph:
        return blank                                  # slot not requested -> nothing
    if self.grade == "light":
        return self._stub()                           # weak showcase tier never researches
    alias = self.research_alias or self.alias
    personality = self._personality_dossier(alias)
    if not personality:
        return self._stub()                           # no "you" to ground -> stub (before paying)
    company = self._recipient()["company"]
    research = CompanyResearcher(
        company, getattr(self.job_posting, "posting_text", ""),
        alias=alias, user=self.user, language=language,
    ).research()
    if not research["ok"]:
        return self._stub()                           # non-capable model / search failed / empty
    text = PersonalParagraphWriter(
        posting_text=getattr(self.job_posting, "posting_text", ""),
        title=title, language=language,
        company_dossier=research["dossier"], personality_dossier=personality,
        alias=alias, user=self.user,
    ).write()
    if not text:
        return self._stub()
    grounding = {"count": None, "claims": []}
    if self.verify_grounding:
        grounding = ParagraphGroundingCheck(
            text, research["dossier"], personality,
            alias=self.verifier_alias or alias, user=self.user,
        ).critique()
    return {"text": text, "is_stub": False, "sources": research["sources"], "grounding": grounding}

def _personality_dossier(self, alias) -> str:
    try:
        from spa.models import PersonalityProfile
        prof = PersonalityProfile.objects.filter(user=self.user).first()
    except Exception:
        return ""
    if not prof or not prof.has_answers():
        return ""
    return prof.ensure_dossier(alias=alias, user=self.user)
```

`_ai_share` — add `personal_words=0` and fold it in:

```python
def _ai_share(self, snippets, language, ai_fallback, personal_words=0) -> float:
    if ai_fallback or not snippets:
        return 1.0
    tax = self._REWRITE_TAX.get(self.grade, self._REWRITE_TAX["standard"])
    native_w = sum(len(s.content.split()) for s in snippets if s.language == language)
    trans_w  = sum(len(s.content.split()) for s in snippets if s.language != language)
    total = native_w + trans_w + personal_words
    if not total:
        return 1.0
    ai_w = trans_w + tax * native_w + personal_words   # the paragraph is fully AI-authored
    return round(ai_w / total, 2)
```

`render_markdown` — insert after the body block (`out.append(r["body"])` / blank pair), before
the closing:

```python
if r.get("personal_paragraph"):
    out.append(r["personal_paragraph"])
    out.append("")
```

### 8. `cover_letter` management command

Add flags `--personal` (`action="store_true"`) and `--research-llm` (alias, default None); thread
them into `CoverLetter(..., personal_paragraph=opts["personal"], research_alias=opts["research_llm"])`
(both `handle()` arg-passing and the `_one()` signature). In the header/log, when the slot is present,
add one of:
- `> Personal paragraph: ⚠ STUB — no auto-research, write it by hand` when
  `result["personal_paragraph_is_stub"]`;
- else `> Personal paragraph: ✓ (N source(s))` + reuse
  `_grounding_line(result["personal_paragraph_grounding"])`.

## Tests

On disk (red), split by topic across the per-app `tests/` packages — **not** a single feature file
(see the test-package convention). Covers:
- `llm_connector/tests/test_adapters.py` — `WebSearchCapabilityTests` + `AnthropicWebSearchTests` +
  `OpenAIWebSearchTests` + `GoogleWebSearchTests`: `web_search()` parsing + tool request (SDK mocked
  — Anthropic via `messages.create`, OpenAI via `responses.create`, Google via
  `GenerativeModel`/`start_chat`/`send_message` with the `google_search` tool, sources from
  `grounding_metadata.grounding_chunks[].web.uri`); `supports_web_search` True on all three adapters;
  OpenAI forwards a configured `reasoning_effort` as `reasoning={"effort": …}`; base adapter flag
  False + `web_search` raises `NotImplementedError`; `can_web_search()` reflects the flag.
- `jac/tests/test_llm_rungs.py` — `PersonalParagraphWriterTests` + `ParagraphGroundingCheckTests`
  (`jac.llm_prompts` classes; None-never-0 rule).
- `jac/tests/test_cover_letter.py` — `CompanyResearcherTests` (ok dossier when capable; `ok=False`
  on empty company, non-capable alias → no `web_search` call, exception, blank text) +
  `CoverLetterPersonalParagraphTests`: **real** paragraph only when grade≠light + capable + research
  ok + personality present; **stub** (`personal_paragraph_is_stub=True`, `PERSONAL_STUB` in
  `result["text"]`) on `light`, on a non-capable alias at standard/strong, on empty research, on
  missing personality; `blank` when `--personal` off. Stub contributes 0 to `ai_share`. The snippet
  `FaithfulnessCheck` never receives the paragraph (assert its `body`).

```
cd backend && python manage.py test jac llm_connector
```

## Verification (end-to-end, human)

1. Prerequisite guide done + migrated; the questionnaire is filled and produces a dossier.
2. A web-search-capable alias — any of
   `LLMConfig(alias="strong", provider="anthropic", model="claude-sonnet-4-6", api_key=…)`,
   `LLMConfig(alias="strong", provider="openai", model="gpt-5.4", reasoning_effort="high", api_key=…)`,
   or `LLMConfig(alias="strong", provider="google", model="gemini-2.5-pro", api_key=…)`.
3. **Real path:** `python manage.py cover_letter --user 1 --job-file data/test_job.md --grade strong \
   --llm strong --personal --research-llm strong --verify` →
   the `.cover.md` has a company-specific paragraph after the snippet body; header shows
   `Personal paragraph: ✓ (N sources)`, a grounding line, and a higher `AI share`.
4. **Stub paths:** re-run with `--grade light` → the loud `⚠️ … ⚠️` stub appears in the letter and
   `Personal paragraph: ⚠ STUB`. Re-run at `--grade standard` against the **default Ollama** alias
   (non-web-capable) → stub again (no doomed search call). Confirms capability-driven, not
   grade-gated.

## Notes / deferred

- **Send-time safeguard** (far off): before any letter is sent, block/scrub if `PERSONAL_STUB` is
  present — a stub must never ship. See [[project-purpose-cv-showcase]].
- **Frontend** (roadmap #1) renders `personal_paragraph` (real vs `is_stub` styled distinctly) +
  sources/grounding badge — separate guide; the API dict already carries everything.
- **Cost** — a real paragraph = 1 search + 1 write call (+1 verify); stub paths cost nothing
  (no LLM call). Opt-in via `--personal`.
- **Open-source / self-hosted web search** (own roadmap item, parked): let a self-hosted standard
  run produce a *real* paragraph by wiring a tool-capable local model (qwen3, gpt-oss, the
  Ollama "web search" models) to a **self-hostable search backend** (SearXNG / Tavily / Brave /
  Firecrawl-style) via a tool-calling loop — folding in the parked `scraper` app. Ollama's own
  hosted `/api/web_search` (cloud + `OLLAMA_API_KEY`, free tier) is the quick alternative but is
  *cloud* search, so it doesn't prove the self-hosted thesis — hence a dedicated agent later, not a
  flag flip here. Adapter `supports_web_search` stays `False` for `ollama`/`custom` until then. See
  [[project-purpose-cv-showcase]].
