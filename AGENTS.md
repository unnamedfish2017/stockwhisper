# Repository Guidelines

## Project Structure & Module Organization

stockwhisper（股情报）is a small FastAPI application with a static browser UI.

- `app/main.py` contains the FastAPI app, SQLite access, auth/session logic, rumor scoring, summarization, unlock rules, and backtest APIs.
- `app/__init__.py` marks the backend package.
- `static/index.html`, `static/app.js`, and `static/styles.css` provide the client UI and API calls.
- `data/stockwhisper.db` is the local SQLite database created or updated at runtime.
- `requirements.txt` lists Python runtime dependencies.

Keep backend behavior in `app/main.py` unless a change clearly justifies splitting modules. Keep static assets in `static/`.

## Build, Test, and Development Commands

Install dependencies:

```bash
rtk python3 -m pip install -r requirements.txt
```

Run the server with Uvicorn:

```bash
rtk python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8289
```

Run through the module entrypoint:

```bash
rtk python3 -m app.main
```

The app serves the UI from `/` and static files from `/static`. On first start it creates `data/stockwhisper.db`; when present, it may import sanitized source data from `../私有信息/info_collection.db` and read local A-share market data from `/home/vscode/workspace/data/store/rsync`.

## Coding Style & Naming Conventions

Use Python 3 style with 4-space indentation, type hints for new helpers, and small functions around database or scoring behavior. Keep API payload models as Pydantic `BaseModel` classes with clear field limits. Use snake_case for Python names and camelCase for JavaScript functions only where existing browser code already does so. Prefer explicit SQL statements and parameterized queries.

## Testing Guidelines

No test suite is currently checked in. For backend changes, add focused `pytest` tests under `tests/` and use FastAPI `TestClient` for route behavior. Name tests `test_<feature>.py`. For data or scoring changes, cover both expected and edge cases, especially empty fields, locked tiers, session cookies, and missing market data.

Run tests with:

```bash
rtk pytest -q
```

## Commit & Pull Request Guidelines

This directory has no local Git history, so no project-specific commit convention is available. Use concise imperative subjects, preferably Conventional Commits, for example `feat: add rumor tier filter` or `fix: handle missing backtest data`.

Pull requests should include the user-facing impact, API or schema changes, manual verification steps, and screenshots for UI changes. Note any database migration or data reset requirement.

## Security & Configuration Tips

Do not commit real private databases, API keys, or generated credentials. LLM summarization reads `AGUWHISPER_LLM_API_KEY` or `OPENAI_API_KEY`, plus optional base URL, model, and timeout environment variables. Treat `data/stockwhisper.db` as local runtime state.
