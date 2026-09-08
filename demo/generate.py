#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate synthetic Kaspi data so the tool can be run without a merchant token.

The records mirror the real API's shape — the same nesting, field names and
millisecond timestamps — so code exercised against this demo behaves the same
way against a live account. The numbers are invented.

    python demo/generate.py            # writes demo/data/ and demo/config.json
    python -m kaspi_analytics.report --config demo/config.json --data demo/data
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

KZ = timezone(timedelta(hours=5))
HERE = Path(__file__).resolve().parent

SEED = 20260908           # fixed so the demo is reproducible
PERIOD_START = datetime(2026, 2, 1, tzinfo=KZ)
PERIOD_DAYS = 150

PRODUCTS = {
    "DEMO-VC-100": {
        "name": "Demo Vacuum Standard",
        "price": 89_000,
        "batch": 100,
        "sales": 86,
        "returns": 2,
        "cancelled": 1,
        "ads": 420_000,
        "given_away": 4,
        "written_off": 1,
    },
    "DEMO-VC-200": {
        "name": "Demo Vacuum Pro",
        "price": 119_000,
        "batch": 100,
        "sales": 41,
        "returns": 3,
        "cancelled": 2,
        "ads": 610_000,
        "given_away": 2,
        "written_off": 0,
    },
}

EXPENSES = [
    {"date": "2025-12-26", "item": "trademark registration", "amount": 770_000, "category": "launch"},
    {"date": "2025-12-27", "item": "product photography",    "amount": 73_000,  "category": "launch"},
    {"date": "2026-01-08", "item": "supplier invoice",       "amount": 7_050_000, "category": "goods"},
    {"date": "2026-01-30", "item": "freight to warehouse",   "amount": 761_000, "category": "goods"},
    {"date": "2026-02-02", "item": "local delivery",         "amount": 30_000,  "category": "goods"},
    {"date": "2026-03-15", "item": "accounting",             "amount": 120_000, "category": "operating"},
]

OFFLINE = {"DEMO-VC-200": {"qty": 1, "amount": 110_000}}
COMMISSION = 0.12
SHIPPING_PER_ORDER = (1_200, 3_800)


def to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def build() -> tuple[list[dict], list[dict]]:
    rng = random.Random(SEED)
    orders: list[dict] = []
    entries: list[dict] = []
    counter = 0

    for sku, spec in PRODUCTS.items():
        plan = (
            [("COMPLETED", spec["sales"])]
            + [("RETURNED", spec["returns"])]
            + [("CANCELLED", spec["cancelled"])]
        )
        for status, count in plan:
            for _ in range(count):
                counter += 1
                created = PERIOD_START + timedelta(
                    days=rng.randint(0, PERIOD_DAYS - 1),
                    hours=rng.randint(8, 21),
                    minutes=rng.randint(0, 59),
                )
                order_id = f"demo-order-{counter:05d}"
                order_code = f"{300_000_000 + counter}"
                # A cancelled order only carries a shipping charge if it had
                # already been dispatched — that distinction is what lets the
                # report flag units that travelled and came back.
                shipping = (
                    rng.randint(*SHIPPING_PER_ORDER)
                    if status != "CANCELLED" or rng.random() < 0.6
                    else 0
                )
                price = spec["price"]

                orders.append({
                    "id": order_id,
                    "type": "orders",
                    "attributes": {
                        "code": order_code,
                        "status": status,
                        "creationDate": to_ms(created),
                        "totalPrice": price,
                        "deliveryCostForSeller": shipping,
                        "deliveryAddress": {"town": rng.choice(
                            ["Astana", "Almaty", "Shymkent", "Karaganda"])},
                    },
                })
                entries.append({
                    "order_id": order_id,
                    "order_code": order_code,
                    "entry_id": f"demo-entry-{counter:05d}",
                    "attributes": {
                        "quantity": 1,
                        "basePrice": price,
                        "totalPrice": price,
                        "offer": {"code": sku, "name": spec["name"]},
                    },
                })

    orders.sort(key=lambda o: o["attributes"]["creationDate"])
    return orders, entries


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_config() -> dict:
    products = {}
    for sku, spec in PRODUCTS.items():
        offline = OFFLINE.get(sku, {"qty": 0, "amount": 0})
        # A shop that counted its shelves would enter the real number here.
        # The demo derives it, so the check passes on a clean run — change
        # any figure below and watch the check fail, which is the point.
        counted = (spec["batch"] - spec["sales"] - offline["qty"]
                   - spec["given_away"] - spec["written_off"])
        products[sku] = {
            "name": spec["name"],
            "batch_qty": spec["batch"],
            "ad_spend": spec["ads"],
            "given_away": spec["given_away"],
            "written_off": spec["written_off"],
            "offline_qty": offline["qty"],
            "offline_amount": offline["amount"],
            "stock_counted": counted,
        }

    return {
        "currency": "₸",
        "commission_rate": COMMISSION,
        "expected_total_expenses": sum(e["amount"] for e in EXPENSES),
        "products": products,
        "expenses": EXPENSES,
    }


def main() -> None:
    orders, entries = build()
    write_jsonl(HERE / "data" / "orders.jsonl", orders)
    write_jsonl(HERE / "data" / "entries.jsonl", entries)
    (HERE / "config.json").write_text(
        json.dumps(build_config(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    print(f"orders:  {len(orders):>4} -> demo/data/orders.jsonl")
    print(f"entries: {len(entries):>4} -> demo/data/entries.jsonl")
    print("config:       -> demo/config.json")
    print("\nnow run:")
    print("  python -m kaspi_analytics.report --config demo/config.json --data demo/data")


if __name__ == "__main__":
    main()
