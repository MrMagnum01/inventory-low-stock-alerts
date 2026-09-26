"""
Configuration for the low-stock alert tool. Every knob is overridable via
environment variable for cron/systemd use without editing this file.
"""
import os

INVENTORY_REQUIRED_COLUMNS = ["sku", "product_name", "warehouse", "on_hand", "lead_time_days"]
INVENTORY_OPTIONAL_COLUMNS = ["category"]

SALES_REQUIRED_COLUMNS = ["date", "sku", "warehouse", "units_sold"]

# How many trailing days of sales history to use for the average-daily-sales
# and safety-stock calculation.
LOOKBACK_DAYS = int(os.environ.get("INV_LOOKBACK_DAYS", "28"))

# How many trailing weeks the weekly trend chart shows.
TREND_WEEKS = int(os.environ.get("INV_TREND_WEEKS", "8"))

# Service-level z-score for the safety-stock formula
# (safety_stock = Z * stdev(daily_sales) * sqrt(lead_time_days)).
# 1.65 ~= 95% single-sided service level, the conventional default for a
# reorder-point calculation. See README "How the numbers are computed".
SERVICE_LEVEL_Z = float(os.environ.get("INV_SERVICE_LEVEL_Z", "1.65"))

# A daily sales value at or above this many stdevs above a SKU's own mean
# is flagged as an outlier day (kept in the average -- flagged, not
# dropped -- unless it's the *only* history a SKU has, see reorder.py).
SALES_OUTLIER_STDEV = float(os.environ.get("INV_SALES_OUTLIER_STDEV", "3.0"))


def get_state_dir():
    """Where per-day alert-dedup state lives. Overridable via INV_STATE_DIR."""
    default = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state"
    )
    return os.environ.get("INV_STATE_DIR", default)
