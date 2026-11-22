#!/bin/sh
# Nightly rebuild on bigde-vps: fresh statistics → new release → atomic symlink swap.
# Source: /opt/chamedoon-site/src (shipped by scripts/deploy.sh --src). Build runs in node:22-alpine, capped at 1 GB.
# Cron (ubuntu): 17 3 * * * /opt/chamedoon-site/vps-rebuild.sh
set -eu
BASE=/opt/chamedoon-site
# Public origin of the site (site.config.mjs reads the same variable). Domain move: change it here and in the build env.
SITE_URL=${SITE_URL:-https://chamedoon.bigde.li}
export SITE_URL
LOG=/var/log/chamedoon-site-build.log
exec >>"$LOG" 2>&1
exec 9>"$BASE/.build.lock"
flock -n 9 || { echo "$(date -u +%FT%TZ) another build is running"; exit 0; }
ID=$(date -u +%Y%m%dT%H%M%SZ)
echo "=== $ID start"
# Build env, read from the app container into a 600 file the build loads; never written to the log.
#   PUBLIC_POSTHOG_KEY: public PostHog project key (client-side by design) for the anonymous page-view count.
#   STATS_TOKEN: build-only secret for /api/v1/public/stats/ (backend PUBLIC_STATS_TOKEN); never PUBLIC_*.
( umask 077
  k=$(sudo -n docker exec chamedoon-web printenv POSTHOG_KEY 2>/dev/null || true)
  t=$(sudo -n docker exec chamedoon-web printenv PUBLIC_STATS_TOKEN 2>/dev/null || true)
  : > "$BASE/src/.env.production.local"; chmod 600 "$BASE/src/.env.production.local"
  { [ -n "$k" ] && printf 'PUBLIC_POSTHOG_KEY=%s
' "$k"; [ -n "$t" ] && printf 'STATS_TOKEN=%s
' "$t"; true; } > "$BASE/src/.env.production.local"
) || true
sudo -n docker run --rm --name chamedoon-site-build -m 1g --memory-swap 1g --cpus 1 \
  -u "$(id -u):$(id -g)" -e HOME=/tmp -e SITE_URL="$SITE_URL" -e npm_config_cache=/app/.npm -e MEDIA_DIR=/media \
  -v "$BASE/src:/app" -v "$BASE/media:/media:ro" -w /app node:22-alpine \
  sh -c 'npm ci --no-audit --no-fund --omit=dev --loglevel=error && npm run build && echo "memory.peak bytes: $(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo n/a)"'
test -f "$BASE/src/dist/fa/index.html"
mkdir -p "$BASE/releases/$ID"
cp -a "$BASE/src/dist/." "$BASE/releases/$ID/"
cd "$BASE/releases"
ln -sfn "$ID" current.tmp && mv -T current.tmp current
ls -1d 20*/ | sed 's#/##' | sort | head -n -5 | xargs -r rm -rf
code=$(curl -s -o /dev/null -w '%{http_code}' "$SITE_URL/fa/" || true)
echo "=== $ID live, /fa/ -> $code"
