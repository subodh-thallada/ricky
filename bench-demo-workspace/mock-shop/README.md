# mock-shop

A tiny in-memory e-commerce backend for exercising Bench end to end.

## Layout
- `models.py` — domain dataclasses (`User`, `Product`, `Order`, `CartItem`, `OrderStatus`)
- `db.py` — seed users/products + in-memory `ORDERS` store
- `auth.py` — `signup`, `login`, session tokens (no rate limiting, sha256 hashing)
- `payments.py` — `charge_card`, `refund` (no idempotency, no retries)
- `checkout.py` — `cart_total_cents`, `checkout` (no stock rollback on payment failure)

## Known gaps (good Bench prompts)
- auth: no login rate limiting / lockout; weak password hashing
- payments: no idempotency keys; partial-refund accounting is loose
- checkout: stock decremented before payment confirmed, no rollback
