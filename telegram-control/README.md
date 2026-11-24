# Telegram control MVP

Isolated Cloudflare Worker, read-only. No paid generations, publication, repository writes, or automatic deployment. Requires owner approval before live activation.

1. Install Wrangler locally or use Cloudflare dashboard. Deploy from this directory with npx wrangler deploy after reviewing the code.
2. Configure Worker secrets with wrangler secret put (not public variables): TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID (numeric Telegram user ID), TELEGRAM_WEBHOOK_SECRET (random 32+ characters from a secure generator), ROUTERAI_API_KEY (optional), EDITORIAL_PRIVATE_TOKEN (optional, preferably a separate read-only Contents token for the private review repo).
3. GitHub Actions secrets do not automatically become Worker secrets. Never paste tokens into chat, source code, URLs in a browser, or logs.
4. Register the deployed HTTPS /telegram endpoint using Telegram setWebhook with secret_token equal to TELEGRAM_WEBHOOK_SECRET, allowed_updates ["message"]. Use local protected environment variables or a trusted deployment process. Do not enable webhook until owner ID and secret are set. Verify /health, then /start from owner's private chat.
5. Run node --test worker.test.mjs locally. Never rerun paid editorial tests from this bot.

Current limitations: /status reports mode only, /models lists intended models, /expenses is not connected, /articles and /tests list private result directories (not completed article content). No automatic notifications or controls yet. Future commands should use a durable authorization/approval and budget layer, not direct LLM execution. Telegram delivery can duplicate read-only replies on webhook retries.

## Offline production and finance checks (2026-11-24)

`adapter.mjs` builds only a deterministic draft-task graph from existing confirmed jobs; it never dispatches a workflow, creates an article, or authorizes RSS. `spend-simulation.mjs` is isolated under the `simulation:spend:` storage namespace, without public endpoints or any live provider integration. Its synthetic context assumes verified owner policy, matching daily configuration, a monthly cap, and opening fixture funds. It reserves in integer kopecks, checks daily/monthly limits and Moscow dates, records atomic article starts, rejects replays, and retains uncertain charges while halting new reservations. Actual provider balance, price bounds, authenticated receipts, cross-worker ledger, and manual reconciliation of ambiguous charges still need verified production integration. No simulated balance or receipt may be shown as live expenditure. Do not enable production on the basis of these tests.

Run `node --test telegram-control/worker.test.mjs telegram-control/adapter.test.mjs telegram-control/spend-simulation.test.mjs` from the repository root. The offline workflow requires no secrets and does not deploy.
