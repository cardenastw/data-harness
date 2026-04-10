# Glossary

Reference for the terms, codes, and category values used across Brewed Awakening's data.

## Order statuses

- **pending** — order placed but not yet fulfilled
- **completed** — order fulfilled, payment captured, no refund
- **cancelled** — order voided before fulfillment; no payment captured
- **refunded** — order was fulfilled and paid, then refunded after the fact

Net revenue calculations exclude both `cancelled` and `refunded` orders.

## Order types

- **in_store** — placed at the counter at a physical location
- **online_pickup** — placed via the app or website, picked up in store

## Product categories

`coffee`, `espresso`, `tea`, `pastry`, `sandwich`, `other`. Drink categories have a
`size` (small / medium / large); pastries and sandwiches do not.

## Loyalty tiers

- **bronze** — entry tier, assigned on enrollment
- **silver** — 500 lifetime points earned
- **gold** — 2000 lifetime points earned

See the loyalty program doc for tier benefits and the points-earning rules.

## Campaign channels

`email`, `social_media`, `in_store`, `referral`. A campaign's `status` follows the same
lifecycle as orders but with `planned` instead of `pending`.
