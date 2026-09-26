"""
Failure/low-stock alert hook.

This is a STUB: it never contacts a real endpoint. It prints exactly what
it would have sent, so a client can see the payload shape and wire in
their own channel by replacing `_send`, or by pointing NOTIFY_CMD at an
executable that reads JSON on stdin.

Delivery is deduplicated per (as_of, event_id) via alert_state.py, so a
retry/rerun of the same as_of date never re-notifies on a condition that
was already reported.
"""
import json
import os
import sys
from datetime import datetime, timezone

from alert_state import already_delivered, mark_delivered

NOTIFY_CMD = os.environ.get("NOTIFY_CMD")


def _send(event: dict) -> bool:
    payload = json.dumps(event, sort_keys=True)
    if NOTIFY_CMD:
        import subprocess
        try:
            proc = subprocess.run(
                NOTIFY_CMD, input=payload, text=True, shell=True,
                capture_output=True, timeout=15,
            )
            if proc.returncode != 0:
                print(
                    f"[alert] NOTIFY_CMD exited {proc.returncode}: {proc.stderr.strip()}",
                    file=sys.stderr,
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[alert] NOTIFY_CMD raised: {exc}", file=sys.stderr)
            return False

    print(f"[alert-stub] would send: {payload}")
    return True


def fire(as_of: str, event_id: str, severity: str, message: str) -> bool:
    if already_delivered(as_of, event_id):
        return True

    event = {
        "event_id": event_id,
        "as_of": as_of,
        "severity": severity,
        "message": message,
        "fired_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    ok = _send(event)
    if ok:
        mark_delivered(as_of, event_id)
    return ok
