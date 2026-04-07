#!/usr/bin/env bash
# wechat-scrape.sh — trigger all 3 we-mp-rss containers to fetch latest articles from WeChat
# Runs fetch_all_article() inside each container (MaxPage=1, ~10 latest articles per feed)
# Scheduled: every 4 hours via cron

set -euo pipefail
mkdir -p /var/log

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "=== WeChat scrape started at $TIMESTAMP ==="

for CONTAINER in we-mp-rss we-mp-rss-2 we-mp-rss-3; do
  echo "--- $CONTAINER ---"
  docker exec "$CONTAINER" bash -c \
    "cd /app && source /app/environment.sh && source /app/env_x86_64/bin/activate && \
     python3 -c 'from jobs.mps import fetch_all_article; fetch_all_article()'" \
    2>&1 || echo "WARN: $CONTAINER scrape failed (container may be restarting)"
done

echo "=== WeChat scrape finished at $(date '+%Y-%m-%d %H:%M:%S') ==="
