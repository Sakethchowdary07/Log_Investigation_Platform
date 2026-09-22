# AI Log Investigation Platform (LogSleuth)

Upload application or server logs and get an automated first-pass investigation:
the platform detects **error spikes**, infers a **possible cause**, lists the
**related errors**, and produces an **ordered set of suggested investigation
steps**. If you also upload a deployment timeline, spikes are correlated to
nearby deploys so you can spot "this started right after the 10:39 AM release".

Example result for the bundled sample log:

```
ERROR SPIKE
────────────
10:42 AM

Possible cause:
Database connection pool exhaustion

Related errors:
• DB timeout
• Connection refused
• HTTP 503

Suggested investigation:
1. Check DB connections
2. Check CPU
3. Check connection pool
4. Review deployment at 10:39 AM
```

## Design: rule-based, with a user-extensible knowledge base

Detection is **rule-based** and driven entirely by a knowledge base config
file. There is **no hardcoded cause-inference logic** in the engine: every
inferred cause, related-error list, and suggested-step list comes from rules
you can read and edit in `backend/app/config/knowledge_base.yaml` (see
[Extending the knowledge base](#extending-the-knowledge-base)).

The analyzer sits behind a small interface (`Analyzer` in
`backend/app/knowledge_base.py`), and the v1 implementation is
`RuleBasedAnalyzer`. Because callers depend only on the `Analyzer` contract, an
**LLM-backed analysis mode can be added later** as an alternative `Analyzer`
implementation without changing the detection pipeline or the API.

## Architecture

- **Backend** – FastAPI application (package `app`) that parses logs, buckets
  ERROR-level entries into time windows to find spikes, matches error messages
  against the knowledge base, and persists uploads and analyses to **SQLite**.
- **Frontend** – React + TypeScript single-page app built with **Vite**. In dev
  it proxies `/api` calls to the backend, so no CORS setup is needed locally.
- **Persistence** – SQLite via the stdlib `sqlite3` module.

```
project root
├── backend/            FastAPI app, tests, knowledge base, sample logs
│   ├── app/
│   │   ├── main.py                 API routes (upload, analyses, knowledge-base, health)
│   │   ├── parsers.py              plain-text + JSON-lines log parsing
│   │   ├── detection.py            error-spike detection
│   │   ├── knowledge_base.py       Rule / KnowledgeBase / Analyzer / RuleBasedAnalyzer
│   │   ├── correlation.py          deployment-timeline correlation
│   │   ├── storage.py              SQLite persistence
│   │   └── config/knowledge_base.yaml   the rules (editable)
│   ├── samples/        app.log, app.jsonl, deployments.csv
│   └── tests/
└── frontend/           Vite React TypeScript dashboard
```

## Prerequisites

- **Python 3.11** (the repo pins `3.11.15` via the root `.python-version`).
- **[uv](https://github.com/astral-sh/uv)** for backend dependency management.
- **Node.js 22 + npm 11** for the frontend.

## Backend: setup and run

```bash
cd backend

# Install runtime + dev dependencies (pytest, httpx) into a local .venv
uv sync --extra dev

# Run the test suite
uv run pytest -q

# Start the API server (http://localhost:8000)
uv run uvicorn app.main:app --reload --port 8000
```

Once running, the interactive Swagger UI is available at
[http://localhost:8000/docs](http://localhost:8000/docs).

Key API endpoints:

- `GET  /api/health` – liveness check.
- `POST /api/upload` – multipart upload of a log `file` (and optional
  `deployments` CSV); returns the analysis result.
- `GET  /api/analyses` – list previously stored analyses.
- `GET  /api/analyses/{id}` – fetch a stored analysis by id.
- `GET  /api/knowledge-base` – return the currently loaded rules.

### Environment variables

| Variable      | Purpose                              | Default                        |
| ------------- | ------------------------------------ | ------------------------------ |
| `LOG_KB_PATH` | Path to the knowledge base YAML file | `backend/app/config/knowledge_base.yaml` |
| `LOG_DB_PATH` | Path to the SQLite database file     | `backend/data/logsleuth.db`    |

The database file and its parent directory are created automatically on
startup.

## Frontend: setup and run

```bash
cd frontend

# Install dependencies
npm install

# Start the Vite dev server (http://localhost:5173)
# It proxies /api requests to the backend on http://localhost:8000
npm run dev

# Produce a production build in frontend/dist
npm run build
```

The dev server listens on port **5173** and proxies `/api` to the backend on
port **8000**, so start the backend first (or alongside) for uploads to work.

## End-to-end demo walkthrough

1. Start the backend: `cd backend && uv run uvicorn app.main:app --reload --port 8000`
2. In a second terminal, start the frontend: `cd frontend && npm run dev`
3. Open [http://localhost:5173](http://localhost:5173).
4. Upload the sample log `backend/samples/app.log`. (A JSON-lines sample,
   `backend/samples/app.jsonl`, is also provided and is parsed the same way.)
5. Optionally also upload the deployment timeline
   `backend/samples/deployments.csv` in the same submission.

Expected result for `app.log`:

- An **ERROR SPIKE** around **10:42 AM** (the peak window of ERROR/CRITICAL
  entries).
- Possible cause: **Database connection pool exhaustion**.
- Related errors: **DB timeout**, **Connection refused**, **HTTP 503**.
- Suggested investigation steps, in order:
  1. Check DB connections
  2. Check CPU
  3. Check connection pool
- When `deployments.csv` is included, the spike is correlated to the
  nearby deploy and a step to **review the deployment at 10:39 AM**
  (orders-api v3.8.0, "Connection pool config change") is surfaced.

## Extending the knowledge base

All detection rules live in `backend/app/config/knowledge_base.yaml`. Edit that
file to add or change causes, or point `LOG_KB_PATH` at your own file to use a
different rule set without touching the shipped one:

```bash
LOG_KB_PATH=/path/to/my_rules.yaml uv run uvicorn app.main:app --reload --port 8000
```

### YAML schema

The file has a top-level `rules:` list. Each rule has these fields (mirrored by
the `Rule` model in `backend/app/knowledge_base.py`):

| Field             | Required | Description                                                                 |
| ----------------- | -------- | --------------------------------------------------------------------------- |
| `id`              | yes      | Stable unique identifier for the rule.                                      |
| `name`            | yes      | Human-readable rule name.                                                   |
| `match.any_of`    | yes\*    | Rule matches a message if **any** listed pattern matches.                   |
| `match.all_of`    | no       | If given, **all** listed patterns must also match.                          |
| `possible_cause`  | no       | The cause label shown to the user.                                          |
| `related_errors`  | no       | Symptom labels shown to the user.                                           |
| `suggested_steps` | no       | Ordered investigation steps (order is preserved).                           |
| `weight`          | no       | Score multiplier, default `1.0`; higher values win ties.                    |

\* Provide at least one of `any_of` / `all_of`; a rule with no patterns matches
nothing.

Patterns are **case-insensitive regular expressions** (plain substrings work
too, since they are valid regexes). A rule's score for a spike window is
`(number of matching error messages) * weight`; the highest-scoring rule wins
and supplies the cause, related errors, and suggested steps.

### Example rule

```yaml
rules:
  - id: tls_handshake_failure
    name: TLS handshake failure
    match:
      any_of:
        - "tls handshake"
        - "ssl handshake failed"
        - "certificate verify failed"
    possible_cause: "TLS/SSL handshake failure"
    related_errors:
      - "TLS handshake timeout"
      - "Certificate verification failed"
    suggested_steps:
      - "Check certificate validity and chain"
      - "Verify system clock / cert expiry"
      - "Confirm supported TLS versions and ciphers"
    weight: 1.0
```

After editing the YAML, restart the backend so the new rules are loaded.
