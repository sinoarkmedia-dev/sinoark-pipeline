# sinoark-pipeline

Consolidated data pipeline for SinoArk — WeChat article ingestion, classification, translation, and digest generation.

## Structure

```
sinoark-pipeline/
├── sinoark-filter/                  Hourly AI/tech classification (FC — every hour :20)
├── translation-digest/              Hourly English translation via Gemini (FC — every hour :00)
├── digest/                          Daily AI digest generation via Gemini (FC — 11pm BJT)
├── sinoark_media_wechat_pipeline/   Core ingestion: sync, fetch, sources, archive, backup, cleanup
│                                    FC: daily-backup (11:59pm BJT), monthly-cleanup (5th 02:00 UTC)
│                                    ECS: container-sync, fetch-new, sync-sources, historical-backfill
├── sinoark-profiles/                Daily profiles via Claude Haiku (ECS — 11:30pm BJT)
├── fc-deploy/                       Serverless Devs config (s.yaml) for FC deployment
└── scripts/                         ECS scripts: wechat-scrape, bootstrap, backup-to-oss, crontab
```

## Jobs

| Job | Where | Schedule | Entry point |
|-----|-------|----------|-------------|
| AI Filtering | Function Compute | Every hour :20 | `sinoark-filter/filter.py` |
| Translation | Function Compute | Every hour :00 | `translation-digest/scripts/translate_batch.py` |
| Daily Digest | Function Compute | Daily 11pm BJT | `digest/generate_digest.py` |
| Daily Backup | Function Compute | Daily 11:59pm BJT | `sinoark_media_wechat_pipeline/digest/daily-backup.ts` |
| Monthly Cleanup | Function Compute | 5th @ 02:00 UTC | `sinoark_media_wechat_pipeline/digest/monthly-cleanup.ts` |
| WeChat Scrape | ECS cron | Every 4h | `scripts/wechat-scrape.sh` |
| Container Sync | ECS cron | Every hour :15 | `sinoark_media_wechat_pipeline/sync/container-sync.ts` |
| Fetch New | ECS cron | Every 30 min | `sinoark_media_wechat_pipeline/digest/fetch-new.ts` |
| Sync Sources | ECS cron | Every 2h | `sinoark_media_wechat_pipeline/sources/sync-sources.ts` |
| Historical Backfill | ECS cron | Every 3h | `sinoark_media_wechat_pipeline/archive/historical-backfill.ts` |
| Profiles | ECS cron | Daily 11:30pm BJT | `sinoark-profiles/generate_profiles.py` |

## Deployment

### Function Compute (FC)

```bash
cd fc-deploy/
export SUPABASE_URL=...
export SUPABASE_SERVICE_KEY=...
export GEMINI_API_KEY=...
s deploy                     # deploy all FC functions
s sinoark-filter deploy      # deploy one function
```

### ECS cron jobs

```bash
crontab scripts/crontab.txt
```

### Fresh ECS node

```bash
bash scripts/bootstrap-ecs.sh
```

## Secrets

Each sub-directory expects a `.env` file (gitignored). Required keys:

| Directory | Keys needed |
|-----------|------------|
| `sinoark-filter/` | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` |
| `translation-digest/` | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GEMINI_API_KEY` |
| `digest/` | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GEMINI_API_KEY` |
| `sinoark_media_wechat_pipeline/` | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `WERSS_BASE_URL`, `GEMINI_API_KEY` |
| `sinoark-profiles/` | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GEMINI_API_KEY` |

For FC functions, set these as environment variables in the FC console (not .env files).

## Related repos

- [sinoark-web](https://github.com/sinoarkmedia-dev/sinoark-web) — Next.js frontend (Vercel)
