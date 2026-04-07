#!/usr/bin/env bash
# =============================================================================
# SinoArk OSS Backup Script
# Backs up SQLite databases and .env files to Alibaba OSS.
# Run manually or add to crontab (runs daily at 03:00 UTC by default).
#
# Setup:
#   1. Install ossutil:
#      wget https://gosspublic.alicdn.com/ossutil/1.7.19/ossutil64 -O /usr/local/bin/ossutil
#      chmod +x /usr/local/bin/ossutil
#   2. Configure credentials:
#      ossutil config  (enter AccessKey ID, Secret, endpoint e.g. oss-cn-hangzhou.aliyuncs.com)
#   3. Set OSS_BUCKET below and uncomment the crontab line in scripts/crontab.txt
# =============================================================================
set -euo pipefail

OSS_BUCKET="${OSS_BUCKET:-YOUR_BUCKET_NAME}"
OSS_PREFIX="sinoark"
TIMESTAMP=$(date -u +%Y-%m-%d)

if [ "$OSS_BUCKET" = "YOUR_BUCKET_NAME" ]; then
  echo "ERROR: Set OSS_BUCKET environment variable or edit this script."
  echo "  export OSS_BUCKET=my-bucket && bash backup-to-oss.sh"
  exit 1
fi

if ! command -v ossutil &>/dev/null; then
  echo "ERROR: ossutil not found. Install it first:"
  echo "  wget https://gosspublic.alicdn.com/ossutil/1.7.19/ossutil64 -O /usr/local/bin/ossutil"
  echo "  chmod +x /usr/local/bin/ossutil"
  echo "  ossutil config"
  exit 1
fi

echo "[$(date -u +%H:%M:%S)] Starting SinoArk OSS backup → oss://$OSS_BUCKET/$OSS_PREFIX/"

# ── SQLite databases (containers' WeChat data + auth) ─────────────────────────
for i in 1 2 3; do
  case $i in
    1) SRC=/root/data/db.db;  DST="oss://$OSS_BUCKET/$OSS_PREFIX/data/db.db" ;;
    2) SRC=/root/data2/db.db; DST="oss://$OSS_BUCKET/$OSS_PREFIX/data2/db.db" ;;
    3) SRC=/root/data3/db.db; DST="oss://$OSS_BUCKET/$OSS_PREFIX/data3/db.db" ;;
  esac
  if [ -f "$SRC" ]; then
    ossutil cp -f "$SRC" "$DST" --meta "x-oss-meta-date:$TIMESTAMP"
    echo "  Backed up $SRC → $DST"
  else
    echo "  SKIP: $SRC not found"
  fi
done

# ── .env files (secrets) ──────────────────────────────────────────────────────
# Bundle all .env files into a single tar — do NOT store individually (security)
ENV_ARCHIVE=/tmp/sinoark-env-backup.tar.gz
find /root/workspace -maxdepth 3 -name ".env" | \
  tar -czf "$ENV_ARCHIVE" -T - 2>/dev/null || true
if [ -f "$ENV_ARCHIVE" ]; then
  ossutil cp -f "$ENV_ARCHIVE" \
    "oss://$OSS_BUCKET/$OSS_PREFIX/secrets/env-files.tar.gz" \
    --meta "x-oss-meta-date:$TIMESTAMP"
  rm -f "$ENV_ARCHIVE"
  echo "  Backed up .env files → oss://$OSS_BUCKET/$OSS_PREFIX/secrets/env-files.tar.gz"
fi

# ── Crontab ───────────────────────────────────────────────────────────────────
crontab -l > /tmp/sinoark-crontab.txt 2>/dev/null || true
ossutil cp -f /tmp/sinoark-crontab.txt \
  "oss://$OSS_BUCKET/$OSS_PREFIX/crontab.txt" \
  --meta "x-oss-meta-date:$TIMESTAMP"
echo "  Backed up crontab"

echo "[$(date -u +%H:%M:%S)] Backup complete"
