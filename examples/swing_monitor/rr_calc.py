#!/usr/bin/env python3
"""
Swing Trade Risk:Reward Calculator

Calculates R:R ratios for single and multi-target (partial exit) swing trades.
Supports position sizing from a risk budget and computes blended P&L across
scaled exits.

Usage:
    pip install rich
    python rr_calc.py                          # interactive mode
    python rr_calc.py -e 1003 -s 960 -t 1100   # quick single-target
    python rr_calc.py -e 1003 -s 960 \\
        -t 1100:20 -t 1200:25 -t 1350:25 -t 1500:30  # multi-target partial exits
    python rr_calc.py -e 1003 -s 960 -t 1100:20 -t 1200:25 -t 1350:25 -t 1500:30 \\
        --capital 60000 --currency INR
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
except ImportError:
    sys.exit("Missing dependency: rich. Install with: pip install rich")

console = Console()


@dataclass
class Target:
    price: float
    pct_of_position: float  # percentage of total shares to sell here
    label: str = ""


@dataclass
class TradeSetup:
    entry: float
    stop: float
    targets: list[Target] = field(default_factory=list)
    capital: float | None = None
    currency: str = "INR"
    risk_budget_pct: float | None = None  # % of account to risk
    account_size: float | None = None

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def risk_pct(self) -> float:
        return (self.risk_per_share / self.entry) * 100

    @property
    def qty(self) -> float | None:
        if self.capital:
            raw = self.capital / self.entry
            return raw if raw < 1 else int(raw)
        return None

    @property
    def total_risk(self) -> float | None:
        if self.qty is not None and self.qty > 0:
            return self.risk_per_share * self.qty
        return None

    def position_size_from_risk(self) -> float | None:
        if self.risk_budget_pct and self.account_size:
            max_loss = self.account_size * (self.risk_budget_pct / 100)
            shares = int(max_loss / self.risk_per_share)
            return shares * self.entry
        return None


def rr_ratio(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk == 0:
        return float("inf")
    return reward / risk


def calc_single_target(setup: TradeSetup) -> None:
    if not setup.targets:
        return

    t = setup.targets[0]
    ratio = rr_ratio(setup.entry, setup.stop, t.price)
    sym = "$" if setup.currency == "USD" else "₹"

    console.rule("[bold]Single Target R:R[/bold]")

    tbl = Table(box=box.SIMPLE_HEAVY)
    tbl.add_column("Metric", style="bold")
    tbl.add_column("Value", justify="right")

    tbl.add_row("Entry", f"{sym}{setup.entry:,.2f}")
    tbl.add_row("Stop", f"{sym}{setup.stop:,.2f}")
    tbl.add_row("Target", f"{sym}{t.price:,.2f}")
    tbl.add_row("Risk/share", f"{sym}{setup.risk_per_share:,.2f} ({setup.risk_pct:.1f}%)")
    reward = t.price - setup.entry
    reward_pct = (reward / setup.entry) * 100
    tbl.add_row("Reward/share", f"{sym}{reward:,.2f} ({reward_pct:.1f}%)")
    tbl.add_row("[bold cyan]R:R[/bold cyan]", f"[bold cyan]1:{ratio:.1f}[/bold cyan]")

    if setup.qty:
        tbl.add_row("", "")
        tbl.add_row("Position size", f"{sym}{setup.capital:,.0f} ({setup.qty} shares)")
        tbl.add_row("Max loss", f"[red]{sym}{setup.total_risk:,.0f}[/red]")
        tbl.add_row("Max gain", f"[green]{sym}{reward * setup.qty:,.0f}[/green]")

    console.print(tbl)


def calc_multi_target(setup: TradeSetup) -> None:
    if len(setup.targets) < 2:
        calc_single_target(setup)
        return

    sym = "$" if setup.currency == "USD" else "₹"
    qty = setup.qty or 100  # default to 100 shares for ratio math
    has_capital = setup.qty is not None

    console.rule("[bold]Multi-Target Partial Exit R:R[/bold]")

    # Core metrics
    info = Table(box=box.SIMPLE, show_header=False)
    info.add_column("", style="bold", width=20)
    info.add_column("", justify="right")
    info.add_row("Entry", f"{sym}{setup.entry:,.2f}")
    info.add_row("Stop", f"{sym}{setup.stop:,.2f}")
    info.add_row("Risk/share", f"{sym}{setup.risk_per_share:,.2f} ({setup.risk_pct:.1f}%)")
    if has_capital:
        info.add_row("Capital", f"{sym}{setup.capital:,.0f}")
        info.add_row("Shares", f"{qty:.3f}" if qty < 1 else str(int(qty)))
        info.add_row("Max risk (full stop)", f"[red]{sym}{setup.total_risk:,.0f}[/red]")
    console.print(info)
    console.print()

    # Targets table
    tbl = Table(title="Scale-Out Plan", box=box.ROUNDED)
    tbl.add_column("Target", style="bold")
    tbl.add_column("Price", justify="right")
    tbl.add_column("% Sold", justify="right")
    if has_capital:
        tbl.add_column("Shares", justify="right")
    tbl.add_column("Reward/sh", justify="right")
    tbl.add_column("R:R", justify="right", style="cyan")
    if has_capital:
        tbl.add_column("₹ Realized", justify="right", style="green")
    tbl.add_column("Cumul. Realized", justify="right")
    tbl.add_column("Stop After", justify="right")

    total_pct_sold = 0.0
    cumul_profit = 0.0
    weighted_rr_sum = 0.0
    weighted_exit_sum = 0.0  # for avg exit price
    total_shares_sold = 0
    remaining_shares = qty

    exit_log = []  # collect per-tranche data for journal summary

    for i, t in enumerate(setup.targets):
        raw_shares = qty * t.pct_of_position / 100
        shares_this = raw_shares if qty < 1 else int(raw_shares)
        if i == len(setup.targets) - 1:
            shares_this = remaining_shares

        reward = t.price - setup.entry
        reward_pct = (reward / setup.entry) * 100
        ratio = rr_ratio(setup.entry, setup.stop, t.price)
        profit_this = reward * shares_this
        cumul_profit += profit_this
        total_pct_sold += t.pct_of_position
        weighted_rr_sum += ratio * t.pct_of_position
        weighted_exit_sum += t.price * shares_this
        total_shares_sold += shares_this

        exit_log.append({
            "label": t.label or f"T{i+1}",
            "price": t.price,
            "shares": shares_this,
            "pct": t.pct_of_position,
            "profit": profit_this,
            "r": ratio,
        })

        # Stop moves to break-even after T2 typically
        if i == 0:
            stop_after = f"{sym}{setup.stop:,.0f}"
        elif i == 1:
            stop_after = f"[green]{sym}{setup.entry:,.0f} (BE)[/green]"
        else:
            stop_after = f"[green]Trail 10EMA[/green]"

        label = t.label or f"T{i+1}"

        row = [
            label,
            f"{sym}{t.price:,.2f}",
            f"{t.pct_of_position:.0f}%",
        ]
        if has_capital:
            row.append(f"{shares_this:.3f}" if shares_this < 1 else str(int(shares_this)))
        row.extend([
            f"{sym}{reward:,.2f} (+{reward_pct:.1f}%)",
            f"1:{ratio:.1f}",
        ])
        if has_capital:
            row.append(f"{sym}{profit_this:,.0f}")
        row.extend([
            f"{sym}{cumul_profit:,.0f}" if has_capital else f"{cumul_profit / qty:.2f}/sh",
            stop_after,
        ])

        tbl.add_row(*row)
        remaining_shares -= shares_this

    console.print(tbl)

    # Computed averages
    weighted_avg_rr = weighted_rr_sum / total_pct_sold if total_pct_sold else 0
    avg_exit = weighted_exit_sum / total_shares_sold if total_shares_sold else 0
    avg_pnl_per_share = cumul_profit / total_shares_sold if total_shares_sold else 0
    total_r = cumul_profit / (setup.risk_per_share * qty) if (setup.risk_per_share and qty) else 0

    console.print()
    summary = Table(title="Summary", box=box.SIMPLE_HEAVY)
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")

    # Exit price analytics
    summary.add_row("Avg exit price (weighted)", f"[bold]{sym}{avg_exit:,.2f}[/bold]")
    avg_exit_pct = (avg_exit / setup.entry - 1) * 100
    summary.add_row("Avg exit vs entry", f"[bold green]+{avg_exit_pct:.1f}%[/bold green]")
    summary.add_row("P&L per share (avg)", f"[green]{sym}{avg_pnl_per_share:,.2f}[/green]")
    summary.add_row("", "")

    # R multiples
    summary.add_row("Weighted Avg R:R", f"[bold cyan]1:{weighted_avg_rr:.1f}[/bold cyan]")
    summary.add_row("Total R-multiple (full run)", f"[bold cyan]+{total_r:.1f}R[/bold cyan]")

    if has_capital:
        summary.add_row("", "")
        summary.add_row("Full-run P&L", f"[bold green]{sym}{cumul_profit:,.0f}[/bold green]")
        roi = (cumul_profit / setup.capital) * 100
        summary.add_row("Full-run ROI", f"[bold green]+{roi:.1f}%[/bold green]")
        summary.add_row("Max loss (stopped before T1)", f"[red]{sym}{setup.total_risk:,.0f}[/red]")

        # Payoff ratio: what you make on a full run vs what you lose on a full stop
        payoff = cumul_profit / setup.total_risk if setup.total_risk else 0
        summary.add_row("Payoff ratio (full win / full loss)", f"[bold]{payoff:.1f}x[/bold]")

        # Break-even win rate
        be_wr = 1 / (1 + payoff) * 100 if payoff else 0
        summary.add_row("Break-even win rate", f"{be_wr:.0f}%")

        # After T1 analysis
        t1 = setup.targets[0]
        t1_raw = qty * t1.pct_of_position / 100
        t1_shares = t1_raw if qty < 1 else int(t1_raw)
        t1_profit = (t1.price - setup.entry) * t1_shares
        summary.add_row("", "")
        summary.add_row("[dim]If only T1 hits + rest at BE[/dim]",
                        f"[dim]{sym}{t1_profit:,.0f}[/dim]")
        summary.add_row("[dim]T1 profit covers max loss?[/dim]",
                        f"[dim]{'✓ Yes' if t1_profit >= setup.total_risk else '✗ No'} "
                        f"({t1_profit/setup.total_risk*100:.0f}%)[/dim]")

    console.print(summary)

    # Journal-ready one-liner
    console.print()
    journal = Table(title="Journal Summary (copy to trade log)", box=box.DOUBLE)
    journal.add_column("Entry", justify="right")
    journal.add_column("Avg Exit", justify="right")
    journal.add_column("Stop", justify="right")
    journal.add_column("Shares", justify="right")
    journal.add_column("P&L", justify="right")
    journal.add_column("R", justify="right")
    journal.add_column("ROI", justify="right")
    journal.add_column("Exits", justify="left")

    exits_str = " / ".join(
        f"{e['label']}={sym}{e['price']:,.0f}×{e['shares']}"
        for e in exit_log
    )

    journal.add_row(
        f"{sym}{setup.entry:,.2f}",
        f"{sym}{avg_exit:,.2f}",
        f"{sym}{setup.stop:,.2f}",
        (f"{qty:.3f}" if qty < 1 else str(int(qty))) if has_capital else "—",
        f"{sym}{cumul_profit:,.0f}" if has_capital else f"{avg_pnl_per_share:,.2f}/sh",
        f"+{total_r:.1f}R",
        f"+{(cumul_profit / setup.capital * 100):.1f}%" if has_capital else "—",
        exits_str,
    )
    console.print(journal)


def interactive_mode() -> TradeSetup:
    console.rule("[bold]Swing Trade R:R Calculator[/bold]")
    console.print("[dim]Enter trade parameters. Press Ctrl+C to quit.[/dim]\n")

    currency = console.input("Currency [INR/USD] (default INR): ").strip().upper() or "INR"
    sym = "$" if currency == "USD" else "₹"

    entry = float(console.input(f"Entry price ({sym}): "))
    stop = float(console.input(f"Stop loss ({sym}): "))

    capital_str = console.input(f"Position capital ({sym}, or press Enter to skip): ").strip()
    capital = float(capital_str) if capital_str else None

    account_str = console.input("Account/sleeve size (for risk % calc, or Enter to skip): ").strip()
    account_size = float(account_str) if account_str else None

    console.print("\n[bold]Targets[/bold] — enter price and % of position to sell.")
    console.print("[dim]Format: price,pct (e.g. 1100,20). Empty line to finish.[/dim]\n")

    targets = []
    total_pct = 0
    i = 1
    while total_pct < 100:
        remaining = 100 - total_pct
        line = console.input(f"  T{i} (price,pct%) [{remaining}% remaining]: ").strip()
        if not line:
            if targets:
                # Assign remaining to last target
                targets[-1].pct_of_position += remaining
                console.print(f"  [dim]→ Assigned remaining {remaining}% to T{i-1}[/dim]")
            break
        parts = line.split(",")
        price = float(parts[0].strip())
        pct = float(parts[1].strip()) if len(parts) > 1 else remaining
        pct = min(pct, remaining)
        targets.append(Target(price=price, pct_of_position=pct, label=f"T{i}"))
        total_pct += pct
        i += 1

    return TradeSetup(
        entry=entry,
        stop=stop,
        targets=targets,
        capital=capital,
        currency=currency,
        account_size=account_size,
    )


def parse_target(s: str) -> Target:
    parts = s.split(":")
    price = float(parts[0])
    pct = float(parts[1]) if len(parts) > 1 else 100.0
    return Target(price=price, pct_of_position=pct)


def main():
    parser = argparse.ArgumentParser(
        description="Swing Trade Risk:Reward Calculator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -e 1003 -s 960 -t 1100
  %(prog)s -e 1003 -s 960 -t 1100:20 -t 1200:25 -t 1350:25 -t 1500:30 --capital 60000
  %(prog)s   (interactive mode)
        """,
    )
    parser.add_argument("-e", "--entry", type=float, help="Entry price")
    parser.add_argument("-s", "--stop", type=float, help="Stop loss price")
    parser.add_argument(
        "-t", "--target", action="append", dest="targets",
        help="Target as PRICE or PRICE:PCT (e.g. 1100:20 = sell 20%% at 1100)",
    )
    parser.add_argument("--capital", type=float, help="Position capital amount")
    parser.add_argument("--currency", default="INR", help="INR or USD (default: INR)")
    parser.add_argument("--account", type=float, help="Total account/sleeve size")

    args = parser.parse_args()

    if args.entry and args.stop and args.targets:
        targets = [parse_target(t) for t in args.targets]

        # If percentages not specified, distribute equally
        if all(t.pct_of_position == 100.0 for t in targets) and len(targets) > 1:
            pct_each = 100.0 / len(targets)
            for t in targets:
                t.pct_of_position = pct_each

        # If percentages don't sum to 100, assign remainder to last target
        total_pct = sum(t.pct_of_position for t in targets)
        if total_pct < 100:
            targets[-1].pct_of_position += 100 - total_pct

        setup = TradeSetup(
            entry=args.entry,
            stop=args.stop,
            targets=targets,
            capital=args.capital,
            currency=args.currency.upper(),
            account_size=args.account,
        )
    elif args.entry or args.stop or args.targets:
        parser.error("Provide all of --entry, --stop, and --target, or run with no args for interactive mode")
        return
    else:
        try:
            setup = interactive_mode()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled.[/dim]")
            return

    console.print()
    if len(setup.targets) > 1:
        calc_multi_target(setup)
    else:
        calc_single_target(setup)

    # Position sizing suggestion if account given
    if setup.account_size and setup.capital:
        console.print()
        risk_pct = (setup.total_risk / setup.account_size) * 100
        console.print(
            f"[dim]Risk as % of account: {risk_pct:.2f}% "
            f"({'✓ under 2%' if risk_pct < 2 else '⚠ above 2%'})[/dim]"
        )

    # Optimal position size from risk budget
    if setup.account_size and not setup.capital:
        for budget_pct in [0.5, 1.0, 1.5, 2.0]:
            max_loss = setup.account_size * budget_pct / 100
            shares = int(max_loss / setup.risk_per_share)
            cap = shares * setup.entry
            sym = "$" if setup.currency == "USD" else "₹"
            console.print(
                f"[dim]  {budget_pct}% risk budget → {shares} shares "
                f"({sym}{cap:,.0f})[/dim]"
            )

    console.print()


if __name__ == "__main__":
    main()
