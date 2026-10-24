#!/bin/bash
# install-jimmy-units.sh — render + install the systemd units for jimmy-tunnel,
# jimmy-listener, and the script-only timer jobs (work-guard-check,
# okf-threads-sync) from the templates in ./systemd/ (next to this script).
#
# Why systemd: the tunnel/listener used to be supervised by a heartbeat
# watchdog that only notices a death within ~15-30 min, and the processes die
# with their parent session when the VM is replaced or a session tree is torn
# down. Under systemd (PID 1) a death restarts in seconds and the services
# start on boot. The timer jobs moved under systemd for the same reason:
# their checks are pure scripts, so they run with no agent involvement at
# all unless something actually happened.
#
# Idempotent: if the installed units already match the render, this is a no-op.
# Safe to run every minute (the units-ensure hook does): after a VM
# replacement wipes /etc, the next run reinstalls supervision automatically.
#
# The egress proxy value is rendered from THIS process's $HTTPS_PROXY at run
# time, so a rotated proxy credential is picked up on the next run. The value
# lands only in /etc/systemd/system/*.service — templates and this script hold
# the __HTTPS_PROXY__ placeholder, never the value.
# In the live deployment this script lives at ~/workspace/bin/ with templates
# in ~/workspace/systemd/; set SRC_DIR to override the template location.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SRC_DIR:-$HERE/systemd}"
UNIT_DIR="/etc/systemd/system"
PROXY="${HTTPS_PROXY:-}"

SERVICES="jimmy-tunnel jimmy-listener work-guard-check okf-threads-sync"
TIMERS="work-guard-check okf-threads-sync"

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

changed_units=""
install_one() {
  # $1 = unit base name, $2 = suffix (service|timer)
  local name="$1" suffix="$2"
  local tmpl="$SRC_DIR/$name.$suffix.tmpl"
  local dest="$UNIT_DIR/$name.$suffix"
  [ -f "$tmpl" ] || { echo "missing template $tmpl" >&2; exit 1; }
  local rendered
  rendered="$(mktemp)"
  render "$tmpl" > "$rendered"
  if [ -f "$dest" ] && [ "$(normalize "$rendered")" = "$(normalize "$dest")" ]; then
    : # up to date (proxy value may differ; leave the running config alone)
  else
    sudo cp "$rendered" "$dest"
    echo "installed $name.$suffix"
    changed_units="$changed_units $name.$suffix"
  fi
  rm -f "$rendered"
}

for name in $SERVICES; do install_one "$name" service; done
for name in $TIMERS; do install_one "$name" timer; done

if [ -n "$changed_units" ]; then
  sudo systemctl daemon-reload
  sudo systemctl enable jimmy-tunnel.service jimmy-listener.service work-guard-check.timer okf-threads-sync.timer >/dev/null 2>&1
  for unit in $changed_units; do
    if systemctl is-active --quiet "$unit"; then
      sudo systemctl restart "$unit"
      echo "restarted $unit (unit changed)"
    fi
  done
fi

# Start anything not active (fresh install, post-replacement, stopped unit).
# The oneshot services (work-guard-check, okf-threads-sync) are timer-driven,
# so only the long-running services and the timers get started directly.
for name in jimmy-tunnel jimmy-listener; do
  if ! systemctl is-active --quiet "$name.service"; then
    sudo systemctl start "$name.service"
    echo "started $name.service"
  fi
done
for name in $TIMERS; do
  if ! systemctl is-active --quiet "$name.timer"; then
    sudo systemctl start "$name.timer"
    echo "started $name.timer"
  fi
done
exit 0
