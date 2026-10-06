# LogSleuth Backend

FastAPI backend for the AI Log Investigation Platform. It parses plain-text,
JSON-lines, and mainframe logs, detects error spikes over time windows, and
infers a possible cause using a user-extensible rule-based knowledge base.

Mainframe support (z/OS abends, DB2 for z/OS SQLCODEs, CICS transaction abends,
JES and system messages) and the single-critical-message detection mode are
documented in the repository root README under "Mainframe log support",
including the important note to verify the seeded rules against the official
IBM manuals.

See the repository root README for full run instructions.

## Quick start

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

Run the tests:

```bash
uv run pytest -q
```
