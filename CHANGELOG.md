# Changelog

## [Unreleased]

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
