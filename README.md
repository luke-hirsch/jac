# jac

**Paste a job posting. jac picks the parts of your career that fit and writes the CV and cover letter,
with a self-hosted model by default.**

It's also one half of a portfolio that renders itself: the same career database feeds a public site
that shows each visitor something different, depending on how they arrived.

## Try it yourself

- **[app.luke-hirsch.de](https://app.luke-hirsch.de):** sign up, no API key needed. No career of your
  own to enter? Load the sample one and generate from that.
- **[luke-hirsch.de](https://luke-hirsch.de):** the portfolio, rendered per visitor.

The model runs on a GPU machine that sleeps when idle. Your run says so while it waits, keeps its
place in line, and you can leave the page: you get a mail when it's done.

## The thesis

Small self-hosted models are a viable alternative to a large hosted one: a 9B model on an 8 GB
consumer GPU, with an embedding model as the ranking floor. Commercial models are an opt-in on the
user's own key, never required. And every AI-written part is measured: `ai_share` says how much the
machine wrote, and a grounding check flags claims your career database doesn't support.

## What's in it

- **Selection:** [`jac/filter.py`](backend/jac/filter.py). Modes `manual`, `standard` (embedding
  ranking + 0–3 labels) and `high` (commercial only: one holistic pick). Pinned entries survive every
  rung.
- **The letter:** [`jac/cover_letter.py`](backend/jac/cover_letter.py),
  [`jac/llm_prompts.py`](backend/jac/llm_prompts.py). Your CV entries are the writer's only source of
  facts, and it doesn't see the posting outside `high` mode, because that's where fabrication came
  from. German and English, with the right form of "you".
- **The connector:** [`llm_connector/`](backend/llm_connector). One run touches exactly one executor
  (HirschAI, or Anthropic / OpenAI on the user's key). Keys are encrypted per user. LLM I/O is
  line-format, not JSON: one missing brace shouldn't lose a whole answer.
- **Queues:** [`jac/tasks.py`](backend/jac/tasks.py),
  [`llm_connector/tasks.py`](backend/llm_connector/tasks.py). A model server that is off or waking is
  normal, not an error. Jobs can run twice safely, status rows say what's happening, and short jobs
  never wait behind long ones.
- **The portfolio:** [`spa/portfolio.py`](backend/spa/portfolio.py). A subdomain per user, private
  until they publish it; a questionnaire for strangers; and links that freeze what was sent.
- **Rendering:** [`frontend/src/lib/render/`](frontend/src/lib/render). A JSON layout spec drives
  react-pdf, and the page fit demotes, drops and grows entries to the page budget.

## Stack

| | |
| --- | --- |
| Backend | Django 6, DRF, Channels, Celery, allauth headless (MFA + passkeys) |
| Frontend | React 19, Vite, TanStack Router / Query / Form, Tailwind v4, shadcn/ui, react-pdf |
| Data | Postgres in dev, the test suite, CI and prod; Valkey for broker, channel layer and cache |
| AI | Ollama behind a URL; Anthropic and OpenAI on the user's key |

## Run it (dev)

```bash
pyenv virtualenv 3.12 jac && pyenv local jac
pip install -r requirements.txt
echo DEBUG=1 > backend/.env.local      # without a DEBUG variable, the app runs in production mode
brew services start postgresql@18
createdb jac                           # the default: a local server, over its socket

cd backend
python manage.py migrate
python manage.py seed_system_defaults  # the SPA renders layouts from the database's media copy
```

Then five processes. Generation and the editor's AI tools run on the queue, so **the web server alone
isn't enough**:

```bash
brew services start valkey                     # broker + channel layer
ollama serve                                   # the zero-cost default model
cd backend && python manage.py runserver       # web + WebSockets
cd backend && celery -A lukehirsch worker -Q jac-ai,jac-cloud,jac-quick,jac-mail -c 3 -l info
cd frontend && npm install && npm run dev
```

Optional: `VECTOR_STORE` turns on the RAG read path. A filesystem path runs Qdrant embedded (then the
worker must be the only process touching it: `--pool=solo`), and an `http(s)://` URL targets a server.
Unset, every run embeds the corpus per request. Backfill with `python manage.py vector_sync`.

Other commands: `cv_export`, `cv_import`, `llm_check [--user <pk>]`.

## Tests

```bash
cd backend && python manage.py test --skip llm    # add --parallel auto
cd frontend && npm test
```

`--skip` opts out of named groups: `llm` (live prompt tests, which need a model), `auth`, `files`.
[CI](.github/workflows/test.yml) runs both sides plus lint on every push.

## Deploy

One image runs everything: nginx + the SPA, daphne, and a worker per queue. See
[`Dockerfile`](Dockerfile) and [`compose.yaml`](compose.yaml).

```bash
docker buildx build --platform linux/amd64 --build-arg RELEASE=$(git rev-parse --short HEAD) -t jac:dev --load .
```

It runs on any box with Docker behind a TLS proxy: [`.env.example`](.env.example) documents every
variable, and a test keeps it honest. Migrations are a deploy step (`./migrate`). Postgres and Valkey
stay outside the stack, behind `DATABASE_URL` and `REDIS_URL`. This instance runs on a homelab; the
only file that knows is [`deploy/compose.lab.yaml`](deploy/compose.lab.yaml).

## License

[CC BY-NC 4.0](LICENSE).
