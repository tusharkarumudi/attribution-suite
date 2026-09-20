# Changelog

## [0.2.0] - 2026-08-18

Initial release of the unification package.

### Added
- `attribution run` — full chain from one case file: import, collect, score,
  resolve, verify, report
- Passthrough commands for `index`, `portfolio`, `handles`, `registries`
- `attribution verify` and `attribution version`
- Non-zero exit when evidence verification fails, so scripted callers can detect
  an unpresentable result
