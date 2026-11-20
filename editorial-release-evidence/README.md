# Evidence package for conditional automatic editorial routing

Do **not** commit invented test results, model guesses, or placeholder `completed` fields.
A real file must be named `editorial-release-evidence/<article-slug>.json` and relate to the
**exact current article**, including unchanged image bytes.

## Release policy, as authorized on 2026-11-20

- **Active now:** one NEW `legends` article and one NEW `science` article per day.
- **Paused:** `cinema`; no cinema auto-release even if all tests pass.
- Article `auto_release_candidate: true` is only allowed on genuinely new prepared drafts.
  Do not retrofit historic articles to force a release.
- **Aggregate editorial risk:** `0..100` provisional points. `<=20` passes numerical triage;
  `>20` requests owner review.
- **Hard gates:** all four AI reports completed; ruBERT maximum-window score `<=25%`,
  at least 3,000 non-whitespace characters, at least seven available and unique images,
  explicit traceable image licenses, grammar/style/links/image/RSS checks completed,
  no critical unresolved issue, AND verified original-fiction facts or science claim coverage.
- AI Accent Detector is a stylistic diagnostic, not a probability. ModernBERT and LightGBM
  share source training and cannot be averaged into a valid authorship probability.
  The risk index is a **local editorial sorting heuristic**, not a scientific estimate.
- Unavailable/failed/inconsistent source reports => owner review, **never** automatic release.
- Only a successful verified release transaction sets `publication_enabled:true` and
  deploys the site/RSS. Nothing is published on enabling the system or adding this README.
- Any explicit `release_hold:true`, channel pause, or source/document/image change
  requires a new evaluation. Every material carries a content-and-image SHA-256 fingerprint.
- The independent connection **Yandex Zen RSS import** has not been verified. RSS on GitHub
  becoming available does not mean automatic publication inside Zen has been confirmed.

## Required structure

The following is an **illustration of the field names only**, not a valid release report.
All actual `status`, `report_ref`, and hash fields must come from completed checks,
not from these examples.

```json
{
  "ai": {
    "status": "completed",
    "article_sha256": "<REAL_CURRENT_ARTICLE_HASH>",
    "report_ref": "<REAL_GITHUB_ACTION_RUN_URL>",
    "checks": {
      "rubert_v4": {"status": "completed", "score_percent": 12.3},
      "modernbert": {"status": "completed", "score_percent": 9.1},
      "lightgbm": {"status": "completed", "score_percent": 18.2},
      "ai_accent_detector": {"status": "completed", "score_percent": 6.3}
    }
  },
  "quality": {
    "status": "completed",
    "article_sha256": "<REAL_CURRENT_ARTICLE_HASH>",
    "report_ref": "<REAL_GITHUB_ACTION_RUN_URL>",
    "checks": {
      "language_tool": {"status": "completed"},
      "vale": {"status": "completed"},
      "links": {"status": "completed"},
      "image_similarity": {"status": "completed"},
      "rss_preview": {"status": "completed"}
    }
  },
  "factual": {
    "status": "completed",
    "article_sha256": "<REAL_CURRENT_ARTICLE_HASH>",
    "report_ref": "<REAL_EDITORIAL_EVIDENCE_URL>",
    "original_fiction_confirmed": true,
    "fiction_not_presented_as_fact": true
  },
  "style_warning_count": 0,
  "image_warning_count": 0,
  "source_warning_count": 0,
  "unresolved_critical_findings": []
}
```

For `science`, replace fiction flags with `claim_sources_checked:true`,
`no_unresolved_factual_contradictions:true` and `verified_claim_ids`, whose set
must **exactly match** the list of `claims[].id` in the article. Every claim has
at least one `source_urls` link to an actually read and relevant study/source.

Image rights must be stated *per image* under the article's
`image_sources[]`: `file`, `license`, `license_verified:true`,
`source_page:https://...`. Supported rights basis: `CC0-1.0`, `CC-BY-4.0`,
`CC-BY-SA-4.0`, `PUBLIC_DOMAIN`, `OWN_WORK`, `LICENSED`.
The checker cannot independently prove the claimed rights: record actual documentary evidence
and do not mark `license_verified:true` without it.

## Actual commands

```sh
python src/editorial_auto_route.py --slug legends-new-article
python src/editorial_auto_release.py --slug legends-new-article
# --apply only in CI after verifying real reports, never to bypass missing results
```

For new candidates GitHub Actions runs `.github/workflows/editorial-auto-route.yml`
and uses its own `--apply` only for genuinely evidenced results. The run uploads
routing JSON as a GitHub Actions artifact. If eligible it updates article release
metadata and global site settings in one commit, then dispatches the existing
GitHub Pages/RSS publisher. If not eligible **no source file or publication state
is changed**.

The daily 11:00 MSK assistant task is instructed to show only the articles requiring
a human decision, with specific blockers and real evidence; accepted automatic releases
should not be re-sent for approval.

## Why score 20 is provisional

No collection of ground-truth human evaluations currently exists to validate a
statistical auto-publication threshold. `20/100` was chosen as a **cautious initial
workflow policy**, not calculated from a claimed empirical distribution.
Compare at least 30 subsequent manually audited articles with automatic decisions
before tuning false negatives and the score weights. Hard gates are more important
than any aggregate number.
