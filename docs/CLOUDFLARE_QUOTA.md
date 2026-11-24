# Cloudflare Workers AI: read-only quota monitor

> Архив. Старый опрос квот Workers AI снят с эксплуатации. Cloudflare Worker и его штатное развёртывание сохранены. [Действующая инструкция](EDITORIAL_PRODUCTION.md)

This is an isolated infrastructure diagnostic. It does not call AI models, alter
Cloudflare resources, produce articles, publish RSS, or change editorial schedules.

## Credentials

GitHub Actions repository secrets:

- `CLOUDFLARE_QUOTA_TOKEN`: dedicated Cloudflare token with
  `Account Analytics: Read` and `Workers AI: Read` for the project account.
- `CLOUDFLARE_ACCOUNT_ID`: existing account ID used by the production workflow.
  If absent, add this as a GitHub Actions secret without sharing it in chat.

Never put either value in source, workflow inputs, issues, or logs. The workflow
verifies token status via Cloudflare REST, then queries the read-only GraphQL API.

## Use

Open **Actions → Cloudflare Workers AI quota (read-only) → Run workflow**.
There is also one scheduled check at 06:30 UTC / 09:30 Moscow time.

The step summary reports the UTC date, aggregated Workers AI inference neuron
usage, and the **estimated** free remaining amount. These are not confirmed
billing numbers: GraphQL analytics may be sampled or delayed. Do not treat the
API's ordinary rate-limit headers as free inference quota. If the query fails,
the action fails and prints `unavailable` instead of inventing zero usage.

Cloudflare currently documents a daily free allocation of 10,000 neurons,
resetting at 00:00 UTC. Account plan/billing and model-specific access can
impose additional restrictions. Only Cloudflare's billing/usage dashboard
is authoritative for actual consumption.

- https://developers.cloudflare.com/workers-ai/platform/pricing/
- https://developers.cloudflare.com/analytics/graphql-api/
- https://developers.cloudflare.com/analytics/graphql-api/limits/

## Safety

The dedicated token is read-only. The script never prints token/account IDs
and does not include raw API responses in logs. Only aggregate diagnostic
numbers are shown in the public repository's workflow summary; do not add
private analytics, personally identifiable information, or raw request traces.

If a run reports missing credentials or a GraphQL permissions/schema error,
fix configuration or permissions and rerun. Do not auto-upgrade billing or
charge paid inference to resolve quota errors.
