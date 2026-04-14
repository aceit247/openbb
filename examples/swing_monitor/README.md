# Swing Trade Monitor

A small daily-status tool for managing swing trades following Minervini /
Darvas / Qullamaggie discipline. Reads positions from `positions.yaml`,
fetches live prices via `yfinance`, and prints a one-screen status table
with P&L, distance to stop, next target, EMA alignment, and risk-regime
flags (climax extension, distribution-day count).

## Install

```bash
pip install yfinance pandas pyyaml rich
```

## Run

```bash
cd examples/swing_monitor
python monitor.py
```

## What it shows

- **Open positions table**: last price, P&L %, P&L in INR, distance to stop,
  next target, risk-at-stop in INR, EMA stack health, and any active flags
  (`<10EMA`, `<21EMA`, `CLIMAX?`, `DIST`, `STOPPED`).
- **Roll-up**: total deployed, total open P&L, total risk-at-stops as a
  percentage of swing sleeve and total net worth.
- **Planned positions**: any entry with `status: planned` is shown with
  current price vs the intended stop so you can see if your setup is still
  valid before tomorrow's open.
- **Watchlist / regime**: market gauges (Nifty, QQQ) and pending setups,
  with buy-zone hit detection.

## Workflow

1. Edit `positions.yaml` whenever you enter, scale, or exit a trade. Always
   update `entry_avg` to the volume-weighted average and `qty` to current
   share count.
2. Run `python monitor.py` once per day, ideally after the relevant market
   close (US close ≈ 1:30 AM IST, NSE close ≈ 3:30 PM IST).
3. Act on flags:
   - `STOPPED` → exit at next open, no exceptions.
   - `<10EMA` → first warning, tighten mental stop.
   - `<21EMA` → momentum broken, consider partial exit.
   - `CLIMAX?` → stock >25% above 50EMA, reduce size on next strength.
   - `DIST ≥ 4` → distribution-day cluster, market regime weakening.

## Methodology references

- Mark Minervini, *Trade Like a Stock Market Wizard* — trend template,
  pivot point analysis, distribution-day counting.
- Nicolas Darvas, *How I Made $2,000,000 in the Stock Market* — box
  theory, buying boxes at new highs.
- Kristjan "Qullamaggie" Kullamägi — episodic pivots, big prior moves
  followed by tight consolidations, 10/20 EMA trailing exits.
