# attribution-suite

[![CI](https://github.com/tusharkarumudi/attribution-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/tusharkarumudi/attribution-suite/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/attribution-suite.svg)](https://pypi.org/project/attribution-suite/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

One install and one CLI over the attribution toolchain.

```bash
pip install attribution-suite
attribution run --case case.yaml --index paytrace.sqlite --out ./out
```

---

## Ask it a question

```bash
export ANTHROPIC_API_KEY=sk-ant-...
attribution ask "who operates scraper-site.example?" --index paytrace.sqlite
```

Routes across the toolchain by what you named — a domain, company, seller ID or
handle file — runs the right packages, and writes the report.

```
planner:  llm
packages: paytrace, attribution-graph
subject:  domain:scraper-site.example

CONCLUSION: Example Media Holdings Ltd
Source:     derived from the evidence graph
```

```python
from attribution_suite import ask
r = ask("who operates scraper-site.example?")
print(r.conclusion, r.packages_used, r.warnings)
```

**No API key needed.** Without one it falls back to a deterministic planner that
covers the common shapes. `--offline` uses fixtures and no network at all.

### What the model decides

Which tools to call, in what order. **Not** what the evidence means — scoring,
resolution and confidence bands come from `attribution-graph` and the model
cannot reach them.

An LLM asked to weigh evidence produces a fluent number with no error rate
behind it. An LLM asked to sequence tool calls is doing something it is good at,
and the part that has to be defensible stays in code.

### What it refuses

Name-keyed searches on private individuals, before any I/O — the refusal does
not depend on the model agreeing:

```
$ attribution ask "who is Jane Doe"
This searches entities — domains, companies, ad accounts, seller IDs. It does
not run name-keyed searches on individuals...
```

Officers and beneficial owners surfacing from a corporate registry are in scope.

Guards are inherited, not re-implemented: operator free text never reaches the
model, self-published claims about the publisher are demoted, required pivots
derive from the claim graph, and injection attempts are logged into the report.

## Commands

```bash
attribution ask        "who operates example.com?" [--index db] [--offline]
attribution run        --case case.yaml [--index paytrace.sqlite] [--handles h.csv]
                       [--spiderfoot scan.db] [--opencti bundle.json]
                       [--robin investigation.json] [--no-scores]
attribution verify     ./out/evidence
attribution version

attribution index build --domains tranco.txt --db paytrace.sqlite
attribution portfolio  scraper-site.example --index paytrace.sqlite --registrants
attribution handles    --observations handles.csv --corpus usernames.txt
attribution registries --jurisdiction IN
```

The last four delegate to the component CLIs.

---

## Further reading

The method in depth — What `run` does, in order, Case file, Outputs, Why four packages, Collection policy — is in
[docs/GUIDE.md](docs/GUIDE.md).

## Deploying

[DEPLOYMENT.md](DEPLOYMENT.md). Run investigations outside the repo:

```bash
mkdir -p ~/cases/CASE-001 && cd ~/cases/CASE-001
attribution run --case case.yaml --index ~/corpus/paytrace.sqlite --out .
```

## Screenshot evidence

> **Not wired into `attribution run`.** The flag exits 2 with an explanation.
> `ScreenshotCapturer` launches a browser that navigates the target directly,
> so its subresource requests do not pass through the URL policy that guards
> every other fetch — a second network stack outside the SSRF boundary. Do not
> point it at an untrusted target from a host with access to internal networks
> or cloud metadata.

```bash
# NOT available: `attribution run --screenshots` exits 2.
# Use attribution_graph.ScreenshotCapturer directly, understanding that it
# launches a browser OUTSIDE the SSRF boundary (see below).
```

Captures a rendering of each page alongside the wire bytes, records **where on
the page** each finding appeared, and writes a plain-text log.

```
[0001]
URL          https://viewer-site.example/
Captured at  2026-09-03T16:47:06+00:00
Status       captured
Image        screenshots/0001.png
SHA-256      6a8ede5f807e37a9ed9f57aa03d5808a...
Viewport     1440x900 (full page)
Renderer     playwright/chromium
Page title   ExampleViewer — Story Viewer
Renders      body sha256 9f3a2b1c...
Findings
  - in the footer, at (8, 140), matching `footer > div.contact > a`
    text: info@viewer-site.example
Chain        seq 1, prev (genesis)
Entry hash   4d8e1a...
```

**A screenshot is a rendering, not a capture.** The bytes on the wire are the
primary evidence and verify by hash; a screenshot is what one browser displayed
from them, once, at one viewport, possibly after JavaScript. It is recorded as a
derived artifact with its own hash chain and never offered as proof of what was
served. What it is good for: content that only exists after JavaScript runs,
*where* on the page a finding sat, and what a visitor would actually have seen.

Four locators per finding — CSS selector, DOM path, bounding box, surrounding
text — because each fails differently. Selectors break on reflow, coordinates
break on a viewport change, text survives both and says nothing about position.

Annotation is a **second image**. The unannotated capture is hashed first and is
the one to verify; a highlighted version is a separate derived file. An
annotated image with no original is an illustration, not evidence.

Needs a headless browser (`pip install playwright && playwright install
chromium`). Without one the run records that a screenshot was **not captured**,
with the reason — an absent screenshot is not a blank page.

Outputs: `SCREENSHOT_LOG.txt` (readable without this package) and
`screenshot_manifest.json`, both kept separate from `evidence_manifest.json` so
a rendering cannot be mistaken for a capture.

## Known limitations

`METHODOLOGY_AUDIT.md` is an adversarial read of this toolkit, written as if by
a reviewer with no stake in it. Read it before relying on a number.

The governing limitation: **calibration is unvalidated.** Every probability is a
defensible ordering, not a measured frequency. And of nine identified failure
modes, **four bias toward overconfidence and none bias low** — an asymmetry that
is a direct consequence of having nothing fitted to catch it.

## Status

See `CHANGELOG.md`. Pre-release. Without `--index`, confidence figures are upper bounds — the CLI warns.

---

## Author

**Tushar Karumudi** — [github.com/tusharkarumudi](https://github.com/tusharkarumudi)

## License

Copyright 2026 Tushar Karumudi.

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).

Cite via [CITATION.cff](CITATION.cff).
