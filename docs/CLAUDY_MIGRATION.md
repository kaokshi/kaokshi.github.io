# Claudy migration: billing acceptance gate

Status: owner chat may use Claudy as an owner-selected provider (settlement by monthly-counter delta, see the 2026-11-28 owner decision below). Production, development and the Python editorial executor stay staged with no paid Claudy dispatch. Existing RouterAI holds and receipts are not changed.

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

## Support clarification, 2026-11-28
- Owner supplied support response: GET /v1/me exposes key.monthly_limit, key.spent_this_month, key.left, and account_limit when configured. Completed requests are charged immediately and appear in cabinet history with X-Request-ID after a short delay. No programmatic request-history endpoint is currently offered.
- Live read-only probe 46153834215 confirmed 1500/1500/4500 RUB monthly limits for Sonnet/Opus/editorial respectively. account_limit was unavailable under the validated schema.
- Dedicated development key counter increased 2.02 RUB during UI review 46154161179 and 3.05 RUB during patch proposal 46154886439. Those are observed aggregate changes, not independently verified request receipts.
- Monthly counters and freshness checks are implemented and tested. They do not resolve the existing RouterAI hold or provide authority to dispatch paid chat.
- Remaining acceptance gap: supported per-operation evidence or explicit reviewed manual receipt workflow. Until that gate passes, paid routing stays disabled. Do not label monthly counter differences as individual receipts even if the requests appear serialized.

## Owner decision, 2026-11-28: owner chat settles from the monthly counter
Owner quote: "Что касается Claudy - да". Scope: the ZEN CONTROL owner chat only. Production stays on RouterAI; the Python executor and editorial roles are unchanged.

The earlier statement that counter differences must not be labelled as individual receipts is superseded **only** for this chat lane and **only** under the rule below. Every other use of the counters (development, editorial, cross-request intervals) remains an aggregate observation.

### Route
- `agent:settings.provider` = `routerai` (default) or `claudy`. Claudy serves Claude Sonnet 5.5 and Claude Opus 5.5 only. Older settings without a provider read as RouterAI; an older client that omits the provider keeps the current one.
- Key: `CLAUDY_SONNET_API_KEY`, the chat-role key from `claudy-role-keys.mjs`. It is the only Claudy key bound to the Worker and must not be used by anything else.
- The daily chat limit, the Opus daily limit, the per-call limit and the 20 calls/day ceiling apply exactly as for RouterAI. The reservation is the conservative `claudyPreflight` upper bound (free `count_tokens`, never lowered), with `max_tokens` = 4000.
- Single flight: the persistent `agent:charge-hold` (one chat request at a time, `billingLane: "claudy_chat"`) plus the account coordinator chat lane (1 concurrent, `reserveClaudySlot`).

### Exact settlement rule (`claudy-chat.mjs`, `claudyCounterSettlement`)
1. Reserve the bound in the daily chat ledger and write the hold `pending`.
2. Take the coordinator chat slot. Read `GET /v1/me`. Require `claudyMonthlyEligibility` (read <= 60 s old, `key.left` and balance >= bound, `left = limit - spent` within 0.02 RUB). Persist that read as `counterBefore` in the hold **before** sending. If any step fails the request is not sent: cost 0, hold released.
3. Send one `POST /v1/messages`. Never resend.
4. Read `GET /v1/me` again. `delta = after.spent_this_month - before.spent_this_month`.
5. **Settled** (cost confirmed, hold released, `confirmedRub` += delta) only when all hold: both reads `ok` and internally consistent; same UTC month and same `monthly_limit`; `after.observedAt - before.observedAt <= 100 s`; an HTTP answer was received; no other coordinator admission since this request's slot; `0 <= delta <= bound`; and not (`delta = 0` while the reply reported output tokens, i.e. the counter has not caught up).
6. **Overrun**: `delta > bound`. The movement is accounted against the ledger, the day's reservation is raised to at least the daily limit (chat blocked until Moscow midnight), `confirmedRub` becomes unknown, and the message is marked unconfirmed. The hold is released because no later read can attribute the movement.
7. **Unknown** (any other case: failed or inconsistent read, stale window, counter reset or limit change, overlapping admission, no HTTP answer, lagging counter): the reservation and the hold stay. Chat is blocked until the owner reconciles.

### Owner-confirmed reconcile (`POST /api/core/agent/reconcile`, button «Сверить списание»)
- No `counterBefore` and no `dispatchAt` in the hold: the request never left. Release at 0.
- Otherwise refuse while `now - dispatchAt < 120 s` (a timed-out request may still complete). Read `GET /v1/me` fresh and apply the same rule against the persisted baseline without the 100 s window. Settle only if the read is ok and consistent, same month and limit, no other coordinator admission since the slot, `0 <= delta <= bound`, and the lagging-counter case does not apply. Otherwise refuse (`claudy_counter_exceeds_bound`, `claudy_counter_unverified`, `claudy_counter_baseline_missing`, `claudy_request_may_be_in_flight`) and keep the hold.
- Reconcile never resends the request and never recovers the lost reply.

### Remaining caveats
- The counter proves the key's total movement, not this request alone. Attribution rests on the key being used only by this chat. Spend on the same key from outside this Worker (cabinet, another tool) inside the window is attributed to the chat if it fits the bound, or turns into an overrun. Both directions overstate, never understate, the chat cost.
- Claudy's billing month boundary and timezone are not documented; a reset between the two reads is detected (spent decreases) and stays unknown.
- Support says completed requests are charged immediately. If the counter lags, a delta of 0 with output tokens stays unknown, but a partial lag (counter updated with a smaller amount) cannot be detected.
- The adapter's per-model rates are estimates used only for the bound. If Claudy raises prices above them, requests show as overruns and block the day.
- A Claudy chat hold shares `agent:charge-hold` with RouterAI, so an unresolved Claudy hold also blocks RouterAI chat and model-policy changes (`financial_hold`). It does not block production claims (`claimProduction`), which run on the separate RouterAI account.
- An unknown hold whose counter later cannot prove attribution (for example, more spend on the key) cannot be reconciled from the counter. It then needs a separate owner decision; the code does not offer a manual override.
