"""
Loads and validates the two input files: a current inventory snapshot and a
sales history log.

Same design convention throughout this project: a row that's syntactically
broken (missing field, wrong type) is a "bad row", skipped and categorised.
A row that parses fine but fails a data-quality check (duplicate key) is
"quarantined" -- kept out of the usable set, counted and listed separately
so the exceptions report can show why. Nothing is ever dropped silently.
"""
import csv
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Tuple

from config import INVENTORY_REQUIRED_COLUMNS, SALES_REQUIRED_COLUMNS

CAT_MISSING_FIELD = "missing_field"
CAT_NON_NUMERIC_ON_HAND = "non_numeric_on_hand"
CAT_NON_NUMERIC_LEAD_TIME = "non_numeric_lead_time"
CAT_INVALID_LEAD_TIME = "invalid_lead_time"  # <= 0
CAT_DUPLICATE_SKU_WAREHOUSE = "duplicate_sku_warehouse"

CAT_BAD_DATE = "bad_date"
CAT_NON_NUMERIC_UNITS = "non_numeric_units_sold"
CAT_NEGATIVE_UNITS = "negative_units_sold"
CAT_DUPLICATE_SALES_ROW = "duplicate_sales_row"


@dataclass
class InventoryRow:
    sku: str
    product_name: str
    warehouse: str
    on_hand: int
    lead_time_days: int
    category: str = ""


@dataclass
class SalesRow:
    date: str
    sku: str
    warehouse: str
    units_sold: int


@dataclass
class QuarantinedRow:
    category: str
    raw: dict
    detail: str


@dataclass
class LoadResult:
    rows: list = field(default_factory=list)
    bad_row_categories: dict = field(default_factory=dict)
    quarantined: List[QuarantinedRow] = field(default_factory=list)
    file_present: bool = False
    file_empty: bool = False

    def total_bad_rows(self) -> int:
        return sum(self.bad_row_categories.values())

    def _bump(self, cat):
        self.bad_row_categories[cat] = self.bad_row_categories.get(cat, 0) + 1


def load_inventory(csv_path: str) -> LoadResult:
    result = LoadResult()
    if not os.path.isfile(csv_path):
        return result
    result.file_present = True

    seen_keys = set()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            result.file_empty = True
            return result
        any_row = False
        for raw in reader:
            any_row = True
            missing = [c for c in INVENTORY_REQUIRED_COLUMNS if not (raw.get(c) or "").strip()]
            if missing:
                result._bump(CAT_MISSING_FIELD)
                continue

            try:
                on_hand = int(float(raw["on_hand"]))
                if on_hand < 0:
                    raise ValueError("negative on_hand")
            except (ValueError, TypeError):
                result._bump(CAT_NON_NUMERIC_ON_HAND)
                continue

            try:
                lead_time = int(float(raw["lead_time_days"]))
            except (ValueError, TypeError):
                result._bump(CAT_NON_NUMERIC_LEAD_TIME)
                continue

            if lead_time <= 0:
                result._bump(CAT_INVALID_LEAD_TIME)
                continue

            sku = raw["sku"].strip()
            warehouse = raw["warehouse"].strip()
            key = (sku, warehouse)
            if key in seen_keys:
                result.quarantined.append(QuarantinedRow(
                    category=CAT_DUPLICATE_SKU_WAREHOUSE, raw=dict(raw),
                    detail=f"duplicate sku+warehouse {key!r} (kept first occurrence)",
                ))
                continue
            seen_keys.add(key)

            result.rows.append(InventoryRow(
                sku=sku,
                product_name=raw["product_name"].strip(),
                warehouse=warehouse,
                on_hand=on_hand,
                lead_time_days=lead_time,
                category=(raw.get("category") or "").strip(),
            ))
        if not any_row:
            result.file_empty = True
    return result


def _parse_date(raw: str):
    raw = (raw or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def load_sales(csv_path: str) -> LoadResult:
    result = LoadResult()
    if not os.path.isfile(csv_path):
        return result
    result.file_present = True

    seen_keys = set()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            result.file_empty = True
            return result
        any_row = False
        for raw in reader:
            any_row = True
            missing = [c for c in SALES_REQUIRED_COLUMNS if not (raw.get(c) or "").strip()]
            if missing:
                result._bump(CAT_MISSING_FIELD)
                continue

            parsed_date = _parse_date(raw["date"])
            if parsed_date is None:
                result._bump(CAT_BAD_DATE)
                continue

            try:
                units_sold = int(float(raw["units_sold"]))
            except (ValueError, TypeError):
                result._bump(CAT_NON_NUMERIC_UNITS)
                continue

            if units_sold < 0:
                result._bump(CAT_NEGATIVE_UNITS)
                continue

            sku = raw["sku"].strip()
            warehouse = raw["warehouse"].strip()
            key = (parsed_date, sku, warehouse)
            if key in seen_keys:
                result.quarantined.append(QuarantinedRow(
                    category=CAT_DUPLICATE_SALES_ROW, raw=dict(raw),
                    detail=f"duplicate date+sku+warehouse {key!r} (kept first occurrence)",
                ))
                continue
            seen_keys.add(key)

            result.rows.append(SalesRow(
                date=parsed_date, sku=sku, warehouse=warehouse, units_sold=units_sold,
            ))
        if not any_row:
            result.file_empty = True
    return result
