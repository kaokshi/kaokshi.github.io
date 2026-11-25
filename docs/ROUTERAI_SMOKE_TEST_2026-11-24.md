# One-shot RouterAI test — 24.11.2026

> Архив разового теста 24.11.2026. Команды этого workflow сняты с эксплуатации. [Действующая инструкция](EDITORIAL_PRODUCTION.md)

Owner confirmed 150 RUB on RouterAI and authorized one-time testing. This run is isolated from article production and does not authorize publishing.

- src/editorial_routerai_smoke.py reads GET /api/v1/credits with ROUTERAI_API_KEY and requires at least 30 RUB before any model call.
- Up to three short sequential calls: Qwen 3.6 Plus, Claude Sonnet 5.5, DeepSeek V4 Pro 0813. Each allows at most 250 visible output tokens; no tools or search.
- After each model call the script checks balance again. It stops if an individual call costs >3 RUB, or the session >10 RUB. This is NOT a guaranteed hard ceiling against unexpected backend billing.
- It stores sanitized model/cost data and synthetic outputs in seven-day GitHub Actions artifacts.
- .github/workflows/routerai-one-time-smoke.yml runs once as part of introducing the workflow on main; subsequent runs require explicit manual dispatch.
- Full two-article production remains disabled pending end-to-end cost validation, durable spending reservations, and model response checks.
- Production retains EDITORIAL_ROUTERAI_EDITOR_ALLOWED=false; config retains routerai_editor.enabled=false.

API /credits may occasionally be stale or return non-JSON, per RouterAI docs. On uncertainty the test must stop instead of guessing.