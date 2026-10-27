---
description: Investigate which legal entity operates a domain, from its advertising declarations and public registers
argument-hint: investigate <domain> [--show-person]
allowed-tools: Bash(attribution:*), Bash(attribution-suite:*), Read, Glob
---

Run an attribution investigation for: **$ARGUMENTS**

Read `SKILL.md` in the `ad-attribution` skill before reporting anything: it
carries the rules that decide how a result may be described, and they are not
inferable from the output.

## Steps

1. Take the domain out of `$ARGUMENTS`. If there is none, ask for one and stop.
2. Ask the user **why this investigation is authorised**, in one line, unless
   `$ARGUMENTS` already says. Pass it verbatim. Do not invent one. A run
   without it records itself as unattested in the audit trail.
3. Run it:

   ```bash
   attribution-suite --domain <domain> --authorization "<what the user said>" --stdout
   ```

   Add `--show-person` only if the user asked for it explicitly.
4. If every retrieval is blocked, check the warnings before concluding
   anything. A proxy in this environment is adopted automatically, so there is
   no flag to add. `host(s) could not be reached from this machine` means an
   egress allowlist: report that list — it is what needs permitting — and do
   not describe the result as an absence, because nothing was checked. `TLS was
   terminated by this environment's proxy` means the run worked but the
   captures prove what the proxy served; say so. `EGRESS FAILED OPEN` means the
   declared vantage point did not hold — lead with that.
5. If the command is not found, run `attribution doctor` and report what it
   says rather than guessing. If the tool is not installed at all, say so and
   point at `pipx install attribution-suite`.

## Reporting

- If the summary says `SELECTIVE SERVING`, lead with **that**: the site
  declared different accounts to different retrievals, so the payee below is
  the copy this tool was shown, not the site's public declaration.
- Otherwise lead with the `PAYEE` block: who the ad system says it pays.
- `DIRECT` names the seller. `RESELLER` names an intermediary and is **not**
  the party being paid.
- Give the band and the number of independent evidence groups. Bands are
  evidence strength, **not** probability — never restate one as a percentage.
- Exit code 3 is INCOMPLETE, not failure: say which budget or block stopped the
  search, and that an absence may mean "could not be checked".
- Any conclusion about a natural person is a **lead** until a statutory
  register corroborates it. Say that when you report one.
- `--stdout` puts the whole report in front of you: summarise it in the
  conversation. Say that the evidence package was also written, and where, but
  never answer by pointing at a path the reader cannot open.
- Lead from the report's own **Executive summary** rather than rewriting it,
  and carry any condition it names into your first sentence.
- Pass the **Network map** `mermaid` fence through unchanged if this surface
  renders mermaid; otherwise describe the shape in one sentence. Do not redraw
  it. Arrow labels are bands, not probabilities.
- **Indicators** rows marked `DEMOTED` stay in the table on purpose — a
  planted identifier is a fact about whoever planted it. Never pass one on as
  a clean indicator.
- A **gravatar** link means the page displays that address's avatar, computed
  from values already collected. It does **not** mean the operator controls
  the address. Report it with that caveat attached.
