"""
Per-run alert delivery state, so a retried or manually-rerun report on the
same as_of date does not re-fire notifications that already succeeded.

State file: <state_dir>/alerts-<as_of>.json, a JSON object of
{event_id: delivered_at_iso}. Written atomically (temp file + os.replace).
"""
import json
import os
import tempfile
from datetime import datetime, timezone

from config import get_state_dir


def _state_path(as_of: str) -> str:
    return os.path.join(get_state_dir(), f"alerts-{as_of}.json")


def _load(as_of: str) -> dict:
    path = _state_path(as_of)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def already_delivered(as_of: str, event_id: str) -> bool:
    return event_id in _load(as_of)


def mark_delivered(as_of: str, event_id: str) -> None:
    state_dir = get_state_dir()
    os.makedirs(state_dir, exist_ok=True)
    state = _load(as_of)
    state[event_id] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path = _state_path(as_of)
    fd, tmp_path = tempfile.mkstemp(dir=state_dir, prefix=".alerts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
