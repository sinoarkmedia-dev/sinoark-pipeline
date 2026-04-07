# sinoark_media_wechat_pipeline

**Status: ACTIVE — primary ingestion pipeline**

Core data ingestion system that syncs WeChat articles from 3x we-mp-rss Docker containers into Supabase. This is the main, current pipeline (supersedes `wechat-rss-archive`).

## What It Does

1. **Container Sync** — pulls SQLite records from 3 we-mp-rss containers (ports 8001, 8003, 8004) into Supabase PostgreSQL
2. **Digest** — fetches new articles (April 1, 2026 onwards) every 30 minutes into `articles_digest`
3. **Archive** — backfills historical articles (2021–March 31, 2026) every 3 hours into `articles_archive`
4. **Sources** — auto-registers and profiles new WeChat accounts every 2 hours

## Tech Stack

- Node.js 22+, TypeScript, tsx
- Supabase (PostgreSQL) — project `fbjpgaqoldptjnejrbeh`
- Gemini API (English source profiling / translation)
- Qwen API (Chinese source profiling — two-step flow added Apr 4, 2026)

## Directory Structure

```
digest/            # Real-time article ingestion (articles_digest table)
  fetch-new.ts     # Main cron entrypoint
archive/           # Historical article backfill (articles_archive table)
  historical-backfill.ts
sources/           # WeChat account registration and profiling
  sync-sources.ts
sync/              # SQLite → Supabase sync logic
  container-sync.ts
monthly-backup.ts  # Move digest articles to archive monthly
monthly-cleanup.ts # Cleanup old records
```

## Active Cron Jobs

| Schedule | Script | Purpose |
|---|---|---|
| `*/30 * * * *` | `digest/fetch-new.ts` | Fetch new articles every 30 min |
| `15 * * * *` | `sync/container-sync.ts` | Sync containers → Supabase hourly |
| `0 */2 * * *` | `sources/sync-sources.ts` | Auto-register new accounts |
| `0 */3 * * *` | `archive/historical-backfill.ts` | Backfill historical articles |
| `0 2 1 * *` | `monthly-backup.ts` | Monthly digest → archive migration |
| `0 2 5 * *` | `monthly-cleanup.ts` | Monthly cleanup |

## Data Sources

- Container 1 (port 8001): ~212 WeChat feeds → `data/original/db.db` (370 MB)
- Container 2 (port 8004): ~178 WeChat feeds → `data/data2/db.db` (65 MB)
- Container 3 (port 8003): ~178 WeChat feeds → `data/data3/db.db` (73 MB)

## Source Profiling Flow (Two-Step)

1. Qwen API → generate Chinese profile for each WeChat account
2. Gemini API → translate Chinese profile to English

Added April 4, 2026. Requires a valid Qwen API key in `.env`.
