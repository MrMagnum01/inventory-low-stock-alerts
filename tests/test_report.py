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
