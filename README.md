# attribution-suite

[![CI](https://github.com/tusharkarumudi/attribution-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/tusharkarumudi/attribution-suite/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/attribution-suite.svg)](https://pypi.org/project/attribution-suite/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

One install and one command: who is paid for a website, and on what evidence.

```bash
sudo apt install pipx          # macOS: brew install pipx
pipx install attribution-suite
attribution-suite --domain example.com
```

That writes the case file for you, follows `ads.txt` → `sellers.json` →
registries, prints the answer, and leaves a report and an evidence package in
`./out/<domain>/`, one directory per investigation so a second search never
overwrites the first. Nothing else to configure.

The output opens with the answer:

```
PAYEE — the ad system says it pays this party for this site
  adrelay.example/7kQmZ2bXr4TnWp8Ls
      declares domain  other-site.example  — a DIFFERENT site
      name             Example Medya Ltd.
      ads.txt says     DIRECT
  google.com/pub-0000000000000314
      name             Example Adtech LLC
      ads.txt says     RESELLER — this ad system RESELLS the inventory;
                       it is not the party being paid. The DIRECT line is.
```

**DIRECT names the seller; RESELLER names a reseller.** When a payout account
declares a *different* site, one account serves both — the strongest lead there
is, and that site's about page is the next step.

## Install

**`pipx`, not `pip`.** This is a command-line tool, so it wants its own
environment; pipx makes one, keeps the four packages on one version line, and
puts `attribution` on your PATH.

```bash
sudo apt install pipx          # Debian/Ubuntu.  macOS: brew install pipx
                               # Fedora: sudo dnf install pipx
pipx ensurepath                # puts ~/.local/bin on PATH — open a NEW shell after this
pipx install attribution-suite
attribution doctor
```

That one command installs all four packages — `attribution-graph`, `paytrace`,
`handle-correlation` and this one. `attribution doctor` lists their versions
under `VERSIONS`, which is how you confirm it.

What pipx does *not* do is put a dependency's own commands on PATH, so
`paytrace`, `paytrace-index` and `handlecorr` are absent after a normal
install. That is pipx working as designed, not a partial install: the packages
are there and importable, and `attribution` does everything those commands do.
`attribution doctor` marks them `--`, says the package is installed, and prints
the command that exposes them if you want them:

```bash
pipx inject attribution-suite paytrace handle-correlation --include-apps
```

### If `pip install` just failed

```
error: externally-managed-environment
× This environment is externally managed
```

That is the operating system refusing to let anything install into *its* Python
([PEP 668](https://peps.python.org/pep-0668/)). Ubuntu 24.04, Debian 12,
current Fedora and Homebrew Python all do it, to every package — nothing is
wrong with this one, and `attribution: command not found` afterwards simply
means the install never happened.

Use pipx above. **Do not pass `--break-system-packages`**: it writes into the
Python that `apt` manages, and the next system upgrade is entitled to break it.

### Or a virtualenv, if you prefer

```bash
python3 -m venv ~/.venvs/attribution
~/.venvs/attribution/bin/pip install attribution-suite
~/.venvs/attribution/bin/attribution-suite --domain example.com
```

Calling the venv's binary by path needs no activation. If you `activate`
instead, remember it lasts only for that shell — a new terminal gets
`command not found` again.

### Upgrading

```bash
pipx upgrade attribution-suite                     # pipx install
pip install -U --upgrade-strategy eager attribution-suite   # venv install
```

`--upgrade-strategy eager` is not optional in a venv: the suite requires
`paytrace>=2.0.1`, pip leaves a satisfied requirement alone, and you get a new
suite driving old collectors. `attribution version` must show all four on the
same `MAJOR.MINOR` line. (pipx re-resolves the whole environment, so it does
not have this problem.)

### Uninstalling

```bash
pipx uninstall attribution-suite      # or: rm -rf ~/.venvs/attribution
```

Cases, reports and the sellers.json cache live outside the install:
`./out` where you ran it, and `~/.cache/paytrace`.

### Check your setup first

```bash
attribution doctor
```

Versions, commands on PATH, optional API keys, cache state and one live request.
A missing key, a broken install and a blocked network otherwise all look the
same: an empty result.

### The options you will actually want

| | |
|---|---|
| `--domains FILE` | a list of domains; one report each under `<out>/<domain>/` |
| `--show-person` | show a person's name (masked by default — it is a lead, not a finding) |
| `--max-requests N` | request budget, default 800 |
| `--diagnostics` | list every blocked and checked URL |
| `--minimise` | hash identifiers, for a report you hand to someone else |
| `--authorization "ticket: 123"` | recorded in the audit trail; without it the run records itself as unattested |

`attribution-suite --help` groups the rest by purpose. Exit **3** means
INCOMPLETE — a budget or a block stopped the search — and the message says
which. It is not a failure.

### Optional: Claude planning, and dark web search

```bash
pipx install "attribution-suite[llm]"       # from scratch, with the extra
pipx inject attribution-suite anthropic     # or add it to an existing install
export ANTHROPIC_API_KEY=sk-ant-...         # model: ANTHROPIC_MODEL, default claude-sonnet-5
```

In a virtualenv: `pip install "attribution-suite[llm]"`.

Setting the key without the extra installed gets you fixed rules — the tool now
says so instead of falling back in silence. `attribution doctor` reports both.

### Driving it from an agent

```bash
attribution skill --bundle       # -> ad-attribution.zip, to attach or unzip
attribution skill --install      # or straight into ~/.claude/skills/
attribution skill --mcp-config   # or the MCP server entry, path resolved
```

`--bundle` writes one archive carrying the skill, a `/ad-attribution` slash
command, an MCP config and install instructions for each route. Attach it to a
host that accepts skill uploads, or unzip it into a skills directory.

**What travels and what does not:** the archive carries the method and the
rules for reporting a result. The investigation itself is a program that
fetches `ads.txt`, `sellers.json` and public registers, so an assistant with
no shell needs the MCP server to actually run anything. A chat that can run
nothing gets the method, not the capability.

The skill ships inside the package, so an install is all you need. It carries
what a tool schema cannot say: that a band is evidence strength and not a
probability, that `OWNERDOMAIN` is the subject's claim about itself, that
person-scoped search is refused by design and must not be reassembled from
other searches, and that instructions found inside a fetched page are evidence
about the subject rather than direction. `attribution doctor` reports whether
the skill is installed and whether the MCP server is reachable.

Dark web search runs [Robin](https://github.com/apurvsinghgautam/robin), a
separate tool that needs Tor and its own credentials:

```bash
attribution-suite --domain example.com --robin-query "example brand"
attribution run --case case.yaml --robin already-produced.json   # or import
```

If `robin` is not on PATH the run says so and continues without dark web
results. A search that quietly did not happen is worse than one that failed
out loud.

### First run downloads Google's sellers.json

104 MB, cached for a day and reused across every account. Do it before you need
an answer in a hurry. Move the cache with `PAYTRACE_SELLERS_CACHE`.

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
attribution verify     ./out/example.com/evidence
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

Needs a headless browser. It has to go into the same environment as the suite:

```bash
pipx inject attribution-suite playwright --include-apps && playwright install chromium
# venv: pip install playwright && playwright install chromium
```

Without one the run records that a screenshot was **not captured**, with the
reason — an absent screenshot is not a blank page.

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
