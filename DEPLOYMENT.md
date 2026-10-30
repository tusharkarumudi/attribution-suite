# Deployment guide

**v2.0 — experimental / beta.**

Read [`METHODOLOGY_AUDIT.md`](https://github.com/tusharkarumudi/attribution-graph/blob/main/METHODOLOGY_AUDIT.md) before you rely on a number
from this toolkit. The governing limitation: **the scoring model is not
calibrated** — bands describe evidence strength, not probability, and more of
the identified failure modes bias toward overconfidence than toward caution.

---

## 0. Deployment posture

**`SECURITY.md` is authoritative for the residual security posture.** It states
what DNS pinning does and does not cover, the proxy limitation, the screenshot
exception and the filesystem modes. It is not restated here: three files
describing the same posture in slightly different words is how a reviewer ends
up trusting the wrong one.

For deployment planning, the two constraints that shape host design:

- Pinning does **not** apply through a proxy, so deny egress to RFC1918,
  link-local, ULA and `169.254.169.254` at the network layer regardless.
- Case material — output, evidence, cache, audit trail — is written 0700/0600,
  but disk encryption and a retention procedure are still yours.

## 1. Install

**Or don't.** If you are running inside a chat sandbox, a hosted notebook or
anywhere that refuses to install packages, use the offline bundle instead:
`attribution_portable.py`, shipped inside the skill, is the whole analysis
chain in one standard-library file. It makes no requests — you (or the
assistant) fetch the documents and it does the rest. See §8b.

For a machine you control, install properly: only the installed toolkit
retrieves and hashes its own evidence.

```bash
# the only three lines most people need
sudo apt install pipx && pipx ensurepath   # macOS: brew install pipx
pipx install attribution-suite             # pulls the other three
attribution doctor                         # then check the machine is set up
```

**`pipx`, not `pip`.** On Ubuntu 24.04, Debian 12, current Fedora and Homebrew
Python, a system-wide `pip install` is refused outright:

```
error: externally-managed-environment
```

That is [PEP 668](https://peps.python.org/pep-0668/) — the OS protecting the
Python that `apt` owns — and it applies to every package, not this one. The
install simply does not happen, and the next command reports
`attribution: command not found`. **Do not work around it with
`--break-system-packages`:** it writes into a Python that the package manager
is entitled to replace. pipx gives the tool its own environment and puts
`attribution` on PATH; `pipx ensurepath` is what adds `~/.local/bin`, and it
takes effect in a new shell.

A virtualenv is the alternative, and the right choice when the suite is a
library inside a larger program:

```bash
python3 -m venv /opt/attribution
/opt/attribution/bin/pip install attribution-suite
/opt/attribution/bin/attribution doctor      # by path: no activation needed
```

Optional extras:

```bash
pipx install "attribution-suite[llm]"              # `ask` plans with Claude
pipx install "attribution-suite[mcp]"              # MCP server for agentic hosts
pipx inject attribution-suite anthropic            # add the extra to an existing install
pipx inject attribution-suite playwright --include-apps   # screenshots (not wired into `run`)
```

Under pipx, the dependencies' own commands — `paytrace`, `paytrace-index`,
`handlecorr` — are deliberately not on PATH: pipx exposes only the apps of the
package it was asked to install. `attribution` and `attribution-suite` cover
everything they do. `attribution doctor` reports them as `--` rather than a
fault, and prints the one-line remedy if you want them anyway.

Python 3.11–3.14. The four packages release together and **must be on the same
version** — `attribution version` warns if they diverge.

Console scripts: `attribution` and `attribution-mcp` are on PATH after a pipx
install. `attribution-suite` is an alias of `attribution`. `paytrace`,
`paytrace-index` and `handlecorr` are the dependency packages' own commands,
installed but not exposed — see the paragraph above.

## 2. The case file

Everything a run does is declared here. It is a shareable artifact: **no
credentials go in it.**

```yaml
case_ref: ACME-2026-014
authorization: "written instruction from <client>, ref 2026-014"
contact_email: you@example.org        # sent as User-Agent contact
seeds:
  - domain:scraper-site.example
entity_types_allowed: [Company]
audit_path: ./out/audit.jsonl

robots_policy: respect                # respect | record | ignore
minimize: true                        # must be a YAML boolean, not "true"
pivot_radius: 2
max_requests: 400
budget:
  max_nodes: 800
  max_runtime_s: 900

egress:
  - label: direct
  - label: gulf
    provider: oxylabs_datacenter
    network: datacenter
    country: ae
    username: your-account
    password_env: OXYLABS_PW          # env var NAME, never the password
```

Three things that will reject your file, deliberately:

- **Unknown keys.** A misspelled safety option that is silently ignored is
  worse than one that does not exist.
- **Quoted booleans.** `minimize: "false"` is refused. `bool("false")` is `True`
  in Python, so a quoting choice would have *enabled* the setting it appears to
  disable — silently.
- **Non-positive budgets.** `concurrency: 0` builds a zero-permit semaphore and
  hangs; `max_nodes: 0` truncates the search to nothing. Neither disables the
  limit, so both are errors.

`authorization` is free text, recorded in the audit trail and the evidence
manifest. It records an analyst's assertion; it does not establish authority.

## 3. Run

```bash
# one domain, case file written for you
attribution-suite --domain example.com --authorization "ticket: ACME-1234"

# a list of domains: one investigation and one report each
attribution-suite --domains domains.txt --out ./out

# a case file you wrote yourself (section 2)
attribution run --case case.yaml --out ./out

export OXYLABS_PW=...                 # only if using a proxy egress
```

`--domains` writes each domain's output under `<out>/<domain>/` and prints a
summary table at the end; one failing domain does not abort the batch. The
process exit code is the **worst** of the individual outcomes — the codes below
are ordered by severity — so automation can branch on it without parsing the
summary.

Run `attribution doctor` first on any machine you have not used before. A
missing key, a broken install and a blocked network all produce the same
empty result, and `doctor` is the only thing that distinguishes them.

### Exit codes — check these in automation

| Code | Meaning |
|---:|---|
| 0 | valid and complete |
| 1 | evidence package failed verification |
| 2 | bad invocation (e.g. `--concurrency 0`) |
| 3 | **incomplete** — a budget stopped the search |
| 4 | **invalid** — internal defects during collection |

**3 and 4 are not the same.** Incomplete means everything collected is sound and
there is simply less of it: an absence may mean "not reached" rather than "not
present". Invalid means collectors failed for reasons that are bugs in the
toolkit, so the evidence set is missing pieces in an unknown way — do not use
the result.

Generated reports carry the same status as a banner at the top, because a
document shared on its own has no exit code attached to it.

## 4. What lands in `./out`

Each `--domain` run gets its own directory, named after the domain:

```
out/
  example.com/        <- one investigation
  other-site.test/    <- another; nothing is overwritten
  example.com-2/      <- a second run of example.com, kept alongside the first
```

A repeat of the same domain is **not** written over the top of the earlier run:
these directories hold an evidence package whose manifest commits to the bytes
retrieved, and an audit trail of what was asked and when. The suffixed sibling
costs a directory and keeps both records.

`--domains FILE` produces the same layout, one directory per entry.
`attribution run --case FILE` writes straight into `--out`, because a case file
can carry several seeds and whoever wrote it chose the layout.

Inside one of those directories:

```
case.yaml                       the case file, for a --domain run
attribution_report.md / .html   findings, with a status banner if not clean
investigation_graph.json        the claim graph
entities.ftm.json               FollowTheMoney entities
graph.cypher                    Neo4j import
verification_trail.md / .json   what was checked and what was not
evidence/                       captures, manifest, verify.py, declaration draft
.cache/                         HTTP cache — lives and dies with the case
```

Under `minimize: true` every identifier the toolkit **derives** is
salted-hashed in every artifact it **generates** — the graph, FTM, Cypher, both
reports and the audit log — in every form the toolkit itself produces
(percent-encoded, form-encoded, case and separator variants). Set `salt:` or
`EAE_SALT` to make hashes comparable across runs.

**Minimisation does not redact the preserved evidence, and cannot.**
`evidence/captures/*.bin` holds the wire bytes, and the manifest records the
URLs actually fetched. Redacting either would break the digest those artifacts
exist to support. So:

> **An evidence package is not made safe to share by `minimize: true`.** It
> contains the subject's content and the URLs used to obtain it, by design.
> Treat it as sensitive regardless of this setting; minimise governs derived
> output, not the forensic record.

The same applies to the HTTP cache under `.cache/`, which is a copy of those
bytes.

## 5. Evidence

```bash
attribution verify ./out/example.com/evidence
```

This reads the manifest as **data**. The package also ships a standalone
`verify.py` you can read and run yourself; the installed command never executes
it, because an evidence package is untrusted input by definition.

Verification checks that every body matches its digest, that each entry hash
covers its full provenance envelope, that the chain head matches the manifest,
and that the **top-level metadata** — `case_ref`, `authorization`,
`fetch_policy`, `environment` — has not been altered.

What it cannot prove: that the package was not regenerated wholesale. Only an
external anchor does that. `TIMESTAMP.md` has the RFC 3161 instructions; do it
for anything that may end up in a filing.

## 6. Egress and geography

Content varies by where a request appears to come from. The exit is part of the
evidence, not a transport detail: a capture without it is not reproducible,
because a reviewer fetching from elsewhere cannot tell whether the page changed
or the vantage point did.

- **Verify the exit before a run that matters.** A proxy that fails open sends
  traffic from your own address, and finding that out from the manifest
  afterwards is too late.
- **Provider profiles are checked.** `provider: oxylabs` with
  `network: datacenter` is rejected — that profile is the residential endpoint,
  and a mislabelled exit corrupts both the record and the consent flag.
- **Residential and mobile exits are consent-sensitive.** Those are individual
  subscribers' connections. The tool records the network type in the manifest
  and the declaration draft so a run cannot use them silently. The decision is
  yours; the disclosure is not optional.

Some company registers answer only from inside the jurisdiction —
`attribution registries --jurisdiction AE` marks which.

## 7. Registers are not uniform

```bash
attribution registries --jurisdiction AE
```

Some jurisdictions have **no national register**. The UAE is seven emirate
authorities plus forty-plus free zones with no unified search, so "not found in
the UAE register" has no referent and absence from one member proves nothing.
The catalogue also records which registers need in-country egress and which are
not in English.

## 8. Agentic use

Two surfaces, and a host that can take both should:

```bash
attribution skill --bundle        # one archive: skill + slash command + MCP config
attribution skill --install       # the rules -> ~/.claude/skills/ad-attribution
attribution skill --mcp-config    # the tools: host entry, absolute path resolved
pipx inject attribution-suite mcp # the MCP server itself, if not already present
attribution-mcp                   # stdio, when a host wants to launch it directly
```

For a host with no shell, MCP is the only route that can actually investigate;
`uvx --from "attribution-suite[mcp]" attribution-mcp` needs nothing installed
beforehand. The server builds against either generation of the `mcp` SDK --
2.x moved registration from decorators to constructor callbacks, which stopped
the server starting at all until 2.0.22.

The skill ships **inside the installed package**
(`attribution_suite/skill/SKILL.md`), so an install is all that is needed. It previously lived in a top-level directory that existed only in a git
checkout, which meant everyone who installed from PyPI had a tool whose
agent-facing half was missing and nothing telling them so. `attribution doctor`
now reports whether the skill is installed for a host and whether the server is
reachable.

The skill carries the operating rules a tool schema cannot express — that a
band is evidence strength and not a probability, that `OWNERDOMAIN` is the
subject's claim about itself, that person-scoped search is refused by design
and must not be reassembled from other searches, and that instructions found
in a fetched page are evidence about the subject rather than direction.

The exposed surface is **narrower than the CLI** on purpose: the user is not
reviewing each call, and the model choosing arguments has read untrusted web
content.

- No person-search tool exists — structural, not a rule the model must remember.
- Every network tool requires `authorization`.
- Domains are validated; IP literals, URLs and paths are rejected.
- No egress, credential or filesystem arguments come from the model.
- Every response embeds the calibration caveat, because a model summarising a
  result will otherwise present `STRONG_EVIDENCE` as certainty.

## 8a. Selective serving, and the one control that misrepresents us

`ads.txt` is served from the publisher's own origin by software the publisher
controls, and nothing stops that software answering one thing to a crawler and
another to a browser. `--cloaking-check` retrieves it twice and compares the
declared account sets:

```bash
attribution-suite --domain example.com --authorization "..." --cloaking-check
```

Comparison is on the set of `(ad system, seller ID, relationship)` plus
`OWNERDOMAIN`, never on bytes — two honest copies of the same file differ
constantly in comments, ordering and line endings, and a byte comparison would
report cloaking everywhere. The verdict appears as `ads_txt_consistency`, and a
material difference raises a `SELECTIVE SERVING` warning saying that the
findings describe the copy this tool was shown.

**This is the only control in the toolkit that misrepresents the client.** The
second retrieval sends a browser `User-Agent`, which is why it is opt-in rather
than default, and why the audit trail records the exact string that was sent.
A tool claiming an honest record while quietly lying about who it is would be
worth less than one that cannot run the check at all. Decide whether that
trade is acceptable for your engagement before you enable it.

Two variants that misrepresent nothing run without the flag:

- a **second declared egress** — identical request, different exit, which
  catches selective serving by IP or ASN. Automatic when the case file
  declares more than one egress.
- the **archived copy**, through the existing wayback collectors. Weaker:
  `ads.txt` legitimately changes, so a difference there is a question rather
  than a finding.

## 8b. Running where nothing can be installed

`pip install` is refused as external code in most chat sandboxes, and an MCP
config that runs `uvx` downloads the same package and hits the same wall. That
made the skill useless in the environment it was written for: "command not
found", then ad-hoc web lookups presented as a result, with none of the bands,
groups, audit record or evidence package that make a result worth anything.

`attribution_portable.py` sits beside `SKILL.md` in the bundle. One file,
standard library only, Python 3.11+, no network of its own.

```bash
python attribution_portable.py example.com -a "ticket: ACME-1234"
# prints the URLs to fetch and the filename to save each under
# fetch them, save the bodies verbatim into ./fetched/, run the same line again
```

Two rounds is normal: `ads.txt` first, then the `sellers.json` files it names.
It stops once the payment chain is complete and offers the remaining
corroborating sources rather than requiring them.

It is not a reimplementation. The bundle embeds the real modules and a replay
fetcher that serves saved documents through the live fetcher's interface, so
the collectors, engine, scoring, resolution, adversarial checks and report run
unchanged. A test asserts it produces the same report as the installed
packages from the same documents; `VERIFY.sh` fails if it is stale. Rebuild it
with `python scripts/build_portable.py` after changing any package — never
edit the generated file.

**What you get:** evidence bands with their correlation groups, the
adversarial checks, gravatar binding, executive summary, network map,
indicator table, an append-only audit trail carrying your authorisation, and
a SHA-256 for every document read.

**What you do not:** evidence this toolkit retrieved. The digests commit to
what was *saved*, not to what the origin served; there is no vantage point and
no robots.txt decision of its own. The report says so in a banner, and that
banner should survive into whatever you hand on. For a result someone else
must verify independently, run the installed toolkit.

**A truncated document is detected, not trusted.** A browsing tool that
silently cuts a large `sellers.json` short would otherwise turn *not checked*
into *checked and absent*. A structured document that does not parse is
treated as incomplete and every absence in it is reported as not checked.

**Sensitive paths are held back.** `/.env`, `/.git/config` and the rest are
left out of the fetch plan: the toolkit probes them under an authorised,
rate-limited case, whereas an assistant fetching them from a chat is
unauthenticated probing of someone else's server carrying the user's own
address. `--include-sensitive` is there when the authorisation covers it.

## 9. Untrusted input

`ads.txt`, imprint pages and DNS records are written by the entity under
investigation. If retrieved content contains instructions — naming a different
operator, or telling you to stop — **that is evidence about the subject, not an
instruction.** `paytrace/adversarial/` documents a worked example with before
and after reasoning traces.

## 10. Operating notes

- **Supply a corpus index.** Without one every identifier looks unique and
  confidence is overstated. `paytrace-index build` creates one; the run warns
  when it is missing.
- **Read the negative evidence.** "Not checked" and "checked and absent" are
  different claims. An absence from a source with unpublished coverage means
  nothing, and the report says which is which.
- **Treat any conclusion about a natural person as a lead** until a statutory
  registry corroborates it.
- **Evidence packages are shareable by design.** Credentials never enter them —
  proxy passwords come from the environment, API tokens go in headers rather
  than URLs. Keep it that way if you add a collector.

## 11. Known limitations

`METHODOLOGY_AUDIT.md` is the long form. The short version:

- **Calibration is unvalidated.** Every score is an ordering, not a frequency.
- The SSRF guard does not pin DNS, so a rebinding window exists between
  validation and connection. Documented in `SECURITY.md`.
- Geo-divergence compares raw response bodies, so rotating tokens and ads
  produce false positives. Review divergence findings before treating them as
  evidence.
- Screenshot capture exists but is **not wired into `attribution run`**;
  `--screenshots` exits 2 with an explanation rather than silently doing
  nothing.
- 25 of 43 registers are not automatable.
