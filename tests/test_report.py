import csv
import os
import subprocess
import sys
import time

import pytest

from alert import fire
from alert_state import already_delivered
from data_loader import (
    CAT_DUPLICATE_SALES_ROW,
    CAT_DUPLICATE_SKU_WAREHOUSE,
    CAT_INVALID_LEAD_TIME,
    CAT_MISSING_FIELD,
    CAT_NEGATIVE_UNITS,
    CAT_NON_FINITE_ON_HAND,
    CAT_NON_FINITE_UNITS,
    CAT_NON_INTEGER_ON_HAND,
    CAT_NON_NUMERIC_ON_HAND,
    load_inventory,
    load_sales,
)
from reorder import compute_reorder_points, compute_sales_outliers, weekly_trend
from report import write_csv_summary, write_exceptions_csv

AS_OF = "2026-01-15"

INV_HEADER = "sku,product_name,category,warehouse,on_hand,lead_time_days\n"
SALES_HEADER = "date,sku,warehouse,units_sold\n"


def _write(tmp_path, name, header, body):
    path = os.path.join(tmp_path, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.write(body)
    return path


def test_missing_inventory_file(tmp_path):
    result = load_inventory(str(tmp_path / "nope.csv"))
    assert result.file_present is False


def test_cli_fails_loudly_on_header_only_inventory_file(tmp_path):
    # Astra follow-up finding 1 regression, exact probe fixture: a
    # header-only inventory CSV used to give exit 0, "OK", no alert. It
    # must now fail non-zero, fire a critical alert, and the report itself
    # must say "no data" rather than silently looking like a clean check.
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    inv_csv = tmp_path / "inv.csv"
    inv_csv.write_text("sku,product_name,warehouse,on_hand,lead_time_days\n")
    sales_csv = tmp_path / "sales.csv"
    sales_csv.write_text("date,sku,warehouse,units_sold\n2026-01-15,s,w,1\n")

    env = dict(os.environ, NOTIFY_CMD="true", INV_STATE_DIR=str(tmp_path / "state"))
    proc = subprocess.run(
        [sys.executable, os.path.join(src_dir, "generate_report.py"),
         "--inventory", str(inv_csv), "--sales", str(sales_csv),
         "--output-dir", str(tmp_path / "output"), "--as-of", AS_OF],
        cwd=src_dir, env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "OK: report" not in proc.stdout
    html = (tmp_path / "output" / f"low-stock-report-{AS_OF}.html").read_text()
    assert "no data" in html.lower()


def test_empty_inventory_load_result_has_no_rows(tmp_path):
    inv_csv = tmp_path / "inv.csv"
    inv_csv.write_text("sku,product_name,warehouse,on_hand,lead_time_days\n")
    result = load_inventory(str(inv_csv))
    assert result.file_present is True
    assert result.file_empty is True
    assert result.rows == []


def test_valid_inventory_row_parses(tmp_path):
    row = "SKU-1,Lamp,Home,wh-east,50,7\n"
    path = _write(tmp_path, "inv.csv", INV_HEADER, row)
    result = load_inventory(path)
    assert len(result.rows) == 1
    assert result.rows[0].on_hand == 50


def test_missing_field_is_bad_row_inventory(tmp_path):
    row = "SKU-1,,Home,wh-east,50,7\n"
    path = _write(tmp_path, "inv.csv", INV_HEADER, row)
    result = load_inventory(path)
    assert result.bad_row_categories[CAT_MISSING_FIELD] == 1


def test_non_numeric_on_hand_is_bad_row(tmp_path):
    row = "SKU-1,Lamp,Home,wh-east,lots,7\n"
    path = _write(tmp_path, "inv.csv", INV_HEADER, row)
    result = load_inventory(path)
    assert result.bad_row_categories[CAT_NON_NUMERIC_ON_HAND] == 1


def test_zero_lead_time_is_bad_row(tmp_path):
    row = "SKU-1,Lamp,Home,wh-east,50,0\n"
    path = _write(tmp_path, "inv.csv", INV_HEADER, row)
    result = load_inventory(path)
    assert result.bad_row_categories[CAT_INVALID_LEAD_TIME] == 1


def test_duplicate_sku_warehouse_is_quarantined(tmp_path):
    rows = "SKU-1,Lamp,Home,wh-east,50,7\nSKU-1,Lamp,Home,wh-east,99,7\n"
    path = _write(tmp_path, "inv.csv", INV_HEADER, rows)
    result = load_inventory(path)
    assert len(result.rows) == 1
    assert result.rows[0].on_hand == 50
    assert len(result.quarantined) == 1
    assert result.quarantined[0].category == CAT_DUPLICATE_SKU_WAREHOUSE


def test_infinite_on_hand_is_validation_exception_not_crash(tmp_path):
    # Astra finding 4 regression: int(float("inf")) raises an uncaught
    # OverflowError in the old code. It must now be a categorized bad row.
    row = "SKU-1,Lamp,Home,wh-east,inf,7\n"
    path = _write(tmp_path, "inv.csv", INV_HEADER, row)
    result = load_inventory(path)  # must not raise
    assert result.rows == []
    assert result.bad_row_categories[CAT_NON_FINITE_ON_HAND] == 1


def test_nan_on_hand_is_validation_exception_not_crash(tmp_path):
    row = "SKU-1,Lamp,Home,wh-east,nan,7\n"
    path = _write(tmp_path, "inv.csv", INV_HEADER, row)
    result = load_inventory(path)  # must not raise
    assert result.rows == []
    assert result.bad_row_categories[CAT_NON_FINITE_ON_HAND] == 1


def test_fractional_on_hand_is_validation_exception_not_silently_truncated(tmp_path):
    row = "SKU-1,Lamp,Home,wh-east,12.5,7\n"
    path = _write(tmp_path, "inv.csv", INV_HEADER, row)
    result = load_inventory(path)
    assert result.rows == []
    assert result.bad_row_categories[CAT_NON_INTEGER_ON_HAND] == 1


def test_infinite_units_sold_is_validation_exception_not_crash(tmp_path):
    row = "2026-01-15,SKU-1,wh-east,inf\n"
    path = _write(tmp_path, "sales.csv", SALES_HEADER, row)
    result = load_sales(path)  # must not raise
    assert result.rows == []
    assert result.bad_row_categories[CAT_NON_FINITE_UNITS] == 1


def test_negative_units_sold_is_bad_row(tmp_path):
    row = "2026-01-15,SKU-1,wh-east,-3\n"
    path = _write(tmp_path, "sales.csv", SALES_HEADER, row)
    result = load_sales(path)
    assert result.bad_row_categories[CAT_NEGATIVE_UNITS] == 1


def test_duplicate_sales_row_is_quarantined(tmp_path):
    rows = "2026-01-15,SKU-1,wh-east,5\n2026-01-15,SKU-1,wh-east,9\n"
    path = _write(tmp_path, "sales.csv", SALES_HEADER, rows)
    result = load_sales(path)
    assert len(result.rows) == 1
    assert result.rows[0].units_sold == 5
    assert result.quarantined[0].category == CAT_DUPLICATE_SALES_ROW


def test_reorder_point_flags_low_stock():
    from data_loader import InventoryRow, SalesRow

    inv = [InventoryRow(sku="SKU-1", product_name="Lamp", warehouse="wh-east", on_hand=5, lead_time_days=10)]
    sales = []
    for i in range(30):
        from datetime import datetime, timedelta
        d = (datetime.strptime(AS_OF, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")
        sales.append(SalesRow(date=d, sku="SKU-1", warehouse="wh-east", units_sold=6))

    results = compute_reorder_points(inv, sales, AS_OF)
    assert len(results) == 1
    r = results[0]
    assert r.status == "LOW"
    assert r.reorder_point > 5


def test_reorder_point_no_history_status():
    from data_loader import InventoryRow

    inv = [InventoryRow(sku="SKU-9", product_name="Untracked", warehouse="wh-east", on_hand=100, lead_time_days=7)]
    results = compute_reorder_points(inv, [], AS_OF)
    assert results[0].status == "NO_HISTORY"
    assert results[0].insufficient_history is True


def test_reorder_point_ok_when_on_hand_comfortable():
    from data_loader import InventoryRow, SalesRow
    from datetime import datetime, timedelta

    inv = [InventoryRow(sku="SKU-2", product_name="Bag", warehouse="wh-east", on_hand=500, lead_time_days=5)]
    sales = [
        SalesRow(
            date=(datetime.strptime(AS_OF, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d"),
            sku="SKU-2", warehouse="wh-east", units_sold=2,
        )
        for i in range(30)
    ]
    results = compute_reorder_points(inv, sales, AS_OF)
    assert results[0].status == "OK"


def test_two_days_of_sales_is_not_treated_as_full_history_regression():
    # Exact Astra probe fixture (finding 3): only 2 days of sales rows
    # exist against a 28-day default lookback. The old code treated
    # "2 nonzero days" as sufficient (status OK, avg=0.71 from summing
    # 2 real days over 28 assumed days). It must now be NO_HISTORY,
    # never OK, because the window isn't actually covered.
    from data_loader import InventoryRow, SalesRow

    inv = [InventoryRow(sku="s", product_name="p", warehouse="w", on_hand=100, lead_time_days=3)]
    sales = [
        SalesRow(date="2026-01-14", sku="s", warehouse="w", units_sold=10),
        SalesRow(date="2026-01-15", sku="s", warehouse="w", units_sold=10),
    ]
    r = compute_reorder_points(inv, sales, "2026-01-15")[0]
    assert r.status == "NO_HISTORY"
    assert r.status != "OK"
    assert r.insufficient_history is True
    assert r.observed_days == 2
    assert r.required_days == 28
    assert r.days_of_cover == float("inf")  # no trusted days-of-cover number either


def test_full_window_coverage_is_required_for_a_trusted_status():
    # Complement: a SKU with a sales row for every day of the lookback
    # window (even a short custom one) DOES get a real LOW/OK status.
    from data_loader import InventoryRow, SalesRow
    from datetime import datetime, timedelta

    inv = [InventoryRow(sku="s", product_name="p", warehouse="w", on_hand=100, lead_time_days=3)]
    sales = [
        SalesRow(
            date=(datetime.strptime("2026-01-15", "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d"),
            sku="s", warehouse="w", units_sold=1,
        )
        for i in range(5)
    ]
    r = compute_reorder_points(inv, sales, "2026-01-15", lookback_days=5)[0]
    assert r.insufficient_history is False
    assert r.status in ("LOW", "OK")
    assert r.observed_days == 5


def test_future_sale_excluded_from_weekly_trend_regression():
    # Exact Astra probe fixture (finding 4): a sale dated the day AFTER
    # as_of, in the same ISO week, must not appear in that week's total.
    from data_loader import SalesRow

    future_sale = SalesRow(date="2026-01-16", sku="s", warehouse="w", units_sold=999)
    trend = weekly_trend([future_sale], "2026-01-15")
    assert trend[-1][1] == 0
    assert 999 not in [v for _, v in trend]


def test_sales_outlier_detection():
    from data_loader import SalesRow
    from datetime import datetime, timedelta

    rows = [
        SalesRow(
            date=(datetime.strptime(AS_OF, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d"),
            sku="SKU-1", warehouse="wh-east", units_sold=5,
        )
        for i in range(10)
    ]
    spike = SalesRow(date=AS_OF, sku="SKU-1", warehouse="wh-east", units_sold=200)
    rows.append(spike)
    outliers = compute_sales_outliers(rows)
    assert id(spike) in outliers


def test_weekly_trend_zero_fills_missing_weeks():
    from data_loader import SalesRow

    rows = [SalesRow(date=AS_OF, sku="SKU-1", warehouse="wh-east", units_sold=10)]
    trend = weekly_trend(rows, AS_OF, weeks=4)
    assert len(trend) == 4
    assert sum(v for _, v in trend) == 10


def test_csv_summary_and_exceptions_written(tmp_path):
    from data_loader import LoadResult
    from reorder import ReorderResult

    results = [ReorderResult(
        sku="SKU-1", warehouse="wh-east", product_name="Lamp", on_hand=5,
        lead_time_days=7, avg_daily_sales=3.0, stdev_daily_sales=1.0,
        reorder_point=25.0, days_of_cover=1.7, status="LOW", insufficient_history=False,
    )]
    csv_out = tmp_path / "summary.csv"
    write_csv_summary(str(csv_out), AS_OF, results)
    assert csv_out.exists()
    with open(csv_out) as fh:
        text = fh.read()
    assert "SKU-1" in text
    assert "LOW" in text

    inv_result = LoadResult(bad_row_categories={"missing_field": 1})
    sales_result = LoadResult()
    exc_out = tmp_path / "exceptions.csv"
    write_exceptions_csv(str(exc_out), inv_result, sales_result, outlier_count=2)
    with open(exc_out) as fh:
        rows = list(csv.reader(fh))
    assert any("missing_field" in r for r in rows)
    assert any("sales_spike" in r for r in rows)


def test_alert_fires_and_is_idempotent_on_rerun(tmp_path, monkeypatch):
    monkeypatch.setenv("INV_STATE_DIR", str(tmp_path / "state"))
    calls = []
    import alert as alert_mod
    monkeypatch.setattr(alert_mod, "_send", lambda event: calls.append(event) or True)

    ok1 = fire(AS_OF, "low_stock", "warning", "first")
    ok2 = fire(AS_OF, "low_stock", "warning", "second (rerun)")

    assert ok1 is True and ok2 is True
    assert len(calls) == 1
    assert already_delivered(AS_OF, "low_stock") is True


def test_cli_end_to_end_writes_report_files(tmp_path):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(repo_root, "src")
    gen_script = os.path.join(repo_root, "data", "sample", "generate_sample_data.py")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    subprocess.run(
        [sys.executable, gen_script, "--out-dir", str(data_dir), "--as-of", AS_OF, "--seed", "7"],
        check=True,
    )

    out_dir = tmp_path / "output"
    env = dict(os.environ, INV_STATE_DIR=str(tmp_path / "state"))
    proc = subprocess.run(
        [sys.executable, os.path.join(src_dir, "generate_report.py"),
         "--inventory", str(data_dir / "inventory.csv"),
         "--sales", str(data_dir / "sales.csv"),
         "--output-dir", str(out_dir), "--as-of", AS_OF],
        cwd=src_dir, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out_dir / f"low-stock-report-{AS_OF}.html").exists()
    assert (out_dir / f"low-stock-summary-{AS_OF}.csv").exists()
    assert (out_dir / f"exceptions-{AS_OF}.csv").exists()
    assert (out_dir / f"weekly-trend-{AS_OF}.png").exists()


def test_run_daily_skips_when_lock_held(tmp_path):
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    lock_file = tmp_path / "held.lock"
    alerts_log = tmp_path / "alerts.log"

    holder = subprocess.Popen(["bash", "-c", f'exec 9>"{lock_file}"; flock 9; sleep 5'])
    try:
        time.sleep(0.5)
        env = dict(
            os.environ,
            INV_LOCK_FILE=str(lock_file),
            INV_ALERTS_LOG=str(alerts_log),
            INV_OUTPUT_DIR=str(tmp_path / "output"),
        )
        proc = subprocess.run(
            ["bash", os.path.join(src_dir, "run_daily.sh")],
            env=env, capture_output=True, text=True, timeout=20,
        )
        assert proc.returncode == 0
        assert "another run is already in progress" in alerts_log.read_text()
        assert not (tmp_path / "output").exists()
    finally:
        holder.kill()
        holder.wait()


def test_cli_exits_nonzero_and_no_ok_when_notifier_fails(tmp_path):
    # Astra finding 1 regression, via the real CLI subprocess. A SKU that's
    # LOW guarantees at least one fire() call happens; NOTIFY_CMD=false
    # makes delivery fail, so the CLI must exit non-zero and never print
    # "OK: report".
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    inv_csv = tmp_path / "inventory.csv"
    inv_csv.write_text(INV_HEADER + "SKU-1,Lamp,Home,wh-east,0,7\n")
    sales_csv = tmp_path / "sales.csv"
    from datetime import datetime, timedelta
    lines = [SALES_HEADER.strip()]
    for i in range(28):
        d = (datetime.strptime(AS_OF, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")
        lines.append(f"{d},SKU-1,wh-east,5")
    sales_csv.write_text("\n".join(lines) + "\n")

    env = dict(os.environ, NOTIFY_CMD="false", INV_STATE_DIR=str(tmp_path / "state"))
    proc = subprocess.run(
        [sys.executable, os.path.join(src_dir, "generate_report.py"),
         "--inventory", str(inv_csv), "--sales", str(sales_csv),
         "--output-dir", str(tmp_path / "output"), "--as-of", AS_OF],
        cwd=src_dir, env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "OK: report" not in proc.stdout
    assert "PARTIAL" in proc.stdout or "PARTIAL" in proc.stderr


def test_run_daily_retries_delivery_after_notifier_failure(tmp_path):
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    inv_csv = tmp_path / "inventory.csv"
    inv_csv.write_text(INV_HEADER + "SKU-1,Lamp,Home,wh-east,0,7\n")
    sales_csv = tmp_path / "sales.csv"
    from datetime import datetime, timedelta
    lines = [SALES_HEADER.strip()]
    for i in range(28):
        d = (datetime.strptime(AS_OF, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")
        lines.append(f"{d},SKU-1,wh-east,5")
    sales_csv.write_text("\n".join(lines) + "\n")

    env = dict(
        os.environ,
        NOTIFY_CMD="false",
        INV_INVENTORY=str(inv_csv),
        INV_SALES=str(sales_csv),
        INV_AS_OF=AS_OF,
        INV_OUTPUT_DIR=str(tmp_path / "output"),
        INV_ALERTS_LOG=str(tmp_path / "alerts.log"),
        INV_LOCK_FILE=str(tmp_path / "run.lock"),
        INV_RETRY_ATTEMPTS="2",
        INV_RETRY_BACKOFF_SECONDS="0",
        INV_STATE_DIR=str(tmp_path / "state"),
    )
    proc = subprocess.run(
        ["bash", os.path.join(src_dir, "run_daily.sh")],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    log = (tmp_path / "alerts.log").read_text()
    assert "attempt 1/2 failed" in log
    assert "CRITICAL" in log


def test_run_daily_retries_then_escalates(tmp_path):
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    env = dict(
        os.environ,
        INV_INVENTORY=str(tmp_path / "missing-inv.csv"),
        INV_SALES=str(tmp_path / "missing-sales.csv"),
        INV_OUTPUT_DIR=str(tmp_path / "output"),
        INV_ALERTS_LOG=str(tmp_path / "alerts.log"),
        INV_LOCK_FILE=str(tmp_path / "run.lock"),
        INV_RETRY_ATTEMPTS="2",
        INV_RETRY_BACKOFF_SECONDS="0",
        INV_STATE_DIR=str(tmp_path / "state"),
    )
    proc = subprocess.run(
        ["bash", os.path.join(src_dir, "run_daily.sh")],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    log = (tmp_path / "alerts.log").read_text()
    assert "attempt 1/2 failed" in log
    assert "CRITICAL" in log
