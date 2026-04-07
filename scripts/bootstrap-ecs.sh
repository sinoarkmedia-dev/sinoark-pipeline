#!/usr/bin/env bash
# =============================================================================
# SinoArk ECS Bootstrap Script
# Provisions a fresh Ubuntu 24.04 ECS node from scratch.
# Run as root after the node is created.
#
# Usage:
#   bash bootstrap-ecs.sh
#
# Prerequisites before running:
#   1. Copy /root/workspace/ from OSS or another node (see backup-to-oss.sh)
#   2. Restore /root/data/, /root/data2/, /root/data3/ from OSS backup
#      (or accept that WeChat containers will need re-authentication via QR code)
#   3. Have your secrets ready (see "Secrets" section below)
#
# What this script does:
#   - Installs Docker, nvm, Node.js 24, Python deps
#   - Creates the 3 we-mp-rss Docker containers with correct config
#   - Installs npm + pip dependencies
#   - Installs the crontab
#   - Enables Docker to start on boot
# =============================================================================
set -euo pipefail

WORKSPACE=/root/workspace
PIPELINE=/root/workspace/sinoark-pipeline
NODE_VERSION=24
IMAGE=docker.1ms.run/rachelos/we-mp-rss:latest

echo "============================================================"
echo "  SinoArk ECS Bootstrap — Ubuntu 24.04"
echo "============================================================"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
  git curl wget ca-certificates gnupg lsb-release \
  python3 python3-pip python3-venv \
  build-essential

# ── 2. Docker ─────────────────────────────────────────────────────────────────
echo "[2/8] Installing Docker..."
if ! command -v docker &>/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io
fi
systemctl enable docker
systemctl start docker
echo "  Docker $(docker --version)"

# ── 3. nvm + Node.js ─────────────────────────────────────────────────────────
echo "[3/8] Installing nvm + Node.js ${NODE_VERSION}..."
if [ ! -d "$HOME/.nvm" ]; then
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
fi
export NVM_DIR="$HOME/.nvm"
# shellcheck source=/dev/null
source "$NVM_DIR/nvm.sh"
nvm install "$NODE_VERSION"
nvm use "$NODE_VERSION"
nvm alias default "$NODE_VERSION"
# Make node available system-wide for cron
NODE_BIN=$(nvm which "$NODE_VERSION")
ln -sf "$NODE_BIN" /usr/local/bin/node
ln -sf "$(dirname "$NODE_BIN")/npm" /usr/local/bin/npm
ln -sf "$(dirname "$NODE_BIN")/npx" /usr/local/bin/npx
echo "  Node $(node --version)"

# ── 4. Workspace ─────────────────────────────────────────────────────────────
echo "[4/8] Checking workspace..."
if [ ! -d "$WORKSPACE" ]; then
  echo ""
  echo "  ERROR: $WORKSPACE not found."
  echo "  Restore it from OSS first, then re-run this script:"
  echo ""
  echo "    # Install ossutil:"
  echo "    wget https://gosspublic.alicdn.com/ossutil/1.7.19/ossutil64 -O /usr/local/bin/ossutil"
  echo "    chmod +x /usr/local/bin/ossutil"
  echo "    ossutil config   # enter AccessKey ID, Secret, endpoint"
  echo ""
  echo "    # Restore workspace:"
  echo "    ossutil cp -r oss://<YOUR_BUCKET>/sinoark/pipeline/ /root/workspace/sinoark-pipeline/
    # Also clone sinoark-web separately:
    # git clone https://TOKEN@github.com/sinoarkmedia-dev/sinoark-web.git /root/workspace/sinoark-web"
  echo ""
  exit 1
fi
echo "  Found $WORKSPACE"

# ── 5. Data directories ───────────────────────────────────────────────────────
echo "[5/8] Setting up data directories..."
mkdir -p /root/data /root/data2 /root/data3
mkdir -p /var/log /tmp/sinoark-logs

if [ ! -f /root/data/db.db ]; then
  echo ""
  echo "  WARNING: /root/data/db.db not found."
  echo "  The WeChat containers will start with empty databases."
  echo "  Each container will require WeChat QR code re-authentication."
  echo ""
  echo "  To restore from OSS backup:"
  echo "    ossutil cp oss://<YOUR_BUCKET>/sinoark/data/db.db /root/data/db.db"
  echo "    ossutil cp oss://<YOUR_BUCKET>/sinoark/data2/db.db /root/data2/db.db"
  echo "    ossutil cp oss://<YOUR_BUCKET>/sinoark/data3/db.db /root/data3/db.db"
  echo ""
fi

# ── 6. Secrets (.env files) ───────────────────────────────────────────────────
echo "[6/8] Checking secrets..."
REQUIRED_ENVS=(
  "$PIPELINE/sinoark_media_wechat_pipeline/.env"
  "$PIPELINE/sinoark-filter/.env"
  "$PIPELINE/translation-digest/.env"
  "$PIPELINE/sinoark-profiles/.env"
  "$PIPELINE/digest/.env"
)
MISSING=0
for f in "${REQUIRED_ENVS[@]}"; do
  if [ ! -f "$f" ]; then
    echo "  MISSING: $f"
    MISSING=1
  fi
done
if [ "$MISSING" -eq 1 ]; then
  echo ""
  echo "  Restore .env files from your secret store or OSS before continuing."
  echo "  Required keys per file are documented in each module's CLAUDE.md."
  echo ""
  echo "  To restore from OSS:"
  echo "    ossutil cp oss://<YOUR_BUCKET>/sinoark/secrets/env-files.tar.gz /tmp/"
  echo "    tar -xzf /tmp/env-files.tar.gz -C /"
  echo ""
  read -rp "  Continue anyway? (y/N) " yn
  [[ "$yn" =~ ^[Yy]$ ]] || exit 1
fi

# ── 7. Docker containers ──────────────────────────────────────────────────────
echo "[7/8] Creating Docker containers..."
docker pull "$IMAGE"

create_container() {
  local name=$1 host_port=$2 data_dir=$3
  if docker inspect "$name" &>/dev/null; then
    echo "  $name already exists — skipping (run 'docker rm $name' to recreate)"
    return
  fi
  docker run -d \
    --name "$name" \
    --restart unless-stopped \
    -p "${host_port}:8001" \
    -v "${data_dir}:/app/data" \
    -e INSTALL=True \
    -e ENABLE_JOB=True \
    -e PNPM_HOME=/pnpm \
    -e DEBIAN_FRONTEND=noninteractive \
    -e "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple" \
    -e BROWSER_TYPE=webkit \
    -e PLANT_PATH=/app/env \
    "$IMAGE"
  echo "  Started $name on port $host_port"
}

create_container we-mp-rss   8001 /root/data
create_container we-mp-rss-2 8004 /root/data2
create_container we-mp-rss-3 8003 /root/data3

# ── 8. Node.js + Python dependencies ─────────────────────────────────────────
echo "[8/8] Installing pipeline dependencies..."
cd "$PIPELINE/pipeline" && npm install --silent
echo "  sinoark_media_wechat_pipeline: npm install done"

pip3 install -q -r "$PIPELINE/translation-digest/requirements.txt"
echo "  translation-digest: pip install done"

pip3 install -q -r "$PIPELINE/digest/requirements.txt"
echo "  sinoark-web: pip install done"

pip3 install -q google-genai supabase python-dotenv requests
echo "  sinoark-filter + sinoark-profiles: pip install done"

# ── Crontab ───────────────────────────────────────────────────────────────────
echo ""
echo "Installing crontab..."
crontab "$PIPELINE/scripts/crontab.txt"
echo "  Crontab installed"

echo ""
echo "============================================================"
echo "  Bootstrap complete!"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  1. Verify containers:  docker ps"
echo "  2. If data was empty:  authenticate each container via WeChat QR code"
echo "     C1: http://<ECS_IP>:8001"
echo "     C2: http://<ECS_IP>:8004"
echo "     C3: http://<ECS_IP>:8003"
echo "  3. FC jobs are already running on Function Compute — no action needed"
echo "  4. Set up OSS backups: bash $PIPELINE/scripts/backup-to-oss.sh"
echo ""
