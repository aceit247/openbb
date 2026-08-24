#!/usr/bin/env python3
"""
Watchlist manager — add / update / remove / list entries in the
`watchlist:` section of positions.yaml.

Deliberately does NOT do a full YAML load-and-dump round trip. This file
is hand-curated with heavy comments, historical notes, and inconsistent
spacing that a generic YAML dumper would flatten or reorder. Instead this
tool finds the watchlist block as raw text and only ever touches lines
inside it — every other line in the file, and every comment, is left
byte-for-byte untouched.

Usage:
    python watchlist_cli.py list

    python watchlist_cli.py add TICKER --yf TICKER.NS --market IN \
        --zone 100,110 --invalidate 95 --trigger "100-110 reclaim" \
        --stop 95 --note "some note"

    python watchlist_cli.py update TICKER --stop 90        # only changes --stop, keeps the rest
    python watchlist_cli.py update TICKER --note "revised" # only changes --note, keeps the rest

    python watchlist_cli.py remove TICKER

After any change, review the diff (git diff) and commit/push yourself —
this script only edits the local file, it does not touch git.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

POSITIONS_FILE = Path(__file__).parent / "positions.yaml"
WATCHLIST_KEY = "watchlist:"


def load_lines() -> list[str]:
    if not POSITIONS_FILE.exists():
        sys.exit(f"Not found: {POSITIONS_FILE}")
    return POSITIONS_FILE.read_text().splitlines(keepends=True)


def save_lines(lines: list[str]) -> None:
    POSITIONS_FILE.write_text("".join(lines))


def find_watchlist_block(lines: list[str]) -> tuple[int, int]:
    """Return (start, end) line indices: lines[start] is 'watchlist:',
    lines[start+1:end] are its entries. end is exclusive."""
    start = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == WATCHLIST_KEY:
            start = i
            break
    if start is None:
        sys.exit(f"Could not find a top-level '{WATCHLIST_KEY}' line in {POSITIONS_FILE}")

    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line.strip() == "":
            continue
        # A line with no leading whitespace is a new top-level key -> block ends here.
        if not line[0].isspace():
            end = j
            break
    return start, end


def parse_ticker(line: str) -> str | None:
    m = re.search(r"ticker:\s*([^\s,}]+)", line)
    return m.group(1) if m else None


def existing_field(line: str, key: str) -> str | None:
    """Best-effort extraction of a field's raw value (quoted or not) from
    an existing flow-mapping watchlist line, for partial updates."""
    m = re.search(rf'{re.escape(key)}:\s*"([^"]*)"', line)
    if m:
        return m.group(1)
    m = re.search(rf"{re.escape(key)}:\s*\[([^\]]*)\]", line)
    if m:
        return m.group(1)
    m = re.search(rf"{re.escape(key)}:\s*([^,}}]+)", line)
    if m:
        return m.group(1).strip()
    return None


def format_entry(a: argparse.Namespace) -> str:
    fields = [f"ticker: {a.ticker}"]
    if a.yf:
        fields.append(f'yf: "{a.yf}"')
    if a.market:
        fields.append(f"market: {a.market}")
    if a.zone:
        lo, hi = (x.strip() for x in a.zone.split(","))
        fields.append(f"zone: [{lo}, {hi}]")
    if a.invalidate is not None:
        fields.append(f"invalidate: {a.invalidate}")
    if a.trigger:
        fields.append(f'trigger: "{a.trigger}"')
    if a.stop:
        fields.append(f'stop: "{a.stop}"')
    if a.note:
        fields.append(f'note: "{a.note}"')
    return "  - {" + ", ".join(fields) + "}\n"


def cmd_list(_args: argparse.Namespace) -> None:
    lines = load_lines()
    start, end = find_watchlist_block(lines)
    entries = [l for l in lines[start + 1:end] if l.strip().startswith("-")]
    if not entries:
        print("Watchlist is empty.")
        return
    print(f"{len(entries)} watchlist entries:\n")
    for line in entries:
        ticker = parse_ticker(line) or "?"
        trigger = existing_field(line, "trigger") or ""
        stop = existing_field(line, "stop") or ""
        note = existing_field(line, "note") or ""
        note_preview = (note[:80] + "…") if len(note) > 80 else note
        print(f"  {ticker:14s} trigger={trigger:20s} stop={stop:10s} {note_preview}")


def cmd_add(a: argparse.Namespace) -> None:
    lines = load_lines()
    start, end = find_watchlist_block(lines)
    for i in range(start + 1, end):
        if parse_ticker(lines[i]) == a.ticker:
            print(f"{a.ticker} already exists in watchlist — updating in place instead of duplicating.")
            lines[i] = format_entry(a)
            save_lines(lines)
            return
    lines.insert(end, format_entry(a))
    save_lines(lines)
    print(f"Added {a.ticker} to watchlist. Review with 'git diff', then commit/push.")


def cmd_update(a: argparse.Namespace) -> None:
    lines = load_lines()
    start, end = find_watchlist_block(lines)
    for i in range(start + 1, end):
        if parse_ticker(lines[i]) == a.ticker:
            existing = lines[i]
            # Fill in any field the caller didn't pass with its existing value,
            # so a partial `update --stop 90` doesn't blank out the rest.
            a.yf = a.yf or existing_field(existing, "yf")
            a.market = a.market or existing_field(existing, "market")
            a.trigger = a.trigger or existing_field(existing, "trigger")
            a.stop = a.stop or existing_field(existing, "stop")
            a.note = a.note or existing_field(existing, "note")
            if a.invalidate is None:
                v = existing_field(existing, "invalidate")
                a.invalidate = float(v) if v else None
            if not a.zone:
                a.zone = existing_field(existing, "zone")
            lines[i] = format_entry(a)
            save_lines(lines)
            print(f"Updated {a.ticker}. Review with 'git diff', then commit/push.")
            return
    sys.exit(f"{a.ticker} not found in watchlist — use 'add' instead.")


def cmd_remove(a: argparse.Namespace) -> None:
    lines = load_lines()
    start, end = find_watchlist_block(lines)
    for i in range(start + 1, end):
        if parse_ticker(lines[i]) == a.ticker:
            del lines[i]
            save_lines(lines)
            print(f"Removed {a.ticker} from watchlist. Review with 'git diff', then commit/push.")
            return
    sys.exit(f"{a.ticker} not found in watchlist.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manage positions.yaml's watchlist section only")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("ticker")
        sp.add_argument("--yf", help='data symbol, e.g. TICKER.NS (Indian) or TICKER (US)')
        sp.add_argument("--market", choices=["IN", "US"])
        sp.add_argument("--zone", help="entry zone lo,hi e.g. 100,110")
        sp.add_argument("--invalidate", type=float, help="hard invalidation price")
        sp.add_argument("--trigger", help='human-readable trigger, e.g. "100-110 reclaim"')
        sp.add_argument("--stop", help="stop level, e.g. 95 or $95")
        sp.add_argument("--note", help="free-text note / thesis")

    p_add = sub.add_parser("add", help="add a new ticker (or update it if it already exists)")
    common(p_add)
    p_add.set_defaults(func=cmd_add)

    p_upd = sub.add_parser("update", help="update one or more fields on an existing ticker")
    common(p_upd)
    p_upd.set_defaults(func=cmd_update)

    p_rm = sub.add_parser("remove", help="remove a ticker from the watchlist")
    p_rm.add_argument("ticker")
    p_rm.set_defaults(func=cmd_remove)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
