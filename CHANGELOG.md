# Changelog

## [Unreleased]

## [2.0.10] - 2026-09-23

- A finding supported by one evidence group now reads UNCORROBORATED rather
  than UNSUPPORTED ("contradicted or unsupported"). A site's own ads.txt entry
  is not contradicted; it is simply not corroborated yet, and the stronger word
  made every finding look like nothing had been found.
- Completed checks that found nothing are listed separately from blocked
  retrievals and do not make a result incomplete.

## [2.0.9] - 2026-09-23

- Blocked retrievals now name the URL, not just the count. Two runs of the same
  site minutes apart gave different answers — one reached the file naming the
  payee, the other was blocked before fetching it — and nothing in the output
  said which URL was missing.

## [2.0.8] - 2026-09-23

- `PAYEE` falls back to the account the site declares in its own ads.txt when
  the ad system publishes no domain for that seller — common for individual
  sellers. Requiring the strongest signal hid a name the run had found.

## [2.0.7] - 2026-09-23

- New `PAYEE` section names the operator directly. An ads.txt lists dozens of
  DIRECT accounts, most of them networks whose sellers.json entry covers
  thousands of sites; the one that identifies the operator is the entry whose
  DECLARED DOMAIN is this site. That account, its seller_type and the name are
  shown first, with natural-person names masked and marked as leads.

## [2.0.6] - 2026-09-23

- Findings are ordered by distance from the seed, not by score. Ranking both
  hops together let seller-to-seller pairs outrank the site's own ads.txt
  declarations, so the seed's own findings vanished from the list.
- The second hop is shown only when it reaches a named party, under
  `WHO THOSE ACCOUNTS BELONG TO`. Two sellers sharing an ad system are peers,
  not a step toward whoever is paid.

## [2.0.5] - 2026-09-23

- Findings follow the chain two hops, so the payee name appears. The answer is
  domain -> seller_id -> org_name, and keeping only links that named the seed
  filed the payee itself under "other links".
- A link is labelled `[self-published]` when nothing INDEPENDENT connects the
  seed to the other side. A registry lookup of a name found on the subject's own
  page corroborates that the company exists, not that it operates the site —
  previously that counted as independent support, so "the terms page names Meta"
  was presented as STRONG_EVIDENCE that Meta operates the site.

## [2.0.4] - 2026-09-23

- Findings are now about the domain that was asked about. Every assessment in
  the graph was ranked together and the top five printed, so a run about one
  site reported links between unrelated third parties and never named the
  site's own payee. Links not involving the seed are counted, not listed.
- A link supported only by the subject's own pages is labelled
  `[self-published]`. An Instagram viewer's terms page names Meta, and that
  mention was being presented as STRONG_EVIDENCE that Meta operates the site.

## [2.0.3] - 2026-09-23

- The INCOMPLETE exit message now names what actually stopped the run. Four
  conditions set a result incomplete — blocked retrievals, the request budget,
  the wall-clock budget, the node budget — and all four reported "a budget
  stopped the search". A run halted by robots.txt sent the reader looking for a
  limit to raise when nothing had been near one.

## [2.0.2] - 2026-09-22

- New: `attribution-suite --domain example.com` runs the whole chain from one
  command, writing the case file for you and printing the findings. Equivalent
  to `attribution run --domain`. Defaults are modest — one pivot hop, a 200
  request budget — so a one-liner cannot start an unbounded crawl.
- `--domain` records `authorization` as unattested unless `--authorization` is
  given: the control exists so a human states their authority, and the
  convenience form must not invent one.
- Personal identifiers are masked in the on-screen summary (`--show-person` to
  display). Nothing is removed from the evidence or the graph.
- `attribution ask` no longer crashes on a live run. Every run without
  `--offline` failed at the pivot step with "asyncio.run() cannot be called from
  a running event loop". Only `--offline` had been tested.
- `attribution run` no longer crashes on a real network after collecting
  evidence ("Event loop is closed"). The crash came before any report was
  written.

## [2.0.1] - 2026-09-21

- Documentation, examples and test fixtures now use only placeholder data.
  2.0.0 has been withdrawn; upgrade to 2.0.1.
- Dependencies between the four packages now require `>=2.0.1,<2.1`.

## [0.2.0] - 2026-08-18

Initial release of the unification package.

### Added
- `attribution run` — full chain from one case file: import, collect, score,
  resolve, verify, report
- Passthrough commands for `index`, `portfolio`, `handles`, `registries`
- `attribution verify` and `attribution version`
- Non-zero exit when evidence verification fails, so scripted callers can detect
  an unpresentable result
