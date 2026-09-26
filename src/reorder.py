"""
Reorder-point computation and weekly trend aggregation.

reorder_point = avg_daily_sales * lead_time_days
                + SERVICE_LEVEL_Z * stdev(daily_sales) * sqrt(lead_time_days)

The first term covers expected demand during the supplier lead time; the
second is safety stock against demand variability, using a standard
service-level z-score formula (see README "How the numbers are computed").
A SKU+warehouse with fewer than 2 days of sales history in the lookback
window gets a reorder point based on the mean alone (stdev undefined) and
is flagged `insufficient_history` in the report rather than silently
trusted.
"""
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from config import LOOKBACK_DAYS, SALES_OUTLIER_STDEV, SERVICE_LEVEL_Z, TREND_WEEKS


@dataclass
class ReorderResult:
    sku: str
    warehouse: str
    product_name: str
    on_hand: int
    lead_time_days: int
    avg_daily_sales: float
    stdev_daily_sales: float
    reorder_point: float
    days_of_cover: float
    status: str  # "LOW", "OK", or "NO_HISTORY"
    insufficient_history: bool
    observed_days: int = 0
    required_days: int = 0


def _daily_series(sales_rows, sku, warehouse, as_of: str, lookback_days: int):
    """Dense daily units_sold for one sku+warehouse over the lookback
    window ending at (and including) as_of, zero-filled for days that have
    an observed row on record but with zero units sold.

    Returns (series, observed_days): `series` is the zero-filled daily
    values (for the average/stdev math); `observed_days` is the count of
    days in the window for which the input actually contained a sales row
    for this sku+warehouse -- i.e. real *coverage* evidence, not merely
    "the average happened to come out non-trivial." A day with no row at
    all is NOT assumed to be a real zero-sales day (the file may simply
    not cover it yet); it contributes 0 to `series` for arithmetic
    convenience but does NOT count toward `observed_days`.
    """
    end = datetime.strptime(as_of, "%Y-%m-%d")
    start = end - timedelta(days=lookback_days - 1)
    by_date = defaultdict(int)
    observed_dates = set()
    for r in sales_rows:
        if r.sku != sku or r.warehouse != warehouse:
            continue
        d = datetime.strptime(r.date, "%Y-%m-%d")
        if start <= d <= end:
            by_date[r.date] += r.units_sold
            observed_dates.add(r.date)
    series = []
    d = start
    while d <= end:
        key = d.strftime("%Y-%m-%d")
        series.append(by_date.get(key, 0))
        d += timedelta(days=1)
    return series, len(observed_dates)


def compute_sales_outliers(sales_rows):
    """Flag individual sales rows whose units_sold is a statistical outlier
    relative to that SKU+warehouse's own history. Informational only --
    kept in the averages, just called out."""
    groups = defaultdict(list)
    for r in sales_rows:
        groups[(r.sku, r.warehouse)].append(r)

    flagged = set()
    for key, rows in groups.items():
        values = [r.units_sold for r in rows]
        if len(values) < 3:
            continue
        mean = statistics.mean(values)
        stdev = statistics.pstdev(values)
        if stdev == 0:
            continue
        threshold = mean + SALES_OUTLIER_STDEV * stdev
        for r in rows:
            if r.units_sold >= threshold and r.units_sold > 0:
                flagged.add(id(r))
    return flagged


def compute_reorder_points(inventory_rows, sales_rows, as_of: str, lookback_days: int = None):
    """
    A SKU/warehouse is only given a trusted LOW/OK status when the sales
    input actually *covers* the full lookback window -- i.e. it contains a
    row for every day in that window, not just some. Astra finding 3: two
    positive-sales rows out of a 28-day window used to be silently treated
    as "sufficient" (average = sum/28 including 26 assumed zero-sales
    days that were never actually reported). That conflated "no data" with
    "zero demand." Now: `observed_days < lookback_days` (the window is not
    fully covered) -> status is always "NO_HISTORY", regardless of what
    the partial average looks like. This is deliberately strict -- a
    single missing day in an otherwise-complete export also falls back to
    NO_HISTORY rather than silently trusting a slightly-incomplete window.
    """
    lookback_days = lookback_days or LOOKBACK_DAYS
    results = []
    for inv in inventory_rows:
        series, observed_days = _daily_series(sales_rows, inv.sku, inv.warehouse, as_of, lookback_days)
        avg = statistics.mean(series) if series else 0.0
        insufficient = observed_days < lookback_days

        if insufficient:
            stdev = 0.0
            safety_stock = 0.0
        else:
            stdev = statistics.pstdev(series)
            safety_stock = SERVICE_LEVEL_Z * stdev * math.sqrt(inv.lead_time_days)

        reorder_point = round(avg * inv.lead_time_days + safety_stock, 2)
        days_of_cover = round(inv.on_hand / avg, 1) if (avg > 0 and not insufficient) else float("inf")

        if insufficient:
            status = "NO_HISTORY"
        elif inv.on_hand < reorder_point:
            status = "LOW"
        else:
            status = "OK"

        results.append(ReorderResult(
            sku=inv.sku, warehouse=inv.warehouse, product_name=inv.product_name,
            on_hand=inv.on_hand, lead_time_days=inv.lead_time_days,
            avg_daily_sales=round(avg, 2), stdev_daily_sales=round(stdev, 2),
            reorder_point=reorder_point, days_of_cover=days_of_cover,
            status=status, insufficient_history=insufficient,
            observed_days=observed_days, required_days=lookback_days,
        ))
    return results


def weekly_trend(sales_rows, as_of: str, weeks: int = None):
    """Total units sold per ISO week, most recent `weeks` weeks ending at
    the week containing as_of. Returns a list of (week_label, total) tuples
    in chronological order, suitable for a line/bar chart.

    Rows dated *after* as_of are excluded, even if they fall within the
    same ISO week as as_of -- an "as of <date>" chart must not contain
    information from the future relative to that date (Astra finding 4).
    """
    weeks = weeks or TREND_WEEKS
    end = datetime.strptime(as_of, "%Y-%m-%d")
    totals = defaultdict(int)
    for r in sales_rows:
        d = datetime.strptime(r.date, "%Y-%m-%d")
        if d > end:
            continue
        iso_year, iso_week, _ = d.isocalendar()
        totals[(iso_year, iso_week)] += r.units_sold

    # Build the last `weeks` ISO-week keys ending at as_of's week, so weeks
    # with zero sales still appear (a real gap, not a missing bar).
    labels = []
    cursor = end
    seen_weeks = []
    for _ in range(weeks):
        iso_year, iso_week, _ = cursor.isocalendar()
        seen_weeks.append((iso_year, iso_week))
        cursor -= timedelta(days=7)
    seen_weeks = list(reversed(seen_weeks))

    result = []
    for (iso_year, iso_week) in seen_weeks:
        label = f"{iso_year}-W{iso_week:02d}"
        result.append((label, totals.get((iso_year, iso_week), 0)))
    return result
