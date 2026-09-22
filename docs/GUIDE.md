# attribution-suite — reference guide

In-depth reference for the method. The [README](../README.md) covers
what the package does and how to start.

## What `run` does, in order

1. Import claims from SpiderFoot / OpenCTI / Robin — seeds the frontier
2. Collect from registries and ad-tech sources
3. Adversarial checks — a planted identifier never reaches the model at full weight
4. Score and resolve
5. Verify the evidence package
6. Report

**Ordering is fixed and load-bearing.** `attribution run` **exits non-zero if
evidence verification fails**, so a scripted caller can detect an unpresentable
result rather than publishing it.

Robin handles feed the handle-correlation pass automatically.
`.onion`-derived claims raise a warning: no archive, no preserved body, so they
cannot be verified after the fact by anyone.

---

## Case file

```yaml
case_ref: SCRAPE-2026-0417
authorization: "IR ticket SEC-88213 / preservation request 2026-08-02"
contact_email: "threatintel@example.com"

seeds:
  - domain:scraper-site.example
  - seller_id:pubmatic.com/156423

pivot_radius: 3
entity_types_allowed: [Company]
robots_policy: record          # respect | record | ignore
minimize: true
retention_days: 180
```

`authorization` is mandatory. "self-directed research" is a legitimate value —
the point is that it is stated and recorded.

---

## Outputs

| File | Contents |
|---|---|
| `attribution_report.md` / `.html` | Findings, evidence-strength bands, source terms |
| `verification_trail.md` | Ordered timestamped steps, numbered citations |
| `investigation_graph.json` | Every claim with provenance |
| `entities.ftm.json` | FollowTheMoney — yente / Aleph |
| `graph.cypher` | Neo4j |
| `evidence/evidence_manifest.json` | Hash-chained capture record |
| `evidence/verify.py` | Standalone checker, no dependencies |
| `evidence/DECLARATION_DRAFT.md` | Qualified-person certification skeleton |

---

## Why four packages

`attribution-graph` performs no network I/O — that is what makes its scoring
auditable, since every number is a function of claims plus index counts.
Someone who wants only the scoring model should not have to install an HTTP
client. This package exists for when you want all of it.

| Package | Provides |
|---|---|
| [attribution-graph](https://github.com/tusharkarumudi/attribution-graph) | Inference core, calibration, evidence |
| [paytrace](https://github.com/tusharkarumudi/paytrace) | 25 collectors, corpus index, agent |
| [handle-correlation](https://github.com/tusharkarumudi/handle-correlation) | Same-actor scoring for handles |

Versions move together; the test suite asserts alignment.

---

## Collection policy

`robots_policy`: `respect`, `record` (default), `ignore`.

No silent enforcement. Whichever you pick is written into the evidence manifest
and the declaration draft — the question in review is never whether the tool
obeyed robots.txt, but whether you can state what your policy was.

Rarely binds in practice: RDAP, crt.sh, GLEIF, EDGAR, `sellers.json` and
`ads.txt` are all published for machine consumption.

---
