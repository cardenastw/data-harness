# Data Dictionary

Plain-English description of every table in the Brewed Awakening database.

## locations
Physical Brewed Awakening coffee shop locations. One row per store.
Key columns: `name`, `city`, `state`, `opened_date`.

## products
Menu items sold across all locations. One row per SKU.
Key columns: `name`, `category`, `size`, `price`, `cost`, `is_active`.
Categories: `coffee`, `espresso`, `tea`, `pastry`, `sandwich`, `other`.

## customers
Registered customers (people who have at least one account). Walk-ins without an
account are *not* in this table — they appear as `customer_id IS NULL` on `orders`.
Key columns: `name`, `email`, `first_order_date`, `preferred_location_id`.

## orders
The transactional core of the database. One row per order placed, regardless of
status. Refunds and cancellations are mutations of the original row, not new rows.
Key columns: `customer_id`, `location_id`, `order_date`, `order_type`, `status`,
`subtotal`, `tax`, `total`.

## order_items
Line items within an order. One row per (order, product) pair, with quantity.
Key columns: `order_id`, `product_id`, `quantity`, `unit_price`, `line_total`.
`line_total` equals `quantity * unit_price`.

## inventory
Current stock levels — **point-in-time snapshot**, not a history. One row per
(location, product). Key columns: `quantity_on_hand`, `reorder_level`,
`last_restocked`. Use `quantity_on_hand < reorder_level` to find understocked items.

## campaigns
Marketing campaigns (see `campaigns.md` for attribution rules).
Key columns: `name`, `channel`, `start_date`, `end_date`, `budget`, `status`.

## loyalty_program
Loyalty program enrollments. One row per enrolled customer (see `loyalty_program.md`
for tier/earning rules). Key columns: `points_earned`, `points_redeemed`, `tier`,
`enrolled_date`.

## Time coverage

The dataset covers **January through March 2026**. Anything outside that range will
return zero rows.
