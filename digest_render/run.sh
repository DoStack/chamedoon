#!/bin/sh
# Hourly on bigde-vps (cron). Sends each courier board that is due (20:00 local, Courier settings →
# channel_digest_on). The worker container builds the numbers and sends; this container only draws.
# Drawing uses any image with Playwright + Chrome: the housing content image by default
# (RENDER_IMAGE=chamedoon-content), or one built from digest_render/Dockerfile.
# crontab: 2 * * * * /opt/chamedoon-worker/digest_render/run.sh >> /var/log/courier-digest.log 2>&1
set -u
WORKER_DIR=${WORKER_DIR:-/opt/chamedoon-worker}
RENDER_IMAGE=${RENDER_IMAGE:-chamedoon-content}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cd "$WORKER_DIR" || exit 1
SCOPES=$(docker compose exec -T worker python manage.py courier_digest due)
for scope in $SCOPES; do
  echo "$(date -u +%FT%TZ) $scope due"
  docker compose exec -T worker rm -f "/tmp/digest_$scope.json" "/tmp/digest_$scope.png"
  docker compose exec -T worker python manage.py courier_digest build --scope "$scope" --out "/tmp/digest_$scope.json" || continue
  docker compose cp "worker:/tmp/digest_$scope.json" "$WORK/$scope.json" 2>/dev/null || { echo "$scope skipped"; continue; }
  docker run --rm -v "$WORKER_DIR/digest_render:/render" -v "$WORK:/work" -w /render --entrypoint python "$RENDER_IMAGE" render.py "/work/$scope.json" "/work/$scope.png" || { echo "$scope render failed"; continue; }
  docker compose cp "$WORK/$scope.png" "worker:/tmp/digest_$scope.png"
  docker compose exec -T worker python manage.py courier_digest send --scope "$scope" --image "/tmp/digest_$scope.png" --data "/tmp/digest_$scope.json"
done
