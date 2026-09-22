# LogSleuth Backend

FastAPI backend for the AI Log Investigation Platform. It parses plain-text and
JSON-lines logs, detects error spikes over time windows, and infers a possible
cause using a user-extensible rule-based knowledge base.

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
