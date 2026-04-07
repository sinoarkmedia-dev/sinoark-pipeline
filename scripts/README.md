# scripts

**Status: MIXED — some active, some deprecated**

Utility and operational scripts for running, monitoring, and deploying the SinoArk platform.

## Active Scripts

| Script | Purpose |
|---|---|
| `wechat-scrape.sh` | WeChat content scraping (cron every 4h) |
| `git-autocommit.sh` | Auto-commit changes on a schedule |
| `monitor_backfill.py` | Monitor backfill progress |
| `monitor-backfill.sh` | Shell wrapper for backfill monitoring |
| `generate_digest.py` | Generate daily digest |
| `sync_rss_april2.py` | RSS sync for April 2026 onwards |
| `deploy-sinoark.sh` | Deploy helper for SinoArk services |
| `sinoark-start.sh` | Start all SinoArk services |
| `check_pipeline_status.py` | Check status of pipeline processes |

## Broken / Deprecated Scripts

| Script | Issue |
|---|---|
| `run-sync.sh` | References `/root/scripts/deep-archive.ts` — path broken, script non-functional |
| `aggressive-backfill-66.sh` | One-time backfill operation, no longer needed |
| `backfill-all-65-priority.sh` | One-time priority backfill, no longer needed |
| `backfill-all-containers.sh` | One-time batch operation |
| `continuous-backfill-accelerator.sh` | Temporary backfill acceleration script |
| `force-backfill-all-66.py` | One-time forced backfill |
| `run-full-backfill.sh` | One-time full backfill |

## Placement Convention

All new scripts should be placed in this directory (`workspace/scripts/`). See `workspace/CLAUDE.md` for the project organization guidelines.
