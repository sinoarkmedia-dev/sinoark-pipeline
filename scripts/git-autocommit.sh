#!/usr/bin/env bash
# Auto-commits code changes in all SinoArk projects every 6 hours.
# Only commits if there are actual changes.

DIRS=(
  /root/wechat-rss-archive
  /root/sinoark-web
  /root/translation-digest
  /root/stakeholder-labelling
)

TS=$(date -u +"%Y-%m-%d %H:%M UTC")
CHANGED=0

for dir in "${DIRS[@]}"; do
  cd "$dir" || continue
  # Stage all tracked + untracked (except ignored)
  git add -A 2>/dev/null
  if ! git diff --cached --quiet; then
    git commit -m "auto: $TS" --quiet
    echo "[git] committed $dir"
    CHANGED=$((CHANGED + 1))
  fi
done

[ $CHANGED -eq 0 ] && echo "[git] no changes to commit"
