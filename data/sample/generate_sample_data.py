#!/usr/bin/env python3
"""
Generates fully synthetic inventory.csv and sales.csv for the demo --
fixed seed, no real products/customers/companies. Plants rows that
exercise every validation and business-logic path: missing fields,
non-numeric/invalid values, duplicate keys, a demand spike outlier, at
least one guaranteed LOW-stock SKU, and one SKU with no sales history at
all (NO_HISTORY).

Usage: python3 generate_sample_data.py --out-dir . --as-of 2026-01-15 --seed 7
"""
import argparse
import csv
import random
from datetime import datetime, timedelta

PRODUCTS = [
    ("SKU-2001", "Aurora Desk Lamp", "Home"),
    ("SKU-2002", "Nimbus Backpack", "Bags"),
    ("SKU-2003", "Cobalt Water Bottle", "Outdoor"),
    ("SKU-2004", "Quartz Wireless Mouse", "Electronics"),
    ("SKU-2005", "Fernwood Notebook Set", "Stationery"),
    ("SKU-2006", "Drift Bluetooth Speaker", "Electronics"),
    ("SKU-2007", "Basalt Coffee Grinder", "Kitchen"),
    ("SKU-2008", "Meridian Yoga Mat", "Fitness"),
]
WAREHOUSES = ["wh-east", "wh-west"]
HISTORY_DAYS = 70  # >= LOOKBACK_DAYS(28) + TREND_WEEKS(8)*7, with margin


def gen_inventory(out_path, seed):
    rnd = random.Random(seed)
    rows = []
    for sku, name, category in PRODUCTS:
        for wh in WAREHOUSES:
            rows.append({
                "sku": sku, "product_name": name, "category": category,
                "warehouse": wh, "on_hand": rnd.randint(20, 220),
                "lead_time_days": rnd.choice([5, 7, 10, 14]),
            })

    # Force one guaranteed LOW-stock case: low on_hand, short lead time,
    # for a SKU that (below) gets steady sales history.
    for r in rows:
        if r["sku"] == "SKU-2004" and r["warehouse"] == "wh-east":
            r["on_hand"] = 5
            r["lead_time_days"] = 10

    # --- deliberately planted exception rows ------------------------------
    rows.append({  # missing product_name
        "sku": "SKU-2002", "product_name": "", "category": "Bags",
        "warehouse": "wh-west", "on_hand": 40, "lead_time_days": 7,
    })
    rows.append({  # non-numeric on_hand
        "sku": "SKU-2003", "product_name": "Cobalt Water Bottle", "category": "Outdoor",
        "warehouse": "wh-east", "on_hand": "lots", "lead_time_days": 7,
    })
    rows.append({  # invalid (zero) lead time
        "sku": "SKU-2007", "product_name": "Basalt Coffee Grinder", "category": "Kitchen",
        "warehouse": "wh-west", "on_hand": 30, "lead_time_days": 0,
    })
    dup = {
        "sku": "SKU-2005", "product_name": "Fernwood Notebook Set", "category": "Stationery",
        "warehouse": "wh-east", "on_hand": 60, "lead_time_days": 7,
    }
    rows.append(dup)
    rows.append(dict(dup))  # exact duplicate sku+warehouse

    fieldnames = ["sku", "product_name", "category", "warehouse", "on_hand", "lead_time_days"]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return out_path


def gen_sales(out_path, as_of: str, seed: int):
    rnd = random.Random(seed + 1000)
    end = datetime.strptime(as_of, "%Y-%m-%d")
    start = end - timedelta(days=HISTORY_DAYS - 1)

    rows = []
    for sku, name, category in PRODUCTS:
        if sku == "SKU-2008":
            continue  # deliberately no sales history at all -> NO_HISTORY case
        for wh in WAREHOUSES:
            base = rnd.uniform(2.0, 9.0)
            d = start
            while d <= end:
                # weekday/weekend variation + noise, never negative
                weekday_factor = 1.15 if d.weekday() < 5 else 0.75
                units = max(0, round(rnd.gauss(base * weekday_factor, base * 0.35)))
                rows.append({
                    "date": d.strftime("%Y-%m-%d"), "sku": sku, "warehouse": wh,
                    "units_sold": units,
                })
                d += timedelta(days=1)

    # Give SKU-2004 @ wh-east a steady, clearly-above-on-hand demand so it
    # reliably lands LOW with the forced on_hand=5 above.
    for r in rows:
        if r["sku"] == "SKU-2004" and r["warehouse"] == "wh-east":
            r["units_sold"] = max(r["units_sold"], rnd.randint(4, 8))

    # --- deliberately planted exception / outlier rows --------------------
    rows.append({  # bad date
        "date": "15-01-2026", "sku": "SKU-2001", "warehouse": "wh-east", "units_sold": 5,
    })
    rows.append({  # non-numeric units_sold
        "date": as_of, "sku": "SKU-2002", "warehouse": "wh-east", "units_sold": "many",
    })
    rows.append({  # negative units_sold
        "date": as_of, "sku": "SKU-2003", "warehouse": "wh-west", "units_sold": -4,
    })
    dup = {"date": as_of, "sku": "SKU-2006", "warehouse": "wh-west", "units_sold": 6}
    rows.append(dup)
    rows.append(dict(dup))  # exact duplicate date+sku+warehouse

    # A genuine demand-spike outlier for SKU-2006 @ wh-east, a few days
    # before as_of, well above that SKU's own history.
    spike_date = (end - timedelta(days=3)).strftime("%Y-%m-%d")
    rows.append({"date": spike_date, "sku": "SKU-2006", "warehouse": "wh-east", "units_sold": 95})

    rnd.shuffle(rows)

    fieldnames = ["date", "sku", "warehouse", "units_sold"]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return out_path


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default=".")
    p.add_argument("--as-of", default="2026-01-15")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    import os
    inv_path = os.path.join(args.out_dir, "inventory.csv")
    sales_path = os.path.join(args.out_dir, "sales.csv")
    gen_inventory(inv_path, args.seed)
    gen_sales(sales_path, args.as_of, args.seed)
    print(f"wrote {inv_path} and {sales_path} (as_of {args.as_of}, seed {args.seed})")
