# Telegram control MVP

Isolated Cloudflare Worker, read-only. No paid generations, publication, repository writes, or automatic deployment. Requires owner approval before live activation.

1. Install Wrangler locally or use Cloudflare dashboard. Deploy from this directory with npx wrangler deploy after reviewing the code.
2. Configure Worker secrets with wrangler secret put (not public variables): TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID (numeric Telegram user ID), TELEGRAM_WEBHOOK_SECRET (random 32+ characters from a secure generator), ROUTERAI_API_KEY (optional), EDITORIAL_PRIVATE_TOKEN (optional, preferably a separate read-only Contents token for the private review repo).
3. GitHub Actions secrets do not automatically become Worker secrets. Never paste tokens into chat, source code, URLs in a browser, or logs.
4. Register the deployed HTTPS /telegram endpoint using Telegram setWebhook with secret_token equal to TELEGRAM_WEBHOOK_SECRET, allowed_updates ["message"]. Use local protected environment variables or a trusted deployment process. Do not enable webhook until owner ID and secret are set. Verify /health, then /start from owner's private chat.
5. Run node --test worker.test.mjs locally. Never rerun paid editorial tests from this bot.

Current limitations: /status reports mode only, /models lists intended models, /expenses is not connected, /articles and /tests list private result directories (not completed article content). No automatic notifications or controls yet. Future commands should use a durable authorization/approval and budget layer, not direct LLM execution. Telegram delivery can duplicate read-only replies on webhook retries.
