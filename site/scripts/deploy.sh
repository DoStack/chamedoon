#!/usr/bin/env bash
# Ship site/dist to bigde-vps. Usage: bash scripts/deploy.sh [--media] [--src]
# Releases: /opt/chamedoon-site/releases/<UTC id>, symlink `current` swapped atomically, last 5 kept.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=${DEPLOY_HOST:-bigde-vps}
BASE=/opt/chamedoon-site
ID=$(date -u +%Y%m%dT%H%M%SZ)
test -f dist/fa/index.html || { echo "no dist/fa/index.html; run npm run build first" >&2; exit 1; }
tar -C dist -czf - . | ssh "$HOST" "set -e; mkdir -p $BASE/releases/$ID && tar -xzf - -C $BASE/releases/$ID && find $BASE/releases/$ID -type d -exec chmod 755 {} + && find $BASE/releases/$ID -type f -exec chmod 644 {} + && cd $BASE/releases && ln -sfn $ID current.tmp && mv -T current.tmp current && ls -1d 20*/ | sed 's#/##' | sort | head -n -5 | xargs -r rm -rf && echo release $ID live"
if [[ " $* " == *" --src "* ]]; then
  # Source for the nightly rebuild on the VPS (no node_modules, dist or media).
  tar --exclude=./node_modules --exclude=./dist --exclude=./.media --exclude=./.astro --exclude=./.npm -C . -czf - . | ssh "$HOST" "set -e; mkdir -p $BASE/src && tar -xzf - -C $BASE/src && cp $BASE/src/deploy/vps-rebuild.sh $BASE/vps-rebuild.sh && chmod +x $BASE/vps-rebuild.sh && echo source synced"
fi
if [[ " $* " == *" --media "* && -d .media ]]; then
  tar -C .media -czf - . | ssh "$HOST" "set -e; mkdir -p $BASE/media && tar -xzf - -C $BASE/media && find $BASE/media -type d -exec chmod 755 {} + && find $BASE/media -type f -exec chmod 644 {} + && echo media synced"
fi
