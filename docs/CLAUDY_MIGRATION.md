# Claudy migration: billing acceptance gate

Status: staged only, no paid dispatch. Existing RouterAI holds and receipts are not changed.

## Verified
- Native Messages API for Sonnet and Opus worked in isolated tests.
- GET /v1/me returned a ruble balance; the free count_tokens test left 45.41 RUB unchanged.
- Count-tokens quota headers in the live test reported 240/239; this is not a generation quota.
- Public documentation describes usage token counts and request history in the cabinet, not a verified per-request settlement endpoint.

## Required before paid production/chat routing
1. Confirm with Claudy support whether a documented API provides final RUB cost, currency, request ID, finality/status, key scope and delayed/refunded charge semantics. Confirm supported authentication; do not use a cabinet session or undocumented endpoint as a substitute.
2. Add a validated provider-specific receipt adapter and synthetic tests for missing, duplicate, delayed, mismatched, out-of-bound and zero-cost records. Do not infer a receipt from overall balance movement or token estimates.
3. Connect the durable account coordinator and existing owner budget to all approved routes; do not remove or reset existing RouterAI holds or ledgers.
4. Run isolated, bounded acceptance tests using the documented settlement contract, then owner-requested independent Claudy Opus High review and regression tests. Production deployment and publishing remain separately gated.

Sources: https://claudy.shop/docs/api-overview , https://claudy.shop/docs/api-account , https://claudy.shop/docs/api-messages , https://claudy.shop/docs/prompt-caching , https://claudy.shop/docs/faq .

## Support question
Does Claudy provide an API-key authenticated read-only request-history/receipt endpoint for the final amount debited in RUB by request ID, with status, key ID and refunds/adjustments? Please provide the documented path, response schema, rate limits and finality timing. If there is no such API, can you provide a documented machine-readable export or another supported reconciliation method? No key or private account data is required for this question.
