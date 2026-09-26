"""
Renders the low-stock alert report (HTML + CSV) and the exceptions CSV.
All CSV-derived text is HTML-escaped before reaching the template.
"""
import csv
import html
from collections import defaultdict
from datetime import datetime, timezone


def _esc(v) -> str:
    return html.escape(str(v), quote=True)


def write_csv_summary(path, as_of, results):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["as_of", as_of])
        w.writerow(["sku_warehouse_count", len(results)])
        w.writerow(["low_stock_count", sum(1 for r in results if r.status == "LOW")])
        w.writerow(["no_history_count", sum(1 for r in results if r.status == "NO_HISTORY")])
        w.writerow([])
        w.writerow([
            "sku", "product_name", "warehouse", "status", "on_hand",
            "avg_daily_sales", "stdev_daily_sales", "reorder_point", "days_of_cover",
            "observed_days", "required_days",
        ])
        for r in sorted(results, key=lambda x: (x.status != "LOW", x.sku, x.warehouse)):
            w.writerow([
                r.sku, r.product_name, r.warehouse, r.status, r.on_hand,
                r.avg_daily_sales, r.stdev_daily_sales, r.reorder_point,
                "inf" if r.days_of_cover == float("inf") else r.days_of_cover,
                r.observed_days, r.required_days,
            ])


def write_exceptions_csv(path, inv_result, sales_result, outlier_count):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "kind", "category", "detail", "raw"])
        for cat, count in sorted(inv_result.bad_row_categories.items()):
            w.writerow(["inventory", "bad_row_category_count", cat, count, ""])
        for q in inv_result.quarantined:
            w.writerow(["inventory", "quarantined", q.category, q.detail, dict(q.raw)])
        for cat, count in sorted(sales_result.bad_row_categories.items()):
            w.writerow(["sales", "bad_row_category_count", cat, count, ""])
        for q in sales_result.quarantined:
            w.writerow(["sales", "quarantined", q.category, q.detail, dict(q.raw)])
        if outlier_count:
            w.writerow(["sales", "outlier_flag_count", "sales_spike", outlier_count, ""])


def render_html(as_of, results, inv_result, sales_result, outlier_count, trend_chart_rel_path=None, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    low = [r for r in results if r.status == "LOW"]
    no_hist = [r for r in results if r.status == "NO_HISTORY"]
    ok = [r for r in results if r.status == "OK"]

    def row_html(r):
        cover = "—" if r.days_of_cover == float("inf") else f"{r.days_of_cover:g} d"
        return (
            f"<tr><td>{_esc(r.sku)}</td><td>{_esc(r.product_name)}</td>"
            f"<td>{_esc(r.warehouse)}</td><td>{r.on_hand}</td>"
            f"<td>{r.avg_daily_sales:g}</td><td>{r.reorder_point:g}</td>"
            f"<td>{_esc(cover)}</td></tr>"
        )

    def no_hist_row_html(r):
        return (
            f"<tr><td>{_esc(r.sku)}</td><td>{_esc(r.product_name)}</td>"
            f"<td>{_esc(r.warehouse)}</td><td>{r.on_hand}</td>"
            f"<td>{_esc(r.observed_days)} / {_esc(r.required_days)} days</td></tr>"
        )

    low_rows = "\n".join(row_html(r) for r in low) or "<tr><td colspan=7>No SKUs below reorder point</td></tr>"
    no_hist_rows = "\n".join(no_hist_row_html(r) for r in no_hist) or "<tr><td colspan=5>None</td></tr>"

    bad_rows_html = ""
    total_bad = inv_result.total_bad_rows() + sales_result.total_bad_rows()
    if total_bad:
        items = "".join(
            f"<li>inventory / {_esc(cat)}: {count}</li>"
            for cat, count in sorted(inv_result.bad_row_categories.items())
        ) + "".join(
            f"<li>sales / {_esc(cat)}: {count}</li>"
            for cat, count in sorted(sales_result.bad_row_categories.items())
        )
        bad_rows_html = f"<div class='flag warn'><strong>Malformed rows skipped:</strong><ul>{items}</ul></div>"

    quarantine_html = ""
    total_q = len(inv_result.quarantined) + len(sales_result.quarantined)
    if total_q:
        by_cat = defaultdict(int)
        for q in inv_result.quarantined:
            by_cat[f"inventory / {q.category}"] += 1
        for q in sales_result.quarantined:
            by_cat[f"sales / {q.category}"] += 1
        items = "".join(f"<li>{_esc(cat)}: {count}</li>" for cat, count in sorted(by_cat.items()))
        quarantine_html = f"<div class='flag warn'><strong>Rows quarantined (excluded from calculations):</strong><ul>{items}</ul></div>"

    outlier_html = ""
    if outlier_count:
        outlier_html = f"<div class='flag'><strong>{outlier_count} sales row(s) flagged as demand spikes</strong> (kept in the average, see exceptions report).</div>"

    chart_html = ""
    if trend_chart_rel_path:
        chart_html = f"<h2>Weekly sales trend</h2><img src='{_esc(trend_chart_rel_path)}' alt='Weekly units sold trend' style='max-width:100%;border:1px solid #ddd;border-radius:8px;'>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Low-Stock Alert Report — {_esc(as_of)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; color: #1a1a1a; background: #fafafa; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  .kpis {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
  .kpi {{ background: white; border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.4rem; min-width: 140px; }}
  .kpi.low .value {{ color: #c62828; }}
  .kpi .label {{ font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.03em; }}
  .kpi .value {{ font-size: 1.6rem; font-weight: 600; margin-top: 0.2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; background: white; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.7rem; text-align: left; font-size: 0.92rem; }}
  th {{ background: #f0f0f0; }}
  .flag {{ background: #fff8e1; border: 1px solid #f0d878; border-radius: 6px; padding: 0.8rem 1rem; margin-bottom: 1rem; font-size: 0.9rem; }}
  .flag.warn {{ background: #fff3e0; border-color: #f0b878; }}
  h2 {{ margin-top: 2rem; }}
  .note {{ color: #666; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>Low-Stock Alert Report</h1>
<div class="meta">As of: {_esc(as_of)} &middot; generated {_esc(generated_at)}</div>

<div class="kpis">
  <div class="kpi low"><div class="label">Low stock</div><div class="value">{len(low)}</div></div>
  <div class="kpi"><div class="label">OK</div><div class="value">{len(ok)}</div></div>
  <div class="kpi"><div class="label">No history</div><div class="value">{len(no_hist)}</div></div>
  <div class="kpi"><div class="label">SKU x warehouse</div><div class="value">{len(results)}</div></div>
</div>

{outlier_html}
{bad_rows_html}
{quarantine_html}

<h2>Below reorder point</h2>
<table>
<tr><th>SKU</th><th>Product</th><th>Warehouse</th><th>On hand</th><th>Avg daily sales</th><th>Reorder point</th><th>Days of cover</th></tr>
{low_rows}
</table>

<h2>Insufficient sales history</h2>
<p class="note">Shown when the sales input does not cover the full lookback window for that SKU/warehouse -- no reorder point is computed or trusted.</p>
<table>
<tr><th>SKU</th><th>Product</th><th>Warehouse</th><th>On hand</th><th>Days of coverage observed</th></tr>
{no_hist_rows}
</table>

{chart_html}

</body>
</html>
"""
