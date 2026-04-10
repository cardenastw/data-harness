# Loyalty Program

Brewed Awakening Rewards is the customer loyalty program. Membership is free and
points-based.

## Tiers and thresholds

| Tier | Lifetime points required |
|---|---|
| Bronze | 0 (assigned on enrollment) |
| Silver | 500 |
| Gold | 2000 |

Tiers are calculated from **lifetime points earned**, not current balance — redeeming
points does not lower a customer's tier.

## Earning rules

- **1 point per $1 spent**, rounded down on `orders.total` (tax-inclusive).
- **Double points on Tuesdays** (one of marketing's standing campaigns).
- **Bonus 50 points** for first online_pickup order.
- Points are credited when the order reaches `completed` status. Refunded orders claw
  back the originally awarded points.

## Redemption rules

- Redemptions happen at checkout: 100 points = $1 off subtotal.
- Minimum redemption is 200 points.
- Points cannot be redeemed against a tax line.
- Tier benefits are stackable with redemptions.

## Tier benefits

- **Silver**: free size upgrade once per week.
- **Gold**: free drink on birthday week + early access to seasonal menu items.

## Where this lives in the data

The `loyalty_program` table stores one row per enrolled customer with
`points_earned`, `points_redeemed`, and `tier`. There is **no** historical points
ledger — only the running totals.
