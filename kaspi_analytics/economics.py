#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit economics for a Kaspi marketplace shop, presented two ways and checked
against data the tool did not compute itself.

**Two presentations.** The same period is shown as

  * **P&L** — profit on goods actually *sold*; unsold stock is an asset,
    not an expense;
  * **cash flow** — money that actually came in and went out.

They answer different questions ("did this product earn?" vs "why is the
account empty?") and a seller needs both. Note that the gap between them is
*always* the value of unsold stock — that is an algebraic identity of the
model, not a test. Presenting it as a passing check would be dishonest: it
cannot fail, whatever the inputs.

**Two real checks.** A check is only worth printing if wrong inputs can make
it fail, which means comparing against a number this tool did not derive:

  1. ``expected_total_expenses`` — the expense total from your own
     bookkeeping. Compared against the sum of the itemised expenses; catches
     a line you forgot to enter.
  2. ``stock_counted`` — units physically on the shelf. Compared against
     purchased minus sold, given away and written off; catches a wrong batch
     size, an unrecorded giveaway, or shrinkage.

Both are optional. If you supply neither, the report says plainly that
nothing was verified rather than implying the figures are confirmed.

All figures come from a config file plus the fetched API data. Nothing is
hardcoded, and no number is typed into the report by hand.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Order statuses that count as a completed sale.
SOLD_STATUS = "COMPLETED"
RETURNED_STATUS = "RETURNED"
CANCELLED_STATUS = "CANCELLED"

#: Expense categories. "goods" is landed cost and is the only category
#: amortised per unit; everything else is a period expense.
GOODS = "goods"


def money(value: float) -> str:
    """Thousands-separated integer, the way KZT is written locally."""
    return f"{round(value):,}".replace(",", " ")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Product:
    sku: str
    name: str
    batch_qty: int = 0          # units purchased; 0 = not part of a tracked batch
    ad_spend: float = 0.0       # marketplace ad spend attributed to this SKU
    given_away: int = 0         # samples, demo units — left the shelf, earned nothing
    written_off: int = 0        # damaged / lost
    offline_qty: int = 0        # sold outside the marketplace (cash)
    offline_amount: float = 0.0
    stock_counted: int | None = None   # units physically counted on the shelf


@dataclass
class Config:
    commission_rate: float
    products: dict[str, Product]
    expenses: list[dict[str, Any]] = field(default_factory=list)
    currency: str = "₸"
    #: Expense total from your own bookkeeping, if you have one. Checked
    #: against the sum of ``expenses`` to catch a line left out.
    expected_total_expenses: float | None = None

    @classmethod
    def load(cls, path: Path) -> "Config":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        products = {
            sku: Product(sku=sku, **values)
            for sku, values in raw["products"].items()
        }
        return cls(
            commission_rate=raw["commission_rate"],
            products=products,
            expenses=raw.get("expenses", []),
            currency=raw.get("currency", "₸"),
            expected_total_expenses=raw.get("expected_total_expenses"),
        )

    @property
    def goods_cost(self) -> float:
        """Total landed cost of the batch: purchase plus freight to warehouse."""
        return sum(e["amount"] for e in self.expenses if e.get("category") == GOODS)

    @property
    def period_expenses(self) -> float:
        """Everything that is not landed cost of goods."""
        return sum(e["amount"] for e in self.expenses if e.get("category") != GOODS)

    @property
    def batch_size(self) -> int:
        return sum(p.batch_qty for p in self.products.values())

    @property
    def unit_cost(self) -> float:
        """
        Average landed cost per unit.

        Averaged across the batch because suppliers usually invoice one total
        for a mixed order; if you do have a per-model purchase price, split
        ``expenses`` per SKU and this becomes exact.
        """
        if not self.batch_size:
            return 0.0
        return self.goods_cost / self.batch_size


# ---------------------------------------------------------------------------
# Reading fetched data
# ---------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


@dataclass
class SalesLine:
    qty: int = 0
    revenue: float = 0.0
    shipping: float = 0.0       # marketplace delivery cost charged to the seller
    returned: int = 0
    cancelled_in_transit: int = 0


def aggregate_sales(orders_path: Path, entries_path: Path,
                    config: Config) -> dict[str, SalesLine]:
    """
    Fold raw orders and line items into per-SKU totals.

    Shipping is billed per *order*, so for a multi-item order it is split
    across its lines in proportion to line value — charging it all to one SKU
    would silently distort that product's margin.
    """
    orders = {o["id"]: o["attributes"] for o in read_jsonl(orders_path)}
    entries = read_jsonl(entries_path)

    order_total: dict[str, float] = defaultdict(float)
    for entry in entries:
        order_total[entry["order_id"]] += entry["attributes"].get("totalPrice") or 0

    sales = {sku: SalesLine() for sku in config.products}

    for entry in entries:
        attributes = entry["attributes"]
        sku = (attributes.get("offer") or {}).get("code")
        if sku not in sales:
            continue

        order = orders.get(entry["order_id"])
        if order is None:
            continue

        quantity = attributes.get("quantity") or 0
        status = order.get("status")

        if status == SOLD_STATUS:
            line_value = attributes.get("totalPrice") or 0
            share = line_value / (order_total[entry["order_id"]] or 1)
            sales[sku].qty += quantity
            sales[sku].revenue += line_value
            sales[sku].shipping += (order.get("deliveryCostForSeller") or 0) * share

        elif status == RETURNED_STATUS:
            sales[sku].returned += quantity

        elif status == CANCELLED_STATUS and (order.get("deliveryCostForSeller") or 0) > 0:
            # Shipping was charged, so the unit physically travelled and came
            # back. Worth inspecting before it goes back on the shelf.
            sales[sku].cancelled_in_transit += quantity

    return sales


# ---------------------------------------------------------------------------
# The two computations
# ---------------------------------------------------------------------------

@dataclass
class ProductResult:
    product: Product
    sales: SalesLine
    unit_cost: float
    commission_rate: float = 0.0

    @property
    def cogs(self) -> float:
        return self.sales.qty * self.unit_cost

    @property
    def commission(self) -> float:
        """Marketplace commission — charged on marketplace revenue only."""
        return self.sales.revenue * self.commission_rate

    @property
    def contribution(self) -> float:
        """Revenue less everything directly attributable to this product."""
        return (self.sales.revenue - self.cogs - self.commission
                - self.sales.shipping - self.product.ad_spend)

    @property
    def per_unit(self) -> float:
        return self.contribution / self.sales.qty if self.sales.qty else 0.0

    @property
    def stock_left(self) -> int:
        """Physical balance: what should still be on the shelf."""
        return (self.product.batch_qty - self.sales.qty - self.product.offline_qty
                - self.product.given_away - self.product.written_off)


def compute(config: Config, sales: dict[str, SalesLine]) -> dict[str, Any]:
    unit_cost = config.unit_cost
    results: dict[str, ProductResult] = {}

    for sku, product in config.products.items():
        results[sku] = ProductResult(
            product=product,
            sales=sales.get(sku, SalesLine()),
            unit_cost=unit_cost,
            commission_rate=config.commission_rate,
        )

    marketplace_revenue = sum(r.sales.revenue for r in results.values())
    offline_revenue = sum(p.offline_amount for p in config.products.values())
    offline_qty = sum(p.offline_qty for p in config.products.values())

    total_cogs = sum(r.cogs for r in results.values()) + offline_qty * unit_cost
    total_commission = sum(r.commission for r in results.values())
    total_shipping = sum(r.sales.shipping for r in results.values())
    total_ads = sum(p.ad_spend for p in config.products.values())

    given_away_units = sum(p.given_away for p in config.products.values())
    written_off_units = sum(p.written_off for p in config.products.values())
    stock_units = sum(r.stock_left for r in results.values())

    # --- 1. P&L: unsold stock is an asset, not an expense --------------------
    pnl_profit = (
        marketplace_revenue + offline_revenue
        - total_cogs
        - total_commission
        - total_shipping
        - total_ads
        - config.period_expenses
        - (given_away_units + written_off_units) * unit_cost
    )

    # --- 2. Cash flow: money in minus money out -----------------------------
    cash_in = marketplace_revenue + offline_revenue - total_commission - total_shipping
    cash_out = config.goods_cost + config.period_expenses + total_ads
    cash_flow = cash_in - cash_out

    stock_value = stock_units * unit_cost

    # --- 3. Checks against data this tool did not derive ---------------------
    checks: list[dict[str, Any]] = []

    if config.expected_total_expenses is not None:
        computed = config.goods_cost + config.period_expenses
        delta = computed - config.expected_total_expenses
        checks.append({
            "name": "expenses vs bookkeeping",
            "detail": (f"itemised {money(computed)} vs "
                       f"bookkeeping {money(config.expected_total_expenses)}"),
            "delta": delta,
            "passed": abs(delta) < 1.0,
            "hint": "an expense line is missing from the config, or double-counted",
        })

    counted_products = {sku: p for sku, p in config.products.items()
                        if p.stock_counted is not None}
    if counted_products:
        for sku, product in counted_products.items():
            expected = results[sku].stock_left
            delta = expected - product.stock_counted
            checks.append({
                "name": f"stock: {product.name}",
                "detail": (f"expected {expected} units, "
                           f"counted {product.stock_counted}"),
                "delta": delta,
                "passed": delta == 0,
                "hint": ("wrong batch size, an unrecorded giveaway, "
                         "or shrinkage"),
            })

    return {
        "unit_cost": unit_cost,
        "results": results,
        "marketplace_revenue": marketplace_revenue,
        "offline_revenue": offline_revenue,
        "total_cogs": total_cogs,
        "total_commission": total_commission,
        "total_shipping": total_shipping,
        "total_ads": total_ads,
        "period_expenses": config.period_expenses,
        "goods_cost": config.goods_cost,
        "pnl_profit": pnl_profit,
        "cash_flow": cash_flow,
        "stock_units": stock_units,
        "stock_value": stock_value,
        "checks": checks,
        "all_passed": all(c["passed"] for c in checks),
        "verified": bool(checks),
    }
