# Changelog

## [Unreleased]

## [2.0.25] - 2026-10-29

### Narrow requests, for the files a fetch tool will not return

A real investigation got one step from the answer and stopped: the ads.txt
named `MANAGERDOMAIN` and a DIRECT seller, so the payee was a single record in
that manager's `sellers.json` -- and the fetch tool would not return a file
that size. The whole-file route fails on exactly the files that carry the
answer.

Asking for one record is a short quote, which fetch tools do answer. So every
wanted `sellers.json` in the plan now carries a second route: fetch it whole,
or quote the record. The prompt is written out ready to paste and **names the
seller_id**, taken from the `adsystem/seller_id` identifiers the ads.txt
collector already recorded -- no cross-package plumbing, and Google's
off-domain file is mapped back to its ad system so that prompt names an id too.

What makes it sound is an asymmetry: **a quoted record proves the record is
there; only the whole file can prove one is absent.** So an extract can name
the payee and can never support "checked and not found" -- anything the
collectors recorded as a checked absence against a URL seen only in fragment
is demoted to a block, centrally, because the collectors were right to trust
what they were given.

- **New `## Payees named by the ad systems` section.** `sellers.json` names the
  payee and the collector records it, but with one self-published ads.txt
  behind it nothing reaches the merge threshold -- so "Resolved entities" was
  empty and the name appeared nowhere. A run with the answer in its graph
  reported "no legal entity named". It is now stated for what it is: a name a
  document gave, with its source, marked when it came from an extract, and a
  lead until a statutory register corroborates it.
- **A replay no longer reads the shared sellers.json cache.** `paytrace` keeps
  a day-long on-disk cache keyed by URL across every run on the machine --
  sound for live collection, wrong for a replay, where the supplied documents
  ARE the evidence and a cached copy from another case substitutes different
  bytes under a digest computed over the cache. Not hypothetical: a 9 KB
  fixture left there by an unrelated test surfaced as a 50-character
  placeholder company name in a live-looking report.
- **A web page returned for an ads.txt path is an absence, not a bad
  document.** A site with no `app-ads.txt` commonly answers that path with its
  own single-page app, which is the site saying the file does not exist -- a
  usable finding. Treating it as "not the document" marked an ordinary run
  INVALID.

### Authorisation must be the user's words

An assistant composed `"<Employer> ad-fraud investigation, case XYZ"` from
context and passed it as the authorisation. Nothing in the package contains
that string; it was inferred from what the assistant knew about the user. An
invented basis is worse than none: the audit trail reads as attested when
nothing was attested, and it writes an organisation's name into a record the
user did not author. The skill, the slash command and the MCP schema now say
so explicitly -- ask, never compose, and if they decline pass nothing and
report the run as unattested.

### Every search was writing into the same `out` folder

A second investigation overwrote the first one's report, evidence package and
audit trail, because everything landed directly in `--out`. Each `--domain` run
now gets `<out>/<domain>/`.

- **A repeat of the same domain is not written over the top of the earlier
  run.** It becomes `<domain>-2`, `-3` and so on. These directories hold an
  evidence package whose manifest commits to the bytes retrieved and an audit
  trail of what was asked and when; `secure_mkdir` passes `exist_ok=True` and
  the writes truncate, so replacing one in place destroyed a record somebody
  may already have been relying on, silently. A suffixed sibling costs a
  directory and keeps both. "Already used" means a run's output is there, so a
  directory someone pre-created, or one holding only the `case.yaml` this run
  just wrote, is still used as-is.
- **`--domain` is treated as user input, because it becomes a directory name.**
  It is reduced to a host and filtered to hostname characters, so
  `../../etc/passwd`, a Windows separator or drive letter, whitespace, shell
  metacharacters and a bidirectional override cannot reach a path. A pasted URL
  and a capitalised host resolve to the same directory as the bare domain.
- **One code path, named in one place.** `--domains FILE` already produced
  `<out>/<domain>/` by building the path itself, which left two places to keep
  in step and nothing at all for a single run. The batch now hands over the
  root and the single-run path names the directory, so both routes agree by
  construction; a test fails if the batch starts nesting the domain twice
  again.
- `attribution run --case FILE` still writes straight into `--out`. A case file
  can carry several seeds, so there is no one domain to name a directory after,
  and whoever wrote it chose the layout.

An empty `--domain` is refused where the other missing-argument cases are,
rather than becoming a directory called `domain`.

### A crash on any prefix the process may not read

Reported from a real machine: six tests failed with
`PermissionError: [Errno 13] ... /root/.local/share/pipx/venvs/attribution-suite/pipx_metadata.json`.

Two separate faults, and the second is the one that mattered.

**`install_mode()` could raise.** `Path.is_file()` does not swallow every
OSError -- pathlib ignores ENOENT, ENOTDIR, EBADF and ELOOP and re-raises the
rest -- so a prefix the process may not stat raised instead of answering "no
marker here". This is not confined to the doctor: `install_mode` is called from
`investigator` and `mcp_server`, so it would abort an investigation over a
directory the process was never entitled to read. A prefix that cannot be
stat'd now falls through to the path test, which touches no filesystem.

**The tests were never hermetic.** They pointed `sys.prefix` at the literal
`/root/.local/share/pipx/venvs/attribution-suite`. That is a real location: on
a machine where it exists and the test user cannot read it, the check raises.
They passed here only because this container runs as root. Every pipx case now
builds its prefix under `tmp_path`, and the path classifier is tested through
`_mode_from_path`, which is pure.

### The MCP sweep and another user's case directory

Found by running the suite as a non-root user, which is what the report above
prompted. `sweep_stale_cases` guarded `path.stat()` with `try/except OSError`
but left `path.is_dir()` and `(path / ".retain").exists()` outside it, one line
above -- and both stat. The temp directory is shared, so a single
`attribution-mcp-*` directory belonging to someone else on the same host
crashed the sweep, and the sweep runs at MCP server start.

The whole body is inside the guard now, and the sweep also skips directories it
does not own: even where permissions would allow it, deleting another user's
case directory is not this process's to do, and `ignore_errors=True` was hiding
the attempt while still counting it as removed.

### A correct install that read as a broken one

`pipx install attribution-suite` was reported to have "never installed
paytrace and handle-correlation and attribution graph". It had. All three are
ordinary dependencies and pipx installs every one of them; `attribution doctor`
lists their versions under VERSIONS, and all four modules import. What pipx
does not do is put a *dependency's* console scripts on PATH -- it exposes only
the apps of the package it was asked about -- so `paytrace`, `paytrace-index`
and `handlecorr` are absent after a normal install, by design.

The doctor printed that as a bare `NOT ON PATH` in a list that reads like a
list of packages, so the only reasonable conclusion was that the install had
failed, and the remedy was to inject packages that were already there. The
install was never the problem; the report was.

- **The three dependency commands now read `--`, with the reason.** "command
  not exposed by pipx (the package IS installed -- see VERSIONS)", followed by
  a short paragraph on why and the one command that exposes them anyway. They
  no longer count against the exit code on a pipx install.
- **Every remedy is collected into one `TO FIX -- copy and run` block** at the
  end, each command with the reason beside it, under the detected install
  method. Scattered one per section, the reader had to assemble them -- and the
  line that mattered most was the one that looked like a failure.
- **`--` and `x` now mean what the legend says.** The anthropic SDK line
  printed `x` while the sentence under it called the fixed-rule planner "a
  supported mode, not a failure", and the legend promises `x` blocks a run.
  The marker follows the key: `--` with no key set, `x` when a key is set and
  the SDK cannot be imported, which is a real inconsistency.
- **One check per fact.** The SDK was detected twice -- a `find_spec` for the
  remedy and a real `import` for the marker -- which disagree for a package
  that is present but broken. The import is what the runtime does, so the
  import decides both.
- **MCP is reported as its two separate parts**, because they fail separately
  and have different remedies: the `mcp` SDK (a library) and the
  `attribution-mcp` launcher (a console script). The offline bundle is now
  listed too, so its absence from an install is visible rather than inferred.
- **A stale host copy of the skill is detected.** `skill --install` copies the
  directory and an upgrade never revisits the copy, so a host can keep reading
  rules several releases old -- and the symptom is a model applying superseded
  rules, which looks like a model fault. Nothing in the copy records a version,
  so the content is digested and compared against the shipped skill; a
  mismatch reports `STALE` and the `--force` command that refreshes it.
- **The remedy is withheld once it has been applied.** The first version of
  this gated on pipx's `include_dependencies` flag, which is set by
  `pipx install --include-deps` and stays False after the
  `pipx inject --include-apps` that doctor itself prints -- so anyone who ran
  the printed command was told to run it again, with all three commands
  reported `ok` three lines above. Found by running the printed command
  against a real pipx install. What is on PATH now decides, because that is
  what the advice is about and it is directly observable.
- **`install_mode()` finds pipx by its metadata**, not by looking for "pipx" in
  the path. Any unusually-placed `PIPX_HOME` -- a directory called `pipxhome`
  has no path part equal to "pipx" -- was classified as a plain venv and told
  to run `pip install`, which a pipx venv has no pip to run.
- README and DEPLOYMENT.md state that one install brings all four packages, and
  that the dependency commands are deliberately not exposed. DEPLOYMENT.md's
  console-script list contradicted the paragraph above it and no longer does.

## [2.0.24] - 2026-10-27

Released as 2.0.24 rather than 2.0.23. The 2.0.23 tag exists but its CI run
failed before publishing, so nothing reached PyPI under that number and
re-using it would mean force-deleting a public tag. The failure was a test of
mine that demanded all four repositories be checked out; CI clones siblings on
a best-effort basis by design, so it reported a checkout layout as a defect.

- **A supplied file that is not the document is refused, not parsed.** Asked
  for `weather.com/ads.txt` verbatim, a real assistant's fetch tool answered
  "I can't reproduce the full file" and returned a structural summary. Saved
  under the ads.txt name it parsed to zero records -- and the report then said
  *"a site with no advertising declarations publishes no payee"* about a site
  with about 185 of them. A confident absence drawn from a document nobody
  read is the one inversion this toolkit exists to prevent, and the
  instruction not to save a summary turned out to be a request rather than a
  control.

  The test is structural, not a guess at intent: every non-comment line of a
  real ads.txt parses as a record or a variable, and a real sellers.json
  parses as JSON or is a cut-off prefix of one. An empty or comments-only
  ads.txt is legitimate and still passes -- five cases assert that, because an
  over-eager check would throw away real files. A refused document is recorded
  as a block, never as "checked and found nothing"; the run is marked INVALID
  rather than merely incomplete; and the executive summary says the files were
  never read instead of offering an absence.

- **The bundle's loader no longer calls `exec`.** It subclasses
  `importlib.abc.InspectLoader` and hands the embedded source to the import
  machinery, which compiles and runs it. The first version called
  `exec(compile(...))` directly -- a Medium severity, High confidence bandit
  finding (B102) sitting under `src/`, which failed the static-analysis step
  of every one of the four CI workflows. The tempting fix was `--exclude`,
  repeated in four workflow files and `VERIFY.sh`, each free to drift and
  each leaving the generated code unscanned. A test now asserts the bundle
  calls neither `exec` nor `eval`, and a second asserts no bandit invocation
  anywhere excludes it.
- **`_is_fetchable` judges addresses by what they are.** It carried a denylist
  of literals plus an RFC1918 regex, which missed IPv6 entirely, missed
  carrier-grade NAT, and tripped bandit's B104 by containing a bare
  `"0.0.0.0"` -- suppressed with a `nosec` that was itself on the wrong line.
  It now uses `ipaddress`: `is_global and not is_multicast`, swept against
  every special-purpose range in a parametrised test. No suppression left in
  the package.
- **The version-consistency check no longer hard-fails on a single-repo
  checkout.** The non-vacuous guard now sits on this repository's own package,
  which is always present, and siblings are checked when they are there.
- **`build_portable.py` refuses to write a partial bundle.** From a single-repo
  checkout it silently assembled eight modules instead of sixty-eight, and
  that bundle looked like it worked: the import hook only claims modules it
  embedded, so `import attribution_graph` fell through to the installed
  package. Three tests passed on it. They now assert where each module was
  loaded *from*, and skip when the bundle cannot be built at all.


### Offline mode: the toolkit runs where it could not be installed

The skill was instructions pointing at a package from PyPI, and the chat
sandboxes it was written for will not install one: `pip install` is refused as
code from an external source, and the MCP config runs `uvx`, which downloads
the same package and hits the same wall. So a real session reported "command
not found" and fell back to ad-hoc web lookups -- no evidence bands, no
correlation groups, no evidence package, no audit record. Every property the
toolkit exists to provide was exactly what got dropped, and nothing in CI
noticed, because nothing here had ever run the way a user runs it.

The work splits along the line that actually divides those environments: the
assistant **can** reach the web, its sandbox cannot. So the assistant fetches
and the toolkit analyses.

- **`attribution_portable.py`** -- the whole chain in one standard-library
  file, shipped inside the skill. No install, no network, Python 3.11+.
- **Nothing is re-implemented.** `ReplayFetcher` satisfies the live fetcher's
  interface and serves saved documents, so the real collectors, engine,
  scoring, resolution, adversarial checks, gravatar linking and report run
  unchanged. A test asserts the bundle and the installed packages produce the
  same report from the same documents; `VERIFY.sh` fails if the bundle is
  stale. A second copy of the scoring rules would drift, and the dead copy is
  the one that gets corrected -- this project has shipped that defect four
  times already.
- **The fetch plan comes from the collectors**, not a list: run the pipeline,
  record what was asked for and not found, print that. It cannot go stale
  against the collectors because it *is* them.
- **A truncated document is detected, not declared.** The browsing tool in
  that real session silently cut the large `sellers.json` files short, and a
  truncated file read as "the seller is not in here" turns *not checked* into
  *checked and absent* -- the one inversion this toolkit exists to prevent. A
  structured document that does not parse is incomplete, and any absence in it
  is reported as not checked.
- **An audit trail is written.** "Your ticket numbers and reasons are only in
  this chat" was a real cost of running without the tool; it costs one
  append-only file.
- The report opens with an **OFFLINE REPLAY** banner: the digests commit to
  what was saved, not to what the origin served. It is a reading of supplied
  documents, and says so.
- Paths like `/.env` and `/.git/config` are **held back by default**. The
  toolkit probes them under an authorised, rate-limited case; an assistant
  fetching them from a chat is unauthenticated probing of someone else's
  server carrying the user's own address, and several read as an attack in the
  target's logs. `--include-sensitive` is there when the authorisation covers
  it.
- `attribution_portable.py --version` exists, because the skill tells an
  assistant to probe for it to decide which mode it is in. A test now asserts
  that every flag the docs show being passed to that command exists; its first
  version filtered to a hardcoded allowlist, so planting `--nonexistent-flag`
  in SKILL.md passed it, which is a test of the allowlist rather than of the
  docs.
- The run stops when the payment chain is complete, not when every
  corroborating source is exhausted -- there is always another register, so
  the first version never terminated. The rest is offered as optional.

### Also

- The constructor-wiring invariant was wrong in both directions and is now
  scoped correctly. Per-file counting flagged a stand-in class that exists to
  be read by other modules; counting package-wide made it vacuous, which was
  proved by planting the exact defect and watching it pass. It now requires a
  read on `self` in the same class or a read on some other object somewhere,
  and both canaries fail it.

### A gate for the defect that keeps recurring

`assess_identifier` -- the whole planted-identifier defence -- had one caller
and it was the demo script, so four releases of real investigations scored
planted identifiers at face value. It was found by accident. This release found
three more of the same shape, and a test suite cannot find them: a suite only
exercises code someone remembered to call, so the dead code is dead in the
tests too.

`scripts/find_unreachable.py` is a reachability census over all four
repositories, run by `VERIFY.sh`, and it is tested like any other deliverable
-- starting with the test that it actually fails, because a gate that cannot
fail is worse than no gate. Registry decorators (`@register`, `@rule`) count as
reachable; `@property` deliberately does not, since a property nobody reads is
as dead as a function nobody calls, and exempting it hid most of what turned up.
An orphan that is deliberate goes in `ALLOWED` with its reason, so the decision
is recorded rather than left.

### Network

- An ambient proxy is adopted automatically and recorded as the `environment`
  vantage point, so a sandbox run needs no flag; `--direct` refuses it for
  the case that wants this machine's own reachability. The doctor no longer
  tells people to pass `--egress-env`, which is now only an override.
- An egress preflight runs before the work: one request, non-fatal, and a proxy
  that is not carrying the traffic is reported as FAILED OPEN at the start
  rather than discovered in the manifest afterwards.
- TLS interception is disclosed in the warnings and the report summary, because
  a capture taken through it proves what the proxy served.
- Partial reads are reported separately from blocks, and unconditionally -- the
  first version reported them only when something *else* was also blocked, so a
  run with one truncated read and no blocks said nothing.
- `Trail.filtered` had no caller while two sites hand-rolled the same step. The
  demotion step now uses the helper; the Robin step keeps a direct `add`,
  because those claims are marked rather than set aside and "Set aside" would
  have been the wrong word.

### Demo

A seventh beat, `python ncs_demo.py report`: two emails and two avatar hashes on
one page, one of them a journalist's, placed by the operator. Both bind exactly,
with zero requests, and neither is drawn on the map -- because one page is one
source. Asserted in CI like the other six.

- **Gravatar linking runs in every investigation**, before the adversarial
  checks rather than after -- order matters, because a binding those checks
  should demote has to exist in the graph to be demoted. `gravatar_links` is
  in the summary, and a warning states the caveat so it cannot be read as
  control of the address.
- The report gained an **executive summary, a mermaid network map of the
  pivots and an indicator table**, and the skill and the `/ad-attribution`
  command now say how each is to be used: lead from the summary, pass the
  mermaid fence through unchanged or describe it in a sentence, and never pass
  a `DEMOTED` indicator on as a clean one.

- **`--cloaking-check` closes the selective-serving gap.** A material
  difference raises `SELECTIVE SERVING` and states that the findings describe
  the copy this tool was shown, not the site's public declaration; the verdict
  is in the summary as `ads_txt_consistency`. The skill and the
  `/ad-attribution` command are told to lead with it when it fires.
- It is **the only control here that misrepresents the client** -- the second
  retrieval sends a browser `User-Agent` -- so it is opt-in, and the audit
  trail records the exact string sent. Two variants that misrepresent nothing
  need no flag: a second declared egress (same request, different exit, which
  catches selective serving by IP) runs automatically when a case file declares
  one, and the archived copy comes through the existing wayback collectors.
- **`--stdout` prints the whole report.** A run driven by an assistant could
  only answer with paths under `./out` — files on whatever machine ran the
  tool, which the person being answered usually cannot open. The skill and the
  `/ad-attribution` command now pass it by default, and the evidence package is
  still written for verification later.
- The INCOMPLETE exit line names the cause that actually dominated. It said
  "blocked by fetch policy or URL safety" over runs whose blocks were almost
  entirely this machine failing to reach anything — the same misattribution the
  per-URL reasons made, in the one line most likely to be quoted.
- **Runs inside a sandbox now work, and when they cannot, they say why.**
  An ambient `HTTPS_PROXY` that was deliberately not used is reported with the
  flag that uses it (`--egress-env`). Hosts that could not be reached from
  this machine at all are counted and **named**: that list is what an egress
  allowlist needs, and it was previously buried in a cascade of blocks that
  read as though the sites had refused us.
- `doctor` reports an ambient proxy and the flag to use with it.
- The skill and the `/ad-attribution` command tell an assistant to read those
  two warnings before reporting anything -- saying "the site blocked us" when
  the sandbox did is the easiest way to mislead someone with this tool.
- **The MCP server could not start.** `mcp` 2.x replaced the 1.x decorator
  registration (`@app.list_tools()`) with constructor callbacks, and the
  handlers were bound at import, so `attribution-mcp` died with
  `AttributeError: 'Server' object has no attribute 'list_tools'` before
  printing anything -- against exactly the SDK `pipx inject` and `uvx` install
  today. Every MCP test here inspected the source as text, so nothing noticed
  the module could not be imported. The server is now built by
  `build_server()`, which registers with whichever generation is installed,
  `mcp` is in the `dev` extra so CI imports it, and three tests build the
  server, list the tools and call one.
- **`attribution skill --bundle`** writes `ad-attribution.zip`: the skill, a
  `/ad-attribution` slash command, an MCP config and an INSTALL.md covering
  every route. The archive's top level is the skill directory, which is what a
  host that accepts an uploaded skill requires, and the build is deterministic
  so a checksum of it means something.
- The skill is named `ad-attribution`, so an explicit `/ad-attribution`
  matches it. A copy installed under the old `entity-attribution` name is not
  removed; delete that directory if you had one.
- `INSTALL.md` states plainly what a bundle attached to a chat with no shell
  can and cannot do: it carries the method and the reporting rules, not the
  capability. The capability needs the MCP server or a host with a shell.

## [2.0.22] - 2026-10-23

- Released from the MCP and bundle work only: `build_server()` for both
  SDK generations, `attribution skill --bundle`, the `ad-attribution`
  rename. The sandbox and cloaking work that followed is 2.0.23, and its
  dependency floors are higher.

## [2.0.21] - 2026-10-21

- **The planted-identifier checks now run on every investigation.** The header
  of `runner.py` said adversarial checks run before scoring. Nothing called
  them: `assess_identifier` and `apply_assessments` had one caller in the whole
  project and it was the demo script, so the demo showed a defence the pipeline
  did not apply. The pass runs between collection and scoring, bounded to
  `ADVERSARIAL_MAX` identifiers that bear on the seed, reports
  `adversarial_assessed` and `adversarial_demoted`, records each demotion in
  the verification trail as a FILTER step with its reason, and warns when
  anything was demoted. `--no-adversarial` skips it, and the result then says
  so -- a weaker result and a full one must not be indistinguishable.
- The demo script ships in the repository (`demo/ncs_demo.py`) and every beat
  is tested in a subprocess, which is what a presenter runs. It lived outside
  every repository, which is how its claims drifted from the tool: a beat that
  demoted only one orientation of a claim, a refusal message shadowed by a
  generic one so it could never print, and a runbook asserting a portfolio size
  the command does not produce. The portfolio count is asserted now.
- New `injection` beat: the same poisoned world investigated twice, once with
  the agent's guards off and once on. The naive run stops two steps in and
  adopts the entity named in the site's own `ads.txt` comment; the defended run
  keeps reading the records and reaches the entity the ad system names. Both
  flag the attempt.
- The demo writes its corpus under the directory you run from, not next to the
  script, so running the copy that ships with the package no longer writes a
  database into the installed package directory. `NCS_DEMO_DIR` overrides it.
- A test asserting that blocked retrievals name their URL read
  `res.stats["warnings"]`, a key that does not exist, so it passed vacuously
  from the day it was written. It failed for the first time when a change
  caused something to be blocked -- the one case it existed to check.

## [2.0.20] - 2026-10-20

- **No real-world case data in any of the four repositories.** The README --
  and therefore the PyPI project page -- carried the payee, seller account,
  declared domain and company name of a site that had actually been
  investigated. Test fixtures carried more of the same. All of it is now
  fictional, and `scripts/scan_for_real_world_data.py` keeps it that way:
  identifier classes that denote one specific account or legal entity must
  match a placeholder form, and the specific values removed here can never
  reappear. The denylist is stored as truncated hashes, because a denylist
  written out in full would republish exactly what it exists to keep out.
  `VERIFY.sh` runs the scan over the bundle being shipped, and the check has
  controls proving it still fires.
- **The skill ships inside the package.** It lived in a top-level directory
  that existed only in a git checkout, so everyone who followed the quickstart
  and installed from PyPI got a tool whose agent-facing half was missing, with
  nothing saying so. New `attribution skill` prints where it is,
  `--install` copies it to `~/.claude/skills/entity-attribution`, and
  `--mcp-config` prints the host entry with the path resolved.
- `doctor` reports the agent surface: whether the skill is installed for a
  host, whether the MCP SDK and server are present, and what to run if not.
- The skill's own description advertised "calibrated confidence" in one of two
  divergent copies -- the single line a host reads when deciding to use it, and
  the opposite of this toolkit's central caveat. One canonical copy now, and a
  test that the description cannot claim calibration.
- The skill said how to interpret a result but never how to produce one: no
  commands, no `--authorization`, no output paths, no exit codes. A model could
  describe a finding it had no way to obtain.
- The planner's reply is validated: malformed JSON, a non-object and a tool
  name that does not exist each end the plan with a reason instead of raising.
- `--help` names the command actually typed -- `attribution-suite --help`
  opened with `usage: attribution run`, a command the reader never used and
  cannot type -- and every `run` option carries a description, enforced by a
  test against the real parser rather than its rendered output.
- A batch exits with the **worst** outcome of its runs. Anything other than 0
  or 3 was collapsed to 1, which the documented table defines as "evidence
  package failed verification", so a batch containing an INVALID result
  reported a verification failure that never happened.
- `DEPLOYMENT.md` and the demo runbook rebuilt against this build, with every
  command and every block of output run and copied from the terminal. The
  runbook had claimed a portfolio size its own command does not print.
- **Install is `pipx install attribution-suite`.** `pip install` is refused
  outright on Ubuntu 24.04, Debian 12, current Fedora and Homebrew Python
  (`error: externally-managed-environment`, PEP 668), so the first command in
  the README could not work on a stock machine, and the
  `attribution: command not found` that followed looked like a broken package.
  README, `DEPLOYMENT.md`, the MCP and screenshot instructions and the
  verification guide now all use pipx, with a virtualenv as the documented
  alternative and the error text quoted so a search for it lands on the fix.
  `--break-system-packages` is called out as the wrong answer.
- `doctor` reports the install mode — `pipx`, `venv` or `system` — and adapts.
  On a system Python it explains the PEP 668 refusal and prints the pipx
  commands. Under pipx, the dependencies' commands (`paytrace`,
  `paytrace-index`, `handlecorr`) are reported as `--` with the `pipx inject`
  remedy instead of `x`: pipx exposes only the apps of the package it
  installed, so a correct install was being reported as three faults.
- The minimisation leak test asserted that a seed value appears nowhere in any
  artifact **as a bare substring**, which no hashing scheme can deliver: the
  3-character seed `abc` is three hex characters, and a random 32-hex claim id
  contains it 0.78% of the time -- about one CI run in eleven. It failed on a
  correct build, pointing at the id `e5020dfabc32471cb30f6ef7c32b098a`, with
  the seed itself properly hashed to `handle:#099b030c...`. The check now uses
  the token boundaries that `minimise.scrub` actually implements, with two
  controls: five shapes of genuine leak that must still be caught, and the
  colliding id from that CI run forced deterministically so the flake cannot
  return unnoticed.
- The upgrade line for each install mode, including why `--upgrade-strategy
  eager` is mandatory in a virtualenv and unnecessary under pipx.

## [2.0.19] - 2026-09-27

- `--robin-query` runs Robin and imports the result. Robin could previously
  only be IMPORTED from a file produced by hand, so no dark web search ever
  happened: nothing invoked it. A missing, failing or empty-handed Robin is
  reported and the run continues without dark web results.
- A key set without the `llm` extra no longer falls back to fixed rules in
  silence — the commonest reason `ask` ignored ANTHROPIC_API_KEY.
- Default model is `claude-sonnet-5`, overridable with ANTHROPIC_MODEL, so a
  new model does not need a release.
- `doctor` reports the SDK and model, not just whether a key is set.

## [2.0.18] - 2026-09-25

- New `attribution doctor`: versions, commands on PATH, optional keys, cache
  state and one live request. A missing key, a broken install and a blocked
  network all presented identically — as an empty result.
- `--help` groups options by purpose instead of listing seventeen flat.
- Live end-to-end tests (`pytest -m network`) and a scheduled CI job. The run
  test fails on the event-loop bug that produced no report at all.
- README quickstart rewritten around `attribution-suite --domain`, which it
  did not previously mention.

## [2.0.17] - 2026-09-25

- New `--domains FILE`: one investigation per domain, each with its own
  report under `<out>/<domain>/`. URLs, duplicates, blanks and comments in
  the list are handled, and one failing domain does not abort the batch.
- The run summary names collectors that produced nothing, so a dead
  collector is visible instead of hidden inside a blocked total.

## [2.0.16] - 2026-09-24

- INCOMPLETE now says WHERE the blocks fell: how many were on the subject
  itself and how many on third-party hosts. Every blocked retrieval raised the
  same banner, so 226 robots refusals on probe paths and 72 dead hosts listed in
  someone else's ads.txt read exactly like a run that could not reach the file
  naming the payee.

## [2.0.15] - 2026-09-24

- `--domain` no longer minimises by default: the analyst's own report was
  `min:a5979016...` throughout — every name, domain and account replaced by a
  hash, and unreadable. Use `--minimise` for a report you hand to someone
  else.

## [2.0.14] - 2026-09-24

- `PAYEE` ranks the DIRECT line first and labels RESELLER lines. A RESELLER
  entry means that ad system resells inventory sold by someone else and is
  NOT the party being paid; following it named the reseller instead of the
  seller — the wrong answer, stated confidently.

## [2.0.13] - 2026-09-24

- `PAYEE` prints. It read the graph from `resolution`, which returned nothing,
  so the section never appeared even when the payee had been resolved. It also
  now accepts a declaration written in either direction.
- A declared domain that is NOT the seed is reported as such — one payout
  account serving this site and another is the strongest lead in the chain, and
  it was being labelled "matches the seed".
- `--domain` allows 800 requests (was 300) and takes `--max-requests`. Two hops
  across a few dozen declared accounts exhausted 300 before reaching the
  seller's own site, which is where people are named.

## [2.0.12] - 2026-09-24

- Default output is readable: per-URL blocked and checked lines are hidden
  behind `--diagnostics` (counts and caveats still shown, full list in
  verification_trail.json).
- Warnings are de-duplicated. The blocked report is appended more than once,
  so the whole list printed twice — dozens of duplicated lines on a real run.

## [2.0.11] - 2026-09-23

- `--domain` now pivots two hops and allows 300 requests. The answer is
  seed -> seller_id -> the seller's OWN site, whose about or imprint page
  names the people; radius 1 stopped at the seller account and never fetched
  the site that identifies it.
- The seller's own site is shown under `WHO THOSE ACCOUNTS BELONG TO`.

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
