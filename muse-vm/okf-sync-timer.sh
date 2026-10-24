#!/bin/bash
# okf-sync-timer.sh — systemd-timer wrapper for a knowledge-base sync + an
# audit-signal push. (Named for the live deployment's sync job; rename the
# unit and flag to match your own job.)
#
# Replaces an hourly LLM cron (token-burn modulation): both steps are pure
# scripts. Routine pushes stay silent (the log below is the record).
# WARNING lines or a non-zero exit drop a flag file at
# ~/workspace/.flags/knowledge-sync-warning, which the inbox-trigger hook
# wakes an agent on — those warnings need the user's attention.
set -u

FLAGS="$HOME/workspace/.flags"
LOG="$HOME/workspace/knowledge-sync/sync-timer.log"
mkdir -p "$FLAGS" "$(dirname "$LOG")"

OUT1="$(python3 "$HOME/workspace/knowledge-sync/bin/sync-docs" 2>&1)"
RC1=$?
OUT2="$("$HOME/workspace/bin/audit-push.sh" 2>&1)"
RC2=$?

{
  echo "=== $(date -u +%FT%TZ) sync-threads rc=$RC1 push rc=$RC2 ==="
  printf '%s\n' "$OUT1"
  printf '%s\n' "$OUT2"
} >> "$LOG"

if [ "$RC1" -ne 0 ] || [ "$RC2" -ne 0 ] || printf '%s\n%s\n' "$OUT1" "$OUT2" | grep -q "WARNING"; then
  {
    echo "Knowledge sync needs attention ($(date -u +%FT%TZ)) sync rc=$RC1, audit push rc=$RC2"
    printf '%s\n%s\n' "$OUT1" "$OUT2" | grep -iE "warning|error|fail" | head -20
  } > "$FLAGS/knowledge-sync-warning"
fi

exit $(( RC1 + RC2 ))
