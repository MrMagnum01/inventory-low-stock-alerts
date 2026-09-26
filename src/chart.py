"""
Renders the weekly units-sold trend as a PNG bar chart using matplotlib's
non-interactive Agg backend (no display needed -- safe for a headless
scheduled run).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def render_weekly_trend_chart(trend, out_path: str, title: str = "Weekly units sold"):
    labels = [t[0] for t in trend]
    values = [t[1] for t in trend]

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=120)
    ax.bar(labels, values, color="#3b6fa0")
    ax.set_title(title)
    ax.set_ylabel("Units sold")
    ax.set_xlabel("ISO week")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for label in ax.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
