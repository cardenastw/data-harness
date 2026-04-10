# Net Revenue

Net revenue is Brewed Awakening's primary top-line metric. It is the total monetary
value of orders that the customer actually paid for and kept.

## Definition

```
net_revenue = SUM(orders.total) WHERE orders.status NOT IN ('refunded', 'cancelled')
```

`orders.total` already includes tax (it is `subtotal + tax`). Net revenue is therefore
**tax-inclusive**. If you need a tax-exclusive figure, sum `orders.subtotal` instead
under the same status filter.

## Why we exclude refunded and cancelled orders

- **refunded** orders represent money returned to the customer — counting them would
  overstate revenue.
- **cancelled** orders never collected payment in the first place.
- **pending** orders are excluded too in some reports (they may still cancel), but the
  finance team's default `net_revenue` definition includes pending because the
  fulfillment rate is high.

## What net revenue is *not*

- It is **not** the same as gross revenue. Gross revenue includes refunded orders.
  Marketing reports sometimes use gross — always confirm which is meant.
- It is **not** profit. Cost of goods (`products.cost * order_items.quantity`) must
  be subtracted to get gross profit.
- It is **not** average order value. For per-order figures use the
  `average_order_value` metric.

## Common questions

- *"What was net revenue last month?"* → finance default, uses the formula above.
- *"What was gross revenue last month?"* → drop the status filter.
- *"How much did we refund last month?"* → `SUM(orders.total) WHERE status =
  'refunded'`.
