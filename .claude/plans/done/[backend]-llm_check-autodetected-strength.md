# [backend] Surface autodetected filter strength in `llm_check`

## Context / goal

Each LLM alias has a **filter strength** — `"light" | "standard" | "strong"` — that maps 1:1 to
the `CVFilter` grade ladder (`CV.FILTER_GRADE = ["strong", "standard", "light"]`). This is what
decides which scoring rung the CV pipeline uses for a given config.

That detection already exists: `llm_connector/conf.py::get_alias_strength(alias, user=...)`. It
honours an explicit `strength` in the resolved config and otherwise falls back to
`_autodetect_strength()`, which guesses from the model id (size token like `7b`/`70b`, or name
hints like `haiku`/`mini`/`flash`). So nothing about detection needs to be built — we only need to
**display** it.

Goal: `python manage.py llm_check` should print each alias's strength next to its connectivity
result, so you can see at a glance which `CVFilter` grade an alias resolves to.

This serves roadmap item **1 (CV ladder)** indirectly — it's the operator-facing readout for the
strength signal that drives grade selection.

## Affected files

| path | why |
| --- | --- |
| `backend/llm_connector/management/commands/llm_check.py` | import `get_alias_strength`; print strength on each result line |
| `backend/llm_connector/tests.py` | assert the strength label appears in command output |

## The code

### `backend/llm_connector/management/commands/llm_check.py`

**1. Extend the import** at the top (line 8) to also pull in `get_alias_strength`:

```python
from llm_connector.conf import get_alias_strength, get_llm_settings
```

**2. In `handle()`, show the strength on the result line.** Replace the final display loop
(currently lines 104–118) with the version below. The only change is computing `strength` once per
alias via the canonical resolver and appending it to both the OK and FAIL lines — strength is
config-derived, so it's worth showing even when connectivity fails.

```python
        for alias, _, _ in targets:
            if alias not in results:
                continue
            r = results[alias]
            strength = get_alias_strength(alias, user=user)
            self.stdout.write(f"  [{alias}] ", ending="")
            if "error" in r:
                self.stdout.write(
                    self.style.ERROR("FAIL")
                    + f"  provider={r['provider']}  model={r['model']}"
                    + f"  strength={strength}  error={r['error']}"
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS("OK")
                    + f"  provider={r['provider']}  model={r['model']}"
                    + f"  strength={strength}  latency={r['latency']}ms"
                )
```

Notes:
- `get_alias_strength` takes the same `(alias, user)` pair the command already uses, so it resolves
  exactly the config that was checked: per-user `LLMConfig` when `--user` is given, `settings.LLM`
  otherwise.
- It is internally exception-safe (returns `"strong"` on a missing/broken config), so no extra
  guarding is needed in the command.
- We don't print strength for `missing` aliases — there's no config to resolve.

## Tests

Add to `backend/llm_connector/tests.py`, inside the existing `LLMCheckCommandTests` class
(`@override_settings(LLM=FAKE_LLM, ...)`). `FAKE_LLM`'s models (`fake-1`, `fake-2`) carry no size
token or name hint, so they autodetect to `"strong"`.

```python
    def test_reports_strength_for_working_alias(self):
        out = StringIO()
        call_command("llm_check", "default", stdout=out)
        # fake-1 has no size/name hint -> autodetects to the full ladder.
        self.assertIn("strength=strong", out.getvalue())

    def test_strength_respects_explicit_config(self):
        with override_settings(
            LLM={"default": {"provider": "fake", "model": "fake-1", "strength": "light"}}
        ):
            out = StringIO()
            call_command("llm_check", "default", stdout=out)
            self.assertIn("strength=light", out.getvalue())

    def test_strength_autodetects_small_model(self):
        with override_settings(
            LLM={"default": {"provider": "fake", "model": "llama3.2:1b"}}
        ):
            out = StringIO()
            call_command("llm_check", "default", stdout=out)
            self.assertIn("strength=light", out.getvalue())
```

Optionally extend the existing `test_user_flag_checks_user_configs` to also assert
`"strength="` appears, confirming the per-user path resolves strength too.

## Verification

From `backend/` with the `jac` virtualenv active:

```bash
python manage.py test llm_connector.tests.LLMCheckCommandTests
```

Expected: all tests pass, including the three new ones.

Then exercise it live against the real default Ollama config:

```bash
python manage.py llm_check default
```

Expected line shape (default model is `llama3.2:1b`, a ≤3b model → `light`):

```
Checking 1 alias(es) in parallel [settings.LLM]...
  [default] OK  provider=ollama  model=llama3.2:1b  strength=light  latency=...ms
```

If you have a user with a personal `LLMConfig`:

```bash
python manage.py llm_check --user <pk>
```

Expected: each user alias line now carries a `strength=...` field consistent with that config's
model id (or its explicit `strength`).

**Done looks like:** every checked alias prints `strength=<light|standard|strong>`, matching what
`get_alias_strength` resolves and therefore the `CVFilter` grade that alias would drive.
