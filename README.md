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
│   │   ├── parsers.py              plain-text + JSON-lines + mainframe log parsing
│   │   ├── detection.py            error-spike detection
│   │   ├── knowledge_base.py       Rule / KnowledgeBase / Analyzer / RuleBasedAnalyzer
│   │   ├── correlation.py          deployment-timeline correlation
│   │   ├── storage.py              SQLite persistence
│   │   └── config/knowledge_base.yaml   the rules (editable)
│   ├── samples/        app.log, app.jsonl, deployments.csv, mainframe-joblog.log, db2-sqlcode.log, cics-abend.log
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

## Mainframe log support

LogSleuth can also investigate **mainframe logs** in addition to application and
server logs. When an uploaded file looks like mainframe output, the parser
routes it through a dedicated path that normalizes the lines into the same
entry shape used everywhere else, while surfacing the significant codes (message
IDs, abend codes, CICS abend codes, DB2 SQLCODEs and reason codes) as plain
tokens so the knowledge base can match them. No change to the detection pipeline
or the frontend is required: mainframe findings render in the same detection
card as every other result.

### Log styles handled

- **z/OS SYSLOG / OPERLOG** operator and system message streams.
- **JES job logs** (JESMSGLG / JESYSMSG style job and step output).

Timestamp conventions common to these styles are recognized where present
(for example `HH.MM.SS` SYSLOG times), with a graceful fallback when a line
carries no usable timestamp.

### Code categories recognized

The seeded rules cover four categories, in the following priority order:

1. **DB2 for z/OS SQLCODEs** – for example `SQLCODE -911` / `-913`
   (deadlock or lock timeout rollback), `-904` resource unavailable
   (the `00C9xxxx` reason-code family), `-805`, `-818` / `-803`,
   `-530` / `-531`, `-204`, and `-922`.
2. **z/OS system completion (abend) codes** – for example `S0C1`, `S0C4`,
   `S0C7`, `S0CB`, `S806`, `S80A`, `S822`, `S878`, `S913`, `SB37` / `SD37` /
   `SE37`, and `S213` / `S214`.
3. **CICS transaction abends** – for example `ASRA`, `ASRB`, `AICA`, `AKEA`,
   `AEY9`, and the `AEIx` / `AEXx` families.
4. **JES / system messages** – for example `IEF450I` (job or step abend),
   `IEC141I` (open or close error), `ICH408I` (RACF access or authorization),
   `IRA200E` (storage shortage), and `IEA995I` (symptom dump).

### Single-critical-message detection

Classic spike detection still works exactly as before: a window of ERROR-level
entries at or above the threshold becomes an **ERROR SPIKE**. On top of that,
mainframe support adds a **single-critical-message** mode. Rules may be flagged
`critical: true`, and when a message matches such a rule it triggers a
**`CRITICAL MESSAGE`** detection on its own: a single `S0C7`, a lone
`SQLCODE -911`, or one `ASRA` is enough to open an investigation even without a
burst. The cause, related errors, and ordered suggested steps still come
entirely from the matched YAML rule; the `critical` flag only changes the
**trigger**, never where the explanation comes from.

> **Note on auto-detection.** LogSleuth auto-detects mainframe logs by sampling
> the uploaded file and routing it through the mainframe parser only when the
> majority of lines carry a mainframe marker (a z/OS message ID, abend token,
> CICS abend, SQLCODE, or SYSLOG prefix). This conservative default avoids false
> positives from ordinary logs that merely mention a mainframe-ish token in
> prose, so a genuine mainframe code sitting inside a mostly plain-text log may
> not auto-route to the mainframe parser and therefore may not trigger
> single-critical detection. If you want to force mainframe parsing in that case,
> call `parse_logs(text, fmt="mainframe")`.

### Sample files

Three mainframe samples are bundled under `backend/samples/`. Upload any of
them through the UI (or `POST /api/upload`) to see the behavior.

- **`mainframe-joblog.log`** – a JES / z/OS job-log excerpt including an
  `IEF450I MYJOB STEP01 - ABEND=S0C7` data-exception abend, an `ICH408I` RACF
  access line, an `IEC141I` open error, and an `IEA995I` symptom dump. Expected
  output:

  ```
  CRITICAL MESSAGE
  ────────────────
  Possible cause:
  A data exception: a decimal/arithmetic operation ran on fields containing
  invalid (non-numeric or improperly initialized) packed/zoned data.

  Suggested investigation:
  1. Identify the failing instruction and the numeric field it operated on using the dump offset.
  2. Check that the field is initialized and contains valid packed/zoned numeric data before arithmetic.
  3. Validate input data and move layouts; a bad file record or redefinition is a common source.
  ```

- **`db2-sqlcode.log`** – a DB2 for z/OS diagnostic snippet with a
  `SQLCODE -911` deadlock/timeout rollback line and a `SQLCODE -904`
  resource-unavailable line referencing a generic `00C9xxxx` reason code.
  Expected output:

  ```
  CRITICAL MESSAGE
  ────────────────
  Possible cause:
  DB2 detected a deadlock or lock timeout; the current unit of work was
  rolled back (-911) or not rolled back (-913).

  Suggested investigation:
  1. Identify the contending threads/transactions holding incompatible locks around the failure time.
  2. Review commit frequency and lock scope; shorten units of work and commit more often.
  3. Check access paths and indexing so rows are locked consistently and briefly.
  4. Retry the transaction after rollback where the application design allows it.
  ```

- **`cics-abend.log`** – a CICS transaction-abend excerpt with `ABCODE=ASRA`
  lines. Expected output: a `CRITICAL MESSAGE` detection whose possible cause
  describes the `ASRA` program check (a storage or data exception in the
  application program).

### Extending the mainframe knowledge base

Mainframe rules live in the **same** `backend/app/config/knowledge_base.yaml`
file and use the **same YAML schema** as every other rule (see
[Extending the knowledge base](#extending-the-knowledge-base) below). Just add a
new entry to the `rules:` list whose `match.any_of` patterns include the code
you want to detect (patterns are case-insensitive regexes, so `S0C7`,
`SQLCODE -911`, or `AEI[0-9A-Z]` all work).

Two **optional** fields support the mainframe workflow:

| Field         | Type            | Description                                                                                 |
| ------------- | --------------- | ------------------------------------------------------------------------------------------- |
| `critical`    | bool (`false`)  | When `true`, a single matching message triggers a `CRITICAL MESSAGE` detection on its own.  |
| `verify_note` | string (`null`) | A reminder of what to confirm against the official IBM manuals before relying on the rule.  |

Both are back-compatible: rules that omit them load exactly as before, and they
are exposed through `GET /api/knowledge-base`. Example:

```yaml
rules:
  - id: db2_sqlcode_deadlock_timeout
    name: DB2 deadlock or lock timeout (SQLCODE -911 / -913)
    match:
      any_of:
        - "SQLCODE -911"
        - "SQLCODE -913"
    possible_cause: "DB2 detected a deadlock or lock timeout; the current unit of work was rolled back (-911) or not rolled back (-913)."
    related_errors:
      - "Deadlock"
      - "Lock timeout"
    suggested_steps:
      - "Identify the contending threads/transactions holding incompatible locks around the failure time."
      - "Review commit frequency and lock scope; shorten units of work and commit more often."
    weight: 2.0
    critical: true
    verify_note: "Verify the exact SQLCODE meaning and reason codes against DB2 for z/OS Codes before production reliance."
```

> ### Important: verify against the official IBM manuals
>
> The seeded mainframe rules are **compiled from general, well-established
> mainframe knowledge**. They are **not** verbatim IBM documentation and were
> **not** taken from IBM's official docs. The code meanings and remediation
> steps are paraphrased general guidance intended to give a useful first-pass
> investigation, not authoritative text.
>
> Before relying on any mainframe rule in production, **verify the specifics**
> (exact code meanings, reason-code sub-values, and recommended programmer
> responses) against the official IBM manuals, in particular:
>
> - **z/OS MVS System Codes** and **z/OS MVS System Messages** (abends and
>   system or JES messages).
> - **DB2 for z/OS Codes** (SQLCODEs and reason codes).
> - **CICS Transaction Server Messages and Codes** (transaction abends).
>
> Each seeded mainframe rule carries a `verify_note` field as a built-in
> reminder of this. As with every rule, the knowledge base is fully editable:
> correct, extend, or replace these entries to match your own validated
> references.

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
| `critical`        | no       | If `true`, a single matching message triggers a `CRITICAL MESSAGE` detection on its own (used by the mainframe rules; see [Mainframe log support](#mainframe-log-support)). Default `false`. |
| `verify_note`     | no       | Free-text reminder of what to confirm against an authoritative source before relying on the rule. Default none. |

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
