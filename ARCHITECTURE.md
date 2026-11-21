# Architecture

## The shape of the problem

A retrieval system has one job: given a question, find the passages that answer
it. Everything else — the UI, the auth, the database — is scaffolding around
that. So the architecture puts the retrieval engine in its own package with no
dependencies on the web layer, and lets the web layer be as boring as possible.

```
┌─────────────────────────────────────────────────────────────┐
│  Browser                                                    │
│  index.html + app.js + styles.css — no framework, no build  │
└───────────────────────────┬─────────────────────────────────┘
                            │ JSON over HTTP
┌───────────────────────────▼─────────────────────────────────┐
│  FastAPI                                                    │
│  ┌──────────┬──────────────┬──────────────┬───────────────┐ │
│  │ auth     │ documents    │ search       │ health        │ │
│  └────┬─────┴──────┬───────┴──────┬───────┴───────────────┘ │
│       │            │              │                         │
│  ┌────▼────────────▼──────────────▼───────────────────────┐ │
│  │  services.py — ingestion + in-memory index registry    │ │
│  └────┬───────────────────────────────────┬───────────────┘ │
│       │                                   │                 │
│  ┌────▼─────────────┐          ┌──────────▼──────────────┐  │
│  │  SQLAlchemy      │          │  app/engine/            │  │
│  │  users           │          │  ┌───────────────────┐  │  │
│  │  collections     │          │  │ text.py           │  │  │
│  │  documents       │          │  │ chunker.py        │  │  │
│  │  chunks          │          │  │ bm25.py           │  │  │
│  │  queries         │          │  │ vector.py         │  │  │
│  └────┬─────────────┘          │  │ hybrid.py         │  │  │
│       │                        │  │ answer.py         │  │  │
│  ┌────▼─────────────┐          │  └───────────────────┘  │  │
│  │ SQLite/Postgres  │          │  pure functions, no I/O  │  │
│  └──────────────────┘          └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Why the engine is a separate package

`app/engine/` imports nothing from `app.db`, `app.models` or FastAPI. It takes
strings and dataclasses and returns dataclasses.

That constraint buys three things:

1. **Tests run in microseconds.** No database fixture, no HTTP client, no
   teardown. The 105-test suite finishes in eleven seconds, most of which is
   the API tests.
2. **The API cannot leak into the logic.** There is no temptation to reach for
   a request object inside the scoring function, because there isn't one.
3. **The engine is portable.** It could be lifted into a CLI, a batch job or a
   different web framework without changes.

## Data flow: ingestion

```
POST /api/documents  { collection_id, title, text }
        │
        ▼
   validate (size cap, non-empty, ownership of collection)
        │
        ▼
   chunk_text(text)  ──►  [Chunk(text, index, start, end), ...]
        │                  sentence-aware, overlapping
        ▼
   INSERT documents, chunks
        │
        ▼
   registry.invalidate(user_id)   ← next query rebuilds the index
```

The index is rebuilt lazily rather than updated incrementally. For a knowledge
base of this size that is the right trade: a rebuild over a few thousand chunks
takes milliseconds, and incremental updates are where index bugs live.

## Data flow: query

```
POST /api/query  { question, collection_id? }
        │
        ▼
   registry.get(user_id)  ──►  HybridRetriever (built from DB if cold)
        │
        ▼
   retriever.search(question, limit=8)
        │
        ├──► BM25Index.search()      ──► ranked list A
        └──► TfidfVectorIndex.search() ──► ranked list B
                    │
                    ▼
            reciprocal rank fusion
            score = Σ 1/(k + rank)  over both lists
                    │
                    ▼
   synthesize(question, retriever)
        │
        ├── signal 1: IDF-weighted corpus coverage
        ├── signal 2: normalised retrieval strength
        └── signal 3: best supporting sentence
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   all pass                 any fails
        │                       │
   build answer            return answered=false
   with citations          with the reason
```

## The three signals

The refusal decision is the most important part of the system, so it is worth
being explicit about what each signal catches.

**Coverage** — what fraction of the question's *IDF-weighted* terms appear
anywhere in the retrieved passages. Weighting matters: "how long do refunds
take" has three content terms, but "refunds" is the one carrying the question.
Counting equally would score a correct answer at 0.33 and refuse it.

**Retrieval strength** — the best fused RRF score, normalised against the
theoretical ceiling (a chunk ranked first by both rankers). Catches the case
where the terms appear but the passages are only loosely related.

**Sentence support** — the best single sentence's coverage. Catches the case
where the terms are scattered across a passage but no one sentence answers the
question. This is the signal that stops a system from stitching together a
plausible-sounding answer from unrelated fragments.

Each failure returns a different message, so the user knows whether to rephrase,
add a document, or give up.

## Why hybrid retrieval

BM25 and TF-IDF vectors fail in different directions:

| | BM25 | TF-IDF vectors |
|---|---|---|
| Exact identifiers ("SKU-4471") | strong | weak |
| Rare names | strong | weak |
| Paraphrase ("cost" vs "price") | weak | moderate |
| Vocabulary overlap | strong | strong |
| Long documents | normalised by length | normalised by L2 |

Fusing them with reciprocal rank fusion means a chunk found by both outranks a
chunk found by either. RRF is used rather than score averaging because the two
rankers produce scores on incomparable scales — BM25 scores are unbounded,
cosine similarity is in [0, 1]. RRF only uses rank, which sidesteps the problem
entirely.

## Scaling notes

**Where it breaks first:** the in-memory index. It is rebuilt per process and
held in RAM. At roughly 10⁵ chunks this becomes slow to build and expensive to
hold. The fix is pgvector or a dedicated store, with the same `HybridRetriever`
interface behind it.

**Second:** the rate limiter. It is a dict in module scope, so N workers means
N× the configured limit. Redis with a sliding window is the standard fix.

**Third:** ingestion is synchronous. A large document blocks the request. A
background job queue (Celery, RQ, arq) with a status field on the document
would fix it.

**What scales fine:** the database layer. Every query is scoped by `owner_id`
and indexed on it, so adding users is linear in storage and constant in query
time.

## Security decisions

**Ownership returns 404, not 403.** Asking for another user's document returns
"not found" rather than "forbidden". A 403 confirms the record exists, which
leaks information about other tenants. 404 is the honest answer to "is there a
document here you can see".

**Secrets only from the environment.** `config.py` has no default for
`SECRET_KEY`. In production the app refuses to start without one, because a
default secret in a public repository is how sessions get forged.

**Every input is bounded.** `schemas.py` caps string lengths, question length
and upload size. An unbounded field is a denial-of-service vector.

**Passwords are bcrypt.** Not SHA-256, not a hand-rolled scheme. The cost
factor is the library default.

## Testing strategy

Three layers, in order of how much they protect:

1. **Engine tests** (`test_engine.py`, `test_quality.py`) — the retrieval logic
   in isolation. These are the ones that matter, because a bug here produces a
   confident wrong answer.
2. **API tests** (`test_api.py`) — auth, ownership isolation, status codes,
   ingestion round-trips.
3. **Browser verification** — done manually with Chromium, not automated. The
   screenshots in `docs/` are from a real session.

The quality tests are written as regression tests for specific bugs found by
running the app. Each one has a comment explaining what would have shipped
without it.
