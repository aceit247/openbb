#!/usr/bin/env python3
"""
Live swing trade dashboard.

Auto-refreshes prices every 60 seconds and displays positions, P&L,
watchlist, and portfolio roll-up in a persistent terminal UI.

Usage:
    pip install yfinance pandas pyyaml rich
    python dashboard.py
    python dashboard.py --refresh 30        # refresh every 30 seconds
    python dashboard.py --positions positions.yaml
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
    import yaml
    import yfinance as yf
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
except ImportError as e:
    sys.exit(
        f"Missing dependency: {e.name}. Install with:\n"
        "  pip install yfinance pandas pyyaml rich"
    )


def fetch_price(symbol: str) -> dict | None:
    try:
        hist = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        close = hist["Close"]
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else last
        ema10 = float(close.ewm(span=10, adjust=False).mean().iloc[-1])
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        return {"last": last, "prev": prev, "ema10": ema10, "ema21": ema21}
    except Exception:
        return None


def fmt(x: float, cur: str) -> str:
    sym = "$" if cur == "USD" else "₹"
    return f"{sym}{x:,.2f}"


def pct_str(val: float) -> str:
    color = "green" if val >= 0 else "red"
    return f"[{color}]{val:+.2f}%[/{color}]"


def money_str(val: float, cur: str = "INR") -> str:
    sym = "₹" if cur == "INR" else "$"
    color = "green" if val >= 0 else "red"
    return f"[{color}]{sym}{val:+,.0f}[/{color}]"


def build_positions_table(cfg: dict, prices: dict, fx: float) -> Table:
    t = Table(
        title="[bold]Active Swing Positions[/bold]",
        box=box.ROUNDED,
        title_style="bold white",
        expand=True,
    )
    t.add_column("Ticker", style="bold cyan", no_wrap=True)
    t.add_column("Last", justify="right")
    t.add_column("Entry", justify="right")
    t.add_column("P&L %", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Stop", justify="right")
    t.add_column("Dist Stop", justify="right")
    t.add_column("Next Tgt", justify="right")
    t.add_column("Trend", justify="center")
    t.add_column("Flags", justify="left")

    positions = cfg.get("positions", [])
    if not positions:
        t.add_row("[dim]No active positions[/dim]", *[""] * 9)
        return t

    for pos in positions:
        if pos.get("status") != "open":
            continue
        sym = pos["yf_symbol"]
        cur = pos["currency"]
        p = prices.get(sym)
        if not p:
            t.add_row(pos["ticker"], "[red]fetch err[/red]", *[""] * 8)
            continue

        last = p["last"]
        entry = pos["entry_avg"]
        qty = pos["qty"]
        stop = pos["stop"]

        pnl_pct = (last / entry - 1) * 100
        pnl_abs = (last - entry) * qty
        pnl_inr = pnl_abs * fx if cur == "USD" else pnl_abs

        dist_stop = (last / stop - 1) * 100

        next_tgt = None
        for tgt in pos.get("targets", []):
            if tgt["price"] > last:
                next_tgt = tgt
                break

        flags = []
        if last < stop:
            flags.append("[bold red]STOPPED[/bold red]")
        if last < p["ema10"]:
            flags.append("[yellow]<10EMA[/yellow]")
        if last < p["ema21"]:
            flags.append("[yellow]<21EMA[/yellow]")

        trend = "[green]ok[/green]" if last > p["ema21"] else "[red]weak[/red]"

        tgt_str = "—"
        if next_tgt:
            dist = (next_tgt["price"] / last - 1) * 100
            tgt_str = f"{fmt(next_tgt['price'], cur)} ({dist:+.1f}%)"

        t.add_row(
            pos["ticker"],
            fmt(last, cur),
            fmt(entry, cur),
            pct_str(pnl_pct),
            money_str(pnl_inr),
            fmt(stop, cur),
            pct_str(dist_stop),
            tgt_str,
            trend,
            " ".join(flags) if flags else "[green]ok[/green]",
        )

    return t


def build_rollup_panel(cfg: dict, prices: dict, fx: float) -> Panel:
    positions = cfg.get("positions", [])
    total_pnl = 0.0
    total_risk = 0.0
    deployed = 0.0

    for pos in positions:
        if pos.get("status") != "open":
            continue
        p = prices.get(pos["yf_symbol"])
        if not p:
            continue
        last = p["last"]
        entry = pos["entry_avg"]
        qty = pos["qty"]
        stop = pos["stop"]
        cur = pos["currency"]
        m = fx if cur == "USD" else 1.0

        total_pnl += (last - entry) * qty * m
        total_risk += max(0, (entry - stop) * qty) * m
        deployed += entry * qty * m

    sleeve = cfg.get("swing_account", {}).get("sleeve_target_inr", 275000)
    nw = cfg.get("networth", {}).get("total_inr", 2750000)

    lines = [
        f"Deployed: [bold]₹{deployed:,.0f}[/bold] ({deployed/sleeve*100:.1f}% of sleeve)",
        f"Open P&L: {money_str(total_pnl)}",
        f"Risk @ stops: [bold]₹{total_risk:,.0f}[/bold] ({total_risk/sleeve*100:.2f}% sleeve, {total_risk/nw*100:.3f}% NW)",
    ]
    return Panel("\n".join(lines), title="[bold]Roll-up[/bold]", box=box.ROUNDED)


def build_closed_panel(cfg: dict) -> Panel:
    closed = cfg.get("closed", [])
    wins = sum(1 for c in closed if c.get("result") == "profit")
    losses = sum(1 for c in closed if c.get("result") == "loss")
    total = wins + losses
    wr = (wins / total * 100) if total else 0

    total_pnl = 0.0
    for c in closed:
        s = c.get("summary", {})
        total_pnl += s.get("pnl_inr", 0)
        if "pnl_usd" in s:
            total_pnl += s["pnl_usd"] * 95

    lines = [
        f"Record: [bold]{wins}W / {losses}L[/bold] ({wr:.0f}% win rate)",
        f"Realized P&L: {money_str(total_pnl)}",
    ]
    if closed:
        best = max(closed, key=lambda c: c.get("summary", {}).get("pnl_pct", 0))
        worst = min(closed, key=lambda c: c.get("summary", {}).get("pnl_pct", 0))
        lines.append(
            f"Best: {best['ticker']} {best['summary']['pnl_pct']:+.1f}%  |  "
            f"Worst: {worst['ticker']} {worst['summary']['pnl_pct']:+.1f}%"
        )
    return Panel("\n".join(lines), title="[bold]Scorecard[/bold]", box=box.ROUNDED)


def build_watchlist_table(cfg: dict, prices: dict) -> Table:
    t = Table(
        title="[bold]Watchlist[/bold]",
        box=box.ROUNDED,
        expand=True,
    )
    t.add_column("Ticker", style="bold", no_wrap=True)
    t.add_column("Trigger", justify="right")
    t.add_column("Note", ratio=2)

    for w in cfg.get("watchlist", []):
        t.add_row(
            w.get("ticker", ""),
            w.get("trigger", "—"),
            w.get("note", ""),
        )
    return t


def build_dashboard(cfg: dict, prices: dict, fx: float, refresh: int, err: str) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nw = cfg.get("networth", {}).get("total_inr", 0)
    sleeve = cfg.get("swing_account", {}).get("sleeve_target_inr", 0)
    header_text = (
        f"[bold white] SWING DASHBOARD [/bold white]  |  "
        f"NW ₹{nw:,.0f}  |  Sleeve ₹{sleeve:,.0f}  |  "
        f"USD/INR {fx}  |  {now}"
    )
    layout["header"].update(Panel(header_text, box=box.HEAVY))

    layout["body"].split_column(
        Layout(name="positions", ratio=3),
        Layout(name="bottom", ratio=2),
    )
    layout["body"]["positions"].update(build_positions_table(cfg, prices, fx))

    layout["body"]["bottom"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )

    left_layout = Layout()
    left_layout.split_column(
        Layout(build_rollup_panel(cfg, prices, fx), name="rollup"),
        Layout(build_closed_panel(cfg), name="scorecard"),
    )
    layout["body"]["bottom"]["left"].update(left_layout)
    layout["body"]["bottom"]["right"].update(build_watchlist_table(cfg, prices))

    status = f"[dim]Refreshing every {refresh}s  |  Press Ctrl+C to quit[/dim]"
    if err:
        status += f"  |  [red]{err}[/red]"
    layout["footer"].update(Panel(status, box=box.SIMPLE))

    return layout


def main():
    parser = argparse.ArgumentParser(description="Live swing trade dashboard")
    parser.add_argument("--positions", default="positions.yaml")
    parser.add_argument("--refresh", type=int, default=60, help="Refresh interval in seconds")
    args = parser.parse_args()

    cfg_path = Path(args.positions)
    if not cfg_path.exists():
        sys.exit(f"positions file not found: {cfg_path}")

    console = Console()

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while True:
            err = ""
            try:
                cfg = yaml.safe_load(cfg_path.read_text())
            except Exception as e:
                err = f"YAML parse error: {e}"
                cfg = {}

            fx = cfg.get("networth", {}).get("usd_inr_rate", None)
            if fx is None:
                fx = cfg.get("account", {}).get("usd_inr_rate", 95.0)

            symbols = set()
            for pos in cfg.get("positions", []):
                if pos.get("status") == "open":
                    symbols.add(pos["yf_symbol"])

            prices = {}
            for sym in symbols:
                p = fetch_price(sym)
                if p:
                    prices[sym] = p

            layout = build_dashboard(cfg, prices, fx, args.refresh, err)
            live.update(layout)

            for _ in range(args.refresh):
                time.sleep(1)


if __name__ == "__main__":
    main()
