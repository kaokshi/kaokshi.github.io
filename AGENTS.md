# AGENTS.md — Дзен — ИИ-редакция (Codex / coding agents)

This repository is the **existing** editorial pipeline, not a new website. Read `docs/EDITORIAL_AUTOMATION.md`, the latest owner instructions, `docs/EDITORIAL_AGENT_V1_MODEL_A_20261120.md`, `config/ai_revision_policy.json`, `config/agent_learning.json` before editing.

## Operating rules

- The owner has three channels; only `legends` and `science` are currently active for drafts. `cinema` stays paused.
- Do not change `publication_enabled=false`, publish content, activate RSS delivery, or remove release holds without an explicit owner launch instruction. A successful check is not publication authorization.
- Articles: natural original Russian prose, 3000–6000 visible body characters without whitespace, meaningful source checks, original channel CTA, usually 7–8 verified rights-cleared illustrations.
- The current diagnostic Model A is `modernbert + lightgbm + transformer`; the reported index is the arithmetic mean of three 0–100 classifier scores. `ai_accent_detector` remains a separate style score. The **owner-recommended nonbinding review flag** is strictly >85 on this **uncalibrated index**, NOT 80% of AI authorship. Never claim detector scores prove origin or optimize prose to evade classifiers.
- Up to two genuine editorial-agent revisions, followed by at most one ChatGPT revision. After a revision, a rising Model A index prepares a ChatGPT handoff; a plateau stops further score-trend editing. Decreases only justify continuing when concrete editorial defects remain. Bounded local failures also escalate to ChatGPT. The ChatGPT transport is not connected. Every attempt needs an actual text change, immutable SHA-256, a documented substantive issue, and complete fresh checks. If unresolved, escalate to the owner. Do not falsely claim ChatGPT handoff or user delivery: these transports are not yet fully integrated.

## Self-learning feature (requested 20 November 2026)

Self-learning is **feedback-driven improvement**, not uncontrolled online model training. Configuration: `config/agent_learning.json`.

1. Gather verified reviewer feedback, concrete factual/style corrections, full revision provenance, and successful/failed quality checks into structured cases. Retain failures and never treat a ZeroGPT or Model A score as human-written ground truth.
2. Identify recurring editorial defects by genre; recommend improvements to drafting prompts, checking rules, source vetting, and image rights. Only use lawfully reusable, provenance-verified training samples; avoid contamination of independent holdouts.
3. Propose experiments in separate branches and demonstrate improvements on an untouched holdout with regression tests and a clear rollback plan. Present before/after evidence and uncertainty; human approval required to deploy new weights, prompts or thresholds.
4. Do not automatically retrain or swap classifier weights, autonomously change the owner-recommended >85 review baseline, create open-ended revision loops, or change publication policies. `src/editorial_learning.py` collects owner-reviewed cases and proposes experiments on request or after a batch. Keep case data and feedback outside the repository.
5. Never store private data, credentials, unlicensed datasets or personal information in public repository files.

## Development expectations

Develop in a dedicated branch with clear tests and a pull request. Prefer free local CPU tools. Use fail-closed semantics for missing model scores, stale text hashes, unverified claims and rights. Do not alter existing schedules or publish drafts automatically. If the required integration for an action is unavailable, return a real `not_connected`/pending result rather than marking success.

Relevant implementation: `src/editorial_ai_check.py`, `src/editorial_agent_v1.py`, `src/editorial_auto_route.py`, `tests/test_editorial_agent_v1.py`.
