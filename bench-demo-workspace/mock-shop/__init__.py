"""Mock e-commerce backend used to exercise Bench feature-options end to end.

Modules:
- models:    User, Product, Order, CartItem domain types
- db:        in-memory seed data + lookups
- auth:      signup / login / sessions (intentionally weak)
- payments:  mock charge / refund gateway
- checkout:  cart totals + order creation flow
"""
