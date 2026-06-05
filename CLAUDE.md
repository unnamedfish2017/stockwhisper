# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

stockwhisper（股情报）is a public-facing A-share (Chinese stock) rumor intelligence community. Users submit market "rumors" (recommendations from research institutions/individuals), which get AI-scored, tiered, summarized, and backtested against historical price data. A gamified reputation/unlock economy gates access to higher-value content. UI and all user-facing text are in Chinese.

## Commands

Commands are prefixed with `rtk` in this environment (a sandboxed runner present on PATH); plain `python3` also works.

```bash
# Install dependencies
python3 -m pip install -r requirements.txt

# Run the server (two equivalent entrypoints, both bind 0.0.0.0:8289)
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8289
python3 -m app.main                      # respects HOST / PORT env vars

# Tests (none checked in yet; convention if adding them)
pytest -q                                # use FastAPI TestClient under tests/, name test_<feature>.py
```

The UI serves from `/`, static assets from `/static`.

## Architecture

Nearly all backend logic lives in a single module, `app/main.py` (~960 lines). Keep it there unless a change clearly justifies splitting. The frontend is vanilla HTML/JS/CSS in `static/` (`index.html`, `app.js`, `styles.css`) — no build step, no framework. `app.js` talks to the JSON API under `/api/*`.

### Data sources (all optional; app degrades gracefully when absent)

- `data/stockwhisper.db` — SQLite, created and migrated at runtime on startup. This is local runtime state, not a fixture.
- `../私有信息/info_collection.db` — private seed DB. On first start, when the `rumors` table is empty, `seed_reference_data()` imports its `recommendations` rows as `source='reference'` rumors.
- `/home/vscode/workspace/data/store/rsync/tonglian_data_daily/` — local A-share daily market data. Backtests prefer `tonglian_stock_day_n.parquet` (raw unadjusted `close`), falling back to `tonglian_data_daily.pickle` (forward-adjusted `closew`, plus `WA_names_cn` for name→code lookup). No market data → backtests are skipped silently.

### Core domain flows

These interlocking systems are the heart of the app and span multiple functions:

- **Scoring** (`score_text`): heuristic 1–100 score from ticker/catalyst keyword density and text length → maps to tier S/A/B/C. Pure function, no LLM.
- **Summarization** (`summarize_payload`): `heuristic_summary` (regex extraction of target/logic/institution/key_points) is the base, merged with optional `llm_summary` when an API key is set. `llm_summary` tries OpenAI-compatible `/chat/completions` first, then falls back to the `/responses` endpoint. Always returns at least the heuristic result.
- **Tier access & contribution** (`tier_access`, `contribution_for_user`): contribution is a time-decayed sum of a user's submitted rumor scores (30-day half-life, `CONTRIBUTION_HALF_LIFE_DAYS`). A user can view a tier if it's free, or their XP / contribution clears the `TIER_RULES` threshold.
- **Unlock economy** (`unlock_similar`, `can_view`, `public_rumor`): submitting a rumor auto-unlocks one random rumor of comparable score (±12). Locked rumors are returned with masked target (`mask_target`) and hidden fields. Ownership, prior unlock, or tier access all grant visibility.
- **Backtest + reputation** (`refresh_backtests` → `recalc_user_scores`): maps each rumor's target to a stock code (`find_code` via name lookup or embedded ticker), computes 5/20/60-day and max-60-day returns from the recommendation date, stores in `backtests`. `recalc_user_scores` then derives every submitter's XP, reputation, and direct quota from their rumors' scores and realized returns, and updates `level_for` tiers. These run together after each submission and on manual refresh.

### Database schema

Five tables, defined in `init_db()` via `executescript`: `users`, `sessions`, `rumors`, `unlocks` (composite PK user_id+rumor_id), `backtests` (PK rumor_id). Migrations are handled inline (e.g. the `pragma table_info` check that adds `key_points`) — follow this pattern for additive schema changes rather than introducing a migration tool.

### Auth & sessions

Cookie-based (`agu_session`, httponly, 30-day). `current_user` resolves the session or transparently creates a guest user — so every endpoint always has a user, and guests are first-class. Passwords use PBKDF2-HMAC-SHA256 (120k iterations) with per-user salt; verification is constant-time (`hmac.compare_digest`).

## Conventions

- All DB access goes through the `db()` / `execute()` / `query()` helpers with parameterized SQL — never build SQL by string interpolation.
- Pydantic `BaseModel`s with explicit `Field` limits define API payloads (`AuthPayload`, `RumorPayload`).
- snake_case Python; camelCase JS only where existing browser code already does.
- LLM config via env vars: `AGUWHISPER_LLM_API_KEY` / `OPENAI_API_KEY` (key), plus optional `*_BASE_URL`, `*_MODEL`, `AGUWHISPER_LLM_TIMEOUT`. Code path is a no-op without a key.
- Treat `data/stockwhisper.db` and the private DB as local-only — never commit real databases or credentials.
