# Marketing Campaigns

How marketing campaigns are structured and how their effectiveness is measured.

## Campaign anatomy

Each row in the `campaigns` table represents a single marketing campaign with:

- `name` — human-readable identifier
- `channel` — one of `email`, `social_media`, `in_store`, `referral`
- `start_date` / `end_date` — campaign run dates (inclusive)
- `budget` — total dollars allocated
- `target_audience` — free-text segment description
- `status` — `planned`, `active`, `completed`, or `cancelled`

## Attribution rules

We attribute customer acquisition by **first order date**: a customer is attributed
to a campaign if their `first_order_date` falls within `[start_date, end_date]`. There
is no campaign_id foreign key on customers — attribution is purely date-based.

This means:
- Overlapping campaigns share credit (we cannot tell which one drove the customer).
- Campaigns that ran before the data window cannot be attributed.

## Standard effectiveness metrics

- **Customer acquisition cost (CAC)**: `budget / customers acquired during campaign window`.
- **Reach**: customers whose first order fell in the window.
- **Revenue lift**: net revenue during the campaign window minus the trailing average.

## Channels notes

- `email` campaigns reach existing loyalty members; they are best for retention, not
  acquisition.
- `social_media` and `referral` skew toward new customer acquisition.
- `in_store` campaigns are usually point-of-sale promotions and rarely have a
  trackable conversion outside of order volume changes.
