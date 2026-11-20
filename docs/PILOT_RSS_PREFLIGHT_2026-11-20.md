# Полный пилот редакционной цепочки — 20.11.2026

Статус: **RSS preview validated; publication readiness blocked**. Запись в публичные RSS и развёртывание не выполнялись.

## Материалы
- `content/articles/legends-pilot-counting-stairs-20261120.json` — 4082 знака без пробелов.
- `content/articles/science-pilot-lake-nyos-20261120.json` — 4551 знак без пробелов.

## Фактические запуски
- Legends AI: https://github.com/kaokshi/kaokshi.github.io/actions/runs/44711059113
- Science AI: https://github.com/kaokshi/kaokshi.github.io/actions/runs/44711064239
- Legends editorial quality: https://github.com/kaokshi/kaokshi.github.io/actions/runs/44711059091
- Science editorial quality: https://github.com/kaokshi/kaokshi.github.io/actions/runs/44711064098
- Read-only in-memory RSS preview and publication freeze: https://github.com/kaokshi/kaokshi.github.io/actions/runs/44711783868

| Detector | Legends | Science |
|---|---:|---:|
| ruBERT v4 max-window | 83.3333 | 83.3333 |
| ModernBERT classifier output | 97.93 | 20.72 |
| LightGBM classifier output | 38.01 | 36.24 |
| AI Accent heuristic index | 7.30 | 7.73 |

All four detectors completed for both. They are **not** independent or calibrated probabilities; no score-only mandatory rewriting threshold exists.

LanguageTool in respective quality workflows: `completed`, 0 suggestions on each test article. Quality workflow success is not full legal/factual approval, and corpus-wide quality warning counts do not mean zero pilot-specific warnings.

## RSS
`tests/test_pilot_rss_preflight.py` performs two independent, in-memory RSS item serializations, validates XML, title/link and content for each test article. GitHub Actions passed 61 unit tests, including the two new RSS preflights. Normal site build reported `published: 0`, with all three generated RSS files structurally valid and each containing zero items. GitHub Pages deployment steps were skipped.

## Unresolved hard blockers
- Both drafts deliberately have zero images versus the release requirement of at least seven **distinct, licensed, accessible, source-tracked** images.
- Science article has an initial list of references, but no completed claim-by-claim verified factual evidence. URLs alone are insufficient.
- No version-linked full evidence package for each article: AI + quality + factual + rights, all tied to exact content/image SHA-256.
- Both drafts retain `status:draft`, `release_hold:true`, `publication_authorized:false`, `auto_release_candidate:false`.
- `config/ai_revision_policy.json` retains `status:awaiting_independent_validation` and genre thresholds `null`. Automatic release is intentionally blocked regardless of model values.
- No automatic revision cycle with a measured before/after result has been demonstrated. Do not claim complete editorial autonomy.
- Automatic Zen ingestion from public RSS has not been demonstrated.

## Verdict
The technical XML serialization step is **reproducible in a non-publishing dry-run**. Actual unattended publication **is not yet eligible**, and no public RSS was modified. Before enabling release, resolve blockers on fresh pilots, execute new model and editorial audits after every edit, and independently validate the proposed release criteria.
