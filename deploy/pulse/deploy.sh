#!/usr/bin/env bash
# Install or update Pulse on the Chamedoon VM. Run as root on the VM with the repo tarball at /tmp/pulse-src.tar.gz
# (made by `git archive --format=tar.gz -o pulse-src.tar.gz HEAD`). Secrets stay in /etc/chamedoon-pulse/*.env (root, 600).
set -euo pipefail
ROOT=/opt/chamedoon-pulse
id pulse >/dev/null 2>&1 || useradd --system --home-dir "$ROOT" --shell /usr/sbin/nologin pulse
install -d -o pulse -g pulse "$ROOT" "$ROOT/static"
rm -rf "$ROOT/app.new" && install -d -o pulse -g pulse "$ROOT/app.new"
tar -xzf /tmp/pulse-src.tar.gz -C "$ROOT/app.new"
chown -R pulse:pulse "$ROOT/app.new"
[ -x "$ROOT/venv/bin/python" ] || sudo -u pulse python3 -m venv "$ROOT/venv"
sudo -u pulse "$ROOT/venv/bin/pip" install -q --upgrade pip
sudo -u pulse "$ROOT/venv/bin/pip" install -q -r "$ROOT/app.new/backend/requirements.txt"
rm -rf "$ROOT/app.old"; [ -d "$ROOT/app" ] && mv "$ROOT/app" "$ROOT/app.old"; mv "$ROOT/app.new" "$ROOT/app"
cd "$ROOT/app/backend"
sudo -u pulse env DJANGO_SETTINGS_MODULE=pulse.settings PULSE_STATIC_ROOT="$ROOT/static" DJANGO_SECRET_KEY=collectstatic-only "$ROOT/venv/bin/python" manage.py collectstatic --noinput -v 0
install -m 644 "$ROOT/app/deploy/pulse/chamedoon-pulse.service" /etc/systemd/system/chamedoon-pulse.service
systemctl daemon-reload
systemctl enable --now chamedoon-pulse
systemctl restart chamedoon-pulse
sleep 3
curl -s -o /dev/null -w "pulse local: %{http_code}\n" -H "Host: chamedoon-pulse.bigde.li" http://127.0.0.1:8100/admin/pulse/
