#!/usr/bin/env python3
"""
Watchlist setup scanner.

Reads the structured watchlist in positions.yaml, fetches current prices via
yfinance, and reports which setups are IN ZONE, approaching, extended past
entry, or invalidated — plus regime gauges (price vs 21D EMA). Mirrors the
TradingView watchlist so setups can be checked headlessly twice a day.

Usage:
    python watchlist_scan.py                 # scan everything
    python watchlist_scan.py --market IN     # Indian tickers only (morning run)
    python watchlist_scan.py --market US     # US tickers only (evening run)
    python watchlist_scan.py --out report.md # also write a markdown report
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
    import yfinance as yf
except ImportError as e:
    sys.exit(f"Missing dependency: {e.name}. pip install pyyaml yfinance")

HERE = Path(__file__).parent


def fetch(symbol: str):
    """Return (last, ema21, dist_21_pct) or None."""
    try:
        h = yf.Ticker(symbol).history(period="6mo", interval="1d")
        if h is None or h.empty:
            return None
        close = h["Close"]
        last = float(close.iloc[-1])
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        return last, ema21, (last / ema21 - 1) * 100
    except Exception:
        return None


def classify(last: float, zone, invalidate) -> str:
    lo, hi = zone
    if invalidate and last < invalidate:
        return "INVALIDATED — below stop level, remove/re-analyze"
    if lo <= last <= hi:
        return ">>> IN ENTRY ZONE <<<"
    if last < lo:
        gap = (lo / last - 1) * 100
        return f"below zone ({gap:+.1f}% to zone floor)"
    gap = (last / hi - 1) * 100
    return f"extended {gap:+.1f}% past zone — do not chase"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["IN", "US", "ALL"], default="ALL")
    ap.add_argument("--positions", default=str(HERE / "positions.yaml"))
    ap.add_argument("--out", default=None, help="write markdown report to this path")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.positions).read_text())
    wl = cfg.get("watchlist", [])
    if args.market != "ALL":
        wl = [w for w in wl if w.get("market", "ALL") == args.market]

    lines = [f"# Watchlist scan — {args.market} — {datetime.now():%Y-%m-%d %H:%M}", ""]
    for w in wl:
        sym = w.get("yf") or w["ticker"]
        data = fetch(sym)
        if data is None:
            lines.append(f"- **{w['ticker']}**: fetch failed ({sym})")
            continue
        last, ema21, dist = data
        regime_str = f"21D EMA {ema21:,.2f} ({dist:+.1f}%)"
        if w.get("regime"):
            state = "RISK-ON (above 21D)" if last > ema21 else "RISK-OFF (below 21D) — no new longs"
            lines.append(f"- **{w['ticker']}** [regime]: {last:,.2f} | {regime_str} → **{state}**")
            continue
        status = classify(last, w.get("zone", [0, 0]), w.get("invalidate"))
        lines.append(f"- **{w['ticker']}**: {last:,.2f} | {regime_str} → **{status}**")
        lines.append(f"    - plan: {w.get('note', '')}")
    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
