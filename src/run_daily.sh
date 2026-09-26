#!/usr/bin/env bash
# run_daily.sh — scheduled-run wrapper around generate_report.py.
#
# Same operational contract as the sibling csv-daily-sales-report demo:
# retry a transient failure with backoff, refuse to run concurrently with
# itself (flock), and let generate_report.py's own per-event alert
# deduplication (alert_state.py) make a rerun of the same as_of date safe.
#
# Configure via env vars: INV_INVENTORY, INV_SALES, INV_OUTPUT_DIR,
# INV_AS_OF, INV_RETRY_ATTEMPTS, INV_RETRY_BACKOFF_SECONDS, INV_LOCK_FILE,
# INV_PYTHON, INV_ALERTS_LOG, NOTIFY_CMD.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INVENTORY="${INV_INVENTORY:-$SCRIPT_DIR/../data/sample/inventory.csv}"
SALES="${INV_SALES:-$SCRIPT_DIR/../data/sample/sales.csv}"
OUTPUT_DIR="${INV_OUTPUT_DIR:-$SCRIPT_DIR/../output}"
ALERTS_LOG="${INV_ALERTS_LOG:-$SCRIPT_DIR/alerts.log}"
LOCK_FILE="${INV_LOCK_FILE:-$SCRIPT_DIR/.inventory-report.lock}"
PY="${INV_PYTHON:-$SCRIPT_DIR/../.venv/bin/python3}"

AS_OF_ARGS=()
if [ -n "${INV_AS_OF:-}" ]; then
    AS_OF_ARGS=(--as-of "$INV_AS_OF")
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "${TS} [WARNING] run_daily.sh: another run is already in progress (lock held at $LOCK_FILE) -- skipping" >> "$ALERTS_LOG"
    exit 0
fi

RETRY_ATTEMPTS="${INV_RETRY_ATTEMPTS:-3}"
IFS=',' read -r -a BACKOFFS <<< "${INV_RETRY_BACKOFF_SECONDS:-5,15,30}"

if [ ! -x "$PY" ]; then
    TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "${TS} [CRITICAL] run_daily.sh: venv python not found/executable at $PY -- report NOT run" >> "$ALERTS_LOG"
    exit 1
fi

attempt=1
exit_code=1
while [ "$attempt" -le "$RETRY_ATTEMPTS" ]; do
    "$PY" generate_report.py --inventory "$INVENTORY" --sales "$SALES" --output-dir "$OUTPUT_DIR" "${AS_OF_ARGS[@]}"
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        break
    fi

    if [ "$attempt" -lt "$RETRY_ATTEMPTS" ]; then
        idx=$((attempt - 1))
        if [ "$idx" -ge "${#BACKOFFS[@]}" ]; then
            idx=$((${#BACKOFFS[@]} - 1))
        fi
        backoff="${BACKOFFS[$idx]}"
        TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "${TS} [INFO] run_daily.sh: attempt ${attempt}/${RETRY_ATTEMPTS} failed (exit ${exit_code}) -- retrying in ${backoff}s" >> "$ALERTS_LOG"
        sleep "$backoff"
    fi

    attempt=$((attempt + 1))
done

if [ $exit_code -ne 0 ]; then
    TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "${TS} [CRITICAL] run_daily.sh: generate_report.py failed after ${RETRY_ATTEMPTS} attempt(s), exit ${exit_code}" >> "$ALERTS_LOG"
fi

exit $exit_code
