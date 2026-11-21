# Setup

## Requirements

- Python 3.11 or newer
- pip
- (optional) Docker and Docker Compose

No Node, no build step, no external services.

## Local install

```bash
git clone https://github.com/mohdabrarbaloch-arch/day-30-khazana.git
cd day-30-khazana

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Configure

```bash
cp .env.example .env
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
```

That appends a generated secret to `.env`. Do not commit `.env` — it is in
`.gitignore`.

The defaults are fine for local use. `DATABASE_URL` falls back to SQLite, which
needs no setup.

## Seed demo data

```bash
python -m scripts.seed
```

This creates:

- a user `abraz@example.com` / `khazana-demo-2026`
- a "Company Handbook" collection
- four documents: refunds, expenses, onboarding, support

The documents are chosen to exercise the awkward cases — a tiered policy, a
hard-wrapped paragraph, and two documents that share vocabulary.

## Run

```bash
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**.

## Docker

```bash
cp .env.example .env
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
docker compose up --build
```

Postgres comes up alongside the app. The app waits for the database healthcheck
before starting.

To seed inside the container:

```bash
docker compose exec app python -m scripts.seed
```

## Verify the install

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok","version":"1.0.0",...}
```

Then sign in through the UI and ask "How long do refunds take?" — you should get
the 30-day rule with a citation.

## Running the tests

```bash
pytest -q
```

105 tests, about eleven seconds, no network and no database required.

## Linting

```bash
ruff check .
ruff format --check .
```

## Troubleshooting

**`SECRET_KEY is not set`** — the app refuses to start in production mode
without one. Run the `secrets.token_urlsafe` command above and append it to
`.env`.

**`sqlite3.OperationalError: no such table`** — the schema is created on
startup. If you see this, the app did not finish starting; check the log.

**Port 8000 already in use** — `uvicorn app.main:app --port 8001`.

**Answers are always refused** — check that documents were actually ingested.
The sidebar shows chunk counts per collection; zero chunks means ingestion
failed or the text was empty.

**Docker: app exits immediately** — almost always a missing `SECRET_KEY` in
`.env`. `docker compose logs app` will say so.
