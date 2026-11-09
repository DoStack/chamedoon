#!/bin/sh
# Outreach health (operating plan v1): every 5 minutes from root's crontab. Alerts and the 20:30 Toronto report are
# sent by the backend command; this script only feeds it the worker's backend errors and restarts the worker when asked,
# at most once an hour.
# The backend container runs this check: if a deploy left it created but stopped (2026-11-09 00:3x UTC), start it.
for NAME in chamedoon-worker chamedoon-outreach; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != "true" ]; then
    DIR=/opt/$NAME; [ "$NAME" = chamedoon-worker ] && DIR=/opt/chamedoon-worker
    (cd "$DIR" && docker compose up -d >/dev/null 2>&1)
    echo "$(date -u '+%F %T') started $NAME" >> /var/log/outreach-health.log
  fi
done
ERRORS=$(cd /opt/chamedoon-outreach && docker compose logs --since 5m outreach 2>/dev/null | grep -c "Backend unavailable\|Server Error (500)")
OUT=$(cd /opt/chamedoon-worker && docker compose exec -T worker python manage.py outreach_health --worker-errors "$ERRORS" 2>&1)
echo "$(date -u '+%F %T') errors=$ERRORS $(echo "$OUT" | grep -c INCIDENT) incidents" >> /var/log/outreach-health.log
echo "$OUT" | grep -q "^RESTART" || exit 0
STAMP=/var/tmp/outreach-restart.stamp
if [ -f "$STAMP" ] && [ $(( $(date +%s) - $(stat -c %Y "$STAMP") )) -lt 3600 ]; then exit 0; fi
touch "$STAMP"
cd /opt/chamedoon-outreach && docker compose restart outreach >/dev/null 2>&1
echo "$(date -u '+%F %T') restarted" >> /var/log/outreach-health.log
