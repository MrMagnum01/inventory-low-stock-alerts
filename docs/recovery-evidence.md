# Scheduled recovery evidence

A real `systemd --user` timer ran this repo's `src/run_daily.sh` on this
box for ~9 minutes (2026-09-26 13:08–13:16 UTC), with a forced failure, a
recovery, and a simulated scheduler restart, against a controlled local
notification receiver (`rehearsal-notify.sh`, appends the JSON payload it
receives to a log; never contacts anything external). This closes the
"real scheduled failure/recovery and restart evidence" gap Astra flagged
as still open after the code-level fixes, and doubles as a live exercise
of the header-only-inventory fix (finding 1 of the follow-up review).

This was throwaway infrastructure: a temporary timer/service pair under
`~/.config/systemd/user/`, firing every 50s, pointed at a
`rehearsal-input/`, `rehearsal-output/`, `rehearsal-state/` set of
directories separate from the checked-in `data/sample/`. **Both unit
files were deleted and the timer stopped/disabled at the end of the
rehearsal; nothing was left running or installed.** Paths below are
sanitized (`/opt/inventory-low-stock-alerts/...` in place of this box's
real checkout path; hostname replaced with `rehearsal-host`).

## Timeline

| Phase | What happened |
|---|---|
| 13:08:06 | Timer started. `rehearsal-input/inventory.csv` deliberately header-only (forced failure); `rehearsal-input/sales.csv` valid (the fixed-seed sample, with its own deliberately-planted exceptions) from the start. |
| 13:08:15 – 13:10:00 | Three separate timer firings (~13:08:15, 13:09:04, 13:09:55), each: `run_daily.sh` retries once (3s backoff), still fails (`exit 4` — the new empty-inventory exit code), logs `CRITICAL`. |
| 13:10:36 | Recovery: real fixed-seed `data/sample/inventory.csv` copied into `rehearsal-input/inventory.csv`. No code/service change. |
| 13:10:46 onward | Next and all subsequent firings: `OK: report for 2026-01-15`. |
| 13:13:10 | **Simulated scheduler restart**: `systemctl --user stop` then `start` on the timer, mid-rehearsal. |
| 13:13:16 – 13:15:49 | Firings continue post-restart, every ~50s, all `OK`. |
| 13:16:11 | Timer stopped, disabled, unit files deleted, `daemon-reload`. |

Full sanitized excerpts: [`recovery-evidence-journal.log`](recovery-evidence-journal.log) (systemd journal, one line per invocation), [`recovery-evidence-alerts.log`](recovery-evidence-alerts.log) (`run_daily.sh`'s own retry/escalation log), [`recovery-evidence-notify-received.log`](recovery-evidence-notify-received.log) (everything the controlled receiver got).

## What failed

Inventory input present but header-only (zero usable rows). `generate_report.py` fires the `no_inventory_data` critical alert and exits 4 (the follow-up-review fix: this used to exit 0/`OK` with no alert). `run_daily.sh` retries once after 3s, still fails (input is still empty on retry), logs `[CRITICAL] ... failed after 2 attempt(s), exit 4` to `alerts.log`.

## What retried

- **Within one run**: `run_daily.sh`'s own retry loop (2 attempts, 3s backoff) — one `[INFO] attempt 1/2 failed ... retrying in 3s` line per firing in `recovery-evidence-alerts.log`.
- **Across runs**: the timer re-fired every ~50s and re-attempted the whole job three times during the outage (13:08:15, 13:09:04, 13:09:55) before the inventory file was populated.
- **Across a restart**: after the simulated `systemctl --user stop`/`start` at 13:13:10, the timer resumed firing on schedule and kept succeeding — state persisted on disk through the restart.

## Idempotent output check

- **No duplicate alerts despite 3 separate failed firings**: `recovery-evidence-notify-received.log` shows exactly **one** `no_inventory_data` delivery (13:08:15) and exactly **one** `malformed_rows` delivery (13:08:15, from the sales file's own planted exceptions, present from the start) — not three of each — despite the underlying failure recurring at 13:08:15, 13:09:04, and 13:09:55.
- **No duplicate alerts across 7 successful post-recovery runs, including across the restart**: `low_stock` (5 SKU/warehouse combinations below reorder point, from the fixed-seed sample data) appears exactly **once**, at 13:10:46, despite the report being regenerated successfully 6 more times afterward through 13:15:49, spanning the 13:13:10 restart.
- **Deterministic report content**: re-ran `generate_report.py` twice in a row against the same rehearsal input/date immediately after the timed rehearsal and diffed both HTML reports with the `generated <timestamp>` line stripped — byte-identical.

## What this does and does not establish

Establishes: the real scheduler retries a genuine empty-inventory failure (exercising the follow-up fix live, not just in a unit test), escalates to `CRITICAL` after exhausting retries, recovers cleanly once real inventory data appears with no code change, survives a scheduler restart without duplicating alerts or corrupting state, and produces deterministic report output across reruns.

Does not establish: behavior against a *real* notification channel (the receiver here is a local script, not email/Slack/PagerDuty), behavior over a much longer unattended period (this was ~9 minutes, not days), or anything about client-specific data/volume. Support/SLA scope is unchanged from the README.
