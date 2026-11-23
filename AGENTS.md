# AGENTS.md — Дзен — ИИ-редакция (Codex / coding agents)

This repository is the **existing** editorial pipeline, not a new website. Read `docs/EDITORIAL_AUTOMATION.md`, the latest owner instructions, `docs/EDITORIAL_AGENT_V1_MODEL_A_20261120.md`, `config/ai_revision_policy.json`, `config/agent_learning.json` before editing.

## Operating rules

- The owner has three channels; only `legends` and `science` are currently active for drafts. `cinema` stays paused.
- Do not change `publication_enabled=false`, publish content, activate RSS delivery, or remove release holds without an explicit owner launch instruction. A successful check is not publication authorization.
- Articles: natural original Russian prose, at least 3000 characters and normally no more than 6000 characters, excluding all whitespace (Unicode `\s`); up to 7000 characters only if strictly necessary with a recorded nonempty `length_exception_reason`, meaningful source checks, original channel CTA, usually 7–8 verified rights-cleared illustrations.
- **Mandatory Dzen layout, owner order 23 Nov 2026, for all 3 channels:** title → table of contents → strongest cover → short intro → for every following section: **bold heading → immediately matching picture → prose**. Use 6–7 sections and 7–8 DISTINCT images, exactly one separate cover plus one per section; no image duplication, missing section images, text-only gaps, empty spacer paragraphs or decorative blank spacing. Select cover for semantic relevance and strength; all assets still require visual/rights/provenance checks. Fail closed if the layout is incomplete. The agent must check actual HTML/DOCX/Markdown order and re-check after pasting into Dzen. This is a requirement for new drafts, not retroactive publication authorization of old posts.
- The current diagnostic Model A is `modernbert + lightgbm + transformer`; the reported index is the arithmetic mean of three 0–100 classifier scores. `ai_accent_detector` remains a separate style score. The **owner-selected review flag** is strictly >80 on this **uncalibrated index**, NOT 80% of AI authorship. Never claim detector scores prove origin or optimize prose to evade classifiers.
- Up to five genuine editorial-agent revisions, followed by at most two ChatGPT revisions through a connected transport. Every attempt needs an actual text change, immutable SHA-256, a documented substantive issue, and complete fresh checks. If unresolved, escalate to the owner. Do not falsely claim ChatGPT handoff or user delivery: these transports are not yet fully integrated.

- Mandatory for ALL THREE genres including paused `cinema` test drafts: article body must be at least 3,000 characters and normally at most 6,000, with an exceptional maximum of 7,000 only when needed and justified in a nonempty `length_exception_reason` field; check after each real revision and before any release. Under 3,000, above 7,000, or 6,001–7,000 without documented justification is a blocking content defect. Do not count HTML markup, captions, metadata or image URLs toward length.

## Self-learning feature (requested 20 November 2026)

Self-learning is **feedback-driven improvement**, not uncontrolled online model training. Configuration: `config/agent_learning.json`.

1. Gather verified reviewer feedback, concrete factual/style corrections, full revision provenance, and successful/failed quality checks into structured cases. Retain failures and never treat a ZeroGPT or Model A score as human-written ground truth.
2. Identify recurring editorial defects by genre; recommend improvements to drafting prompts, checking rules, source vetting, and image rights. Only use lawfully reusable, provenance-verified training samples; avoid contamination of independent holdouts.
3. Propose experiments in separate branches and demonstrate improvements on an untouched holdout with regression tests and a clear rollback plan. Present before/after evidence and uncertainty; human approval required to deploy new weights, prompts or thresholds.
4. Do not automatically retrain or swap classifier weights, autonomously change the >80 review threshold, create open-ended revision loops, or change publication policies. The learning runtime counts verified cases and evaluates candidate instructions against supplied independent holdout results. It does not train in the background.
5. Never store private data, credentials, unlicensed datasets or personal information in public repository files.

## Development expectations

Develop in a dedicated branch with clear tests and a pull request. Prefer free local CPU tools. Use fail-closed semantics for missing model scores, stale text hashes, unverified claims and rights. Avoid duplicate schedules at cutover. Do not publish drafts automatically. If the required integration for an action is unavailable, return a real `not_connected`/pending result rather than marking success.

Relevant implementation: `src/editorial_ai_check.py`, `src/editorial_agent_v1.py`, `src/editorial_auto_route.py`, `tests/test_editorial_agent_v1.py`.
