#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Download orders and line items from the Kaspi Shop API into JSONL files.

Resumable by design: a merchant's history is thousands of orders, the API is
rate-limited, and a run can take many minutes. Both files are append-only and
keyed by id, so re-running after a network drop picks up where it stopped
instead of re-downloading everything.

Usage:
    python -m kaspi_analytics.fetch --from 2026-01-01 --to 2026-07-27
    python -m kaspi_analytics.fetch --from 2026-01-01 --to 2026-07-27 --out data/

Token resolution order:
    1. ``KASPI_TOKEN`` environment variable
    2. ``KASPI_TOKEN=...`` line in a local ``.env``

The token is never written to disk by this tool and never appears in output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from .api import KZ, KaspiApiError, KaspiClient


def read_token(env_file: Path = Path(".env")) -> str:
    """Token from the environment, falling back to a local .env file."""
    token = os.environ.get("KASPI_TOKEN", "").strip()
    if token:
        return token

    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("KASPI_TOKEN="):
                return line.split("=", 1)[1].strip().strip("'\"")

    raise SystemExit(
        "KASPI_TOKEN not set.\n"
        "  export KASPI_TOKEN=... , or put KASPI_TOKEN=... in .env "
        "(see .env.example)"
    )


def load_seen(path: Path, key: str) -> set[str]:
    """Ids already present in a JSONL file — the basis for resuming."""
    seen: set[str] = set()
    if not path.exists():
        return seen
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line)[key])
            except (ValueError, KeyError):
                # A truncated last line from an interrupted run. Skipping it
                # is correct: the record gets re-fetched on this pass.
                continue
    return seen


def fetch_orders(client: KaspiClient, orders_file: Path,
                 start: datetime, end: datetime) -> int:
    seen = load_seen(orders_file, "id")
    print(f"orders already stored: {len(seen)}", flush=True)

    written = 0
    with orders_file.open("a", encoding="utf-8") as out:
        for order in client.orders(start, end):
            if order["id"] in seen:
                continue
            seen.add(order["id"])
            out.write(json.dumps(order, ensure_ascii=False) + "\n")
            out.flush()
            written += 1

    print(f"new orders written: {written} | total: {len(seen)}", flush=True)
    return written


def fetch_entries(client: KaspiClient, orders_file: Path,
                  entries_file: Path) -> int:
    """Line items for every stored order that does not have them yet."""
    order_refs: list[tuple[str, str]] = []
    with orders_file.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            order = json.loads(line)
            order_refs.append((order["id"], order["attributes"]["code"]))

    seen = load_seen(entries_file, "order_id")
    todo = [ref for ref in order_refs if ref[0] not in seen]
    print(f"entries: {len(todo)} orders to fetch ({len(seen)} already stored)",
          flush=True)

    written = 0
    with entries_file.open("a", encoding="utf-8") as out:
        for index, (order_id, order_code) in enumerate(todo, 1):
            for entry in client.order_entries(order_id):
                out.write(json.dumps({
                    "order_id": order_id,
                    "order_code": order_code,
                    "entry_id": entry["id"],
                    "attributes": entry.get("attributes", {}),
                }, ensure_ascii=False) + "\n")
                written += 1
            out.flush()
            if index % 25 == 0 or index == len(todo):
                print(f"  entries: {index}/{len(todo)}", flush=True)
            time.sleep(client.pause)

    return written


def parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=KZ)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD, got {value!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download Kaspi Shop orders and line items into JSONL.")
    parser.add_argument("--from", dest="start", required=True, type=parse_date,
                        metavar="YYYY-MM-DD", help="period start (inclusive)")
    parser.add_argument("--to", dest="end", required=True, type=parse_date,
                        metavar="YYYY-MM-DD", help="period end (inclusive)")
    parser.add_argument("--out", default="data", type=Path,
                        help="output directory (default: data)")
    args = parser.parse_args(argv)

    # --to is a date; the merchant means the whole of that day.
    end = args.end.replace(hour=23, minute=59, second=59)
    if end <= args.start:
        parser.error("--to must be after --from")

    args.out.mkdir(parents=True, exist_ok=True)
    orders_file = args.out / "orders.jsonl"
    entries_file = args.out / "entries.jsonl"

    client = KaspiClient(read_token())
    started = time.time()
    print(f"=== KASPI FETCH: {args.start:%d.%m.%Y} .. {end:%d.%m.%Y} ===",
          flush=True)
    try:
        fetch_orders(client, orders_file, args.start, end)
        fetch_entries(client, orders_file, entries_file)
    except KaspiApiError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 1

    print(f"=== DONE in {time.time() - started:.0f}s ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
