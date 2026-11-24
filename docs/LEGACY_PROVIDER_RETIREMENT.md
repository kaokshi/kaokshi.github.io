# Retirement of legacy editorial providers

Owner request, 2026-11-24: remove Groq and other unused inference routes without breaking the project.

Active target: RouterAI Qwen 3.6 Plus (planning), Claude Sonnet 5.5 (writing), DeepSeek V4 Pro (review), Seedream 5.0 Flash (illustrations, alongside licensed photos and accurate diagrams). PR #85 remains the independent validation branch. No production activation until validated.

This maintenance change disables the old editorial-production workflow's execution job and removes automatic Groq/Klein-era Cloudflare AI and Gemini quota schedules. Offline regression coverage and explicitly manual read-only quota diagnostics remain. It does not delete provider adapters, shared code, tests, private drafts, historical branches, tokens, Cloudflare Workers (ZEN CONTROL hosting), RSS, or quality detectors (CLIP, SigLIP, Florence-2, text QA). These are not interchangeable with generation models.

The old Python pipeline imports Groq budget utilities, calls Groq as fallback and Klein for image generation, so deleting adapters before the RouterAI production replacement is connected would break imports and tests. Full source removal is a second-stage change after the replacement and dependency audit. Never merge abandoned experimental PRs as a cleanup shortcut. The current main schedule and publication flags remain false; do not enable either as part of maintenance.

Rollback: revert the retirement commit/PR if required. A rollback is not authorization to start production.
