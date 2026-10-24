#!/usr/bin/env bash
# jimmy-units-ensure.sh — Muse hook script (install to ~/hooks/scripts/).
#
# Hook form of the old 15-minute LLM cron (token-burn modulation). Runs
# the idempotent unit installer
# every poll; silent when everything is installed and active; wakes an
# agent only when supervision was restored (installer printed install /
# start / restart lines), the installer failed, or a unit is still down.
#
# This hook is the recovery anchor for the systemd units themselves: hook
# definitions are runtime-managed and survive VM replacements that wipe
# /etc, and the installer it runs reinstalls all units (tunnel, listener,
# work-guard timer, okf-sync timer) on its next pass.
#
# Pair it with a Muse hook definition whose prompt is the units-ensure
# prompt (see units-ensure-prompt.md), polling every ~60s.
# The hook runtime ($HATCH_HOOK_RUNTIME) provides: silent, log, wake.
# Deliberately a hook, not a systemd timer: a timer cannot reinstall
# itself after a VM replacement wipes /etc, so this hook stays the
# recovery anchor for all the units.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

UNITS="jimmy-tunnel.service jimmy-listener.service work-guard-check.timer okf-threads-sync.timer"

RC=0
OUT="$("$HOME/workspace/bin/install-jimmy-units.sh" 2>&1)" || RC=$?

DOWN=""
for u in $UNITS; do
  if ! systemctl is-active --quiet "$u"; then
    DOWN="$DOWN $u"
  fi
done

PAYLOAD="$(python3 - "$RC" "$OUT" "$DOWN" <<'PYEOF'
import json, sys
print(json.dumps({"installer_rc": int(sys.argv[1]),
                  "installer_output": sys.argv[2],
                  "units_down": sys.argv[3].split()}))
PYEOF
)"

if [ "$RC" -ne 0 ]; then
  log "unit installer failed" "$PAYLOAD"
  wake "jimmy unit installer failed" "$PAYLOAD"
fi

if [ -n "$DOWN" ]; then
  log "units still down after ensure" "$PAYLOAD"
  wake "systemd units still down after ensure pass" "$PAYLOAD"
fi

if [ -n "$OUT" ]; then
  log "supervision restored" "$PAYLOAD"
  wake "systemd supervision was restored by the ensure pass" "$PAYLOAD"
fi

silent "all jimmy units installed and active" "$PAYLOAD"
