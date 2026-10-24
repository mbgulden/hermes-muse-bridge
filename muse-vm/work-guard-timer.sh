#!/bin/bash
# work-guard-timer.sh — systemd-timer wrapper for the work-guard failsafe check.
#
# Replaces the 15-minute LLM cron (retired Oct 23, 2026 in the token-burn
# modulation): the check is a pure script, so no agent runs unless something
# actually happened. Notable outcomes become flag files under
# ~/workspace/.flags/, which the inbox-trigger hook wakes an agent on:
#   work-guard-restarted — a stale job was restarted (report which + count)
#   work-guard-failed    — the check itself exited non-zero
set -u

FLAGS="$HOME/workspace/.flags"
mkdir -p "$FLAGS"

OUT="$("$HOME/workspace/bin/work-guard.sh" check 2>&1)"
RC=$?
printf '%s\n' "$OUT" >> "$HOME/workspace/.work-guard.log"

if [ "$RC" -ne 0 ]; then
  {
    echo "work-guard.sh check exited $RC at $(date -u +%FT%TZ)"
    echo "$OUT"
  } > "$FLAGS/work-guard-failed"
  exit "$RC"
fi

case "$OUT" in
  *"check done, restarted:"*)
    {
      echo "work-guard restarted job(s) at $(date -u +%FT%TZ)"
      echo "$OUT"
    } > "$FLAGS/work-guard-restarted"
    ;;
esac
exit 0
