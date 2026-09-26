#!/usr/bin/env python3
"""
CLI entry point: load inventory + sales history, compute reorder points,
render the low-stock HTML report + CSV summary + exceptions CSV + weekly
trend chart, and fire alerts.

Exit codes: 0 = report generated. Non-zero = the run itself failed (an
input file missing entirely, or an unhandled error) -- this is what
run_daily.sh's retry loop watches for.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

from alert import fire
from chart import render_weekly_trend_chart
from data_loader import load_inventory, load_sales
from reorder import compute_reorder_points, compute_sales_outliers, weekly_trend
from report import render_html, write_csv_summary, write_exceptions_csv


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory", required=True, help="path to the inventory snapshot CSV")
    p.add_argument("--sales", required=True, help="path to the sales history CSV")
    p.add_argument("--output-dir", required=True, help="directory to write report files into")
    p.add_argument("--as-of", default=None, help="as-of date YYYY-MM-DD (default: today, UTC)")
    p.add_argument("--lookback-days", type=int, default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv or sys.argv[1:])
    as_of = args.as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    os.makedirs(args.output_dir, exist_ok=True)

    inv_result = load_inventory(args.inventory)
    if not inv_result.file_present:
        fire(as_of, "inventory_input_missing", "critical",
             f"Inventory input file not found: {args.inventory}")
        print(f"ERROR: inventory file not found: {args.inventory}", file=sys.stderr)
        return 2

    sales_result = load_sales(args.sales)
    if not sales_result.file_present:
        fire(as_of, "sales_input_missing", "critical",
             f"Sales input file not found: {args.sales}")
        print(f"ERROR: sales file not found: {args.sales}", file=sys.stderr)
        return 2

    outlier_ids = compute_sales_outliers(sales_result.rows)
    results = compute_reorder_points(
        inv_result.rows, sales_result.rows, as_of, args.lookback_days
    )
    trend = weekly_trend(sales_result.rows, as_of)

    chart_filename = f"weekly-trend-{as_of}.png"
    chart_path = os.path.join(args.output_dir, chart_filename)
    render_weekly_trend_chart(trend, chart_path)

    html_path = os.path.join(args.output_dir, f"low-stock-report-{as_of}.html")
    csv_path = os.path.join(args.output_dir, f"low-stock-summary-{as_of}.csv")
    exceptions_path = os.path.join(args.output_dir, f"exceptions-{as_of}.csv")

    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(render_html(
            as_of, results, inv_result, sales_result, len(outlier_ids),
            trend_chart_rel_path=chart_filename,
        ))
    write_csv_summary(csv_path, as_of, results)
    write_exceptions_csv(exceptions_path, inv_result, sales_result, len(outlier_ids))

    total_bad = inv_result.total_bad_rows() + sales_result.total_bad_rows()
    if total_bad > 0:
        fire(as_of, "malformed_rows", "warning",
             f"{total_bad} malformed row(s) skipped on {as_of} "
             f"(inventory: {dict(inv_result.bad_row_categories)}, "
             f"sales: {dict(sales_result.bad_row_categories)})")

    low = [r for r in results if r.status == "LOW"]
    if low:
        skus = ", ".join(f"{r.sku}@{r.warehouse}" for r in low[:10])
        more = f" (+{len(low) - 10} more)" if len(low) > 10 else ""
        fire(as_of, "low_stock", "warning",
             f"{len(low)} SKU/warehouse combination(s) below reorder point on {as_of}: {skus}{more}")

    print(f"OK: report for {as_of} -> {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
