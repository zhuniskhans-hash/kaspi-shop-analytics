#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render the unit-economics report — to the terminal, and optionally to HTML.

Usage:
    python -m kaspi_analytics.report --config config.json --data data/
    python -m kaspi_analytics.report --config config.json --data data/ --html report.html

Every number printed here is derived from the config and the fetched data.
Nothing is typed in by hand, so a wrong figure means a wrong input — which
the reconciliation check at the end is there to catch.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

from .economics import Config, aggregate_sales, compute, money

WIDTH = 78


def print_report(config: Config, totals: dict) -> None:
    cur = config.currency
    line = "=" * WIDTH

    print(line)
    print("UNIT ECONOMICS".center(WIDTH))
    print(line)

    print("\nLANDED COST")
    print(f"  batch                    {config.batch_size:>12} units")
    print(f"  cost of goods            {money(config.goods_cost):>12} {cur}")
    print(f"  per unit                 {money(totals['unit_cost']):>12} {cur}")

    print("\n" + "-" * WIDTH)
    for result in totals["results"].values():
        product = result.product
        sales = result.sales
        if not sales.qty:
            print(f"{product.name} — no sales in period")
            print("-" * WIDTH)
            continue

        average = sales.revenue / sales.qty
        print(product.name)
        print(f"  sold {sales.qty} units · average price {money(average)} {cur}"
              f" · revenue {money(sales.revenue)} {cur}")
        print(f"  − cost of goods          {money(result.cogs):>12} {cur}"
              f"  ({sales.qty} × {money(result.unit_cost)})")
        print(f"  − commission {config.commission_rate:.0%}         "
              f"{money(result.commission):>12} {cur}")
        print(f"  − shipping               {money(sales.shipping):>12} {cur}")
        print(f"  − advertising            {money(product.ad_spend):>12} {cur}")
        margin = 100 * result.contribution / sales.revenue
        print(f"  = contribution           {money(result.contribution):>12} {cur}"
              f"   ({margin:.1f}% of revenue, {money(result.per_unit)} per unit)")
        if product.batch_qty:
            print(f"  stock left: {result.stock_left} units"
                  f"  (batch {product.batch_qty} − sold {sales.qty}"
                  f" − offline {product.offline_qty}"
                  f" − given away {product.given_away}"
                  f" − written off {product.written_off})")
        if sales.returned or sales.cancelled_in_transit:
            print(f"  returned {sales.returned}, cancelled in transit "
                  f"{sales.cancelled_in_transit} — inspect before restocking")
        print("-" * WIDTH)

    print("\nTOTALS")
    print(f"  marketplace revenue      {money(totals['marketplace_revenue']):>12} {cur}")
    if totals["offline_revenue"]:
        print(f"  offline revenue          {money(totals['offline_revenue']):>12} {cur}")
    print(f"  − cost of goods sold     {money(totals['total_cogs']):>12} {cur}")
    print(f"  − commission             {money(totals['total_commission']):>12} {cur}")
    print(f"  − shipping               {money(totals['total_shipping']):>12} {cur}")
    print(f"  − advertising            {money(totals['total_ads']):>12} {cur}")
    print(f"  − other period expenses  {money(totals['period_expenses']):>12} {cur}")

    print("\nTWO VIEWS OF THE SAME PERIOD")
    print(f"  P&L profit (stock is an asset)   {money(totals['pnl_profit']):>12} {cur}")
    print(f"  cash flow (money in − money out) {money(totals['cash_flow']):>12} {cur}")
    print(f"  stock on hand: {totals['stock_units']} units"
          f" = {money(totals['stock_value'])} {cur}")
    print("  (the gap between the two views is the stock value by construction —")
    print("   that is an identity of the model, not a verification)")

    print("\nCHECKS")
    if not totals["verified"]:
        print("  none configured — nothing here has been verified against")
        print("  independent data. Add expected_total_expenses and/or")
        print("  stock_counted to the config to get a real check.")
    else:
        for check in totals["checks"]:
            mark = "✓" if check["passed"] else "✗"
            print(f"  {mark} {check['name']}: {check['detail']}")
            if not check["passed"]:
                print(f"      off by {money(check['delta'])} — {check['hint']}")
    print(line)


HTML_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>Unit economics</title>
<style>
  body {{ font: 15px/1.55 -apple-system, Segoe UI, Roboto, sans-serif;
         max-width: 900px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .sub {{ color: #666; margin-bottom: 28px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 18px 0 30px; }}
  th, td {{ padding: 9px 12px; border-bottom: 1px solid #e6e6e6; text-align: right; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ background: #fafafa; font-weight: 600; }}
  .ok {{ color: #0a7c3f; font-weight: 600; }}
  .bad {{ color: #c0392b; font-weight: 600; }}
  .note {{ background: #f7f7f7; padding: 14px 16px; border-radius: 8px; color: #444; }}
</style>
<h1>Unit economics</h1>
<div class="sub">Landed cost {unit_cost} {cur}/unit · batch {batch} units</div>

<table>
  <tr><th>Product</th><th>Sold</th><th>Revenue</th><th>COGS</th>
      <th>Commission</th><th>Shipping</th><th>Ads</th><th>Contribution</th><th>Per unit</th></tr>
  {rows}
</table>

<table>
  <tr><th>View</th><th>Amount</th></tr>
  <tr><td>P&amp;L profit (unsold stock is an asset)</td><td>{pnl} {cur}</td></tr>
  <tr><td>Cash flow (money in − money out)</td><td>{cash} {cur}</td></tr>
  <tr><td>Stock on hand ({stock_units} units)</td><td>{stock_value} {cur}</td></tr>
</table>

<p class="note">
  <strong>Checks against independent data:</strong><br>
  {checks}
</p>
"""


def write_html(path: Path, config: Config, totals: dict) -> None:
    rows = []
    for result in totals["results"].values():
        if not result.sales.qty:
            continue
        rows.append(
            "<tr><td>{name}</td><td>{qty}</td><td>{rev}</td><td>{cogs}</td>"
            "<td>{comm}</td><td>{ship}</td><td>{ads}</td>"
            "<td>{contrib}</td><td>{unit}</td></tr>".format(
                name=html.escape(result.product.name),
                qty=result.sales.qty,
                rev=money(result.sales.revenue),
                cogs=money(result.cogs),
                comm=money(result.commission),
                ship=money(result.sales.shipping),
                ads=money(result.product.ad_spend),
                contrib=money(result.contribution),
                unit=money(result.per_unit),
            )
        )

    if totals["verified"]:
        check_lines = []
        for check in totals["checks"]:
            css = "ok" if check["passed"] else "bad"
            mark = "✓" if check["passed"] else "✗"
            text = f"{mark} {check['name']} — {check['detail']}"
            if not check["passed"]:
                text += f" ({check['hint']})"
            check_lines.append(f'<span class="{css}">{html.escape(text)}</span>')
        checks_html = "<br>\n  ".join(check_lines)
    else:
        checks_html = ('<span class="bad">none configured — these figures have '
                       'not been verified against independent data</span>')

    path.write_text(HTML_TEMPLATE.format(
        cur=html.escape(config.currency),
        unit_cost=money(totals["unit_cost"]),
        batch=config.batch_size,
        rows="\n  ".join(rows),
        pnl=money(totals["pnl_profit"]),
        cash=money(totals["cash_flow"]),
        stock_units=totals["stock_units"],
        stock_value=money(totals["stock_value"]),
        checks=checks_html,
    ), encoding="utf-8")
    print(f"\nHTML written to {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kaspi shop unit economics report.")
    parser.add_argument("--config", required=True, type=Path,
                        help="config file (see config.example.json)")
    parser.add_argument("--data", default=Path("data"), type=Path,
                        help="directory holding orders.jsonl and entries.jsonl")
    parser.add_argument("--html", type=Path, help="also write an HTML report here")
    args = parser.parse_args(argv)

    orders = args.data / "orders.jsonl"
    entries = args.data / "entries.jsonl"
    for path in (args.config, orders, entries):
        if not path.exists():
            print(f"not found: {path}", file=sys.stderr)
            return 1

    config = Config.load(args.config)
    sales = aggregate_sales(orders, entries, config)
    totals = compute(config, sales)

    print_report(config, totals)
    if args.html:
        write_html(args.html, config, totals)

    # Exit 2 only when a configured check actually failed. No checks configured
    # is not a failure — it is an unverified run, and the report says so.
    return 2 if (totals["verified"] and not totals["all_passed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
