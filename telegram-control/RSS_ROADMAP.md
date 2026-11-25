# RSS integration — ZEN CONTROL

Owner requirement (2026-11-24): connect the existing RSS pipeline to ZEN CONTROL for automatic delivery to Yandex Zen. Reuse GitHub Pages, not a new website:
- https://kaokshi.github.io/feeds/cinema.xml — Кадр за Кадром
- https://kaokshi.github.io/feeds/legends.xml — Легенды тёмных улиц
- https://kaokshi.github.io/feeds/science.xml — За гранью очевидного

Source of truth: latest main config/site.json and config/ai_revision_policy.json, especially future_rss_delivery. Verified on 2026-11-24: publication_enabled=false, project_launched=false, currently_active=false.
Before explicit global owner launch: drafts and owner review only; no RSS transmission. Review decisions in the private journal never authorize launch or publication. After explicit global launch and publication flag enabled: automatic RSS entry for eligible active channels only, with all applicable checks passed, no unresolved substantive defects, required escalation completed, and current article SHA-256 verified. Existing policy does not require individual owner approval after launch.
Planned interface: live status of each feed, active channel status, publication gate, queue and delivery evidence, errors, emergency pause. Do not claim Zen ingestion from RSS availability alone; confirm via Zen's supported integration and status when connected.
Keep unpublished text/images in private storage. No public feed modifications, launch flags, Zen account changes or paid model calls from this development branch. Before enabling, confirm supported Zen RSS onboarding, test on a nonpublic fixture, and verify end-to-end with explicit owner authorization.
