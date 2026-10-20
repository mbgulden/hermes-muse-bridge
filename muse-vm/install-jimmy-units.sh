#!/bin/bash
# install-jimmy-units.sh — render + install the systemd units for jimmy-tunnel
# and jimmy-listener from the templates in ./systemd/ (next to this script).
#
# Why systemd: the tunnel/listener used to be supervised by a heartbeat
# watchdog that only notices a death within ~15-30 min, and the processes die
# with their parent session when the VM is replaced or a session tree is torn
# down. Under systemd (PID 1) a death restarts in seconds and the services
# start on boot. Diagnosis + plan: the fix was made Oct 20, 2026 after 29
# watchdog restarts in one day.
#
# Idempotent: if the installed units already match the render, this is a
# no-op. Safe to re-run every 15 min from cron: after a VM replacement wipes
# /etc, the next run reinstalls supervision automatically.
#
# The egress proxy value is rendered from THIS process's $HTTPS_PROXY at run
# time. The value lands only in /etc/systemd/system/*.service — the templates
# and this script hold the __HTTPS_PROXY__ placeholder, never the value.
# In the live deployment this script lives at ~/workspace/bin/ with templates
# in ~/workspace/systemd/; set SRC_DIR to override the template location.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SRC_DIR:-$HERE/systemd}"
UNIT_DIR="/etc/systemd/system"
PROXY="${HTTPS_PROXY:-}"

render() {
  python3 - "$1" "$PROXY" <<'PYEOF'
import sys
tmpl, proxy = sys.argv[1], sys.argv[2]
sys.stdout.write(open(tmpl).read().replace("__HTTPS_PROXY__", proxy))
PYEOF
}

# Compare with the proxy VALUE masked out: different agent processes get
# different (equally valid) proxy credentials in their env, so a raw compare
# made every other run rewrite the units and bounce both services. Only
# structural differences — or a missing unit — count as a change. A fresh
# install (e.g. after a VM replacement wiped /etc) always writes the current
# run's proxy value.
normalize() { sed -E 's|(Environment=HTTPS_PROXY=).*|\1__HTTPS_PROXY__|' "$1"; }

changed=0
for name in jimmy-tunnel jimmy-listener; do
  tmpl="$SRC_DIR/$name.service.tmpl"
  dest="$UNIT_DIR/$name.service"
  [ -f "$tmpl" ] || { echo "missing template $tmpl" >&2; exit 1; }
  rendered="$(mktemp)"
  render "$tmpl" > "$rendered"
  if [ -f "$dest" ] && [ "$(normalize "$rendered")" = "$(normalize "$dest")" ]; then
    : # up to date (proxy value may differ; leave the running config alone)
  else
    sudo cp "$rendered" "$dest"
    echo "installed $name.service"
    changed=1
  fi
  rm -f "$rendered"
done

if [ "$changed" = 1 ]; then
  sudo systemctl daemon-reload
  sudo systemctl enable jimmy-tunnel.service jimmy-listener.service >/dev/null 2>&1
  for name in jimmy-tunnel jimmy-listener; do
    if systemctl is-active --quiet "$name.service"; then
      sudo systemctl restart "$name.service"
      echo "restarted $name.service (unit changed)"
    fi
  done
fi

# Start anything not active (fresh install, post-replacement, stopped unit)
for name in jimmy-tunnel jimmy-listener; do
  if ! systemctl is-active --quiet "$name.service"; then
    sudo systemctl start "$name.service"
    echo "started $name.service"
  fi
done
exit 0
