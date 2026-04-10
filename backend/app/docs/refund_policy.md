# Refund Policy

How refunds are handled and how they show up in the data.

## Customer-facing policy

- Drinks may be refunded within 30 minutes of purchase if the customer is dissatisfied.
- Pastries and sandwiches may be refunded within 24 hours with a receipt.
- Online pickup orders may be refunded if not picked up within 2 hours of the ready
  notification.

## How refunds flow into the database

Refunds **mutate the original `orders` row** rather than creating a new transaction.

- `orders.status` is updated from `completed` to `refunded`.
- `orders.total` is **not** changed — the original total stays in place. The refund
  amount is implicit (it equals the original total; partial refunds are not
  supported in this dataset).
- `loyalty_program.points_earned` is decremented by the points originally awarded.

This means: if you query `orders.status = 'refunded'`, the `total` column is the
amount refunded.

## Common pitfalls

- **Don't** double-count by joining `orders` to a `refunds` table — there is no
  refunds table.
- **Don't** subtract refunds from gross revenue *and* exclude them from net
  revenue. Pick one; net revenue already excludes them.
- **Do** filter by `order_date` (the original order date) when measuring refund rate
  in a time period — there is no separate `refund_date`.
