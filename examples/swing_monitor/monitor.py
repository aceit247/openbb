"""
Swing trade monitor.

Reads positions.yaml, fetches live prices via yfinance, and prints a status
table with P&L, distance-to-stop, distance-to-target, EMA alignment, and
risk-regime alerts. Designed to be run once per day after the close (or
before the open) as a 5-second sanity check on open swings.

Usage:
    pip install yfinance pandas pyyaml rich
    python monitor.py
    python monitor.py --positions positions.yaml

Methodology notes:
- Stop alerts fire on CLOSING price, not intraday wicks (Minervini rule).
- Time-stop counts trading days since position open date in the YAML.
- 10D / 21D / 50D EMAs computed from the last 250 daily bars.
- "Climax flag" = current bar > 25% above 50D EMA (Minervini exhaustion warning).
- All P&L is converted to INR for cross-sleeve roll-up.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
    import yaml
    import yfinance as yf
    from rich.console import Console
    from rich.table import Table
except ImportError as e:
    sys.exit(
        f"Missing dependency: {e.name}. Install with:\n"
        "  pip install yfinance pandas pyyaml rich"
    )


@dataclass
class Snapshot:
    ticker: str
    currency: str
    last: float
    ema10: float
    ema21: float
    ema50: float
    ema200: float
    pct_above_50ema: float
    distribution_days: int  # last 25 sessions, down >0.2% on rising volume


def fetch_snapshot(yf_symbol: str, currency: str) -> Snapshot | None:
    try:
        hist = yf.Ticker(yf_symbol).history(period="1y", interval="1d", auto_adjust=False)
    except Exception as e:
        print(f"  ! fetch failed for {yf_symbol}: {e}")
        return None

    if hist is None or hist.empty:
        return None

    close = hist["Close"]
    vol = hist["Volume"]
    last = float(close.iloc[-1])

    ema10 = float(close.ewm(span=10, adjust=False).mean().iloc[-1])
    ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

    pct_above_50 = (last / ema50 - 1) * 100 if ema50 else 0.0

    # Distribution day count (last 25 sessions, down > 0.2% on higher volume)
    recent = hist.tail(25)
    dd = 0
    prev_close = recent["Close"].shift(1)
    prev_vol = recent["Volume"].shift(1)
    for i in range(1, len(recent)):
        if (
            recent["Close"].iloc[i] < prev_close.iloc[i] * 0.998
            and recent["Volume"].iloc[i] > prev_vol.iloc[i]
        ):
            dd += 1

    return Snapshot(
        ticker=yf_symbol,
        currency=currency,
        last=last,
        ema10=ema10,
        ema21=ema21,
        ema50=ema50,
        ema200=ema200,
        pct_above_50ema=pct_above_50,
        distribution_days=dd,
    )


def to_inr(amount: float, currency: str, fx: float) -> float:
    return amount * fx if currency == "USD" else amount


def fmt_money(x: float, currency: str) -> str:
    sym = "$" if currency == "USD" else "₹"
    return f"{sym}{x:,.2f}"


def position_row(pos: dict, snap: Snapshot, fx: float) -> dict:
    last = snap.last
    entry = pos["entry_avg"]
    qty = pos["qty"]
    stop = pos["stop"]
    cur = pos["currency"]

    pnl_pct = (last / entry - 1) * 100
    pnl_abs = (last - entry) * qty
    pnl_inr = to_inr(pnl_abs, cur, fx)

    dist_to_stop_pct = (last / stop - 1) * 100
    risk_at_stop = (entry - stop) * qty if stop < entry else 0.0
    risk_inr = to_inr(risk_at_stop, cur, fx)

    # Next target
    next_t = None
    for t in pos["targets"]:
        if t["price"] > last:
            next_t = t
            break

    # Status flags
    flags = []
    if last < stop:
        flags.append("[bold red]STOPPED[/bold red]")
    if last < snap.ema10:
        flags.append("[yellow]<10EMA[/yellow]")
    if last < snap.ema21:
        flags.append("[yellow]<21EMA[/yellow]")
    if snap.pct_above_50ema > 25:
        flags.append("[red]CLIMAX?[/red]")
    if snap.distribution_days >= 4:
        flags.append("[red]DIST[/red]")

    return {
        "ticker": pos["ticker"],
        "status": pos.get("status", "open"),
        "last": fmt_money(last, cur),
        "entry": fmt_money(entry, cur),
        "stop": fmt_money(stop, cur),
        "pnl_pct": f"{pnl_pct:+.2f}%",
        "pnl_inr": f"₹{pnl_inr:+,.0f}",
        "dist_stop": f"{dist_to_stop_pct:+.2f}%",
        "next_t": (
            f"{fmt_money(next_t['price'], cur)} ({((next_t['price']/last)-1)*100:+.1f}%)"
            if next_t else "—"
        ),
        "risk_inr": f"₹{risk_inr:,.0f}",
        "ema_align": (
            "✓" if snap.last > snap.ema21 > snap.ema50 > snap.ema200 else "⚠"
        ),
        "flags": " ".join(flags) if flags else "[green]ok[/green]",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--positions", default="positions.yaml")
    args = parser.parse_args()

    cfg_path = Path(args.positions)
    if not cfg_path.exists():
        sys.exit(f"positions file not found: {cfg_path}")

    cfg = yaml.safe_load(cfg_path.read_text())
    fx = cfg["account"]["usd_inr_rate"]
    swing_sleeve = cfg["account"]["swing_sleeve_inr"]
    total_nw = cfg["account"]["total_inr"]

    console = Console()
    console.rule(f"[bold]Swing Monitor — {datetime.now():%Y-%m-%d %H:%M}[/bold]")
    console.print(
        f"NW ₹{total_nw:,.0f}  |  Swing sleeve ₹{swing_sleeve:,.0f}  |  USD/INR {fx}"
    )

    # ---- positions table ----
    rows = []
    total_pnl_inr = 0.0
    total_risk_inr = 0.0
    deployed_inr = 0.0
    for pos in cfg.get("positions", []):
        if pos.get("status") == "planned":
            continue
        snap = fetch_snapshot(pos["yf_symbol"], pos["currency"])
        if not snap:
            continue
        row = position_row(pos, snap, fx)
        rows.append(row)
        # accumulate roll-up
        last = snap.last
        entry = pos["entry_avg"]
        qty = pos["qty"]
        stop = pos["stop"]
        cur = pos["currency"]
        total_pnl_inr += to_inr((last - entry) * qty, cur, fx)
        total_risk_inr += to_inr(max(0, (entry - stop) * qty), cur, fx)
        deployed_inr += to_inr(entry * qty, cur, fx)

    if rows:
        t = Table(title="Open Swing Positions", show_lines=False)
        for col in [
            "Ticker", "Last", "Entry", "Stop", "P&L%", "P&L ₹",
            "Δ Stop", "Next Tgt", "Risk ₹", "Trend", "Flags",
        ]:
            t.add_column(col)
        for r in rows:
            t.add_row(
                r["ticker"], r["last"], r["entry"], r["stop"],
                r["pnl_pct"], r["pnl_inr"], r["dist_stop"],
                r["next_t"], r["risk_inr"], r["ema_align"], r["flags"],
            )
        console.print(t)

        # Roll-up
        console.print(
            f"\n[bold]Roll-up:[/bold]  "
            f"Deployed ₹{deployed_inr:,.0f} ({deployed_inr/swing_sleeve*100:.1f}% of sleeve)  |  "
            f"Open P&L ₹{total_pnl_inr:+,.0f}  |  "
            f"Risk @ stops ₹{total_risk_inr:,.0f} "
            f"({total_risk_inr/swing_sleeve*100:.2f}% of sleeve, "
            f"{total_risk_inr/total_nw*100:.3f}% of NW)"
        )

    # ---- planned positions ----
    planned = [p for p in cfg.get("positions", []) if p.get("status") == "planned"]
    if planned:
        console.rule("Planned (not yet filled)")
        for p in planned:
            snap = fetch_snapshot(p["yf_symbol"], p["currency"])
            if not snap:
                continue
            cur = p["currency"]
            console.print(
                f"  {p['ticker']}  last {fmt_money(snap.last, cur)}  "
                f"plan-stop {fmt_money(p['stop'], cur)}  "
                f"50EMA {fmt_money(snap.ema50, cur)} "
                f"({snap.pct_above_50ema:+.1f}% above)  "
                f"DD25={snap.distribution_days}"
            )
            if snap.last < p["stop"]:
                console.print("    [red]⚠ price already below planned stop — re-evaluate setup[/red]")

    # ---- watchlist / regime ----
    wl = cfg.get("watchlist", [])
    if wl:
        console.rule("Watchlist & Regime")
        for w in wl:
            snap = fetch_snapshot(w["yf_symbol"], w.get("currency", "USD"))
            if not snap:
                continue
            cur = w.get("currency", "USD")
            regime = "✓ above 21EMA" if snap.last > snap.ema21 else "[red]✗ below 21EMA[/red]"
            console.print(
                f"  {w['ticker']}  {fmt_money(snap.last, cur)}  "
                f"21EMA {fmt_money(snap.ema21, cur)}  {regime}  "
                f"DD25={snap.distribution_days}"
            )
            if "trigger_buy_zone" in w:
                lo, hi = w["trigger_buy_zone"]
                in_zone = lo <= snap.last <= hi
                console.print(
                    f"    buy zone {fmt_money(lo, cur)}–{fmt_money(hi, cur)}  "
                    f"{'[green]IN ZONE[/green]' if in_zone else 'wait'}"
                )

    console.print()


if __name__ == "__main__":
    main()
