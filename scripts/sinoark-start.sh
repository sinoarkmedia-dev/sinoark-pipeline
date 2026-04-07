#!/usr/bin/env bash
# ============================================================
# SinoArk Full Pipeline Launcher
# Starts RSS expansion workers + Supabase sync
# Usage: bash /root/sinoark-start.sh [num_containers]
# ============================================================
set -e

NUM=${1:-1}   # number of logged-in we-mp-rss containers (default 1)
LOG_DIR="/tmp/sinoark-logs"
mkdir -p "$LOG_DIR"

CONTAINERS=("we-mp-rss" "we-mp-rss-2" "we-mp-rss-3")
PORTS=(8001 8004 8003)

echo "=== SinoArk starting — $NUM RSS worker(s) + continuous sync ==="
echo ""

# ── Stop any previous workers ─────────────────────────────────
for c in "${CONTAINERS[@]}"; do
  docker exec "$c" pkill -f deep-history 2>/dev/null || true
done
pkill -f deep-archive 2>/dev/null || true
echo "[ok] Stopped previous workers"

# ── Launch RSS expansion workers (one per logged-in container) ─
for ((i=0; i<NUM; i++)); do
  C="${CONTAINERS[$i]}"
  LOG="$LOG_DIR/rss-worker-${i}.log"
  echo "[start] RSS worker $i in $C (port ${PORTS[$i]}) → $LOG"
  docker exec -d "$C" bash -c \
    "source /app/environment.sh && source /app/env_x86_64/bin/activate && \
     python3 /tmp/deep-history.py $i $NUM >> /tmp/dh-w${i}.log 2>&1"
done
echo ""

# ── Launch Supabase sync (continuous, 60s between rounds) ─────
SYNC_LOG="$LOG_DIR/sync.log"
echo "[start] Supabase sync → $SYNC_LOG"
cd /root/wechat-rss-archive
nohup npx tsx scripts/deep-archive.ts >> "$SYNC_LOG" 2>&1 &
SYNC_PID=$!
echo "  PID: $SYNC_PID"
echo ""

# ── Status ────────────────────────────────────────────────────
sleep 4
echo "=== Status ==="
for ((i=0; i<NUM; i++)); do
  C="${CONTAINERS[$i]}"
  RUNNING=$(docker exec "$C" pgrep -f deep-history > /dev/null 2>&1 && echo RUNNING || echo STOPPED)
  echo "  RSS worker $i ($C): $RUNNING"
done
echo "  Supabase sync    : $(pgrep -f deep-archive > /dev/null && echo RUNNING || echo STOPPED)"
echo ""
echo "Monitor:"
echo "  RSS worker 0 : docker exec we-mp-rss tail -f /tmp/dh-w0.log"
for ((i=1; i<NUM; i++)); do
  echo "  RSS worker $i : docker exec ${CONTAINERS[$i]} tail -f /tmp/dh-w${i}.log"
done
echo "  Supabase sync: tail -f $SYNC_LOG"
