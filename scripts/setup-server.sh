#!/usr/bin/env bash
# One-shot server setup for Repost Radar (Ubuntu/Debian, run as root):
#   bash /opt/repost-radar/scripts/setup-server.sh
# Safe to run again: existing values in .env are kept, missing ones are filled in.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
cd "$APP_DIR"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" = "0" ] || { echo "Run this as root (sudo -i first)."; exit 1; }

say "Installing Docker, firewall and tools"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates openssl ufw git >/dev/null
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

say "Firewall: allow SSH, HTTP, HTTPS only"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status | sed -n '1,8p'

# --- .env -------------------------------------------------------------------
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
get() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
put() {  # put KEY VALUE (replace or append)
  if grep -qE "^$1=" "$ENV_FILE"; then
    local tmp; tmp="$(mktemp)"
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} {print}' "$ENV_FILE" > "$tmp" && cat "$tmp" > "$ENV_FILE" && rm -f "$tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
  fi
}
ensure_secret() { [ -n "$(get "$1")" ] || put "$1" "$(openssl rand -hex 32)"; }
ask() {  # ask KEY "description" : prompt only if empty and we have a terminal
  local cur; cur="$(get "$1")"
  if [ -z "$cur" ] && [ -t 0 ]; then
    read -r -p "  $2 (Enter to skip): " val || true
    [ -n "${val:-}" ] && put "$1" "$val"
  fi
  return 0
}

say "Configuring .env"
if [ -z "$(get DOMAIN)" ]; then
  default_domain="$(hostname -f 2>/dev/null || hostname)"
  if [ -t 0 ]; then
    echo "  You can give several, comma separated, e.g. crm.smartemploy.io, $default_domain"
    read -r -p "  Domain(s) for the CRM [$default_domain]: " d || true
    put DOMAIN "${d:-$default_domain}"
  else
    put DOMAIN "$default_domain"
  fi
fi
ensure_secret POSTGRES_PASSWORD
ensure_secret SESSION_SECRET
ensure_secret ENGINE_API_SECRET
NEW_PASSWORD=""
if [ -z "$(get CRM_PASSWORD)" ] && [ -z "$(get CRM_PASSWORD_HASH)" ]; then
  NEW_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
  put CRM_PASSWORD "$NEW_PASSWORD"
fi
echo "  API keys (all can be added later by editing $ENV_FILE and re-running this script):"
ask REED_API_KEY "Reed API key"
ask COMPANIES_HOUSE_API_KEY "Companies House API key"
ask ADZUNA_APP_ID "Adzuna app id"
ask ADZUNA_APP_KEY "Adzuna app key"
ask THEIRSTACK_API_KEY "TheirStack API key"
ask CAREERJET_API_KEY "Careerjet API key"
ask JOOBLE_API_KEY "Jooble UK API key"
ask APIFY_TOKEN "Apify API token (Indeed)"
ask BRAVE_API_KEY "Brave Search API key"
ask HUNTER_API_KEY "Hunter.io API key"
for kv in REPOST_THRESHOLD=3 LOOKBACK_DAYS=90 REED_MAX_CALLS_PER_RUN=300 REED_REQUESTS_PER_SECOND=1 \
          ADZUNA_MAX_CALLS_PER_RUN=40 THEIRSTACK_MAX_JOBS_PER_RUN=100 THEIRSTACK_BACKFILL_DAYS=90 ENABLE_DOCS=0; do
  [ -n "$(get "${kv%%=*}")" ] || put "${kv%%=*}" "${kv#*=}"
done
[ -n "$(get CAREERJET_USER_IP)" ] || put CAREERJET_USER_IP "$(curl -fsS -4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
# Daily 06:00 UK collection from the server itself, so history starts building now.
# Once Make.com Scenario 1 is running you can remove this line to avoid running twice.
[ -n "$(get COMPOSE_PROFILES)" ] || put COMPOSE_PROFILES scheduler

say "Building and starting (first time takes a few minutes)"
docker compose up -d --build

say "Waiting for the engine"
SECRET="$(get ENGINE_API_SECRET)"
for i in $(seq 1 60); do
  if docker compose exec -T engine python -c "import urllib.request as u; r=u.Request('http://localhost:8000/health', headers={'X-API-Key': '$SECRET'}); print(u.urlopen(r, timeout=3).read().decode())" 2>/dev/null; then
    break
  fi
  sleep 3
done
docker compose ps

DOMAIN="$(get DOMAIN)"
DOMAIN="${DOMAIN%%,*}"; DOMAIN="${DOMAIN// /}"
say "Done"
cat <<EOF

  CRM:        https://$DOMAIN
  Engine:     https://$DOMAIN/engine   (for Make.com, header X-API-Key = ENGINE_API_SECRET in $ENV_FILE)
EOF
if [ -n "$NEW_PASSWORD" ]; then
  echo "  Password:   $NEW_PASSWORD     <- save it in your password manager"
else
  echo "  Password:   (unchanged, see CRM_PASSWORD in $ENV_FILE)"
fi
cat <<EOF

  The HTTPS certificate can take a minute on the first visit.
  Logs:    cd $APP_DIR && docker compose logs -f engine
  Update:  cd $APP_DIR && git pull && bash scripts/setup-server.sh
EOF
