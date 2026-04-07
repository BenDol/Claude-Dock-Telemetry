#!/usr/bin/env python3
"""
Generate a telemetry dashboard for Claude Dock.

Reads JSON telemetry data from data/{YYYY}/{MM}/{YYYY}-{MM}-{DD}.json,
produces charts as PNGs in charts/, and writes a dashboard README.md.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CHARTS_DIR = REPO_ROOT / "charts"
README_PATH = REPO_ROOT / "README.md"
LOOKBACK_DAYS = 60  # ~2 months

# Chart style
plt.rcParams.update({
    "figure.facecolor": "#0d1117",
    "axes.facecolor": "#161b22",
    "axes.edgecolor": "#30363d",
    "axes.labelcolor": "#c9d1d9",
    "text.color": "#c9d1d9",
    "xtick.color": "#8b949e",
    "ytick.color": "#8b949e",
    "grid.color": "#21262d",
    "grid.linestyle": "--",
    "grid.alpha": 0.6,
    "font.size": 11,
    "figure.dpi": 150,
})

ACCENT = "#58a6ff"
ACCENT2 = "#3fb950"
ACCENT3 = "#d29922"
ACCENT4 = "#f85149"
ACCENT5 = "#bc8cff"
PALETTE = [ACCENT, ACCENT2, ACCENT3, ACCENT4, ACCENT5, "#79c0ff", "#56d364"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_telemetry(lookback_days: int) -> list[dict]:
    """Load all telemetry payloads within the lookback window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    payloads: list[dict] = []

    if not DATA_DIR.exists():
        return payloads

    for year_dir in sorted(DATA_DIR.iterdir()):
        if not year_dir.is_dir():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for json_file in sorted(month_dir.glob("*.json")):
                # Quick date check from filename
                try:
                    file_date = datetime.strptime(json_file.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if file_date < cutoff - timedelta(days=1):
                    continue
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        payloads.extend(data)
                except (json.JSONDecodeError, OSError):
                    continue

    # Filter by actual timestamp
    filtered = []
    for p in payloads:
        try:
            ts = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            if ts >= cutoff:
                p["_ts"] = ts
                p["_date"] = ts.date()
                filtered.append(p)
        except (KeyError, ValueError):
            continue

    return sorted(filtered, key=lambda p: p["_ts"])


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------
def group_by_date(payloads: list[dict]) -> dict:
    """Group payloads by date."""
    by_date = defaultdict(list)
    for p in payloads:
        by_date[p["_date"]].append(p)
    return dict(sorted(by_date.items()))


def fill_date_gaps(date_map: dict, default=0) -> tuple[list, list]:
    """Fill gaps in date series with a default value."""
    if not date_map:
        return [], []
    dates = sorted(date_map.keys())
    start, end = dates[0], dates[-1]
    current = start
    filled_dates = []
    filled_values = []
    while current <= end:
        filled_dates.append(current)
        filled_values.append(date_map.get(current, default))
        current += timedelta(days=1)
    return filled_dates, filled_values


def group_by_week(date_map: dict) -> tuple[list, list]:
    """Aggregate daily values into ISO weeks (summed)."""
    weekly = defaultdict(float)
    for d, v in date_map.items():
        # Use Monday of that week as the key
        monday = d - timedelta(days=d.weekday())
        weekly[monday] += v
    weeks = sorted(weekly.keys())
    return weeks, [weekly[w] for w in weeks]


# ---------------------------------------------------------------------------
# Chart generators
# ---------------------------------------------------------------------------
def save_chart(fig, name: str):
    path = CHARTS_DIR / f"{name}.png"
    fig.savefig(path, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    return path


def chart_daily_active_users(by_date: dict) -> str:
    dau = {d: len(set(p["deviceId"] for p in ps)) for d, ps in by_date.items()}
    dates, values = fill_date_gaps(dau)
    if not dates:
        return ""

    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.fill_between(dates, values, alpha=0.15, color=ACCENT)
    ax.plot(dates, values, color=ACCENT, linewidth=2, marker="o", markersize=3)
    ax.set_title("Daily Active Users (unique devices)", fontweight="bold", pad=12)
    ax.set_ylabel("Users")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.grid(True)
    fig.autofmt_xdate()
    save_chart(fig, "daily_active_users")
    return "daily_active_users.png"


def chart_daily_sessions(by_date: dict) -> str:
    sessions = {d: len(ps) for d, ps in by_date.items()}
    dates, values = fill_date_gaps(sessions)
    if not dates:
        return ""

    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.bar(dates, values, color=ACCENT2, alpha=0.8, width=0.8)
    ax.set_title("Daily Sessions", fontweight="bold", pad=12)
    ax.set_ylabel("Sessions")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.grid(True, axis="y")
    fig.autofmt_xdate()
    save_chart(fig, "daily_sessions")
    return "daily_sessions.png"


def chart_session_duration(by_date: dict) -> str:
    avg_dur = {}
    for d, ps in by_date.items():
        durations = [p.get("sessionDurationMs", 0) for p in ps]
        # Filter out very short sessions (<5s) as noise
        meaningful = [ms for ms in durations if ms >= 5000]
        if meaningful:
            avg_dur[d] = sum(meaningful) / len(meaningful) / 60000  # to minutes

    dates, values = fill_date_gaps(avg_dur, default=0)
    if not dates:
        return ""

    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.fill_between(dates, values, alpha=0.15, color=ACCENT3)
    ax.plot(dates, values, color=ACCENT3, linewidth=2, marker="o", markersize=3)
    ax.set_title("Average Session Duration (minutes)", fontweight="bold", pad=12)
    ax.set_ylabel("Minutes")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.grid(True)
    fig.autofmt_xdate()
    save_chart(fig, "session_duration")
    return "session_duration.png"


def chart_terminal_usage(by_date: dict) -> str:
    avg_terms = {}
    for d, ps in by_date.items():
        counts = [p.get("terminalCount", 0) for p in ps]
        avg_terms[d] = sum(counts) / len(counts) if counts else 0

    dates, values = fill_date_gaps(avg_terms, default=0)
    if not dates:
        return ""

    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.bar(dates, values, color=ACCENT5, alpha=0.8, width=0.8)
    ax.set_title("Average Terminals per Session", fontweight="bold", pad=12)
    ax.set_ylabel("Terminals")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.grid(True, axis="y")
    fig.autofmt_xdate()
    save_chart(fig, "terminal_usage")
    return "terminal_usage.png"


def chart_feature_adoption(payloads: list[dict]) -> str:
    features = {
        "Linked Mode": 0,
        "Git Manager": 0,
        "CI Tab": 0,
        "PR Tab": 0,
        "Plugins (1+)": 0,
    }
    total = len(payloads)
    if total == 0:
        return ""

    for p in payloads:
        f = p.get("features", {})
        if f.get("linkedModeEnabled"):
            features["Linked Mode"] += 1
        if f.get("gitManagerOpened"):
            features["Git Manager"] += 1
        if f.get("ciTabUsed"):
            features["CI Tab"] += 1
        if f.get("prTabUsed"):
            features["PR Tab"] += 1
        if f.get("pluginCount", 0) > 0:
            features["Plugins (1+)"] += 1

    labels = list(features.keys())
    pcts = [features[k] / total * 100 for k in labels]

    fig, ax = plt.subplots(figsize=(8, 3.5))
    bars = ax.barh(labels, pcts, color=PALETTE[:len(labels)], alpha=0.85)
    ax.set_title("Feature Adoption (% of sessions)", fontweight="bold", pad=12)
    ax.set_xlabel("% of Sessions")
    ax.set_xlim(0, max(max(pcts) * 1.2, 10))
    for bar, pct in zip(bars, pcts):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                f"{pct:.1f}%", va="center", fontsize=10, color="#c9d1d9")
    ax.grid(True, axis="x")
    ax.invert_yaxis()
    save_chart(fig, "feature_adoption")
    return "feature_adoption.png"


def chart_os_distribution(payloads: list[dict]) -> str:
    os_map = defaultdict(int)
    for p in payloads:
        platform = p.get("os", {}).get("platform", "unknown")
        label = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(platform, platform)
        os_map[label] += 1

    if not os_map:
        return ""

    labels = list(os_map.keys())
    sizes = list(os_map.values())
    colors = PALETTE[:len(labels)]

    fig, ax = plt.subplots(figsize=(5, 3.5))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, autopct="%1.1f%%", colors=colors,
        textprops={"color": "#c9d1d9"}, startangle=90,
        pctdistance=0.6,
    )
    for t in autotexts:
        t.set_fontsize(10)
        t.set_fontweight("bold")
    ax.set_title("OS Distribution", fontweight="bold", pad=12)
    save_chart(fig, "os_distribution")
    return "os_distribution.png"


def chart_weekly_trends(by_date: dict) -> str:
    """Weekly sessions + unique users as dual-axis line chart."""
    sessions_daily = {d: len(ps) for d, ps in by_date.items()}
    users_daily = {d: len(set(p["deviceId"] for p in ps)) for d, ps in by_date.items()}

    w_dates_s, w_vals_s = group_by_week(sessions_daily)
    w_dates_u, w_vals_u = group_by_week(users_daily)

    if not w_dates_s:
        return ""

    fig, ax1 = plt.subplots(figsize=(10, 3.5))
    ax1.bar(w_dates_s, w_vals_s, width=5, color=ACCENT2, alpha=0.6, label="Sessions")
    ax1.set_ylabel("Sessions", color=ACCENT2)
    ax1.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax1.tick_params(axis="y", labelcolor=ACCENT2)

    ax2 = ax1.twinx()
    ax2.plot(w_dates_u, w_vals_u, color=ACCENT, linewidth=2.5, marker="o", markersize=5, label="Unique Users")
    ax2.set_ylabel("Unique Users", color=ACCENT)
    ax2.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax2.tick_params(axis="y", labelcolor=ACCENT)

    ax1.set_title("Weekly Trends", fontweight="bold", pad=12)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax1.grid(True, axis="y")
    fig.autofmt_xdate()

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left",
               facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")

    save_chart(fig, "weekly_trends")
    return "weekly_trends.png"


def chart_crash_rate(by_date: dict) -> str:
    crash_pct = {}
    for d, ps in by_date.items():
        crashed = sum(1 for p in ps if p.get("crashCount", 0) > 0)
        crash_pct[d] = crashed / len(ps) * 100 if ps else 0

    dates, values = fill_date_gaps(crash_pct, default=0)
    if not dates:
        return ""

    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.fill_between(dates, values, alpha=0.2, color=ACCENT4)
    ax.plot(dates, values, color=ACCENT4, linewidth=2, marker="o", markersize=3)
    ax.set_title("Crash Rate (% of sessions with crashes)", fontweight="bold", pad=12)
    ax.set_ylabel("% Sessions")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.grid(True)
    fig.autofmt_xdate()
    save_chart(fig, "crash_rate")
    return "crash_rate.png"


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------
def compute_summary(payloads: list[dict], by_date: dict) -> dict:
    if not payloads:
        return {}

    unique_devices = set(p["deviceId"] for p in payloads)
    total_sessions = len(payloads)
    durations = [p.get("sessionDurationMs", 0) for p in payloads if p.get("sessionDurationMs", 0) >= 5000]
    avg_duration_min = (sum(durations) / len(durations) / 60000) if durations else 0
    total_terminals = sum(p.get("terminalCount", 0) for p in payloads)
    crash_sessions = sum(1 for p in payloads if p.get("crashCount", 0) > 0)

    # Date range
    dates = sorted(by_date.keys())
    date_range_str = f"{dates[0].strftime('%b %d, %Y')} - {dates[-1].strftime('%b %d, %Y')}" if dates else "N/A"

    # Average DAU
    dau_values = [len(set(p["deviceId"] for p in ps)) for ps in by_date.values()]
    avg_dau = sum(dau_values) / len(dau_values) if dau_values else 0

    # Peak DAU
    peak_dau = max(dau_values) if dau_values else 0

    # Platforms
    platforms = defaultdict(int)
    for p in payloads:
        plat = p.get("os", {}).get("platform", "unknown")
        platforms[plat] += 1

    return {
        "date_range": date_range_str,
        "total_sessions": total_sessions,
        "unique_devices": len(unique_devices),
        "avg_dau": avg_dau,
        "peak_dau": peak_dau,
        "avg_duration_min": avg_duration_min,
        "total_terminals": total_terminals,
        "crash_sessions": crash_sessions,
        "crash_rate_pct": crash_sessions / total_sessions * 100 if total_sessions else 0,
        "platforms": dict(platforms),
        "days_tracked": len(dates),
    }


# ---------------------------------------------------------------------------
# README generation
# ---------------------------------------------------------------------------
def generate_readme(summary: dict, charts: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    platform_str = ", ".join(
        f"{'Windows' if k == 'win32' else 'macOS' if k == 'darwin' else 'Linux' if k == 'linux' else k}: {v}"
        for k, v in sorted(summary.get("platforms", {}).items(), key=lambda x: -x[1])
    )

    md = f"""# Claude Dock Telemetry Dashboard

> Auto-generated on **{now}** | Data: last {LOOKBACK_DAYS} days ({summary.get('date_range', 'N/A')})

## Key Metrics

| Metric | Value |
|--------|-------|
| Total Sessions | **{summary.get('total_sessions', 0):,}** |
| Unique Devices | **{summary.get('unique_devices', 0):,}** |
| Avg Daily Active Users | **{summary.get('avg_dau', 0):.1f}** |
| Peak Daily Active Users | **{summary.get('peak_dau', 0):,}** |
| Avg Session Duration | **{summary.get('avg_duration_min', 0):.1f} min** |
| Total Terminals Spawned | **{summary.get('total_terminals', 0):,}** |
| Sessions with Crashes | **{summary.get('crash_sessions', 0):,}** ({summary.get('crash_rate_pct', 0):.1f}%) |
| Days Tracked | **{summary.get('days_tracked', 0)}** |
| Platforms | {platform_str} |

---

## Weekly Trends

![Weekly Trends](charts/weekly_trends.png)

## Daily Active Users

![Daily Active Users](charts/daily_active_users.png)

## Daily Sessions

![Daily Sessions](charts/daily_sessions.png)

## Session Duration

![Session Duration](charts/session_duration.png)

## Terminal Usage

![Terminal Usage](charts/terminal_usage.png)

## Feature Adoption

![Feature Adoption](charts/feature_adoption.png)

## OS Distribution

![OS Distribution](charts/os_distribution.png)

## Crash Rate

![Crash Rate](charts/crash_rate.png)

---

<sub>This dashboard is updated automatically every week by a GitHub Action.
Only anonymous, aggregated telemetry is displayed. No personal data is collected or shown.
See the [Claude Dock privacy policy](https://github.com/BenDol/claude-dock) for details.</sub>
"""
    return md


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"Loading telemetry data (last {LOOKBACK_DAYS} days)...")
    payloads = load_telemetry(LOOKBACK_DAYS)
    print(f"  Loaded {len(payloads)} payloads")

    if not payloads:
        print("No telemetry data found. Writing minimal README.")
        README_PATH.write_text(
            "# Claude Dock Telemetry Dashboard\n\nNo telemetry data available yet.\n",
            encoding="utf-8",
        )
        return

    by_date = group_by_date(payloads)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    print("Generating charts...")
    charts = []
    for gen in [
        chart_weekly_trends,
        chart_daily_active_users,
        chart_daily_sessions,
        chart_session_duration,
        chart_terminal_usage,
        chart_crash_rate,
    ]:
        name = gen(by_date)
        if name:
            charts.append(name)
            print(f"  Created {name}")

    for gen in [chart_feature_adoption, chart_os_distribution]:
        name = gen(payloads)
        if name:
            charts.append(name)
            print(f"  Created {name}")

    print("Computing summary...")
    summary = compute_summary(payloads, by_date)

    print("Writing README.md...")
    readme = generate_readme(summary, charts)
    README_PATH.write_text(readme, encoding="utf-8")

    print("Done!")


if __name__ == "__main__":
    main()
