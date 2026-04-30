# Telegram Messages Use a Separate Database

## Architecture
Telegram messages (`telegram_msgs` table) live in `telegram.db`, a sibling file next to the main `project-lens.db`. This is by design — the ingest script (`telegram_ingest.py`) creates a separate SQLAlchemy engine pointing at `telegram.db` so that Telegram data can be selectively shared without exposing GitHub tokens or other sensitive data in the main database.

## Consequence for LensClient API
The `LensClient` API (used by consumers like Project PM) originally only had an engine for the main database. When `list_telegram_participants()` was added, it initially queried the main DB and returned zero results — the `telegram_msgs` table in the main DB exists (created by SQLAlchemy's `create_all`) but is always empty.

The fix was to add a second engine/session in `LensConfig` specifically for `telegram.db`:
- `LensConfig.telegram_engine` / `LensConfig.telegram_session` — points to `{var_dir}/telegram.db`
- `TelegramService.list_participants()` uses `self._config.telegram_session()`

## How to Tell Which DB Has Data
- Main DB (`project-lens.db`): `github_prs`, `pr_assessments`, `ai_digests`, `contributor_profiles`, `issue_assessments`
- Telegram DB (`telegram.db`): `telegram_msgs` only
- Both DBs have the `telegram_msgs` table schema (SQLAlchemy creates it in both), but only `telegram.db` has actual rows
