# Kaspi Shop Analytics

**Pull your Kaspi.kz marketplace orders, compute real unit economics, and check the result against data the tool didn't produce itself.**

Kaspi's merchant cabinet shows revenue. Revenue is not the number that matters — a product can top the sales chart and still lose money once commission, seller-paid shipping, ad spend and landed cost come out. This tool computes contribution per product from the raw order data, and then tries to prove itself wrong.

Runs on demo data out of the box, no merchant account needed:

```bash
git clone https://github.com/zhuniskhans-hash/kaspi-shop-analytics.git
cd kaspi-shop-analytics
python3 demo/generate.py
python3 -m kaspi_analytics.report --config demo/config.json --data demo/data
```

Python 3.10+, standard library only — no dependencies to install.

---

## What the API actually does

These are things the documentation does not tell you, established by probing a live merchant account:

| Behaviour | Consequence |
|---|---|
| `page[size]` is capped at **100** | Anything larger is silently clamped |
| A `creationDate` range wider than ~**14 days** errors out | Long periods must be walked in windows — the client uses 13-day windows with a 1 ms gap, so nothing is fetched twice or skipped |
| Order entries embed `attributes.offer.{code,name}` | The extra `/products` request the docs imply is unnecessary — this alone cuts request count roughly in half |
| Timestamps are Unix **milliseconds** | Kazakhstan is UTC+5 with no DST, so a fixed offset is correct |
| Rate limits surface as `429` | The client backs off exponentially; non-429 `4xx` is never retried, since retrying a malformed request only burns quota |

Fetching is **resumable**. A few thousand orders at 0.25 s between calls takes a while, and networks drop. Both output files are append-only and keyed by id, so re-running after a failure continues instead of starting over.

```bash
export KASPI_TOKEN=...    # or put it in .env, see .env.example
python3 -m kaspi_analytics.fetch --from 2026-01-01 --to 2026-07-27 --out data/
```

---

## The economics

Contribution per product, with two details that change the answer:

**Shipping is billed per order, not per item.** In a two-item order, charging the whole delivery cost to one SKU silently ruins that product's apparent margin. It gets split across lines in proportion to line value.

**Landed cost is amortised across the batch.** Suppliers invoice one total for a mixed order, so per-unit cost is `(purchase + freight) / batch size`. If you have per-model purchase prices, split the expense lines per SKU and it becomes exact.

Output per product: units sold, average price, revenue, cost of goods, commission, shipping, ads, contribution in money and as a share of revenue, contribution per unit, and the units that should still be on the shelf.

It also flags units that **travelled and came back** — returns, plus cancellations that still carry a seller shipping charge, since that charge proves the unit was dispatched. Those are the units to inspect before restocking.

---

## Two views, and two actual checks

The same period is presented two ways, because they answer different questions:

- **P&L** — profit on what was *sold*; unsold stock is an asset, not an expense. Answers *"does this product earn?"*
- **Cash flow** — money in minus money out. Answers *"why is the account empty when the P&L looks fine?"*

The gap between them is always the value of unsold stock. That is an **algebraic identity of the model, not a verification** — it holds for any inputs, including wrong ones. The report says so rather than dressing it up as a passing check, because a check that cannot fail is worse than no check: it manufactures confidence.

A check is only worth printing if wrong inputs can make it fail. That means comparing against a number the tool did not derive:

| Check | Compares | Catches |
|---|---|---|
| `expected_total_expenses` | Your bookkeeping total vs the sum of itemised expenses | An expense line you forgot to enter, or double-counted |
| `stock_counted` | Units physically on the shelf vs purchased − sold − given away − written off | A wrong batch size, an unrecorded giveaway, shrinkage |

Both are optional. Configure neither and the report states plainly that nothing was verified — it does not imply the figures are confirmed. A failed check exits `2`, so this drops into a cron job or CI without extra glue.

To see a check fail, edit `stock_counted` in `demo/config.json` and re-run.

---

## Configuration

See [`config.example.json`](config.example.json). Products are keyed by Kaspi SKU:

```json
{
  "currency": "₸",
  "commission_rate": 0.12,
  "expected_total_expenses": 8804000,
  "products": {
    "YOUR-SKU-1": {
      "name": "Product name",
      "batch_qty": 100,
      "ad_spend": 420000,
      "given_away": 4,
      "written_off": 1,
      "offline_qty": 0,
      "offline_amount": 0,
      "stock_counted": 9
    }
  },
  "expenses": [
    { "date": "2026-01-08", "item": "supplier invoice", "amount": 7050000, "category": "goods" },
    { "date": "2026-03-15", "item": "accounting", "amount": 120000, "category": "operating" }
  ]
}
```

`category: "goods"` is landed cost and is the only category amortised per unit; everything else is a period expense. Commission and ad spend are not in the API — commission is a contract rate, ad spend lives in the Kaspi ads cabinet — so both come from the config.

`offline_qty` / `offline_amount` cover sales made outside the marketplace: they add revenue and consume stock, but carry no Kaspi commission or shipping.

---

## Layout

```
kaspi_analytics/
  api.py          Kaspi Shop API client — windowing, pagination, backoff
  fetch.py        resumable download to JSONL
  economics.py    unit economics and the checks
  report.py       terminal and HTML output
demo/
  generate.py     synthetic data in the real API's shape
```

Raw API responses are stored verbatim as JSONL. Re-analysis never needs a re-download, and if a calculation turns out wrong the source data is still there to recompute from.

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with Kaspi.kz. Uses the public merchant API with your own token.
