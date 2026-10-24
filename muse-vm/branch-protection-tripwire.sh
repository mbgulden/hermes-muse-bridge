#!/usr/bin/env bash
# branch-protection-tripwire.sh — Muse hook script (install to ~/hooks/scripts/).
#
# Hook form of the old 5-minute LLM cron (token-burn modulation). Runs
# the deterministic
# tripwire check script every poll; silent on healthy verdicts; wakes an
# agent only on a `trip` verdict (exit 2) or after 3+ consecutive check
# errors (exit 1) — the same reporting rules the cron body carried.
# State: ~/hooks/state/branch-protection-tripwire.errors = consecutive
# error count. Dry runs never advance it.
#
# Pair it with a Muse hook definition whose prompt is the tripwire
# prompt (see tripwire-prompt.md), polling every ~300s.
# The hook runtime ($HATCH_HOOK_RUNTIME) provides: silent, log, wake.
# The check script itself is deployment-specific (it watches your
# protected branches and opens a revert PR on a trip); point
# TRIPWIRE_SCRIPT / TRIPWIRE_LOG at yours.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

SCRIPT="${TRIPWIRE_SCRIPT:-$HOME/workspace/tripwire/branch_protection_tripwire.py}"
LOGF="${TRIPWIRE_LOG:-$HOME/workspace/tripwire/tripwire.log}"
STATE_DIR="$HOME/hooks/state"
ERR_FILE="$STATE_DIR/branch-protection-tripwire.errors"
mkdir -p "$STATE_DIR" "$(dirname "$LOGF")"

set +e
/usr/bin/python3 "$SCRIPT" --check >> "$LOGF" 2>&1
RC=$?
set -e

TAIL="$(tail -n 15 "$LOGF" 2>/dev/null || true)"
PAYLOAD="$(python3 - "$RC" "$TAIL" <<'PYEOF'
import json, sys
print(json.dumps({"exit_code": int(sys.argv[1]), "log_tail": sys.argv[2]}))
PYEOF
)"

ERRS=0
if [ -f "$ERR_FILE" ]; then ERRS="$(cat "$ERR_FILE")"; fi

if [ "$RC" -eq 2 ]; then
  if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then printf '0' > "$ERR_FILE"; fi
  log "tripwire TRIPPED" "$PAYLOAD"
  wake "Branch-protection tripwire tripped" "$PAYLOAD"
fi

if [ "$RC" -eq 1 ]; then
  ERRS=$((ERRS + 1))
  if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then printf '%s' "$ERRS" > "$ERR_FILE"; fi
  if [ "$ERRS" -eq 3 ]; then
    log "tripwire check erroring repeatedly" "$PAYLOAD"
    wake "Branch-protection tripwire check has errored 3+ consecutive times" "$PAYLOAD"
  fi
  silent "tripwire check error (consecutive: $ERRS)" "$PAYLOAD"
fi

if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then printf '0' > "$ERR_FILE"; fi
silent "branch protection healthy" "$PAYLOAD"
