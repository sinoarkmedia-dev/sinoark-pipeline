#!/usr/bin/env bash
set -euo pipefail
PATTERN='we-?rss|weress|weres'
TMP=/tmp/find_weress.$$ 
: > "$TMP"

echo "WeRSS detector - searching Kubernetes, Docker, Compose, systemd, filesystem"
echo "Pattern: $PATTERN"
echo

if command -v kubectl >/dev/null 2>&1; then
  echo "== kubectl: scanning resources =="
  kubectl get all --all-namespaces -o wide 2>/dev/null | grep -Ei "$PATTERN" | tee -a "$TMP" || true
  echo
  kubectl get pods --all-namespaces -o jsonpath='{range .items[*]}{.metadata.namespace} "|" {.metadata.name} "|" {.spec.containers[*].image}{"\n"}{end}' 2>/dev/null |
    grep -Ei "$PATTERN" | tee -a "$TMP" || true
  echo
  kubectl get deploy,daemonset,statefulset --all-namespaces -o yaml 2>/dev/null | grep -nEi "($PATTERN)|configMapKeyRef|secretKeyRef|config.yaml" | tee -a "$TMP" || true
  kubectl get configmap,secret --all-namespaces -o yaml 2>/dev/null | grep -nEi "($PATTERN)|config.yaml" | tee -a "$TMP" || true
else
  echo "kubectl not found, skipping k8s"
fi

echo
if command -v docker >/dev/null 2>&1; then
  echo "== docker: running containers/images =="
  docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | grep -Ei "$PATTERN" | tee -a "$TMP" || true
  echo
  for cid in $(docker ps --format '{{.Names}}' 2>/dev/null || true); do
    img=$(docker inspect --format '{{.Config.Image}}' "$cid" 2>/dev/null || true)
    if echo "$cid $img" | grep -Ei "$PATTERN" >/dev/null 2>&1; then
      echo "-- container: $cid (image: $img) --" | tee -a "$TMP"
      echo "Env:" | tee -a "$TMP"
      docker inspect --format '{{json .Config.Env}}' "$cid" 2>/dev/null | jq -r '.[]' 2>/dev/null | tee -a "$TMP" || true
      echo "Mounts:" | tee -a "$TMP"
      docker inspect --format '{{json .Mounts}}' "$cid" 2>/dev/null | jq '.' 2>/dev/null | tee -a "$TMP" || true
      echo "Searching common paths inside container for config.yaml:"
      docker exec "$cid" sh -c 'for p in /app /config /etc /opt /usr/local /srv; do [ -d "$p" ] && find "$p" -maxdepth 4 -type f -name "config.yaml" -print 2>/dev/null; done' 2>/dev/null | tee -a "$TMP" || true
      echo
    fi
  done
else
  echo "docker not found, skipping docker"
fi

echo
# Docker Compose (search common locations for compose files)
echo "== docker-compose files (searching /home /srv /opt /etc) =="
find /home /srv /opt /etc -maxdepth 4 -type f -name 'docker-compose*.y*ml' 2>/dev/null |
  while read -r file; do
    if grep -Ei "$PATTERN" "$file" >/dev/null 2>&1; then
      echo "-- compose file: $file --" | tee -a "$TMP"
      grep -nEi "$PATTERN|config.yaml|env_file" "$file" | sed -n '1,200p' | tee -a "$TMP"
      echo
    fi
  done

echo
if command -v systemctl >/dev/null 2>&1; then
  echo "== systemd services =="
  systemctl list-units --type=service --no-pager 2>/dev/null | grep -Ei "$PATTERN" | tee -a "$TMP" || true
  for svc in $(systemctl list-units --type=service --no-legend --all 2>/dev/null | awk '{print $1}'); do
    if systemctl cat "$svc" 2>/dev/null | grep -Ei "$PATTERN|EnvironmentFile|ExecStart" >/dev/null 2>&1; then
      echo "-- unit: $svc --" | tee -a "$TMP"
      systemctl cat "$svc" 2>/dev/null | sed -n '1,200p' | tee -a "$TMP"
      echo
    fi
  done
else
  echo "systemctl not found, skipping systemd"
fi

echo
# Filesystem search (common app dirs)
echo "== filesystem quick search (common dirs) =="
SEARCH_DIRS="/etc /opt /srv /var /home /usr/local"
for d in $SEARCH_DIRS; do
  echo "-- scanning $d for config.yaml or WeRSS refs --"
  find "$d" -maxdepth 6 -type f \( -iname 'config.yaml' -o -iname '*wer*' -o -iname '*we-rss*' \) 2>/dev/null | tee -a "$TMP" || true
  grep -RIn --exclude-dir={proc,sys,dev,tmp} -nE "($PATTERN)|config.yaml" "$d" 2>/dev/null | tee -a "$TMP" || true
done

echo
echo "==== SUMMARY (raw matches saved to $TMP) ===="
awk 'NR<=200{print}' "$TMP"
echo
echo "Raw file: $TMP"
