# Open Wearables — Lane 20 promotion queue

> **Project-local queue (October 16, 2026).** The downstream fork has GitHub Issues disabled, so this file replaces the legacy [OSS Factory #80](https://github.com/kvnloo/oss-factory/issues/80). Original upstream issues and contributor attribution remain primary evidence. This coordination page does **not** grant an upstream write, claim implementation ownership, or assert use of any person's wearable data.

## Ready for human origin review

**None verified.** Original reporters/maintainers own the open work below. [Factory PostgreSQL QA #90](https://github.com/kvnloo/oss-factory/pull/90) passed its narrow real SQL test but is not an Open Wearables production fix.

## Existing upstream review / owner-covered

| Priority | Source and owners | Status and next gate |
| --- | --- | --- |
| P0 | [Apple Health XML timezone issue #1712](https://github.com/the-momentum/open-wearables/issues/1712), reporter **kiranshivaraju**, and [iOS SDK issue #1779](https://github.com/the-momentum/open-wearables/issues/1779), reporter **strulock** | **EXTERNAL_OWNED/CHECK:** original maintainer coordination first. Synthetic actual XML/SDK → database → daily API test to verify supported timezone rule; do not duplicate owner code or assume an arbitrary timezone for missing offsets. |
| P0 | [Unit normalization #1125](https://github.com/the-momentum/open-wearables/issues/1125), reporter **knowald**, with **MichalZak17 / kiranshivaraju** contributions | **EXTERNAL_OWNED/CHECK:** metric and source-unit normalization, including affine Fahrenheit→Celsius conversion; read existing owner design and tests before contributing. |
| P1 | [Mixed-source rollup #1749](https://github.com/the-momentum/open-wearables/issues/1749), reporter **guilhermeshibuya-namu** | **CHECK:** validate distinct device-source choice/merge requirement and API behavior, not just source inspection. |
| P1 | [Sleep-lock false success #1713](https://github.com/the-momentum/open-wearables/issues/1713), reporter **kiranshivaraju**; [factory evidence #91](https://github.com/kvnloo/oss-factory/issues/91) | **OWNER_COVERED / LIMITED:** original reporter volunteered narrower fix; rare/unobserved in production, no real-device incident established. Verify Redis lock failure, status semantics and partial-payload idempotency. |
| P0 | [Upstream PR #1858 GPS precision](https://github.com/the-momentum/open-wearables/pull/1858), head `1d32ce93eb5ffe6b581ccf065b20d3ec6c9fc25a` | **ORIGIN_LIVE / HOLD:** already maintainer-owned upstream work with passing checks but two unresolved CodeRabbit review threads: archive column remains constrained and downgrade can round/delete precision. Original PR author owns the code. Do not create a competing PR; support via source-only independent QA if useful. |

## Fork-local QA and next proof

- [Factory synthetic PostgreSQL local-day PR #90](https://github.com/kvnloo/oss-factory/pull/90), head `72f1f43bb6b35cbf154616af7fd601d92637827a`: [hosted PostgreSQL 16 test](https://github.com/kvnloo/oss-factory/actions/runs/38447368844) passed five fabricated offset cases, proving **only** the SQL date bucket expression when offsets exist. XML importer upstream still needs to preserve offsets and prove real import API semantics.
- No actual live consumer's Health XML, biomarker records or personally identifying data may enter this queue. No claims about clinical efficacy, water/energy gains, or wearable calibration.

## Attribution, licensing, scope and update rule

Reuse the original the-momentum maintainers' supported SDK/XML importer, ORM, and integration patterns. Seek core-team confirmation before an origin-facing contribution and obtain **explicit scoped human approval** under [current factory HITL](https://github.com/kvnloo/oss-factory/blob/main/factory/HITL.md). Linear has been retired. No duplication of already-assigned issue fixes or PR #1858. Keep human reporters, authors and reviewers linked for credit without tagging them.

Before each wave, re-read this file and the exact upstream PR/issue heads/reviews and fork worker claims. Only update a row when source, tests, ownership or readiness changes. The legacy [factory #80](https://github.com/kvnloo/oss-factory/issues/80) remains an archival citation; future updates belong **here** in the Open Wearables fork. [Factory #14](https://github.com/kvnloo/oss-factory/issues/14) is a repository directory, not a work queue.
