#!/usr/bin/env bash
# Ship site/dist (and the media folder) to bigde-vps. Usage: bash scripts/deploy.sh [--media]
# Releases: /opt/chamedoon-site/releases/<UTC id>, symlink `current` swapped atomically, last 5 kept.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=${DEPLOY_HOST:-bigde-vps}
BASE=/opt/chamedoon-site
ID=$(date -u +%Y%m%dT%H%M%SZ)
test -f dist/fa/index.html || { echo "no dist/fa/index.html; run npm run build first" >&2; exit 1; }
tar -C dist -czf - . | ssh "$HOST" "set -e; mkdir -p $BASE/releases/$ID && tar -xzf - -C $BASE/releases/$ID && find $BASE/releases/$ID -type d -exec chmod 755 {} + && find $BASE/releases/$ID -type f -exec chmod 644 {} + && cd $BASE/releases && ln -sfn $ID current.tmp && mv -T current.tmp current && ls -1d 20*/ | sed 's#/##' | sort | head -n -5 | xargs -r rm -rf && echo release $ID live"
if [[ "${1:-}" == "--media" && -d .media ]]; then
  tar -C .media -czf - . | ssh "$HOST" "set -e; mkdir -p $BASE/media && tar -xzf - -C $BASE/media && find $BASE/media -type d -exec chmod 755 {} + && find $BASE/media -type f -exec chmod 644 {} + && echo media synced"
fi
